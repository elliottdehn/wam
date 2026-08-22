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
voices
  voice lute source=pluck tone=bright env=pluck
  voice bass source=pluck tone=reedy octave=-2
song s
  bars 2
  progression i V
  track bass voice=bass notes=q
    phrase L = *1 . *5 .
    play L
  track lute voice=lute notes=e level=50%
    phrase A = 1 5 b3 5 1^ 5 b3 5
    play A
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
check("tones, voices, a song and a sound all parse",
      set(doc["tones"]) == {"bright", "reedy"} and len(doc["songs"]) == 1
      and len(doc["sounds"]) == 1)
check("a track is a track", doc["songs"][0]["tracks"][0]["name"] == "bass")

for bad, why in (
        ("audio t\n  key h minor\n", "an unknown key root"),
        ("audio t\n  key d minor\n  tempo 9\n", "a tempo outside the range"),
        ("audio t\nsong s\n  key d minor\n  tempo 90\n  track a voice=nope\n"
         "    phrase A = 1\n    play A\n", "a voice that is never declared"),
        ("audio t\nsong s\n  key d minor\n  tempo 90\n  track a\n"
         "    phrase A = 1\n    play B\n", "a phrase that is never declared"),
        ("audio t\nvoices\n  voice v source=saw wobble=3\n"
         "sound s\n  layer l\n    source saw\n", "an unknown voice option"),
        ("audio t\nsound s\n  layer l\n    source saw\n    sweep pitch low\n",
         "a sweep with no destination"),
        ("audio t\nsound s\n  length 40%\n  layer l\n    source saw\n",
         "a sound whose own length is a percentage"),
):
    try:
        ap.parse(bad)
        if "voice that is never declared" in why:
            waudio.compile_document(ap.parse(bad))
        raised = False
    except ap.WamAudioError:
        raised = True
    except Exception:
        raised = False
    check("%s is rejected" % why, raised)

print("\n-- harmony --")
key = ap.parse_key(["d", "minor"], 1, "")
names = "C C# D D# E F F# G G# A A# B".split()
spell = lambda sym: [names[waudio.degree_to_midi(n, key) % 12]
                     for n in waudio.chord_notes(ap.parse_chord_symbol(sym, 1, ""), key)]
check("i in d minor is D F A", spell("i") == ["D", "F", "A"], str(spell("i")))
check("iv stays in the key", spell("iv") == ["G", "A#", "D"], str(spell("iv")))
check("uppercase V raises the third for a real cadence",
      spell("V") == ["A", "C#", "E"], str(spell("V")))
check("V7 adds the diatonic seventh", spell("V7") == ["A", "C#", "E", "G"],
      str(spell("V7")))

# The end-to-end claim of the progression feature: what a track plays follows
# the harmony, in the rendered samples, without the track naming a note.
print("\n-- the progression reaches the audio --")
doc = ap.parse_file(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "examples", "audio", "tavern.wama"))
song = doc["songs"][0]
pieces = waudio.compile_document(doc)
piece = pieces[0]
spans = waudio.chord_timeline(song)
cycle = spans[-1][1]
bass = piece["stems"]["bass"].mean(axis=1)
beat = 60.0 / song["tempo"]
for bar in range(int(song["bars"])):
    sym = waudio.chord_at(spans, bar * song["meter"], cycle)
    want = waudio.midi_to_freq(waudio.degree_to_midi(
        waudio.chord_notes(sym, song["key"])[0], song["key"], -2))
    start = int(bar * song["meter"] * beat * piece["rate"]) + 2000
    got = peak_freq(bass[start:start + int(0.7 * piece["rate"])], below=400.0)
    check("bar %d plays the root of %s" % (bar + 1, sym["text"]),
          abs(got - want) / want < 0.04, "wanted %.1f Hz, measured %.1f Hz" % (want, got))

print("\n-- mix --")
check("declared checks pass on the example", all(c["ok"] for c in piece["checks"]),
      str([c for c in piece["checks"] if not c["ok"]]))
check("the example lints clean", not piece["warnings"], str(piece["warnings"]))
check("every track contributes audible level",
      all(t["peak_db"] > -40 for t in piece["meta"]["tracks"]))
check("stems line up with the mix sample for sample",
      all(len(s) == len(piece["buf"]) for s in piece["stems"].values()))
summed = sum(piece["stems"].values())
# The stems are the mix, minus the master bus space, so they cannot be
# bit-identical -- but they must not be a different balance.
check("the stems sum to something close to the mix",
      abs(20 * np.log10(rms(summed) / rms(piece["buf"]))) < 4.0,
      "%+.1f dB" % (20 * np.log10(rms(summed) / rms(piece["buf"]))))

solo_src = SRC.replace("track lute voice=lute notes=e level=50%",
                       "track lute voice=lute notes=e level=50% solo")
solo = waudio.compile_document(ap.parse(solo_src))[0]
check("solo silences everything else",
      [t["track"] for t in solo["meta"]["tracks"]] == ["lute"],
      str([t["track"] for t in solo["meta"]["tracks"]]))
check("a soloed render says so", any("solo is on" in w for w in solo["warnings"]))

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
voices
  voice v source=saw env=gate
song s
  bars 4
  track v voice=v notes=q level=20%->100%
    phrase A = 1
    play A
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
again = waudio.compile_document(ap.parse_file(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "examples", "audio", "tavern.wama")))[0]
check("the same file renders the same samples twice",
      np.allclose(again["buf"], piece["buf"]))

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
