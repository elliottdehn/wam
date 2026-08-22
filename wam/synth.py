"""WAM audio synthesis engine: named archetypes in, samples out.

The audio language mirrors WAM's rule for models -- the author only makes
discrete, named, relative decisions (a source archetype, a tone adjective, an
envelope name, a scale degree, a percentage) and the compiler generates every
sample, every anti-aliased harmonic, every click-free fade.

Everything here is numpy-only. Two deliberate implementation choices:

* Oscillators are additive and drop harmonics above Nyquist per sample, so a
  sweep can cross the whole spectrum without aliasing back down as grit.
* Filters come in two machines. A *static* cutoff is an FIR (windowed sinc),
  which is exact and transient-clean. A *swept* cutoff is an STFT gain mask,
  because a time-varying IIR would need a per-sample Python loop. The STFT
  smears transients by half a window, so the window is kept short (512).
"""
import math

import numpy as np

SR = 44100

# ---------------------------------------------------------------- utilities


def db_to_amp(db):
    return 10.0 ** (db / 20.0)


def amp_to_db(a):
    return -120.0 if a <= 1e-6 else 20.0 * math.log10(a)


def rng_for(seed):
    """Deterministic noise: the same source in the same slot sounds the same."""
    return np.random.default_rng(int(seed) & 0x7FFFFFFF)


def _as_curve(v, n):
    """Accept a scalar or an array and always hand back n samples."""
    arr = np.asarray(v, dtype=float)
    if arr.ndim == 0:
        return np.full(n, float(arr))
    if len(arr) == n:
        return arr
    # Resample a control curve to audio rate.
    return np.interp(np.linspace(0.0, 1.0, n), np.linspace(0.0, 1.0, len(arr)), arr)


def ramp(start, end, n, curve="linear"):
    """Named interpolation shapes -- the author picks a word, not an exponent."""
    t = np.linspace(0.0, 1.0, max(n, 1))
    if curve == "ease":          # slow in, slow out
        t = t * t * (3.0 - 2.0 * t)
    elif curve == "fast":        # most of the move happens early
        t = 1.0 - (1.0 - t) ** 3
    elif curve == "slow":        # most of the move happens late
        t = t ** 3
    elif curve == "snap":        # jump most of the way, then settle
        t = np.clip(t * 6.0, 0.0, 1.0)
    elif curve == "drop":        # exponential fall, the classic pitch-drop
        t = 1.0 - np.exp(-5.0 * t)
        t /= t[-1] if t[-1] else 1.0
    return start + (end - start) * t


# ---------------------------------------------------------------- envelopes

# (attack, decay, sustain, release) -- times as a fraction of note length,
# with an absolute floor in seconds so short notes still get a real attack.
ENVELOPES = {
    "pluck":   dict(a=0.002, d=0.45, s=0.00, r=0.10, a_abs=0.002),
    "hit":     dict(a=0.000, d=0.30, s=0.00, r=0.05, a_abs=0.0006),
    "stab":    dict(a=0.010, d=0.18, s=0.45, r=0.20, a_abs=0.004),
    "pad":     dict(a=0.250, d=0.15, s=0.85, r=0.35, a_abs=0.02),
    "swell":   dict(a=0.600, d=0.05, s=1.00, r=0.40, a_abs=0.03),
    "sustain": dict(a=0.020, d=0.10, s=0.90, r=0.10, a_abs=0.006),
    "gate":    dict(a=0.001, d=0.00, s=1.00, r=0.02, a_abs=0.001),
    "bloom":   dict(a=0.080, d=0.22, s=0.55, r=0.45, a_abs=0.01),
    "bow":     dict(a=0.120, d=0.10, s=0.90, r=0.25, a_abs=0.015),
}


