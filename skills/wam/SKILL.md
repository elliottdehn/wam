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
Python package and `SPEC.md` (the full grammar). Requires Python 3 with numpy.

There are no bundled example models — **`SPEC.md` is the reference**. Read it
before authoring; every construct there has a worked snippet.

```bash
cd "$CLAUDE_PLUGIN_ROOT"                        # or the wam repo checkout
python3 -m wam.cli my.wam                       # -> out/my_sheet.png + .gltf
python3 -m wam.cli my.wam --anim walk --frames 6        # one row per --views
python3 -m wam.cli my.wam --anim guard --anim-views side  # pick the telling angle
python3 -m wam.cli my.wam --bones               # skeleton overlay render
python3 -m wam.cli my.wam --width 760 --height 560   # landscape, for long models
```

To compile a model that lives in another project:
`PYTHONPATH="$CLAUDE_PLUGIN_ROOT" python3 -m wam.cli /path/to/model.wam -o out/name`

## The iteration loop (non-negotiable)

1. Write or edit the `.wam` file.
2. **Write the `checks` for what you just added, in the same edit.** See the
   next section — this is not optional polish, it is how you avoid breaking
   things you already fixed.
3. Compile. **Read every lint warning** — they name the fix.
4. **View the rendered sheet PNG** (front / three-quarter / side / back).
5. Audit all four views before claiming success: does every attachment
   visibly touch its base? Is every prop oriented right? Does anything float,
   fold, or read as the wrong object? Are proportions sane?
6. Fix by editing angles and ratios, never by adding coordinate math.

