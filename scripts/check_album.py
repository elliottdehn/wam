#!/usr/bin/env python3
"""Inspect a muxed album: level consistency, and whether the joins hold.

    python3 scripts/check_album.py out/alien_god/alien_god.wav out/alien_god/tracklist.txt

An album is not ten renders in a row; it is one thing to sit through. The two
ways a mux goes wrong are audible immediately and invisible in any per-track
measurement: a track that jumps out because it was normalised on its own, and
a join that clicks because two renders were butted together at non-zero
samples.
"""
from __future__ import annotations

import re
import sys
import wave

import numpy as np


def read_wav(path):
    with wave.open(path) as fh:
        data = np.frombuffer(fh.readframes(fh.getnframes()), dtype="<i2")
        return data.astype(np.float64).reshape(-1, fh.getnchannels()) / 32768.0, \
            fh.getframerate()


def db(x):
    return -120.0 if x <= 1e-9 else 20.0 * np.log10(x)


def main(wav_path, list_path):
    album, rate = read_wav(wav_path)
    mono = album.mean(axis=1)
    starts = []
    for line in open(list_path):
        m = re.match(r"\s*(\d+)\.\s+(.+?)\s+(\d+):(\d\d)\s+(\d+):(\d\d)", line)
        if m:
            starts.append((m.group(2).strip(),
                           int(m.group(3)) * 60 + int(m.group(4)),
                           int(m.group(5)) * 60 + int(m.group(6))))
    print("album: %s, %d:%02d, %d tracks"
          % (wav_path, len(mono) // rate // 60, len(mono) // rate % 60, len(starts)))
    print()
    print("%-30s %8s %8s %8s" % ("track", "rms dB", "peak dB", "vs album"))
    whole = db(float(np.sqrt(np.mean(mono ** 2))))
    levels = []
    for name, start, dur in starts:
        seg = mono[int(start * rate):int((start + dur) * rate)]
        r = db(float(np.sqrt(np.mean(seg ** 2))))
        levels.append(r)
        print("%-30s %8.1f %8.1f %8.1f"
              % (name, r, db(float(np.max(np.abs(seg)))), r - whole))
    print()
    print("loudest track sits %.1f dB over the quietest"
          % (max(levels) - min(levels)))

    print()
    print("joins:")
    worst = 0.0
    for name, start, _ in starts[1:]:
        i = int(start * rate)
        step = abs(float(mono[i] - mono[i - 1]))
        typical = float(np.mean(np.abs(np.diff(mono[i - rate:i + rate]))))
        ratio = db(step) - db(typical or 1e-9)
        worst = max(worst, ratio)
        print("  into %-26s step %.5f, %+.1f dB over the surrounding motion"
              % (name, step, ratio))
    print()
    print("worst join is %+.1f dB over the material around it %s"
          % (worst, "(inaudible)" if worst < 14 else "(AUDIBLE - fix this)"))
    print("clipped samples: %d" % int(np.sum(np.abs(mono) >= 0.999)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
