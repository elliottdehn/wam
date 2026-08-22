# WAM Audio (`.wama`) — language spec

WAM's model language exists because an LLM cannot place a vertex but can make
a named, relative, discrete decision. Audio has the same shape and one extra
constraint: **the author cannot listen.** So the compiler owns every sample,
checks what it can, and prints a picture and a page of measurements on every
compile — because those are the only review that happens before a human hears
it.

Music is written as a score. Songs are staves of voices, voices are bars of
notation, and the spelling is close enough to LilyPond that an author already
knows it. That is a deliberate bet about who writes these files: notation is a
standard with centuries of use and an enormous written corpus, so an author
writing it draws on knowledge they already have instead of inventing a private
encoding.

```
python3 -m wam.audio_cli examples/audio/altar_of_an_alien_god.wama --stems
```

writes `out/<name>.wav`, `out/<name>_once.wav` for looping songs,
`out/<name>_sheet.png` and `out/<file>_audio.json`. Exit status is non-zero if
any declared `assert` fails.

---

## 1. File shape

Line-oriented, indentation-scoped, two spaces per level, `#` comments.
Sections at column 0:

```
audio <name>        header: rate, key, tempo, master
tones               redefine or derive tone adjectives      (optional)
instruments         the sound of each thing that plays
song <name>         a piece of music: staves of voices of bars
sound <name>        a one-shot: layers over a named length
```

A file may hold any number of songs and sounds; every one is rendered.

### Header

| field | meaning |
|---|---|
| `rate 44100` | 22050, 32000, 44100 or 48000 |
| `key c phrygian` | default key for songs that do not set their own |
| `tempo 96` | bpm, or `funeral slow walk medium brisk fast frantic` |
| `master -1.5` | peak target in dBFS for every piece in the file |

---

## 2. Instruments

An instrument is an archetype plus adjectives, never a patch of numbers.

```
instruments
  instrument lead source=square tone=grind env=gate level=100% drive=hard
```

| option | values |
|---|---|
| `source=` | `sine tri saw square pulse pluck piano bell metal thump breath bow drone noise pink band sample` |
| `tone=` | `plain bright warm dark soft harsh hollow thin fat`, or any tone the file defines |
| `env=` | `pluck hit stab pad swell sustain gate bloom bow natural` |
| `level=` | fader, as a percentage |
| `octave=` | whole octaves up or down |
| `detune=` | cents |
| `damp=` | plucked-string decay: 0 an open string, 1 palm-muted |
| `glide=` | `none short long` — pitch slides from the previous note |
| `cut=` | a fixed lowpass, as a register or a frequency |
| `space=` | `none room hall cave plate` |
| `echo=` | `none slap tape canyon` |
| `drive=` | `none soft hard fuzz` |
| `pan=` | `left center right hard-left hard-right` or a signed percentage |

`space=` places a sound; it never changes its level. The wet path is matched to
the dry one and the crossfade's loss put back, so a fader means what it says.

### Sampled instruments

`source=sample` plays recordings instead of generating them:

```
instrument piano source=sample bank=samples/piano env=natural
```

`bank=` is a directory of WAVs whose filenames end in the pitch they were
recorded at — `piano_c'.wav`, `piano_es''.wav` — so the bank is readable and
each file says what it is. Each written note is played from the nearest
recording, transposed. `file=` with `root=` names a single sample instead.

Use a bank rather than one file. Transposing a recording more than a few
semitones drags its formants along with it, and a piano sample pushed two
octaves down sounds like a piano the size of a building. Paths resolve
relative to the `.wama` file. `scripts/sf2_extract.py` builds a bank from any
SoundFont — a `.sf2` is RIFF, so no soundfont library is needed to read one.

The trade is that the file stops being self-contained: a `.wama` using samples
is text plus a payload, and everything else in this language is text alone.
That is worth it for one instrument in particular — see the note on pianos in
**Hard-won rules**.

### Envelopes

`natural` is the one to reach for on a source that already decays by itself —
`piano`, `bell`, `metal`. It only opens and closes without a click. Anything
else multiplies a second decay over the source's own and takes its tail off,
which is how a piano ends up sounding plucked.

`pad swell sustain gate bow stab` hold for exactly their written value.
`pluck hit bloom natural` ring past it — a plucked eighth cut dead at the eighth
sounds like a mute, not a note. Every envelope ends at true zero.

---

## 3. Tones are defaults, not a closed set

