# WAM repository guidance

Use `.agents/skills/wam/SKILL.md` for character, creature, prop, rig,
animation, reference-image, or `.wam` work.

## Local setup

- On Windows, run `powershell -ExecutionPolicy Bypass -File .\Setup-WAM.ps1`.
- Use `.\.venv\Scripts\python.exe` explicitly after setup. Do not depend on
  whichever `python` or `python3` alias happens to be first on `PATH`.
- Use `python -m wam.codex_cli` for agent work because it reserves stdout for
  deterministic JSON. `python -m wam.cli` remains the human-oriented command.

## Multi-image and multi-view contract

- Prepare every supplied reference with `wam.codex_cli references`; keep
  whole-object views, detail images, and palette images as separate named
  evidence.
- Read every entry and crop group in `references.json`; do not summarize all
  references from a single contact sheet.
- Compile with all relevant named or custom views. Read `_views.json`, then
  inspect every listed PNG individually before judging the combined sheet.
- Unknown, empty, and duplicate view names are errors. Custom views use
  `id:yaw[:pitch]` in degrees.
- Preserve the historical `.gltf`, viewer HTML, and sheet outputs when changing
  render behavior.

## Validation and safety

- Run `.\.venv\Scripts\python.exe tests\run_all.py` for the complete suite;
  the runner keeps the repository's script-style tests isolated.
- Add useful English comments for non-obvious contracts and fallbacks.
- Never upload or publish a generated model unless the user explicitly asks.
  Read `PUBLISHING_MUST_READ.md` immediately before any publishing action.
