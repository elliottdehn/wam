/**
 * The registry: one Durable Object holding the whole graph, plus the R2 writes.
 *
 * R2 access lives in here rather than in the Worker because two of the rules
 * are read-then-write races that only a single-threaded DO makes safe:
 *
 *   - first to upload wins — two simultaneous uploads of one source must
 *     produce one node, not two;
 *   - parents must be public — checking that a parent exists and is public and
 *     then writing the edge cannot be two independent steps.
 */
import { DurableObject } from 'cloudflare:workers'
import { deriveBucket, mintSecret, similarity, sourceId } from './ids'

export const LIMITS = {
  /** A .wam is a few KB. This is generous and still bounds a hostile upload. */
  source: 1 << 20,
  title: 200,
  description: 2000,
  parents: 8,
  /** Client-asserted compile stats. Stored, never trusted. */
  meta: 16 << 10,
  /** R2 caps a batch delete at 1000 keys, so a big bucket is a loop. */
  deleteBatch: 1000,
} as const

export interface NodeRow {
  // SqlStorage row types must be indexable
  [key: string]: SqlStorageValue
  id: string
  title: string | null
  description: string | null
  visibility: 'private' | 'public'
  bucketId: string
  tombstoned: number
  createdAt: number
  publishedAt: number | null
  lastOkAt: number | null
  brokenSince: number | null
  meta: string | null
}

export type UploadResult =
  | { kind: 'created'; node: NodeRow; secret?: string; parents: string[] }
  | { kind: 'existing'; node: NodeRow; owned: boolean }
  | { kind: 'badParent'; ids: string[]; reason: 'missing' | 'private' }