Render animation strips (`--anim NAME --frames 6`) for every gait and action
you author — animation bugs (reversed gaits, non-moving cloth) are invisible
in static views. Strips honour `--views`; if a pose reads from only one
angle (a shield facing outward, a blade's edge), pass `--anim-views` for it
rather than squinting at a three-quarter view.

## Write checks constantly

Reading a render tells you the model is wrong *now*. A check tells you the
moment it goes wrong, three edits later, in a view you didn't render. Every
model should end up with **dozens** of them, and they cost a fraction of a
second even with animation sampling.

**The rule: the instant you verify something by eye, write it down as a
check before moving on.** You just squinted at the side view and decided the
snout clears the brow? That is `assert zmax(muzzle) > zmax(skull)`. You just
counted head-heights? That is an `assert`. Anything you confirmed once and
would have to re-confirm after the next edit belongs in `checks`.

Every model gets at least this battery — write it early, tighten the numbers
as the model settles:

```
checks
  # proportion — the thing eyes are worst at
  assert dist(shoulder.l, hip.l) / len(head) in 2.0..2.5
  assert height / len(head) in 6..8              # heroic humanoid
  assert width / height in 0.35..0.55
  # ground and placement
  assert bottom(foot.l) < 0.01
  assert lowest(walk) > -0.02                    # nothing sinks mid-stride
  assert zmax(muzzle) > zmax(skull)
  # mass balance
  assert volume(head) < volume(torso) / 3
  assert volume(arm.l) == volume(leg.l) / 2 +- 0.01
  # clearance and clipping, at rest and in motion
  assert gap(sword, thigh.r) > 0.015
  noclip in=*                                    # nothing intersects anything
  noclip kilt vs thigh.l,thigh.r in=walk         # cloth: name the pair
  noclip cape strict in=*
  assert clip(sword, shield) == 0
  # rigging — catches the bugs renders cannot show
  assert influences(cape) >= 2                   # not welded to one bone
  assert influences(wing.l) >= 2
  assert weight(kilt, thigh.l) > 0.3             # cloth actually follows
  assert bonecount(wing.l) >= 4
  # animation — every anim gets all four
  assert slide(foot.l, walk) < 0.02              # THE moonwalk detector
  assert swing(thigh.l, walk) in 25..60
  assert moves(cape, walk) > 0.03                # cloth is not frozen
  assert travel(hand.r, attack) > 0.15           # the swing actually swings
  # symmetry and budget
  assert asymmetry(torso) < 0.005
  assert tris < 6000
  assert influences(torso) <= 4
```

Turn every rule below into a check where you can. Rules 1, 4, and 7 in
particular now have exact detectors — `slide()`, `moves()`/`weight()`, and
`bottom()`/`lowest()` — so a violation should reach you as a failed check,
not as something you notice in a render (or don't).

`measure <label> <expr>` is the other half of the habit: when you don't yet
know the right bound, print the value every compile and watch it while you
tune. Convert it to an `assert` once you know what "right" is.

Read the `checks` section of SPEC.md for the full vocabulary — roughly forty
functions across position, direction, extent, mass, clearance, symmetry, rig
quality, and animation.

## Language quick reference

Sections in order: `model` (height in meters, `style chunky|smooth|fine`),
`palette` (named `#rrggbb`), `skeleton`, `parts`, `animations`. All lengths
are fractions of height. +Y up, +Z forward (character faces +Z), +X = the
character's left; ground is y=0. `mirror` … `end` blocks author the left
side; the compiler emits `.l`/`.r`.

**Skeleton** — turtle graphics: `bone thigh parent=pelvis side=0.07 dir=down
tilt=4 pitch=-12 len=0.21`. `pitch` rotates about +X (tips an `up` bone
forward, a `down` bone backward, a `fwd` bone downward), `yaw` about +Y,
`tilt` about +Z (side bones up/down, down bones splay). `pin <bone>
at=(x,y,z)` (or `tail=`) nails a bone's world position down so lengthening
an ancestor doesn't drag everything mounted downstream — never hand-solve a
compensating root offset.

**Parts** — five generators, all closed and skinned automatically:
- `loft` — cross-section rings along a bone chain (`bones=a..b`) or a free
  ray (`bone=x at=0.5 dir=fwd len=0.1`). Rings: `ring 0.55 w=0.36 d=0.31
  fwd=0.01 material=steel` (`material=` makes color bands: belts, sleeves,
  boots). `tip` collapses to a point; caps `flat|dome|point|none`.
  - `frame=up|fwd|side` / `refaxis=(x,y,z)` pins which axis `d` spans —
    **always set it on a diagonal part**, or rings flip 90° at the automatic
    frame's switchover and panels stand edge-on to what they should span.
  - `wtop=/wbot=/dtop=/dbot=` make the section asymmetric about its depth
    axis: keels, flat bellies, broad-shouldered trapezoid chests.
  - `material_arc=belly:200-340` runs a stripe *around* the tube (0° = the
    `w` axis, 90° = the `d` axis) — never author a belly as a separate part,
    it always reads as a disconnected slab with a hard seam.
  - `skin=boneA:0.6,boneB:0.4` overrides the computed binding for a ring.
- `sweep` — curved tube from a point: segments with `len r` and bends.
  Use `curl=` (about the transported side axis) for horns/rings/spirals;
  `up=`/`fwd=` world bends degenerate when the tangent aligns with the axis.
- `web` — **a membrane across a fan of bones**: wings, frills, fins, webbed
  feet, capes, sails. `web wing anchor=wrist material=membrane
  trailing=scallop` plus one `rib bones=a..b` line per spar (`inset=` stops
  short of the tip, `from=` re-roots the leading edge). The compiler builds
  one shared grid and blends each vertex across the ribs it lies between, so
  the panels never crack apart and every digit deforms it independently.
  Do not try to fake this with tubes — that is six failed topologies.
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
   Prove it with `noclip kilt vs thigh.l,thigh.r in=walk` — `gap()` will not
   catch this, and on a low-poly mesh it often reports a healthy positive
   distance while the leg is straight through the cloth.
5. **Caps**: never leave `cap none` unless the end is provably buried inside
   other geometry from every angle. A part buried *entirely* inside another
   is now a lint warning — it renders nothing and costs triangles; use `on=`
   to sit it on the surface instead.
6. **Folds**: if lint says a loft folds at a bend, split the loft at that
   joint (end the torso at the shoulders; give the neck its own loft) —
   folded surfaces read as invisible/missing faces.
7. **Ground**: lint reports floating or sunken feet with the distance;
   adjust the root height or limb lengths, then re-check.
8. **Nothing verified by eye stays verified by eye** — see *Write checks
   constantly*. Every rule on this list has a detector; use it.
9. **Membranes**: a surface between bones is a `web`, never a flattened tube.
10. **Framing**: if a model is longer than it is tall, render it landscape
   (`--width 760 --height 560`) — the default panel is portrait.
11. **Work from SPEC.md, not from memory** — there are no example models to
   crib from, and half the constructs (`web`, `frame=`, `material_arc=`,
   `pin`, `checks`) have no analogue in other 3D formats. Build in passes:
   skeleton first, then block out the masses with a few rings per part,
   compile and *look*, and only then add detail. A silhouette that reads
   wrong never gets fixed by adding more parts.

## Zones

`python3 -m wam.zone myzone.zone` compiles whole environments: `terrain`
landform ops (base/rim/opening/ridge/plateau/hill/basin/level, all with
optional `wobble` for organic edges), per-texel `surface` splat rules
(`rock where slope>32`, `sand where shore<5`, `dirt where road`), `river`/
`road` splines with chirped `meander`, generated `bridge from= to=` spans
(terrain-seated, arch-solved, clearance-linted), `water level=`, and
`place`/`scatter` prop placement (terrain-snapped, masked, seeded;
`raise=`/`float` modifiers). Read the Zones section of SPEC.md before
authoring; the same visual iteration loop applies — compile, read
warnings, look at the vista/overview renders, adjust named values.

## Outputs

- `out/<name>.gltf` — glTF 2.0, skinned + animated, imports into
  Blender/three.js/engines directly.
- `out/<name>_sheet.png` — 4-view turntable; `_anim_<x>.png` strips;
  `_bones.png` overlay.
- `out/<name>_viewer.json` — viewer data, emitted on every compile.
- `viewer/template.html` is a standalone WebGL viewer: open it directly in a
  browser and drop any `*_viewer.json` onto it (or use the Open button).
  `scripts/build_viewer.py <json> <out.html>` bakes a page with a model
  preloaded.
