# If you were given an image

Read this before you write a single bone.

There are two ways to fail here and they pull in opposite directions.

**You will compress the image into a gist** — "an armoured knight with a red
cape" — and model the gist. The gist is not the character; it is the part
every other knight also has. What made this one recognizable was in the
specifics you dropped: the pauldron on one shoulder only, the chipped horn,
the three studs left of the buckle.

**And then, told to stop dropping specifics, you will try to model all of
them.** This is the worse failure and the more likely one. A WAM character is
one to three thousand triangles. It reads through **silhouette and colour
blocking** and essentially nothing else. Chase a numeral on a badge with
geometry and you get a scatter of unrecognizable micro-parts, a silhouette
buried under noise, and every one of those details failing anyway — which is
strictly worse than not having attempted them.

So: **enumerate exhaustively, build selectively.** Reading the reference
closely is free and text-only. Deciding what earns geometry is a separate,
budgeted act. Never let the first turn into the second by momentum.

## The budget, before anything else

- The **silhouette and proportions** carry the character. Get them right and
  the model reads even with no detail at all.
- Beyond the base body, roughly **five to eight** features earn their own
  geometry. That is the whole allowance. Spend it on things that change the
  outline.
- Everything else is **palette, `material_arc`, or texture** — all of which
  are nearly free and read better at this scale than a tiny mesh does.
- Anything that survives neither is **dropped, out loud, with a reason.**

The test for geometry: *would this change the shape at 32 pixels?* If not, it
is not geometry. Run `python3 scripts/silhouette.py my.wam` and look — that is
what the player sees first, and it is the only channel small parts do not
reach.

## Enumerate: every glance yields written requirements

Cut the reference up and read the pieces:

```bash
python3 scripts/crop.py ref.jpg --grid 3x3 -o out/refcrops
```

**Open one crop. Write down what it shows. Only then open the next.** The
obvious process — look at all nine tiles, then write the list — does not
work, because *the writing is where compression happens*. Nine tiles held in
your head and summarized afterwards produce the gist again with extra steps.
The list accumulates; it is never composed. Do not go back and tidy earlier
entries to read more evenly — tidying is compression wearing a hat.

Tag each entry with the tile it came from, and **give it a tier as you write
it**, so an undifferentiated list never becomes a build list:

- `[G]` geometry — changes the outline
- `[C]` colour — palette, material band, texture
- `[-]` noted and not modelled

Tile names come from `--grid` (`r0c0.png` … `r2c2.png`):

```
# --- from reference: ref.jpg ------------------------------------------
# [G] r0c1  left horn chipped near the tip; right horn intact
# [G] r1c2  pauldron on the LEFT shoulder only; right shoulder bare
# [G] r2c1  boot cuffs fold over at the top, taller on the right
# [C] r0c1  dark band painted across the eyes, ear to ear
# [C] r1c0  red cape, gold trim on the outer edge only, none at the collar
# [C] r1c1  badge is round and sits high on the LEFT breast
# [-] r1c1  '1' stamped centre of the badge — too small to read; texture
# [-] r2c1  three studs left of the buckle — below silhouette scale
# ----------------------------------------------------------------------
```

Note the shape of that list. Three `[G]`, three `[C]`, two dropped — and the
dropped ones are *written down*, so the decision is visible and reversible
rather than forgotten.

Four kinds of detail are worth more than they look, because they are cheap
*and* they survive at silhouette scale. Ask about all four on every crop:

1. **Asymmetries.** A single pauldron, one chipped horn, a strap over one
   shoulder. Everything in this language mirrors by default, so an asymmetric
   feature silently becomes symmetric unless you name it. The highest
   value-per-triangle detail there is, and the most commonly lost.
2. **Counts.** Three spikes, five plates. You will default to "some". But
   count only what is big enough to see — three studs on a belt is not a
   count worth having, it is noise.
3. **Colour placement, not palette.** Not "red and gold" — *where* the red
   stops, which panel is gold, whether trim sits on the edge or inset. Free,
   and it does more work than any small part.
4. **Proportion.** Head size against body, limb length, where the mass sits.
   Free, invisible to you, and the thing that most decides whether it reads
   as the same creature.

Text, numerals and glyphs are the standing exception. They are frequently the
most identifying thing in the image and they are almost never buildable —
record them, put them in the texture if anywhere, and say so.

## Build in that order

1. **Skeleton and masses.** Block out the body with a few rings per part.
2. **Check the silhouette** — `scripts/silhouette.py`, smallest thumbnail
   row. Does it read as this creature, or as a generic member of its
   archetype? Fix that now. Nothing added later will fix it.
3. **Proportions**, against the reference, in crops.
4. **Colour blocking** — palette and material bands.
5. **Only now**, the `[G]` list, in order, stopping when the budget is spent.

If a `[G]` item turns out not to change the silhouette once built, take it
back out. Chanel's rule applies: before calling it done, remove one thing.

## Verify one item at a time

Go back to the list. For each `[G]` and `[C]` item, cut the matching region
out of both images side by side:

```bash
python3 scripts/crop.py ref.jpg out/mine_sheet.png \
    --box 0.30,0.00,0.70,0.35 --box2 0.05,0.02,0.20,0.30 -o out/cmp_head.png
```

`--box2` aims the render separately, and you will always need it — a
reference is framed differently from a four-view sheet. **Match the view
first**: find which panel is closest to the reference's camera. Comparing a
three-quarter reference to a front render produces confident and completely
wrong conclusions about width and depth.

Give each item a verdict in words before editing: "the badge is there but
sits too low", "both horns intact — the chip was dropped". Vague
dissatisfaction produces flailing; a named difference produces one edit.

Check the back. Rear features are dropped at a far higher rate than anything
else, because the sheet is read front-first.

## Done means

Every `[G]` and `[C]` item ticked against a crop or explicitly abandoned, the
silhouette reading at thumbnail size, and the `[-]` list reported to the user
so they can overrule any of it. Not a whole-image glance — at that scale you
are comparing silhouettes and nothing else, which is how a model gets shipped
wrong in six places while looking right.

## Related

- `COMMON_MISTAKES_MUST_READ.md` entry 1 — the same discipline for things
  that can be measured rather than enumerated.
- The skill's calibration list names *detail instead of shape* as a standing
  failure mode. This file is the one most likely to cause it. If you are
  adding a fourth small part before the silhouette reads, you have already
  gone wrong.
