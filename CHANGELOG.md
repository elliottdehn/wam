# Changelog

All notable changes in the `plaupetit/wam` fork are documented here.

This fork starts from [`elliottdehn/wam` commit `938e08e`](https://github.com/elliottdehn/wam/commit/938e08e).
It retains the upstream MIT license and copyright notice in
[`LICENSE`](LICENSE).

## Fork additions — 2026-08-14

### Codex workflow

- Added a repository-local Codex skill, project guidance, a deterministic
  JSON CLI (`wam-codex`), and a Windows setup/launcher workflow.
- Added structured JSON errors and a script-style full regression runner for
  reliable agent and local validation.

### Multiple references and render views

- Added named multi-image reference preparation with independent crop grids,
  hashes, view metadata, and a labelled reference board.
- Added validated standard and custom camera views, individual per-view PNGs,
  and deterministic render manifests while retaining the historical sheet,
  glTF, and viewer outputs.
- Made invalid, empty, duplicate, and Windows-colliding view/reference IDs
  explicit errors instead of silently falling back to another result.

### Non-destructive editor

- Added versioned `.wamedit.json` edit layers for part transforms, face or
  part recolouring, hiding, manual primitives, mirror targets, and safe
  source fingerprint validation.
- Added a standalone WebGL editor with object/face selection, depth-tested
  picking, Blender-style gizmos, local or world transform orientations,
  mirror pairing, face painting, palette selection, armature display,
  undo/redo, draft sessions, and browser-local recovery.
- Added conservative isolated-rig synchronization: a correction can move an
  exclusively owned ear or tail bone, but never moves a shared body bone just
  because one attached mesh was edited.

### Rebuild and migration

- Added `wam-codex rebase-edits` for validated migration of an edit layer to
  a revised WAM source without overwriting the original layer.
- Added a loopback-only editor bridge. The launcher can save an edit layer
  beside its WAM source and strictly rebuild the matching viewer, glTF, PNGs,
  sheet, and manifests in place.

### Documentation and quality

- Documented the Codex and editor workflows in English.
- Added regression coverage for multi-view rendering, multi-reference input,
  edit layers, mirror targets, local transforms, recovery, and editor bridge
  safety.
- Preserved the upstream command-line and output compatibility paths when no
  new option or edit layer is used.
