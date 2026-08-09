#!/usr/bin/env python3
"""Roll a creature brief, for when the user asks for "something" and nothing else.

    python3 dice.py                # one brief
    python3 dice.py -n 5           # five, to pick from
    python3 dice.py --seed 1312    # reproducible
    python3 dice.py --prompt       # wrapped in the full paste-me prompt

Why three slots and not two. An adjective and a noun gives you "rusty crab",
which is a colour swap on an archetype — and a colour swap is exactly the thing
this toolchain is worst at making memorable, because a WAM character reads
through silhouette first and almost nothing else. The third slot is a *trait*,
and every one of them changes an outline: a spare pair of legs, a shell carried
on the back, a neck that cannot hold its own head up. That is the slot doing the
work; the other two set palette and mood.

The adjectives lean toward material and wear rather than size or hue, for the
same reason. "Brine-crusted" tells you what the surface does to light. "Big"
tells you to scale a number.
"""
import argparse
import random

# Material, wear, and temperament. Deliberately few plain colours: those are a
# palette decision, and the palette is the cheapest part of a model to change.
ADJECTIVES = [
    "sun-bleached", "tar-black", "brine-crusted", "gilded", "moth-eaten",
    "hollow", "bloated", "sinew-wrapped", "lacquered", "frost-rimed",
    "ash-choked", "bramble-knotted", "iron-shod", "glass-veined",
    "chitin-plated", "wax-sealed", "root-bound", "storm-worn", "salt-scoured",
    "barnacled", "soot-stained", "mould-furred", "cracked-open", "over-fed",
    "half-starved", "spavined", "pot-bellied", "raw-boned", "swaybacked",
    "knock-kneed", "broad-shouldered", "long-limbed", "stub-legged",
    "thick-necked", "flat-faced", "snub-nosed", "hook-beaked", "tusked",
    "antlered", "shaggy", "bald", "scabbed", "blistered", "peeling",
    "waterlogged", "sun-cured", "smoke-cured", "pickled", "fossilised",
    "petrified", "clockwork", "steam-scalded", "rust-eaten", "verdigris",
    "brass-bound", "leather-strapped", "rope-lashed", "nail-studded",
    "shard-quilled", "spine-ridged", "plate-armoured", "scale-mailed",
    "feather-cloaked", "wattle-throated", "dewlapped", "humped", "crested",
    "frilled", "hooded", "mantled", "veiled", "masked", "blindfolded",
    "one-eyed", "wall-eyed", "sleepless", "ever-grinning", "lockjawed",
    "silent", "keening", "purring", "shuddering", "twitching", "lurching",
    "loping", "shuffling", "prowling", "wallowing", "burrowing", "roosting",
    "nesting", "brooding", "moulting", "shedding", "swollen", "shrivelled",
    "desiccated", "waterborne", "cave-pale", "sun-mad", "moon-touched",
    "wind-scoured", "tide-worn", "kiln-fired", "forge-blackened",
    "quarry-dusted", "bog-sunk", "silt-caked", "amber-set", "resin-dipped",
    "tallow-slick", "grease-matted", "honey-glazed", "frost-bitten",
    "ember-lit", "cinder-flecked", "starved-lean", "temple-fat",
    "cathedral-grey", "reliquary-gold", "funeral-white", "carnival-bright",
    "banner-draped", "chain-hung", "bell-laden", "lantern-carrying",
    "ash-crowned", "thorn-crowned", "salt-crowned", "half-buried",
    "newly-hatched", "long-dead", "badly-mended", "hastily-assembled",
    "lovingly-repaired", "over-decorated", "unfinished",
]

# Archetypes that read as a shape at thumbnail size. Mixed animal, occupational
# and object, because the collisions are where the interesting ones come from.
NOUNS = [
    "scarab", "heron", "mastiff", "kiln", "reliquary", "angler", "wyrm",
    "sow", "magistrate", "pilgrim", "thresher", "lamprey", "ox", "crane",
    "mantis", "tortoise", "warden", "hound", "mole", "stork", "bison",
    "boar", "vulture", "toad", "eel", "moth", "drake", "golem", "effigy",
    "hermit crab", "trilobite", "centipede", "weevil", "tick", "louse",
    "crow", "magpie", "owl", "pelican", "cormorant", "flamingo", "emu",
    "elk", "yak", "camel", "tapir", "warthog", "hyena", "jackal", "badger",
    "wolverine", "pangolin", "armadillo", "sloth", "baboon", "gibbon",
    "ram", "goat", "donkey", "mule", "carthorse", "bull", "cockerel",
    "peacock", "swan", "goose", "catfish", "sturgeon", "pike", "ray",
    "urchin", "anemone", "jellyfish", "nautilus", "squid", "barnacle",
    "beetle", "stag beetle", "cicada", "locust", "wasp", "hornet",
    "spider", "harvestman", "scorpion", "salamander", "newt", "gecko",
    "iguana", "monitor", "python", "viper", "tortoise-knight", "beadle",
    "bailiff", "tithe-collector", "night-watchman", "lamplighter",
    "gravedigger", "chimney-sweep", "ferryman", "tanner", "cooper",
    "smelter", "miller", "drover", "poacher", "reliquary-bearer",
    "bell-ringer", "flagellant", "penitent", "hermit", "oracle", "augur",
    "inquisitor", "executioner", "herald", "standard-bearer", "drummer",
    "siege-engine", "battering-ram", "printing-press", "loom", "bellows",
    "anvil", "millstone", "cauldron", "reliquary-box", "sarcophagus",
    "confessional", "pulpit", "gargoyle", "weathervane", "scarecrow",
    "marionette", "mannequin", "automaton", "clock-tower", "lighthouse",
    "dredger", "trawler", "cart", "plough", "gibbet", "pillory",
]

