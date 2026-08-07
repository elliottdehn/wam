# Common mistakes — read before authoring

Every entry here is a failure that **compiled cleanly and looked plausible**.
None were caught by reasoning; all were found by measuring a model that had
already been declared finished. Read this before building a character, not
after.

Each entry gives the symptom, the real cause (with the numbers that exposed
it), the correct form, and what the compiler now says about it — because
several of these are ambient lints today precisely because they cost hours.

---

## 1. Adding detail before the silhouette is blocked out

**Symptom.** The model accumulates belts, cuffs, studs, trim and spikes, and
still reads as a generic member of its archetype. Each addition feels like
progress. None of it helps, and the reason is that the problem was never
missing detail.

**A WAM character reads through silhouette and colour blocking and almost
nothing else.** At the size a player first sees it, small parts do not exist.
Detail cannot rescue a shape that does not read, because detail is not in the
channel the shape is read through.

Measured on this repo's own hero. Its belt, cuffs, hair and eyes are **180
triangles — 18% of the whole model** — and removing them changes the 24px
silhouette by:

| view | change |
|---|---|
| front | 11.5% |
| three-quarter | 6.5% |
| side | 4.5% |

Under 12% of the outline for nearly a fifth of the budget. And the number that
matters is the one that does not appear in that table: **with all of it
present, the hero still reads at 24px as an anonymous humanoid**,
indistinguishable from any other biped. The scorpion in the same repo is
identifiable instantly from its raised tail and claws — and that comes
entirely from its masses, not from anything added on top.

The wing is the same failure in its purest form. Three separate rounds tuned
digit lengths, sweep angles, spar colour and spar thickness — decoration —
while the topology stayed a fan and the silhouette stayed a **Y**. The fix
was structural: the span went from 1.46 body-lengths to 3.60. No amount of
the first kind of work substitutes for the second.

**Correct form.** Block the masses, look, fix the shape, and only then add
anything:

1. Skeleton, then the masses with a few rings per part. No detail at all.
2. `python3 scripts/silhouette.py my.wam` and read the **smallest** thumbnail
   row. Shading flatters a shape that does not read; this is the only view
   that removes it.
3. Say in one sentence what it reads as. If the sentence is "a humanoid" or
   "a quadruped", the shape is the deliverable and nothing else matters yet.
   Change proportions, mass distribution and outline until the sentence names
   *this* creature.
4. Colour blocking — palette and `material_arc`. Nearly free, and it does
   more work at this scale than any small part.
5. Only now, detail, and only where it changes the outline.

The test before any part earns geometry: *would this change the shape at 32
pixels?* If not, it belongs in the palette or the texture, or nowhere.

**No detector for this one.** The compiler cannot tell a generic silhouette
from a distinctive one — `silhouette()` in a `.wamset` compares two models
against *each other*, which catches a roster whose units are
indistinguishable, but says nothing about a single model. Reading the
thumbnail row yourself is the whole check.

---

## 2. Judging by eye what a measurement would settle

Entry 1 is the mistake you make first; this is the one you make everywhere
else. Every entry below was found by measuring something that had already
been looked at and declared fine — including entry 1's own numbers, which
only became convincing once the silhouette was rasterised and differenced
rather than squinted at.

**The render is for noticing. It is not for concluding.** Eyes are good at
generating hypotheses — "that looks short", "that seems detached" — and bad
at every step after. A glance is confident, fast, and frequently wrong about
the direction of the error, not just its size.

From this project's own history, eye-verdict against measurement:

| looked like | actually measured | verdict |
|---|---|---|
| "the cape starts at mid-thigh — too short" | hem at **-0.05**, through the floor; it was too **long** | wrong direction, wrong cause — the real fault was 0.30 width at the shoulders |
| "`wtrail2` is the whole fix for the wing" | deleting it **raises** membrane area 4% | backwards |
| "a dragon wing needs a 25–45° fan spread" | the accepted wing spreads **69°** | asserted as canonical, contradicted by the model itself |
| "the fit check named the real leak (0.011)" | reported **~0.040 at every waist width**, including the correct one | it was measuring the hem the whole time |
| "the skirt is at the knee" | hem **0.26**, knee **0.26** | correct, and settled in one command |
| "can't tell if the shield is on the arm" | surface distance **0.0060** = exactly the declared layer | the eye *could not* see it; the number could |

