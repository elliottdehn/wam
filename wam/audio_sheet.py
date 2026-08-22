"""Contact sheet for a rendered piece: waveform, spectrogram, onsets.

The model pipeline renders PNG turntables because an author cannot review a
mesh by reading its vertex list. The same problem applies here in a harsher
form -- an agent authoring audio cannot listen at all -- so every compile
prints a picture of the result: level over time, energy over frequency and
time, and where the transients actually landed relative to the grid the author
asked for.
"""
import math

import numpy as np

from . import audio as waudio
from . import render as wrender


# A 5x7 bitmap font. Small, ugly, and legible at 1:1 in a terminal-scaled PNG,
# which matters more here than typography: these labels are read, not admired.
FONT = {
    "A": ".###./#...#/#...#/#####/#...#/#...#/#...#",
    "B": "####./#...#/#...#/####./#...#/#...#/####.",
    "C": ".###./#...#/#..../#..../#..../#...#/.###.",
    "D": "####./#...#/#...#/#...#/#...#/#...#/####.",
    "E": "#####/#..../#..../####./#..../#..../#####",
    "F": "#####/#..../#..../####./#..../#..../#....",
    "G": ".###./#...#/#..../#.###/#...#/#...#/.###.",
    "H": "#...#/#...#/#...#/#####/#...#/#...#/#...#",
    "I": ".###./..#../..#../..#../..#../..#../.###.",
    "J": "..###/...#./...#./...#./...#./#..#./.##..",
    "K": "#...#/#..#./#.#../##.../#.#../#..#./#...#",
    "L": "#..../#..../#..../#..../#..../#..../#####",
    "M": "#...#/##.##/#.#.#/#.#.#/#...#/#...#/#...#",
    "N": "#...#/##..#/#.#.#/#..##/#...#/#...#/#...#",
    "O": ".###./#...#/#...#/#...#/#...#/#...#/.###.",
    "P": "####./#...#/#...#/####./#..../#..../#....",
    "Q": ".###./#...#/#...#/#...#/#.#.#/#..#./.##.#",
    "R": "####./#...#/#...#/####./#.#../#..#./#...#",
    "S": ".####/#..../#..../.###./....#/....#/####.",
    "T": "#####/..#../..#../..#../..#../..#../..#..",
    "U": "#...#/#...#/#...#/#...#/#...#/#...#/.###.",
    "V": "#...#/#...#/#...#/#...#/#...#/.#.#./..#..",
    "W": "#...#/#...#/#...#/#.#.#/#.#.#/##.##/#...#",
    "X": "#...#/#...#/.#.#./..#../.#.#./#...#/#...#",
    "Y": "#...#/#...#/.#.#./..#../..#../..#../..#..",
    "Z": "#####/....#/...#./..#../.#.../#..../#####",
    "0": ".###./#...#/#..##/#.#.#/##..#/#...#/.###.",
    "1": "..#../.##../..#../..#../..#../..#../.###.",
    "2": ".###./#...#/....#/...#./..#../.#.../#####",
    "3": "####./....#/....#/.###./....#/....#/####.",
    "4": "...#./..##./.#.#./#..#./#####/...#./...#.",
    "5": "#####/#..../####./....#/....#/#...#/.###.",
    "6": ".###./#..../#..../####./#...#/#...#/.###.",
    "7": "#####/....#/...#./..#../.#.../.#.../.#...",
    "8": ".###./#...#/#...#/.###./#...#/#...#/.###.",
    "9": ".###./#...#/#...#/.####/....#/....#/.###.",
    " ": "...../...../...../...../...../...../.....",
    "-": "...../...../...../#####/...../...../.....",
    "+": "...../..#../..#../#####/..#../..#../.....",
    ".": "...../...../...../...../...../..##./..##.",
    ",": "...../...../...../...../..##./..##./.#...",
    ":": "...../..##./..##./...../..##./..##./.....",
    "/": "....#/...#./...#./..#../.#.../.#.../#....",
    "%": "##..#/##..#/...#./..#../.#.../#..##/#..##",
    "(": "...#./..#../.#.../.#.../.#.../..#../...#.",
    ")": ".#.../..#../...#./...#./...#./..#../.#...",
    "[": ".###./.#.../.#.../.#.../.#.../.#.../.###.",
    "]": ".###./...#./...#./...#./...#./...#./.###.",
    "<": "...#./..#../.#.../#..../.#.../..#../...#.",
    ">": ".#.../..#../...#./....#/...#./..#../.#...",
    "=": "...../...../#####/...../#####/...../.....",
    "*": "...../#...#/.#.#./..#../.#.#./#...#/.....",
    "!": "..#../..#../..#../..#../..#../...../..#..",
    "#": ".#.#./#####/.#.#./.#.#./#####/.#.#./.....",
    "'": "..#../..#../...../...../...../...../.....",
    "_": "...../...../...../...../...../...../#####",
}

