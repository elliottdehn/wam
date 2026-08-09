# `cinematic` — staging, cameras, and shot lint

```bash
python3 -m wam.cinematic film.cine                 # -> out/<name>/<shot>/0000.png …
python3 -m wam.cinematic film.cine --shot gate     # re-render one shot
python3 -m wam.cinematic film.cine --force         # redo shots that are already done
python3 -m wam.cinematic film.cine -o /tmp/take3   # somewhere else
```

A `.cine` is to a camera what a `.wam` is to a mesh. The founding rule is the
same: **the author makes discrete, named, relative decisions — where a camera
starts and ends, what it looks at, how long the shot runs — and the compiler
does every continuous one**: interpolation, easing, ground heights, framing,
occlusion, and saying out loud what it decided.

The lint is the reason this exists. Film work fails in ways that are invisible
until you watch the frames: a camera two metres under the terrain, a subject
four percent of frame height, a look target behind a wall, an animation too
small to read at this framing. Each of those is one measurement, and measuring
2,160 frames after the fact is not a workflow. **The render is for noticing; it
is not for concluding** — here as everywhere else in WAM.

## The shape of a file

```
cinematic embervale_opening
  aspect 2.39                   # letterbox is a compiler concern
  fps 24
  size 1440                     # long edge; the other comes from aspect

scene vista
  zone veyr.zone                                  # terrain and its props
  light elevation=32 azimuth=140 ambient=0.22
  place crown.wam at=(0,365,95) pitch=90 scale=3  # full orientation
  actor pilgrim.wam at=(2,0,-70) yaw=12 anim=walk phase=0.30 shadow
  actor pilgrim.wam at=(6,0,-64) yaw=12 anim=walk phase=0.62 as=second
  ground extend                 # seam-free floor when there is no zone
  fog auto                      # horizon-matched, so the world does not stop

shot push dur=10 scene=vista
  eye  0%=(0,+11,-186)  100%=(0,+9,-160) ease=smooth
  look at=crown
  fov 54
  cut hard
  checks
    assert frames(crown) in 0.25..0.6
    assert visible(crown) > 0.9
    assert clearance > 1.0

shot fracture dur=4 scene=vista
  eye orbit around=crown.break radius=175..145 arc=-10 height=+14..-20
  look at=crown.break
  fov 34
  dissolve 0.5
```

