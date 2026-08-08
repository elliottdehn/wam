/**
 * wamshare — the API.
 *
 * The service stores `.wam` source and serves it back. It never compiles:
 * that happens in the visitor's browser under Pyodide, which is why no
 * pre-rendered output is accepted or stored anywhere.
 *
 * See API_DESIGN.md. Every response carries `hint` / `next` / `retryable`,
 * because the client is a language model and a bare status code tells it
 * nothing about what to do next.
 */
import { LIMITS, Registry } from './registry'
import { fail, ok } from './reply'

export { Registry }

/** One registry instance: the graph is small and must be serialisable. */
function registry(env: Env): DurableObjectStub<Registry> {
  return env.REGISTRY.getByName('v1')
}

const PUBLIC_NODE_FIELDS = [
  'id', 'title', 'description', 'visibility', 'bucketId',
  'tombstoned', 'createdAt', 'publishedAt',
] as const

function publicNode(n: Record<string, unknown>) {
  const out: Record<string, unknown> = {}
  for (const k of PUBLIC_NODE_FIELDS) out[k] = n[k]
  out.tombstoned = !!n.tombstoned
  out.meta = n.meta ? JSON.parse(n.meta as string) : null
  return out
}

/**
 * Links are canonical, not whatever host the caller happened to use.
 *
 * An agent that POSTs to the workers.dev hostname should still hand its user a
 * wamshare.com link, because that link gets shared and outlives the request
 * that made it.
 *
 * Note for local work: `wrangler dev` adopts the configured route's hostname,
 * so the request already looks like wamshare.com on a laptop and links come
 * out production-shaped either way. Put `PUBLIC_ORIGIN=http://localhost:8787`
 * in `.dev.vars` if you need local ones.
 */
function linkFor(req: Request, env: Env, id: string) {
  return new URL(`/m/${id}`, env.PUBLIC_ORIGIN || req.url).toString()
}

export default {
  async fetch(request, env): Promise<Response> {
    const url = new URL(request.url)
    if (!url.pathname.startsWith('/api/')) return env.ASSETS.fetch(request)

    try {
      return await route(request, env, url)
    } catch (err) {
      console.error(err)
      return fail(500, 'Something broke on our side. Nothing was changed.', {
        retryable: true,
      })
    }
  },
} satisfies ExportedHandler<Env>