A tone is six fields: `tilt` (how fast harmonics fall away), `harm` (how many
there are), `noise`, `odd` (odd harmonics only), `thin`, `spread` (detune, in
cents). Override them three ways:

```
tones
  tone bright harm=1.2 noise=0%           # redefine a built-in, file-wide
  tone alien from=hollow tilt=1.5 harm=0.8  # derive a new name from any tone

instruments
  instrument lute source=pluck tone=bright harm=1.4    # inline, this one only
```

A name with no `from=` that matches a built-in starts from that built-in;
otherwise it starts from `plain`.

---

## 4. Songs are staves of voices

```
song altar
  meter 7/8
  bars 40
  feel straight
  space cave

  staff guitar instrument=guitar level=50% pan=25%
    voice chug
      phrase CHUG
        | <c, g,>8 q q q q q q
        | <c, g,>8 q q <d, a,>8 q <c, g,>8 q
      dynamic ff
      play CHUG CHUG CHUG CHUG
```

The nesting is a score's: a **staff** is one instrument and one mix strip, the
**voices** inside it are independent lines played at the same time by that
instrument, and each voice is written in **bars**.

| song field | meaning |
|---|---|
| `key` / `tempo` | override the header |
| `meter 7/8` | any `count/value`; beats are quarter notes throughout, so tempo means the same thing in every meter |
| `bars N` | length. A voice writing more bars than this is an error |
| `feel` | `straight swing shuffle` |
| `space` | a space over the whole mix |
| `loop off` | see **Two renders** below |