Note the last row. Occlusion, a three-quarter camera, and low-poly shading
routinely make the thing you need to judge invisible. "I can't tell" is not a
reason to guess — it is a reason to measure.

Its qualitative twin has its own file: details from a reference image are not
measured, they are **enumerated**, and dropping them is just as invisible.
See `README_IF_GIVEN_IMAGE.md` whenever the user supplies a picture.

**The rule: before asserting anything about a model, name the measurement
that settles it.** If you cannot name one, that is the finding — the language
is missing a check, and building it is the work. Half the constructs in SPEC
exist because someone hit exactly that wall.

Two corollaries that cost real time here:

- **Audit every panel.** Twice a change was declared correct from one cropped
  view and was wrong in a panel that was never opened.
- **A measurement that does not move is not a measurement.** Before trusting
  a number, sweep the input across good and bad and confirm the number
  tracks it. A fit check that read 0.040 for a splitting skirt *and* a
  fitting one looked authoritative for hours.

---

## 3. Held props are placed by guessing a transform

**Symptom.** The sword floats beside the fist instead of in it; the blade
reverses into the chest; the blade presents its flat like a paddle instead of
its edge.

**Cause.** `to=hand.r dir=up pitch=35 offset=(0,0,0.02)` is a hand-derived
transform — the one thing the language forbids everywhere else. It is correct
for exactly one arm pose and drifts the moment the wrist moves. Measured: a
tuned `pitch=90` holds a blade square to the wrist at rest and **47.9°** off
square after the wrist is reposed 55°.

Worse, no proximity check can find it. A hammer floating beside the arm and
one held in the fist measure **0.026** and **0.023** from that arm. Only the
prop itself knows which is *held*.

**Correct form: the weapon states how it is carried, and no composition ever
states it again.** This is the whole fix. Placement written in the `.wamset`
has to be re-derived by hand for every character who picks the thing up, and
it gets re-derived wrong — that is why this failure keeps coming back.

```
model sword
  anchor grip   at=(0,0.055,0) dir=up     # the point and axis that meets a hand
  marker pommel at=(0,0.000,0)
  marker tip    at=(0,0.470,0)
  marker edge   at=(0.030,0.280,0)
  hold grip point=tip edge=edge carry=62  # stated once, by the sword
```

```
graft sword to=hand.r:0.55
```

That is the entire graft. It produces exactly the placement that
`align=grip aim=62:fwd face=edge:bone.up` produced by hand — `hold` supplies
all three. Anything the composition says explicitly still wins, for a
two-handed stance or a weapon slung across a back.

`point=` is not optional, because it is what tells the compiler which end is
the business end. Without it nothing can tell a sword from a reversed sword.

- `align=` puts the grip in the fist. Position stops being a guess.
- `aim=<deg>:<hint>` sets the carry angle **relative to the wrist**, so it
  survives a repose. `along` and `across` are just `aim=0` and `aim=90`, and
  neither is enough on its own: a wrist points *down*, so every `across` hint
  yields a horizontal blade and there is no way to angle one downward.
- `face=<marker>:<dir>` solves the roll. Against a hand-tuned `spin=55`, the
  guess leaves the edge **35°** from target and `face=edge:bone.up` leaves it
  **0°**.

Give the host bone a `roll=` so `bone.up` means something — a bone is a frame,
not a line, and "which way the palm faces" is otherwise unrepresentable.

**A weapon aimed into its wielder is now rejected outright.** Once a model
declares `point=`, the business end must not finish nearer the body than the
grip does — and that is a hard error, not a warning, because this is the
failure that survives everything else. A reversed sword sits on the right
bone, grips correctly, and frequently does not even intersect anything.

| graft | verdict |
|---|---|
| `graft sword to=hand.r:0.55` (from `hold`) | accepted |
| `... aim=90:left` — blade across the body | **rejected**, tip 0.027 vs grip 0.039 |
| `... aim=120:left` — further across | **rejected**, tip 0.032 vs grip 0.039 |
| `... aim=170:back` — raised overhead | accepted |
| `... aim=90:left backhand` | accepted |

Raised and shouldered carries pass; only pointing at the carrier fails. If you
genuinely want a reverse-grip dagger or a point-down sword, the `backhand`
flag says so deliberately and the check stands down.

**The compiler also says:**

```
info: graft 'sword' aims down-fwd, with 'edge' toward up-fwd
WARN: graft 'hammer' was aimed by hand, but that model declares an anchor
      'grip' which landed 0.073 from hand.r — use align=grip
```

