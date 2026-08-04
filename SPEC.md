# WAM — WoW-ish Art Model language

A text language for describing low-poly character models, rigs, and animations
in a form that LLMs can author reliably. The design rule: the author only makes
**discrete, named, relative, symmetric** decisions; the compiler makes every
continuous/spatial one (vertices, skinning weights, normals, winding). It is
structurally impossible to produce a non-manifold mesh or a disconnected rig.

Toolchain: `python3 -m wam.cli models/foo.wam` → glTF 2.0 (skinned + animated)
plus turntable PNG renders for visual iteration.

```
python3 -m wam.cli models/tauren.wam            # compile + render sheet + glTF
python3 -m wam.cli models/tauren.wam --bones    # also render skeleton overlay
python3 -m wam.cli models/tauren.wam --anim walk --frames 6
```

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
  `animations`. A `mirror` … `end` block inside `skeleton`/`parts` authors the
  left side only; the compiler emits `.l` and a reflected `.r`.

## `model`

```
model tauren
  height 2.6          # meters
  style chunky        # chunky=8-sided lofts, smooth=12, fine=16
```

## `palette`

Named flat colors (WoW-style hand-painted look comes from silhouette + palette,
not textures):

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

## `parts`

Three generators. Everything is closed and skinned automatically; ring/segment
counts come from `style`.

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
- `w` spans the side axis, `d` spans forward (for vertical chains) or up (for
  forward chains).
- Skinning: each ring binds to the chain bone under it, blending near joints.
- **Cloth**: `follow=<bone>:<frac>` on a ring makes its vertices partially
  follow a mirrored bone pair, split left/right per vertex — the kilt idiom:
  `ring 1.00 w=0.41 follow=thigh:0.85` makes the hem swing with the legs
  instead of letting them clip through.

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
operator stacks evaluated per-vertex (deterministic seeds, so renders are
reproducible and tunable by parameter edits). A material name resolves to
its palette color (or the texture's `base=`), then ops adjust it:

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
about +X, `yaw` about +Y, `roll` about the bone's own axis. Two authoring
styles that can mix inside one `anim`:

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

## The compiler fights back (by design)

`wam.cli` runs a semantic lint on every compile: bones with no geometry,
feet dipping below / floating above ground, asymmetry, degenerate triangles,
**loft folds** (a ring wider than the bend it sits on doubles the surface
back — the classic "invisible faces" bug; split the loft at the joint),
parts hosted on the wrong bone, plus a proportion report (head-heights, bbox). Errors name the fix — the
intended workflow is *compile → read warnings → look at the render sheet →
edit angles/ratios → repeat*.

## Files

- `wam/` — compiler (parser, skeleton solver, mesh gen, lint, glTF, renderer)
- `models/tauren.wam` — reference model
- `out/` — glTF + PNG sheets per model
