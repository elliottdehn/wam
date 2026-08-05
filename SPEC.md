# WAM — WoW-ish Art Model language

A text language for describing low-poly character models, rigs, and animations
in a form that LLMs can author reliably. The design rule: the author only makes
**discrete, named, relative, symmetric** decisions; the compiler makes every
continuous/spatial one (vertices, skinning weights, normals, winding). It is
structurally impossible to produce a non-manifold mesh or a disconnected rig.

Toolchain: `python3 -m wam.cli foo.wam` → glTF 2.0 (skinned + animated)
plus turntable PNG renders for visual iteration.

```
python3 -m wam.cli mymodel.wam            # sheet PNG + glTF + viewer JSON
python3 -m wam.cli mymodel.wam --bones    # also render skeleton overlay
python3 -m wam.cli mymodel.wam --anim walk --frames 6
python3 -m wam.cli mymodel.wam --anim guard --anim-views side,front
python3 -m wam.cli mymodel.wam --width 760 --height 560   # landscape panels
```

Animation strips honour `--views` (one row of frames per view) unless
`--anim-views` overrides them — a pose is often only legible from one angle,
and a strip shot from a single hardcoded camera hides exactly the thing you
are checking. A looping anim samples phases 0 … (n−1)/n, since phase 1 is
phase 0 again; a one-shot samples 0 … 1 so its final pose is actually shown.

Panels default to 480×600 portrait; pass `--width/--height` for anything
longer than it is tall. The camera fits the model's **projected extents** in
each view (not its bounding sphere) and uses one distance for the whole
sheet, so panels share a scale and nothing crops off the sides.

Every compile also writes `out/<name>_viewer.json` — open
`viewer/template.html` in a browser and drop the JSON on it to inspect the
model interactively (orbit, wireframe, skeleton, animation playback).

## Conventions

- **Coordinates**: +Y up, +Z forward (the character faces +Z), +X = the
  character's **left**. Mirroring reflects across X=0. Ground plane is y=0.
- **Units**: every length is a **fraction of the model's height** (`height` is
  the only value in meters). A 0.20 thigh on a 2.6 m character is 52 cm.
- **Angles** are degrees. Rotations used to aim bones:
  - `pitch` — rotation about +X. Tips an `up` bone **forward**; a `down` bone
    backward; a `fwd` bone downward. (Right-hand rule about the left axis.)
  - `yaw` — rotation about +Y. Turns a `fwd` bone toward the left.
  - `tilt` — rotation about +Z. Tips a `side` bone up (+) or down (−);
    splays a `down` bone outward (+).
- Comments start with `#` (hex colors like `#6b4a33` are still parsed).
- Sections appear in order: `model`, `palette`, `skeleton`, `parts`,
  `textures` (optional), `animations`, `checks` (optional). A `mirror` … `end`
  block inside `skeleton`/`parts` authors the left side only; the compiler
  emits `.l` and a reflected `.r`.

## `model`

```
model tauren
  height 2.6          # meters
  style chunky        # chunky=8-sided lofts, smooth=12, fine=16
```

## `palette`

Named flat colors. Any material without a `textures` entry renders as a
flat fill; the hand-painted look comes from layering the `textures` section
on top of these bases:

```
palette
  fur  #6b4a33
  horn #cbbfa4
```

## `skeleton`

Bones are turtle-graphics: parent + world-space direction + length. Only the
root has a position.

```
skeleton
  root pelvis at 0.55                      # y only, or at (x,y,z)
  bone spine1 parent=pelvis dir=up pitch=10 len=0.13
  bone neck   parent=spine2 dir=fwd pitch=-56 len=0.12
  mirror
    bone clavicle parent=spine2 dir=side tilt=-14 len=0.21
    bone thigh    parent=pelvis side=0.075 dir=down tilt=4 pitch=-12 len=0.21
  end
```