Read the `aims … with 'edge' toward …` line. "Edge toward **left**" is a
paddle, and it is in the compile output before you render anything.

---

## 4. Wings come out as webbed hands

**Symptom.** Weak 3–4 fingered wings. Spiky, small, splayed. Not big and swept.

**Cause, and it is topological rather than numeric.** `web` is built and
documented as *a fan of ribs from a shared anchor*, and **a fan from one hub
can only produce a sector.** Membrane exists only *between* ribs, so if every
rib leaves the wrist there is nothing behind the arm — and the arm is the
middle of the wing. The silhouette is a **Y**: a stem that splits into prongs.

Tuning never fixes it. Three separate attempts adjusting digit lengths, sweep
angles, spar colour and thickness changed the wing's decoration while the
topology stayed a fan.

**Do not try to fix it with proportions.** Both wings below were measured on
real models; the second is the one that was accepted. Two of the four ratios
barely move, and the fan spread — the number most obviously "wrong" about a
splayed wing — is *within six degrees of identical*:

| | hand-wing (rejected) | wing that was accepted |
|---|---|---|
| longest digit / arm | 0.62 | **0.92** |
| longest / shortest digit | 1.37 | **2.20** |
| digit fan spread | 75° | **69°** |
| span / body length | 1.46 | **3.60** |
| membrane area / body area | — | **0.80** |

An earlier draft of this file asserted a dragon wing needs a 25–45° spread and
a digit/arm ratio of 1.5–2.5. The accepted wing has 69° and 0.92, so both
figures were wrong. They were guesses about a numeric cause for what is
actually a topological defect, and following them would have sent the next
author tuning angles that were already fine.

**Correct form: a wing is an outline, not a fan.** Ribs anchor at *different*
points along the leading edge, which is what `from=` is for. The load-bearing
line is a trailing strut from the **elbow** — it is the only thing putting
membrane behind the arm, and it turns the Y into a diamond.

This is the accepted wing, verbatim:

```
bone whum parent=spine2 at=0.35 side=0.09 up=0.06 dir=side tilt=40 yaw=-24 len=0.30
bone wrad parent=whum dir=side tilt=18 yaw=16 len=0.42

bone wf1 parent=wrad dir=side tilt=4   yaw=10 len=0.66    # spars, graded so
bone wf2 parent=wrad dir=side tilt=-20 yaw=22 len=0.50    # their tips land on
bone wf3 parent=wrad dir=side tilt=-42 yaw=30 len=0.38    # a swept line, not
bone wf4 parent=wrad dir=side tilt=-62 yaw=34 len=0.30    # an arc
bone wtrail1 parent=wrad dir=down pitch=38 len=0.46       # closes the hand
bone wtrail2 parent=whum at=1.0 dir=down pitch=54 len=0.58  # from the ELBOW

web wing anchor=wrad:0.0 material=membr trailing=scallop scallop=0.07 steps=8 usteps=3
  rib bones=whum..wf1 from=spine2:0.55       # leading edge starts at the BODY
  rib bones=wf2..wf2
  rib bones=wf3..wf3
  rib bones=wf4..wf4
  rib bones=wtrail1..wtrail1
  rib bones=wtrail2..wtrail2 from=whum:1.0   # trailing edge behind the arm
  rib bones=thigh..thigh from=spine1:0.30    # and home to the hip
```

The load-bearing lines are the two `from=` ribs. `from=spine2:0.55` starts the
leading edge at the **body** and `from=spine1:0.30` brings the trailing edge
home to the **hip**; together they are what makes the outline a closed shape
rather than a sector swept around the wrist.

Do not over-credit any single strut. Deleting the elbow rib `wtrail2` was
measured, and it *raises* membrane area by 4% while shortening the chord from
0.469 to 0.437 — so it contributes depth behind the arm, but it is not by
itself the difference between a fan and a wing. The span is: the rejected
wing spanned 1.46 body-lengths and the accepted one spans **3.60**.

Also: spars are ribs *inside* the membrane, not fingers on top of it. Pale,
thick spars protruding past the sheet are most of the "fingered" read.

**The compiler now says:**

```
WARN: web 'wing.l': the outline does not close — the first rib begins 0.34
      from where the last one does, 48% of the membrane's own size, so the
      trailing edge ends at the hub instead of coming back toward the body
```

