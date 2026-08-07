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

### `girth` and `reach` — proportional rescaling

Absolute size is one number: `height` is the only value in meters, so
changing it rescales the whole model and leaves every internal proportion —
and therefore every one of the model's own checks — untouched. Changing shape
is the hard case, because "make it chunkier" otherwise means editing every
size-bearing token in the file: 211 of them in a real character. Two knobs
do it instead:

```
model brute
  height 2.4
  girth 1.25        # every cross-section: ring w/d, seg r, attach extents
  reach 1.12        # every length along a path: bone len, seg len, free-ray len
```

On a real model, `girth 1.25` takes it from 2.10 m wide to 2.25 and from 0.98
deep to 1.14 while the height barely moves; `reach 1.12` takes it from 2.17 m
tall to 2.42 and leaves the depth alone. Width over height — the number that
says "heavy" or "lean" — goes 0.967 → 1.007 under girth and 0.967 → 0.912
under reach.

Both scale the **authored numbers**, before the skeleton solves, rather than
the finished mesh. That is the whole reason they are safe: a non-uniform
scale applied to geometry shears limbs as they rotate, so an arm stretched
along y would grow *thicker* instead of longer once it raised to horizontal.
Scaling what the author wrote has no such artifact — the solver simply runs
on different numbers, and the model animates exactly as if you had typed them.

`reach` scales the root's position too, so the skeleton grows about the
ground rather than sinking through it; expect ground contact to drift a
centimetre or two and the ground lint to tell you exactly how much.

Because they are one number, they are tunable against a check. Sweeping
`girth` against a `.wamset` assertion that the guardian must read broader
than it is tall:

| girth | width/height | result |
|---|---|---|
| 1.00 | 0.967 | fail |
| 1.10 | 0.983 | fail |
| 1.20 | 0.999 | fail |
| **1.25** | **1.007** | **pass** |

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

**`bend=` — say which way a joint actually turns.** A knee that yaws, or that
bends the way it is not hinged, is the most recognisable broken rig there is,
and nothing else catches it: the clip is valid, the bone exists, and the pose
merely *looks* wrong.

```
bone shin    parent=thigh    dir=down len=0.228 bend=-pitch   # knee: one way
bone forearm parent=upperarm dir=down len=0.148 bend=+pitch   # elbow: the other
bone neck    parent=chest    dir=up   len=0.040 bend=pitch,yaw
bone thigh   parent=pelvis   dir=down len=0.238 bend=+yaw     # swings out only
```

Each entry is a channel (`pitch`, `yaw`, `roll`, `tilt`), optionally prefixed
with `+` or `-` to permit one direction only; a bare channel permits both, and
a channel left out is forbidden entirely. A bone with no `bend=` is
unconstrained. Any anim channel or pose key that violates it is a **hard
error**, naming the joint and the offending value:

```
ERROR: anim 'walk' bends 'shin.l' by pitch +20, but that joint is declared
       bend=-pitch — it only bends the other way, and a joint bent backwards
       through itself is the classic broken rig
ERROR: anim 'walk' turns 'shin.l' about yaw, but that joint is declared
       bend=-pitch — it does not turn that way at all
```

**Signs mirror with the limb.** Animation mirrors by flipping yaw, roll and
tilt while preserving pitch, so a constraint written once against the authored
name flips for the `.r` copy along with it: `bend=+yaw` on a thigh permits the
same physical outward swing on both sides — `+yaw` on the left, `-yaw` on the
right — and rejects the inward one on both.

**`to=` — reach a landmark instead of guessing a length.** A hem, a fringe, a
trailing strut: these end at a *place* on the body, not at a number. Written
as `len=0.22` that place is correct only for the body it was tuned on, and
nothing notices when it stops being true.

```
bone cape1   parent=chest  dir=down pitch=-5 to=thigh.l:0.9   # to the hip
bone cape2   parent=cape1  dir=down pitch=-3 to=shin.l:0.55   # to mid-calf
bone hemline parent=pelvis dir=down to=thigh.l:0.55           # mid-thigh tasset
bone drape   parent=chest  dir=down to=ground                 # floor-length
```

`to=<bone>[:t]` extends the bone along the direction it was already given
until it is *level with* that point — a projection, not a rendezvous, which
is what "reaches the calf" means for cloth that must also hang a particular
way. `to=ground` solves against the y=0 plane. `len=` and `to=` are mutually
exclusive, and a landmark that lies behind the bone is an error rather than a
negative length.

The landmark must already exist. A worn model therefore carries a **landmark
stub** — the wearer's own bones, under the wearer's own names, mirrored the
way the wearer mirrors them — which costs nothing (bones without geometry are
exempt from the usual lint) and is what makes one cape fit two bodies.

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
  Blending only happens *at* a joint, so the compiler inserts a ring at every
  joint the author left bare — otherwise a ring spanning two bones welds to
  one of them and the tube creases instead of bending. The inserted section is
  interpolated from its neighbours, so the silhouette is unchanged.
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

Segments accumulate bends. **Prefer the world-referenced, intention-named
ones**: `up=` / `down=` tip the tangent toward +Y / −Y, `fwd=` / `back=`
toward +Z / −Z. Each pair is the other's negation, so a bend never has to be
written as a negative number whose sign you guessed.