def envelope(name, n, sr=SR):
    """ADSR over exactly n samples. Always ends at zero: no clicks, ever."""
    p = ENVELOPES.get(name) or ENVELOPES["pluck"]
    dur = n / float(sr)
    a = max(p["a"] * dur, p["a_abs"])
    d = p["d"] * dur
    r = max(p["r"] * dur, 0.004)
    # Attack and release are inviolable; decay yields first if the note is short.
    if a + r > dur * 0.98:
        scale = dur * 0.98 / (a + r)
        a, r = a * scale, r * scale
    d = min(d, max(dur - a - r, 0.0))
    na, nd, nr = int(a * sr), int(d * sr), int(r * sr)
    ns = max(n - na - nd - nr, 0)
    s = p["s"]
    segs = [
        np.linspace(0.0, 1.0, na) ** 0.6 if na else np.zeros(0),
        (1.0 - (1.0 - s) * (1.0 - np.exp(-4.0 * np.linspace(0, 1, nd)))) if nd else np.zeros(0),
        np.full(ns, s) if ns else np.zeros(0),
    ]
    body = np.concatenate(segs) if segs else np.zeros(0)
    tail_from = body[-1] if len(body) else 1.0
    tail = tail_from * np.exp(-5.0 * np.linspace(0, 1, nr)) if nr else np.zeros(0)
    if nr:
        tail = tail * np.linspace(1.0, 0.0, nr)   # force a true zero at the end
    env = np.concatenate([body, tail])
    if len(env) < n:
        env = np.concatenate([env, np.zeros(n - len(env))])
    return env[:n]


# -------------------------------------------------------------- oscillators

# Tone adjectives shape the harmonic series. A source archetype says *what*
# the thing is; the adjective says how bright and how thick it is.
#
# These are defaults, not a closed set. A file can redefine any of them or
# derive new ones in its `tones` section, and any voice or layer can override a
# single field inline -- the same way a `.wam` palette colour is a name you
# choose but a value you own.
TONE_FIELDS = ("tilt", "harm", "noise", "odd_only", "thin", "detune")

TONES = {
    "plain":  dict(tilt=1.0, harm=1.0, noise=0.0),
    "bright": dict(tilt=0.7, harm=1.6, noise=0.02),
    "warm":   dict(tilt=1.5, harm=0.7, noise=0.0),
    "dark":   dict(tilt=2.2, harm=0.45, noise=0.0),
    "soft":   dict(tilt=1.8, harm=0.6, noise=0.0),
    "harsh":  dict(tilt=0.5, harm=1.9, noise=0.08),
    "hollow": dict(tilt=1.0, harm=1.0, noise=0.0, odd_only=True),
    "thin":   dict(tilt=1.2, harm=0.8, noise=0.0, thin=True),
    "fat":    dict(tilt=1.1, harm=1.2, noise=0.0, detune=8.0),
}

MAX_HARMONICS = 48

# Where struck-metal models stop putting *discrete* partials. Real metal has
# plenty of energy above this, but packed too densely to hear as pitches, so
# above the line it is synthesised as noise. A lone sine up here is the one
# artefact that no energy-based measurement catches and every listener hears.
PARTIAL_TOP_HZ = 8000.0
BELL_TOP_HZ = 8000.0


def tone_params(tone, table=None):
    """Accept a tone name, an already-resolved dict, or None.

    Every archetype in this module calls this instead of indexing TONES, so an
    overridden or file-defined tone reaches the oscillators unchanged.
    """
    if isinstance(tone, dict):
        base = dict(TONES["plain"])
        base.update(tone)
        return base
    lookup = table or TONES
    got = lookup.get(tone)
    if got is None:
        got = TONES.get(tone, TONES["plain"])
    base = dict(TONES["plain"])
    base.update(got)
    return base


def resolve_tone(name, overrides=None, table=None):
    """Build a tone dict: built-in or file-defined base, then explicit fields."""
    params = tone_params(name, table)
    for k, v in (overrides or {}).items():
        if v is None:
            continue
        if k not in TONE_FIELDS:
            raise KeyError("unknown tone field %r (have: %s)"
                           % (k, ", ".join(TONE_FIELDS)))
        params[k] = v
    return params


