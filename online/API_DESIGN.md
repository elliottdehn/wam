# wamshare — API design

The service behind `wamshare.com`. One sentence: **an agent uploads a `.wam`,
the server compiles it, and the user gets a link.**

This document records decisions *and why they were made*, because most of them
look arbitrary from the outside and every one of them is load-bearing. Where
something is undecided it says so — a design doc that invents answers is worse
than one with holes in it.

## Principles

**The uploader is an agent, not a person.** Every affordance has to survive
that. No email verification, no password reset, no "click the link we sent
you." What the agent *is* good at — writing a secret to disk and finding it
again later — is what the design leans on.

**The agent never publishes on its own initiative.** It asks the user, in the
conversation, and uploads only on a yes. This is the whole anti-spam story for
now, and it is a better one than it looks: the failure mode is not malice, it
is enthusiastic compliance. An agent told to "share when done" will share the
draft where the wings ate the silhouette, because it believed it was done.

**Source in, render out.** Uploads carry the `.wam` and nothing else; the
server compiles. Under a PR flow a human reads the diff — under an API nobody
does, so the server must be the only thing that produces markup. Accepting
pre-rendered HTML would mean hosting stranger-generated markup on our own
origin, unreviewed.

**Narrate, don't reject.** Where the service notices something off — a
declared parent the source shares nothing with, a model that compiles with
warnings — it reports what it observed rather than refusing. That is how the
compiler already behaves and the service should not have a different
personality.

## Visibility and licence

Two tiers, and the licence rides on the visibility rather than being a
separate axis:

| | **private** | **public** |
|---|---|---|
| Discovery | unlisted; link only | listed in the gallery |
| Licence | all rights reserved | CC0 1.0 |
| Default | **yes** | deliberate second choice |

Private is the default because it needs no moderation at all and covers the
common case: you made a thing, you want to show one person. Public is a choice
someone makes on purpose.

### Public means CC0, and that is forced rather than chosen

**All six Creative Commons licences carry the BY condition** — BY, BY-SA,
BY-NC, BY-ND, BY-NC-SA, BY-NC-ND. Attribution is not one option among several,
it is in every one of them. The only CC instrument without it is **CC0 1.0**,
a public-domain dedication rather than a licence.

So the identity decision already made this choice. There is no account, no
name, and `bucketId` is a hash of a secret — attribute to *what*? A licence
whose central obligation cannot be satisfied is worse than no licence, because
it leaves a downstream user wondering whether shipping the model in their game
requires crediting a hex string.

That leaves a clean binary, which is the right shape for a service with no
identity:

| | keep everything | give up everything |
|---|---|---|
| private | all rights reserved | — |
| public | — | CC0 1.0 |

Everything in between needs to know who the licensor is. We deliberately do
not.

Two things worth knowing about CC0 specifically:

- **It has a fallback.** Where a public-domain dedication is not effective in
  some jurisdiction, CC0 grants a broad permissive licence instead, so it
  degrades sensibly rather than failing open or shut.
- **It is a dedication by someone who must actually hold the rights**, and an
  anonymous service cannot verify that. That is a Terms problem, not a licence
  problem: publishing has to carry a representation that the uploader has the
  right to do so. Same exposure under any licence — CC0 just makes it visible.

The lineage graph still supplies credit; it simply supplies it as a *social*
convention rather than a legal obligation. Parent links show where a model came
from whether or not anyone is required to say so, which is arguably the better
arrangement for a remix corpus.

Rejected: an optional, unverified display name to make BY coherent. It
reintroduces the identity we removed on purpose, and an unverified name field
is an impersonation vector the first time someone types a studio's name into
it.

### Licence changes are one-way

**private → public is allowed. public → private is not.** Not a technical
limitation — a legal one. A CC grant is irrevocable for copies already made,
so a service that offered "make it private again" would be promising something
it cannot deliver. Unpublishing removes it from the gallery and nothing more,
and the API should say that in those words rather than implying a takeback.

## Identity and immutability