`curl=+deg` rotates about the sweep's own *transported* side axis. It is the
only bend whose axis is not visible in the source, so reach for it only when
you need a ring or a tight spiral, where the world-referenced bends degenerate
as the tangent swings onto their axis. Two things to know about it: the sign
is stable (`curl` and `up` bend opposite ways for a forward-pointing sweep),
but **bends accumulate**, so four segments of `curl=45` is a half turn that
carries the tip back over its own root whichever sign you used.

Either way you get told what happened. Every sweep that bends more than 15°
reports its realized path:

```
info: sweep 'tentacle.l' leaves down-fwd and ends up pointing up-fwd
      (96° of bend, tip +0.212 in y)
```

so a tentacle meant to droop that arcs over the skull is a line of lint, not
something to find in a render.

A sweep is **rigid by default** — every ring binds to the one bone it is
mounted on. Give it `bones=a..b` and its rings bind along that chain by arc
length instead, so the curve you tuned deforms with the skeleton:

```
sweep tentacle bones=tc1..tc3 material=hide      # origin and aim from the chain
  seg len=0.09 r=0.045 down=12
  seg len=0.09 r=0.035 down=18
  seg len=0.08 r=0.020 down=22 tip
```

`bone=`/`at=`/`dir=` still work alongside `bones=` when the mount point and
the skinning chain differ; with `bones=` alone the sweep starts at the chain's
head and aims along its first bone. Decide this **before** tuning the shape —
converting a rigid sweep into a bone chain afterwards means re-deriving every
segment length as a ring fraction by hand.

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

#### A wing is an outline, not a fan

The ribs above all leave one anchor, and that shape has a hard limit: **a fan
of ribs from a single hub can only produce a sector.** It is right for a
webbed foot or a frill, and it cannot be a wing at any size. Membrane exists
only *between* ribs, so if every rib leaves the wrist there is nothing behind
the arm, and the result reads as a splayed hand on a stick — tuning rib
lengths, sweep angles or spar thickness never fixes it, because the topology
is wrong rather than the numbers.

A wing is bounded by an **outline that closes back to the body**: a leading
edge from the body out to the tip, spars inside it, and a trailing edge that
returns past the elbow to the hip. Ribs therefore anchor at *different*
points along the leading edge, which is what `from=` is for:

```
mirror
  bone whum parent=spine2 at=0.35 side=0.09 up=0.06 dir=side tilt=40 yaw=-24 len=0.30
  bone wrad parent=whum dir=side tilt=18 yaw=16 len=0.42
  bone wf1 parent=wrad dir=side tilt=4   yaw=10 len=0.66     # spars, graded so
  bone wf2 parent=wrad dir=side tilt=-20 yaw=22 len=0.50     # their tips land
  bone wf3 parent=wrad dir=side tilt=-42 yaw=30 len=0.38     # on a swept line,
  bone wf4 parent=wrad dir=side tilt=-62 yaw=34 len=0.30     # not an arc
  bone wtrail1 parent=wrad dir=down pitch=38 len=0.46        # closes the hand
  bone wtrail2 parent=whum at=1.0 dir=down pitch=54 len=0.58 # from the ELBOW

  web wing anchor=wrad:0.0 material=membrane trailing=scallop scallop=0.07
    rib bones=whum..wf1 from=spine2:0.55       # leading edge starts at the body
    rib bones=wf2..wf2
    rib bones=wf3..wf3
    rib bones=wf4..wf4
    rib bones=wtrail1..wtrail1
    rib bones=wtrail2..wtrail2 from=whum:1.0   # trailing edge behind the arm
    rib bones=thigh..thigh from=spine1:0.30    # and home to the hip
end
```

**`wtrail2` is the bone that matters.** Anchored at the elbow rather than the
wrist, it is the only thing putting membrane behind the arm, and the arm is
the middle of the wing. Without it the silhouette is a Y — a stem that splits
into prongs. With it the silhouette is a diamond.

The compiler checks for this: a membrane of four or more ribs whose first and
last ribs *begin* far apart has an outline that never closes, and says so:

```
WARN: web 'wing.l': the outline does not close — the first rib begins 0.34
      from where the last one does, 48% of the membrane's own size, so the
      trailing edge ends at the hub instead of coming back toward the body.
```

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

**Pressing.** `on=` also *presses* the part flat against the surface: the
compiler takes the outward normal where the part landed and aims the part
along it, so a spike on a sloped shoulder stands square on the slope instead
of cutting into it on one side and floating off it on the other. Four spikes
written identically as `dir=up` on a cone each come out perpendicular to the
cone at their own height.

The normal is averaged over the triangles the part's base actually covers, so
a base spanning several facets of a low-poly surface stands on their average
rather than snapping to whichever facet it happened to land on. `attach`
prims press too — their mass hangs below their origin, so it is that axis
which follows the normal and a `box` boss stands proud of the surface.

