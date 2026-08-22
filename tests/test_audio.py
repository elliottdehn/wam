#!/usr/bin/env python3
"""WAM audio: the language, the synthesis, and the measurements.

    python3 tests/test_audio.py

An author of audio cannot listen to the result, so the compiler's numbers are
the only review that happens. These tests are mostly about keeping those
numbers honest: a pitch that is the pitch that was asked for, a level that
survives an effect, a metric that measures the thing it is named after.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wam import audio as waudio          # noqa: E402
from wam import audio_parser as ap       # noqa: E402
from wam import audio_sheet as wsheet    # noqa: E402
from wam import notation as wnotation    # noqa: E402
from wam import synth                    # noqa: E402

fails = []
TOTAL = 0
SR = 44100


def check(label, cond, detail=""):
    global TOTAL
    TOTAL += 1
    print(("PASS  " if cond else "FAIL  ") + label
          + ("\n        " + detail if detail and not cond else ""))
    if not cond:
        fails.append(label)


def peak_freq(x, sr=SR, below=None):
    sp = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    f = np.fft.rfftfreq(len(x), 1.0 / sr)
    if below is not None:
        sp, f = sp[f < below], f[f < below]
    return float(f[np.argmax(sp)])


def centroid(x, sr=SR):
    sp = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1.0 / sr)
    return float(np.sum(f * sp) / max(np.sum(sp), 1e-12))


# --------------------------------------------------------------- synthesis

print("\n-- sources --")
for kind in synth.SOURCES:
    x = synth.render_source(kind, 330.0, SR // 4, SR)
    check("%s is finite and bounded" % kind,
          bool(np.isfinite(x).all()) and float(np.max(np.abs(x))) <= 1.0001)
    check("%s is DC-free" % kind, abs(float(np.mean(x))) < 0.01,
          "mean %.4f" % float(np.mean(x)))

# A pitched source has to actually be at the pitch it was handed, at every
# pitch. This is the check that caught the delay-line string, whose loudest
# partial was the fourth harmonic for a 587 Hz note.
print("\n-- pitch --")
for kind in ("sine", "saw", "tri", "pluck", "bow", "drone"):
    for f0 in (73.4, 146.8, 293.7, 587.3):
        x = synth.render_source(kind, f0, SR, SR, "plain", 1)
        if kind == "pluck":
            x = x * synth.envelope("pluck", SR, SR)
        got = peak_freq(x, below=f0 * 1.6)
        check("%s at %.0f Hz peaks on its fundamental" % (kind, f0),
              abs(got - f0) / f0 < 0.05, "measured %.1f Hz" % got)

print("\n-- tone adjectives --")
base = centroid(synth.render_source("saw", 220.0, SR // 2, SR, "plain"))
bright = centroid(synth.render_source("saw", 220.0, SR // 2, SR, "bright"))
dark = centroid(synth.render_source("saw", 220.0, SR // 2, SR, "dark"))
check("bright is brighter than plain, dark is darker", dark < base < bright,
      "dark %.0f  plain %.0f  bright %.0f" % (dark, base, bright))
override = centroid(synth.render_source("saw", 220.0, SR // 2, SR,
                                        synth.resolve_tone("dark", {"harm": 1.9})))
check("an inline harm= override beats the preset it came from", override > dark,
      "dark %.0f  overridden %.0f" % (dark, override))

print("\n-- envelopes --")
for name in synth.ENVELOPES:
    e = synth.envelope(name, int(0.4 * SR), SR)
    check("%s envelope starts and ends silent" % name,
          e[0] < 1e-6 and e[-1] < 1e-6)
short = synth.envelope("pad", int(0.02 * SR), SR)
check("a long envelope on a short note still closes", short[-1] < 1e-6)

print("\n-- filters --")
noise = synth.noise("white", SR, SR, seed=2)
lp = synth.filter_signal(noise, "low", 1000.0, SR)
check("a 1 kHz lowpass keeps the band and drops the stop",
      centroid(lp) < 900.0, "centroid %.0f" % centroid(lp))
swept = synth.noise("band", SR, SR, seed=3, center=synth.ramp(700, 2600, SR, "ease"))
early = peak_freq(swept[:8192])
late = peak_freq(swept[-8192:])
check("a swept band tracks its curve", 550 < early < 900 and 2200 < late < 3000,
      "start %.0f Hz, end %.0f Hz" % (early, late))

print("\n-- reverb is causal --")
# A reverb impulse response is not symmetric, and the FFT helper's
# linear-phase compensation -- correct for filter kernels -- dragged every
# tail half its length earlier. Pieces opened with the reverb of music that
# had not happened yet, which read as a click at the loop point.
hit = np.zeros(3 * SR)
n = int(0.1 * SR)
hit[SR:SR + n] = synth.render_source("metal", 600.0, n, SR) * synth.envelope("hit", n, SR)
for space in ("room", "hall", "cave", "plate"):
    wet = synth.reverb(hit, space, SR)
    before = float(np.max(np.abs(wet[:SR - 64])))
    check("%s puts nothing before the sound that caused it" % space, before < 1e-9,
          "%.6f" % before)

print("\n-- cymbals have a body --")
# Hats built from white noise highpassed at 6.5 kHz had zero energy below
# 2 kHz: all air, no metal, which is what "hissy" describes.
for piece_name in ("hat", "openhat", "crash", "ride"):
    x = waudio._drum(piece_name, SR, 3)
    sp = np.abs(np.fft.rfft(x)) ** 2
    f = np.fft.rfftfreq(len(x), 1.0 / SR)
    body = 100.0 * sp[(f > 1000) & (f < 4000)].sum() / sp.sum()
    check("the %s has energy where metal rings, not only where it hisses" % piece_name,
          body > 20.0, "%.0f%% between 1 and 4 kHz" % body)

print("\n-- effects are level-neutral --")
dry = synth.render_source("saw", 330.0, SR, SR) * synth.envelope("pluck", SR, SR)
rms = lambda x: float(np.sqrt(np.mean(x * x)))
for space in ("room", "hall", "cave", "plate"):
    wet = synth.reverb(dry, space, SR)
    ratio = 20 * np.log10(rms(wet[:len(dry)]) / rms(dry))
    check("%s does not change the level it is applied to" % space, abs(ratio) < 3.0,
          "%+.1f dB" % ratio)

# -------------------------------------------------------------- the language

print("\n-- parsing --")
SRC = """
audio t
  key d minor
  tempo walk
