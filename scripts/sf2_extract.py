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