- `dir=` one of `up down fwd back side in` (`side`=+X=left; mirrored copies flip).
- `at=<t>` attaches the bone at fraction *t* along the parent (default 1.0 = tail).
- Head offsets from that point: `side= up= fwd=` scalars or `offset=(x,y,z)`.
- Inside `mirror`, `parent=` references resolve to the same-side bone first
  (`clavicle` → `clavicle.l`), falling back to central bones.

**Pinning** — every bone's position is derived from its ancestors, so
lengthening the trunk moves every wing, limb, and prop mounted downstream.
Rather than hand-solving a compensating offset, nail the bone down:

```
skeleton
  ...
  pin wing_root at=(0.09,1.66,0.20)      # absolute head, whatever the spine does
  pin chest tail=(0,1.07,0)              # or hold the tip and let the head follow
  bone horn parent=skull head=(0.05,1.9,0.1) dir=up len=0.10   # same, inline
```

A pin overrides where the parent chain would have put the bone; the bone
still belongs to the hierarchy and still animates with its parent. Inside a
`mirror` block the pin coordinates reflect for the `.r` copy like any other
offset — write `pin wing_root.r` to target one side literally.

## `parts`

Four generators (`loft`, `sweep`, `web`, `attach`) plus the `group` construct
for compound props. Everything is closed and skinned automatically; ring/segment
counts come from `style` (override per part with `sides=`). `shape=` on a
loft line sets the default cross-section for all its rings.

### `loft` — cross-section rings along a bone chain

The workhorse: torso, limbs, head, tail, ears, beard.

```
loft torso bones=pelvis..neck material=fur
  ring 0.00 w=0.33 d=0.28            # t along the chain, full width / depth
  ring 0.40 w=0.34 d=0.33 fwd=0.015  # center nudged forward (belly)
  ring 0.70 w=0.42 d=0.30            # shoulder hump
  ring 1.00 w=0.145 d=0.14
  cap start=dome end=flat            # flat | dome | point | none
```

- `bones=a..b` lofts along an ancestor chain; `bone=x at=0.5 dir=fwd len=0.1`
  lofts along a free ray from a point on one bone (muzzle, ears).
- Ring options: `d=` depth (defaults to `w`), `fwd= side= up=` center offsets,
  `shape=round|squarish|box`, `roll=`, `material=` (band + cap override),
  `tip` (converge to a point).
- `w` spans the side axis, `d` spans the **depth axis** — see `frame=` below.
- `fwd=`/`side=` offsets move the ring center along its own axes *after*
  `roll=`, so they mean what the rolled section looks like; `up=` is always
  world +Y.
- Skinning: each ring binds to the chain bone under it, blending near joints.
- **Cloth**: `follow=<bone>:<frac>` on a ring makes its vertices partially
  follow a mirrored bone pair, split left/right per vertex — the kilt idiom:
  `ring 1.00 w=0.41 follow=thigh:0.85` makes the hem swing with the legs
  instead of letting them clip through.

#### `frame=` — pin the ring's depth axis

By default the depth axis is chosen automatically: forward (+Z) for
mostly-vertical paths, up (+Y) for mostly-forward ones, switching at 45°-ish
of path tilt. A part that runs diagonally sits near that switch, and rings on
either side of it stand at right angles to each other for no visible reason.
Say which axis you meant:

```
loft body bones=pelvis..neck frame=up      # d spans up/down, w spans left/right
loft blade bone=hand dir=fwd len=0.4 refaxis=(0.3,1,0)
```

`frame=auto|up|fwd|side` picks a world axis; `refaxis=(x,y,z)` gives an
arbitrary one. Either way `d` spans the reference axis projected into the
ring plane, and `w` spans the perpendicular. Lint warns if the axis you named
runs along the part instead of across it.

#### Asymmetric sections

Rings are centered superellipses, so `w`/`d` are symmetric about the center —
which makes a keel, a flat belly, or a broad-shouldered inverted trapezoid
inexpressible. Override either half of the depth axis independently:

```
ring 0.60 w=0.34 wtop=0.34 wbot=0.22 dtop=0.22 dbot=0.34
```