CHAR_W, CHAR_H = 5, 7


def draw_text(img, x, y, text, color=(0.85, 0.88, 0.92), scale=1):
    """Blit uppercase text. Unknown glyphs render as a blank, never a crash."""
    h, w, _ = img.shape
    cx = int(x)
    y = int(y)
    for ch in str(text).upper():
        glyph = FONT.get(ch)
        if glyph is None:
            cx += (CHAR_W + 1) * scale
            continue
        for ry, row in enumerate(glyph.split("/")):
            for rx, cell in enumerate(row):
                if cell != "#":
                    continue
                x0, y0 = cx + rx * scale, y + ry * scale
                if 0 <= y0 < h and 0 <= x0 < w:
                    img[y0:y0 + scale, x0:x0 + scale] = color
        cx += (CHAR_W + 1) * scale
    return cx


def text_width(text, scale=1):
    return len(str(text)) * (CHAR_W + 1) * scale


# ------------------------------------------------------------------ colours

BG = (0.055, 0.06, 0.075)
PANEL = (0.09, 0.10, 0.125)
GRID = (0.20, 0.22, 0.27)
INK = (0.80, 0.84, 0.90)
DIM = (0.45, 0.49, 0.57)
WAVE = (0.42, 0.78, 0.95)
WAVE2 = (0.95, 0.62, 0.36)
ACCENT = (0.98, 0.78, 0.30)

# Perceptually-ordered ramp for the spectrogram: dark blue -> magenta ->
# orange -> white. Ordered by luminance so a printout still reads correctly.
_RAMP = [(0.02, 0.02, 0.09), (0.16, 0.06, 0.35), (0.45, 0.09, 0.45),
         (0.72, 0.21, 0.33), (0.91, 0.47, 0.15), (0.99, 0.78, 0.28),
         (1.00, 0.98, 0.86)]


def colormap(v):
    """v in 0..1 -> (H,W,3), vectorised."""
    v = np.clip(v, 0.0, 1.0) * (len(_RAMP) - 1)
    lo = np.floor(v).astype(int)
    hi = np.minimum(lo + 1, len(_RAMP) - 1)
    f = (v - lo)[..., None]
    ramp = np.array(_RAMP)
    return ramp[lo] * (1.0 - f) + ramp[hi] * f


def _rect(img, x0, y0, x1, y1, color):
    h, w, _ = img.shape
    x0, x1 = max(int(x0), 0), min(int(x1), w)
    y0, y1 = max(int(y0), 0), min(int(y1), h)
    if x1 > x0 and y1 > y0:
        img[y0:y1, x0:x1] = color


def _vline(img, x, y0, y1, color, alpha=1.0):
    h, w, _ = img.shape
    x = int(x)
    if not 0 <= x < w:
        return
    y0, y1 = max(int(y0), 0), min(int(y1), h)
    if y1 > y0:
        img[y0:y1, x] = np.array(color) * alpha + img[y0:y1, x] * (1.0 - alpha)


# -------------------------------------------------------------- spectrogram


