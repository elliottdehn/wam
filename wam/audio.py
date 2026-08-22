"""WAM audio compiler: parsed decisions in, stereo samples and metrics out.

Split of responsibility, matching the model pipeline:

* ``audio_parser`` decides what the text says.
* this module decides what that means -- where a beat lands, how loud a layer
  ends up, how long a plucked note is allowed to ring past its slot.
* ``synth`` decides what it sounds like.

Nothing here asks the author for a number that can be derived. Degrees become
frequencies from the key, percentages become gains from the parent, and named
lengths become seconds from the piece.
"""
import math
import struct
import wave

import numpy as np

from . import audio_parser as ap
from . import synth


# --------------------------------------------------------------------- tones


def build_tone_table(doc):
    """Resolve the file's `tones` section, following `from=` chains.

    A file-defined name shadows the built-in of the same name everywhere it is
    used, which is what makes the built-ins defaults rather than a fixed set.
    """
    declared = doc.get("tones", {})
    table = {}

    def resolve(name, seen):
        if name in table:
            return table[name]
        spec = declared.get(name)
        if spec is None:
            return synth.tone_params(name)
        if name in seen:
            raise ap.WamAudioError("tone %r inherits from itself" % name, spec["line"])
        base_name = spec["base"]
        if base_name == name:
            # Redefining a built-in: start from the built-in, not from itself.
            base = synth.tone_params(name)
        else:
            base = resolve(base_name, seen | {name})
        params = dict(base)
        params.update(spec["fields"])
        table[name] = params
        return params

    for name in declared:
        resolve(name, frozenset())
    return table


def tone_for(name, overrides, table):
    return synth.resolve_tone(name, overrides, table)


# -------------------------------------------------------------------- pitch


def degree_to_midi(deg, key, octave=0):
    """Scale degree -> MIDI note. Degrees past the scale wrap up an octave."""
    if "semis" in deg:                    # already stacked by chord_notes
        semis = deg["semis"] + deg["alter"]
    else:
        intervals = ap.MODES[key["mode"]]
        n = len(intervals)
        idx = deg["degree"] - 1
        wrap, step = divmod(idx, n)
        semis = intervals[step] + 12 * wrap + deg["alter"]
    return 60 + key["root"] + semis + 12 * (deg["octave"] + octave)


def midi_to_freq(m):
    return 440.0 * 2.0 ** ((m - 69) / 12.0)


# ---------------------------------------------------------------- harmony


def chord_notes(symbol, key, seventh=None):
    """Stack thirds inside the mode: a numeral always lands in the key."""
    intervals = ap.MODES[key["mode"]]
    n = len(intervals)
    root = symbol["degree"] - 1
    want = 4 if (symbol["seventh"] if seventh is None else seventh) else 3
    out = []
    for i in range(want):
        idx = root + i * 2
        wrap, step = divmod(idx, n)
        out.append({"degree": idx + 1, "alter": 0, "octave": 0,
                    "semis": intervals[step] + 12 * wrap})
    # Case (and `o`/`+`) override the mode's own quality, third and fifth only.
    # The seventh is left diatonic, which is what makes `V7` in a minor key the
    # dominant seventh an author is asking for.
    quality = symbol.get("quality")
    if quality and len(out) >= 3:
        base = out[0]["semis"]
        third, fifth = {"major": (4, 7), "minor": (3, 7),
                        "dim": (3, 6), "aug": (4, 8)}[quality]
        out[1]["semis"] = base + third
        out[2]["semis"] = base + fifth
    return out


def chord_timeline(song):
    """(start_beat, end_beat, chord) for the whole song, tiled to fill it."""
    prog = song.get("progression")
    if not prog:
        return []
    meter = song["meter"]
    spans = []
    beat = 0.0
    for sym in prog:
        length = sym["bars"] * meter
        spans.append((beat, beat + length, sym))
        beat += length
    return spans


def chord_at(spans, beat, cycle_beats):
    """Which chord is sounding, with the progression looping under the song."""
    if not spans or cycle_beats <= 0:
        return None
    pos = beat % cycle_beats
    for start, end, sym in spans:
        if start <= pos < end:
            return sym
    return spans[-1][2]


# ------------------------------------------------------------------ stereo


PANS = {"left": -0.8, "hard-left": -1.0, "center": 0.0, "centre": 0.0,
        "right": 0.8, "hard-right": 1.0}


def pan_gains(pan):
    """Constant-power pan. Accepts a name or a signed percentage."""
    if pan in (None, ""):
        p = 0.0
    elif isinstance(pan, str) and pan in PANS:
        p = PANS[pan]
    else:
        try:
            p = float(str(pan).rstrip("%")) / (100.0 if "%" in str(pan) else 1.0)
        except ValueError:
            p = 0.0
    p = float(np.clip(p, -1.0, 1.0))
    angle = (p + 1.0) * math.pi / 4.0
    return math.cos(angle), math.sin(angle)