Under pressing `dir=` selects the axis that gets aimed at the normal, and
`pitch`/`yaw`/`tilt` become deviations **from** it, exactly like aiming a
`group`: `dir=up pitch=30` is thirty degrees off square, not thirty degrees
off vertical. With no angles you get the normal itself, which means `dir=up`
and `dir=side` produce the same result — the surface is deciding.

That last point is the catch, and it is why the compiler reports it:

```
info: on= pressed 12 part(s) more than 20° off their authored dir
      (rib_mid.r 90°, rib_mid.l 90°, ...) — that is the surface deciding the
      angle; add press=off to keep the authored aim
```

A part placed with a large `offset=` can land on a face pointing somewhere
quite different from the direction you aimed it, and pressing will honour the
face. When the authored direction was the point — ribs meant to wrap
sideways, a horn deliberately swept back — turn it off:

```
sweep rib bone=spine at=0.6 offset=(0.03,0,0.15) dir=side on=thorax press=off
```

### `rest=` — sit on a surface instead of guessing a standoff

The gap between a cape and a back, or a shield and a forearm, is not a number
the author can know. Written as `fwd=-0.135` it is a guess that reads as
floating while satisfying every other check in the language.

```
loft cloth bones=cape1..cape2 material=wool double_sided \
     rest=back push=back layer=0.012
```

`rest=<part>` slides the finished part along one direction until its surface
meets the target's with `layer=` clearance (default 0.008), solved by
bisection on real surface separation — not on extremes along an axis, which
overshoots badly for anything sitting beside a tilted limb. `push=` names the
direction; omitted, it is taken away from the host bone's *axis*, since
centre-to-centre on a long limb points up the arm.

**`rest=` is for things that sit on one side of something.** A cape hangs
behind a back; a shield straps to the outside of a forearm. A skirt *wraps*
the hips, and a wrapping garment clears its body by being **wider**, not by
being moved. Asking anyway is an error, because the alternative is silently
walking the garment down the body until it clears the ankles:

```
ERROR: part 'skirt': rest='hips', but the two are coaxial — 'skirt' surrounds
       'hips' rather than sitting on one side of it ... A garment that wraps
       clears its body by being wider, not by being moved
```

A graft takes the same three keys, which is usually the better place for
them: `graft cape rest=torso push=back layer=0.012` rests against the real
body, so the worn model needs no stand-in geometry of its own.

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
| `clip(a, b)` | how deeply two parts pass *through* each other, 0 if they do not |
| `clip(a, b, anim)` | ...at its worst over an animation |
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
| `slide(p, anim)` | forward travel while planted — **the moonwalk detector**, 0 for a correct gait |
| `drift(p, anim)` | total horizontal travel while planted (≈ stride length in place) |
| `lowest(anim)` `highest(anim)` | extreme y reached at any moment |

Set files add `dist(a, b)`, `x/y/z(point)` and `covers(outer, inner)` over
model members, all in meters, where a point is `model.part` or `model.bone`.

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
parts that interpenetrate both read 0 — and on a low-poly mesh it often reads
a comfortable *positive* distance while two surfaces cross, because the
nearest vertices are nowhere near the crossing. Use `gap` for things that must
stay apart and `clip` for things that must not intersect.

`slide` measures *direction*, not distance. There is no root translation
channel, so a looping gait is authored in place and its planted foot travels a
full stride backwards through stance — correctly. What separates a good gait
from a moonwalk is a foot moving *forward* while it is on the ground, which is
what `slide` sums, over the longest unbroken contact run. A correct gait reads
0 and `assert slide(foot.l, walk) < 0.02` is satisfiable; use `drift` if you
ever drive the root with `shift=` pose keys.

`clip` works from surface crossings, not volume containment, so it stays
meaningful for open parts — a `cap none` kilt and a thickness-free `web` have
no inside for a containment test to ask about, and cloth is where clipping
matters most. Depth is perpendicular to the pierced surface; where the two
directions disagree (a long edge crossing a small surface runs far past it
without the parts overlapping deeply) the shallower reading wins.

### `noclip` — sweep for intersections

`clip` measures one pair. `noclip` asserts over many at once:

```
checks
  noclip                            # every pair the language does not mandate
  noclip cape                       # this part against everything else
  noclip cape strict                # ...and stop forgiving shared joints
  noclip sword vs thigh.r,cape      # exactly these pairs, nothing forgiven
  noclip in=walk                    # test the animation, not the rest pose
  noclip in=* depth=0.01 except=hand.r+sword
```

Almost every part in a model overlaps another **on purpose**, so a sweep is
only usable if it knows what to forgive. Two tiers:

- *Always* forgiven: `on=` pairs (sinking a part into a surface is what `on=`
  means), parts in one `group`, the two halves of a mirrored part, and a
  `web` against the tubes wrapping its own rib bones.
- Forgiven unless `strict`: parts on bones that **share a joint** — a thigh
  starting inside the hip mass, a neck inside the chest, digits off one
  wrist, a leg and a tail off one pelvis — and any overlap confined to the
  first third of a part, which is a limb rooted in the mass it grows out of.
  A part crossing another *mid-span* is reported however shallow it is,
  which is the distinction that matters: a plume rooted 0.026 deep in a torso
  is structural, a spar 0.018 through the middle of one is a bug.