tones
  tone bright harm=1.2
  tone reedy from=warm odd=on
instruments
  instrument lute source=pluck tone=bright env=pluck
  instrument bass source=pluck tone=reedy octave=-2
song s
  meter 4/4
  bars 2
  staff bass instrument=bass level=70%
    voice line
      | d4 r4 a,4 r4
      | d4 r4 a,4 r4
  staff lute instrument=lute level=50%
    voice upper
      phrase A
        | d'8 f'8 a'8 f'8 d'8 f'8 a'8 f'8
      play A A
    voice lower
      | <d f>2 <d f>2
      | <d f>2 <d f>2
  checks
    assert peak <= -1 db
sound click
  length tiny
  layer tick
    source tri
    sweep pitch 1800 -> 900 curve=drop
    env hit
  assert onsets == 1
"""
doc = ap.parse(SRC)
check("tones, instruments, a song and a sound all parse",
      set(doc["tones"]) == {"bright", "reedy"} and len(doc["songs"]) == 1
      and len(doc["sounds"]) == 1)
song = doc["songs"][0]
check("a song is staves of voices",
      [st["name"] for st in song["staves"]] == ["bass", "lute"]
      and len(song["staves"][1]["voices"]) == 2)
check("a voice can write bars straight out, or name phrases and play them",
      len(song["staves"][0]["voices"][0]["bars"]) == 2
      and len(song["staves"][1]["voices"][0]["bars"]) == 2)

for bad, why in (
        ("audio t\n  key h minor\n", "an unknown key root"),
        ("audio t\n  key d minor\n  tempo 9\n", "a tempo outside the range"),
        ("audio t\nsong s\n  key d minor\n  tempo 90\n  staff a instrument=nope\n"
         "    voice v\n      | d4 r4 a,4 r4\n", "an instrument that is never declared"),
        ("audio t\nsong s\n  key d minor\n  tempo 90\n  staff a\n"
         "    voice v\n      phrase A\n        | d1\n      play B\n",
         "a phrase that is never declared"),
        ("audio t\ninstruments\n  instrument v source=saw wobble=3\n"
         "sound s\n  layer l\n    source saw\n", "an unknown instrument option"),
        ("audio t\nsound s\n  layer l\n    source saw\n    sweep pitch low\n",
         "a sweep with no destination"),
        ("audio t\nsound s\n  length 40%\n  layer l\n    source saw\n",
         "a sound whose own length is a percentage"),
        ("audio t\nsong s\n  key d minor\n  tempo 90\n  staff a\n"
         "    voice v\n      | d4 r4 a,4\n", "a bar that does not fill the meter"),
        ("audio t\nsong s\n  key d minor\n  tempo 90\n  staff a\n"
         "    voice v\n      | d4 r4 a,4 r4 d4\n", "a bar that overfills it"),
        ("audio t\nsong s\n  key d minor\n  tempo 90\n  staff a\n"
         "    voice v\n      | zz4 r4 a,4 r4\n", "a pitch that is not a pitch"),
        ("audio t\nsong s\n  key d minor\n  tempo 90\n  staff a\n"
         "    voice v\n      | d5 r4 a,4 r4\n", "a note value that does not exist"),
        ("audio t\nsong s\n  key d minor\n  tempo 90\n  staff a\n"
         "    voice v\n      | d4 r4 a,4 r4\n      phrase A\n        | d1\n"
         "      play A\n", "a voice that writes bars and phrases both"),
        ("audio t\nsong s\n  key d minor\n  tempo 90\n  meter 4/3\n  staff a\n"
         "    voice v\n      | d1\n", "a meter whose value is not a note value"),
):
    try:
        parsed = ap.parse(bad)
        waudio.compile_document(parsed)
        raised = False
    except ap.WamAudioError:
        raised = True
    except Exception:
        raised = False
    check("%s is rejected" % why, raised)

print("\n-- notation --")
key = ap.parse_key(["d", "minor"], 1, "")
meter = wnotation.parse_meter("4/4")
check("the key signature is derived from the mode",
      wnotation.key_signature(key) == {"b": -1},
      str(wnotation.key_signature(key)))
for mode, want in (("major", {"f": 1}), ("dorian", {"b": -1}),
                   ("phrygian", {"a": -1, "b": -1, "e": -1})):
    k = ap.parse_key(["g", mode], 1, "")
    check("g %s spells its own signature" % mode,
          wnotation.key_signature(k) == want, str(wnotation.key_signature(k)))

def midis(text, key=key, meter=meter, drums=False):
    bars = wnotation.parse_bars([(text, 1, "")], key, meter, drums)
    return [e.get("midis") or e["kind"] for e in bars[0]]

check("middle C is c'", midis("| c'1") == [[60]], str(midis("| c'1")))
check("octave marks move by twelves",
      midis("| c1") == [[48]] and midis("| c''1") == [[72]] and midis("| c,1") == [[36]])
check("the signature applies to a written note",
      midis("| b'1") == [[70]], str(midis("| b'1")))
check("an explicit accidental overrides it",
      midis("| b!'1") == [[71]], str(midis("| b!'1")))
check("and holds for the rest of the bar in that octave",
      midis("| b!'4 b'4 b'2") == [[71], [71], [71]], str(midis("| b!'4 b'4 b'2")))
check("but not into the next bar",
      [e.get("midis") for e in wnotation.parse_bars(
          [("| b!'4 b'4 b'2", 1, ""), ("| b'1", 2, "")], key, meter)[1]] == [[70]])
check("LilyPond and plain spellings agree",
      midis("| bes'1") == midis("| bb'1") == [[70]])
check("durations are note values",
      [e["beats"] for e in wnotation.parse_bars([("| c'2 c'4 c'8 c'8", 1, "")],
                                                key, meter)[0]] == [2.0, 1.0, 0.5, 0.5])
check("a dot adds half",
      [e["beats"] for e in wnotation.parse_bars([("| c'4. c'8 c'2", 1, "")],
                                                key, meter)[0]] == [1.5, 0.5, 2.0])
check("durations are sticky",
      [e["beats"] for e in wnotation.parse_bars([("| c'8 d' e' f' g' a' b' c''", 1, "")],
                                                key, meter)[0]] == [0.5] * 8)
check("a chord is one event with several pitches",
      midis("| <d' f' a'>1") == [[62, 65, 69]], str(midis("| <d' f' a'>1")))
check("q repeats the chord before it",
      midis("| <d' f' a'>4 q4 q4 q4") == [[62, 65, 69]] * 4)
check("q carries across a bar line",
      [e.get("midis") for e in wnotation.parse_bars(
          [("| <d' f'>1", 1, ""), ("| q4 q4 q2", 2, "")], key, meter)[1]]
      == [[62, 65]] * 3)
check("a chord symbol is voiced from its name",
      midis("| <Dm>1") == [[50, 53, 57]], str(midis("| <Dm>1")))
check("and knows its qualities", midis("| <A7>1") == [[57, 61, 64, 67]],
      str(midis("| <A7>1")))
check("a dynamic takes no time",
      [e["beats"] for e in wnotation.parse_bars([("| \\p c'1", 1, "")], key, meter)[0]]
      == [0.0, 4.0])
check("a drum staff reads pieces, not pitches",
      midis("| k4 s4 h4 r4", drums=True) == ["drum", "drum", "drum", "rest"])
check("7/8 is three and a half beats", wnotation.parse_meter("7/8")["beats"] == 3.5)
check("6/8 is three", wnotation.parse_meter("6/8")["beats"] == 3.0)
check("a bar is checked against the meter it is in",
      len(wnotation.parse_bars([("| c'8 d' e' f' g' d' e'", 1, "")], key,
                               wnotation.parse_meter("7/8"))) == 1)

# A tie is written on the note that starts it and may cross a bar line, so it
# is joined after the bars are checked -- checking counts the written value.
tied = wnotation.parse_bars([("| c'2 c'2~", 1, ""), ("| c'2 r2", 2, "")], key, meter)
flat, total = waudio.flatten_voice(tied, 4.0)
check("a tie joins two notes into one",
      [round(e["beats"], 3) for _, e in flat] == [2.0, 4.0, 2.0],
      str([round(e["beats"], 3) for _, e in flat]))
check("and the bars still measured four beats each", total == 8.0)

print("\n-- the score reaches the audio --")

# The end-to-end claim of the notation: the pitch written on the staff is the
# pitch in the samples, key signature and all.
doc = ap.parse(SRC)
piece = waudio.compile_document(doc)[0]
bass = piece["stems"]["bass"].mean(axis=1)
beat = 60.0 / doc["tempo"]
for bar in range(2):
    for slot, want_midi in ((0, 50), (2, 45)):        # `d4` then `a,4`
        want = waudio.midi_to_freq(want_midi - 24)    # the staff is octave=-2
        start_s = (bar * 4 + slot) * beat
        seg = bass[int(start_s * SR) + 1500:int(start_s * SR) + int(0.5 * SR)]
        got = peak_freq(seg, below=200.0)
        check("bar %d beat %d sounds the note that is written" % (bar + 1, slot + 1),
              abs(got - want) / want < 0.05,
              "wanted %.1f Hz, measured %.1f Hz" % (want, got))

# The signature is not decoration: a written `b` in D minor has to arrive as
# B flat, not B natural.
FLAT = SRC.replace("      | d4 r4 a,4 r4\n      | d4 r4 a,4 r4",
                   "      | b,1\n      | b,1")
flat_piece = waudio.compile_document(ap.parse(FLAT))[0]
seg = flat_piece["stems"]["bass"].mean(axis=1)[2000:int(0.8 * SR)]
got = peak_freq(seg, below=200.0)
want = waudio.midi_to_freq(46 - 24)                   # B flat, not B natural
check("a written b in d minor sounds as b flat",
      abs(got - want) / want < 0.05,
      "wanted %.1f Hz (Bb), measured %.1f Hz" % (want, got))

print("\n-- mix --")
check("declared checks pass on the example", all(c["ok"] for c in piece["checks"]),
      str([c for c in piece["checks"] if not c["ok"]]))
check("the example lints clean", not piece["warnings"], str(piece["warnings"]))
check("every staff contributes audible level",
      all(t["peak_db"] > -40 for t in piece["meta"]["staves"]))
check("a staff reports how many voices it carries",
      [t["voices"] for t in piece["meta"]["staves"]] == [1, 2],
      str([t["voices"] for t in piece["meta"]["staves"]]))
check("stems line up with the mix sample for sample",
      all(len(v) == len(piece["buf"]) for v in piece["stems"].values()))
summed = sum(piece["stems"].values())
# The stems are the mix, minus the master bus space, so they cannot be
# bit-identical -- but they must not be a different balance.
check("the stems sum to something close to the mix",
      abs(20 * np.log10(rms(summed) / rms(piece["buf"]))) < 4.0,
      "%+.1f dB" % (20 * np.log10(rms(summed) / rms(piece["buf"]))))

solo = waudio.compile_document(ap.parse(
    SRC.replace("staff lute instrument=lute level=50%",
                "staff lute instrument=lute level=50% solo")))[0]
check("solo silences everything else",
      [t["staff"] for t in solo["meta"]["staves"]] == ["lute"],
      str([t["staff"] for t in solo["meta"]["staves"]]))
check("a soloed render says so", any("solo is on" in w for w in solo["warnings"]))

# A looping song is delivered twice, because the loop-ready render has its
# ring-out wrapped onto its head and sounds truncated played once.
loops = waudio.compile_document(ap.parse(SRC.replace("  bars 2", "  bars 2\n  loop off")))[0]
check("loop off renders only the un-wrapped version", loops["once"] is None)
check("a looping song carries both renders", piece["once"] is not None)
check("and the un-wrapped one is longer, because it keeps its tail",
      len(piece["once"]) > len(piece["buf"]),
      "%d vs %d samples" % (len(piece["once"]), len(piece["buf"])))

print("\n-- measurements --")
sine = synth.osc("sine", 440.0, SR, SR)
dirty = sine + 0.001 * np.random.default_rng(0).standard_normal(SR)
m = waudio.measure(dirty, SR)
check("centroid ignores an inaudible noise floor", abs(m["centroid"] - 440.0) < 40.0,
      "measured %.0f Hz" % m["centroid"])
clicks = np.zeros(int(1.2 * SR))
for i in range(4):
    n = int(0.1 * SR)
    at = int(i * 0.25 * SR)
    clicks[at:at + n] += synth.render_source("metal", 400.0, n, SR) * \
        synth.envelope("hit", n, SR)
found = waudio.find_onsets(clicks, SR)
check("four hits count as four onsets", len(found) == 4, str([round(t, 3) for t in found]))
check("onsets land on the hits",
      all(abs(t - i * 0.25) < 0.03 for i, t in enumerate(found)) if len(found) == 4 else False,
      str([round(t, 3) for t in found]))
check("silence has no onsets", waudio.find_onsets(np.zeros(SR), SR) == [])

pad = synth.render_source("drone", 220.0, SR, SR) * synth.envelope("pad", SR, SR)
buried = pad.copy()
n = int(0.1 * SR)
buried[SR // 2:SR // 2 + n] += 0.6 * synth.render_source("metal", 900.0, n, SR) * \
    synth.envelope("hit", n, SR)
check("a hit inside a sustaining chord still counts",
      any(abs(t - 0.5) < 0.05 for t in waudio.find_onsets(buried, SR)))

seam = np.concatenate([np.linspace(0, 0.8, SR), np.linspace(0.8, 0.0, SR)])
smooth = waudio.measure(np.sin(2 * np.pi * 440 * np.arange(SR) / SR), SR, loop=True)
popped = waudio.measure(np.concatenate([np.full(SR // 2, 0.8), np.full(SR // 2, -0.8)]),
                        SR, loop=True)
check("loop_pop separates a clean seam from a step",
      smooth["loop_pop"] < popped["loop_pop"],
      "clean %.1f  stepped %.1f" % (smooth["loop_pop"], popped["loop_pop"]))

# The tweeter-tone class of bug: inaudible in every energy-based measurement,
# and the first thing a listener notices. It was found by ear twice before this
# detector existed.
print("\n-- whistle detection --")
noise = synth.noise("white", SR, SR, seed=1)
tone = np.sin(2 * np.pi * 12000 * np.arange(SR) / SR)
check("white noise is not a whistle", waudio.whistle_db(noise, SR) < 10.0,
      "%.1f dB" % waudio.whistle_db(noise, SR))
check("an audible tone buried in noise is", waudio.whistle_db(noise + 0.3 * tone, SR) > 20.0,
      "%.1f dB" % waudio.whistle_db(noise + 0.3 * tone, SR))
check("a bare high sine certainly is", waudio.whistle_db(tone, SR) > 60.0)
# Above 8 kHz the floor is digital silence, so an inaudible partial towers over
# its neighbours; without a level gate the metric cries wolf on every track.
quiet = synth.osc("sine", 220.0, SR, SR) + 1e-4 * tone
check("a partial 80 dB down is not a whistle", waudio.whistle_db(quiet, SR) == 0.0,
      "%.1f dB" % waudio.whistle_db(quiet, SR))
for kit_piece in ("hat", "openhat", "ride", "crash"):
    x = np.tile(waudio._drum(kit_piece, SR, 3), 4)
    check("the %s is a cymbal, not a beep" % kit_piece, waudio.whistle_db(x, SR) < 30.0,
          "%.1f dB" % waudio.whistle_db(x, SR))
for kind, f0 in (("bow", 880.0), ("bell", 780.0), ("metal", 900.0)):
    x = synth.render_source(kind, f0, SR, SR, "bright", 2)
    check("a bright %s has no lone partial in the tweeters" % kind,
          waudio.whistle_db(x, SR) < 30.0, "%.1f dB" % waudio.whistle_db(x, SR))

print("\n-- level ramps --")
ramp_src = """
audio t
  key c major
  tempo 120