# The slot that changes the outline. Every one of these is something you could
# recognise as a black shape at thirty-two pixels.
TRAITS = [
    "that walks on its knuckles",
    "with a hollow ribcage you can see through",
    "with two more legs than it needs",
    "carrying its own shell on its back",
    "with a tail longer than the rest of it",
    "hunched around a lantern it will not put down",
    "with a crown of horns too heavy for its neck",
    "with one enormous claw and one useless one",
    "split down the middle and stitched back together",
    "with wings it has never been able to fold",
    "that drags its hind legs",
    "with a second, smaller head where a shoulder should be",
    "wearing its skeleton on the outside",
    "with a fan of quills down the spine",
    "that has to lower its head to fit through a door",
    "with a jaw that unhinges past its own chest",
    "balanced on three legs because the fourth is gone",
    "with a sail of skin between its spines",
    "grown around the thing it swallowed",
    "with arms that reach the floor",
    "wrapped in chains it seems fond of",
    "with a nest built into its back",
    "that folds almost flat",
    "with a trunk it uses like a hand",
    "with far too many eyes and no visible mouth",
    "carrying a bell that never stops moving",
    "with roots instead of feet",
    "with a ribcage flung open like double doors",
    "coiled and never fully uncoiled",
    "with antlers that branch into more antlers",
    "leaning permanently to one side",
    "with a mane that reaches its knees",
    "that has fused with its own armour",
    "with a proboscis longer than its body",
    "squatting on a plinth it grew itself",
    "with a hood it never lowers",
    "half in and half out of its shell",
    "with legs jointed the wrong way",
    "dragging a cocoon behind it",
    "with a crest that opens like a hand",
    "on stilts of its own bone",
    "with a face only on the back of its head",
    "wearing a mask it cannot remove",
    "with a lantern jaw and no eyes at all",
    "that has to be carried by its own smaller selves",
    "with a spiral shell taller than the body under it",
    "with paddles instead of forelimbs",
    "so long it needs a second set of hips",
    "with a hump that hangs over its shoulders",
    "that stands like it expects to be hit",
    "with pincers where a face should be",
    "with nothing at all where its front legs used to be",
    "with a crown of eyes around a beak",
    "wearing a bell-shaped skirt of chitin",
    "with a tongue that reaches the ground",
    "that has grown a chimney",
    "with a keel like an upturned boat",
    "held together with rope and hope",
    "with a fan of ribs opened like a peacock",
    "carrying a smaller one of itself",
    "with a scythe where the jaw should be",
    "that walks upright but should not",
    "with a maw that runs the length of its belly",
    "with a horn curling back into its own skull",
    "that has too much neck and knows it",
    "with a saddle nobody has ever used",
    "wearing the shell of something it ate",
    "with feathers only on one side",
    "that trails its guts like ribbons",
    "built around a furnace",
    "with a beak wide enough to swallow a dog",
    "with a spine that arches over its own head",
    "that has stopped bothering to stand up",
    "with a tail it uses as a third leg",
    "with a lantern for a head",
    "wearing a collar meant for something bigger",
    "with a crest of bone like a shovel",
    "that has more mouth than sense",
    "with legs like a table and a body like a chest",
]


# Words that open with a vowel letter but a consonant sound, so the naive rule
# gets the article wrong: "a one-eyed heron", not "an one-eyed heron".
_CONSONANT_SOUNDED = ("one-", "eu", "uni")


def article(word):
    if word.lower().startswith(_CONSONANT_SOUNDED):
        return "a"
    return "an" if word[:1].lower() in "aeiou" else "a"


def roll(rng=random):
    """One brief: adjective, archetype, and the trait that carries the shape."""
    adj = rng.choice(ADJECTIVES)
    return "%s %s %s %s" % (
        article(adj),
        adj,
        rng.choice(NOUNS),
        rng.choice(TRAITS),
    )


PROMPT = ("Clone https://github.com/elliottdehn/wam and make me a model "
          "that I can see: %s")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-n", type=int, default=1, help="how many to roll")
    ap.add_argument("--seed", type=int, help="reproducible roll")
    ap.add_argument("--prompt", action="store_true",
                    help="wrap it in the full paste-me prompt")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    for _ in range(max(1, args.n)):
        brief = roll(rng)
        print(PROMPT % brief if args.prompt else brief)


if __name__ == "__main__":
    main()
