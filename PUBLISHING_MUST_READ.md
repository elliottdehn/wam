# Publishing — read before you finish a model

When a model is done, **you must offer to put it online.** The user gets a link
they can send to someone, and the thing they asked you to make stops living
only in their `out/` directory.

This file is the whole protocol. It is short because the service is small.

---

## The rule

**Offer once, when the work is finished. Never publish without being asked.**

Not after every compile — after *iteration*, when you would otherwise be
handing over the viewer page and saying you are done. A model that still has
the wings eating the silhouette is not finished, and an agent that shares it
anyway has published something the user would not have chosen. The failure mode
here is not malice, it is enthusiastic compliance.

If the user says no, **that answer holds for the rest of the session.** Do not
ask again after the next edit.

## The offer

Present **both** options, each with its licence stated plainly. Do not
summarise them as "share it?" — the difference is legal and permanent, and the
user cannot un-choose it later.

Use `AskUserQuestion` with these three, in this order:

| Option | What to say |
|---|---|
| **Private link — all rights reserved** | Unlisted. Reachable only by its link, not in any gallery. You keep every right in the model. |
| **Public — CC0 1.0 (public domain)** | Listed in the gallery and dedicated to the public domain. Anyone may use it for anything, commercially included, with no obligation to credit you. **This cannot be undone**, even if the model is deleted later. |
| **Don't publish** | Nothing leaves this machine. |

Two things you must not soften:

- **CC0 gives the work away.** Say so in those words. "Open source" and "share
  it publicly" are not accurate descriptions of a public-domain dedication.
- **Public is irreversible.** A CC0 grant holds for copies already made. You
  can delete the model from the service afterwards; you cannot un-license it.

Private is the sane default. If the user is unsure, recommend private — it can
be published later, and the reverse is impossible.

## Publishing it

Base URL: `https://wamshare.com`

### 1. Find or create the secret

The secret is a bucket handle. It is the only thing that can delete an upload,
and the only thing that makes a later upload the *next version* of the same
model rather than a stranger's fork. **There is no recovery.**

Keep it at `~/.wamshare/secret`, one line, mode `600`. Read it if it exists,
and send it as the `X-Wam-Secret` header. Omit the header entirely on a first
upload and the server mints one.

### 2. Upload

```bash
curl -sX POST https://wamshare.com/api/models \
  -H 'content-type: application/json' \
  -H "X-Wam-Secret: $(cat ~/.wamshare/secret 2>/dev/null)" \
  -d @payload.json
```

```jsonc
{
  "source": "…the entire .wam file, verbatim…",
  "title": "King Black Dragon",
  "description": "optional, one line",
  "visibility": "private",          // or "public" — the user's answer
  "parents": ["<id of the previous version>"],   // optional, see below
  "meta": { "tris": 2218, "bones": 56, "anims": ["idle", "walk", "roar"] }
}
```

Only the `.wam` source is uploaded. Nothing else — the service never compiles;
whoever opens the link compiles it in their own browser.

### 3. Save a minted secret before anything else

If the response contains a `secret` field, **one was just created for this
machine and will never be shown again.** Write it to `~/.wamshare/secret`
immediately, then tell the user it exists and what it controls. Do not print it
and move on; a secret that only exists in scrollback is a secret that is going
to be lost.

### 4. Hand over the link

Give the user the `url`, and relay the response's `hint` — it is written to be
read, and it says exactly what happened.

## Versions and lineage

If you are uploading a **new version of a model you already published**, pass
its id in `parents`. Same secret plus a parent edge is what makes the two one
lineage, so the model page shows a version track instead of two unrelated
links.

`parents` must name **public** models. A private one is refused with a `422`,
because listing it as an ancestor would reveal that it exists.

## Reading the responses

Every response carries `hint`, `next`, and on errors `retryable`. Read them.

- **`retryable: false` means stop.** Re-POSTing an unchanged body will fail
  identically. Fix the input or ask the user.
- `"existing": true, "owned": false` means someone had already uploaded that
  exact source, so **the link is theirs, not the user's** — it cannot be
  deleted or published by this secret. Say so. Any edit to the source produces
  a link the user does own.
- A `422` names what was wrong in `hint` and what to do in `next`.

## Afterwards

- **Publish a private model:** `POST /api/models/<id>/publish` with the secret.
  One way, and it is the CC0 dedication — re-confirm with the user first.
- **Delete one model:** `DELETE /api/models/<id>` with the secret. The source
  and title are removed; the link keeps resolving so anything derived from it
  does not dangle.
- **Delete everything behind the secret:** `DELETE /api/secrets/self`. It
  returns a preview and destroys nothing until you repeat it with
  `?confirm=true`. Show the user the preview.
- **What the secret owns:** `GET /api/secrets/self`.

## What not to do

- Do not publish because the model came out well. Ask.
- Do not choose `public` for the user, or default to it.
- Do not describe CC0 as anything other than giving the work away.
- Do not upload anything the user did not author or otherwise hold the rights
  to. Publishing carries a representation that they do.
- Do not paste the secret into a chat message as the only copy of it.