That second tier also covers a leg inside a kilt, which is a bug. Cloth and
props hang across joints they are not part of, so give them their own line:
`noclip kilt strict` or, best, name the pair — `noclip kilt vs leg.l,leg.r`.
**An explicitly named pair forgives nothing**, which makes it the form to
reach for when you know what must not intersect.

`depth=` sets the tolerance (default 0.005 of model height); `in=<anim>` or
`in=*` tests posed frames as well as rest; `except=a+b,c+d` drops pairs.

Failures are reported as lint warnings naming the measured value and the
expectation; passing checks print as info so the numbers stay visible while
you iterate.

## `.wamset` — checks *between* models

A model's `checks` section can only see itself, so the properties that exist
only between models have nowhere to live: that an ogre is half again a human,
that every unit in a set shares a poly budget, that a mount is longer than it
is tall. Those surface when the models finally stand in one scene, which is
too late to be cheap. A set file asserts over a whole cast:

```
set bestiary

models
  human   models/human.wam
  ogre    models/ogre.wam
  kodo    models/kodo.wam

checks
  assert height(ogre) / height(human) in 1.4..1.8
  assert length(kodo) > height(kodo)          # a mount is longer than tall
  assert fill(human) > 0.95
  assert tris(kodo) < 8000
  measure tallest max(height(ogre), height(kodo))
  measure headroom height(ogre.helmet)        # reach into a model for a part
```

```
python3 -m wam.modelset bestiary.wamset      # exits nonzero if a check fails
```

**Lengths here are meters, not the height-fractions used inside a model** —
that distinction is the entire point of the file. A model declares a `height`
in meters and writes every length as a fraction of it, but the geometry need
not fill that unit. Of two real models, one declaring 2.35 m spans only 0.92
of a unit and so really stands 2.17 m, while the other fills 0.99 of its
2.75. Compare declared heights and their ratio is 1.17; compare what they
actually measure and it is 1.26. That 8% is invisible from inside either
model and obvious the moment they share a scene.

`fill(m)` is the check for exactly that: the fraction of its declared height
a model's geometry actually spans. Assert it near 1.0 on every model in a
set, or every ratio computed against it inherits the error.

The vocabulary is `height` `width` `depth`/`length` `span` `ground` `top`
(meters), `declared` and `fill`, and the counts `tris` `verts` `materials`
`bonecount` `parts`, plus `abs`/`min`/`max` and the same arithmetic and
`assert`/`measure` forms as a model's own checks — the two levels share one
parser and one evaluator, so an assertion means the same thing in both. A
bare name is a whole model; a dotted one (`ogre.helmet`) is a part inside it.
`noclip` is model-local and is rejected in `checks`; it belongs in `every`.

### `every` — conventions each model must satisfy

`checks` compares models to each other. `every` runs ordinary **model-level**
checks inside each member's own namespace, so the conventions a whole cast
shares live in one file instead of being copy-pasted into the top of every
model:

```
every
  assert ground == 0 +- 0.02        # everyone stands on the floor
  assert height > 0.9               # everyone fills the height it declares
  assert tris < 8000
  assert asymmetry < 0.35
  noclip                            # nothing intersects anything, anywhere
  measure headroom height
```

The full model vocabulary is available, `noclip` included, and failures name
the model they came from:

```
WARN: check failed — [stalker] line 6: tris = 9104.0000, expected < 8000
```

A check naming a part (`assert bottom(foot.l) < 0.01`) fails for any member
that has no such part, which is a feature: it makes "every unit has a
`foot.l` and it touches the ground" enforceable across the set. Scope by
splitting into more than one set file rather than by weakening the check.

Note that a model's own `height` scalar is the fraction of its declared
height the geometry spans — the same number `fill()` reports at set level —
so `every: assert height > 0.9` is the per-model form of the fill check.

### `compose` — graft one model into another

Anything worn or carried should be **its own model**, and a set file
assembles them. That is the same argument as `group`, one level up: `group`
exists because hand-deriving world rotations across a multi-part prop always
failed, and welding armour into a body file fails the same way — it cannot be
reused, swapped, or checked on its own, and every variant duplicates the
whole character.

There is one operation, `graft`, and wearing and holding are outcomes of it
rather than separate primitives:

```
set knight_kit

models
  body    bodies/human_m.wam
  plate   armor/plate_set.wam
  hammer  weapons/warhammer.wam
  cape    armor/cape.wam

compose knight
  base body
  graft plate                                   # every bone name matches: fuses
  graft hammer to=hand.r:0.5                    # no names match: joins as new
  graft cape  to=spine                          # a mixture, which is the point
```

Each worn slot is its own model — helm, pauldrons, cuirass, gauntlets,
greaves, boots. A single "plate set" cannot be half-swapped, so every
combination of pieces becomes another file, which is the duplication problem
again one level up.

#### `hold` — the model states how it is carried

Placement written in the set file has to be re-derived by hand for every
character who picks the thing up, and it gets re-derived wrong: a blade
reversed into a chest, or presenting its flat like a paddle. How a sword is
gripped is a fact about the *sword*, so it belongs in the sword.

