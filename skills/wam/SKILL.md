---
name: wam
description: Author low-poly WoW-style 3D characters — mesh, skeleton, and animations — in the WAM text language, compiled to glTF plus turntable PNG renders. Use when the user wants to create or edit a stylized/low-poly 3D character, creature, or prop with a rig and animations, mentions WAM or .wam files, or asks for "WoW-style" / game-ready models.
---

# Authoring WAM models

Approach this as the character artist at a studio whose work is recognizable
across a whole roster — every creature reads as itself in silhouette at
thumbnail size, and none of them could be swapped into another studio's game
without being noticed. This client has already seen generic fantasy asset-flip
work and did not want it. Make deliberate choices about proportion, palette
and silhouette that come from *this* creature, and take one real shape risk
you can justify.

The founding rule of the language serves that: **you make discrete, named,
relative decisions** — bone angles, ring widths, palette colors, relations
between parts — and the compiler generates every vertex, weight, normal and
winding. If you are deriving world coordinates or rotation matrices by hand,
you are misusing it; there is a construct that removes the math. Time not
spent on arithmetic is time available for the thing that actually matters,
which is whether the character reads.

**Read `SPEC.md` for the grammar and `COMMON_MISTAKES_MUST_READ.md` before
you author. If the user gave you a reference image, read
`README_IF_GIVEN_IMAGE.md` first** — working from a picture has its own
failure mode, and it is not one you will notice happening. There are no bundled example models. Every construct in SPEC
has a worked snippet, and half of them (`web`, `frame=`, `to=`, `rest=`,
`hold`, `material_arc=`, `pin`, `checks`) have no analogue in other 3D
formats, so memory of other tools actively misleads here.

## Setup

The compiler ships with this skill: the plugin root (the directory holding
`skills/`, available as `$CLAUDE_PLUGIN_ROOT`) has the `wam/` package,
`SPEC.md` and `COMMON_MISTAKES_MUST_READ.md`. Python 3 with numpy.

```bash
cd "$CLAUDE_PLUGIN_ROOT"                       # or the wam repo checkout
python3 -m wam.cli my.wam                      # -> out/my_sheet.png + .gltf
python3 -m wam.cli my.wam --anim walk --frames 6       # one row per --views
python3 -m wam.cli my.wam --anim guard --anim-views side  # the telling angle
python3 -m wam.cli my.wam --bones              # skeleton overlay
python3 -m wam.cli my.wam --width 760 --height 560     # landscape, long models
python3 -m wam.modelset kit.wamset             # compose body + gear
```

From another project: `PYTHONPATH="$CLAUDE_PLUGIN_ROOT" python3 -m wam.cli …`

## Ground it in the creature

**If you were given a reference image, stop and read
`README_IF_GIVEN_IMAGE.md`.** The short version: read the image exhaustively,
build from it selectively. You will first compress it to a gist and lose what
made the character recognizable; corrected, you will then try to model every
specific and bury the silhouette under micro-parts that all fail anyway. A
WAM character reads through silhouette and colour blocking and almost nothing
else, so beyond the body only about five to eight features earn geometry.
Everything else is palette, texture, or dropped out loud.

If the brief does not pin down what this thing *is*, pin it yourself before
modelling: name the creature, what it does, and the single thing a player
should recognize it by — then say your choice. If memory holds anything about
this user's preferences or a roster you have built with them before, use it.

Distinctive shapes come from the creature's own world: what it eats and
therefore what its jaw is for, how it moves and therefore where its mass
sits, what its gear is made of and therefore how it catches light. A carrion
bird's neck and a warhorse's neck are not the same cylinder with different
numbers. Derive ring profiles and palette from that, not from fantasy stock.

## Calibration: what your default output looks like

LLM-authored characters cluster hard, and the cluster appears regardless of
the brief. Recognize these as *defaults rather than choices*:

1. **The default biped** — 4–5 head-heights, neutral A-pose, limbs as
   constant-width tubes, a near-spherical head, and every feature centred and
   mirrored. Nothing about it is wrong; nothing about it is chosen.
2. **The mud palette** — brown leather, grey steel, desaturated everything,
   with one saturated accent on a gem or an eye.
3. **The fan wing** — digits radiating from a single wrist hub. This is a
   topology error, not a proportion one: membrane exists only *between* ribs,
   so a fan from one hub can only make a sector and the silhouette is a **Y**
   instead of a diamond. Three rounds of tuning digit lengths and sweep
   angles will not fix it. Ribs must anchor at different points along the
   body (`from=`), and the span matters far more than the ratios — a rejected
   wing spanned 1.46 body-lengths where an accepted one spanned 3.60.
4. **Equipment welded into the body** — one real model measured 13 of 37
   parts as an equipment layer fused into the character.
5. **Detail instead of shape** — adding buckles, studs and spikes to a
   silhouette that does not read. It never works. A silhouette that reads
   wrong is not fixed by adding parts.
6. **Cylinders everywhere** — uniform ring widths, so no limb has taper or
   mass and the whole figure reads as balloon animals.