| staff option | meaning |
|---|---|
| `instrument=` | which instrument plays it (defaults to the staff's own name) |
| `level=` | `60%`, or `20%->45%` to ramp across the song |
| `curve=` | shape of that ramp: `linear ease fast slow snap drop` |
| `octave= pan= space= echo=` | the rest of the mix strip |
| `humanize=` | `off light loose` — seeded timing and velocity jitter |
| `mute` / `solo` | bare flags; any solo silences every staff not soloed |

A voice may set `level=`, `octave=` and `pan=` of its own. `--stems` writes one
WAV and sheet per staff, at exactly its level in the mix.

**Humanize is not free.** It is what stops a sequenced part sounding like a
list being read out, but two staves that must hit as one — a chugging guitar
and its kick — jitter on *separate* seeds and drift apart. For anything whose
character is being locked to the grid, leave it `off`.

---

## 5. Notation

### Pitch

`c d e f g a b`, with the octave in LilyPond's absolute convention: bare `c` is
the C below middle C, `c'` is middle C, `c''` an octave above, `c,` an octave
below. Accidentals attach to the letter: `is` sharp, `es` flat, `isis`/`eses`
double, or `#`/`b`/`##`/`bb` if you prefer. `!` is an explicit natural and may
be written before or after the octave mark: `f!'` and `f'!` are the same note.

### The key signature applies

`key c phrygian` puts D♭, E♭, A♭ and B♭ in the signature, so a written `d`
*sounds* D♭. That is what a staff means, and it is why transposing a piece is
one line rather than every note.

The signature is derived from the mode by spelling the scale — walk the letters
up from the tonic and take whatever alteration each needs — so every diatonic
mode gets a correct signature, not just major and minor.

An explicit accidental overrides the signature and then **holds for the rest of
that bar in that octave**, as in reading practice. `f!'4 f'4` is two F
naturals.

### Duration

The number after the pitch is the note value: `4` a quarter, `8` an eighth, `2`
a half, `1` a whole; `16 32 64` below that. Dots multiply: `4.` is a dotted
quarter. Values are **sticky** — `c'8 d' e' f'` is four eighths — which is safe
only because bars are checked.

### Bars are checked

`|` starts a bar. Every bar must add up to the meter, and one that does not is
an error naming what it holds and what it needs. This is the single most
valuable thing the notation buys: an author who cannot hear the result would
otherwise not notice a rhythm quietly drifting a beat.

```
this bar is 2., but 7/8 is 3.5 beats -- the bar holds 3 beats and needs 3.5
```

### Everything else in a bar

| token | meaning |
|---|---|
| `r4` | a rest |
| `<c' e' g'>4` | a chord |
| `<Dm>4` `<A7>2` | a chord by lead-sheet name — uppercase is what distinguishes a symbol from a pitch. Qualities: `maj m min 7 maj7 m7 dim dim7 aug sus2 sus4 6 m6 9 add9` and their aliases |
| `q` | repeat the chord before it, as LilyPond spells it. A riff that hammers one shape reads as a rhythm instead of the same chord typed eight times |
| `~` | tie to the next note; may cross a bar line |
| `!` / `?` | accent / soften this note |
| `\p` `\mf` `\ff` | a dynamic, from `ppp` to `fff`. Takes no time, so it never disturbs the bar sum |

### Phrases and play

A voice either writes its bars straight out, or names phrases and plays them:

```
      phrase RIFF
        | e'8 e'8 e'8 bes'8 e'8 e'8 f!'8 e'8
        | e'8 e'8 g'8 bes'8 a'8 g'8 f!'8 e'8
      dynamic ff
      play RIFF RIFF RIFF RIFF
```

Doing both in one voice is an error — the order it would play in is anyone's
guess. `dynamic <name>` between `play` lines applies from that point, which is
what lets one phrase be reused at four volumes instead of copied four times.
That is how a piece gets a shape: faders apply to a whole staff for a whole
song and cannot say "this section is quiet and the next one is not".

### Drums

A staff whose instrument is `kit` reads its tokens as pieces rather than
pitches, with the same durations and the same bar checking:

| token | piece |
|---|---|
| `k` `s` | kick, snare |
| `h` `H` | hat, open hat |
| `t` `T` | tom, low tom |
| `c` `R` | crash, ride |
| `r` | a rest, as everywhere else — which is why the ride is `R` |

---

## 6. Two renders

A looping song has its ring-out wrapped onto its head, so the repeat has no
hole in it. That is right for a loop and wrong for listening: the ending is
gone from the end, which sounds exactly like the piece being cut off. So a
looping song produces both files from one render:

* `<name>.wav` — loop-ready, seamless on repeat.
* `<name>_once.wav` — the same mix, un-wrapped, decaying properly.

`loop off` renders only the second.

---

## 7. Albums

A directory of `.wama` files is an album. `scripts/build_album.py` compiles
every song in filename order and muxes them into one continuous file:

```
python3 scripts/build_album.py examples/audio/alien_god --title "Alien God"
```

It writes the muxed `alien_god.wav` and a `tracklist.txt` with each track's
start time, length and peak. `--join <seconds>` crossfades between tracks;
the default butts them together, which is seamless because every track's own
render already ends at silence.

**It joins the `_once` renders, not the loop-ready ones.** Concatenating loop
renders would cut off every ending and start each track with the previous
one's reverb, because that ring-out has been moved to the head.

`scripts/check_album.py <album.wav> <tracklist.txt>` inspects the result for
the two things that go wrong in a mux and are invisible in any per-track
measurement: a track that jumps out because it was normalised on its own, and
a join that clicks because two renders were butted together at non-zero
samples.

---

## 8. Sounds are layers

```
sound sword_swing
  length short              # tiny short medium long huge, or `0.4 s`
  space room
  layer whoosh
    source band
    sweep center 600 -> air curve=fast
    env swell
    level 90%
  layer edge at=62%
    source metal bright
    pitch high
    cut low 7000
    env hit
    length 55%
```

Every time inside a sound is a **percentage of the sound's length**, so making
it bigger is one edit.

| layer line | meaning |
|---|---|
| `source <archetype> [<tone>] [field=..]` | what it is |
| `pitch <register\|note\|Hz>` | registers: `sub low mid high air sparkle` |
| `env` / `length` / `level` | envelope, span, share of the mix |
| `cut <low\|high\|band> <freq> [q=]` | a fixed filter |
| `sweep <pitch\|center\|level> <from> -> <to> [curve=]` | a moving one |
| `drive` / `space` | per layer |
| `repeat <n> every <%> [decay=]` | a burst of copies |

Curves: `linear ease fast slow snap drop`.

---

## 9. Checks

Any song or sound may assert on its own measurements. Checks are how an author
who cannot listen states what the thing is *supposed* to be.

```
checks
  assert peak <= -1.0 db
  assert duration ~ 87.5 s
  assert loop_pop < 14 db
  assert centroid < 4000 hz
```

Metrics: `duration` `peak` `rms` `crest` `centroid` `dc` `onsets` `clipped`
`air` `whistle`, plus `loop_pop` for looping songs. Operators `< <= > >= ==`
and `~` (within 10%). Units `db hz khz s ms`.

Four of those exist because a specific bug got past everything else:

* **`centroid`** is power-weighted. A magnitude-weighted centroid reads an
  inaudible hiss floor as brightness — a 440 Hz sine with −60 dB noise on it
  measures 1.5 kHz that way and 440 Hz this way.
* **`onsets`** is spectral flux, so it counts a hit landing inside a sustaining
  chord where energy alone would miss it. The window scales with the length of
  the sound: a 30 ms click is nothing but an onset.
* **`air`** is the share of power above 10 kHz, and **`whistle`** is how far
  the loudest bin above 8 kHz stands over its neighbours, gated so an inaudible
  partial does not cry wolf. A lone inharmonic sine up there carries almost no
  energy — peak, RMS and centroid all read normal — and is the most piercing
  thing in the mix.
* **`loop_pop`** compares the step across the loop point to the steps the
  waveform is already making. A seam only pops when it is bigger than the
  material.

The compiler also lints for clipping, DC offset, near-silence, a mix dominated
by noise, an inaudible staff, and a live solo or mute.

---

## 10. Reading a sheet

Every compile writes `out/<name>_sheet.png`:

1. **Waveform** — left over right. Lopsidedness is a panning bug.
2. **Spectrogram** — log frequency, so an octave is always the same height.
   Aliasing shows as energy mirrored below a source's fundamental.
3. **Hits** — level over time, with detected onsets in gold over the bar grid
   you wrote. This is the panel that answers *did it land where the file said*.

The JSON carries every number per staff and per layer, including each one's
peak and spectral centroid — which is how you find *which* staff is the bright
one without listening to seven of them.

---

## 11. Hard-won rules

* **A piano is worth sampling.** The synthesised `piano` source models the
  things that separate a struck string from a plucked one — stretched partials,
  the hammer notch where the felt lands, a two-stage decay, a soundboard — and
  it is still recognisably not a piano. An acoustic piano is two hundred
  strings, a soundboard, sympathetic resonance across the whole instrument and
  a different timbre at every velocity. Nobody synthesises one in production,
  and neither should this. Use `source=sample` and keep the modelled sources
  for things that are not trying to be a specific famous object.
* **`pluck` is a plucked string and `piano` is a struck one, and the
  difference is not the envelope.** A plucked string's partials are exactly
  harmonic; a piano's are progressively sharp, because stiff strings resonate
  above where an ideal string would — which is why piano octaves are stretched
  and why a `pluck` with a long decay reads as a harp however it is labelled.
  `piano` also pairs its strings a couple of cents apart so they beat, notches
  out the partials with a node where the hammer lands (an eighth along the
  string, so partial 8 and its multiples vanish), and decays in two stages.
  Give it `env=natural`: a source that decays on its own must not have a
  second envelope multiplied over the top.
* **A synth's timbre must not be an accident of its pitch.** Delay-line
  (Karplus-Strong) strings were tried and abandoned: their spectrum emerges
  from a feedback loop, and at some pitches the fundamental held 1% of the
  energy while a middle harmonic took over. Plucks are additive with
  per-partial decay, because "higher partials die sooner" is one rule that
  gives the same instrument at every pitch.
* **A reverb impulse response is causal; a filter kernel is not.** The FFT
  convolution helper compensates for a symmetric kernel's delay, which is
  right for filters and catastrophic for reverb — it dragged every tail half
  its length earlier, so pieces opened with the reverb of music that had not
  happened yet. Pass `causal=True` for anything that is an impulse response.
* **A cymbal is metal that also hisses, not hiss with metal in it.** Hats built
  from white noise highpassed at 6.5 kHz had *zero* energy below 2 kHz: no
  body, just air, which is what "hissy" describes.
* **Discrete partials stop at 8 kHz; above that, noise.** Real struck metal has
  plenty up there but packed too densely to hear as pitches. A lone sine is an
  artefact no energy-based measurement catches.
* **Brightness moves a rolloff knee. It never scales a partial by an exponent
  of its index** — `bright ** (i * 0.4)` is a 13× boost by the fourteenth
  partial, which put the highest mode above the fundamental.
* **DC is immortal in a feedback loop.** Every source is DC-blocked on the way
  out.
* **Effects must be level-neutral** or every declared level becomes a lie.
* **Reach for a percentage before a number.** `at=62%` survives a change of
  `length`; `at=0.21 s` does not.
* **A check is cheaper than a listen.** State the intended duration, peak and
  hit count while authoring; a broken render then announces itself.
