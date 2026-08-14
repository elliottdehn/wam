---
name: wam
description: Author, compile, inspect, and revise WAM low-poly 3D characters, creatures, props, rigs, and animations with ChatGPT Codex. Use for WAM or .wam requests, WoW-style or game-ready models, and one or more reference images or requested render views.
---

# Use WAM with Codex

Resolve the repository root, then read `../../../skills/wam/SKILL.md` fully for
the canonical authoring workflow. Also read the files that workflow routes to;
paths there are relative to the repository root.

On Windows, use `.\.venv\Scripts\python.exe`. If it does not exist, run
`powershell -ExecutionPolicy Bypass -File .\Setup-WAM.ps1`. Use
`python -m wam.codex_cli` through that interpreter for predictable JSON.

For one or more supplied images, prepare every source before authoring:

```powershell
.\.venv\Scripts\python.exe -m wam.codex_cli references `
  --reference front="C:\path\front.png" --view front=front `
  --reference side="C:\path\side.png" --view side=side `
  --reference crest="C:\path\crest detail.png" --kind crest=detail `
  -o out\references
```

Read `out/references/references.json`, inspect every reference and its crop
group independently, and keep notes keyed by reference ID. The overview board
is navigation, not sufficient evidence by itself.

Compile with all telling views, including custom `id:yaw[:pitch]` angles when
a source image does not match a standard view:

```powershell
.\.venv\Scripts\python.exe -m wam.codex_cli compile model.wam `
  --views front,threequarter,side,threequarter_back,back,high:25:30
```

Read the returned JSON and `_views.json`, then inspect every listed view PNG
individually. Do not approve a model from the combined sheet alone.

When a human needs to guide a revision directly, hand over the compiled
`*.html` viewer. Its Edit mode exports a fingerprinted `.wamedit.json` layer;
the `.wam` remains untouched. Recompile an exported layer explicitly, then
inspect all new per-view PNGs before treating its changes as accepted:

```powershell
.\.venv\Scripts\python.exe -m wam.codex_cli compile model.wam `
  --edits out\model.wamedit.json
```

Do not hand-author face indexes or bypass a stale-layer error. The viewer
creates the part/local-face references, recognises `.l/.r`, `_l/_r`, and
`.mirror` pairs, and can store an explicit mirror target for a transform when
the character deliberately uses asymmetric geometry or colours.
