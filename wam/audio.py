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
import zlib

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


def midi_to_freq(m):
    return 440.0 * 2.0 ** ((m - 69) / 12.0)


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


# -------------------------------------------------------------- instruments

# Envelopes that stop when the note stops, versus ones that ring past it. A
# plucked eighth note that gets cut dead at the eighth sounds like a mute, so
# decay-type instruments are allowed to spill into the notes that follow.
SUSTAIN_ENVS = ("pad", "sustain", "swell", "gate", "bow", "stab")

GLIDE_TIMES = {"none": 0.0, "short": 0.04, "long": 0.14}


def _instrument_defaults(name):
    return {"name": name, "source": "saw", "tone": "plain", "env": "pluck",
            "level": 1.0, "octave": 0, "detune": 0.0, "space": "none",
            "echo": "none", "drive": "none", "pan": "center", "damp": 0.5,
            "glide": "none", "cut": None, "tone_fields": {}}


def render_note(voice, freqs, dur_s, rate, tones, seed, prev_freq=None):
    """One note (or chord) of one instrument, as a mono buffer.

    `dur_s` is the written value; the returned buffer may be longer when the
    envelope rings out past it. Callers mix it in at the note's start.
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
        out = synth.filter_signal(out, "low",
                                  ap.parse_freq(voice["cut"], voice.get("line"), ""),
                                  rate)
    out = synth.drive(out, voice.get("drive", "none"))
    return out


# -------------------------------------------------------------------- drums

# The kit is built from the same archetypes an author gets, so a piece can be
# reasoned about and, if it ever needs to be, replaced by a written-out staff.
def _drum(piece, rate, seed):
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
        # A cymbal is metal that also hisses, not hiss with a little metal in
        # it. Highpassing white noise at 6.5 kHz left nothing at all below
        # 2 kHz, so the thing had no body: all air, which is exactly what
        # "hissy" describes. The modes lead now and the wash supports them.
        x = synth.filter_signal(synth.noise("white", n, rate, seed + 2),
                                "high", 2800.0, rate)
        metal = synth.metal(2600.0, n, rate, "bright", seed + 3)
        return (metal + 0.45 * x) * synth.envelope("hit", n, rate)
    if piece in ("tom", "lowtom"):
        n = int(0.34 * rate)
        base = 150.0 if piece == "tom" else 95.0
        return synth.thump(base, n, rate, "plain", seed, drop=2.2) * synth.envelope("hit", n, rate)
    if piece == "crash":
        n = int(1.4 * rate)
        x = synth.filter_signal(synth.noise("white", n, rate, seed + 4),
                                "high", 1500.0, rate)
        return (synth.metal(1900.0, n, rate, "bright", seed + 5) + 0.7 * x) * \
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


# ---------------------------------------------------------------- song build


def flatten_voice(bars, bar_beats):
    """Bars of events -> (start_beat, event), with ties joined.

    A tie is written on the note that starts it and can cross a bar line, so
    joining has to happen after the bars have been read and checked -- which
    is also why bar checking counts the written value rather than the sounding
    one.
    """
    out = []
    beat = 0.0
    pending = None
    for bar_index, events in enumerate(bars):
        for ev in events:
            if pending is not None:
                same = (ev.get("kind") == pending["kind"]
                        and ev.get("midis") == pending["midis"]
                        and ev.get("piece") == pending.get("piece"))
                if same:
                    pending["beats"] += ev["beats"]
                    beat += ev["beats"]
                    if not ev.get("tie"):
                        out.append((pending["start"], pending))
                        pending = None
                    continue
                # A tie into a different note is a slur at best; sound the
                # tied note and carry on rather than silently swallowing it.
                out.append((pending["start"], pending))
                pending = None
            if ev["kind"] == "dynamic":
                out.append((beat, dict(ev, start=beat)))
                continue
            item = dict(ev)
            item["start"] = beat
            beat += ev["beats"]
            if ev.get("tie") and ev["kind"] in ("note", "chord"):
                pending = item
            else:
                out.append((item["start"], item))
    if pending is not None:
        out.append((pending["start"], pending))
    return out, beat


def render_song(song, doc, tones, rate):
    meter = song["meter"]
    beat_s = 60.0 / song["tempo"]
    # Solo wins over mute, and any solo silences everything not soloed -- the
    # same rule as every mixer.
    soloed = [st for st in song["staves"] if st["solo"]]
    live = soloed if soloed else [st for st in song["staves"] if not st["mute"]]

    lengths = []
    for staff in song["staves"]:
        for voice in staff["voices"]:
            lengths.append(len(voice["bars"]))
    written_bars = max(lengths) if lengths else 0
    if song["bars"] is not None:
        for staff in song["staves"]:
            for voice in staff["voices"]:
                if len(voice["bars"]) > song["bars"]:
                    raise ap.WamAudioError(
                        "voice %r in staff %r writes %d bars, but the song "
                        "declares %d"
                        % (voice["name"], staff["name"], len(voice["bars"]),
                           song["bars"]), voice["line"])
        total_bars = song["bars"]
    else:
        total_bars = written_bars
    total_beats = total_bars * meter["beats"]
    total_s = total_beats * beat_s
    tail_s = 2.0 * beat_s + 1.8            # room for releases and reverb tails
    n = int((total_s + tail_s) * rate)
    buf = np.zeros((n, 2))
    stats = []
    stems = {}

    for staff in live:
        if staff["drums"]:
            instrument = dict(_instrument_defaults("kit"))
        else:
            declared = doc["instruments"].get(staff["instrument"])
            if declared is None:
                raise ap.WamAudioError(
                    "staff %r names instrument %r, which the file never declares"
                    % (staff["name"], staff["instrument"]), staff["line"])
            instrument = dict(_instrument_defaults(staff["instrument"]))
            instrument.update(declared)
        staff_buf = np.zeros((n, 2))
        placed = 0
        for voice in staff["voices"]:
            events, _ = flatten_voice(voice["bars"], meter["beats"])
            octave = instrument.get("octave", 0) + staff["octave"] + voice["octave"]
            pan = voice["pan"] or staff["pan"] or instrument.get("pan", "center")
            gain = staff["level"] * voice["level"] * instrument["level"]
            prev_freq = None
            dynamic = 1.0
            for at, ev in events:
                if at >= total_beats:
                    continue
                start = _swing(at, song["feel"]) * beat_s
                jitter, vel_jitter, cents = ap.HUMANIZE[staff["humanize"]]
                drift = 1.0
                if jitter or vel_jitter or cents:
                    # Seeded on the beat, so a re-render is bit-identical: a
                    # file that compiles differently each time cannot be
                    # reviewed, and cannot be checked.
                    r = synth.rng_for(int(at * 1000) + len(voice["name"]) * 7919)
                    start = max(start + float(r.normal(0.0, jitter)), 0.0)
                    swing_vel = 1.0 + float(r.normal(0.0, vel_jitter))
                    drift = 2.0 ** (float(r.normal(0.0, cents)) / 1200.0)
                else:
                    swing_vel = 1.0
                if ev["kind"] == "dynamic":
                    dynamic = ev["gain"]
                    continue
                if ev["kind"] == "rest":
                    prev_freq = None
                    continue
                # crc32, not hash(): Python randomises string hashing per
                # process, so `hash(name)` seeded every noise source
                # differently on every run. The same file rendered differently
                # each time it was compiled -- which cannot be reviewed, cannot
                # be checked, and quietly changed an album on every rebuild.
                seed = int((at * 97 + zlib.crc32(voice["name"].encode()) % 1000)
                           % 100000)
                if ev["kind"] == "drum":
                    sig = _drum(ev["piece"], rate, seed)
                    mix_stereo(staff_buf, sig, int(start * rate),
                               gain * dynamic * ev["accent"] * swing_vel * 0.9,
                               DRUM_PAN.get(ev["piece"], pan))
                    placed += 1
                    continue
                freqs = [midi_to_freq(m + 12 * octave) * drift for m in ev["midis"]]
                sig = render_note(instrument, freqs, ev["beats"] * beat_s, rate,
                                  tones, seed, prev_freq)
                prev_freq = freqs[0]
                mix_stereo(staff_buf, sig, int(start * rate),
                           gain * dynamic * ev["accent"] * swing_vel, pan)
                placed += 1
        if staff.get("level_to") is not None:
            # Ramp across the song body, then hold: the tail must not slide.
            body_n = min(int(total_s * rate), n)
            curve = np.ones(n) * staff["level_to"] / max(staff["level"], 1e-9)
            curve[:body_n] = synth.ramp(
                1.0, staff["level_to"] / max(staff["level"], 1e-9), body_n,
                staff["curve"])
            staff_buf *= curve[:, None]
        staff_buf = _apply_space(staff_buf, staff["echo"] or instrument.get("echo"),
                                 rate, synth.echo)
        staff_buf = _apply_space(staff_buf, staff["space"] or instrument.get("space"),
                                 rate, synth.reverb)
        buf += staff_buf[:n]
        stems[staff["name"]] = staff_buf[:n]
        stats.append({"staff": staff["name"], "instrument": staff["instrument"],
                      "voices": len(staff["voices"]), "events": placed,
                      "muted": bool(staff["mute"]), "soloed": bool(staff["solo"]),
                      "peak_db": synth.amp_to_db(float(np.max(np.abs(staff_buf)))),
                      "centroid": round(measure(staff_buf, rate)["centroid"], 1)})

    buf = _apply_space(buf, song["space"], rate, synth.reverb)[:n]
    body_n = int(total_s * rate)
    once = buf.copy()
    for ch in range(2):
        once[:, ch] = synth.fade_edges(once[:, ch], rate)
    if song["loop"]:
        # Wrap the ring-out onto the head so the loop point has no hole in it.
        # That is right for a loop and wrong for listening: the ending is gone
        # from the end, which sounds exactly like the piece being cut off. So
        # keep the un-wrapped render too, and let the caller hand over both.
        body = buf[:body_n].copy()
        tail = buf[body_n:]
        k = min(len(tail), len(body))
        body[:k] += tail[:k]
    else:
        body = once
        once = None
    meta = {"kind": "song", "bars": total_bars, "tempo": song["tempo"],
            "key": song["key"]["text"], "meter": meter["text"],
            "beats_per_bar": meter["beats"], "feel": song["feel"],
            "loop": bool(song["loop"]), "beats": total_beats, "staves": stats,
            "soloed": [st["name"] for st in soloed],
            "muted": [st["name"] for st in song["staves"] if st["mute"]]}
    cut = len(body)
    return body, meta, {k: v[:cut] for k, v in stems.items()}, once


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
    for stat in meta.get("staves", []) + meta.get("layers", []):
        if stat["peak_db"] < -50.0:
            warn.append("%r contributes nothing audible (%.1f dBFS)"
                        % (stat.get("staff") or stat.get("layer"), stat["peak_db"]))
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
        buf, meta, stems, once = render_song(song, doc, tones, rate)
        pieces.append(_finish(song, buf, meta, doc, rate, loop=song["loop"],
                              stems=stems, once=once))
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


def _finish(spec, buf, meta, doc, rate, loop, stems=None, once=None):
    peak = float(np.max(np.abs(buf))) or 1.0
    buf = synth.normalize(buf, doc["master"])
    # Stems get the mix's own gain change, so a soloed stem sits exactly where
    # it sat inside the mix instead of being re-normalised into a lie.
    gain = (float(np.max(np.abs(buf))) or 1.0) / peak
    metrics = measure(buf, rate, loop=loop)
    return {"name": spec["name"], "kind": meta["kind"], "buf": buf, "rate": rate,
            # The same gain, so the two renders are the same mix.
            "once": None if once is None else once * gain,
            "stems": {k: v * gain for k, v in (stems or {}).items()},
            "meta": meta, "metrics": metrics,
            "checks": run_checks(spec["checks"], metrics),
            "warnings": lint(buf, rate, metrics, meta)}