async function route(request: Request, env: Env, url: URL): Promise<Response> {
  const reg = registry(env)
  const secret = request.headers.get('x-wam-secret')
  const seg = url.pathname.replace(/^\/api\//, '').replace(/\/$/, '').split('/')
  const method = request.method.toUpperCase()

  // ---- POST /api/models ----------------------------------------------------
  if (seg[0] === 'models' && seg.length === 1 && method === 'POST') {
    return upload(request, env, reg, secret)
  }

  // ---- /api/models/:id[...] ------------------------------------------------
  if (seg[0] === 'models' && seg.length >= 2) {
    const id = seg[1]
    const node = await reg.node(id)

    if (seg.length === 2 && method === 'GET') {
      if (!node) return notFound()
      return ok(
        { model: publicNode(node), parents: await reg.parentsOf(id), url: linkFor(request, env, id) },
        node.tombstoned
          ? {
              hint: 'This model was deleted. The link still resolves and its place in the lineage is intact, but the source is gone.',
              next: [`GET /api/lineage/${id} to see what it was derived from`],
            }
          : {
              hint: 'Fetch the source and compile it in the browser — this service never compiles anything.',
              next: [`GET /api/models/${id}/source`, `GET /api/lineage/${id}`],
            },
      )
    }

    if (seg.length === 3 && seg[2] === 'source' && method === 'GET') {
      if (!node) return notFound()
      const src = await reg.source(id)
      if (src === null) {
        return fail(410, 'That model was deleted, so its source is gone. The link stays valid so nothing derived from it dangles.', {
          extra: { tombstoned: true },
        })
      }
      return new Response(src, {
        headers: {
          'content-type': 'text/plain; charset=utf-8',
          // Immutable by construction: the id is a hash of this exact text.
          'cache-control': 'public, max-age=31536000, immutable',
        },
      })
    }

    if (seg.length === 3 && seg[2] === 'successors' && method === 'GET') {
      if (!node) return notFound()
      const successors = await reg.successorsOf(id)
      return ok({ successors }, {
        hint: successors.length
          ? 'Public models that declare this one as a parent. The claims are not verified; the similarity figure is what we measured.'
          : 'Nothing public declares this model as a parent yet.',
      })
    }

    if (seg.length === 3 && seg[2] === 'publish' && method === 'POST') {
      if (!secret) return needSecret()
      const bucketId = await reg.resolveBucket(secret)
      if (!bucketId) return unknownSecret()
      const res = await reg.publish(id, bucketId)
      if (res === 'notFound') return notFound()
      if (res === 'notYours') {
        return fail(403, 'That model belongs to a different secret, so it cannot be published with this one.', {
          next: ['Upload your own copy: any edit at all gives you a link you own'],
        })
      }
      if (res === 'gone') return fail(410, 'That model was deleted and cannot be published.')
      if (res === 'already') {
        return ok({ id, alreadyPublic: true }, { hint: 'That model was already public. Nothing changed.' })
      }
      return ok({ id, visibility: 'public', url: linkFor(request, env, id) }, {
        hint: 'Published under CC0 1.0 and listed in the gallery. This cannot be undone — the dedication holds for copies already made, even if the model is deleted later.',
      })
    }

    if (seg.length === 2 && method === 'DELETE') {
      if (!secret) return needSecret()
      const bucketId = await reg.resolveBucket(secret)
      if (!bucketId) return unknownSecret()
      const res = await reg.deleteOne(id, bucketId)
      if (res === 'notFound') return notFound()
      if (res === 'notYours') {
        return fail(403, 'That model belongs to a different secret.', {
          next: ['DELETE /api/secrets/self removes everything behind the secret you did send'],
        })
      }
      if (res === 'already') return ok({ id, tombstoned: true }, { hint: 'That model was already deleted.' })
      return ok({ id, tombstoned: true }, {
        hint: 'Deleted. The source and the title are gone; the link still resolves so anything derived from this model does not dangle. If it was public, copies made under CC0 are unaffected.',
      })
    }
  }

  // ---- GET /api/lineage/:id ------------------------------------------------
  if (seg[0] === 'lineage' && seg.length === 2 && method === 'GET') {
    const track = await reg.lineage(seg[1])
    if (!track) return notFound()
    return ok(
      {
        focus: track.focus.id,
        canonical: track.canonical.map(publicNode),
        tips: track.tips,
        branches: track.branches,
      },
      {
        hint: 'The canonical track is the versions sharing this model\'s secret; branches are derivatives owned by someone else. Only public models appear, plus the one you asked for.',
      },
    )
  }

  // ---- /api/secrets/self ---------------------------------------------------
  if (seg[0] === 'secrets' && seg[1] === 'self') {
    if (!secret) return needSecret()
    const bucketId = await reg.resolveBucket(secret)
    if (!bucketId) return unknownSecret()

    if (method === 'GET') {
      const models = await reg.ownedBy(bucketId)
      return ok({ bucketId, models: models.map(publicNode) }, {
        hint: 'Everything uploaded with this secret. There is no other way to enumerate it, and no way to recover the secret if it is lost.',
      })
    }

    if (method === 'DELETE') {
      const live = (await reg.ownedBy(bucketId)).filter((n) => !n.tombstoned)
      const confirm = url.searchParams.get('confirm') === 'true'
      if (!confirm) {
        // The one guard on an accepted blast radius: never destroy a bucket on
        // a single unconfirmed call.
        return ok({ bucketId, wouldDelete: live.length, models: live.map(publicNode), deleted: 0 }, {
          hint: 'Nothing has been deleted yet. This would remove the source and title of every model behind this secret, and cannot be undone.',
          next: ['Repeat with ?confirm=true to go ahead', 'DELETE /api/models/:id to remove just one'],
        })
      }
      const { deleted } = await reg.deleteBucket(bucketId)
      return ok({ bucketId, deleted }, {
        hint: 'Every model behind this secret is now a tombstone: sources and titles gone, links and lineage intact. Anything published under CC0 stays licensed for copies already made.',
      })
    }
  }

  // ---- GET /api/gallery ----------------------------------------------------
  if (seg[0] === 'gallery' && seg.length === 1 && method === 'GET') {
    const limit = Math.min(Number(url.searchParams.get('limit') ?? 50) || 50, 200)
    const models = await reg.gallery(limit)
    return ok({ models: models.map(publicNode) }, {
      hint: models.length
        ? 'Public models with no public successor of their own — the tip of each track, newest first.'
        : 'Nothing has been published yet. Uploads are private by default; publishing is a deliberate second step.',
    })
  }

  return fail(404, 'No such endpoint.', {
    next: [
      'POST /api/models',
      'GET /api/models/:id, /source, /successors',
      'GET /api/lineage/:id',
      'POST /api/models/:id/publish',
      'DELETE /api/models/:id',
      'GET or DELETE /api/secrets/self',
      'GET /api/gallery',
    ],
  })
}

// ---- upload ----------------------------------------------------------------

async function upload(request: Request, env: Env, reg: DurableObjectStub<Registry>, secret: string | null) {
  let body: Record<string, unknown>
  try {
    body = (await request.json()) as Record<string, unknown>
  } catch {
    return fail(400, 'The body must be JSON.', {
      next: ['POST { source, title?, description?, visibility, parents?, meta? }'],
    })
  }

  const source = body.source
  if (typeof source !== 'string' || !source.trim()) {
    return fail(400, 'A `source` string is required — the text of a .wam file.')
  }
  if (source.length > LIMITS.source) {
    return fail(413, `That source is larger than the ${LIMITS.source >> 10} KB limit. A .wam is normally a few KB, so this is probably not one.`)
  }

  const visibility = body.visibility ?? 'private'
  if (visibility !== 'private' && visibility !== 'public') {
    return fail(400, '`visibility` must be "private" or "public". Private is the default and keeps all rights; public dedicates the model to the public domain under CC0 and cannot be undone.')
  }

  const title = optionalString(body.title, LIMITS.title)
  const description = optionalString(body.description, LIMITS.description)
  if (title === false) return fail(400, `\`title\` must be a string of at most ${LIMITS.title} characters.`)
  if (description === false) return fail(400, `\`description\` must be a string of at most ${LIMITS.description} characters.`)

  let parents: string[] = []
  if (body.parents !== undefined) {
    if (!Array.isArray(body.parents) || body.parents.some((p) => typeof p !== 'string')) {
      return fail(400, '`parents` must be an array of model ids.')
    }
    parents = [...new Set(body.parents as string[])]
    if (parents.length > LIMITS.parents) {
      return fail(400, `At most ${LIMITS.parents} parents.`)
    }
  }

  if (body.meta !== undefined && JSON.stringify(body.meta).length > LIMITS.meta) {
    return fail(413, '`meta` is too large. It is for compile stats, not for the model.')
  }

  const result = await reg.upload({
    source,
    title: title || undefined,
    description: description || undefined,
    visibility,
    parents,
    meta: body.meta,
    secret,
  })

  if (result.kind === 'badParent') {
    const hint =
      result.reason === 'missing'
        ? 'One of the declared parents does not exist here. Parents are model ids from this service.'
        : 'Parents have to be public. A private model cannot be named as one, including your own, because it would expose that it exists.'
    return fail(422, hint, {
      extra: { badParents: result.ids, reason: result.reason },
      next:
        result.reason === 'private'
          ? ['Publish the parent first, then upload this again']
          : ['Drop the unknown id from `parents`, or upload that model first'],
    })
  }

  if (result.kind === 'existing') {
    const n = result.node
    return ok(
      {
        id: n.id,
        url: linkFor(request, env, n.id),
        bucketId: n.bucketId,
        existing: true,
        owned: result.owned,
        model: publicNode(n),
      },
      {
        hint: result.owned
          ? 'You had already uploaded this exact source, so this is the link you got the first time. Nothing was changed.'
          : 'This exact source was already uploaded, so you have the original link rather than a new one. It belongs to someone else\'s secret: you cannot delete or publish it. Any edit at all gives you your own copy.',
        next: result.owned ? [] : ['Change the source and upload again to get a link you own'],
      },
    )
  }

  const n = result.node
  const minted = result.secret
  return ok(
    {
      id: n.id,
      url: linkFor(request, env, n.id),
      bucketId: n.bucketId,
      existing: false,
      owned: true,
      secret: minted,
      model: publicNode(n),
      parents: result.parents,
    },
    {
      hint: [
        minted
          ? 'Uploaded, and a new secret was minted. It is the only way to delete this or to add later versions to the same lineage, and it cannot be recovered — save it.'
          : 'Uploaded under the secret you supplied.',
        n.visibility === 'public'
          ? 'It is public and dedicated under CC0 1.0, which cannot be undone.'
          : 'It is private: unlisted, all rights reserved, reachable only by its link.',
      ].join(' '),
      next: [
        n.visibility === 'private' ? `POST /api/models/${n.id}/publish to list it publicly (CC0, one way)` : '',
        `DELETE /api/models/${n.id} to remove it`,
      ].filter(Boolean),
    },
    201,
  )
}

function optionalString(v: unknown, max: number): string | null | false {
  if (v === undefined || v === null) return null
  if (typeof v !== 'string' || v.length > max) return false
  return v
}

const notFound = () =>
  fail(404, 'No model with that id. Ids are content hashes, so a typo does not resolve to anything.')

const needSecret = () =>
  fail(401, 'This needs the secret that owns the model, in the X-Wam-Secret header.', {
    next: ['Upload without a secret to be given one'],
  })

const unknownSecret = () =>
  fail(403, 'That secret is not one we have seen. Secrets cannot be recovered — if it is lost, so is the ability to delete or extend what it uploaded.')
