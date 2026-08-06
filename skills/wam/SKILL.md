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
3. Compile. **Read every lint warning** — they name the fix. Many are
   ambient and need no `checks` entry: unknown/misspelled keys (silently
   dropped otherwise), parts intersecting, a mirrored limb crossing the
   centreline from an inverted sign, chain rotations compounding into a fold,
   an animation that moves nothing, a membrane outline that never closes,
   a part buried inside another. Treat a clean compile as meaningful.
4. **View the rendered sheet PNG** (front / three-quarter / side / back).
5. Audit all four views before claiming success: does every attachment
   visibly touch its base? Is every prop oriented right? Does anything float,
   fold, or read as the wrong object? Are proportions sane? If there is a
   reference image, audit it in crops — see *Working from a reference image*.
6. Fix by editing angles and ratios, never by adding coordinate math.

Render animation strips (`--anim NAME --frames 6`) for every gait and action
you author — animation bugs (reversed gaits, non-moving cloth) are invisible
in static views. Strips honour `--views`; if a pose reads from only one
angle (a shield facing outward, a blade's edge), pass `--anim-views` for it
rather than squinting at a three-quarter view.

## Working from a reference image

If the user supplied a reference, **never judge the model by laying the whole
reference next to the whole render sheet.** At that scale you are comparing
silhouettes and nothing else: a head 20% too large, a pauldron sitting an
inch low, a muzzle with the wrong taper, and horns curving the wrong way all
survive a whole-image comparison intact. Every one of them is obvious in a
crop. Comparing whole-to-whole is how a model gets declared finished while
being wrong in six places.

Work regions, at matched scale:

```bash
# 1. see what the reference actually contains, region by region
python3 scripts/crop.py ref.jpg --grid 3x3 -o out/refcrops

# 2. put one region from each image side by side, each aimed separately
python3 scripts/crop.py ref.jpg out/mymodel_sheet.png \
    --box 0.30,0.00,0.70,0.35 --box2 0.05,0.02,0.20,0.30 -o out/cmp_head.png
```

(From another project, call it as `"$CLAUDE_PLUGIN_ROOT/scripts/crop.py"`.)
Boxes are normalized `x0,y0,x1,y1` in 0..1, so they do not depend on either
image's pixel size. `--box2` aims the render separately — you will always
need it, because the reference is framed differently from a four-view sheet.
Read the written file; both crops arrive at the same height in one image.

1. **Match the view before comparing anything.** Find which of
   front/threequarter/side/back is closest to the reference's camera and
   compare against that panel. Re-render with `--views threequarter` alone if
   it helps. Comparing a 3/4 reference to a front render produces confident,
   completely wrong conclusions about width and depth.
2. **Then walk the regions** — head and face, shoulder/neck junction, hands
   and what they hold, hips and belt line, feet and ground contact, and every
   signature detail the reference is recognizable by (a horn curve, a
   pauldron spike, a tabard shape). One `cmp_*.png` per region.
3. **Say what differs in words before editing** — "the muzzle is half the
   reference's length and sits too high" — then make the one edit that
   addresses it. Vague dissatisfaction produces flailing.
4. **Turn every finding into a `check`.** A crop tells you the head is too
   big *today*; `assert height / height(skull) in 6..7` keeps it right after
   the next twenty edits. This is the whole point — see below.

Crops are also the only way to judge texture work: atlas ops (`noise`,
`streaks`, `planks`) are invisible at sheet scale and obvious at 3x.

## Split the character into models

**Anything worn or carried belongs in its own `.wam` file** — armour, helmet,
cloak, weapon, shield, backpack, saddle. Welding them into the body file is
the most common way a character goes wrong: the gear cannot be reused on
another body, cannot be swapped for a variant, cannot be inspected or checked
on its own, and every "same orc with a different axe" duplicates the entire
orc. A real model measured 35% equipment-layer parts fused into the body.

```
compose knight
  base body
  graft plate                                   # names match -> fuses to the body's bones
  graft hammer to=hand.r:0.5 dir=up pitch=35    # no match -> joins as new bones
  graft cape  to=spine                          # a mixture: spine fuses, sway bones join
```

There is one operation. Bones that **match by name fuse** (the geometry
re-solves onto the host's bone and adapts to its length); bones with **no
match join** the skeleton as new children, keeping their own children and
their own animations. Armour is all-fuse, a weapon is all-join, a cape is
both. Use `fuse=none` when the grafted model is a whole creature that happens
to share bone names — a rider would otherwise weld itself into its mount.

Do split: plate sets, helmets, pauldrons, cloaks, weapons, shields, quivers,
mounts and their saddles — anything a *different* character could plausibly
wear, or that this character could plausibly take off.

Do **not** split integral anatomy: a golem's plating, a beetle's carapace, a
dragon's scales. If it cannot come off, it is the body.

Two rules that make it work:

1. **Use conventional bone names** — `pelvis spine neck head`, `shoulder
   upperarm forearm hand`, `thigh shin foot`. `wear` binds by name, so
   equipment written against those names fits every body that uses them, and
   stretches to a longer limb instead of sliding off it. Inventing names locks
   the gear to one character forever.
2. **Author the worn model alone first.** It compiles, renders and checks by
   itself — give it a skeleton matching the intended wearer, and iterate on it
   without recompiling the character.

**Aim relative to the bone, not the world.** `across=fwd` means "perpendicular
to that bone, leaning forward"; `along`/`against` run with or counter to it.
Both `graft` and `group` take them. A hand-tuned `pitch=90` is a fact about
one rest pose — repose the wrist and it drifts 42 degrees off square, while
`across=fwd` stays perpendicular. If you find yourself saying "it should be
perpendicular to the wrist", write that, do not convert it to Euler angles.

**`closer(a, b, c)` is the relational check**: how much nearer `a` is to `b`
than to `c`. `assert closer(hand.r, sword.pommel, sword.tip) > 0` says the
sword is held by the hilt — it reads +1.36 when correct and −1.36 when gripped
by the blade. Pick references that are not equidistant by symmetry: comparing
to a spine at z=0 gives the same answer for a blade pointing forward and one
pointing backward.

**Name the points you need to argue about.** `marker tip at=(0,0.78,0)` in the
prop; then `assert dist(knight.sword.tip, knight.spine) > dist(knight.sword.pommel,
knight.spine)` says "points away from the body" without a single angle, and
`closer(a, b, c)` says which of two things something is nearer. A part's
bounding-box centre is halfway up a blade and tells you nothing.

**Never aim a held prop by hand.** Give the prop an `anchor` — the point and
axis by which it meets something — and graft with `align=`:

```
anchor grip at=(0,0.06,0) dir=up               # in the weapon
graft hammer to=hand.r:0.6 align=grip pitch=-155   # in the set
```

The grip lands in the hand by construction. Add `across=` for the aim axis and
`face=<marker>:<dir>` for the roll — with all three there is no guessed number
left: `graft sword to=hand.r align=grip across=fwd face=edge:up`. A weapon floating
beside the fist is the single most common composition bug, and no proximity
check catches it — a held hammer and a floating one measure the same distance
from the arm. The compiler warns if you graft a model that declares an anchor
without using it.

Worn coverings are checked for you too: `leak(garment, body)` asks how far the
body gets outside the garment *within the span the garment covers*, so legs
below a hem are ignored and a thigh through the cloth is not. A thigh bursting
out of a skirt is reported automatically, with the depth. Neither `covers` nor
`clip` can see this — `clip` false-alarms on any capped garment and misses a
garment the body has swallowed entirely.

Held props are checked for you: anything grafted that fuses no bones is
carried, and a carried thing that intersects its carrier is reported with the
depth and the two parts. That is the "sword reversed into the chest" bug, and
it needs no assertion. Add the `overlap` flag to a graft that is supposed to
intersect (a rider on a mount).

Then check the fit in the set file: `covers(knight.plate.cuirass,
knight.body.torso) > 0.45` proves the armour actually contains the body, which
`gap` and `clip` cannot tell you (both read 0 when one part swallows another).

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

Turn every rule below into a check where you can. The gait, cloth and
ground rules in particular now have exact detectors — `slide()`, `moves()`/`weight()`, and
`bottom()`/`lowest()` — so a violation should reach you as a failed check,
not as something you notice in a render (or don't).

`measure <label> <expr>` is the other half of the habit: when you don't yet
know the right bound, print the value every compile and watch it while you
tune. Convert it to an `assert` once you know what "right" is.

When a model joins a set that already exists, add it to the `.wamset` file
and assert it against the others (`python3 -m wam.modelset x.wamset`):

- `checks` compares models — sizes there are **meters**, not the
  height-fractions used inside a model. `fill()` catches a model that does
  not span the height it declares, which silently corrupts every ratio
  computed against it.
- `every` runs ordinary model-level checks inside *each* member, so shared
  conventions (`ground == 0`, a poly budget, `noclip`) live in one file
  instead of the top of every model.
- `silhouette(a, b)` is the one check no model can make about itself: two
  units can each be good and still be indistinguishable at distance. Assert
  `> 0.25` — reskins measure ~0.1, genuinely different creatures ~0.4+.

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

**Rescaling** — never hand-edit sizes across a file. Absolute size is the
`height` directive alone (one number; internal proportions and the model's
own checks are unaffected). Shape is `girth` (all cross-sections) and `reach`
(all lengths along a path) in the `model` section — one number each, tunable
against a check, and applied to the authored numbers so animation is
unaffected. Editing the ~200 size tokens by hand is how a rescale goes
silently wrong.

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
  **Bend with `up=/down=/fwd=/back=`** — world-referenced and intention-named,
  so no sign is ever guessed. `curl=` bends about the sweep's own transported
  side axis, which is invisible in the source and accumulates (4×`curl=45` is
  a half turn back over the root); keep it for rings and tight spirals, where
  world bends degenerate. Every bent sweep prints its realized path
  (`leaves down-fwd and ends up pointing up-fwd, tip +0.212 in y`) — read it.
  A sweep is **rigid** unless given `bones=a..b`, which binds its rings along
  that chain by arc length. Decide that before tuning the shape: converting
  afterwards means re-deriving every segment length as a ring fraction.
- `web` — **a membrane across a set of ribs**: wings, frills, fins, webbed
  feet, capes, sails. `web wing anchor=wrist material=membrane
  trailing=scallop` plus one `rib bones=a..b` line per spar (`inset=` stops
  short of the tip, `from=` re-roots the leading edge). The compiler builds
  one shared grid and blends each vertex across the ribs it lies between, so
  the panels never crack apart and every digit deforms it independently.
  Do not try to fake this with tubes — that is six failed topologies.
  **For a wing, the ribs must not all share one anchor.** A fan from a single
  hub can only be a sector, which is a webbed hand; membrane exists only
  between ribs, so nothing fills behind the arm and the silhouette is a Y
  rather than a diamond. Anchor the leading rib at the body, the spars at the
  wrist, and trailing ribs at the *elbow* and the hip with `from=`. The
  compiler warns when a multi-rib outline fails to close back toward the
  body. Tuning rib lengths or sweep angles never fixes a fan — the topology
  is wrong, not the numbers.
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
   backward (the moonwalk bug). `slide()` measures the *forward* travel of a
   planted foot, so a correct in-place cycle reads 0 even though the foot
   covers a full stride backwards through stance — that part is not a bug and
   there is no root translation channel to remove it.
2. **Weapons and props**: always a `group` with `dir=` aiming. Never
   hand-derive world rotations across a multi-part prop.
3. **`on=` presses**: a part placed with `on=` is aimed along the surface
   normal where it lands, so identical `dir=up` spikes each stand square on a
   curved surface. `dir=` therefore selects which axis meets the normal and
   `pitch/yaw/tilt` are deviations from square. If the authored direction was
   the point (a rib wrapping sideways, a swept-back horn), use `press=off` —
   the lint reports every part turned more than 20°, so check that line.
4. **Attachments**: host every part on the bone it *visually* attaches to —
   a tabard on the chest hosted on the pelvis will not move when the chest
   does (lint catches this). Use `on=` for small-onto-big surface contact;
   do not use `on=` to place a big plate on a thin shaft (ambiguous snap) —
   use a `group` local position instead.
5. **Cloth**: any skirt-like part needs `follow=` rings or legs clip through.
   Prove it with `noclip kilt vs thigh.l,thigh.r in=walk` — `gap()` will not
   catch this, and on a low-poly mesh it often reports a healthy positive
   distance while the leg is straight through the cloth.
6. **Caps**: never leave `cap none` unless the end is provably buried inside
   other geometry from every angle. A part buried *entirely* inside another
   is now a lint warning — it renders nothing and costs triangles; use `on=`
   to sit it on the surface instead.
7. **Folds**: if lint says a loft folds at a bend, split the loft at that
   joint (end the torso at the shoulders; give the neck its own loft) —
   folded surfaces read as invisible/missing faces.
8. **Ground**: lint reports floating or sunken feet with the distance;
   adjust the root height or limb lengths, then re-check.
9. **Nothing verified by eye stays verified by eye** — see *Write checks
   constantly*. Every rule on this list has a detector; use it.
10. **Membranes**: a surface between bones is a `web`, never a flattened tube.
11. **Framing**: if a model is longer than it is tall, render it landscape
   (`--width 760 --height 560`) — the default panel is portrait.
12. **Work from SPEC.md, not from memory** — there are no example models to
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
