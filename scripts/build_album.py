#!/usr/bin/env python3
"""Compile a directory of `.wama` files and mux them into one album.

    python3 scripts/build_album.py examples/audio/alien_god --title "Alien God"

Tracks play in filename order. Each one is joined to the next with no gap,
using the `_once` render for looping songs -- the loop-ready file has its
ring-out wrapped onto its head, so joining those would cut every ending off
and start the next track with the previous one's reverb.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wam import audio as waudio          # noqa: E402
from wam import audio_cli as wcli        # noqa: E402


def mmss(seconds):
    return "%d:%02d" % (int(seconds) // 60, int(seconds) % 60)


def build(source_dir, outdir, title, join_seconds=0.0):
    sources = sorted(glob.glob(os.path.join(source_dir, "*.wama")))
    if not sources:
        raise SystemExit("no .wama files in %s" % source_dir)
    os.makedirs(outdir, exist_ok=True)

    pieces, rate = [], None
    for path in sources:
        report = wcli.compile_file(path, outdir)
        for entry in report["pieces"]:
            if entry["kind"] != "song":
                continue
            rate = rate or report["rate"]
            if report["rate"] != rate:
                raise SystemExit("%s renders at %d Hz, the album is %d"
                                 % (path, report["rate"], rate))
            # The un-wrapped render is the one that ends where the music ends.
            wav = entry["outputs"].get("once") or entry["outputs"]["wav"]
            pieces.append((os.path.basename(path), entry["name"], wav,
                           entry["metrics"]["duration"],
                           entry["metrics"]["peak"]))

    join = int(join_seconds * rate)
    total = sum(int(d * rate) for _, _, _, d, _ in pieces) - join * (len(pieces) - 1)
    album = np.zeros((max(total, 1), 2))
    at, rows = 0, []
    for i, (source, name, wav, dur, peak) in enumerate(pieces):
        buf = read_wav(wav)
        rows.append((i + 1, name, at / float(rate), dur, peak))
        end = at + len(buf)
        if end > len(album):
            album = np.vstack([album, np.zeros((end - len(album), 2))])
        if join and i:
            # Equal-power crossfade over the join, so neither track's own
            # ending is truncated to make room for the next one's start.
            k = min(join, len(buf), len(album) - at)
            fade = np.linspace(0.0, 1.0, k)[:, None]
            album[at:at + k] = album[at:at + k] * np.cos(fade * np.pi / 2) ** 2 \
                + buf[:k] * np.sin(fade * np.pi / 2) ** 2
            album[at + k:end] += buf[k:]
        else:
            album[at:end] += buf
        at = end - join

    album = album[:at + join if join else at]
    out = os.path.join(outdir, "%s.wav" % title.lower().replace(" ", "_"))
    waudio.write_wav(out, album, rate)

    listing = ["%s — %s" % (title, mmss(len(album) / float(rate))), ""]
    for number, name, start, dur, peak in rows:
        listing.append("%2d.  %-28s %6s   %5s   peak %.1f dB"
                       % (number, name.replace("_", " "), mmss(start),
                          mmss(dur), peak))
    text = "\n".join(listing)
    with open(os.path.join(outdir, "tracklist.txt"), "w") as fh:
        fh.write(text + "\n")
    return out, album, rate, text


def read_wav(path):
    import wave
    with wave.open(path) as fh:
        frames = fh.readframes(fh.getnframes())
        data = np.frombuffer(frames, dtype="<i2").astype(np.float64) / 32768.0
        return data.reshape(-1, fh.getnchannels())


def main(argv=None):
    p = argparse.ArgumentParser(prog="build_album")
    p.add_argument("source", help="directory of .wama files")
    p.add_argument("-o", "--out", default=None)
    p.add_argument("--title", default="Album")
    p.add_argument("--join", type=float, default=0.0,
                   help="crossfade between tracks, in seconds (default: butt-join)")
    args = p.parse_args(argv)
    outdir = args.out or os.path.join("out", os.path.basename(args.source.rstrip("/")))
    out, album, rate, text = build(args.source, outdir, args.title, args.join)
    print(text)
    print()
    print("album -> %s  (%.1f MB)" % (out, os.path.getsize(out) / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