def mix_stereo(buf, sig, start, gain, pan):
    l, r = pan_gains(pan)
    synth.mix_into(buf[:, 0], sig, start, gain * l)
    synth.mix_into(buf[:, 1], sig, start, gain * r)


# ------------------------------------------------------------------- voices

# Envelopes that stop when the note stops, versus ones that ring past it. A
# plucked eighth note that gets cut dead at the eighth sounds like a mute, so
# decay-type voices are allowed to spill into the notes that follow.
SUSTAIN_ENVS = ("pad", "sustain", "swell", "gate", "bow", "stab")

GLIDE_TIMES = {"none": 0.0, "short": 0.04, "long": 0.14}


def _voice_defaults(name):
    return {"name": name, "source": "saw", "tone": "plain", "env": "pluck",
            "level": 1.0, "octave": 0, "detune": 0.0, "space": "none",
            "echo": "none", "drive": "none", "pan": "center", "damp": 0.5,
            "glide": "none", "cut": None, "tone_fields": {}}


def render_note(voice, freqs, dur_s, rate, tones, seed, prev_freq=None):
    """One note (or chord) of one voice, as a mono buffer.

    `dur_s` is the slot; the returned buffer may be longer when the voice's
    envelope rings out past it. Callers mix it in at the slot's start.
    """
    env_name = voice["env"]
    ring = 0.0 if env_name in SUSTAIN_ENVS else min(1.6 * dur_s, 0.9)
    total = max(dur_s + ring, 0.01)
    n = int(total * rate)
    if n <= 0:
        return np.zeros(0)
    env = synth.envelope(env_name, n, rate)
    tone = tone_for(voice["tone"], voice.get("tone_fields"), tones)
    glide = GLIDE_TIMES.get(voice.get("glide", "none"), 0.0)
    out = np.zeros(n)
    for i, f in enumerate(freqs):
        f = f * (2.0 ** (voice["detune"] / 1200.0)) if voice["detune"] else f
        curve = f
        if glide and prev_freq and abs(prev_freq - f) > 1e-6:
            k = min(int(glide * rate), n)
            curve = np.full(n, float(f))
            curve[:k] = synth.ramp(prev_freq, f, k, "ease")
        sig = synth.render_source(voice["source"], curve, n, rate, tone, seed + i * 17)
        out += sig
    if len(freqs) > 1:
        out /= math.sqrt(len(freqs))          # a chord is not N times louder
    out *= env
    if voice.get("cut"):
        out = synth.filter_signal(out, "low", ap.parse_freq(voice["cut"], voice.get("line"), ""), rate)
    out = synth.drive(out, voice.get("drive", "none"))
    return out


# -------------------------------------------------------------------- drums

# The kit is built from the same archetypes an author gets, so a piece can be
# reasoned about and, if it ever needs to be, replaced by a hand-written voice.
def _drum(piece, rate, seed):
    def env(name, secs):
        return synth.envelope(name, int(secs * rate), rate)

    if piece == "kick":
        n = int(0.30 * rate)
        body = synth.thump(55.0, n, rate, "plain", seed, drop=4.0)
        return body * synth.envelope("hit", n, rate)
    if piece == "snare":
        n = int(0.22 * rate)
        tone = synth.osc("tri", 190.0, n, rate, "warm", seed)
        rattle = synth.filter_signal(synth.noise("white", n, rate, seed + 1),
                                     "band", 1900.0, rate, q=1.2)
        return (0.5 * tone + 1.0 * rattle) * synth.envelope("hit", n, rate)
    if piece in ("hat", "openhat"):
        secs = 0.055 if piece == "hat" else 0.34
        n = int(secs * rate)
        x = synth.filter_signal(synth.noise("white", n, rate, seed + 2), "high", 6500.0, rate)
        metal = synth.metal(3200.0, n, rate, "bright", seed + 3)
        return (x + 0.4 * metal) * synth.envelope("hit", n, rate)
    if piece in ("tom", "lowtom"):
        n = int(0.34 * rate)
        base = 150.0 if piece == "tom" else 95.0
        return synth.thump(base, n, rate, "plain", seed, drop=2.2) * synth.envelope("hit", n, rate)
    if piece == "crash":
        n = int(1.4 * rate)
        x = synth.filter_signal(synth.noise("white", n, rate, seed + 4), "high", 3000.0, rate)
        return (x + 0.6 * synth.metal(2400.0, n, rate, "bright", seed + 5)) * \
            synth.envelope("bloom", n, rate)
    if piece == "ride":
        n = int(0.7 * rate)
        # A ride is a ping riding on a wash. Without the wash the ping is a
        # lone sine in the top octave -- measurably a whistle, audibly a beep.
        ping = synth.metal(1800.0, n, rate, "bright", seed + 6)
        wash = synth.filter_signal(synth.noise("white", n, rate, seed + 7),
                                   "high", 4200.0, rate)
        return (ping + 0.5 * wash) * synth.envelope("hit", n, rate)
    return np.zeros(0)


