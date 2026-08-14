# WAM — WoW-ish Art Model language

A text language + compiler for **LLM-authored low-poly characters**: mesh,
skeleton, and animations from many lines of readable source, compiled to
glTF 2.0 with software-rendered turntables for visual iteration.

## Try it

Paste this into your favorite LLM:

```
Clone https://github.com/elliottdehn/wam and make me [what you want to model] that I can see
```

It will read the spec, author the model, compile it, look at the renders it
produced, and iterate until the thing holds together — then hand you a glTF
and a turntable sheet. "that I can see" is the part that matters: it puts the
model in the loop of looking at its own output.

The founding rule: the author only makes **discrete, named, relative,
symmetric** decisions — bone angles as words+degrees, body masses as
cross-section rings, membranes as fans of ribs, symmetry via `mirror`
blocks, props in local-frame `group`s. The compiler generates every vertex,
skin weight, normal, and winding, and a semantic linter rejects the classes
of silent geometry bugs that renders hide (folds, wrong-bone bindings,
floating feet, inside-out faces, parts buried inside other parts) — plus
whatever proportions you write down yourself in a `checks` section.

Models aren't limited to characters: `scripts/compose_town.py` composes a set
of prop models into a single scene with a packed mega-atlas, and `wam.zone`
compiles whole environments from landforms and placement rules.

A `textures` section gives materials a hand-painted look from named
procedural operators (gradients, noise, grain, bricks, planks, AO...),
baked into an auto-unwrapped texel atlas that ships inside the glTF.

## Quick start

Create the local environment once.

```powershell
powershell -ExecutionPolicy Bypass -File .\Setup-WAM.ps1   # Windows
```

```bash
./setup-wam.sh                                             # macOS / Linux
```

Both build a `.venv` beside this checkout and install WAM into it as an
editable package. **Every `python` below means that virtual environment's
interpreter** — `.\.venv\Scripts\python.exe` on Windows,
`./.venv/bin/python` on macOS and Linux. Setup also puts `wam`, `wam-codex`
and `wam-references` on that environment's path if you prefer the short forms.

```
python -m wam.cli mymodel.wam                 # compile + render 5 views
python -m wam.cli mymodel.wam --anim walk --frames 6
python -m wam.cli mymodel.wam --bones
python -m wam.cli mymodel.wam --width 760 --height 560
```

Outputs land in `out/`: a skinned, animated `.gltf` (drops into
Blender/three.js/engines), one PNG per view, a contact sheet, a deterministic
`*_views.json` manifest, the texture atlas, and a
`*_viewer.json` — open `viewer/template.html` in a browser and drop the
JSON onto it for an interactive orbit/animation view. Requires Python 3.9 or
newer and numpy; the multi-reference tools also need Pillow.

The repository ships no example models — [SPEC.md](SPEC.md) is the reference,
and every construct in it has a worked snippet. A taste of the language:

```
skeleton
  root pelvis at 0.52
  bone spine1 parent=pelvis dir=up pitch=13 len=0.13
  mirror
    bone thigh parent=pelvis side=0.07 dir=down pitch=-8 len=0.22
  end

parts
  loft torso bones=pelvis..spine2 material=skin
    ring 0.00 w=0.25 d=0.185 material=cloth
    ring 0.80 w=0.315 d=0.225
    cap start=dome end=dome

  group axe bone=hand.r at=1.0 dir=up pitch=45 yaw=-15
    loft haft at=(0,-0.03,0) dir=up len=0.47 material=leather
      ...
  end

animations
  anim walk loop dur=1.15
    ch thigh.l pitch 0%=-22 50%=19 100%=-22
    mirrorphase 50%
```

## ChatGPT Codex

Open this checkout as a Codex project. `AGENTS.md` and
`.agents/skills/wam/SKILL.md` teach Codex the local commands and the visual
review loop automatically. The agent-friendly CLI keeps stdout as predictable
JSON:

```
python -m wam.codex_cli compile mymodel.wam
```

It rejects unknown or duplicate views, accepts custom
`id:yaw[:pitch]` views, and writes every panel separately so Codex does not
have to inspect a very wide low-resolution sheet.

For several source images, name each piece of evidence and optionally map it
to a camera view:

```
python -m wam.codex_cli references \
  --reference front="refs/front.png" --view front=front \
  --reference side="refs/side.png" --view side=side \
  --reference badge="refs/badge detail.png" --kind badge=detail
```