def _harmonic_weights(kind, tone):
    """Relative amplitude of harmonic k for each waveform archetype."""
    k = np.arange(1, MAX_HARMONICS + 1, dtype=float)
    if kind == "sine":
        w = np.zeros_like(k); w[0] = 1.0
    elif kind == "tri":
        w = np.where(k % 2 == 1, 1.0 / (k * k), 0.0)
        w[2::4] *= -1.0            # triangle alternates sign on odd harmonics
    elif kind == "square":
        w = np.where(k % 2 == 1, 1.0 / k, 0.0)
    elif kind == "pulse":
        w = np.sin(k * math.pi * 0.25) / k        # 25% duty cycle
    else:                          # saw
        w = 1.0 / k
    t = tone_params(tone)
    w = w * (k ** -(float(t["tilt"]) - 1.0))             # adjective tilts the series
    if t.get("odd_only"):
        w = np.where(k % 2 == 1, w, 0.0)
    if t.get("thin"):
        w[3:] *= 0.3
    cut = max(int(round(MAX_HARMONICS * min(t["harm"], 1.0))), 1)
    if t["harm"] < 1.0:
        w[cut:] = 0.0
    return w


def _phase(freq, n, sr):
    f = _as_curve(freq, n)
    return np.cumsum(2.0 * math.pi * f / sr), f


def osc(kind, freq, n, sr=SR, tone="plain", seed=0):
    """Additive oscillator. Harmonics above Nyquist are muted, not folded."""
    if n <= 0:
        return np.zeros(0)
    ph, f = _phase(freq, n, sr)
    w = _harmonic_weights(kind, tone)
    t = tone_params(tone)
    nyq = sr * 0.5
    out = np.zeros(n)
    for i, amp in enumerate(w):
        if amp == 0.0:
            continue
        k = i + 1.0
        alive = (f * k) < nyq * 0.98
        if not alive.any():
            break
        out += amp * np.sin(ph * k) * alive
    if t.get("detune"):
        # A second voice a few cents away, for width without a chorus unit.
        ph2 = ph * (1.0 + t["detune"] / 1200.0 * 0.058)
        out = 0.6 * out + 0.5 * np.sin(ph2)
    if t["noise"]:
        out += t["noise"] * rng_for(seed + 7).standard_normal(n)
    peak = np.max(np.abs(out)) or 1.0
    return out / peak


def noise(color, n, sr=SR, seed=0, center=None, q=6.0):
    """white | pink | band -- band needs a centre frequency (scalar or curve)."""
    if n <= 0:
        return np.zeros(0)
    x = rng_for(seed).standard_normal(n)
    if color == "pink":
        # Pink is a spectral statement, so make it in the spectrum: shape white
        # noise by 1/sqrt(f) for an exact -3 dB/octave tilt with no filter ring.
        spec = np.fft.rfft(x)
        f = np.fft.rfftfreq(n, 1.0 / sr)
        f[0] = f[1] if len(f) > 1 else 1.0
        spec = spec / np.sqrt(f)
        spec[0] = 0.0          # 1/sqrt(f) sends the DC bin to infinity
        x = np.fft.irfft(spec, n)
    elif color == "band":
        c = center if center is not None else 1000.0
        x = filter_signal(x, "band", c, sr, q=q)
    peak = np.max(np.abs(x)) or 1.0
    return x / peak


def _one_pole_lowpass(x, a):
    """Constant-coefficient one-pole, evaluated in blocks so it stays vectorised.

    y[n] = (1-a)*x[n] + a*y[n-1] has a closed form inside a block:
    y = a^i * (y_prev + cumsum((1-a) * x * a^-i)). a^-i overflows for long
    blocks, so the block length is capped where a^-i stays well inside float64.
    """
    a = float(np.clip(a, 0.0, 0.9999))
    if a <= 0.0:
        return x.copy()
    # a**-i is the growing term; keep it under ~1e250 so it never overflows.
    block = max(min(4096, int(250.0 / -math.log10(a))), 16)
    out = np.empty_like(x)
    prev = 0.0
    for start in range(0, len(x), block):
        seg = x[start:start + block]
        i = np.arange(len(seg))
        pw = a ** i
        acc = np.cumsum((1.0 - a) * seg / pw)
        y = pw * (prev + acc)
        out[start:start + len(seg)] = y
        prev = y[-1]
    return out


# ------------------------------------------------------------------ filters


