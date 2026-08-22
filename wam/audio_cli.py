"""WAM audio compiler CLI.

Usage:
  python3 -m wam.audio_cli piece.wama [-o out] [--only name,name]
                           [--no-sheet] [--no-wav] [--json]

Writes, per song and sound in the file:
  out/<name>.wav          16-bit stereo, normalised to the file's master target
  out/<name>_sheet.png    waveform + spectrogram + onset/beat grid
  out/<piece>_audio.json  every metric, warning and check result

Exit status is non-zero when a declared `assert` fails, so a piece with checks
is a regression test as well as an asset.
"""
import argparse
import json
import os
import sys

from . import audio as waudio
from . import audio_parser as ap
from . import audio_sheet as wsheet


def compile_file(path, outdir="out", only=None, sheet=True, wav=True, width=1180,
                 stems=False):
    doc = ap.parse_file(path)
    pieces = waudio.compile_document(doc, only=only)
    os.makedirs(outdir, exist_ok=True)
    report = {"file": path, "name": doc["name"], "rate": doc["rate"],
              "master_db": doc["master"], "pieces": []}
    for piece in pieces:
        entry = {"name": piece["name"], "kind": piece["kind"],
                 "metrics": {k: round(float(v), 4) for k, v in piece["metrics"].items()},
                 "meta": piece["meta"], "warnings": piece["warnings"],
                 "checks": piece["checks"], "outputs": {}}
        base = os.path.join(outdir, piece["name"])
        if wav:
            waudio.write_wav(base + ".wav", piece["buf"], piece["rate"])
            entry["outputs"]["wav"] = base + ".wav"
            # A looping render has its ring-out wrapped onto the head, which
            # is seamless on repeat and sounds truncated played once. Write
            # the un-wrapped version alongside it for listening.
            if piece.get("once") is not None:
                waudio.write_wav(base + "_once.wav", piece["once"], piece["rate"])
                entry["outputs"]["once"] = base + "_once.wav"
        if sheet:
            wsheet.write_sheet(base + "_sheet.png", piece, width)
            entry["outputs"]["sheet"] = base + "_sheet.png"
        if stems and piece.get("stems"):
            # One file per track, at its level in the mix: the fastest way to
            # find out which track is responsible for something.
            entry["outputs"]["stems"] = {}
            for name, stem in piece["stems"].items():
                stem_base = "%s_%s" % (base, name)
                if wav:
                    waudio.write_wav(stem_base + ".wav", stem, piece["rate"])
                if sheet:
                    stem_piece = dict(piece, buf=stem, name="%s / %s" % (piece["name"], name),
                                      metrics=waudio.measure(stem, piece["rate"]))
                    wsheet.write_sheet(stem_base + "_sheet.png", stem_piece, width)
                entry["outputs"]["stems"][name] = stem_base
        report["pieces"].append(entry)
    report["path"] = os.path.join(outdir, "%s_audio.json" % doc["name"])
    with open(report["path"], "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    return report


def _print_human(report):
    for piece in report["pieces"]:
        m = piece["metrics"]
        print("\n%s %s  %.2fs  peak %.1f dB  rms %.1f dB  centroid %.0f Hz  onsets %d"
              % (piece["kind"], piece["name"], m["duration"], m["peak"], m["rms"],
                 m["centroid"], m["onsets"]))
        for stat in piece["meta"].get("staves", []):
            marks = "".join([" MUTE" if stat["muted"] else "",
                             " SOLO" if stat["soloed"] else ""])
            print("   staff %-8s %-8s %d voice(s) %3d events  %6.1f dBFS  %5.0f Hz%s"
                  % (stat["staff"], stat["instrument"], stat["voices"],
                     stat["events"], stat["peak_db"], stat["centroid"], marks))
        for stat in piece["meta"].get("layers", []):
            print("   layer %-10s %-8s at %4.0f%%  x%d  %6.1f dBFS"
                  % (stat["layer"], stat["source"], stat["at"] * 100,
                     stat["repeats"], stat["peak_db"]))
        for warn in piece["warnings"]:
            print("   warn: %s" % warn)
        for chk in piece["checks"]:
            print("   %s %s   (%s)" % ("PASS" if chk["ok"] else "FAIL",
                                       chk["text"], chk["detail"]))
        for kind, path in piece["outputs"].items():
            if kind == "stems":
                print("   -> %d stems: %s_<track>.wav" % (len(path), piece["name"]))
            else:
                print("   -> %s" % path)


def main(argv=None):
    p = argparse.ArgumentParser(prog="wam.audio_cli")
    p.add_argument("source")
    p.add_argument("-o", "--out", default="out")
    p.add_argument("--only", default=None,
                   help="comma-separated song/sound names to render")
    p.add_argument("--no-sheet", action="store_true")
    p.add_argument("--no-wav", action="store_true")
    p.add_argument("--stems", action="store_true",
                   help="also write one file per staff, at its level in the mix")
    p.add_argument("--width", type=int, default=1180)
    p.add_argument("--json", action="store_true",
                   help="print the report as JSON and nothing else")
    args = p.parse_args(argv)
    only = [s.strip() for s in args.only.split(",")] if args.only else None
    try:
        report = compile_file(args.source, args.out, only,
                              sheet=not args.no_sheet, wav=not args.no_wav,
                              width=args.width, stems=args.stems)
    except ap.WamAudioError as err:
        print("error: %s" % err, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=1))
    else:
        _print_human(report)
        print("\nreport -> %s" % report["path"])
    failed = [c for pc in report["pieces"] for c in pc["checks"] if not c["ok"]]
    if failed:
        print("\n%d check(s) failed" % len(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