DRUM_PAN = {"kick": "center", "snare": "center", "hat": "right", "openhat": "right",
            "tom": "left", "lowtom": "left", "crash": "left", "ride": "right"}


# ------------------------------------------------------------------- phrases

# What a play-modifier means. These are the compiler's job, not the author's:
# the author says "answer this phrase", not "transpose the last note".
def apply_mods(events, mods, key):
    out = [dict(e) for e in events]
    for mod in mods:
        if mod == "^":
            out = [_shift_octave(e, 1) for e in out]
        elif mod == "_":
            out = [_shift_octave(e, -1) for e in out]
        elif mod == "'":
            out = _answer(out)
        elif mod == "~":
            out = [dict(e, accent=e.get("accent", 1.0) * 0.6) for e in out]
    return out


def _shift_octave(ev, delta):
    if ev.get("kind") in ("chord_here", "chord_tone"):
        return dict(ev, octave=ev.get("octave", 0) + delta)
    if ev.get("kind") not in ("note", "chord"):
        return dict(ev)
    return dict(ev, degrees=[dict(d, octave=d["octave"] + delta) for d in ev["degrees"]])


def _answer(events):
    """The variation form: same rhythm, resolved ending.

    The last sounding event is pulled to the tonic, and the one before it to
    the leading tone below -- a cadence, deterministically, from a `'`.
    """
    out = [dict(e) for e in events]
    sounding = [i for i, e in enumerate(out) if e.get("kind") in ("note", "chord")]
    # A `'` on a chord-following track would fight the progression, so the
    # cadence is only written onto tracks that spell their own notes.
    if not sounding:
        return out
    last = sounding[-1]
    out[last] = dict(out[last], degrees=[dict(out[last]["degrees"][0], degree=1, alter=0)])
    if len(sounding) > 1:
        prev = sounding[-2]
        out[prev] = dict(out[prev],
                         degrees=[dict(out[prev]["degrees"][0], degree=7, alter=0,
                                       octave=out[prev]["degrees"][0]["octave"] - 1)])
    return out


# ---------------------------------------------------------------- song build


def _track_events(part, song, tones):
    """Flatten a track's `play` list into (beat, event) pairs, repeated to fill."""
    default = ap.DURATION_BEATS[part["notes"]]
    seq = []
    beat = 0.0
    for item in part["play"]:
        if item["phrase"] is None:
            beat += item.get("bars", 1.0) * song["meter"]
            continue
        events = apply_mods(part["phrases"][item["phrase"]], item["mods"], song["key"])
        for ev in events:
            dur = ev["dur"] if ev["dur"] is not None else default
            seq.append((beat, ev, dur))
            beat += dur
    return seq, beat