def _sinc_kernel(kind, cutoff, sr, taps=257, q=4.0):
    """Windowed-sinc FIR for a fixed cutoff. Blackman window, linear phase."""
    n = np.arange(taps) - (taps - 1) / 2.0
    if kind == "band":
        lo = max(cutoff / (1.0 + 1.0 / q), 20.0)
        hi = min(cutoff * (1.0 + 1.0 / q), sr * 0.49)
        k = 2 * hi / sr * np.sinc(2 * hi * n / sr) - 2 * lo / sr * np.sinc(2 * lo * n / sr)
    else:
        fc = float(np.clip(cutoff, 20.0, sr * 0.49))
        k = 2 * fc / sr * np.sinc(2 * fc * n / sr)
        if kind == "high":
            hp = np.zeros(taps); hp[(taps - 1) // 2] = 1.0
            k = hp - k
    k *= np.blackman(taps)
    s = np.sum(np.abs(k)) or 1.0
    return k / (np.sum(k) if kind == "low" and np.sum(k) else s)


def _fftconv(x, k):
    n = len(x) + len(k) - 1
    size = 1 << (n - 1).bit_length()
    y = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(k, size), size)[:n]
    lead = (len(k) - 1) // 2
    return y[lead:lead + len(x)]          # compensate the linear-phase delay


def filter_signal(x, kind, cutoff, sr=SR, q=4.0):
    """Static cutoff -> FIR. Swept cutoff (an array) -> STFT gain mask."""
    if len(x) == 0 or kind in (None, "none"):
        return x
    arr = np.asarray(cutoff, dtype=float)
    if arr.ndim == 0:
        return _fftconv(x, _sinc_kernel(kind, float(arr), sr, q=q))
    return _swept_filter(x, kind, _as_curve(arr, len(x)), sr, q)


def _swept_filter(x, kind, cut, sr, q, win=512, hop=128):
    """Time-varying filter as an overlap-add STFT mask."""
    n = len(x)
    pad = win
    xp = np.concatenate([np.zeros(pad), x, np.zeros(win * 2)])
    w = np.hanning(win + 1)[:win]
    frames = range(0, len(xp) - win, hop)
    out = np.zeros(len(xp))
    norm = np.zeros(len(xp))
    freqs = np.fft.rfftfreq(win, 1.0 / sr)
    for start in frames:
        idx = min(max(start - pad, 0), n - 1)
        fc = float(np.clip(cut[idx], 20.0, sr * 0.49))
        with np.errstate(divide="ignore", invalid="ignore"):
            if kind == "low":
                gain = 1.0 / np.sqrt(1.0 + (freqs / fc) ** 8)
            elif kind == "high":
                gain = 1.0 / np.sqrt(1.0 + (fc / np.maximum(freqs, 1.0)) ** 8)
            else:
                # Textbook 2-pole bandpass magnitude, squared. A gentler skirt
                # reads as "broadband noise" rather than as a moving band, so a
                # swept `center` has to be genuinely narrow to be heard as one.
                bw = fc / max(q, 0.5)
                f = np.maximum(freqs, 1.0)
                gain = (1.0 / np.sqrt(1.0 + ((f * f - fc * fc) / (f * bw)) ** 2)) ** 2
        seg = xp[start:start + win] * w
        out[start:start + win] += np.fft.irfft(np.fft.rfft(seg) * gain, win) * w
        norm[start:start + win] += w * w
    norm[norm < 1e-9] = 1.0
    return (out / norm)[pad:pad + n]


# ------------------------------------------------------- physical archetypes