---

## 5. Capes and skirts pass straight through the body

**Symptom.** Cloth intersects the torso or the legs. Renders hide it: the
cloth is behind the body from the front and in front of it from the back.

**Cause.** For a long time nothing caught it. A cape's bones share a joint
with the spine, and the self-intersection sweep forgave every shared-joint
pair unconditionally — the same exemption that correctly forgives a thigh
entering a hip. A cape hung **0.096** through a torso in silence.

The distinction is not kinship, it is **shape**: cloth is a *sheet* (broad and
thin), while body masses are blobs and limbs are rods. Pairs involving a sheet
now keep no joint exemption.

**Correct form. Do not author the hem length or the standoff.** Both are
relations to the body, and written as numbers they are guesses that go stale:

```
skeleton
  # landmark stub — the body this is worn by has these bones, and the hem is
  # measured against them. Mirror them exactly as the body mirrors them.
  mirror
    bone thigh parent=pelvis side=0.062 dir=down tilt=3 pitch=-3 len=0.238
    bone shin  parent=thigh  dir=down tilt=-3 pitch=4 len=0.228
  end
  bone cape1 parent=chest dir=down pitch=-5 to=thigh.l:0.9
  bone cape2 parent=cape1 dir=down pitch=-3 to=shin.l:0.55   # "to mid-calf"

parts
  loft cloth bones=cape1..cape2 material=wool double_sided \
       rest=back push=back layer=0.012      # standoff solved, not guessed
    ring 0.00 w=0.42 d=0.03                 # as wide as the shoulders, or it
    ring 0.45 w=0.47 d=0.03                 # never reads as attached at all
    ring 1.00 w=0.52 d=0.03
    cap start=none end=none
```

`to=<bone>[:t]` (or `to=ground`) solves the bone's length so it reaches a
landmark. `rest=<part>` slides a finished part until it touches another with
`layer` clearance, solved on real surface separation.

**`rest=` is for things that sit on one side of something.** A cape hangs
behind a back; a shield straps to the outside of a forearm. A skirt *wraps*
the hips, and a wrapping garment clears its body by being **wider**, not by
being moved — there is no direction to slide it. Asking anyway is an error:

```
ERROR: part 'skirt': rest='hips', but the two are coaxial — 'skirt'
       surrounds 'hips' rather than sitting on one side of it ... A garment
       that wraps clears its body by being wider, not by being moved
```

Numbers written where a relation existed produced every one of these:

| authored | meant | what happened |
|---|---|---|
| `fwd=-0.135` | rest against the back | floated 0.070 clear of the body |
| `len=0.22` ×2 | reach the calf | hem went through the floor |
| `len=0.26` | mid-thigh tasset | landed exactly on the knee |
| `offset=(0.115,0,0.01)` | strap to the forearm | floated 0.046 off the arm |

For a skirt, the fit question is *not* "how much is covered" — the legs are
supposed to come out of the hem. `covers` cannot tell an opening from a hole.
Use `leak`, which asks how far the body gets outside the garment **within the
span the garment occupies**:

| | `leak` | `covers` | `clip` |
|---|---|---|---|
| cuirass that fits | **0.000** | 0.634 | **0.102** — false alarm |
| body bursting out | **0.017** | 0.000 | **0.000** — misses it |

`clip` false-alarms on any capped garment and then misses the case where the
garment is swallowed entirely. `leak` reads zero when correct in both.

**Sizing a wrapping garment has a band, not a minimum.** Widen it until it
covers, and no further. On the hero:

- waist under 0.25 and the thigh tops (±0.1245) come through — the "hip bones
  sticking out" look.
- hem over ~0.36 and the **corners** of a squarish cross-section reach 0.23
  and hit the arms hanging at ±0.223. A round cross-section reaches further
  down the flare before this bites.

Both ends are real. Fixing the first by widening without checking walks
straight into the second.

**The compiler now says:**

```
WARN: parts intersect — 'cape' passes 0.096 into 'torso' at rest
WARN: in 'hero_skirted' the body breaks out through worn 'skirt': 'hero.leg.l'
      pokes 0.011 outside 'skirt.skirt' within the span it covers
```

Neither needs an assertion. Both are ambient. It reports **every** leak, not
the worst one — a garment usually fails in more than one place, and reporting
only the deepest hides the rest. It also ignores body parts the garment never
encloses: an arm hanging alongside a tasset overlaps its bounding box
completely without ever being under it.