```
model sword
  anchor grip   at=(0,0.055,0) dir=up      # the point and axis meeting a hand
  marker tip    at=(0,0.470,0)
  marker edge   at=(0.030,0.280,0)
  hold grip point=tip edge=edge carry=62   # stated once, by the sword
```

`hold <anchor> point=<marker> [edge=<marker>] [carry=<deg>]`. Then the whole
graft is:

```
graft sword to=hand.r:0.55
```

which produces exactly what `align=grip aim=62:fwd face=edge:bone.up` produced
by hand. Anything the composition states explicitly still wins, for a
two-handed stance or a weapon slung across a back.

`point=` is required: it is what tells the compiler which end is the business
end, and without it nothing can distinguish a sword from a reversed one. With
it, **a weapon whose point finishes nearer the body than its grip is rejected
outright** — an error, not a warning, because this is the failure that
survives every other check. A reversed sword sits on the right bone, grips
correctly, and frequently intersects nothing at all. Raised and shouldered
carries pass; only aiming at the carrier fails. A reverse-grip dagger or a
point-down sword declares the `backhand` flag and the check stands down.

#### Stating an aim by hand

When you do place something explicitly, state it *relative to the host bone*,
never as world Euler angles — a hand-tuned `pitch=90` is a fact about one rest
pose and drifts as soon as the wrist moves.

- `align=<anchor>` maps a declared anchor onto the host bone. Position stops
  being a guess.
- `aim=<deg>:<hint>` sets the carry angle off the bone axis, leaning toward
  the hint. `along` and `against` are its 0° and 180°; `across=<hint>` is its
  90°. Those three alone are not enough — a wrist points down, so *every*
  `across` hint yields a horizontal blade and there is no way to angle one
  downward, which is what `aim=` exists for.
- `face=<marker>:<dir>` solves the roll by pointing a named marker somewhere.
- `facing=<side>:<dir>,...` orients declared `side`s at once, best-fit across
  all the wishes. Wishes that cannot be jointly satisfied are an error rather
  than a silent 45° compromise: a rotation preserves the angles between sides,
  so contradictory ones have no solution and the residual is reported.

**Bones that match by name fuse**: the grafted geometry is re-solved onto the
host's version of that bone, carrying its rotation, position and length ratio.
So one plate set fits every body using the same bone names, and *adapts* — a
longer forearm stretches the vambrace instead of letting it slide off.

**Bones with no match join** the hierarchy as new children at the declared
point, keeping their own names, their own children, and their own motion.

Armour is the all-fused case and a weapon the all-joined case, but the
interesting models are mixtures. A cape whose `spine` fuses to the body while
its `cape1..cape3` sway bones join as new ones arrives with its sway
animation intact and driving them — measured on a real pair, the cape swings
0.095 while the torso it hangs from moves 0.000.

| | |
|---|---|
| `align=<anchor>` | **solve** the placement from a declared anchor frame |
| `to=<bone>[:t]` | where a joining root lands; omit for armour that only fuses |
| `to=(x,y,z)` | place in the base's space with no host bone (scene assembly) |
| `dir= pitch= yaw= tilt= spin= offset=` | aim it, exactly like a `group` |
| `scale=` | override the automatic declared-height ratio |
| `fuse=name\|none` | `none` forces everything to join, even where names match |
| `prefix=` | namespace the joined bones (defaults to the alias) |

#### `marker` — name the points you need to reason about

A part can only be addressed by its bounding-box centre, which says nothing
useful about a long blade: the centre of a sword is halfway up the steel, so
"is the sword pointing away from the body" is unaskable. Name the points
instead:

```
model sword
  anchor grip   at=(0,0.06,0) dir=up
  marker tip    at=(0,0.78,0)
  marker edge   at=(0.05,0.42,0)
  marker pommel at=(0,0.00,0)
```

Markers are points in the model's own space. They ride through a graft, so
after composition they are addressable as `knight.sword.tip`, and they work
in a model's own `checks` too.

#### `closer(a, b, c)` — relations, not angles

Placement bugs are relational, and relations are unsayable in degrees but
trivial in terms of which of two things is nearer. `closer(a, b, c)` is how
much nearer `a` is to `b` than to `c`, in meters; positive means nearer `b`.
It is a check, usable in `assert` and `measure` at both levels.

```
assert closer(knight.hand.r, knight.sword.pommel, knight.sword.tip) > 0
```

"The hand is nearer the pommel than the tip" — the sword is held by the hilt.
Anchor the same sword by its blade end instead and the same expression reads
**−1.36** where the correct grip reads **+1.36**: a sign flip, no angles, and
nothing to retune when the arm is reposed.

**Pick references that are not equidistant by symmetry.** Asking whether a
blade points away from the body by comparing distances to the *spine* proves
nothing: the spine sits at z=0, so a blade pointing forward and the same blade
pointing backward put the tip exactly as far away. Compare against something
off the plane you are testing — a marker on the chest, the hand, a shoulder.
A `closer` that returns the identical value for a good and a bad case is
almost always this mistake.

#### `face=` — solve the roll too