Indentation marks the body of a block; `#` comments to end of line; a trailing
`\` continues a line.

## `cinematic` — the production

| key | default | meaning |
| --- | --- | --- |
| `aspect <n>` | `1.778` | frame ratio. `2.39` is scope, `1.0` square, `0.5625` vertical |
| `fps <n>` | `24` | frames per second |
| `size <n>` | `1440` | the **long** edge in pixels; the short edge follows from `aspect` |

## `scene` — staging

A scene is built **once** and reused by every shot that names it. Per frame
only the actors are re-posed and the camera moves. Rebuilding the merged mesh
and repacking the atlas every frame is pure waste when nothing but the pose
changed.

### `zone <path.zone>`

Stages a compiled zone: its terrain, props, sky, fog and heightfield. The
`.cine` names the `.zone` **source**; the compiler finds the build product
(`<stem>_scene.npz`) next to it or under `out/`. Compile the zone first with
`python3 -m wam.zone myzone.zone`.

The scene dump is versioned (`SCENE_SCHEMA`). A zone built by an older compiler
is an error telling you to rebuild it, not a silently wrong render.

### `place` and `actor`

```
place <model.wam> at=(x,y,z) [yaw=] [pitch=] [roll=] [scale=] [as=<name>] [shadow]
actor <model.wam> at=(x,y,z) [anim=<name>] [phase=<0..1>] [ … same keys … ]
```

`place` is static set dressing. `actor` is the same thing with an animation and
its own phase, so a crowd of one model reads as a crowd instead of a chorus
line.

- **`at=`** is metres. `y` is **ground-relative** — it adds the terrain height
  under that spot. Write `~` on the y (`at=(0,~365,95)`) for an absolute
  height. Ground-relative is the default because an absolute y is almost never
  what someone means in a zone, and guessing it is how a camera ends up inside
  a mountain.
- **`pitch=` / `yaw=` / `roll=`** are full orientation in degrees, so a model
  authored vertical can hang flat. Applied as `Ry · Rx · Rz`.
- **`scale=`** multiplies the model's declared `height`. A `.wam` mesh is in
  fractions of its height; `height` is the only value in a model that is in
  metres, and staging converts.
- **`as=<name>`** names this instance. Without it an instance is known by its
  file stem, and **two copies of one model share one name** — the compiler
  says so and resolves to the first:

  ```
  info: scene 'vista': 2 models answer to 'pilgrim', so it names the first one —
        give the others as=<name> if you meant a different one
  ```

- **`shadow`** projects this model onto the ground along the sun. Not a shadow
  map: the caster's own triangles are flattened and drawn dark, which is exact
  in silhouette, deterministic, one matmul per frame, and degrades into a shape
  you recognise rather than a soft blob. A sun at or below the horizon is an
  error, because that shadow runs to infinity.

### `light`

```
light elevation=32 azimuth=140 [ambient=0.22] [key=0.85] [fillstrength=0.16]
light sun=(x,y,z) [fill=(x,y,z)]
```

`elevation`/`azimuth` in degrees is the readable form; `sun=` is the vector
form. Omit `light` entirely and the renderer's default three-quarter key
applies. This is direct lighting only — no shadow maps, no global illumination.

### `ground extend`

A horizon-correct floor for staged shots with no zone: a flat grid large enough
that its rim sits past the fog, so the floor fades into the sky instead of
ending at a visible cliff. (A polygon fan shades with diagonal seams and stops
at a hard edge; *"the huntsman is standing on the edge of a void, the terrain
just stops"* was a viewer-reported bug, which makes it the compiler's job.)

### `fog auto` / `fog off`

`auto` — the default — matches the fog colour to the sky's horizon and tunes
its range so the ground's rim is gone before you reach it. In a zone, the
zone's own fog is used. `off` disables it, which is mostly useful for proving
to yourself what the fog was hiding.

## `shot` — a camera over time

```
shot <name> dur=<seconds> scene=<scene name>
```

`dur` defaults to `4`, `fov` to `40`. Frame count is `round(dur * fps)`.

### `eye` — keyed camera positions

```
eye 0%=(0,11,-186) 60%=(0,10,-170) 100%=(0,9,-160) ease=smooth
```

Percent keys, same as an animation's `ch` track, with the same easing
vocabulary: `linear` (default), `smooth`, `in`, `out`. Positions are
ground-relative in y unless prefixed with `~`, exactly as in `place`.

### `eye orbit` — a tangential move

```
eye orbit around=<target> radius=175..145 arc=-10 height=+14..-20
```

`a..b` interpolates over the shot; a bare number holds. `arc` is degrees of
travel around the target. This is the shot you would otherwise write with
per-frame trigonometry.

### `look at=<target>`

A target is any of:

| form | example | resolves to |
| --- | --- | --- |
| a staged model | `look at=crown` | its bounding-box centre |
| an `as=` alias | `look at=second` | that instance |
| a **part** | `look at=knight.pauldron` | the part's bbox centre |
| a **bone** | `look at=knight.head` | the bone's head (`.tail`, `.mid` too) |
| a **marker** | `look at=crown.break` | the point the model named |
| a literal point | `look at=(0,60,400)` | itself |

This is deliberately the same vocabulary the model `checks` already use — one
resolver, not two that happen to agree. Declaring `marker break at=(…)` in
`crown.wam` is what replaces scanning the mesh for ember-coloured triangles and
averaging them.

An unknown target is an error that lists what the scene does know.

### `cut` and `dissolve`

`cut hard` (the default) or `dissolve <seconds>`. Shots render in the order
they appear. Nothing is re-encoded and no frames are duplicated to express an
ordering — the compiler writes `assembly.txt`, an ffmpeg concat list, plus real
generated frames for each dissolve:

```bash
ffmpeg -f concat -safe 0 -r 24 -i out/<name>/assembly.txt -pix_fmt yuv420p film.mp4
```

## `checks` — the shot's own assertions

```
  checks
    assert frames(warden) in 0.25..0.6
    assert visible(warden.head) > 0.8
    assert clearance > 0.5
    assert frames(warden) at 100% > frames(warden) at 0%
    measure dolly_len travel