### Fit is measured against bones, not by containment

Worth knowing why, because the wrong instinct here is very strong. "Is the
body inside the garment" sounds like the question, and it is not one a
garment can answer: cloth is not a closed volume, and the mouths are exactly
where the body is *supposed* to come out. Ray-parity containment therefore
reports the legs below a hem as a leak of near-constant size no matter how
the garment is cut — a skirt that splits over the thighs and one that fits
both read 0.040 — and gating that away by "how much of the body is enclosed"
inverts the test, because the worse a garment splits the less of the body it
contains, so the severe cases go silent.

Bones make it well posed with no special cases. A worn thing wraps the limb
or trunk it is worn on, so at each position along a bone both the body and
the cloth have a radius about it, and fitting means the cloth's is larger.
Openings need no handling at all: at a hem the leg is well inside the cloth's
radius and scores nothing, while a thigh broader than the waistband scores
exactly its overhang. It responds to the defect rather than to the topology:

| skirt waist | reported | | body scaled under a closed cuirass | reported |
|---|---|---|---|---|
| 0.16 (splits open) | **0.036** | | ×1.00 | **0.008** |
| 0.21 (thighs out) | **0.017** | | ×1.25 | **0.026** |
| 0.27 (fits) | **clean** | | ×1.50 | **0.043** |
| 0.32 (wide) | **clean** | | | |

Same measure for an open skirt, a closed cuirass, a cape and a pauldron.

---

## 6. Armour authored as one lump, or welded into the body

**Symptom.** A character file where 35% of the parts are an equipment layer —
helmet, visor, chestplate, pauldrons, greaves, boots — fused into the body.
Measured on a real model: **13 of 37 parts**.

**Cause.** It is the obvious thing to write, and it costs you everything
afterwards: the armour cannot be reused on another body, cannot be swapped for
a variant, cannot be inspected or checked on its own, and every "same character
in different armour" duplicates the entire character.

**Correct form.** Anything worn or carried is **its own model**, and a set file
assembles them. Armour is not one lump either: **each worn slot is a separate
model.** Helm, pauldrons, cuirass, gauntlets, greaves, boots are separate
files, because that is how they are worn, swapped and mixed. A single
"plate set" model cannot be half-swapped, so every combination of pieces
becomes a new file — the same duplication as welding it into the body, one
level up.

```
set knight_kit
models
  body      bodies/human_m.wam
  helm      armor/plate_helm.wam
  pauldrons armor/plate_pauldrons.wam
  cuirass   armor/plate_cuirass.wam
  greaves   armor/plate_greaves.wam
  hammer    weapons/warhammer.wam

compose knight
  base body
  graft helm                                    # names match -> fuses
  graft pauldrons
  graft cuirass
  graft greaves
  graft hammer to=hand.r:0.6 align=grip         # no match -> joins as new
```

`graft` retargets by bone name, so one plate set fits every body using the
same names and *adapts* — a longer forearm stretches the vambrace instead of
letting it slide off.

**Use conventional bone names.** `pelvis spine chest neck head`, `clavicle
upperarm forearm hand`, `thigh shin foot`. This is the contract that makes
gear interchangeable, and it is the one decision that cannot be fixed later.

A garment may also carry bones the body does not have — a hemline, a cape
panel. Those hang off the matched bone they are parented to, so **parent them
to a bone the wearer really has.** A hemline parented to the pelvis follows
the pelvis; one parented to nothing in particular follows the graft frame and
lands wherever that points.

Do **not** split integral anatomy — a golem's plating, a beetle's carapace, a
dragon's scales. If it cannot come off, it is the body.

---

## 7. Chain animations fold the limb through itself

**Symptom.** A tail, tentacle or neck curls into a closed loop mid-animation
and passes through the body.

**Cause.** Animation channels are deltas about each bone's own head, so they
**compound down the chain**. Six segments authored at 26/34/40/38/30/22° —
each individually modest — landed the tip **170°** from rest and drove a
scorpion's sting through its own abdomen. Nothing in the source hints at it.

| bone | authored | actual world turn |
|---|---|---|
| t1 | +26° | 26° |
| t3 | +40° | **100°** |
| t5 | +30° | **168°** |

**Correct form.** Budget the *total*. The corrected strike used
14/12/12/10/8/6 — summing to 62° of authored bend — to land the tip about 60°
from rest.