This writes `out/references/references.json`, one crop grid per image, and a
navigation board. Each source remains separate and hash-addressed.

## Manual, non-destructive corrections

This fork adds an optional escape hatch for AI-assisted iteration: a user can
correct a result directly when clear, repeated instructions to an AI still do
not converge on the intended geometry or colour. It supplements the WAM
source workflow rather than replacing it; the source remains authoritative and
the correction layer can be removed at any time.

Every newly compiled `*.html` viewer includes an **Edit** mode. In that mode,
left-click selects a part or face; right-click-and-drag orbits the camera; the
mouse wheel zooms. Select a part to use the **Move / Scale** and **Rotate**
gizmo (or `W` / `E` / `R`): drag one coloured X/Y/Z handle for a live,
temporary preview that updates the numeric fields. **Transform orientation**
can be **World** or **Local (bone)** for all three modes. Local follows the
selected part's dominant bone rest frame and records that exact right-handed
frame in the edit layer, so the compiler recreates the same correction without
guessing. The preview stays local until
**Add transform to draft** adds it to the pending corrections; `Esc` cancels it.
Editing the numeric fields
updates the same preview. Colour, hide and primitive actions join the same
draft. In a standalone `file://` viewer, **Save all changes (browser only)**
commits the whole draft as one undoable correction batch and writes a
browser-local recovery point; **Discard pending draft** removes only pending
corrections. A face is
intentionally not moveable on its own in this version: use **Face select** to
inspect or hide one face, or enable **Paint faces** and left-click or
left-click-drag across visible faces. A paint stroke is one draft batch and
never mirrors colours automatically.

Every completed correction also updates the local recovery automatically,
including paint strokes, primitives, hides, transforms, undo/redo, and pending
drafts. A matching source is restored automatically after reloading the viewer.
The recovery is scoped to the current browser profile and the exact WAM source
fingerprint; **Forget local recovery** removes that browser copy without
rewriting the `.wam`. Browser storage can be cleared externally, so use
**Download edit layer (.wamedit.json)** for the portable backup and
**Load edit layer file…** to replace the local recovery with a compatible file.

The **Palette** swatches are interactive: click an authored material name and
hex colour to make it the active paint/primitive colour. This only selects a
colour; it never changes geometry until a colour action is used. Every viewer
control has an English hover/focus tooltip describing its effect and shortcut.

The selected geometry receives an orange `#ff9800` outline/halo, so its source
colour and texture stay visible. When Mirror X has a target, that target uses
an orange-red `#ff5a36` halo. Use **Pick mirror pair** beside Mirror target to
click a source and then its target; with a selected source it begins directly
at the target step. It uses depth-tested WebGL picking, so the visible front-most
part wins when geometry overlaps. `Esc` cancels the active picker stage.
Previewing or applying corrections keeps the current
camera centre, orbit and zoom; **Recenter model** is the only control that
deliberately creates a new frame. The Edit panel's temporary **Log** keeps
the last 200 selection, target-picker, draft, save, download/load and error events;
copy or clear it for troubleshooting. It is never saved to `.wamedit.json`,
rendered PNGs, glTF, or the `.wam`.
The Display panel also has a temporary background
colour picker with Reset; this is local to the open viewer and is never saved
to `.wamedit.json`, rendered PNGs, glTF, or the `.wam`. The **Geometry** menu
groups each part under its primary bone. **Mirror X** recognises conventional
`.l/.r`, `_l/_r`, and `.mirror` pairs, or lets you choose an explicit target
for a transform. A manual target receives reflected move/rotate/scale only:
its colours and asymmetric shape remain untouched. The Armature option draws selectable-part-aware
octahedral bones with an optional through-mesh display.

**Transform orientation** applies to Move, Rotate, and Scale. **World** uses
model axes; **Local (bone)** records the selected geometry's right-handed bone
frame in every relevant transform field, so the same layer rebuilds exactly.
Switching geometry, gizmo mode, or orientation retains each geometry's pending
transform session until it is added to the draft, saved, or explicitly
discarded. Undo/Redo restores up to 100 editor states, including pending
sessions and saved batches. **Sync isolated rig** is available only when every
bone weighted by the selected geometry is exclusively owned by it. This keeps
ear and tail corrections aligned with the armature without ever moving a
shared torso or head bone for one cosmetic mesh.

The viewer downloads corrections as a versioned `*.wamedit.json` layer. It
never rewrites the source `.wam`. For a project-side save, start the connected
editor from the repository root:

```bat
Launch-Latest-Version.cmd edit "path\to\model.wam"      ::  Windows
```

```bash
python -m wam.editor_bridge path/to/model.wam           #   macOS / Linux
```

The `.cmd` is a double-click convenience that forwards to exactly that module,
so the two are the same session with the same options (`--out`, `--port`,
`--no-browser`).

Its **Save all changes & rebuild** button validates the layer, writes
`model.wamedit.json` beside `model.wam`, and strictly replaces the matching
viewer HTML, glTF, individual PNGs, sheet, and manifests. The launcher detects
one matching output set from its render manifest; if several sets match, pass
`--out <prefix>` to select one. The connected session is loopback-only and
stays open until its launcher console is closed. Browser-local recovery remains
a safety net, while **Download edit layer (.wamedit.json)** remains the portable
backup.

To rebuild a downloaded layer without the connected editor:

```
python -m wam.codex_cli compile mymodel.wam \
  --edits out/mymodel.wamedit.json
```

The layer carries the exact SHA-256 fingerprint of its source WAM and stable
part/local-face references. A stale source, unknown part, invalid mirror
target, or invalid face blocks compilation with an explicit error instead of moving a
correction onto unrelated generated geometry. Compiling without `--edits` is
an immediate return to the untouched parametric model.

When a WAM source has intentionally changed but a downloaded layer should be
migrated, keep both inputs and use the controlled rebase command:

```
python -m wam.codex_cli rebase-edits old.wamedit.json \
  --from old-model.wam --to rigged-model.wam -o rigged-model.wamedit.json \
  --sync-rig-parts plum_ear,red_ear,plum_tail,red_tail
```

It checks the original fingerprint first, validates every part, face and
mirror target against the revised WAM, writes a new layer instead of changing
the original, and enables rig synchronization only for the explicitly named,
exclusive geometries. Shared-bone geometry remains mesh-only by design.

In this first version, an edited compile uses its per-face material colours
instead of the procedural texture atlas. This keeps a face-only recolour exact
rather than letting a shared vertex or atlas texel bleed it onto a neighbour.

## Claude Code plugin

This repo is a Claude Code plugin: it ships a `wam` skill that teaches
Claude the language, the compile-render-iterate workflow, and the hard-won
authoring rules (gait timing, prop groups, cloth skinning, fold avoidance).

```
/plugin marketplace add elliottdehn/wam
/plugin install wam@wam
```

Then ask Claude for a model ("make a WoW-style troll") and it will author,
compile, and visually iterate using the bundled toolchain.

## Layout

- `wam/` — compiler: parser → skeleton solver → mesh generation → texture
  baker → lint → glTF export, plus a dependency-free software rasterizer.
- `wam/codex_cli.py`, `wam/references.py`, `wam/views.py` — deterministic
  agent CLI, multi-image evidence preparation, and shared camera-view contract.
- `wam/checks.py` — the `checks` section: proportion, clearance, clipping,
  rig-quality and animation assertions re-run on every compile.
- `wam/modelset.py` — `.wamset` files: cross-model checks in meters, so a
  whole cast stays to one scale.
- `SPEC.md` — the language specification.
- `scripts/crop.py` — cuts matching regions out of a reference image and a
  render and pairs them at the same scale, for comparisons that are not just
  silhouette-deep.
- `viewer/` — template for a self-contained WebGL viewer page
  (`wam/viewer_export.py` produces the data blob to inject).
- `skills/wam/` — the Claude Code skill.
- `.agents/skills/wam/` — the local ChatGPT Codex skill entry point.

## License

MIT — see [LICENSE](LICENSE).

## Fork changelog

Fork-specific additions are listed in [CHANGELOG.md](CHANGELOG.md).
For an upstream-oriented review of the fork's interfaces, compatibility,
limitations, and validation evidence, see
[Audits/2026-08-14-upstream-comparison-audit.md](Audits/2026-08-14-upstream-comparison-audit.md).

## No idea what to make?

```bash
python dice.py        # a brine-crusted heron with a crown of horns too heavy for its neck
python dice.py -n 5   # five, and pick one
```

## Publishing

A finished model can be given a link at
[wamshare](https://wamshare.com) — private and unlisted, or
public under CC0 1.0. Agents: read `PUBLISHING_MUST_READ.md`. You must offer
both, with their licences named, and you must never publish unasked.