def render_song(song, doc, tones, rate):
    meter = song["meter"]
    beat_s = 60.0 / song["tempo"]
    # Solo wins over mute, and any solo silences everything not soloed --
    # the same rule as every mixer, so the flags behave the way muscle memory
    # expects while an author is picking one track apart.
    spans = chord_timeline(song)
    cycle = spans[-1][1] if spans else 0.0
    soloed = [t for t in song["tracks"] if t["solo"]]
    live = soloed if soloed else [t for t in song["tracks"] if not t["mute"]]
    parts = []
    natural = 0.0
    for part in live:
        seq, length = _track_events(part, song, tones)
        parts.append((part, seq, length))
        natural = max(natural, length)
    if song["bars"]:
        total_beats = song["bars"] * meter
    else:
        total_beats = math.ceil(natural / meter) * meter if natural else meter
    total_s = total_beats * beat_s
    tail_s = 2.0 * beat_s + 1.8            # room for releases and reverb tails
    n = int((total_s + tail_s) * rate)
    buf = np.zeros((n, 2))
    stats = []
    stems = {}

    for part, seq, length in parts:
        if length <= 0:
            continue
        voice = dict(_voice_defaults(part["voice"]))
        if not part["drums"]:
            declared = doc["voices"].get(part["voice"])
            if declared is None:
                raise ap.WamAudioError(
                    "track %r names voice %r, which the file never declares"
                    % (part["name"], part["voice"]), part["line"])
            voice.update(declared)
        voice["octave"] = voice.get("octave", 0) + part["octave"]
        pan = part["pan"] or voice.get("pan", "center")
        part_buf = np.zeros((n, 2))
        gain = part["level"] * voice["level"]
        prev_freq = None
        # A part shorter than the piece repeats: write one riff, say `bars 8`.
        reps = max(int(math.ceil(total_beats / length)), 1)
        placed = 0
        for rep in range(reps):
            for beat, ev, dur in seq:
                at = beat + rep * length
                if at >= total_beats:
                    continue
                start = _swing(at, song["feel"]) * beat_s
                jitter, vel_jitter = ap.HUMANIZE[part["humanize"]]
                if jitter or vel_jitter:
                    # Seeded on the beat, so a re-render is bit-identical: a
                    # file that compiles differently each time cannot be
                    # reviewed, and cannot be checked.
                    r = synth.rng_for(int(at * 1000) + len(part["name"]) * 7919)
                    start = max(start + float(r.normal(0.0, jitter)), 0.0)
                    swing_vel = 1.0 + float(r.normal(0.0, vel_jitter))
                else:
                    swing_vel = 1.0
                if ev["kind"] == "rest":
                    prev_freq = None
                    continue
                seed = int((at * 97 + rep * 13 + hash(part["name"]) % 1000) % 100000)
                if ev["kind"] == "drum":
                    sig = _drum(ev["piece"], rate, seed)
                    mix_stereo(part_buf, sig, int(start * rate),
                               gain * ev["accent"] * swing_vel * 0.9,
                               DRUM_PAN.get(ev["piece"], pan))
                    placed += 1
                    continue
                if ev["kind"] == "tie":
                    continue
                degrees = _resolve_degrees(ev, song, spans, cycle, at)
                if degrees is None:
                    prev_freq = None
                    continue
                freqs = [midi_to_freq(degree_to_midi(d, song["key"], voice["octave"]))
                         for d in degrees]
                sig = render_note(voice, freqs, dur * beat_s, rate, tones, seed, prev_freq)
                prev_freq = freqs[0]
                mix_stereo(part_buf, sig, int(start * rate),
                           gain * ev["accent"] * swing_vel, pan)
                placed += 1
        if part.get("level_to") is not None:
            # Ramp across the song body, then hold: the tail must not slide.
            body_n = min(int(total_s * rate), n)
            curve = np.ones(n) * part["level_to"] / max(part["level"], 1e-9)
            curve[:body_n] = synth.ramp(1.0,
                                        part["level_to"] / max(part["level"], 1e-9),
                                        body_n, part["curve"])
            part_buf *= curve[:, None]
        part_buf = _apply_space(part_buf, part["echo"] or voice.get("echo"), rate, synth.echo)
        part_buf = _apply_space(part_buf, part["space"] or voice.get("space"), rate, synth.reverb)
        buf += part_buf[:n]
        stems[part["name"]] = part_buf[:n]
        stats.append({"track": part["name"], "voice": part["voice"],
                      "events": placed, "muted": bool(part["mute"]),
                      "soloed": bool(part["solo"]),
                      "peak_db": synth.amp_to_db(float(np.max(np.abs(part_buf)))),
                      "centroid": round(measure(part_buf, rate)["centroid"], 1)})

    buf = _apply_space(buf, song["space"], rate, synth.reverb)[:n]
    body = buf[:int(total_s * rate)].copy()
    if song["loop"]:
        # Wrap the ring-out onto the head so the loop point has no hole in it.
        tail = buf[int(total_s * rate):]
        k = min(len(tail), len(body))
        body[:k] += tail[:k]
    else:
        body = buf[:int((total_s + tail_s) * rate)].copy()
        for ch in range(2):
            body[:, ch] = synth.fade_edges(body[:, ch], rate)
    meta = {"kind": "song", "progression": [s["text"] for s in song["progression"]]
            if song.get("progression") else [],
            "bars": total_beats / meter, "tempo": song["tempo"],
            "key": song["key"]["text"], "meter": meter, "feel": song["feel"],
            "loop": bool(song["loop"]), "beats": total_beats, "tracks": stats,
            "soloed": [t["name"] for t in soloed],
            "muted": [t["name"] for t in song["tracks"] if t["mute"]]}
    # Stems are cut to the same window as the mix so they line up sample for
    # sample; anything else makes an A/B against the mix useless.
    body_n = len(body)
    return body, meta, {k: v[:body_n] for k, v in stems.items()}


