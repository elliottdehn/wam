---
name: wam
description: Author low-poly WoW-style 3D characters — mesh, skeleton, and animations — in the WAM text language, compiled to glTF plus turntable PNG renders. Use when the user wants to create or edit a stylized/low-poly 3D character, creature, or prop with a rig and animations, mentions WAM or .wam files, or asks for "WoW-style" / game-ready models.
---

# Authoring WAM models

WAM is a text language designed so an LLM can author 3D characters reliably.
The founding rule: **you only make discrete, named, relative, symmetric
decisions** (bone angles, ring widths, palette colors); the compiler generates
every vertex, skin weight, normal, and winding. If you find yourself deriving
world-space coordinates or rotation matrices by hand, you are misusing the
language — there is always a construct that removes the math.

## Setup

The compiler ships with this skill: the plugin root (the directory containing
`skills/`, available as `$CLAUDE_PLUGIN_ROOT` when set) holds the `wam/`
Python package, `SPEC.md` (full grammar), and `models/` (four reference
models: `tauren.wam`, `human.wam`, `wolf.wam`, `orc.wam`). Requires Python 3
with numpy.

```bash
cd "$CLAUDE_PLUGIN_ROOT"                        # or the wam repo checkout
python3 -m wam.cli models/tauren.wam            # -> out/tauren_sheet.png + .gltf
python3 -m wam.cli my.wam --anim walk --frames 6
python3 -m wam.cli my.wam --bones               # skeleton overlay render
```

To compile a model that lives in another project:
`PYTHONPATH="$CLAUDE_PLUGIN_ROOT" python3 -m wam.cli /path/to/model.wam -o out/name`

## The iteration loop (non-negotiable)

1. Write or edit the `.wam` file.
2. Compile. **Read every lint warning** — they name the fix.
3. **View the rendered sheet PNG** (front / three-quarter / side / back).
4. Audit all four views before claiming success: does every attachment
   visibly touch its base? Is every prop oriented right? Does anything float,
   fold, or read as the wrong object? Are proportions sane?
5. Fix by editing angles and ratios, never by adding coordinate math.

Render animation strips (`--anim NAME --frames 6`) for every gait and action
you author — animation bugs (reversed gaits, non-moving cloth) are invisible
in static views.

## Language quick reference

Sections in order: `model` (height in meters, `style chunky|smooth|fine`),
`palette` (named `#rrggbb`), `skeleton`, `parts`, `animations`. All lengths
are fractions of height. +Y up, +Z forward (character faces +Z), +X = the
character's left; ground is y=0. `mirror` … `end` blocks author the left
side; the compiler emits `.l`/`.r`.

**Skeleton** — turtle graphics: `bone thigh parent=pelvis side=0.07 dir=down
tilt=4 pitch=-12 len=0.21`. `pitch` rotates about +X (tips an `up` bone
forward, a `down` bone backward, a `fwd` bone downward), `yaw` about +Y,
`tilt` about +Z (side bones up/down, down bones splay).

**Parts** — four generators, all closed and skinned automatically:
- `loft` — cross-section rings along a bone chain (`bones=a..b`) or a free
  ray (`bone=x at=0.5 dir=fwd len=0.1`). Rings: `ring 0.55 w=0.36 d=0.31
  fwd=0.01 material=steel` (`material=` makes color bands: belts, sleeves,
  boots). `tip` collapses to a point; caps `flat|dome|point|none`.
- `sweep` — curved tube from a point: segments with `len r` and bends.
  Use `curl=` (about the transported side axis) for horns/rings/spirals;
  `up=`/`fwd=` world bends degenerate when the tangent aligns with the axis.
- `attach` — stock parts: `hoof box sphere eye` with `size w d h taper`.
- `group` — **compound props (weapons, etc.)**: author once in a clean local
  frame (spine along +Y), mount with one aim:
  `group axe bone=hand.r at=1.0 dir=up pitch=45 yaw=-15` then parts inside
  use `at=(x,y,z)` local positions and local axis words; `spin=` rotates
  about the prop's spine. End with `end`.
- `on=<part>` snaps a part's origin to an earlier part's surface and sinks it
  by `inset=` — spikes, ears, tusks, eyes are flush **by construction**.
- `follow=<bone>:<frac>` on a ring = cloth: vertices split left/right to a
  mirrored bone pair (kilts, tabards, loincloths swing with the legs).

**Animations** — rotations are deltas from rest about the bone head
(`pitch/yaw/roll/tilt`; `tilt` flares arms sideways). Two styles: pose
keyframes (`pose crouch` + `anim death … key 30% pose=crouch`) and channels
(`ch thigh.l pitch 0%=-21 50%=18 100%=-21`). `mirrorphase 50%` mirrors `.l`
channels to `.r` half a cycle later — the gait idiom (use `0%` for symmetric
actions like a double-arm smash).

## The rules (each learned from a real failure)

1. **Gaits**: knees/hocks flex during the *swing* (upper leg moving forward)
   and stay extended through stance — flexing during stance reads as walking
   backward (the moonwalk bug).
2. **Weapons and props**: always a `group` with `dir=` aiming. Never
   hand-derive world rotations across a multi-part prop.
3. **Attachments**: host every part on the bone it *visually* attaches to —
   a tabard on the chest hosted on the pelvis will not move when the chest
   does (lint catches this). Use `on=` for small-onto-big surface contact;
   do not use `on=` to place a big plate on a thin shaft (ambiguous snap) —
   use a `group` local position instead.
4. **Cloth**: any skirt-like part needs `follow=` rings or legs clip through.
5. **Caps**: never leave `cap none` unless the end is provably buried inside
   other geometry from every angle.
6. **Folds**: if lint says a loft folds at a bend, split the loft at that
   joint (end the torso at the shoulders; give the neck its own loft) —
   folded surfaces read as invisible/missing faces.
7. **Ground**: lint reports floating or sunken feet with the distance;
   adjust the root height or limb lengths, then re-check.
8. **Study a reference model first** (`models/orc.wam` is the most complete:
   groups, `on=`, `follow=`, six animations) and copy its patterns.

## Outputs

- `out/<name>.gltf` — glTF 2.0, skinned + animated, imports into
  Blender/three.js/engines directly.
- `out/<name>_sheet.png` — 4-view turntable; `_anim_<x>.png` strips;
  `_bones.png` overlay.
- `wam/viewer_export.py` + `viewer/template.html` build a self-contained
  WebGL viewer page (inject the exported JSON at `/*__DATA__*/`).
