# WAM Audio (`.wama`) — language spec

WAM's model language exists because an LLM cannot place a vertex but can make
a named, relative, discrete decision. Audio has the same shape, and one extra
constraint: **the author cannot listen.** So the compiler owns every sample,
and every compile also emits a picture and a page of measurements, because
those are the only way the result can be reviewed.

The rule, in one line: *the author states musical and acoustic intent; the
compiler produces the waveform, the balance, and the proof.*

```
python3 -m wam.audio_cli examples/audio/tavern.wama --stems
```

writes `out/<name>.wav`, `out/<name>_sheet.png` and `out/<file>_audio.json`.
Exit status is non-zero if any declared `assert` fails.

---

## 1. File shape

Line-oriented, indentation-scoped, two spaces per level, `#` comments.
Sections at column 0:

```
audio <name>        header: rate, key, tempo, master
tones               redefine or derive tone adjectives      (optional)
voices              instrument recipes shared by everything
song <name>         a piece of music, made of tracks
sound <name>        a one-shot, made of layers
```

A file may hold any number of songs and sounds; every one is rendered.

### Header

| field | meaning |
|---|---|
| `rate 44100` | 22050, 32000, 44100 or 48000 |
| `key d minor` | default key for songs that do not set their own |
| `tempo walk` | default tempo: bpm, or `funeral slow walk medium brisk fast frantic` |
| `master -1.5` | peak target in dBFS for every piece in the file |

---

## 2. Voices

A voice is an instrument: an archetype plus adjectives, never a patch of
numbers.

```
voice lute source=pluck tone=bright env=pluck level=90% damp=0.55 space=room
```

| option | values |
|---|---|
| `source=` | `sine tri saw square pulse pluck bell metal thump breath bow drone noise pink band` |
| `tone=` | `plain bright warm dark soft harsh hollow thin fat`, or any tone you define |
| `env=` | `pluck hit stab pad swell sustain gate bloom bow` |
| `level=` | fader, as a percentage |
| `octave=` | whole octaves up or down |
| `detune=` | cents |
| `damp=` | plucked-string decay: 0 an open string, 1 palm-muted |
| `glide=` | `none short long` — pitch slides from the previous note |
| `space=` | `none room hall cave plate` |
| `echo=` | `none slap tape canyon` |
| `drive=` | `none soft hard fuzz` |
| `pan=` | `left center right hard-left hard-right` or a signed percentage |

`space=` places a sound; it never changes its level. The wet path is matched
to the dry one and the crossfade's loss is put back, so a fader means what it
says whether or not a space is declared.

### Envelopes

`pad swell sustain gate bow stab` hold for exactly their note. `pluck hit
bloom` are allowed to ring past it — a plucked eighth cut dead at the eighth
sounds like a mute, not a note. Every envelope ends at true zero, so nothing
ever clicks.

---

## 3. Tones are defaults, not a closed set

Each tone is six fields: `tilt` (how fast harmonics fall away), `harm` (how
many there are), `noise`, `odd` (odd harmonics only), `thin`, `spread`
(detune, in cents). Override any of them three ways:

```
tones
  tone bright harm=1.2 noise=0%        # redefine a built-in, file-wide
  tone reedy from=warm odd=on tilt=1.3 # derive a new name from any tone

voices
  voice lute source=pluck tone=bright harm=1.4    # inline, this voice only

sound zap
  layer edge
    source metal bright thin=on spread=12         # inline, this layer only
```

A name with no `from=` that matches a built-in starts from that built-in;
otherwise it starts from `plain`.

---

## 4. Songs are made of tracks

A track is a DAW track: one voice, its own arrangement, its own mix strip.

```
song tavern_loop
  bars 8
  meter 4
  feel straight             # straight | swing | shuffle
  progression i i VI V
  space room

  track lute voice=lute notes=e level=55% pan=left humanize=light
    phrase A = *1 *3 *5 *3 *5^ *3 *5 *3
    play A
```

