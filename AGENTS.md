# WAM repository guidance

Use `.agents/skills/wam/SKILL.md` for character, creature, prop, rig,
animation, reference-image, or `.wam` work.

Use `AUDIO_SPEC.md` for music, sound-effect, or `.wama` work. Audio compiles
with `python3 -m wam.audio_cli piece.wama --stems`; review the emitted
`out/<name>_sheet.png` and the metrics in `out/<file>_audio.json` rather than
assuming a render is right, and state the intent as `assert` lines in the file
so a broken render announces itself.

## Local setup

- On Windows, run `powershell -ExecutionPolicy Bypass -File .\Setup-WAM.ps1`.
  On macOS and Linux, run `./setup-wam.sh`. Both create the same `.venv`.
- Use that virtual environment's interpreter explicitly afterwards:
  `.\.venv\Scripts\python.exe` on Windows, `./.venv/bin/python3` on macOS and
  Linux. Do not depend on whichever interpreter is first on `PATH`.
- Commands below are written `python3`, which resolves on macOS and Linux both
  inside and outside the venv. Windows has no `python3`: use
  `.\.venv\Scripts\python.exe` or `py -3` there.
- Use `python3 -m wam.codex_cli` for agent work because it reserves stdout for
  deterministic JSON. `python3 -m wam.cli` remains the human-oriented command.

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

## Colour

- Colours are authored as sRGB hex and held internally as **linear**;
  `wam/color.py` owns both conversions and is the only module that should.
- Colour enters through `parser._hex_color` (and `edits._color`) and leaves
  through `render.write_png` / `png_bytes`. A new consumer that wants sRGB
  encodes at its own edge; a new exporter that wants linear writes the value
  it already has.
- `tests/test_colorspace.py` holds the guarantee that whatever hex an author
  writes is what an importer receives.
- **Anything that builds an image and hands it to `write_png` must declare its
  own palette in linear too** — backdrops, grid lines, colour ramps. A constant
  that exists to *look* a certain way is a display value and will be encoded
  twice otherwise. `wam/render.py` (`SHEET_BG`, `PAD_BG`) and
  `wam/audio_sheet.py` both do this; the audio sheets washed out when they did
  not. A colour *ramp* is the exception worth knowing: mix it in display space
  and convert once at the end, or its stops stop being evenly spaced as seen.

## Validation and safety

- Run `python3 tests/run_all.py` for the complete suite; the runner keeps the
  repository's script-style tests isolated. Forward slashes work on every
  supported platform, including Windows.
- Add useful English comments for non-obvious contracts and fallbacks.
- Never upload or publish a generated model unless the user explicitly asks.
  Read `PUBLISHING_MUST_READ.md` immediately before any publishing action.