def karplus(freq, n, sr=SR, tone="plain", seed=0, damp=0.5):
    """Plucked string as damped additive synthesis.

    A delay-line (Karplus-Strong) string is the classic trick, but its timbre
    is an emergent property of a feedback loop: the excitation, the loop gain
    and the pitch interact, and at some pitches the fundamental all but
    disappears while a middle harmonic takes over. That is a bad deal for a
    language whose whole promise is that a named decision means the same thing
    everywhere.

    So the partials are synthesised directly, each with its own decay. Real
    strings lose their high partials first -- damping rises with frequency --
    which is what makes a pluck darken as it rings. That single rule gives a
    predictable spectrum at every pitch: the fundamental always leads.
    """
    if n <= 0:
        return np.zeros(0)
    f = _as_curve(freq, n)
    base = float(np.mean(f))
    t = tone_params(tone)
    tt = np.arange(n) / float(sr)
    # damp 0 -> an open string ringing for seconds, 1 -> palm-muted.
    t60 = max(3.4 * (1.0 - 0.9 * float(np.clip(damp, 0.0, 1.0))), 0.06)
    tau = t60 / 6.9
    ph = np.cumsum(2.0 * math.pi * f / sr)
    rng = rng_for(seed)
    out = np.zeros(n)
    nyq = sr * 0.48
    count = max(int(MAX_HARMONICS * min(float(t["harm"]), 1.0)), 3) \
        if float(t["harm"]) < 1.0 else MAX_HARMONICS
    for k in range(1, count + 1):
        if base * k > nyq:
            break
        if t.get("odd_only") and k % 2 == 0:
            continue
        amp = k ** -(float(t["tilt"]) + 0.35)
        if t.get("thin") and k > 3:
            amp *= 0.3
        if amp < 1e-4:
            break
        # Higher partials die sooner; the exponent is what a string sounds like.
        decay = np.exp(-tt / (tau / k ** 1.25))
        out += amp * decay * np.sin(ph * k + rng.uniform(0, 2 * math.pi))
    # The pick itself: a few milliseconds of bright noise, mostly felt.
    click = int(min(0.006 * sr, n))
    if click > 8:
        pick = rng.standard_normal(click) * np.linspace(1.0, 0.0, click)
        out[:click] += 0.12 * float(t["harm"]) * filter_signal(
            pick, "high", min(base * 4.0, sr * 0.4), sr)
    if t.get("detune"):
        out = 0.75 * out + 0.35 * np.roll(out, int(0.004 * sr))
    return out / (np.max(np.abs(out)) or 1.0)


def bell(freq, n, sr=SR, tone="plain", seed=0):
    """Inharmonic FM: a struck metal body, not a tuned oscillator."""
    if n <= 0:
        return np.zeros(0)
    f = _as_curve(freq, n)
    base = float(np.mean(f))
    t = np.arange(n) / float(sr)
    # FM spreads sidebands out to roughly carrier + index * modulator. Left
    # alone that runs past Nyquist for any bright, high-pitched bell and folds
    # back as inharmonic grit -- which is exactly what a bell is made of, so it
    # hides in plain sight. Cap the index so the top sideband stays in band.
    ratio = 1.414
    # Cap the top sideband well below Nyquist, not just inside it. Staying
    # under Nyquist stops the folding, but a lone inharmonic sine at 17 kHz is
    # still the most piercing thing in a mix -- it carries almost no energy, so
    # nothing else measures it, and it is all anyone hears.
    top = min(sr * 0.45, BELL_TOP_HZ)
    headroom = (top - np.maximum(f, 1.0)) / np.maximum(f * ratio, 1.0)
    idx_max = np.clip(headroom, 0.0, 12.0)
    idx = np.minimum(6.0 * float(tone_params(tone)["harm"]), idx_max) \
        * np.exp(-4.0 * t / max(t[-1], 1e-6))
    mod = np.sin(2 * math.pi * np.cumsum(f * ratio) / sr)
    out = np.sin(2 * math.pi * np.cumsum(f) / sr + idx * mod)
    # FM sidebands stop dead at the index limit, which leaves the outermost
    # one exposed. Roll the top off so the bell fades out instead of ending
    # on a bare partial.
    out = filter_signal(out, "low", min(base * 6.0, 7000.0), sr)
    return out / (np.max(np.abs(out)) or 1.0)