**Nothing is mutable.** Source, title, description, visibility beyond the
one-way publish — all fixed at upload. The secret authorises delete, never
edit.

### First to upload wins

The link id is `hash(source)`, and the first upload of a given source creates
the node. Later uploads of byte-identical source **return the existing link**;
they do not create a second node, do not overwrite its title, and do not join
the newcomer's bucket.

The consequence is accepted deliberately: upload something someone else already
shared and you get back a link **you do not own and cannot delete**, carrying
their title. Two things keep that tolerable —

- **The response says so.** `existing: true, owned: false` is in the body, so
  the agent can tell the user "this exact model is already up, here it is"
  rather than implying they just published it. Silently handing back an
  unowned link is the version that would confuse people.
- **The escape hatch is one character.** Any edit — a comment, a newline —
  changes the hash and produces your own node. Nobody is locked out of
  publishing a model, only out of publishing a *byte-identical* one twice.

Two edges that follow from it:

**Visibility does not transfer.** If the existing node is private, a second
uploader asking for public gets the private node as-is; they cannot publish
someone else's. Learning that a private node exists requires already holding
its exact source, so there is nothing to leak.

**Tombstones are permanent — RECOMMENDED.** After a delete, re-uploading the
same source should return the tombstone rather than reviving it, otherwise
delete does not stick and anyone can restore content its owner removed. The
one-character escape hatch means this costs nobody anything real.

## Lineage

An upload may **cooperatively declare** parents. The claim is not verified —
it is an assertion by the uploader — but it is *measured*: the server computes
textual similarity against each declared parent and stores it. A page can then
show "shares 97% with its parent" or "declares a parent it shares nothing
with," which is narration rather than judgement.

`parents` is a **list**, not a single field. The compiler's own `compose` /
`graft` model is genuinely multi-parent — a knight is a body plus plate plus a
hammer — so the one-parent assumption has a known expiry date.

### What we know, and what we do not

Internally this is a **DAG**: nodes are models, edges are declared parentage,
and it is append-only because nodes are immutable and a parent must already
exist. Deletion tombstones a node without removing it, so the shape survives.

**How any of it is surfaced is deliberately open.** Whether a page lists its
successors, whether the index shows every node or only tips, whether lineage is
a sidebar or a graph view — none of that is settled, and the API should not
prejudge it. `GET /successors` exposes the edges; what a page does with them is
a later decision.

One thing to carry into that decision whenever it is made: showing successors
puts **other people's uploads on someone else's page**, inbound, without
consent. Not an argument against doing it, but the reason a way for a parent's
owner to hide a successor should arrive at the same time as the feature, not
after. Retrofitting that onto a live graph is the painful version.

### Parents must be public

**A private model cannot be named as a parent** — not someone else's, not your
own. Declaring one is a `422`.

This removes a whole class of problem rather than handling it: no leak of a
private model's id or existence through a child's page, no ownership check to
decide whose private models you may reference, and no visibility logic anywhere
in the graph. **The DAG is entirely public**, which is a much simpler object to
reason about and to serve.

The cost is an ordering constraint: publish the parent before you can claim
descent from it. That seems like the right incentive.

## The secret

A secret is a **bucket handle**, not an account. No email, no reset, no
recovery — the only fact the server knows is that you can produce the string.

**You may hold as many as you like.** A secret owns a set of uploads and
nothing more; it does not represent a person, and the server cannot tell that
two secrets belong to the same one. That is deliberate on both sides of the
trade: there is no "everything I ever uploaded" operation, and equally there is
no way for anyone else to assemble that view either. Someone who wants two
models unlinkable uses two secrets.

```
upload with no secret   → the server mints one and returns it
upload with a secret    → the model joins that secret's bucket
delete the secret       → every model in that bucket loses its content
```

The usual objection to bearer secrets is that people lose them. The uploader
here writes files to disk for a living: it keeps a secret at a known path and
reuses it, which is far better ergonomics than a pile of per-upload tokens. It
is also echoed into the conversation, putting a second copy in the user's
scrollback.