`wtop`/`dtop` apply to the +depth half of the section, `wbot`/`dbot` to the
−depth half; each falls back to plain `w`/`d`. Pair this with `frame=up` so
"top" reliably means up.

#### `material_arc=` — bands *around* the tube

`material=` on a ring bands the tube along its length. To run a stripe down
it instead — a pale belly, a dorsal ridge, a painted panel — name an arc in
degrees, where 0° is the +side axis (`w`) and 90° the +depth axis (`d`):

```
loft body bones=pelvis..neck frame=up material=hide material_arc=belly:200-340
  ...
  ring 0.60 w=0.34 material_arc=belly:190-350,ridge:80-100   # per-ring override
```

Arcs may wrap past 360 (`330-30`). This replaces the old workaround of
authoring the belly as a separate part, which always read as a disconnected
slab with a hard seam. Vertices split at every material boundary — along the
tube *and* around it — so the color edge stays crisp.

#### Explicit skin weights

`skin=<bone>[:<w>],…` on a ring (or on the `loft`/`sweep` line, or a `seg`)
replaces the computed binding for those vertices. Weights are relative and
get normalized; glTF keeps the four strongest:

```
ring 0.40 w=0.30 skin=wf2:0.6,wf3:0.4
```

Use it when a surface belongs to several bones at once and `follow=` (which
resolves exactly one mirrored pair, split by world x) is the wrong shape —
though for a membrane spanning a fan of bones, reach for `web` instead, which
computes the blend for you.

### `sweep` — a curved tube from a point (horns, tusks)

Segments accumulate bends: `up=+deg` bends the tip toward world +Y,
`fwd=+deg` toward world +Z, and `curl=+deg` rotates about the sweep's own
(transported) side axis. Use `curl` for rings and tight spirals — the
world-referenced bends degenerate when the tangent aligns with their axis.

```
sweep horn bone=head at=0.50 offset=(0.085,0.035,0) dir=side yaw=12 material=horn
  seg len=0.05 r=0.036 up=15
  seg len=0.06 r=0.027 up=45
  seg len=0.055 r=0.016 up=55 fwd=15
  seg len=0.045 r=0.008 up=35 fwd=20 tip
```

Segments also take `material=` (a band from that segment onward) and `skin=`.
A sweep honours `frame=`/`refaxis=` for its starting cross-section.

### `web` — a membrane across a fan of bones

`loft` and `sweep` both emit closed tubes, so a surface spanning several
bones — a wing, a frill, a fin, a webbed foot, a cape, a sail — cannot be
built out of them. A `web` lofts the surface *between* ribs:

```
mirror
  web wing anchor=wrad:0.0 material=membrane trailing=scallop scallop=0.22
    rib bones=whum..wf1 from=spine2:0.9      # leading edge, rooted at the body
    rib bones=wf1..wf1 inset=0.06
    rib bones=wf2..wf2 inset=0.06
    rib bones=wf3..wf3 inset=0.06
    rib bones=wf4..wf4 inset=0.10
end
```

- Each `rib` is a bone chain (`bones=a..b` or `bone=x`) running out from the
  shared `anchor=<bone>[:<t>]`, or from its own `from=<bone>[:<t>]`.
- `inset=` stops the membrane short of the rib tip (claws stick out past it);
  `start=` moves where it begins.
- `trailing=scallop` bows the outer edge back between rib tips —
  `scallop=` sets how far (default 0.2 of the span). `trailing=straight`
  (the default) runs the edge tip to tip.
- `steps=` rows along the ribs, `usteps=` columns per panel (default 6 / 2).
- `thickness=` makes the membrane a closed slab instead of a single surface;
  a zero-thickness web is emitted as one sheet and its material is marked
  double-sided automatically.

Two things the compiler does that you would otherwise do by hand and get
wrong: the whole fan is **one grid**, so adjacent panels share their boundary
vertices instead of cracking apart, and every vertex is skinned to a
**blend of the ribs it lies between**, so the membrane stretches with each
digit independently. A web must reference real bones, so it cannot live
inside a `group`.

### `attach` — parametric stock parts

