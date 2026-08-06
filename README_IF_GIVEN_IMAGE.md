# If you were given an image

Read this before you write a single bone.

You are about to make one mistake, and it is not a modelling mistake. Shown a
reference, you will compress it into a **gist** — "an armoured knight with a
red cape" — and then model the gist. The gist is not the character. It is the
part of the character that every other knight also has. Everything that makes
this one recognizable is in the details you dropped on the way: the numeral
stamped on the chest badge, the horn chipped on one side only, the three studs
left of the belt buckle, the pauldron that exists on one shoulder and not the
other.

Then you will compare your render to the reference, see a knight next to a
knight, and call it finished.

**The whole-image comparison is what makes this survivable.** At that scale
you are comparing silhouettes and nothing else. A missing badge, a symmetric
pair that should be asymmetric, a wrong count of anything — none of them
change the silhouette, so none of them show up. This is the single most
reliable way to ship a model that is wrong in six places while believing it is
right.

## The rule

**Every glance yields written requirements before the next glance happens.**

This is the part that is easy to get subtly wrong. The obvious process —
look at all nine tiles, then write the list — does not work, because *the
writing is where the compression happens*. Nine tiles held in your head and
then summarized produce the gist again, just with extra steps. You will
faithfully open every crop and still end up with "an armoured knight with a
red cape".

So: open one crop. Write down what it requires. Only then open the next one.
Each crop's output is committed to text before the following crop is looked
at, and no crop's findings are ever merged with another's before being
recorded. The list accumulates; it is never composed.

Tag each requirement with the region it came from, so you can go back to the
crop that produced it when you verify later.

A tile with anything in it that yields **zero** requirements means you
skimmed it. Go back. The only tiles that legitimately produce nothing are
empty background.

Then: model. Then walk the accumulated list item by item against crops, and
give each item a verdict out loud. A feature you cannot find in your render
is missing, not "probably there somewhere".

## 1. Cut the reference up

```bash
python3 scripts/crop.py ref.jpg --grid 3x3 -o out/refcrops
```

A 3x3 is usually right for a full figure; use `--grid 4x4` for something
busy, and re-grid a single region if it is dense (a face, a chest plate, a
weapon head). Re-gridding is cheap and is the main way detail gets found —
if a tile is rich, tile *it*, and run the same one-glance-one-write loop over
its sub-tiles.

You are looking at each tile for things you would not have thought to
mention. That is the entire point — the features you would have volunteered
from memory are the generic ones.

## 2. Write requirements, one crop at a time

For each tile, in order, record every detail in *that tile* that would let
someone pick this character out of a lineup of the same archetype. Be
concrete and be literal. "Ornate armour" is a gist and is worth nothing; "a
raised ridge running down the centre of the breastplate, ending in a point
above the belt" is a requirement.

Do not skip ahead to a region because it looks more interesting, and do not
go back and rewrite an earlier tile's entries to read more tidily. Tidying is
compression wearing a different hat.

These regions each hide a different class of detail, so if your grid does not
happen to isolate them, crop them explicitly and give each its own pass:

- **Head and face** — horns, ears, jaw shape, eye placement, markings, what
  is covering it and what is exposed.
- **Chest and back** — emblems, badges, numerals, straps, buckles, the shape
  of the neckline. Check the back separately; it is a different set.
- **Shoulders and arms** — pauldrons, bracers, gloves. Count them per side.
- **Hands and what is in them** — grip, which hand, how it is angled, which
  way an edge faces.
- **Waist and hips** — belt, buckle placement, hanging items, tassets.
- **Legs and feet** — boot cuffs, knee plates, what the sole looks like.
- **Signature marks** — damage, wear, scars, mismatched pieces, anything
  deliberately broken or asymmetric.

Four kinds of detail get dropped far more often than the rest. Ask about all
four **on every crop**, not once at the end — the whole point is that they go
missing per-region, and a single sweep for them at the end is another
compression pass:

1. **Counts.** Three studs, five spikes, two straps. You will default to
   "some". Write the number down and then model that number.
2. **Asymmetries.** A single pauldron, one chipped horn, a strap over one
   shoulder. Everything in this language mirrors by default, and an
   asymmetric feature will quietly become symmetric unless you name it. This
   is the most common dropped detail of all.
