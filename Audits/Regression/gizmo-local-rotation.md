# Gizmo local rotation regression

## 2026-08-14 — local transforms and drafts must survive tool changes

- **Cause:** Transform operations interpreted their Euler channels only against
  the model's global X/Y/Z axes. This made a wing, arm, tail, or other angled
  part rotate around an unintuitive world ring.
- **Expected behavior:** The editor offers World and Local (bone) Move,
  Rotate, and Scale. Local stores the selected dominant bone's explicit
  right-handed frame in the `transform` operation for rotation, translation
  and scale. A geometry keeps its pending session when the user changes mode,
  selection, or orientation; changing orientation parks the old result rather
  than deleting it.
- **Guard:** `tests/test_edits.py` verifies local matrices numerically,
  rejects malformed bases, protects local Move/Scale fields, the viewer's
  per-geometry session state, and the 100-step editor snapshot history.
- **Compatibility:** Layers without any orientation fields remain legacy
  world-space layers; `schemaVersion: 1` is unchanged.

## 2026-08-14 — a mesh correction could visually diverge from its armature

- **Cause:** The edit layer changed generated vertices after skeleton solving,
  while the bones used by the viewer, overlay and glTF stayed at rest.
- **Expected behavior:** Optional `syncRig: true` moves a bone only when every
  weighted bone of that part is exclusively owned by that part. Dedicated ear
  and tail chains can therefore stay aligned; shared `head` or `chest` bones
  remain intentionally mesh-only.
- **Guard:** `tests/test_edits.py` covers exclusive eligibility, endpoint
  synchronization, rejected shared geometry, and safe `wam-codex rebase-edits`
  migration. The viewer exposes **Sync isolated rig** only for eligible parts.
