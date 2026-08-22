#!/usr/bin/env python3
"""Pull sample notes out of a SoundFont into a WAM sample bank.

    python3 scripts/sf2_extract.py piano.sf2 samples/piano --every 4

A `.sf2` is a RIFF file: every sample in it is raw 16-bit PCM inside one
`smpl` chunk, and an `shdr` table says where each one starts, what rate it was
recorded at, and which MIDI note it was played at. That is all a sample bank
needs, so no soundfont library is required to read one.

Files are written as `<name>_<note>.wav`, which is the naming a WAM
`bank=` directory expects -- the root pitch is in the filename.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
import wave

NAMES = ("c", "cis", "d", "es", "e", "f", "fis", "g", "as", "a", "bes", "b")


def note_name(midi):
    """MIDI number -> the spelling WAM uses: 48 is `c`, 60 is `c'` (middle C)."""
    octave = midi // 12 - 4
    name = NAMES[midi % 12]
    return name + ("'" * octave if octave > 0 else "," * -octave)


def chunks(data, start, end):
    """Walk RIFF chunks between two offsets."""
    pos = start
    while pos + 8 <= end:
        tag = data[pos:pos + 4].decode("latin-1")
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = pos + 8
        yield tag, body, body + size
        pos = body + size + (size & 1)


def read_sf2(path):
    """-> (pcm bytes, [sample header dicts])"""
    raw = open(path, "rb").read()
    if raw[:4] != b"RIFF" or raw[8:12] != b"sfbk":
        raise SystemExit("%s is not a SoundFont" % path)
    smpl = None
    headers = []
    for tag, body, end in chunks(raw, 12, len(raw)):
        if tag != "LIST":
            continue
        kind = raw[body:body + 4].decode("latin-1")
        for sub, sbody, send in chunks(raw, body + 4, end):
            if kind == "sdta" and sub == "smpl":
                smpl = raw[sbody:send]
            elif kind == "pdta" and sub == "shdr":
                for off in range(sbody, send - 45, 46):
                    rec = raw[off:off + 46]
                    name = rec[:20].split(b"\0")[0].decode("latin-1")
                    (start, stop, _ls, _le, rate) = struct.unpack("<IIIII", rec[20:40])
                    pitch = rec[40]
                    if name and stop > start and 0 < pitch < 128:
                        headers.append(dict(name=name, start=start, end=stop,
                                            rate=rate, pitch=pitch))
    if smpl is None:
        raise SystemExit("no sample data in %s" % path)
    return smpl, headers


# How a drum soundfont's sample names map onto WAM's kit pieces. Matched as
# case-insensitive substrings against the name with its take number and
# channel suffix stripped, first match winning -- so the order matters: `Tom4`
# has to be tested before `Tom`.
DRUM_MAP = (
    ("kick", ("kdruml", "kick", "bassdrum")),
    ("snare", ("snare",)),
    ("hat", ("hihatclosed", "hatclosed", "closedhat")),
    ("openhat", ("hihatopen", "hatopen", "openhat")),
    ("lowtom", ("tom4", "tom3", "lowtom", "floortom")),
    ("tom", ("tom2", "tom1", "tom")),
    ("crash", ("crashl", "crash")),
    ("ride", ("ridel", "ride")),
)


def drum_stem(name):
    """A soundfont sample name with its take number and channel stripped."""
    stem = re.sub(r"^\d+-", "", name)
    return re.sub(r"_[LR]$", "", stem).lower()


def drum_piece(stem, exact_only=False):
    """A stripped sample name -> the WAM piece it is, or None."""
    for piece, keys in DRUM_MAP:
        if stem in keys:
            return piece
    if exact_only:
        return None
    for piece, keys in DRUM_MAP:
        for key in keys:
            if stem.startswith(key):
                return piece
    return None


def extract_drums(smpl, headers, outdir, take, trim=0.0):
    """Write one recording per kit piece, named for the piece."""
    os.makedirs(outdir, exist_ok=True)
    # Several takes exist per piece -- velocity layers, and left and right
    # channels as separate samples. Pick one: the take whose leading number is
    # nearest the one asked for, preferring the left channel.
    # Two passes. An exactly-named sample always wins, and the looser prefix
    # match only fills pieces that nothing named exactly -- otherwise a
    # `SnareRest` take at a closer velocity beats the actual `Snare`.
    best = {}
    for exact_only in (True, False):
        for h in headers:
            piece = drum_piece(drum_stem(h["name"]), exact_only)
            if piece is None or (not exact_only and piece in best):
                continue
            m = re.match(r"^(\d+)-", h["name"])
            layer = int(m.group(1)) if m else 0
            rank = (abs(layer - take), 0 if h["name"].endswith("_L") else 1)
            if piece not in best or rank < best[piece][0]:
                best[piece] = (rank, h)
    for piece, (_rank, h) in sorted(best.items()):
        pcm = smpl[h["start"] * 2:h["end"] * 2]
        if trim:
            keep = int(trim * h["rate"]) * 2
            if len(pcm) > keep:
                import numpy as _np
                cut = _np.frombuffer(pcm[:keep], dtype="<i2").astype(float)
                fade = int(0.08 * h["rate"])
                cut[-fade:] *= _np.linspace(1.0, 0.0, fade)
                pcm = cut.astype("<i2").tobytes()
        out = os.path.join(outdir, "%s.wav" % piece)
        with wave.open(out, "wb") as fh:
            fh.setnchannels(1)
            fh.setsampwidth(2)
            fh.setframerate(h["rate"])
            fh.writeframes(pcm)
        print("%-34s %5.2fs  from %s" % (out, len(pcm) / 2.0 / h["rate"], h["name"]))
    print("\n%d pieces -> %s" % (len(best), outdir))
    return 0


import re  # noqa: E402  (used by the drum mapping above)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sf2_extract")
    ap.add_argument("soundfont")
    ap.add_argument("outdir")
    ap.add_argument("--every", type=int, default=4,
                    help="keep one sample every N semitones (default 4)")
    ap.add_argument("--name", default="piano")
    ap.add_argument("--trim", type=float, default=0.0,
                    help="keep only the first N seconds of each sample")
    ap.add_argument("--list", action="store_true", help="only show what is inside")
    ap.add_argument("--drums", action="store_true",
                    help="extract a drum kit by piece name instead of by pitch")
    ap.add_argument("--take", type=int, default=24,
                    help="which velocity layer to prefer for --drums (default 24)")
    args = ap.parse_args(argv)

    smpl, headers = read_sf2(args.soundfont)
    headers.sort(key=lambda h: h["pitch"])
    if args.list:
        for h in headers:
            print("%-24s midi %3d (%-5s) %6.2fs @ %d Hz"
                  % (h["name"], h["pitch"], note_name(h["pitch"]),
                     (h["end"] - h["start"]) / float(h["rate"]), h["rate"]))
        return 0

    # A soundfont usually holds several velocity layers per note. A bank wants
    # one sample per pitch, so keep the longest recording of each -- it is the
    # one with the most tail to work with.
    best = {}
    for h in headers:
        have = best.get(h["pitch"])
        if have is None or (h["end"] - h["start"]) > (have["end"] - have["start"]):
            best[h["pitch"]] = h
    headers = [best[p] for p in sorted(best)]

    if args.drums:
        return extract_drums(smpl, headers, args.outdir, args.take, args.trim)

    os.makedirs(args.outdir, exist_ok=True)
    kept, last = 0, -99
    for h in headers:
        if h["pitch"] - last < args.every:
            continue
        last = h["pitch"]
        pcm = smpl[h["start"] * 2:h["end"] * 2]
        if args.trim:
            keep = int(args.trim * h["rate"]) * 2
            if len(pcm) > keep:
                # Fade the cut so a trimmed tail does not end on a step.
                import numpy as _np
                cut = _np.frombuffer(pcm[:keep], dtype="<i2").astype(float)
                fade = int(0.05 * h["rate"])
                cut[-fade:] *= _np.linspace(1.0, 0.0, fade)
                pcm = cut.astype("<i2").tobytes()
        out = os.path.join(args.outdir,
                           "%s_%s.wav" % (args.name, note_name(h["pitch"])))
        with wave.open(out, "wb") as fh:
            fh.setnchannels(1)
            fh.setsampwidth(2)
            fh.setframerate(h["rate"])
            fh.writeframes(pcm)
        kept += 1
        print("%-40s %5.2fs  root %s" % (out, len(pcm) / 2.0 / h["rate"],
                                         note_name(h["pitch"])))
    print("\n%d samples -> %s" % (kept, args.outdir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