```
attach hoof_l bone=hoofb kind=hoof at=0.25 size=0.055 w=0.10 d=0.115 material=hoof
attach eye_l  bone=head  kind=eye  at=0.84 offset=(0.06,0.02,0.028) size=0.022 material=eye
```

Kinds: `hoof` (tapered box, flares down-forward), `box`, `sphere`, `eye`
(low-poly ellipsoid). `w/d/h/taper` override the default proportions.

### `on=` — surface attachment (no offset guesswork)

Any placed part (`sweep`, `attach`, or a `bone=` loft) can declare
`on=<part>`: the compiler snaps its origin to the closest point on that
part's surface and sinks it in by `inset=` (default ≈ a third of the base
radius), so spikes, ears, horns, and bosses are flush **by construction**:

```
sweep pspike bone=clavicle at=1.0 offset=(0.03,0.08,0) dir=up on=pauldron material=gold
loft  ear    bone=head at=0.38 offset=(0.056,0,0) dir=side len=0.014 on=skull inset=0.008
```

The `offset=` becomes a rough aim rather than a precise landing spot. The
base part must appear earlier in the file.

### `group` — rigid props in their own frame

Compound props (weapons, lanterns, banners) are authored **once in a clean
local frame** — pick the natural axes, e.g. haft along +Y, blade along −X —
and mounted with a single position + orientation. Never hand-derive world
angles for a multi-part assembly:

```
group axe bone=hand.r at=1.0 pitch=14 yaw=-18
  loft haft at=(0,-0.24,0) dir=up len=0.40 material=leather
    ...
  loft blade at=(0,0.09,0) dir=in len=0.16 material=iron shape=box
    ...
  sweep bspike at=(0,0.09,0) dir=back material=iron
    ...
end
```

Inside a group, `at=(x,y,z)` is a group-local position and `dir` words mean
group-local axes. Everything in the group is rigidly skinned to the host
bone. "Rotate the axe 45°" is then a one-number edit on the `group` line.
`on=` inside a group may only reference parts of the same group.

Mounting is easiest with **aiming**: `dir=<word> pitch/yaw/tilt` points the
group's local +Y (the prop's spine) along a direction, exactly like aiming a
bone, and `spin=<deg>` rotates the prop about that spine. "Haft rising 45°
from the fist, blade down" is `dir=up pitch=45` with the blade authored
along local +Z. Prefer aiming over raw group Euler angles.

## `textures` — painted-look procedural texturing

