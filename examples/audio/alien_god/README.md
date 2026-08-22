# Alien God

A 45-minute album, written as ten scores in `.wama` and muxed into one
continuous file. Build it:

```
python3 scripts/build_album.py examples/audio/alien_god --title "Alien God"
python3 scripts/check_album.py out/alien_god/alien_god.wav out/alien_god/tracklist.txt
```

The second command is not optional if you have changed anything. An album is
not ten renders in a row, and the two ways a mux goes wrong — a track that
jumps out because it was normalised on its own, and a join that clicks — are
both invisible in every per-track measurement.

## The running order

| # | track | metre | key | tempo |
|---|---|---|---|---|
| 1 | Signal | 5/4 | D minor | 60 |
| 2 | The Approach | 4/4 | E minor | 138 |
| 3 | Dark & Dangerous | 4/4 | E minor | 152 |
| 4 | Machine Liturgy | 7/8 | A harmonic minor | 120 |
| 5 | Altar of an Alien God | 7/8 | C phrygian | 96 |
| 6 | Procession | 4/4 | D phrygian | 88 |
| 7 | Teeth of the Monolith | 4/4 | F phrygian | 72 |
| 8 | Hymn for the Drowned Star | 6/8 | G dorian | 76 |
| 9 | The God Wakes | 5/4 | C phrygian | 144 |
| 10 | Ashfall | 4/4 | A minor | 54 |

## What holds it together

**The modes carry the identity.** Phrygian on four tracks for its flat second;
harmonic minor on *Machine Liturgy* for the augmented second between its
raised seventh and its natural sixth; dorian on *Hymn* for the raised sixth
that keeps it from settling into plain mourning. The compiler derives each key
signature from the mode itself, so those intervals are written as ordinary
letters — the flat second in C phrygian is just `d`.

Against that, one note is always an explicit accidental fighting the key: the
tritone. `ges` in C phrygian, `bes` in E minor. It is the only chromatic note
on most of the record, which is what makes it land.

**Odd metres for the ceremonial tracks.** 7/8 never resolves where the body
expects, and 5/4 is one beat longer than running music should be.

**The record closes its own circle.** It opens in 5/4 with a signal nobody
answers; *The God Wakes* returns to the title track's key and mode at nearly
twice the tempo. Then *Ashfall* is the only track with **no accidentals at
all** and the only one with `loop off` — the alien intervals are simply gone,
and it is allowed to decay into silence and stay there.

## Notes for anyone editing these

* Every track but the last loops, so each renders twice: `<name>.wav` is
  loop-ready and `<name>_once.wav` keeps its ring-out. The mux joins the
  `_once` files. Joining the loop renders would amputate all ten endings.
* Nothing in the rhythm section is humanised. Two staves that must hit as one
  jitter on separate seeds and drift apart, which is the opposite of what this
  music wants.
* Dynamics do the shaping, not the faders. A staff's `level=` is balance; the
  `\p` … `\ff` marks between `play` lines are what make a section quiet.
* The whole thing is reproducible — same sources, same samples, in any
  process. If that ever stops being true, `tests/test_audio.py` will say so.