def _resolve_degrees(ev, song, spans, cycle, beat):
    """Turn one event into scale degrees, resolving `*` against the harmony."""
    if ev["kind"] in ("note", "chord"):
        return ev["degrees"]
    sym = chord_at(spans, beat, cycle)
    if sym is None:
        raise ap.WamAudioError(
            "a track uses %r but the song declares no progression" % ev["raw"])
    notes = chord_notes(sym, song["key"])
    shift = ev.get("octave", 0)
    if ev["kind"] == "chord_here":
        return [dict(d, octave=d["octave"] + shift) for d in notes]
    idx = ev["tone_index"]
    if idx >= len(notes):
        # Asking for a seventh on a triad: the compiler adds it rather than
        # dropping the note, because a silent note reads as a bug in the file.
        notes = chord_notes(sym, song["key"], seventh=True)
    note = notes[min(idx, len(notes) - 1)]
    return [dict(note, octave=note["octave"] + shift)]


def _swing(beat, feel):
    """Push the offbeat late. Straight leaves every beat exactly where it is."""
    if feel == "straight":
        return beat
    amount = 1.0 / 6.0 if feel == "swing" else 1.0 / 12.0
    frac = beat % 1.0
    if abs(frac - 0.5) < 1e-6:
        return beat + amount
    return beat


def _apply_space(buf, name, rate, fn):
    if name in (None, "", "none"):
        return buf
    out = np.zeros_like(buf)
    for ch in range(buf.shape[1]):
        wet = fn(buf[:, ch], name, rate)
        out[:, ch] = wet[:len(buf)] if len(wet) >= len(buf) else \
            np.concatenate([wet, np.zeros(len(buf) - len(wet))])
    return out


# --------------------------------------------------------------- sound build


NOISY_SOURCES = ("noise", "pink", "band", "breath")


def render_sound(snd, doc, tones, rate):
    base_len = snd["length"]["value"]
    ends = []
    for layer in snd["layers"]:
        ends.append(layer["at"] * base_len + _layer_seconds(layer, base_len)
                    * (layer["repeat"]["count"] if layer["repeat"] else 1))
    total = max(max(ends) if ends else base_len, base_len)
    tail = 2.2 if snd["space"] not in (None, "none") else 0.25
    n = int((total + tail) * rate)
    buf = np.zeros((n, 2))
    stats = []
    for i, layer in enumerate(snd["layers"]):
        sig = _render_layer(layer, base_len, rate, tones, seed=1000 + i * 31)
        start = int(layer["at"] * base_len * rate)
        reps = layer["repeat"]["count"] if layer["repeat"] else 1
        spacing = int((layer["repeat"]["every"] * base_len) * rate) if layer["repeat"] else 0
        decay = layer["repeat"]["decay"] if layer["repeat"] else 1.0
        for r in range(reps):
            mix_stereo(buf, sig, start + r * spacing, decay ** r, "center")
        stats.append({"layer": layer["name"], "source": layer["source"],
                      "at": layer["at"], "repeats": reps,
                      "peak_db": synth.amp_to_db(float(np.max(np.abs(sig))) if len(sig) else 0.0)})
    buf = _apply_space(buf, snd["echo"], rate, synth.echo)[:n]
    buf = _apply_space(buf, snd["space"], rate, synth.reverb)[:n]
    for ch in range(2):
        buf[:, ch] = synth.drive(buf[:, ch], snd["drive"])
        buf[:, ch] = synth.fade_edges(buf[:, ch], rate)
    meta = {"kind": "sound", "length": snd["length"]["text"], "space": snd["space"],
            "base_seconds": base_len, "layers": stats}
    return _trim_silence(buf, rate), meta


def _layer_seconds(layer, base_len):
    spec = layer["length"]
    return spec["value"] * base_len if spec["kind"] == "rel" else spec["value"]


def _render_layer(layer, base_len, rate, tones, seed):
    dur = max(_layer_seconds(layer, base_len), 0.005)
    n = int(dur * rate)
    if n <= 0:
        return np.zeros(0)
    if not layer["source"]:
        raise ap.WamAudioError("layer %r has no source" % layer["name"], layer["line"])
    tone = tone_for(layer["tone"], layer.get("tone_fields"), tones)
    sweeps = {s["target"]: s for s in layer["sweeps"]}
    pitch = layer["pitch"] if layer["pitch"] is not None else ap.REGISTERS["mid"]
    if "pitch" in sweeps:
        s = sweeps["pitch"]
        freq = synth.ramp(s["from"], s["to"], n, s["curve"])
    else:
        freq = float(pitch)
    # A noise source with a centre sweep sweeps its own band; a tone source
    # sweeps a filter over itself. Either way the author just says `center`.
    center = None
    if "center" in sweeps:
        s = sweeps["center"]
        center = synth.ramp(s["from"], s["to"], n, s["curve"])
    if layer["source"] == "band" and center is not None:
        sig = synth.noise("band", n, rate, seed, center=center, q=4.0)
    else:
        sig = synth.render_source(layer["source"], freq, n, rate, tone, seed)
        if center is not None:
            kind = "band" if layer["source"] in NOISY_SOURCES else "low"
            sig = synth.filter_signal(sig, kind, center, rate, q=3.0)
    if layer["cut"]:
        sig = synth.filter_signal(sig, layer["cut"]["kind"], layer["cut"]["freq"],
                                  rate, q=layer["cut"]["q"])
    sig = sig * synth.envelope(layer["env"], n, rate)
    if "level" in sweeps:
        s = sweeps["level"]
        sig = sig * synth.ramp(s["from"], s["to"], n, s["curve"])
    sig = synth.drive(sig, layer["drive"])
    if layer["space"]:
        sig = synth.reverb(sig, layer["space"], rate)
    peak = float(np.max(np.abs(sig))) or 1.0
    return sig / peak * layer["level"]