```

Same grammar as a model's checks: `assert <expr> <op> <bound>`, `assert <expr>
in lo..hi`, and `measure <label> <expr>` for report-only numbers. Arithmetic
(`+ - * /`) works, so ratios like `frames(pilgrim) / frames(ribcage) < 0.05`
are assertable.

Every measurement is sampled at 8 phases across the shot. **A bare name means
the worst frame**, because that is the frame that ruins the shot. **`at N%`
means that moment**, interpolated between samples — which is how you say "the
push-in actually pushes in" or "the resting frame is the clean one".

### What you can measure

**Camera safety**

| name | meaning |
| --- | --- |
| `clearance` | closest approach between the eye and any surface, in metres. Negative when the camera is under the ground |
| `clearance(terrain)` | height of the eye above the heightfield (or the staged floor) specifically |
| `lookdist` | distance from eye to look target. A shot where these coincide has no camera basis |
| `updot` | how close the look direction is to world up, 0..1. Near 1 is where roll flips |
| `nearcross` | most triangles crossing the near plane at any sampled frame |

**Framing**

| name | meaning |
| --- | --- |
| `frames(x)` | subject height as a fraction of frame height. The difference between a shot and a screensaver |
| `inframe(x)` | fraction of the subject's screen box inside the frame. Catches a head cropped while the body is comfortable |
| `offscreen(x)` | how far outside the frame the subject's centre has gone; 0 while it is inside |
| `centered(x)` | horizontal placement, −1 at the left edge, +1 at the right |
| `headroom(x)` | space above the subject's top as a fraction of frame height; negative means cropped |

**Visibility**

| name | meaning |
| --- | --- |
| `visible(x)` | how much of what you would see of `x` is not blocked by something else. **1.0 is a clear view** |

`visible` is measured over the subject's *screen footprint*, not its vertices,
and excludes the subject's own surface. Two earlier definitions were wrong and
are worth knowing about: sampling every vertex counts the far side of a solid
object as occluded — which it always is — so a perfectly clear model measured
about 0.7 and no author could pick a threshold; excluding only the subject's
own triangles still counts its neighbours, so a helmet occluded the head it sat
on. What the current number loses is exactly what got covered up.

It works on a whole model, a part, a bone, or a marker. A point buried inside
its own model reads as hidden, which is the useful answer.

**Camera motion**

| name | meaning |
| --- | --- |
| `travel` | total length of the eye's path, in metres |
| `speed` | peak metres per second. "Slow and deliberate", as a number |
| `accel` | peak change in speed. Catches jerks |
| `swing` | total change in look direction over the shot, in degrees |
| `roll` | dutch angle. Always 0 in this renderer, and assertable rather than assumed |

**Animation, as seen on screen**

| name | meaning |
| --- | --- |
| `moves_px(x)` | how far this actor's animation carries it **in pixels at this framing** |
| `cycles(x)` | how many loops of its animation fit inside the shot |

Amplitude floors in metres are meaningless when the subject is 80 metres away.
Pixels are the honest unit.

**Staging**

| name | meaning |
| --- | --- |
| `gap(a, b)` | closest approach between two staged models, in metres |
| `grounded(x)` | how far the lowest point sits above the ground beneath it. 0 is standing on it, negative is buried, positive is floating |
| `facing(x, camera)` | degrees between the way a model faces and the way to the camera. 0 is down the lens, 180 is the back of the head |

**Production**

`frames_written`, `frame_w`, `frame_h`.

## The compiler fights back

None of this has to be asked for. Every warning below is ambient, names the
thing, and gives you the number:

```
WARN: shot 'gate' 0%..43%: camera is under the ground, 4.0m at its deepest (0,-9)
WARN: shot 'banquet' 78%..100%: up to 244 triangle(s) cross the near plane —
      clipped, not dropped, but the camera is passing through something