Where the brief pins a direction, follow it exactly — the brief's words
always win, including when it asks for one of these. Where it leaves an axis
free, do not spend that freedom on the default.

## Design before you author

Work in two passes. First write a compact plan — four things, no bones yet:

- **Palette**: 4–6 named hex values, and what each is *for*. Not "brown".
- **Proportion**: head-heights, limb-to-trunk ratios, where the mass sits.
  These are the numbers eyes are worst at judging and best at noticing.
- **Silhouette**: what shape this reads as at 32px, in one sentence. If that
  sentence is "a humanoid," go back. Do not guess this — once there is
  geometry, `python3 scripts/silhouette.py my.wam` renders it flat and at
  thumbnail size, and the smallest row settles the question.
- **Signature**: the one feature this creature is remembered by.

Then critique the plan before writing any WAM. For each of the four, ask
whether you would have arrived here for *any* creature in this genre — work
through a neighbouring brief and see if you land in the same place. Revise
what fails, and say what you changed and why. Only then start authoring, and
derive every ring and colour from the plan.

Build in passes after that: skeleton first, then block out the masses with a
few rings per part, compile and **look**, and only then add detail.

## The loop (non-negotiable)

1. Write or edit the `.wam`.
2. **Write the `checks` for what you just added, in the same edit.** Not
   polish — it is how you avoid breaking what you already fixed.
3. Compile and **read every lint line**, including `info:`. Many failures are
   ambient and need no assertion: misspelled keys (silently dropped
   otherwise), intersecting parts, a mirrored limb crossing the centreline,
   chain rotations compounding into a fold, an animation that moves nothing,
   a membrane outline that never closes, a garment the body bursts out of, a
   grafted thing touching nothing. The `info:` lines say what the compiler
   *decided* — "graft 'sword' aims down-fwd, with 'edge' toward up-fwd". Edge
   toward **left** is a paddle, and it is in the output before you render.
4. **View the rendered sheet** — front, three-quarter, side, three-quarter
   rear, rear. Then run `scripts/silhouette.py` and read the smallest
   thumbnail row: shading flatters a shape that does not read, and this is
   the only view that removes it.
5. **Audit every panel, then measure what you found.** Twice in this
   project's history a change was declared correct from a single cropped view
   and was wrong in the panel not looked at. Use the sheet to find suspects —
   something floating, folded, or reading as the wrong object — and then
   settle each one with a number rather than a second look.
6. Fix by editing angles and ratios. Never by adding coordinate math.

Render animation strips for every gait and action you author — reversed gaits
and non-moving cloth are invisible in static views.

Do the planning and iteration in your own passes; show the user work when you
have confidence in it, not every intermediate sheet.

## Measure, do not squint

The single most expensive mistake in this project is judging by eye something
a measurement would settle. **The render is for noticing; it is not for
concluding.** Eyes generate good hypotheses — "that looks short", "that seems
detached" — and are unreliable at every step after, frequently wrong about
the *direction* of an error and not merely its size. A cape that read as "too
short, starting at mid-thigh" measured through the floor, and the actual
fault was its width at the shoulders.

**Before asserting anything about a model, name the measurement that settles
it.** If you cannot name one, that is the finding: the language is missing a
check and building it is the work. Say the number, not the impression — "the
board rests 0.006 from the arm", not "the shield looks attached".

"I can't tell, it's occluded" is not a reason to guess. It is the strongest
possible reason to measure, and low-poly shading at three-quarter view hides
exactly the contacts and clearances you most need to judge.

Then make it durable. A render tells you the model is wrong *now*; a check
tells you the moment it goes wrong, three edits later, in a view you did not
render. **Nothing verified by eye stays verified by eye.** Turn every finding
into an assertion, including findings from a reference crop: a crop says the
head is too big today, `assert height / height(skull) in 6..7` keeps it right
after the next twenty edits. Prefer relational checks (`closer`, `leak`,
`silhouette`) over absolute distances — see SPEC for the vocabulary.

**Sanity-check the check itself.** A measurement that does not move when the
defect does is worthless and reads as authoritative. Sweep the input across
good and bad and confirm the number tracks it before you trust it.

## Restraint

Spend your boldness in one place. Let the signature be the one memorable
thing and keep everything around it disciplined — this matters more in
low-poly than anywhere else, because the triangle budget is real and the
failure mode is additive. Before calling a model done, look at the sheet and
take one thing off.

Build to a quality floor without announcing it: feet on the ground, no
clipping at rest or in motion, symmetry where symmetry is intended, a
silhouette that reads at thumbnail size, and a clean compile.

## Append what you learn

`COMMON_MISTAKES_MUST_READ.md` is the cross-session memory for this project.
When a failure costs you more than one round of iteration, write it there:
the symptom, the measurement that exposed it, the correct form, and what the
compiler now says about it. Prefer measured evidence to intuition — an
earlier version of that file asserted proportion ranges for dragon wings that
the eventually-accepted wing contradicted on two of four numbers.