def metal(freq, n, sr=SR, tone="plain", seed=0):
    """A handful of inharmonic partials -- swords, chains, hinges, coins."""
    if n <= 0:
        return np.zeros(0)
    f = _as_curve(freq, n)
    r = rng_for(seed)
    # Enough partials that the top of the range fuses into a wash. Seven was
    # too few: struck at 3 kHz they land as isolated sines in the octave the
    # ear is most sensitive to, and a lone 10 kHz sine on every hi-hat is the
    # "tweeter tone" that no energy-based measurement will ever flag.
    ratios = np.array([1.0, 1.41, 1.73, 2.11, 2.41, 2.79, 3.19, 3.74, 4.62,
                       5.21, 6.05, 6.83, 7.71, 8.94, 10.2, 11.7])
    ratios = ratios * (1.0 + r.uniform(-0.08, 0.08, len(ratios)))
    t = np.arange(n) / float(sr)
    bright = float(tone_params(tone)["harm"])
    out = np.zeros(n)
    base = float(np.mean(f))
    for i, ratio in enumerate(ratios):
        hz = base * ratio
        if hz > min(sr * 0.45, PARTIAL_TOP_HZ):
            continue
        # Brightness moves where the rolloff starts. It must NOT scale each
        # partial by an exponent of the index: `bright ** (i * 0.4)` is a 13x
        # boost by the fourteenth partial, which made the highest mode louder
        # than the fundamental and put a bare sine in the tweeters.
        amp = 0.88 ** i
        knee = 4000.0 * max(bright, 0.3)
        if hz > knee:
            amp *= (knee / hz) ** 2.0
        decay = np.exp(-t * (2.5 + 1.1 * i) / max(bright, 0.3))
        out += amp * decay * np.sin(2 * math.pi * ratio * np.cumsum(f) / sr)
    # Struck metal is not only modes: the strike itself is broadband, and the
    # high modes are packed too densely to hear apart. Sixteen bare sines leave
    # audible gaps between them, and whichever one lands highest rings alone.
    # A noise bed under the partials is what makes it read as metal.
    src = r.standard_normal(n)
    wash = filter_signal(src, "band", min(base * 3.0, sr * 0.35), sr, q=0.8)
    out += 0.30 * bright * wash * np.exp(-t * 9.0)
    # And the top: broadband, so the shimmer above the partials is a wash the
    # ear reads as metal rather than a pitch it can hum.
    shimmer = filter_signal(src, "high", PARTIAL_TOP_HZ * 0.8, sr)
    out += 0.16 * bright * shimmer * np.exp(-t * 7.0)
    return out / (np.max(np.abs(out)) or 1.0)


def thump(freq, n, sr=SR, tone="plain", seed=0, drop=2.5):
    """Drum body: a sine whose pitch falls fast. Kick, tom, impact, footfall."""
    if n <= 0:
        return np.zeros(0)
    base = float(np.mean(_as_curve(freq, n)))
    t = np.linspace(0.0, 1.0, n)
    f = base * (1.0 + (drop - 1.0) * np.exp(-9.0 * t))
    out = np.sin(2 * math.pi * np.cumsum(f) / sr)
    click = tone_params(tone)["harm"] * 0.25
    if click:
        k = min(int(0.004 * sr), n)
        out[:k] += click * rng_for(seed).standard_normal(k) * np.linspace(1, 0, k)
    return out / (np.max(np.abs(out)) or 1.0)


def breath(freq, n, sr=SR, tone="plain", seed=0):
    """Air moving: wind, whoosh, flute chiff. Noise through two resonances."""
    if n <= 0:
        return np.zeros(0)
    f = _as_curve(freq, n)
    x = rng_for(seed).standard_normal(n)
    body = filter_signal(x, "band", f, sr, q=2.5)
    top = filter_signal(x, "band", np.clip(f * 2.7, 20, sr * 0.45), sr, q=4.0)
    bright = tone_params(tone)["harm"]
    out = body + 0.5 * bright * top
    return out / (np.max(np.abs(out)) or 1.0)