WARN: shot 'vista' 0%..100%: the north edge of the ground is in frame (23% of the
      rim visible at worst) — the world visibly stops. Pull the camera down or in,
      or bring the fog closer so the rim fades out
WARN: shot 'child' 86%..100%: 'child' leaves frame (131% outside at 100%)
WARN: shot 'gate' 0%: 'warden' is occluded (96% hidden at 0%)
WARN: shot 'crowd': 'pilgrim' barely moves — anim 'walk' travels 0.034m over the
      shot, which is 0.99 px at this framing. Give the shot longer than 0.50s,
      move the camera closer, or hold the pose on purpose with `place` instead
      of `actor`
WARN: shot 'idol': 'effigy' plays anim 'sway' and does not move at all — the
      animation has no channels that reach this model's geometry
WARN: shot 'hold': nothing moves — the camera is locked off and every actor is
      still, so this renders 96 identical frames
WARN: shot 'push': check failed — line 20: frames(crown) > 0.25 = 0.1172,
      expected > 0.25
```

And `info:` lines say what the compiler *decided*, which is where you catch the
thing you did not think to assert:

```
info: shot 'push': camera clears geometry by 12.4m at its closest
info: shot 'push': camera travels 4.04m, peak 3.01 m/s, look swings 1 deg
info: shot 'push': 'crown' occupies 34% of frame height at 0%, 41% at 100%
info: shot 'push': 'pilgrim' moves 21 px over the shot (anim 'walk')
```

A shot is measured against whatever it is *about*: its look target, plus
anything its checks name. Measuring every staged model instead would bury the
subject under background actors that are supposed to drift out of frame.

## Output and the retake loop

```
out/<cine>/<shot>/0000.png …     one directory per shot
out/<cine>/<shot>_contact.png    first / 25 / 50 / 75 / last, so an audit is one image
out/<cine>/_dissolve/…           generated transition frames
out/<cine>/assembly.txt          ffmpeg concat list, in narrative order
```

A shot whose last frame already exists is **skipped**; `--force` redoes it and
`--shot NAME` renders one. Everything is deterministic: same file, same bytes.

Read the contact sheet before the frame directories. That is the whole point of
it — the audit loop is one image per shot rather than spelunking through 240
PNGs.

## Two things worth knowing

**Animations run at their own speed.** An actor advances by wall-clock seconds
over its animation's own `dur`, so a 1.4s walk cycle loops seven times in a ten
second shot. (It used to advance by shot fraction, which played every cycle
exactly once — the only symptom being that everybody moved like they were
underwater.)

**Triangles crossing the near plane are clipped, not dropped.** Geometry that
straddles the camera plane used to vanish for the middle of a push-in and
reappear afterwards. `render_view` now clips properly, interpolating every
per-vertex attribute at the cut. The `nearcross` warning remains, because a
camera passing through a table is usually still a mistake — it is just no
longer a *surreal* one.

## Explicitly not here

No timeline audio, no colour grading, no title cards, no video encoding: those
are post, they compose fine with ffmpeg, and they would drag half a DAW into a
mesh compiler. No path tracing, no shadow maps, no depth of field. The
renderer's flat honesty is the aesthetic; what film work needs is staging,
cameras and lint, not prettier pixels.

## Tests

```bash
python3 tests/test_cinematic.py         # staging, cameras, every lint detector
python3 tests/test_acceptance_cine.py   # the full acceptance file, over a real zone
python3 tests/test_near_plane.py        # clipping
```