export class Registry extends DurableObject<Env> {
  private sql: SqlStorage

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env)
    this.sql = ctx.storage.sql
    // blockConcurrencyWhile is unnecessary for DDL this small, but the schema
    // must exist before the first query on a cold object.
    this.sql.exec(`
      CREATE TABLE IF NOT EXISTS buckets (
        secretHash TEXT PRIMARY KEY,
        bucketId   TEXT NOT NULL UNIQUE,
        createdAt  INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS nodes (
        id          TEXT PRIMARY KEY,
        title       TEXT,
        description TEXT,
        visibility  TEXT NOT NULL,
        bucketId    TEXT NOT NULL,
        tombstoned  INTEGER NOT NULL DEFAULT 0,
        createdAt   INTEGER NOT NULL,
        publishedAt INTEGER,
        lastOkAt    INTEGER,
        brokenSince INTEGER,
        meta        TEXT
      );
      CREATE INDEX IF NOT EXISTS nodes_bucket ON nodes(bucketId);
      CREATE INDEX IF NOT EXISTS nodes_public ON nodes(visibility, createdAt);
      CREATE TABLE IF NOT EXISTS edges (
        childId    TEXT NOT NULL,
        parentId   TEXT NOT NULL,
        similarity REAL,
        PRIMARY KEY (childId, parentId)
      );
      CREATE INDEX IF NOT EXISTS edges_parent ON edges(parentId);
    `)
  }

  // ---- buckets --------------------------------------------------------------

  /** Resolve a presented secret, or mint a bucket for one that is new. */
  private async bucketFor(secret: string): Promise<string> {
    const { secretHash, bucketId } = await deriveBucket(secret)
    const found = this.sql
      .exec<Record<string, SqlStorageValue> & { bucketId: string }>('SELECT bucketId FROM buckets WHERE secretHash = ?', secretHash)
      .toArray()
    if (found.length) return found[0].bucketId
    this.sql.exec(
      'INSERT INTO buckets (secretHash, bucketId, createdAt) VALUES (?, ?, ?)',
      secretHash,
      bucketId,
      Date.now(),
    )
    return bucketId
  }

  /** Resolve a presented secret without creating anything. */
  async resolveBucket(secret: string): Promise<string | null> {
    const { secretHash } = await deriveBucket(secret)
    const found = this.sql
      .exec<Record<string, SqlStorageValue> & { bucketId: string }>('SELECT bucketId FROM buckets WHERE secretHash = ?', secretHash)
      .toArray()
    return found.length ? found[0].bucketId : null
  }

  // ---- upload ---------------------------------------------------------------

  async upload(input: {
    source: string
    title?: string
    description?: string
    visibility: 'private' | 'public'
    parents: string[]
    meta?: unknown
    secret: string | null
  }): Promise<UploadResult> {
    const id = await sourceId(input.source)
    const existing = this.node(id)

    // First to upload wins. A repeat of byte-identical source returns the
    // original node — it does not overwrite the title, does not change owner,
    // and does not restore bytes that were deleted.
    if (existing) {
      const bucketId = input.secret ? await this.resolveBucket(input.secret) : null
      return { kind: 'existing', node: existing, owned: !!bucketId && bucketId === existing.bucketId }
    }

    // Parents must be public. Checked here, inside the DO, together with the
    // insert below — outside it this is a race.
    if (input.parents.length) {
      const missing: string[] = []
      const priv: string[] = []
      for (const pid of input.parents) {
        const p = this.node(pid)
        if (!p) missing.push(pid)
        else if (p.visibility !== 'public') priv.push(pid)
      }
      if (missing.length) return { kind: 'badParent', ids: missing, reason: 'missing' }
      if (priv.length) return { kind: 'badParent', ids: priv, reason: 'private' }
    }

    const minted = input.secret ? undefined : mintSecret()
    const bucketId = await this.bucketFor(input.secret ?? minted!)
    const now = Date.now()

    await this.env.SOURCES.put(`src/${id}`, input.source)

    this.sql.exec(
      `INSERT INTO nodes (id, title, description, visibility, bucketId, tombstoned,
                          createdAt, publishedAt, meta)
       VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)`,
      id,
      input.title ?? null,
      input.description ?? null,
      input.visibility,
      bucketId,
      now,
      input.visibility === 'public' ? now : null,
      input.meta === undefined ? null : JSON.stringify(input.meta),
    )

    // Similarity is the one thing about a declared parent we *can* verify
    // without a compiler, since it needs nothing but the two sources.
    for (const pid of input.parents) {
      const parentSrc = await this.source(pid)
      this.sql.exec(
        'INSERT OR IGNORE INTO edges (childId, parentId, similarity) VALUES (?, ?, ?)',
        id,
        pid,
        parentSrc === null ? null : similarity(input.source, parentSrc),
      )
    }

    return { kind: 'created', node: this.node(id)!, secret: minted, parents: input.parents }
  }

  // ---- reads ----------------------------------------------------------------

  node(id: string): NodeRow | null {
    const rows = this.sql.exec<NodeRow>('SELECT * FROM nodes WHERE id = ?', id).toArray()
    return rows.length ? rows[0] : null
  }

  async source(id: string): Promise<string | null> {
    const obj = await this.env.SOURCES.get(`src/${id}`)
    return obj ? await obj.text() : null
  }

  parentsOf(id: string) {
    return this.sql
      .exec<Record<string, SqlStorageValue> & { parentId: string; similarity: number | null }>(
        'SELECT parentId, similarity FROM edges WHERE childId = ?',
        id,
      )
      .toArray()
  }

  /**
   * Declared children. Only public ones — a public model may have private
   * descendants, and listing them would leak their existence to every visitor
   * of the ancestor.
   */
  successorsOf(id: string) {
    return this.sql
      .exec<Record<string, SqlStorageValue> & { id: string; title: string | null; bucketId: string; createdAt: number; tombstoned: number; similarity: number | null }>(
        `SELECT n.id, n.title, n.bucketId, n.createdAt, n.tombstoned, e.similarity
           FROM edges e JOIN nodes n ON n.id = e.childId
          WHERE e.parentId = ? AND n.visibility = 'public'
          ORDER BY n.createdAt ASC`,
        id,
      )
      .toArray()
  }

  /**
   * The track a link lands you in.
   *
   * Canonical means "same secret": edges between nodes owned by one bucket are
   * the version history, and anything else is a branch. That gives "which of
   * these is the next version" an answer a stranger cannot claim.
   *
   * Everything returned is public, except the focus node itself — you only
   * reach that by holding its link.
   */
  lineage(id: string) {
    const focus = this.node(id)
    if (!focus) return null
    const visible = (n: NodeRow | null) =>
      !!n && (n.id === id || n.visibility === 'public')

    // Walk canonical parents back to the root of this bucket's track.
    const back: NodeRow[] = []
    const seen = new Set<string>([id])
    let cursor: NodeRow = focus
    for (;;) {
      const next = this.parentsOf(cursor.id)
        .map((e) => this.node(e.parentId))
        .find((n) => n && n.bucketId === focus.bucketId && !seen.has(n.id) && visible(n))
      if (!next) break
      seen.add(next.id)
      back.unshift(next)
      cursor = next
    }

    // ...and forward through same-bucket children. A bucket may fork its own
    // track, so this is a frontier rather than a line.
    const forward: NodeRow[] = []
    let frontier = [focus]
    while (frontier.length) {
      const nextRow: NodeRow[] = []
      for (const cur of frontier) {
        for (const child of this.successorsOf(cur.id)) {
          const n = this.node(child.id)
          if (n && n.bucketId === focus.bucketId && !seen.has(n.id)) {
            seen.add(n.id)
            nextRow.push(n)
          }
        }
      }
      forward.push(...nextRow)
      frontier = nextRow
    }

    const canonical = [...back, focus, ...forward]
    const inTrack = new Set(canonical.map((n) => n.id))
    const tipIds = canonical
      .filter((n) => !this.successorsOf(n.id).some((c) => inTrack.has(c.id)))
      .map((n) => n.id)

    // Branches: public derivatives owned by anyone else, off any track node.
    const branches: Array<{ fromId: string; id: string; title: string | null; bucketId: string; similarity: number | null }> = []
    for (const n of canonical) {
      for (const c of this.successorsOf(n.id)) {
        if (!inTrack.has(c.id)) {
          branches.push({ fromId: n.id, id: c.id, title: c.title, bucketId: c.bucketId, similarity: c.similarity })
        }
      }
    }

    return { focus, canonical, tips: tipIds, branches }
  }

  ownedBy(bucketId: string) {
    return this.sql
      .exec<NodeRow>('SELECT * FROM nodes WHERE bucketId = ? ORDER BY createdAt ASC', bucketId)
      .toArray()
  }

  /** Public tips: public nodes with no public successor. Newest first. */
  gallery(limit: number) {
    return this.sql
      .exec<NodeRow>(
        `SELECT n.* FROM nodes n
          WHERE n.visibility = 'public' AND n.tombstoned = 0
            AND NOT EXISTS (
              SELECT 1 FROM edges e JOIN nodes c ON c.id = e.childId
               WHERE e.parentId = n.id AND c.visibility = 'public'
                 AND c.bucketId = n.bucketId)
          ORDER BY n.createdAt DESC LIMIT ?`,
        limit,
      )
      .toArray()
  }

  // ---- writes ---------------------------------------------------------------

  /** private -> public. One way: a CC0 grant cannot be taken back. */
  publish(id: string, bucketId: string): 'ok' | 'notFound' | 'notYours' | 'gone' | 'already' {
    const n = this.node(id)
    if (!n) return 'notFound'
    if (n.bucketId !== bucketId) return 'notYours'
    if (n.tombstoned) return 'gone'
    if (n.visibility === 'public') return 'already'
    this.sql.exec("UPDATE nodes SET visibility = 'public', publishedAt = ? WHERE id = ?", Date.now(), id)
    return 'ok'
  }

  /**
   * Delete is one R2 object going away. The row, its edges and its place in
   * the DAG all stay, so descendants do not dangle.
   *
   * The strings go too: metadata is immutable, so delete is the *only* remedy
   * for a bad title. Leaving it standing would mean no remedy at all.
   */
  async deleteOne(id: string, bucketId: string): Promise<'ok' | 'notFound' | 'notYours' | 'already'> {
    const n = this.node(id)
    if (!n) return 'notFound'
    if (n.bucketId !== bucketId) return 'notYours'
    if (n.tombstoned) return 'already'
    await this.env.SOURCES.delete(`src/${id}`)
    this.sql.exec(
      'UPDATE nodes SET tombstoned = 1, title = NULL, description = NULL, meta = NULL WHERE id = ?',
      id,
    )
    return 'ok'
  }

  async deleteBucket(bucketId: string): Promise<{ deleted: number }> {
    const live = this.ownedBy(bucketId).filter((n) => !n.tombstoned)
    for (let i = 0; i < live.length; i += LIMITS.deleteBatch) {
      await this.env.SOURCES.delete(live.slice(i, i + LIMITS.deleteBatch).map((n) => `src/${n.id}`))
    }
    for (const n of live) {
      this.sql.exec(
        'UPDATE nodes SET tombstoned = 1, title = NULL, description = NULL, meta = NULL WHERE id = ?',
        n.id,
      )
    }
    return { deleted: live.length }
  }
}
