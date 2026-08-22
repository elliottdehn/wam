# Piano sample bank — attribution and licence

**These recordings are not covered by WAM's MIT licence.** They are licensed
separately, under **Creative Commons Attribution 3.0**:

> <http://creativecommons.org/licenses/by/3.0/>

WAM's own code and text stay MIT. Redistributing this directory means carrying
this file and `UPSTREAM-NOTICE.txt` with it.

## Where these came from

The **YDP Grand Piano** soundfont, release 2016-08-04, from the FreePats
project — <https://freepats.zenvoid.org/Piano/acoustic-grand-piano.html>

* The soundfont, and the modifications to the original recordings that made
  it, are by roberto@zenvoid.org for the FreePats project, published under
  CC-BY 3.0.
* It is built from the *Zenph Studios Yamaha Disklavier Pro Piano
  Multisamples for OLPC*: grand piano samples played by a Yamaha Disklavier
  Pro, recorded for OLPC by Dr. Mikhail Krishtal, Director of Music Research
  and Production, and his team at Zenph Studios, and included in the OLPC
  sound sample library.

The upstream notice is reproduced verbatim in `UPSTREAM-NOTICE.txt`.

## What we changed

CC-BY asks that modifications be stated. From the original soundfont, this
bank was produced by `scripts/sf2_extract.py`:

* Individual notes extracted from the soundfont's sample chunk as WAV files.
* One recording kept per pitch — the longest of the available velocity
  layers — and the rest discarded.
* Thinned to one sample every four semitones across the keyboard.
* Trimmed to six seconds, with a 50 ms fade so a cut tail does not end on a
  step.
* Written as 16-bit mono at the original 44.1 kHz sample rate.

No note was retuned, pitch-shifted or filtered in producing the bank; WAM
transposes at playback time instead.

## Rebuilding it

```
curl -LO https://freepats.zenvoid.org/Piano/YDP-GrandPiano/YDP-GrandPiano-SF2-20160804.tar.bz2
tar xjf YDP-GrandPiano-SF2-20160804.tar.bz2
python3 scripts/sf2_extract.py YDP-GrandPiano-SF2-20160804/YDP-GrandPiano-20160804.sf2 \
    samples/piano --every 4 --trim 6
```

`sf2_extract.py` reads any SoundFont, so a different piano — or a different
instrument entirely — is one command away.