instruments
  instrument v source=saw env=gate
song s
  meter 4/4
  bars 4
  staff v instrument=v level=20%->100%
    voice line
      phrase A
        | c'4 c'4 c'4 c'4
      play A A A A
"""
ramped = waudio.compile_document(ap.parse(ramp_src))[0]
mono = np.abs(ramped["stems"]["v"]).mean(axis=1)
head = rms(mono[:SR])
tail = rms(mono[-2 * SR:-SR])
check("a level ramp actually builds", 20 * np.log10(tail / head) > 8.0,
      "%+.1f dB across the song" % (20 * np.log10(tail / head)))
flat_src = ramp_src.replace("level=20%->100%", "level=100%")
flat = waudio.compile_document(ap.parse(flat_src))[0]
fmono = np.abs(flat["stems"]["v"]).mean(axis=1)
check("without a ramp the level holds",
      abs(20 * np.log10(rms(fmono[-2 * SR:-SR]) / rms(fmono[:SR]))) < 2.0)

print("\n-- checks and lint --")
res = waudio.run_checks([{"metric": "peak", "op": "<=", "value": -1.0, "unit": "db",
                          "text": "peak <= -1 db"}], {"peak": -2.0})
check("a passing assert passes", res[0]["ok"])
res = waudio.run_checks([{"metric": "duration", "op": "~", "value": 1.0, "unit": "s",
                          "text": "duration ~ 1 s"}], {"duration": 1.5})
check("~ is a tolerance, not a wildcard", not res[0]["ok"])
res = waudio.run_checks([{"metric": "wobble", "op": ">", "value": 1.0, "unit": "",
                          "text": "wobble > 1"}], {"peak": -2.0})
check("an unknown metric fails loudly rather than passing", not res[0]["ok"])

print("\n-- determinism and output --")
EXAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                        "examples", "audio")
tavern = waudio.compile_document(ap.parse_file(os.path.join(EXAMPLES, "tavern.wama")))[0]
again = waudio.compile_document(ap.parse_file(os.path.join(EXAMPLES, "tavern.wama")))[0]
check("the same file renders the same samples twice",
      np.allclose(again["buf"], tavern["buf"]))

sheet = wsheet.build_sheet(piece, width=600)
check("a sheet is a real image", sheet.ndim == 3 and sheet.shape[1] == 600
      and float(sheet.max()) > 0.5)
check("every glyph the sheet draws is 5x7",
      all(len(g.split("/")) == 7 and all(len(r) == 5 for r in g.split("/"))
          for g in wsheet.FONT.values()))

import tempfile                                                   # noqa: E402
with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "t.wav")
    waudio.write_wav(path, piece["buf"], piece["rate"])
    import wave                                                   # noqa: E402
    with wave.open(path) as fh:
        check("the wav is 16-bit stereo at the declared rate",
              fh.getnchannels() == 2 and fh.getsampwidth() == 2
              and fh.getframerate() == piece["rate"])
        check("the wav holds every sample", fh.getnframes() == len(piece["buf"]))

sfx = waudio.compile_document(ap.parse_file(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "examples", "audio", "sfx.wama")))
check("every example sound compiles", len(sfx) == 7)
check("every example sound passes its own checks",
      all(c["ok"] for p in sfx for c in p["checks"]),
      str([c for p in sfx for c in p["checks"] if not c["ok"]]))
check("every example sound lints clean",
      all(not p["warnings"] for p in sfx),
      str([(p["name"], p["warnings"]) for p in sfx if p["warnings"]]))
check("no example sound clips", all(p["metrics"]["clipped"] == 0 for p in sfx))

theme = waudio.compile_document(ap.parse_file(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "examples", "audio",
    "menu_theme.wama")))[0]
check("the menu theme passes its checks", all(c["ok"] for c in theme["checks"]),
      str([c for c in theme["checks"] if not c["ok"]]))
check("the menu theme lints clean", not theme["warnings"], str(theme["warnings"]))
for name, stem in theme["stems"].items():
    w = waudio.whistle_db(stem.mean(axis=1), theme["rate"])
    check("the %s track has no tweeter tone" % name, w < 26.0, "%.1f dB" % w)
check("every example music track is built, not blasted",
      all(p["metrics"]["air"] < 12.0 for p in (theme, piece)),
      str([p["metrics"]["air"] for p in (theme, piece)]))

print("\n%d checks, %d failed" % (TOTAL, len(fails)))
if fails:
    for f in fails:
        print("  FAILED: %s" % f)
    sys.exit(1)