Materials stop being flat fills by adding a `textures` section: named
operator stacks baked into a texel atlas (deterministic seeds, so renders
are reproducible and tunable by parameter edits). A material name resolves
to its palette color (or the texture's `base=`), then ops adjust it:

```
textures
  texture fur base=#6b4a33
    gradient axis=v from=-0.10 to=0.16   # painted top-light (v = world-vertical per part)
    noise scale=0.14 amount=0.10         # hand-painted mottle
    streaks along=v amount=0.08          # fur/wood grain along the part
    ao amount=0.16                       # crevice darkening
  texture horn base=#cbbfa4
    band axis=along at=0.25 width=0.12 color=#a89878   # growth ring along the tube
```

Ops: `gradient` (`axis=v` world-vertical | `along` the loft | `u` around
it), `noise` (3D value noise, scale in height units), `streaks`
(anisotropic grain), `spots` (two-tone blotches — foliage), `band` (soft
stripe), `planks` (`dir=u|v count seam width` — plank/shingle seams with
per-plank value jitter), `bricks` (`courses ratio seam width` — running-bond
courses), `ao` (proximity-based crevice darkening).

Textured models bake to a **texel atlas**: every part gets an auto-unwrapped
chart (u around the loft, v along it — captured at mesh generation, never
authored), triangles rasterize into the atlas with seamless 3D-noise
interpolation, and the glTF ships `TEXCOORD_0` + the embedded PNG. `planks`
and `bricks` only read at texel resolution; pattern coordinates span the
whole chart, so scale `count`/`courses` to the band the material occupies.
The compiler still splits shared vertices at material boundaries so color
edges stay crisp, and `out/<name>_tex.png` lets you inspect the atlas.

## `animations`

Rotations are **deltas from rest**, applied about the bone's head: `pitch`
about +X, `yaw` about +Y, `tilt` about +Z (flares a hanging arm sideways —
the roar/flex idiom), `roll` about the bone's own axis. `yaw`, `roll`, and
`tilt` flip sign under mirroring; `pitch` keeps it. Two authoring styles
that can mix inside one `anim`:

```
animations
  pose breathe
    spine2 pitch=2
    clavicle.* roll=2         # glob patterns hit .l and .r

  anim idle loop dur=3.0      # keyframed named poses
    key 0%  pose=rest
    key 50% pose=breathe
    key 100% pose=rest

  anim walk loop dur=1.1      # channel keyframes on bones
    ch thigh.l pitch 0%=-21 50%=18 100%=-21
    ch shin.l  pitch 0%=6 40%=14 55%=26 75%=46 100%=6
    ch spine1  roll  0%=3 50%=-3 100%=3
    mirrorphase 50%           # right side = mirrored left, half a cycle later
```

- `pose <name> mirror <other>` defines a pose as the left/right mirror of
  another (yaw/roll flip sign, pitch keeps it) — one stride pose gives both.
- `mirrorphase <pct>` does the same per-frame for `ch` tracks on `.l` bones:
  the gait idiom. Central bones (spine sway, head bob) are left untouched.
- **Gait rule**: time knee/hock flexion into the *swing* phase (while the upper
  leg travels forward) and keep the leg extended through stance — flexing
  during stance makes the gait read as walking backward (the moonwalk bug).
- Anims export to glTF as sampled quaternion tracks; `--anim NAME` renders a
  preview strip via CPU skinning.

## `checks` — the model's own regression suite

Proportion, clearance, rig quality, and animation coverage are exactly the
properties that get verified once, by eye, and never re-verified after the
next edit. "Shoulder to hip should be two to two and a half head lengths"
becomes arithmetic done in someone's head and then quietly invalidated three
edits later. Write the judgement down as a number and every compile re-runs
it:

```
checks
  # proportion
  assert dist(shoulder.l, hip.l) / len(head) in 2.0..2.5
  assert width / height == 0.45 +- 0.05
  # placement
  assert bottom(hoof.l) < 0.01                    # feet on the ground
  assert zmax(muzzle) > zmax(skull)               # snout clears the face
  # shape
  assert angle(wingdigit1.l, wingdigit4.l) < 46   # keep the fan spannable
  assert angle(thigh.l, knee.l, ankle.l) in 150..178   # knee not hyperextended
  assert volume(head) < volume(torso) / 3
  # clearance
  assert gap(sword, thigh.r, walk) > 0.015        # the blade never grazes the leg
  # rigging
  assert influences(wing.l) >= 2                  # membrane is not welded to one bone
  assert weight(kilt, thigh.l) > 0.3
  # animation
  assert slide(hoof.l, walk) < 0.02               # no moonwalking
  assert swing(clavicle.l, walk) in 8..25
  assert lowest(walk) > -0.02                     # nothing sinks through the floor
  # budget
  assert tris < 6000
  measure snout dist(skull, jaw.tail)             # report it, don't police it
```

### Forms

- `assert <expr> in <lo>..<hi>`
- `assert <expr> <op> <expr>` with `< > <= >= ==`
- `assert <expr> == <expr> +- <tol>` (spaces around `+-` are required, or it
  reads as `a + (-b)`)
- `measure <label> <expr>` — no assertion, just report the number every compile

**Both sides are full expressions.** A bound is as often another measurement
as it is a literal: `assert zmax(muzzle) > zmax(skull)`,
`assert volume(head) < volume(torso) / 3`.

### Names

- **Points**: a bone name is its head; `bone.tail` and `bone.mid` are the
  other two; a part name is its bounding-box center. An unsuffixed mirrored
  name resolves to the `.l` side.
- **Bones** are named for `len`, `angle`, `elevation`, `heading`, `travel`,
  `swing`, `weight`.
- **Parts** are named for everything about geometry and rigging.
- **Animations** are named by the name on their `anim` line.

### Vocabulary

Position and distance

| | |
|---|---|
| `dist(a, b)` | distance between two points |
| `x(p)` `y(p)` `z(p)` | one coordinate of a point |
| `len(bone)` | bone length |

Direction, all in degrees

| | |
|---|---|
| `angle(boneA, boneB)` | between two bone directions, 0..180 — limb splay, wing dihedral, how far a digit fan opens |
| `angle(a, b, c)` | at `b` in the corner `a-b-c`, over any three points — joint flexion |
| `elevation(bone)` | above the ground plane, +90 straight up |
| `heading(bone)` | in the ground plane, 0 faces +Z, + turns toward +X; undefined for a vertical bone |

Extent

| | |
|---|---|
| `width(p)` `height(p)` `depth(p)` `span(p)` | bounding-box dimensions of a part |
| `xmin/xmax/ymin/ymax/zmin/zmax(p)` | one face of its bounding box |
| `bottom(p)` `top(p)` | aliases for `ymin` / `ymax` |

Mass and budget

| | |
|---|---|
| `volume(p)` | enclosed volume — a thickness-free `web` reads ~0 |
| `area(p)` | surface area |
| `tris(p)` `verts(p)` | poly budget, per part |

Clearance and symmetry

| | |
|---|---|
| `gap(a, b)` | closest approach between two parts |
| `gap(a, b, anim)` | ...at its worst over an animation |
| `asymmetry(p)` | worst distance from a part to its own mirror image across X=0 |

Rig quality

| | |
|---|---|
| `influences(p)` | most bones driving any one vertex — **1 means rigidly welded** |
| `bonecount(p)` | distinct bones the part is skinned to |
| `weight(p, bone)` | strongest influence that bone has anywhere in the part |

Animation

| | |
|---|---|
| `moves(p, anim)` | furthest any vertex travels from rest |
| `travel(bone, anim)` | furthest a bone's tail travels from rest |
| `swing(bone, anim)` | widest angular travel of a bone's direction |
| `slide(p, anim)` | horizontal drift while planted — **the moonwalk detector** |
| `lowest(anim)` `highest(anim)` | extreme y reached at any moment |

Globals (bare words, no parentheses): `height`, `width`, `depth`, `ground`,
`top`, `tris`, `verts`, `materials`, `bonecount`, `parts` (mirrored halves
count separately), `anims`, `asymmetry`, `pi`. Helpers: `abs`, `min`, `max`.
Arithmetic is `+ - * / **` and parentheses.

Which side of something a part sits on needs no special function — compare
coordinates: `assert z(muzzle) > z(eye.l)`.

### Cost and caveats

Animation checks pose the mesh at 12 phases, cached per animation, so a full
battery costs a fraction of a second. Vertex sets are subsampled above 600
points for `gap` and `asymmetry`, making both slightly pessimistic rather
than slow.

`gap` is a proximity measure, not a penetration depth: parts that touch and
parts that interpenetrate both read 0. Use it for things that must stay
*apart*.

Failures are reported as lint warnings naming the measured value and the
expectation; passing checks print as info so the numbers stay visible while
you iterate.

## Zones — environments from landforms and rules

Beyond single models, `wam.zone` compiles a `.zone` file — terrain,
surfacing, waterways, and prop placement as named, discrete decisions —
into one textured scene (viewer JSON + vista renders):

```
python3 -m wam.zone myzone.zone
```

```
zone myzone
  size 640 480                 # meters; terrain continues past the rim
  water level=1.2              # everything carved below this becomes water
camera at=(x,z) look=(x,z) height=6

textures
  texture grass base=#6f7c43 ...      # same operator language as models

terrain                        # landform ops, blended by max/min
  base height=4                # the plain the valley floor sits on
  rim height=64 width=115 wobble=30   # enclosing wall; wobble = organic edge
  opening at=(315,40) width=60        # a pass through rim/ridge ops
  ridge from=(330,-330) to=(330,330) height=52 width=64
  plateau at=(-200,-120) radius=70 height=26 terrace=4.5  # stepped mesa
  hill at=(120,140) radius=55 height=8
  basin at=(58,-12) radius=42 depth=8 wobble=10   # dips below water = lake
  level at=(-184,-112) radius=30 height=26 falloff=8      # flat pad
  noise scale=70 amount=2.0

surface                        # splat rules, evaluated per texel, later wins
  grass
  scrub where height>7
  rock where slope>32
  sand where shore<5
  dirt where road

river from=(..) via=(..) to=(..) width=8 depth=4 meander=18 mwave=64
road  from=(..) via=(..) to=(..) width=6.5 meander=9
bridge from=(x1,z1) to=(x2,z2) width=3.8

props
  place tipi at=(x,z) yaw=25 [scale=] [raise=] [float]
  scatter pine count=85 slope<26 scale=0.75..1.3 seed=3
```

The load-bearing rules, each learned the hard way:

- **Rivers beat roads**: rivers carve first; road flattening is masked to
  zero inside a channel, so roads dead-end at banks and bridges mean
  something. `meander` is a chirped oscillation (never a visible sine);
  basins union into lobed lakes rather than circles.
- **`bridge` is generated, not placed**: the compiler samples terrain at
  both anchors, slopes the deck between them, solves the arch to clear
  terrain across the full deck width, rides the ground at the approaches,
  and lints if the span can't work ("terrain rises 0.9m above the deck at
  (x,z)"). Never hand-place a span.
- **Scatter respects the world**: seeded, with slope/water/road masks,
  min-spacing, and exclusion around `place`d props and the camera. `place`
  snaps to terrain height; `raise=` lifts (bridged decks, chimney smoke),
  `float` sits a prop on the waterline.
- **The world doesn't end**: terrain extends ~70m past the rim as an apron
  fading into fog, so no edge is ever visible from inside.
- Renders: a first-person vista from the `camera` directive (with sky
  gradient + distance fog) and a fogless overview; the viewer JSON ships
  the packed scene atlas.

## The compiler fights back (by design)

`wam.cli` runs a semantic lint on every compile: bones with no geometry,
feet dipping below / floating above ground, asymmetry, degenerate triangles,
**loft folds** (a ring wider than the bend it sits on doubles the surface
back — the classic "invisible faces" bug; split the loft at the joint),
parts hosted on the wrong bone, **parts buried entirely inside another part**
(invisible from every angle — the failure `on=` exists to prevent, and one
nothing else can see), central parts that straddle the axis lopsidedly, ring
frames pinned along the path instead of across it, your own `checks`, plus a
proportion report (head-heights, bbox).
Warnings name the fix — the intended workflow is *compile → read warnings →
look at the render sheet → edit angles/ratios → repeat*.

Every generator now collapses cleanly (a `tip` ring emits triangles, not
zero-area quad halves), so **any** degenerate triangle is a real defect and
the lint reports it from the first one — the warning list is meant to be
empty, not skimmed.

Symmetry is checked per part, not per silhouette: a part that straddles the
centerline is claiming to be symmetric, while a one-sided part or a rigid
`group` prop is the author's business. A sword-and-shield loadout no longer
has to be tuned against itself to reach zero warnings.

## Files

- `wam/` — compiler: parser, skeleton solver, mesh gen, texture baker, lint,
  glTF export, software renderer, viewer-JSON export
- `wam/zone.py` — the zone compiler (see **Zones**)
- `scripts/compose_town.py` — composes a set of prop models into one scene
  (renders + merged viewer JSON with a packed mega-atlas)
- `viewer/template.html` — standalone WebGL viewer (open directly, drop any
  `*_viewer.json`); `scripts/build_viewer.py` bakes a page with a model
  preloaded
- `out/` — per model: `.gltf`, `_sheet.png`, `_anim_*.png`, `_tex.png`
  (atlas), `_viewer.json`
