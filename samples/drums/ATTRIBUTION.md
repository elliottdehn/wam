# Drum kit sample bank — attribution and licence

**These recordings are not covered by WAM's MIT licence.** They are licensed
separately, under **Creative Commons Attribution 4.0 International**:

> <http://creativecommons.org/licenses/by/4.0/>

The full licence text is in `LICENSE-CC-BY-4.0.txt`. WAM's own code and text
stay MIT. Redistributing this directory means carrying these files with it.

## Where these came from

**MuldjordKit**, FreePats version 2020-10-18 —
<https://freepats.zenvoid.org/Percussion/acoustic-drum-kit.html>

* The original MuldjordKit for DrumGizmo is by **Lars Muldjord**
  (www.muldjord.com), published under CC-BY 4.0. The recordings come from a
  2009 session in which he played drums for the band Sepulchrum.
* This FreePats version was assembled by roberto@zenvoid.org. It is a stereo
  kit, where the original DrumGizmo kit provides sixteen channels that can be
  mixed selectively.

The upstream notice is reproduced verbatim in `UPSTREAM-NOTICE.txt`.

## What we changed

CC-BY asks that modifications be stated. From the FreePats soundfont, this
bank was produced by `scripts/sf2_extract.py --drums`:

* One recording kept per kit piece, chosen from the available velocity layers
  and taking the left channel of each stereo pair — so this bank is mono, and
  a fraction of what the source contains.
* Cymbals trimmed to six seconds with an 80 ms fade, so a cut tail does not
  end on a step. The ride was over twenty seconds.
* Mapped onto WAM's piece names: `kick snare hat openhat tom lowtom crash
  ride`. Several articulations in the source — china, ride bell, the second
  kick and snare, four toms rather than two — are not represented here.
* Written as 16-bit mono at the original 44.1 kHz sample rate. No piece was
  pitched, filtered or level-adjusted.

## Rebuilding it

```
curl -LO https://github.com/freepats/muldjordkit/releases/download/2020-10-18/MuldjordKit-SF2-20201018.7z
tar xf MuldjordKit-SF2-20201018.7z          # bsdtar reads 7z
python3 scripts/sf2_extract.py "MuldjordKit-SF2-20201018/MuldjordKit 20201018.sf2" \
    samples/drums --drums --take 24 --trim 6
```

`--take` chooses which velocity layer to prefer; a lower number is a softer
hit in this kit.
