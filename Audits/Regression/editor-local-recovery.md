# Editor local recovery regression

## 2026-08-13 — editable work could disappear behind ambiguous export wording

- **Severity:** High — browser-only corrections existed only in live memory
  unless the user understood that **Export** meant downloading a WAM edit
  layer. Reloading the viewer could therefore lose completed guidance.
- **Cause:** The non-destructive editor exposed a draft and a portable export,
  but had no browser-local checkpoint, restore path, save timestamp, or label
  distinguishing a local save from a file-format download. The displayed
  palette was also informational rather than usable as a paint colour source.
- **Expected behavior:** Every completed correction updates a local recovery
  keyed by the exact WAM source hash and compiled-layer signature. Matching
  changes restore automatically, while **Save all changes** commits the draft
locally and **Download edit layer (.wamedit.json)** remains the explicit
portable backup.
  Palette swatches set the active colour without painting immediately.
- **Guard:** `tests/test_edits.py` protects the recovery key and validation
  boundary, automatic checkpoint calls, non-serialisation into `.wamedit.json`,
  interactive palette swatches, explicit English save/download labels, and
  tooltip coverage.
- **Fallback:** If browser storage is unavailable or full, editing continues
  normally and the viewer warns the user to download the portable layer.

## 2026-08-13 — browser recovery was mistaken for a project save

- **Severity:** High — a browser checkpoint could survive a reload, but it did
  not create the adjacent `.wamedit.json` or refresh the deliverables a user
  expected to share.
- **Cause:** A standalone `file://` page cannot safely choose a filesystem
  path or invoke the WAM compiler. Its earlier **Save all changes** wording
  did not make that boundary sufficiently explicit.
- **Expected behavior:** `Launch-Latest-Version.cmd edit <model.wam>` (or
  `python -m wam.editor_bridge <model.wam>` on macOS/Linux) starts
  a loopback-only session for exactly one source. **Save all changes & rebuild**
  validates the fingerprinted layer, stages a strict compilation, then writes
  `<model>.wamedit.json` beside the source and replaces its current outputs.
- **Guard:** `tests/test_editor_bridge.py` covers profile discovery, stale
  layers, token-gated loopback access, staged replacement, promotion rollback,
  and startup recovery. `tests/test_edits.py` verifies the connected and
  browser-only viewer wording.
- **Fallback:** If the connected rebuild fails, the previous sidecar and
  artifacts are restored; browser-local recovery and portable download retain
  the uncommitted corrections.