`align=` fixes the position and `across=` the aim axis, which leaves exactly
one free number: the roll about that axis. Guessing it is how a blade ends up
edge-on or upside down. Name a marker and where it should point:

```
graft sword to=hand.r:0.6 align=grip across=fwd face=edge:up
```

The compiler spins the prop about its aim until that marker lies as near the
target as it can. Measured against a hand-tuned `spin=55`: the guess leaves
the edge **35°** away from up-across-the-blade, `face=edge:up` leaves it
**0.0°**. With `align`, `across` and `face` together there is no guessed
number left in a placement.

Both degenerate cases are errors rather than silent no-ops — a hint pointing
along the aim axis says nothing about roll, and a marker sitting *on* the aim
axis does not move when you spin it.

#### `anchor` / `align=` — solve the placement, do not guess it

A prop placed with `to=hand.r dir=up pitch=35 offset=(0,0,0.02)` is a
hand-derived transform, which is the one thing the language forbids
everywhere else — and it fails the same way, with the weapon floating beside
the fist that should hold it. Declare instead where the model *meets*
something:

```
model warhammer
  height 2.10
  anchor grip at=(0,0.06,0) dir=up      # the point and axis that meets a hand
```

```
compose knight
  base body
  graft hammer to=hand.r:0.6 align=grip pitch=-155
```

`align=` maps the anchor's frame onto the host bone's frame, so the grip
lands in the hand by construction. Position stops being a guess entirely, and
`pitch`/`yaw`/`spin` become deviations from a correct default — the carry
angle above is one intelligible number relative to the hand, not four
interacting ones relative to the world.

Because the compiler now knows where the grip is, it checks: grafting a model
that declares an anchor *without* using it reports where the anchor actually
landed.

```
WARN: graft 'hammer' was aimed by hand, but that model declares an anchor
      'grip' which landed 0.073 from hand.r — use align=grip to have the
      placement solved instead of guessed
```

That is the warning worth having, because no proximity check finds this bug:
a hammer floating beside the arm and a hammer held in the hand measured 0.026
and 0.023 apart respectively. Only the anchor knows which one is *held*.

#### `along` / `against` / `across=` — aim relative to the bone

"The sword should be perpendicular to the wrist" is a *relationship*, and the
only way to write it with world angles is to guess which of pitch/yaw/tilt
and which sign — producing three numbers that are correct for exactly one
rest pose. Say the relationship instead:

```
graft sword to=hand.r align=grip across=fwd     # perpendicular to the wrist
graft sheath to=spine align=strap along          # running with the bone
group blade bone=hand.r at=0.6 across=fwd spin=15
```

- `along` / `against` run with or counter to the host bone.
- `across=<hint>` is perpendicular to it. Perpendicular alone names a whole
  circle of directions, so the hint (`fwd`, `up`, `side`, … or a vector)
  picks the half-plane; a hint parallel to the bone is an error saying so.
- `spin=`, `pitch=`, `yaw=`, `tilt=` still apply, now as deviations from a
  relationship rather than as the entire aim.

Both `graft` and `group` take them, so it reads the same whether the prop is
a separate model or authored in place.

**Why it is not just shorthand.** A hand-tuned `pitch=90` is a fact about one
arm pose. Reposing the wrist 55° and remeasuring:

| | hand direction | angle between shaft and wrist |
|---|---|---|
| `pitch=90`, as tuned | (0, −1, 0) | 90.0° |
| `pitch=90`, wrist reposed | (−0.82, −0.57, 0) | **47.9°** |
| `across=fwd`, as tuned | (0, −1, 0) | 90.0° |
| `across=fwd`, wrist reposed | (−0.82, −0.57, 0) | **90.0°** |

The guess drifts 42° off square the moment the wrist moves; the relationship
does not. This is the same argument as `on=` pressing (the surface decides the
angle) and `align=` (the anchor decides the position) — name the relationship
and the compiler keeps it true.

#### Worn coverings are checked automatically

A thigh bursting out through a skirt is the worn-side twin of a reversed
sword, and every composition is checked for it:

```
WARN: in 'knight' the body breaks out through worn 'plate': 'body.torso'
      pokes 0.017 outside 'plate.cuirass' within the span it covers —
      widen the garment there, or narrow the body under it
```

Fit is measured **against bones**, and the reason is worth knowing, because
the wrong instinct here is very strong. "Is the body inside the garment"
sounds like the question, and it is not one cloth can answer: a garment is
not a closed volume, and its mouths are exactly where the body is *supposed*
to come out. Ray-parity containment therefore reports the legs below a hem as
a leak of near-constant size however the garment is cut — a skirt that splits
over the thighs and one that fits both read 0.040 — and gating that away by
"how much of the body is enclosed" inverts the test, because the worse a
garment splits the less of the body it contains, so the severe cases go
silent.

Bones make it well posed with no special cases. A worn thing wraps the limb
or trunk it is worn on, so at each position along a bone both the body and
the cloth have a radius about it, and fitting means the cloth's is the larger.
Openings need no handling at all: at a hem the leg is well inside the cloth's
radius and scores nothing, while a thigh broader than the waistband scores
exactly its overhang. It responds to the defect rather than to the topology,
and the same measure serves an open skirt, a closed cuirass, a cape and a
pauldron:

| skirt waist | reported | | body scaled under a closed cuirass | reported |
|---|---|---|---|---|
| 0.16 (splits open) | **0.036** | | x1.00 | **0.008** |
| 0.21 (thighs out) | **0.017** | | x1.25 | **0.026** |
| 0.27 (fits) | **clean** | | x1.50 | **0.043** |
| 0.32 (wide) | **clean** | | | |

Every leak is reported, not the worst one — a garment usually fails in more
than one place, and reporting only the deepest hides the rest. Body parts the
garment never encloses are skipped: an arm hangs alongside a tasset and
overlaps its bounding box completely without ever being under it, and while
that pair was compared it outranked the real leak and hid it.

Use the `overlap` flag on a graft that is meant to be swallowed.

#### Held props are checked automatically

A weapon reversed into its owner's chest is the most common composition
failure and the hardest to catch in a render, because the blade reads as
being *behind* the body rather than inside it. It is also unambiguous, so
every composition is checked for it with no assertion to remember:

```
WARN: in 'knight' the held 'sword' passes 0.147 through the body:
      'sword.blade' into 'body.torso' — a carried prop should never enter its
      carrier, so its aim is reversed or swung across the body
```

The rule needs no heuristics: a model that joined the skeleton **without
fusing any bones** is carried rather than worn, and a carried thing does not
intersect its carrier. The limb gripping it is excluded, since a haft
overlapping the fist around it is the whole point. Measured on one body: a
correctly carried hammer is clear of the body core, the same hammer aimed
into it reads 0.147, and swung across the chest 0.144.

A graft that is *meant* to overlap — a rider straddling a mount, a pack
pressed into a back — says so:

```
graft rider to=spine:0.9 fuse=none scale=0.55 overlap
```

Default checked, because forgetting the flag on a rider costs one warning
while forgetting an assertion on a sword ships the sword through the ribcage.

#### `noclip` at set level

`every: noclip` already sweeps every member, compositions included. In the
`checks` block a sweep must name whose space to search, since the set itself
has none:

```
checks
  noclip knight                 # every unrelated pair inside the composition
  noclip knight hammer.shaft    # just this part against the rest
```

Expect a composed character to report worn gear overlapping the body — that
is what armour does. `covers` is the check for fit; `noclip` is for the things
that should never touch.

`fuse=none` is not an escape hatch, it is the rider case. A rider shares
`spine`, `head` and `hand.l` with its mount, so the default would weld the two
skeletons into one creature; `fuse=none` keeps the rider whole — grafting a
body onto a body gives 16 host bones plus 16 namespaced rider bones, a
complete second rig riding the first, animations and all.

Grafted animations and poses come across with their channels remapped to the
names their bones ended up with, namespaced on collision. Part names are
prefixed by the source alias (`knight.plate.cuirass`), and materials merge by
name — two models defining `steel` differently is a warning, since one would
silently repaint the other.

A composition is a **first-class member of the set**: `height(knight)`,
`silhouette(knight, body)`, `tris(knight)` and `every` all address it, and it
renders and exports with `-o`:

```
python3 -m wam.modelset knight.wamset -o out/
```

**Bone naming is the contract.** Grafting only pays off if bodies agree on
their skeletons, so use conventional names — `pelvis spine neck head`,
`shoulder upperarm forearm hand`, `thigh shin foot` — and equipment written
for one humanoid fits them all.

#### `covers` — does the armour fit?

```
assert covers(knight.plate.cuirass, knight.body.torso) > 0.45
```

`covers(outer, inner)` is the fraction of `inner`'s surface that lies inside
`outer`. Fit needs its own measure because `clip` reads 0 *both* when two
parts are cleanly apart and when one has swallowed the other whole — a cuirass
that fits and a cuirass buried inside a fattened torso both measure zero.
`covers` separates them. On a real pair: a body that fits its plate reads
**0.63** (the rest of the torso emerges at the neck and waist openings), and
the same plate over a `girth 1.35` body reads **0.00** — the armour no longer
contains it anywhere.

### `silhouette` — can you tell two units apart?

```
assert silhouette(guardian, stalker) > 0.25
assert silhouette(guardian, stalker, side) > 0.25
```

`silhouette(a, b [, view])` returns **1 − IoU** of the two models' outlines:
0 is the same shape, larger is easier to distinguish. Each model is projected
orthographically (so viewing distance cannot skew it), scaled so its height is
1, and stood on a common ground line. Absolute size is deliberately removed —
`height()` already checks that — but **aspect ratio is kept**, because a tall
narrow unit and a squat wide one are exactly the pair you want to read apart.
Views are `front`, `threequarter` (the default), `side`, `back`.

This is the check no single model can make about itself: two units can each
be perfectly good and still be indistinguishable at fifty metres. Measured
across real models:

| pair | threequarter | side |
|---|---|---|
| a model against itself | 0.000 | 0.000 |
| a reskin (legs 12% wider) | 0.084 | 0.104 |
| a bulked variant (wider body and stance) | 0.609 | 0.535 |
| a different creature entirely | 0.644 | 0.860 |