3. **Text, numerals and glyphs.** The '1' on the badge, a rune, a house mark.
   These never survive gist compression, and they are frequently the single
   most identifying thing in the image.
4. **Colour placement, not just palette.** Not "red and gold" — *where* the
   red stops, which panel is gold, whether the trim is on the edge or inset.

Requirements go into the top of the `.wam` file as comments as you write
them, so the list travels with the model and is in front of you on every
later edit. Tag each with the crop it came from — that is what lets you
re-open the right image when you verify, instead of hunting:

Tile names come straight from `--grid`, which writes `r0c0.png` … `r2c2.png`
for a 3x3, so a tag is just the filename:

```
# --- from reference: ref.jpg ------------------------------------------
# [ ] r0c1  left horn chipped near the tip; right horn intact
# [ ] r0c1  no helmet — hair exposed, cropped short
# [ ] r0c1  dark band painted across the eyes, ear to ear
# [ ] r1c0  red cape, gold trim on the outer edge only, none at the collar
# [ ] r1c1  '1' stamped centre of the round chest badge
# [ ] r1c1  badge is round, raised, sits high on the LEFT breast
# [ ] r1c2  pauldron on the LEFT shoulder only; right shoulder bare
# [ ] r2c1  three studs on the belt, left of the buckle
# [ ] r2c1  boot cuffs fold over at the top, taller on the right
# ----------------------------------------------------------------------
```

Note what this list is *not*: it is not one tidy bullet per body region. It
is whatever each crop produced, in the order the crops were opened —
`r0c1` yielded three because the head was dense, and one tile yielding three
entries while its neighbour yields one is the normal, correct shape. A list
that comes out even, one line per region and all the same length, is a list
that was composed rather than accumulated, and it has already lost things.

## 3. Decide how each one is represented

Some features do not survive low-poly geometry at this scale, and that is
fine — what is not fine is dropping them silently. For each item, decide:
geometry, a `material_arc` band, a separate small part, or a texture atlas
op. A numeral on a badge is usually texture; a chipped horn is usually
geometry; a trim line is usually `material_arc`.

If a feature genuinely cannot be represented, **say so to the user
explicitly** and say why. That is a decision they may want to overrule. It is
never something to resolve by quietly leaving it out.

## 4. Verify one item at a time

After the model compiles and you have looked at the sheet, go back to the
list. For each item, cut the matching region out of both images and put them
side by side:

```bash
python3 scripts/crop.py ref.jpg out/mine_sheet.png \
    --box 0.30,0.00,0.70,0.35 --box2 0.05,0.02,0.20,0.30 -o out/cmp_head.png
```

`--box2` aims the render separately, and you will always need it, because the
reference is framed differently from a four-view sheet. Both crops arrive at
the same height in one image.

**Match the view first.** Find which panel — front, three-quarter, side,
three-quarter rear, rear — is closest to the reference's camera and compare
against that one. Comparing a three-quarter reference to a front render
produces confident and completely wrong conclusions about width and depth.
Re-render with `--views threequarter` alone if it helps.

Then give each item a verdict in words before editing anything: "the badge is
there but the numeral is missing", "both horns are intact — the chip was
dropped". Vague dissatisfaction produces flailing; a named difference
produces one edit.

Check the back. Features on the back of a character are dropped at a far
higher rate than anything else, because the default sheet is read
front-first and the rear panels get skimmed.

## 5. Do not declare it done on a glance

The model is finished when every line of the inventory has been ticked
against a crop, or explicitly dropped with a stated reason. Not before.

Twice in this project's history a change was declared correct from a single
cropped view and was wrong in a panel that was never opened. A whole-image
glance is weaker than that, not stronger.

## Related

- `COMMON_MISTAKES_MUST_READ.md` entry 1 is this same failure in its
  quantitative form — judging by eye what a measurement would settle. This
  file is the qualitative half: details are not measured, they are
  *enumerated*, and the discipline is the same. Do not trust the gestalt.
- `SPEC.md` — *Comparing against a reference image* for the crop mechanics,
  and the `checks` vocabulary for the proportions that *can* be asserted.
  Anything you can turn into a check, turn into a check; this file is for
  everything left over.