def bowed(freq, n, sr=SR, tone="plain", seed=0):
    """Sustained string/voice: saw plus slow vibrato and a soft top."""
    if n <= 0:
        return np.zeros(0)
    f = _as_curve(freq, n)
    t = np.arange(n) / float(sr)
    vib = 1.0 + 0.004 * np.sin(2 * math.pi * 5.2 * t) * np.clip(t / 0.35, 0, 1)
    out = osc("saw", f * vib, n, sr, tone=tone, seed=seed)
    # Cut in absolute terms as well as relative. Nine harmonics is the right
    # shape for a bowed string, but on a high note that puts the ninth at
    # 10 kHz with nothing around it -- one bare sine in the tweeters.
    cutoff = min(float(np.mean(f)) * 9.0, 7500.0)
    out = filter_signal(out, "low", cutoff, sr)
    # Bow noise. Real rosin fills the top of the band, so no partial up there
    # is ever alone; it is also most of what makes a bow sound bowed.
    hiss = filter_signal(rng_for(seed + 3).standard_normal(n), "band",
                         min(cutoff * 0.8, 6000.0), sr, q=1.1)
    out = out + 0.05 * hiss * (np.max(np.abs(out)) or 1.0)
    return out / (np.max(np.abs(out)) or 1.0)


def drone(freq, n, sr=SR, tone="plain", seed=0):
    """Stacked detuned saws -- choirs, hurdy-gurdies, dread."""
    if n <= 0:
        return np.zeros(0)
    f = _as_curve(freq, n)
    out = np.zeros(n)
    for i, cents in enumerate((-9.0, 0.0, 7.0)):
        out += osc("saw", f * (2.0 ** (cents / 1200.0)), n, sr, tone=tone, seed=seed + i)
    out = filter_signal(out / 3.0, "low", float(np.mean(f)) * 7.0, sr)
    return out / (np.max(np.abs(out)) or 1.0)


SOURCES = {
    "sine": lambda f, n, sr, tone, seed: osc("sine", f, n, sr, tone, seed),
    "tri": lambda f, n, sr, tone, seed: osc("tri", f, n, sr, tone, seed),
    "saw": lambda f, n, sr, tone, seed: osc("saw", f, n, sr, tone, seed),
    "square": lambda f, n, sr, tone, seed: osc("square", f, n, sr, tone, seed),
    "pulse": lambda f, n, sr, tone, seed: osc("pulse", f, n, sr, tone, seed),
    "pluck": karplus,
    "bell": bell,
    "metal": metal,
    "thump": thump,
    "breath": breath,
    "bow": bowed,
    "drone": drone,
    "noise": lambda f, n, sr, tone, seed: noise("white", n, sr, seed),
    "pink": lambda f, n, sr, tone, seed: noise("pink", n, sr, seed),
    "band": lambda f, n, sr, tone, seed: noise("band", n, sr, seed, center=f),
}


def dc_block(x, sr=SR):
    """Strip sub-audio content. Below ~25 Hz nothing is heard, but it still
    eats headroom and pushes the waveform off centre."""
    if len(x) == 0:
        return x
    # Subtract the mean first. A 25 Hz highpass needs a kernel longer than a
    # 25 Hz wavelength to actually stop one, and the FIR here is not; the mean
    # is what a short kernel leaves behind.
    x = x - float(np.mean(x))
    return x if len(x) < 64 else filter_signal(x, "high", 25.0, sr)


def render_source(kind, freq, n, sr=SR, tone="plain", seed=0):
    fn = SOURCES.get(kind)
    if fn is None:
        raise KeyError(kind)
    out = dc_block(fn(freq, n, sr, tone, seed), sr)
    peak = np.max(np.abs(out)) if len(out) else 0.0
    return out / peak if peak > 1e-9 else out


# ------------------------------------------------------------------ effects

SPACES = {
    "none": None,
    "room":  dict(time=0.35, damp=3800.0, mix=0.22, pre=0.008),
    "hall":  dict(time=1.60, damp=2600.0, mix=0.30, pre=0.028),
    "cave":  dict(time=2.80, damp=1400.0, mix=0.38, pre=0.045),
    "plate": dict(time=1.00, damp=7000.0, mix=0.26, pre=0.004),
}