**`> 0.25` is the useful threshold**: reskins land near 0.1, genuine variants
above 0.35, so the boundary sits in a wide empty gap rather than being tuned.

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

`wam.cli` runs a semantic lint on every compile. **Nothing below has to be
asked for** — these are ambient, and the point is that a model written in one
pass gets told what is wrong with it without the author having guessed what to
look for:

- **Unknown keys.** `materal_arc=` or `dpth=` used to be dropped in silence,
  so the model compiled, looked plausible, and ignored what you asked. Each
  directive now knows its own vocabulary and names the nearest match: *"part
  does not understand 'materal_arc', did you mean 'material_arc'?"*
- **Parts intersecting**, with the same forgiveness tiers `noclip` uses. Put
  a `noclip` in `checks` and the ambient sweep stands down — you have set your
  own bar.
- **A mirrored limb crossing the centreline**, which is a `yaw`/`side` sign
  inversion: both copies build correctly and then pass through each other in
  front of the body.
- **Chain rotations compounding.** Animation channels are deltas about each
  bone's own head, so they add up down a chain: *"anim 'strike' turns bone
  'bulb' 163° away from rest (its own channels only ask for 22°)"*. Six
  modest-looking bends fold a tail through the body, and nothing in the
  source hints at it.
- **An animation that moves nothing**, when every channel resolves to zero.

On top of those: bones with no geometry,
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

## Reading the silhouette

```bash
python3 scripts/silhouette.py my.wam                 # sheet + thumbnail rows
python3 scripts/silhouette.py my.wam --thumbs 24,32,48,64
python3 scripts/silhouette.py my.wam --anim walk --frames 6
python3 scripts/silhouette.py long.wam --width 620 --height 420
```

Writes `_sil.png` (flat black on white, one panel per view) and
`_sil_thumbs.png` (the same sheet reduced to 24/32/48px tall and blown back
up, smallest row first).

Shading hides shape. A lit render hands you colour, occlusion and a gradient
running down every limb, and the eye accepts all of it as detail — so a limb
with no taper looks tapered and a generic silhouette reads as fine. The panels
are thresholded to a true binary mask rather than merely drawn in one dark
colour, because the renderer still lights a single-material model and that
shading leaks the shape information the test is meant to withhold.

**The thumbnail rows are the point, and the smallest is the one to read.** At
full panel size everything looks resolved. Measured on this repo's own models:
the hero reads at 24px as an anonymous humanoid — nothing distinguishes it
from any other biped — while the scorpion is identifiable immediately from its
raised tail and claws. That difference is invisible in the shaded sheet and
obvious here, and it is the question the design pass asks: *what shape does
this read as, at the size a player first sees it?*

It also shows which views carry the character and which do not. The scorpion
is unmistakable in side and rear, and an ambiguous "Y" from the front.

## Comparing against a reference image

Never judge a model by laying the whole reference next to the whole render
sheet. At that scale you are comparing silhouettes and nothing else: a head
20% too large, a pauldron an inch low, a muzzle with the wrong taper and
horns curving the wrong way all survive a whole-image comparison intact, and
every one of them is obvious in a crop.

```bash
python3 scripts/crop.py ref.jpg --grid 3x3 -o out/refcrops   # survey it
python3 scripts/crop.py ref.jpg out/mine_sheet.png \
    --box 0.30,0.00,0.70,0.35 --box2 0.05,0.02,0.20,0.30 -o out/cmp_head.png
```

Boxes are normalized `x0,y0,x1,y1` in 0..1, so they do not depend on either
image's pixel size. `--box2` aims the render separately — you will always
need it, because a reference is framed differently from a four-view sheet.
Both crops arrive at the same height in one image.

**Match the view before comparing anything**: find which panel is closest to
the reference's camera, and re-render with `--views threequarter` alone if it
helps. Comparing a 3/4 reference against a front render produces confident and
completely wrong conclusions about width and depth. Crops are also the only
way to judge texture work — atlas ops (`noise`, `streaks`, `planks`) are
invisible at sheet scale and obvious at 3x.

## Files

- `wam/` — compiler: parser, skeleton solver, mesh gen, texture baker, lint,
  glTF export, software renderer, viewer-JSON export
- `wam/zone.py` — the zone compiler (see **Zones**)
- `wam/modelset.py` — the `.wamset` compiler (see **`.wamset`**)
- `scripts/compose_town.py` — composes a set of prop models into one scene
  (renders + merged viewer JSON with a packed mega-atlas)
- `scripts/crop.py` — region crops of a reference image and a render, paired
  at matched scale (`--grid` to survey, `--box`/`--box2` to compare)
- `scripts/silhouette.py` — flat binary silhouette sheet plus thumbnail rows,
  for judging shape with shading removed (see **Reading the silhouette**)
- `viewer/template.html` — standalone WebGL viewer (open directly, drop any
  `*_viewer.json`); `scripts/build_viewer.py` bakes a page with a model
  preloaded
- `out/` — per model: `.gltf`, `_sheet.png`, `_anim_*.png`, `_tex.png`
  (atlas), `_viewer.json`