def spectrogram(mono, rate, width, height, fmin=40.0, fmax=18000.0, floor_db=-78.0):
    """Log-frequency magnitude spectrogram, resampled to exactly width columns."""
    win, hop = 2048, 512
    if len(mono) < win:
        mono = np.concatenate([mono, np.zeros(win - len(mono))])
    frames = 1 + (len(mono) - win) // hop
    idx = np.arange(win)[None, :] + hop * np.arange(frames)[:, None]
    mag = np.abs(np.fft.rfft(mono[idx] * np.hanning(win), axis=1))
    freqs = np.fft.rfftfreq(win, 1.0 / rate)
    db = 20.0 * np.log10(np.maximum(mag, 1e-9))
    db -= np.max(db) if np.max(db) > -np.inf else 0.0
    # Log frequency: an octave gets equal height, the way hearing works.
    targets = np.exp(np.linspace(math.log(fmin), math.log(fmax), height))
    rows = np.interp(targets, freqs, np.arange(len(freqs)))
    grid = db[:, np.clip(np.round(rows).astype(int), 0, db.shape[1] - 1)]
    cols = np.linspace(0, grid.shape[0] - 1, width)
    lo = np.floor(cols).astype(int)
    hi = np.minimum(lo + 1, grid.shape[0] - 1)
    f = (cols - lo)[:, None]
    grid = grid[lo] * (1.0 - f) + grid[hi] * f
    norm = np.clip((grid.T - floor_db) / (0.0 - floor_db), 0.0, 1.0)
    return colormap(norm)[::-1]          # low frequencies at the bottom


# --------------------------------------------------------------------- sheet