def reverb(x, name, sr=SR, seed=11):
    """Convolution with a synthetic decaying-noise impulse response."""
    p = SPACES.get(name)
    if not p or len(x) == 0:
        return x
    n = int(p["time"] * sr)
    t = np.arange(n) / float(sr)
    ir = rng_for(seed).standard_normal(n) * np.exp(-6.0 * t / p["time"])
    ir = filter_signal(ir, "low", p["damp"], sr)
    # A few early reflections give the tail a size instead of a smear.
    for delay, gain in ((0.011, 0.7), (0.019, 0.5), (0.031, 0.35)):
        d = int(delay * sr)
        if d < n:
            ir[d] += gain
    pre = int(p["pre"] * sr)
    ir = np.concatenate([np.zeros(pre), ir])
    ir /= np.sqrt(np.sum(ir * ir)) or 1.0
    wet = _fftconv(np.concatenate([x, np.zeros(len(ir))]), ir)[:len(x) + len(ir)]
    dry = np.concatenate([x, np.zeros(len(wet) - len(x))])
    # Match the wet path's loudness to the dry one before mixing. A space is a
    # place, not a fader: declaring `space=hall` must not make a track louder
    # than the level the author asked for.
    dry_rms = float(np.sqrt(np.mean(dry * dry)))
    wet_rms = float(np.sqrt(np.mean(wet * wet)))
    if wet_rms > 1e-9 and dry_rms > 1e-9:
        wet = wet * (dry_rms / wet_rms)
    out = (1.0 - p["mix"]) * dry + p["mix"] * wet
    # Dry and wet are uncorrelated, so the crossfade loses level even when both
    # sides match. Put it back: the track must sit where its fader says.
    out_rms = float(np.sqrt(np.mean(out * out)))
    if out_rms > 1e-9 and dry_rms > 1e-9:
        out = out * (dry_rms / out_rms)
    return out


ECHOES = {
    "none": None,
    "slap": dict(time=0.12, feedback=0.25, taps=3, damp=4000.0),
    "tape": dict(time=0.28, feedback=0.45, taps=6, damp=2600.0),
    "canyon": dict(time=0.55, feedback=0.55, taps=8, damp=1800.0),
}


def echo(x, name, sr=SR):
    p = ECHOES.get(name)
    if not p or len(x) == 0:
        return x
    d = int(p["time"] * sr)
    out = np.concatenate([x, np.zeros(d * p["taps"])])
    tap = x
    for i in range(1, p["taps"] + 1):
        tap = filter_signal(tap, "low", p["damp"], sr) * p["feedback"]
        start = d * i
        out[start:start + len(tap)] += tap
    return out


def drive(x, name):
    if name in (None, "none") or len(x) == 0:
        return x
    peak = np.max(np.abs(x)) or 1.0
    if name == "soft":
        return np.tanh(x / peak * 2.0) * peak * 0.8
    if name == "hard":
        return np.clip(x / peak * 4.0, -1.0, 1.0) * peak * 0.7
    if name == "fuzz":
        y = np.sign(x) * (1.0 - np.exp(-np.abs(x / peak) * 6.0))
        return y * peak * 0.75
    return x


# ------------------------------------------------------------------ mixdown


def mix_into(buf, sig, start, gain=1.0):
    """Add sig into buf at a sample offset, growing nothing and clipping nothing."""
    if len(sig) == 0:
        return
    start = max(int(start), 0)
    end = min(start + len(sig), len(buf))
    if end <= start:
        return
    buf[start:end] += sig[:end - start] * gain


def normalize(x, target_db=-1.0, limit=True):
    """Peak-normalise to a target, soft-limiting anything that still pokes out."""
    if len(x) == 0:
        return x
    peak = float(np.max(np.abs(x)))
    if peak <= 1e-9:
        return x
    y = x / peak * db_to_amp(target_db)
    if limit:
        ceiling = db_to_amp(target_db)
        hot = np.abs(y) > ceiling
        if hot.any():
            y[hot] = np.sign(y[hot]) * ceiling * np.tanh(np.abs(y[hot]) / ceiling)
    return y


def fade_edges(x, sr=SR, ms=3.0):
    """Every rendered artefact starts and ends at silence."""
    n = min(int(ms * 0.001 * sr), len(x) // 2)
    if n <= 0:
        return x
    x[:n] *= np.linspace(0.0, 1.0, n)
    x[-n:] *= np.linspace(1.0, 0.0, n)
    return x