**The compiler now says:**

```
WARN: anim 'strike' turns bone 'bulb' 163° away from rest (its own channels
      only ask for 22°) — rotations accumulate down a chain
```

---

## 8. Typos are silently discarded

`materal_arc=` and `dpth=` used to be dropped without a word: the model
compiled, looked plausible, and ignored what you asked. Every directive now
knows its own vocabulary:

```
WARN: line 9: part does not understand 'materal_arc', did you mean
      'material_arc'? — it was ignored, so whatever you meant by it did
      not happen
```

If a setting seems to do nothing, check the warnings before changing the value.

---

## 9. Idle animations that read as a still frame

**Symptom.** The idle strip looks like six copies of the rest pose. The
character is technically animated and visibly dead.

**Cause, and it is amplitude — not subtlety, not easing, not phase.** The
instinct is that an idle is a *small* motion, so it gets authored at 2–4° and
disappears. This is very hard to self-diagnose, because the numbers feel
generous while you are typing them.

How badly: three idles were built on the same body, deliberately, to compare a
naive one against a better one. **The "better" one was indistinguishable from
the naive one.** It was written specifically to be an improvement — eleven
channels instead of three, hips and head and both arms, staggered keys — and
side by side in a strip nobody could tell them apart.

| idle | peak angles | displacement / height | reads as motion? |
|---|---|---|---|
| naive — spine/chest/neck | 2.5–3.5° | 0.0355 | no |
| "improved" — 11 channels | 2.5–6° | 0.0402 | **no** |
| readable | 9–16° | **0.1138** | yes |

The step that mattered was roughly **3x the amplitude**, taking peaks into the
9–16° range. Everything below that is noise around the rest pose.

**Secondary cause: every channel peaking at the same instant.** The body
swells and returns as one piece — the mechanical read. Stagger the peaks of
hips, chest, head and arms against each other.

But get the order right, because this is the trap: at low amplitude, phase is
*undetectable*. The same clip with its phases collapsed measured **more**
displacement than the staggered version (0.0437 against 0.0402) and looked
identical. Phase only starts paying once the motion is already big enough to
see. Raising it instead of raising amplitude is polishing something invisible.

**A structural limit worth knowing before you try to fix this with a bob.**
There is no root translation channel — `shift` exists on *poses* only, not on
`ch` lines. The skeleton is rooted at the pelvis and the legs hang from it, so
flexing the knees swings the **feet** rather than lowering the body. Measured
on the readable idle above, vertical travel of the silhouette was **0.0009** of
model height, i.e. nothing, despite 14° of knee flex. Every bit of life in an
idle has to come out of rotation, which is precisely why the rotations have to
be larger than they feel.

**The compiler does not catch this.** It warns only when an animation resolves
to *no* rotation at all (under 1° total):

```
WARN: anim 'idle' moves nothing — every channel resolves to no rotation,
      so the clip plays as a still frame
```

Every idle in the table above compiles with zero warnings, including the two
that read as dead. An ambient lint on phase alignment was attempted and
reverted — it referenced mesh state not available at that point in `lint.py`.
Until that exists, render the strip and compare frame 1 against frame 4: if
you cannot tell them apart at a glance, neither can a player.

## The meta-lesson

Every failure above shares a shape: **the compiler produced a valid result
that was not the intended one, and nothing narrated what it had done.** A
relational placement system has to say what it decided, because the whole
point is that the author never writes the numbers.

So: read the `info:` lines, not just the `WARN:` ones. And audit **all** views
of the sheet — twice in this project's history a change was declared correct
from a single cropped panel and was wrong in the panel not looked at.

---

## The clowntown protocol

If you catch yourself making one of the mistakes in this file, say out loud:

> **I am a clown and this is clowntown.**

Then name which one, and fix it.

This is not decoration. Every entry here was written *after* someone — often
the compiler's own author — did the thing, declared it finished, and was
proved wrong by a measurement. The failure mode is never ignorance of the
list; it is doing the thing anyway and moving on quietly, because admitting
it costs a sentence and rationalising it costs nothing.

Saying it out loud is what makes the correction happen. A mistake you have
named is one you go back and fix. A mistake you have merely noticed is one
you leave in the model and mention in the summary as a "known limitation".

Note the entry number. If it is one that already has a detector and the
detector did not fire, that is a second bug and worth more than the first.