def _trim_silence(buf, rate, floor_db=-72.0):
    """Cut a tail that has decayed below hearing -- but never the attack."""
    mono = np.max(np.abs(buf), axis=1)
    live = np.where(mono > synth.db_to_amp(floor_db))[0]
    if len(live) == 0:
        return buf
    end = min(len(buf), live[-1] + int(0.02 * rate))
    return buf[:end]


# ------------------------------------------------------------------ metrics


def measure(buf, rate, loop=False):
    """Everything a check can assert on, and everything the sheet prints."""
    mono = buf.mean(axis=1) if buf.ndim > 1 else buf
    n = len(mono)
    peak = float(np.max(np.abs(mono))) if n else 0.0
    rms = float(np.sqrt(np.mean(mono ** 2))) if n else 0.0
    spec = np.abs(np.fft.rfft(mono * np.hanning(n))) if n > 8 else np.zeros(1)
    freqs = np.fft.rfftfreq(max(n, 1), 1.0 / rate)
    # Weight by power, not magnitude. Most FFT bins are high-frequency, so a
    # magnitude-weighted centroid reads a -60 dB hiss floor as brightness: a
    # 440 Hz sine with inaudible noise on it measures 1.5 kHz that way.
    power = spec * spec
    total = float(np.sum(power)) or 1.0
    centroid = float(np.sum(freqs[:len(power)] * power) / total)
    m = {
        "duration": n / float(rate),
        "peak": synth.amp_to_db(peak),
        "rms": synth.amp_to_db(rms),
        "crest": synth.amp_to_db(peak) - synth.amp_to_db(rms) if rms else 0.0,
        "centroid": centroid,
        "dc": float(np.mean(mono)) if n else 0.0,
        # Share of power above 10 kHz. Isolated sine partials up there are not
        # brightness, they are a whistle -- and they are invisible in every
        # other metric, because they carry almost no energy while being the
        # most audible thing in the mix.
        "air": float(100.0 * np.sum(power[freqs[:len(power)] > 10000.0]) / total),
        # How far the loudest bin above 8 kHz stands above its neighbours. Noise
        # sits ~10 dB over the local median; a pure tone towers over it. This is
        # the measurement that catches a whistle carrying no real energy.
        "whistle": whistle_db(mono, rate),
        "onsets": int(len(find_onsets(mono, rate))),
        "clipped": int(np.sum(np.abs(mono) >= 0.999)),
    }
    if loop and n > 2:
        # A seam only pops when the step across it is bigger than the steps the
        # waveform is making anyway. Measuring it against the typical
        # sample-to-sample delta is what makes the number mean something.
        edge = abs(float(mono[0] - mono[-1]))
        typical = float(np.mean(np.abs(np.diff(mono)))) or 1e-9
        m["loop_pop"] = synth.amp_to_db(edge) - synth.amp_to_db(typical)
    return m


def whistle_db(mono, rate, floor_hz=8000.0):
    """Peak-to-median of the top band, on a frame-averaged spectrum.

    A single long FFT is too spiky to compare a peak against its neighbours,
    so the spectrum is averaged over frames first: that flattens noise while
    leaving a steady tone standing exactly where it is.
    """
    win, hop = 2048, 1024
    n = len(mono)
    if n < win * 2:
        return 0.0
    frames = 1 + (n - win) // hop
    idx = np.arange(win)[None, :] + hop * np.arange(frames)[:, None]
    mag = np.abs(np.fft.rfft(mono[idx] * np.hanning(win), axis=1)).mean(axis=0)
    freqs = np.fft.rfftfreq(win, 1.0 / rate)
    band = mag[(freqs > floor_hz) & (freqs < rate * 0.47)]
    if len(band) < 8:
        return 0.0
    med = float(np.median(band))
    peak = float(np.max(band))
    full = float(np.max(mag))
    if med <= 1e-12 or full <= 1e-12:
        return 0.0
    # Above 8 kHz the floor is often digital silence, so a partial 100 dB down
    # still towers over its neighbours. Only a peak loud enough to be heard at
    # all counts: below 40 dB under the signal's own peak, nothing is a whistle.
    if 20.0 * np.log10(peak / full) < -40.0:
        return 0.0
    return float(20.0 * np.log10(peak / med))


