# Upstream Comparison and Maintainer Review

**Status:** complete review record; not an upstream-readiness approval
**Date:** 2026-08-14
**Owner:** `plaupetit/wam` fork maintainer
**Upstream baseline:** [`elliottdehn/wam@938e08e`](https://github.com/elliottdehn/wam/commit/938e08e) (`938e08ed3a6b3edb20b8d56b276a92237f851eae`)
**Reviewed fork revision:** `e3b8317` (`Add Codex multi-reference editor workflow`)
**Scope:** the complete semantic diff from the exact upstream baseline to the reviewed fork revision. This record is intended to help an upstream maintainer understand the change before deciding whether any portion is useful upstream.

## Executive summary

This fork keeps WAM's text source, compiler pipeline, glTF output, turntable sheet, and standalone viewer as its core. It adds an optional, local workflow around them:

1. prepare several named reference images rather than treating one image as the only evidence;
2. render validated named or custom camera views to individual PNGs and a deterministic manifest;
3. make non-destructive manual corrections in a standalone WebGL viewer and store them in a fingerprinted `.wamedit.json` sidecar;
4. optionally run a loopback-only local bridge that saves that sidecar beside the source and strictly rebuilds the existing output set.

The implementation is substantial: **34 changed/new tracked files, 5,465 insertions, and 170 deletions**. The highest-review-cost area is the self-contained viewer template (1,641 added lines). It should be treated as a feature branch or as several separately reviewed changes, not as a small upstream patch.

No claim is made that this is ready to merge upstream. In particular, Elliott should review the browser security model, the edit-layer design, the large viewer surface, and the desired long-term product direction independently.

## Fork intent and upstream ownership

This work began from a positive use-case observation: WAM offers a strong
parametric workflow for AI-assisted model generation and iteration. It also
exposed a practical limit in real sessions. A user can describe a correction
precisely and repeat it, yet an AI-authored result may still fail to converge
to the intended shape, placement, or colour. The manual edit layer was built
as an optional way to unblock that situation and let the user express a
specific correction directly.

That motivation is not a claim that the upstream project is missing a required
feature, nor that its original parametric-first philosophy is wrong. WAM may
intentionally prefer source-level authoring over interactive mesh correction.
The audit is therefore a maintainer-facing set of observed experiments and
trade-offs, not a merger request or a design mandate. Elliott can choose to
take no part of it, take individual ideas, split them differently, or
independently reimplement only what fits the upstream direction.

## Method, evidence, and boundaries

### What was inspected

- `git diff --name-status 938e08e..e3b8317` and the complete file-level semantic diff.
- Every changed/new source, test, launcher, packaging, skill, and public documentation file listed in [Change map](#change-map-all-34-files).
- Public command parsers, generated-manifest writers, edit-layer validation, bridge request handling, viewer-export metadata, and regression tests.
- The repository `LICENSE`, which remains the standard MIT text with Elliott Dehnbosel's copyright notice.
- Git ignore rules and the staged diff boundary. Personal model/output data under `out/`, local virtual environments, and the wamshare secret remain ignored and are outside this review.

### Evidence labels

- **Observed** means verified directly in the reviewed source, tests, or command output.
- **Inference** means a maintainer-facing consequence derived from the observed implementation and clearly labelled as such.
- **Not tested** means the boundary was not proven by the local automated checks below. It is not a statement that the boundary fails.

### Important boundary

The audit compares this fork with the stated commit only. It does not compare with later upstream work, audit third-party browser engines, or assert that an upstream maintainer should accept the product direction.

## Change map: all 34 files

| Area | Files | Observed change and purpose |
| --- | --- | --- |
| Codex discovery and guidance | `.agents/skills/wam/SKILL.md`, `.agents/skills/wam/agents/openai.yaml`, `AGENTS.md`, `skills/wam/SKILL.md` | Adds a ChatGPT Codex-discoverable skill entry point, host-neutral repository resolution, explicit virtual-environment guidance, and multi-image/per-view inspection instructions. The existing Claude skill remains, but no longer relies on a Claude-only environment variable. |
| Local setup and packaging | `pyproject.toml`, `Setup-WAM.ps1`, `Launch-Latest-Version.cmd`, `.gitignore`, `scripts/__init__.py`, `viewer/__init__.py` | Packages `wam`, `scripts`, and `viewer`; declares Python `>=3.9`, NumPy, and Pillow; exposes `wam`, `wam-codex`, and `wam-references`; creates the virtual environment on Windows and on macOS/Linux; adds an exact-checkout launcher; ignores local/generated artifacts. |
| Agent command surface | `wam/codex_cli.py` | Adds a JSON-on-stdout command surface for `compile`, `references`, and `rebase-edits`, including JSON argument errors and artifact reporting. |
| View contract and core compiler | `wam/views.py`, `wam/cli.py` | Centralizes named/custom camera parsing; writes per-view PNGs and a deterministic render manifest; validates views, dimensions, and requested animations; accepts an optional edit layer. |
| Other rendering entry points | `scripts/silhouette.py`, `wam/modelset.py`, `scripts/build_viewer.py` | Makes silhouette and composition rendering use the shared view contract; composition gains individual view panels and a manifest while retaining its historical horizontal sheet; viewer building uses explicit UTF-8 file handling. |
| Multi-reference pipeline | `wam/references.py`, `README_IF_GIVEN_IMAGE.md` | Adds named reference preparation, independent crops, optional whole/detail/palette roles and views, EXIF-aware image handling, source hashes, and a labelled board. |
| Edit-layer compiler contract | `wam/edits.py`, `wam/viewer_export.py` | Adds fingerprinted non-destructive edit layers and exposes stable part, local-face, mirror, primary-bone, rig-eligibility, and bone-frame data to the viewer. |
| Standalone manual editor | `viewer/template.html` | Extends the generated standalone WebGL viewer with object/face selection, depth-tested picking, gizmos, World/Local transforms, explicit mirror targets, painting, hiding, primitives, octahedral armature display, drafts, undo/redo, browser recovery, and a connected-save boundary. |
| Connected local rebuild | `wam/editor_bridge.py`, `Launch-Latest-Version.cmd` | Adds a per-source loopback bridge that rebuilds a selected existing output set transactionally after a validated save. The launcher routes `edit <source>` to it. |
| Quality records and release documentation | `CHANGELOG.md`, `Audits/Regression/editor-local-recovery.md`, `Audits/Regression/gizmo-local-rotation.md`, `Audits/Regression/mirror-target-selection.md`, `README.md`, `PUBLISHING_MUST_READ.md` | Adds the concise fork changelog, regression narratives, editor/bridge documentation, and host-neutral publishing prompt wording. |
| Regression harness | `tests/fixtures/codex_smoke.wam`, `tests/run_all.py`, `tests/test_multiview.py`, `tests/test_multireference.py`, `tests/test_edits.py`, `tests/test_editor_bridge.py` | Adds a fixture, isolated full-suite runner, and focused tests for new public contracts and error cases. |

## Public interfaces and generated artifacts

### Existing human CLI: `wam`

`python -m wam.cli` remains the human-oriented compiler. The existing input, `--out`, animation, lighting, glTF/viewer switches, panel dimensions, and `--bones` options remain. The fork adds:

```text
--edits LAYER.wamedit.json
```

`--views` and `--anim-views` now parse a shared grammar:

```text
front | threequarter | side | threequarter_back | back | side_r
custom-id:yaw[:pitch]
```

Unknown, empty, non-finite, duplicate, and Windows-case-colliding identifiers are explicit errors. A custom pitch must be strictly between `-89` and `89` degrees. The default turnaround remains the same five named views as the reviewed upstream CLI: `front,threequarter,side,threequarter_back,back`.

For every requested view, the compiler now also writes:

```text
<prefix>_view_<view-id>.png
<prefix>_views.json
```

The manifest has `schemaVersion: 1`, source and output SHA-256 hashes, ordered view IDs/yaw/pitch, per-panel dimensions, shared framing, diagnostics, and the historical sheet. Main-model compiles additionally record a `buildProfile` containing the reconstruction-relevant render choices used by the local bridge.

The historical `<prefix>_sheet.png`, glTF, viewer data, viewer HTML, texture output for unedited models, and animation output paths are retained. The sheet remains a horizontal strip specifically to avoid changing existing consumers that cut or size it by its old layout.

### Machine-oriented CLI: `wam-codex`

`python -m wam.codex_cli` is new. It reserves stdout for one deterministic JSON record and mirrors errors on stderr for people. Its commands are:

```text
wam-codex compile INPUT [--edits LAYER] [--views LIST] [--anim NAME]
                  [--anim-views LIST] [--frames N] [--bones]
                  [--no-gltf] [--no-viewer] [--width N] [--height N]
                  [--light EL,AZ,AMBIENT[,KEY,FILL]] [--strict]

wam-codex references --reference ID=PATH [--reference ID=PATH ...]
                     [--view ID=VIEW] [--kind ID=whole|detail|palette]
                     [--grid COLSxROWS] [-o DIRECTORY]

wam-codex rebase-edits LAYER --from OLD.wam --to NEW.wam -o NEW-LAYER
                        [--sync-rig-parts PART[,PART...]]
```

**Observed:** `compile` reports explicit artifact paths and separates lint warnings from informational diagnostics. `--strict` exits non-zero when warnings remain. Command and argument errors also emit `{ "ok": false, ... }` on stdout instead of relying on an `argparse` usage page.

### Reference manifest: `references.json`

`wam-codex references` writes `references.json` with `schemaVersion: 1`, an ordered `references` array, and board metadata. Each reference records its stable ID, role (`whole`, `detail`, or `palette`), source path/hash/visible dimensions, optional `id/yaw/pitch` view, and an independent crop grid with its files. The sources are read-only.

Input validation deliberately rejects duplicate/case-colliding IDs, unknown metadata IDs, invalid grids, unreadable images, output/source overlap, and empty crops. EXIF orientation is applied before cropping; transparent pixels are composited onto white rather than becoming misleading opaque evidence.

### Edit layer: `*.wamedit.json`

The portable correction format is a JSON object with:

```json
{
  "schemaVersion": 1,
  "source": { "sha256": "..." },
  "operations": []
}
```

The source fingerprint anchors the layer to the exact WAM bytes. Loading or compiling rejects a stale source rather than applying local face numbers to a new mesh. Supported operation types are:

| Operation | Purpose | Notes |
| --- | --- | --- |
| `transform` | Translate, rotate, and scale one part | Optional `mirror`, explicit `mirrorPart`, pivot, World/Local rotation/translation/scale frames, and conservative `syncRig`. |
| `paint_part` | Recolour every face of one part | Face colours remain intentionally asymmetric unless an existing legacy mirror operation requests otherwise. |
| `paint_faces` | Recolour selected local face ordinals | Face IDs are local to the named part, not global triangle indexes. |
| `hide_part` / `hide_faces` | Non-destructively omit a part or selected faces | The WAM source is not deleted or rewritten. |
| `add_primitive` | Add a box, sphere, cylinder, or cone | Attached to a named existing bone; a mirror option produces a linked reflected primitive. |

`mirrorPart` is intentionally only accepted for transforms. Conventional automatic counterpart names are `.l/.r`, `_l/_r`, or `.mirror`. An explicit asymmetric target keeps its own pivot, so moving a different-shaped counterpart does not overwrite its geometry or colour.

`syncRig: true` is intentionally conservative: it is accepted only if *all* bones with non-trivial weights for each transformed target are exclusively owned by that target. Shared body bones remain mesh-only. This alters generated bones and outputs, never the `.wam` text source.

The optional local-frame fields are backward compatible because omitted fields remain World space, preserving old layer behavior. Local bases are serialized as right-handed unit orthogonal axes, then validated again by the compiler.

### Viewer data and editor

`wam/viewer_export.py` adds data used only by the generated viewer: per-part IDs, face count, automatic mirror candidate, dominant bone, rig bone list and sync eligibility; triangle-to-part/local-face mappings; and bone parent, head, tail, side, and up frames. This allows stable editor choices without reconstructing generator internals in JavaScript.

The standalone HTML has two modes:

- **normal mode:** preserves orbit interaction;
- **edit mode:** left click selects/uses tools and right-button drag orbits.

Edit mode provides depth-tested picking, source/target halos, object/face modes, a World or Local X/Y/Z gizmo for whole parts, numeric transform fields, face selection/painting/hiding, material palette swatches, non-destructive primitives, an octahedral armature, a bone-grouped geometry menu, draft operations, 100-step history, a session log, and temporary browser recovery.

The viewer has no server dependency in static `file://` use. Static mode can restore browser-local recovery and download/load the edit layer, but cannot claim to save beside the WAM file.

### Connected editor and rebuild path

The new Windows entry point is:

```bat
Launch-Latest-Version.cmd edit "path\to\model.wam" [--out PREFIX]   :: Windows
python -m wam.editor_bridge path/to/model.wam [--out PREFIX]        #  macOS / Linux
```

It starts `wam.editor_bridge` on `127.0.0.1` with one random session token and one WAM source. The bridge discovers exactly one matching existing `*_views.json` render profile, or refuses ambiguity until `--out` chooses a prefix. It accepts only a bounded (4 MiB) token-authenticated save request, validates the layer, builds strictly into a sibling transaction folder, then promotes the managed viewer/glTF/PNG/manifest artifacts and the sidecar only after a successful build. A journal supports recovery from an interrupted promotion.

The bridge does not expose a caller-controlled path API, listens only on loopback, rejects stale fingerprints, and rejects concurrent saves. It is started only by the explicit `edit` command; normal `wam-codex` usage does not start a server.

## Compatibility and behavior changes

### Preserved by default

- The WAM grammar and source files are unchanged.
- Compiling without `--edits` follows the upstream parametric path and continues to produce the historical sheet, glTF, viewer data, and viewer page.
- Default camera order remains the reviewed upstream five-view turnaround.
- The composition renderer retains its historical three-view default and horizontal overview sheet.
- Existing human `wam` use remains available; `wam-codex`, references, manifests, edit layers, connected saves, and individual PNGs are additions.
- The upstream MIT license text and copyright notice remain intact.

### Intentional opt-in behavior

- Passing `--edits` changes the generated mesh and all requested outputs, but does not change the WAM source.
- A connected editor session writes `<model>.wamedit.json` beside the WAM only after a strict staged rebuild succeeds. Static HTML intentionally cannot do that.
- Using custom views or the reference command adds manifests and panels; it does not remove the original sheet.
- Enabling `syncRig` moves qualifying generated bones only after exclusivity validation; it never moves shared bones merely because one mesh was edited.

### Deliberately stricter behavior

- Invalid/misspelled views no longer silently render as `front`.
- Duplicate IDs that would collide on Windows are rejected before output.
- Invalid reference metadata, source/output overlap, stale edit layers, unknown parts/faces, invalid mirror targets, non-positive dimensions, and a requested missing animation fail explicitly.
- The JSON agent CLI does not report a stale optional artifact from an earlier run as if it were fresh.

### Known output limitation

When an edit layer is present, the compiler intentionally exports per-face material assignments rather than the upstream procedural texture atlas. This prevents a face-only recolour from bleeding through a shared atlas texel or vertex colour. It is a fidelity trade-off an upstream reviewer may choose to redesign.

## Risks, limitations, and required human review

| Topic | Observed implementation | Maintainer implication / recommended review |
| --- | --- | --- |
| Viewer size | `viewer/template.html` accounts for 1,641 added lines and combines renderer, picker, gizmo, editor state, history, recovery, and bridge client. | **Inference:** this is the highest regression and maintainability risk. Review manually in a browser, ideally split or modularize before any upstream merge. |
| WebGL capability | Picking and halos use WebGL framebuffer/shader paths. | Test on target browsers and integrated GPU drivers. Static HTML needs a graceful degradation decision if WebGL is unavailable. |
| Edit granularity | Version 1 edits parts and faces; it does not edit individual vertices, edges, topology, original WAM declarations, or bone hierarchy. | Treat the format as a focused correction layer, not a general modelling system. Preserve its strict fingerprint check. |
| Post-edit validation | Source lint runs before the overlay is applied; strict rebuild rejects source diagnostics. | **Inference:** source validity is proven, but a maintainer should decide whether a second post-edit mesh lint/check pass is required for future arbitrary edits. |
| Texture behavior | Edited outputs use materials instead of the atlas, as described above. | Inspect edited glTF/viewer material behavior in the target engine; document or redesign before promising texture equivalence. |
| Browser recovery | Recovery is keyed to the exact WAM fingerprint in browser local storage. | It is per browser profile/machine and can be cleared. The downloaded `.wamedit.json` is the portable backup; recovery is not a project repository. |
| Local bridge security | One-source loopback server, per-session random token, token comparison, no arbitrary path endpoint, payload cap, no ordinary HTTP token logging. | **Inference:** appropriate for a local desktop helper, not a multi-user service. A security review should consider hostile local processes, browser history/window exposure, local malware, and any future CORS/network changes. Keep it loopback-only. |
| Transaction promotion | Staged output, backup/promotion journal, and startup recovery are implemented and tested. | Test interruptions on the filesystems intended for users, including antivirus/locked-file behavior. Do not weaken the atomic promotion boundary. |
| Rig synchronization | Only exclusive weighted bones can be synced. | Good conservative boundary, but manual review should confirm intended skin ownership on complex rigs. Shared bones will deliberately remain visually mesh-only. |
| Test scope | Python regression suite and static viewer-source assertions cover contracts. | Browser interactions, real user editing sessions, GPU rendering, and external-engine glTF imports remain manual acceptance boundaries. |

## Recommended upstream review order

The following order minimizes coupled review:

1. **Independent, low-coupling:** `wam/views.py`, `wam/references.py`, their tests, and `README_IF_GIVEN_IMAGE.md`. These establish strict multi-image evidence and view rendering without requiring the editor.
2. **Compiler/output contract:** `wam/cli.py`, `wam/modelset.py`, `scripts/silhouette.py`, `scripts/build_viewer.py`, and the render manifests. Confirm that preserving sheets plus adding panels/manifests is the desired output policy.
3. **Agent and packaging surface:** `wam/codex_cli.py`, `pyproject.toml`, setup/launcher, repository instructions, and agent skills. These can remain fork-specific if upstream does not want a Codex-oriented distribution path.
4. **Edit format and export metadata:** `wam/edits.py` and `wam/viewer_export.py`, with `tests/test_edits.py`. Decide whether the fingerprinted sidecar belongs in core WAM before reviewing UI details.
5. **Viewer/editor:** `viewer/template.html` plus regression records. This is coupled to the edit-layer and metadata decisions; review it as a separate substantial frontend change.
6. **Connected bridge:** `wam/editor_bridge.py` and `tests/test_editor_bridge.py`. It should be accepted only if local write-and-rebuild is a desired WAM responsibility; otherwise static editor downloads remain usable without it.

## Validation evidence

The following commands were run from the repository root after this document was added. The full test command was serialized through the project command guard (Test profile: 180-second idle threshold, 900-second absolute cap, 60-second grace period); no parallel build/test process shared the worktree.

| Check | Command | Result |
| --- | --- | --- |
| Full regression suite | `Invoke-CodexGuardedCommand.ps1` running `.venv\Scripts\python.exe tests\run_all.py` | **Pass** — exit 0 in 129.4 seconds. All 8 scripts passed: existing near-plane (5), mirror (11), cinematic (74), acceptance-cinematic (17), plus multi-view (8), multi-reference (7), edits (8), and editor bridge (5). Guard Test profile completed without a timeout or retry. |
| Strict agent smoke compile | `.venv\Scripts\python.exe -m wam.codex_cli compile tests\fixtures\codex_smoke.wam -o out\upstream-comparison-smoke --views front,side,back --strict` | **Pass** — `ok: true`, `strictFailure: false`, zero warnings; produced three individual panels, sheet, manifest, glTF, viewer data, and viewer HTML. The front, side, and back panels were inspected individually. |
| Viewer JavaScript syntax | Node `new Function` over every inline script from `viewer/template.html` | **Pass** — one inline script block parsed successfully. |
| Patch whitespace | `git diff --check` | **Pass** — no whitespace errors. |
| Publication boundary | `git status --ignored --short out .venv` and worktree inspection | **Pass** — `out/` and `.venv/` report as ignored; before staging, the only tracked changes are this audit and its two documentation links. |

### Untested or manual boundaries

- Manual browser acceptance across supported browsers/GPU combinations.
- External-engine glTF import and shader/material appearance for edited models.
- Abrupt machine/filesystem failures outside the transaction scenarios covered by the bridge tests.
- Behavior on a repository where upstream has advanced after `938e08e`.

## License and publication boundary

**Observed:** `LICENSE` remains the standard MIT License with the original copyright and permission text. This audit, changelog, and code additions do not change that notice.

**Observed:** `out/`, `.venv/`, `*.egg-info/`, build artifacts, and the wamshare ownership secret are ignored. No personal reference images, Steely outputs, browser-local recovery data, or generated renders are included in the documentation follow-up commit.

This review is published to the public fork only. It creates no tag, release, pull request, or upstream publication.