| track option | meaning |
|---|---|
| `voice=` | which voice plays it (defaults to the track's own name) |
| `notes=` | default note length: `w h q e s t` |
| `level= pan= octave= space= echo=` | the mix strip |
| `humanize=` | `off light loose` — seeded timing and velocity jitter |
| `mute` / `solo` | bare flags; any solo silences every track not soloed |

A track whose `play` list is shorter than the song repeats to fill it: write
one bar, say `bars 8`. `--stems` writes each track as its own WAV and sheet,
at exactly its level in the mix.

### Phrases

`phrase A = 1 5 b3 5` — tokens are **scale degrees**, never frequencies.

| token | meaning |
|---|---|
| `5` | fifth degree of the key |
| `b3` `#4` | flattened / sharpened degree |
| `1^` `5_` | octave up / down (repeatable) |
| `[1,b3,5]` | chord |
| `.` | rest |
| `~` | tie to the previous note |
| `:q` `:e.` | this note's length; dotted with a trailing `.` |
| `!` `?` | accent / soften |

`play A A B A'` names phrases in order, with modifiers:

| modifier | meaning |
|---|---|
| `'` | *answer*: same rhythm, cadenced ending — the last note is pulled to the tonic and the one before it to the leading tone below |
| `^` `_` | the whole phrase an octave up / down |
| `~` | the whole phrase quieter, as an echo |

### Harmony

`progression i i VI V` gives one chord per bar, looping under the song.
Chords are built by stacking thirds **inside the declared mode**, so a numeral
always lands in the key. Case overrides quality — lowercase minor, uppercase
major — which is how a minor key gets its major `V`. Suffixes: `7` adds the
seventh, `o` diminishes, `+` augments, `:2` holds for two bars.

Then any track can spell itself against the harmony instead of against the key:

| token | meaning |
|---|---|
| `*` | the whole chord sounding now |
| `*1 *3 *5 *7` | its root, third, fifth, seventh |
| `*5^` `*1_` | the same, an octave up or down |

This is the point of the section: state the harmony once and every track
agrees with every other one, at every bar, without restating a note.

### Drums

A track with `voice=kit` uses `pattern` instead of `phrase`:

```
track drums voice=kit notes=q
  pattern P = k . s .
  play P
```

`k` kick, `s` snare, `h` hat, `H` open hat, `t`/`T` toms, `c` crash, `r` ride,
`.` rest. The kit is built from the same archetypes an author gets.

---

## 5. Sounds are made of layers

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
    env hit
    length 55%
    level 75%
```

Every time inside a sound is a **percentage of the sound's length** — `at=62%`
starts a layer 62% of the way in, `length 55%` runs it for 55% of the whole.
Making the sound bigger is one edit.

| layer line | meaning |
|---|---|
| `source <archetype> [<tone>] [field=..]` | what it is |
| `pitch <register\|note\|Hz>` | registers: `sub low mid high air sparkle`; or `a2`; or `440` |
| `env <name>` | envelope |
| `length <%\|secs>` | relative to the sound, or absolute |
| `level <%>` | its share of the mix |
| `cut <low\|high\|band> <freq> [q=]` | a fixed filter |
| `sweep <pitch\|center\|level> <from> -> <to> [curve=]` | a moving one |
| `drive` / `space` | per layer |
| `repeat <n> every <%> [decay=]` | a burst of copies |

Sweep curves: `linear ease fast slow snap drop`. `center` sweeps the band of a
noise source and a filter over a tone source — either way the author says
`center`.

---

## 6. Checks

Any song or sound may assert on its own measurements. Checks make a piece a
regression test, and they are how an author who cannot listen states what the
sound is *supposed* to be.

```
checks
  assert peak <= -1.0 db
  assert duration ~ 22.9 s
  assert centroid < 3000 hz
  assert onsets == 4
```

Metrics: `duration` (`length`), `peak`, `rms`, `crest`, `centroid`, `dc`,
`onsets`, `clipped`, and `loop_pop` for looping songs. Operators are
`< <= > >= ==` and `~` (within 10%). Units: `db hz khz s ms`.

* `centroid` is **power**-weighted. A magnitude-weighted centroid reads an
  inaudible hiss floor as brightness — a 440 Hz sine with −60 dB noise on it
  measures 1.5 kHz that way and 440 Hz this way.
* `onsets` is spectral flux, so it counts a hit that lands inside a sustaining
  chord, where energy alone would miss it. The analysis window scales with the
  length of the sound: a 30 ms click is nothing but an onset.
* `loop_pop` compares the step across the loop point to the steps the waveform
  is already making. A seam only pops when it is bigger than the material.

The compiler also lints for clipping, DC offset, near-silence, a mix dominated
by noise, an inaudible track, and a live solo or mute.

---

## 7. Reading a sheet

Every compile writes `out/<name>_sheet.png`:

1. **Waveform** — left channel over right. Lopsidedness is a panning bug.
2. **Spectrogram** — log frequency, so an octave is always the same height.
   Aliasing shows as energy mirrored below a source's fundamental.
3. **Hits** — level over time, with detected onsets in gold over the bar grid
   (songs) or the layer starts (sounds). This is the panel that answers *did
   the thing land where the file said it would*.

The JSON report carries every number, per track and per layer, including each
one's peak and spectral centroid — which is how you find out *which* track is
the bright one without listening to five of them.

---

## 8. Hard-won rules

* **A synth's timbre must not be an accident of its pitch.** Delay-line
  (Karplus-Strong) strings were tried first and abandoned: their spectrum is an
  emergent property of a feedback loop, and at some pitches the fundamental
  held 1% of the energy while a middle harmonic took over. Plucks are additive
  with per-partial decay, because "higher partials die sooner" is one rule that
  gives the same instrument at every pitch.
* **DC is immortal inside a feedback loop** and lives under everything else.
  Every source is DC-blocked on the way out.
* **FM aliases quietly.** A bell's sidebands run past Nyquist and fold back as
  inharmonic grit — which is what a bell is made of, so it hides. The index is
  capped to keep the top sideband in band.
* **Effects must be level-neutral** or every declared level becomes a lie.
* **Reach for a percentage before a number.** `at=62%` survives a change of
  `length`; `at=0.21 s` does not.
* **A check is cheaper than a listen.** State the intended duration, peak and
  hit count while authoring; a broken render then announces itself.