def envelope_curve(mono, rate, hop=512):
    n = len(mono)
    frames = max(int(math.ceil(n / float(hop))), 1)
    pad = np.concatenate([mono, np.zeros(frames * hop - n)])
    return np.sqrt(np.mean(pad.reshape(frames, hop) ** 2, axis=1))


def _flux_window(n):
    """Short sounds need a short window, or they analyse as one frame of hiss.

    A UI click is 30 ms; a 1024-sample window is 23 ms of that, so the fixed
    window used for music reports no onsets at all for the sounds that are
    nothing but an onset.
    """
    win = 1024
    while win > 64 and n < win * 4:
        win //= 2
    return win, max(win // 4, 16)


def spectral_flux(mono, rate, win=None, hop=None):
    """Positive spectral change per frame -- the standard onset novelty curve.

    Energy alone misses a hit that lands inside a sustaining chord; flux sees
    it, because new partials appear even when the level does not rise.
    """
    n = len(mono)
    if win is None:
        win, hop = _flux_window(n)
    if n < win * 2:
        return np.zeros(0), hop
    frames = 1 + (n - win) // hop
    w = np.hanning(win)
    idx = np.arange(win)[None, :] + hop * np.arange(frames)[:, None]
    mag = np.abs(np.fft.rfft(mono[idx] * w, axis=1))
    diff = np.diff(mag, axis=0)
    flux = np.sum(np.maximum(diff, 0.0), axis=1)
    # The first frame is a rise out of silence, not a non-event: a sound that
    # starts at sample zero has its loudest onset there and would go uncounted.
    return np.concatenate([[float(np.sum(mag[0]))], flux]), hop


def find_onsets(mono, rate, min_gap=0.045):
    """Peak-pick the flux curve against a local median floor."""
    win, _ = _flux_window(len(mono))
    flux, hop = spectral_flux(mono, rate)
    min_gap = min(min_gap, win / float(rate))
    if len(flux) < 3:
        return []
    peak = float(np.max(flux)) or 1.0
    flux = flux / peak
    half = 5
    onsets = []
    last = -1e9
    for i in range(len(flux) - 1):
        lo, hi = max(0, i - half), min(len(flux), i + half + 1)
        floor = float(np.median(flux[lo:hi])) * 1.7 + 0.08
        # A frame is a window, so report its centre: the transient is inside it,
        # not at its leading edge.
        t = (i * hop + win * 0.5) / float(rate)
        rising = i == 0 or flux[i] >= flux[i - 1]
        if flux[i] > floor and rising and flux[i] >= flux[i + 1] and t - last > min_gap:
            onsets.append(t)
            last = t
    return onsets


UNIT_SCALE = {"db": 1.0, "dbfs": 1.0, "hz": 1.0, "khz": 1000.0, "s": 1.0,
              "sec": 1.0, "ms": 0.001, "": 1.0}

METRIC_ALIASES = {"length": "duration", "loop": "loop_pop", "bright": "centroid"}


def run_checks(checks, metrics):
    """Evaluate a piece's `assert` lines. `~` means within 10%."""
    results = []
    for chk in checks:
        name = METRIC_ALIASES.get(chk["metric"], chk["metric"])
        if name not in metrics:
            results.append({"text": chk["text"], "ok": False, "line": chk.get("line"),
                            "detail": "unknown metric %r (have: %s)"
                                      % (chk["metric"], ", ".join(sorted(metrics)))})
            continue
        got = float(metrics[name])
        want = chk["value"] * UNIT_SCALE.get(chk["unit"].lower(), 1.0)
        op = chk["op"]
        if op == "<":
            ok = got < want
        elif op == "<=":
            ok = got <= want
        elif op == ">":
            ok = got > want
        elif op == ">=":
            ok = got >= want
        elif op == "==":
            ok = abs(got - want) < 1e-6 or int(round(got)) == int(round(want))
        else:                                   # ~
            ok = abs(got - want) <= max(abs(want) * 0.1, 1e-6)
        results.append({"text": chk["text"], "ok": bool(ok), "line": chk.get("line"),
                        "detail": "%s = %.3f" % (name, got)})
    return results


# --------------------------------------------------------------------- lint

def lint(buf, rate, metrics, meta):
    """Warnings for the things that are easy to author and hard to hear."""
    warn = []
    if metrics["clipped"] > 0:
        warn.append("%d samples hit full scale; the master limiter is working hard"
                    % metrics["clipped"])
    if metrics["peak"] < -20.0:
        warn.append("peak is %.1f dBFS -- this piece is nearly silent" % metrics["peak"])
    if abs(metrics["dc"]) > 0.01:
        warn.append("DC offset %.3f: a source is not centred, which eats headroom"
                    % metrics["dc"])
    if metrics.get("loop_pop", -99) > 14.0:
        warn.append("loop seam steps %.1f dB above the waveform's own motion; "
                    "the repeat will click" % metrics["loop_pop"])
    # White noise measures ~11 kHz on this scale and a saw about 600 Hz. A
    # music mix living above 5 kHz is hiss-dominated and wrong; a one-shot is
    # allowed to be a bright tick or a coin, so it is only worth a warning when
    # it is close to being pure noise.
    # Discrete partials above 10 kHz are a whistle, not brightness. Hats and
    # ticks are legitimately airy, so a one-shot gets a lot more rope.
    if metrics.get("whistle", 0.0) > 26.0:
        warn.append("a tone above 8 kHz stands %.0f dB over its neighbours; "
                    "that is a whistle, not brightness" % metrics["whistle"])
    air_limit = 12.0 if meta["kind"] == "song" else 45.0
    if metrics.get("air", 0.0) > air_limit:
        warn.append("%.0f%% of the power is above 10 kHz; check for tweeter tones"
                    % metrics["air"])
    bright_limit = 5000.0 if meta["kind"] == "song" else 9000.0
    if metrics["centroid"] > bright_limit:
        warn.append("spectral centroid %.0f Hz: this is dominated by noise or "
                    "top end" % metrics["centroid"])
    if metrics["crest"] > 30.0:
        warn.append("crest factor %.1f dB: one transient towers over everything else"
                    % metrics["crest"])
    for stat in meta.get("tracks", []) + meta.get("layers", []):
        if stat["peak_db"] < -50.0:
            warn.append("%r contributes nothing audible (%.1f dBFS)"
                        % (stat.get("track") or stat.get("layer"), stat["peak_db"]))
    if meta.get("soloed"):
        warn.append("solo is on (%s): this render is not the finished mix"
                    % ", ".join(meta["soloed"]))
    if meta.get("muted"):
        warn.append("muted: %s" % ", ".join(meta["muted"]))
    return warn


# ---------------------------------------------------------------------- I/O


def write_wav(path, buf, rate):
    """16-bit stereo PCM. Dithered, because 16 bits of a quiet tail is grainy."""
    data = np.clip(buf, -1.0, 1.0)
    if data.ndim == 1:
        data = np.stack([data, data], axis=1)
    dither = (np.random.default_rng(5).random(data.shape) - 0.5) / 32768.0
    ints = np.clip((data + dither) * 32767.0, -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as fh:
        fh.setnchannels(2)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(ints.tobytes())
    return path


# ------------------------------------------------------------------ compile


def compile_document(doc, only=None):
    """Render every song and sound in a parsed document."""
    rate = doc["rate"]
    tones = build_tone_table(doc)
    pieces = []
    for song in doc["songs"]:
        if only and song["name"] not in only:
            continue
        buf, meta, stems = render_song(song, doc, tones, rate)
        pieces.append(_finish(song, buf, meta, doc, rate, loop=song["loop"], stems=stems))
    for snd in doc["sounds"]:
        if only and snd["name"] not in only:
            continue
        buf, meta = render_sound(snd, doc, tones, rate)
        pieces.append(_finish(snd, buf, meta, doc, rate, loop=False))
    if only:
        missing = set(only) - {p["name"] for p in pieces}
        if missing:
            raise ap.WamAudioError("no song or sound named %s"
                                   % ", ".join(sorted(missing)))
    return pieces


def _finish(spec, buf, meta, doc, rate, loop, stems=None):
    peak = float(np.max(np.abs(buf))) or 1.0
    buf = synth.normalize(buf, doc["master"])
    # Stems get the mix's own gain change, so a soloed stem sits exactly where
    # it sat inside the mix instead of being re-normalised into a lie.
    gain = (float(np.max(np.abs(buf))) or 1.0) / peak
    metrics = measure(buf, rate, loop=loop)
    return {"name": spec["name"], "kind": meta["kind"], "buf": buf, "rate": rate,
            "stems": {k: v * gain for k, v in (stems or {}).items()},
            "meta": meta, "metrics": metrics,
            "checks": run_checks(spec["checks"], metrics),
            "warnings": lint(buf, rate, metrics, meta)}