**Delete removes content, not links.** The node stays, the graph stays,
successors do not dangle — the content stops being served and the page reads as
a tombstone. That holds whether one model is deleted or a whole bucket is.

**Edit does not exist, for anything.** Not the source, not the title, not the
description. The secret authorises *delete* and nothing else. A typo in a title
is fixed by uploading again and deleting the first one.

### The public handle is derived, and it is not a person

Where a bucket needs naming publicly, derive it: `bucketId = hash(secret)`.
Stable, public, non-reversible, and it keeps the secret purely a credential —
otherwise reading a page hands you the keys.

It is called `bucketId` and not `uploaderId` on purpose. A secret is not a
person, people may hold several, and naming it after an author invites
person-shaped features that the model cannot actually support.

### Two granularities of delete

**The secret authorises per-model delete**, and deleting the secret itself
tombstones everything in its bucket. The second is a shortcut, not the only
door — otherwise removing one embarrassing model would cost every model
uploaded alongside it.

### Blast radius — accepted

A leaked secret tombstones its whole bucket, irreversibly. That is a real cost
and it is **accepted rather than mitigated**: the alternative is per-upload
tokens, whose ergonomics are worse for the agent that has to keep them, and the
blast radius is bounded anyway by however many uploads someone chose to put
behind one secret.

Recorded so nobody relitigates it later as an oversight. The one cheap guard
worth keeping is that the bucket-wide delete returns a preview of what it would
destroy unless called with `confirm=true`.

## Endpoints — DRAFT

The secret travels in a header (`X-Wam-Secret`), never in a URL — URLs end up
in logs, referrers and screenshots, and this one is the keys to the corpus.

```
POST /api/models
  header X-Wam-Secret: <secret>          # omit on a first upload
  body   { source, title?, description?, visibility: "private"|"public",
           parents?: [id], compilerVersion?, meta? }
  200    { id, url, bucketId,
           existing: bool,               # this source was already uploaded
           owned: bool,                  # ...and whether by this secret
           secret?,                      # present ONLY when one was minted
           compile: { ok, warnings[], stats } }
  422    { compile: { ok: false, errors[] } }   # the compiler's own message

GET  /api/models/:id            → metadata + compiled viewer blob
GET  /api/models/:id/source     → the .wam, as text/plain
GET  /api/models/:id/successors → declared children
POST /api/models/:id/publish    → private → public. One way.
GET  /api/gallery               → presentation; shape TBD

DELETE /api/models/:id          → tombstone one model
GET    /api/secrets/self        → what this secret owns
DELETE /api/secrets/self        → tombstone the whole bucket.
                                  Returns a preview unless confirm=true.
```

`meta` is where the agent's structured compile output goes: triangle count,
bone count, the model's own `checks` and their measured values. That is the
thing an API buys that an upload form never could, and it is what makes the
gallery searchable on real properties — every model under 3000 triangles,
every one using a `web` membrane.

A note for whoever designs the index: immutability plus lineage turns eleven
tuning iterations into a chain rather than eleven strangers, which is better —
but only if the index does something with that. Listing every node makes the
front page somebody's afternoon.

## Compiling untrusted input

`.wam` is declarative data rather than code, so compiling a stranger's upload
is far safer than running their code. It is still hostile parser input reaching
numpy. `sides=512` across forty lofts, a thousand-bone chain, `steps=9999` on a
web: none of it looks malicious and all of it eats a worker. Caps and a
timeout, decided once, plus the job-id escape hatch for anything slow.

For scale: the sentinel compiles in **341 ms** and the runtime boots in about a
second, so synchronous is fine for ordinary models.

## Open questions

- The entire presentation layer: index, lineage display, whether a bucket is
  ever surfaced publicly.
- Whether a private model may be declared as someone else's parent.
- Terms text: publishing has to grant the right to display *and* carry a
  representation that the uploader holds the rights they are dedicating. One
  paragraph now, a mess to retrofit once there are contributors to re-ask.
  This is the one item here worth a real lawyer's eye rather than mine.
- Rate limiting: none for now, by decision, not by oversight.