def build_sheet(piece, width=1180):
    """One PNG per compiled piece. Returns an (H,W,3) float image."""
    buf, rate = piece["buf"], piece["rate"]
    mono = buf.mean(axis=1) if buf.ndim > 1 else buf
    dur = len(mono) / float(rate)
    metrics, meta = piece["metrics"], piece["meta"]

    pad, head = 14, 46
    wave_h, spec_h, mark_h = 120, 300, 74
    plot_w = width - pad * 2 - 42                 # room for a frequency axis
    height = head + wave_h + spec_h + mark_h + pad * 5
    img = np.zeros((height, width, 3))
    img[:, :] = BG

    x0 = pad + 42
    y = pad

    # ---- header ----------------------------------------------------------
    title = "%s  %s" % (meta["kind"], piece["name"])
    draw_text(img, pad, y, title, INK, scale=2)
    if meta["kind"] == "song":
        sub = "%s   %g bpm   %g bars   %s   %s%s" % (
            meta["key"], meta["tempo"], meta["bars"], meta["meter"],
            meta["feel"], "   loop" if meta["loop"] else "")
    else:
        sub = "length %s   space %s   %d layers" % (
            meta["length"], meta["space"], len(meta.get("layers", [])))
    draw_text(img, pad, y + 18, sub, DIM)
    stat = "%.2fs   peak %.1f db   rms %.1f db   crest %.1f db   centroid %.0f hz   onsets %d" % (
        metrics["duration"], metrics["peak"], metrics["rms"], metrics["crest"],
        metrics["centroid"], metrics["onsets"])
    draw_text(img, pad, y + 30, stat, DIM)
    y += head

    # ---- waveform --------------------------------------------------------
    _rect(img, x0, y, x0 + plot_w, y + wave_h, PANEL)
    mid = y + wave_h / 2.0
    for frac in (0.25, 0.5, 0.75):
        _rect(img, x0, int(y + wave_h * frac), x0 + plot_w, int(y + wave_h * frac) + 1, GRID)
    chans = [buf[:, 0], buf[:, 1]] if buf.ndim > 1 else [mono]
    edges = np.linspace(0, len(mono), plot_w + 1).astype(int)
    for ci, chan in enumerate(chans):
        color = WAVE if ci == 0 else WAVE2
        for col in range(plot_w):
            seg = chan[edges[col]:max(edges[col + 1], edges[col] + 1)]
            if not len(seg):
                continue
            hi = float(np.max(seg)) * (wave_h / 2.0 - 2)
            lo = float(np.min(seg)) * (wave_h / 2.0 - 2)
            top, bot = int(mid - hi), int(mid - lo)
            if bot <= top:
                bot = top + 1
            alpha = 0.85 if ci == 0 else 0.55
            img[max(top, y):min(bot, y + wave_h), x0 + col] = (
                np.array(color) * alpha
                + img[max(top, y):min(bot, y + wave_h), x0 + col] * (1 - alpha))
    draw_text(img, pad, int(mid) - 3, "amp", DIM)
    y += wave_h + pad

    # ---- spectrogram -----------------------------------------------------
    spec = spectrogram(mono, rate, plot_w, spec_h)
    img[y:y + spec_h, x0:x0 + plot_w] = spec
    for hz in (100, 250, 500, 1000, 2000, 4000, 8000, 16000):
        frac = (math.log(hz) - math.log(40.0)) / (math.log(18000.0) - math.log(40.0))
        if not 0.0 <= frac <= 1.0:
            continue
        row = int(y + spec_h - frac * spec_h)
        _rect(img, x0 - 4, row, x0, row + 1, DIM)
        label = "%dk" % (hz // 1000) if hz >= 1000 else str(hz)
        draw_text(img, pad, row - 3, label, DIM)
    y += spec_h + pad

    # ---- grid, onsets, level --------------------------------------------
    _rect(img, x0, y, x0 + plot_w, y + mark_h, PANEL)
    env = waudio.envelope_curve(mono, rate)
    env = env / (float(np.max(env)) or 1.0)
    cols = np.interp(np.linspace(0, len(env) - 1, plot_w), np.arange(len(env)), env)
    for col, v in enumerate(cols):
        top = int(y + mark_h - v * (mark_h - 12))
        _rect(img, x0 + col, top, x0 + col + 1, y + mark_h, (0.30, 0.55, 0.70))
    for t in waudio.find_onsets(mono, rate):
        _vline(img, x0 + t / max(dur, 1e-9) * plot_w, y, y + mark_h, ACCENT, 0.9)
    draw_text(img, pad, y + mark_h // 2 - 3, "hits", DIM)

    # Beat/bar grid for songs, layer starts for sounds: this is the panel that
    # answers "did the thing land where the file said it would".
    if meta["kind"] == "song":
        beat_s = 60.0 / meta["tempo"]
        # The grid is the bar lines the author wrote, so a hit that lands off
        # the beat is visible as exactly that.
        per_bar = float(meta["beats_per_bar"])
        beats = int(round(meta["beats"]))
        for b in range(beats + 1):
            t = b * beat_s
            if t > dur:
                break
            is_bar = abs(b % per_bar) < 1e-6
            _vline(img, x0 + t / max(dur, 1e-9) * plot_w, y, y + mark_h,
                   INK if is_bar else GRID, 0.9 if is_bar else 0.5)
            if is_bar:
                draw_text(img, x0 + t / max(dur, 1e-9) * plot_w + 2, y + 2,
                          str(int(b // per_bar) + 1), DIM)
    else:
        # `at=` is a fraction of the sound's authored length, not of the
        # rendered file, which is longer by whatever the tail needed.
        base = meta.get("base_seconds") or dur
        for layer in meta.get("layers", []):
            t = layer["at"] * base
            _vline(img, x0 + t / max(dur, 1e-9) * plot_w, y, y + mark_h, INK, 0.7)
            draw_text(img, x0 + t / max(dur, 1e-9) * plot_w + 2, y + 2, layer["layer"], DIM)
    y += mark_h + pad

    # ---- time axis -------------------------------------------------------
    ticks = 8
    for i in range(ticks + 1):
        t = dur * i / ticks
        px = x0 + int(plot_w * i / ticks)
        _rect(img, px, y, px + 1, y + 4, DIM)
        label = "%.2fs" % t
        draw_text(img, px - text_width(label) // 2, y + 6, label, DIM)
    return img


def write_sheet(path, piece, width=1180):
    wrender.write_png(path, build_sheet(piece, width))
    return path
