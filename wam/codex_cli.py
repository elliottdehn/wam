"""Agent-friendly WAM command surface with stable JSON on stdout.

Human-oriented compiler messages remain available through ``wam.cli``. This
entry point is intentionally quieter so Codex can compose it with shell tools
without scraping prose or guessing which generated files belong to a run.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys

from . import cli as wcli
from . import parser as wparser
from . import references as wreferences
from . import edits as wedges
from . import mesh as wmesh
from . import skeleton as wskel
from . import views as wviews


class CommandLineError(ValueError):
    """Represent argument and dispatch errors without argparse exiting."""


class JsonArgumentParser(argparse.ArgumentParser):
    """Raise usage failures so the agent surface can always emit JSON."""

    def error(self, message):
        raise CommandLineError(message)


def _error_payload(error):
    """Write one machine-readable error record and its human stderr twin."""
    message = str(error)
    print("ERROR: %s" % message, file=sys.stderr)
    print(json.dumps({"ok": False, "error": message}, sort_keys=True))
    return 2


def _absolute(path):
    return os.path.abspath(path) if path is not None else None


def _light(value):
    if not value:
        return None
    try:
        values = tuple(float(item) for item in value.split(","))
    except ValueError:
        raise ValueError("--light wants EL,AZ,AMBIENT[,KEY,FILL]")
    if len(values) not in (3, 4, 5):
        raise ValueError("--light wants 3 to 5 comma-separated numbers")
    return values


def _compile_parser():
    parser = JsonArgumentParser(prog="wam-codex compile")
    parser.add_argument("input")
    parser.add_argument("-o", "--out", default=None)
    parser.add_argument("--views", default=",".join(wviews.DEFAULT_TURNAROUND),
                        help="named views or id:yaw[:pitch], comma-separated")
    parser.add_argument("--anim", default=None)
    parser.add_argument("--anim-views", default=None)
    parser.add_argument("--frames", type=int, default=6)
    parser.add_argument("--bones", action="store_true")
    parser.add_argument("--edits", default=None,
                        help="non-destructive .wamedit.json layer for outputs")
    parser.add_argument("--no-gltf", action="store_true")
    parser.add_argument("--no-viewer", action="store_true")
    parser.add_argument("--width", type=int, default=480)
    parser.add_argument("--height", type=int, default=600)
    parser.add_argument("--light", default=None)
    parser.add_argument(
        "--strict", action="store_true",
        help="return exit code 3 when lint/check diagnostics remain")
    return parser


def compile_command(argv):
    try:
        args = _compile_parser().parse_args(argv)
    except CommandLineError as error:
        return _error_payload(error)
    out_prefix = args.out
    if out_prefix is None:
        base = os.path.splitext(os.path.basename(args.input))[0]
        out_prefix = os.path.join("out", base)
    report = {}
    try:
        model, _, _, warnings = wcli.compile_model(
            args.input,
            out_prefix,
            args.views,
            anim_name=args.anim,
            frames=args.frames,
            bones_overlay=args.bones,
            do_gltf=not args.no_gltf,
            quiet=True,
            width=args.width,
            height=args.height,
            anim_views=args.anim_views,
            do_viewer=not args.no_viewer,
            light=_light(args.light),
            report=report,
            edits_path=args.edits,
        )
    except (OSError, ValueError, wparser.WamError,
            wviews.ViewSpecError, wcli.CompileRequestError) as error:
        return _error_payload(error)

    generated = report["artifacts"]
    artifacts = {
        key: ([_absolute(path) for path in value]
              if key == "views" else _absolute(value))
        for key, value in generated.items()
    }
    strict_failure = bool(args.strict and warnings)
    payload = {
        "ok": not strict_failure,
        "strictFailure": strict_failure,
        "model": model.name,
        "diagnostics": {
            "warnings": list(warnings),
            "infos": list(report.get("infos", [])),
        },
        "artifacts": artifacts,
    }
    print(json.dumps(payload, sort_keys=True))
    return 3 if strict_failure else 0


def _rebase_parser():
    parser = JsonArgumentParser(prog="wam-codex rebase-edits")
    parser.add_argument("layer", help="existing .wamedit.json bound to --from")
    parser.add_argument("--from", dest="old_source", required=True,
                        help="original .wam source used by the layer")
    parser.add_argument("--to", dest="new_source", required=True,
                        help="replacement .wam source that receives the rebased layer")
    parser.add_argument("-o", "--out", required=True,
                        help="new .wamedit.json path; the input layer is never overwritten")
    parser.add_argument("--sync-rig-parts", default="",
                        help="comma-separated exclusively owned geometry to opt into syncRig")
    return parser


def _built_mesh(path):
    model = wparser.parse_file(path)
    bones, _ = wskel.solve(model)
    return wmesh.build(model, bones), bones


def _write_json_atomic(path, value):
    """Write a rebased sidecar without ever leaving a half-written layer."""
    target = os.path.abspath(path)
    folder = os.path.dirname(target) or "."
    os.makedirs(folder, exist_ok=True)
    temp = target + ".tmp"
    with open(temp, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temp, target)


def rebase_edits_command(argv):
    """Move a checked layer to a revised source without weakening references."""
    try:
        args = _rebase_parser().parse_args(argv)
        if os.path.abspath(args.layer) == os.path.abspath(args.out):
            raise ValueError("rebase output must differ from the input layer")
        old_layer = wedges.load_layer(args.layer, args.old_source)
        new_mesh, new_bones = _built_mesh(args.new_source)
        rebased = copy.deepcopy(old_layer)
        rebased["source"] = wedges.new_layer(args.new_source)["source"]
        requested = {part.strip() for part in args.sync_rig_parts.split(",") if part.strip()}
        eligibility = wedges.rig_sync_eligibility(new_mesh, new_bones)
        synchronized = set()
        for op in rebased["operations"]:
            if op.get("type") != "transform":
                continue
            part = op.get("part")
            targets = wedges._mirror_part(new_mesh, part, bool(op.get("mirror", False)),
                                          op.get("mirrorPart") if "mirrorPart" in op else None)
            if requested.intersection(targets):
                if not set(targets).issubset(requested):
                    raise ValueError("syncRig transform must name every mirrored target: %s" %
                                     ", ".join(targets))
                unsafe = [target for target in targets if not eligibility.get(target)]
                if unsafe:
                    raise ValueError("syncRig is unsafe for %s: those parts share a bone or own none" %
                                     ", ".join(unsafe))
                op["syncRig"] = True
                synchronized.update(targets)
        missing = requested - synchronized
        if missing:
            raise ValueError("no safe transform was found for syncRig part(s): %s" %
                             ", ".join(sorted(missing)))
        # Applying to a disposable build checks every part, local face and
        # mirror reference before the old layer can be promoted to the new SHA.
        wedges.apply_layer(new_mesh, new_bones, rebased)
        _write_json_atomic(args.out, rebased)
    except (OSError, ValueError, wparser.WamError, wedges.EditLayerError) as error:
        return _error_payload(error)
    print(json.dumps({"ok": True, "layer": _absolute(args.out),
                      "operations": len(rebased["operations"]),
                      "syncRigParts": sorted(synchronized)}, sort_keys=True))
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: wam-codex {compile,references,rebase-edits} ...")
        print("  compile     compile a .wam file and emit artifact JSON")
        print("  references  prepare multiple named reference images")
        print("  rebase-edits  validate and move a layer to a revised WAM source")
        return 0
    command, rest = argv[0], argv[1:]
    if command == "compile":
        return compile_command(rest)
    if command == "references":
        return wreferences.main(rest)
    if command == "rebase-edits":
        return rebase_edits_command(rest)
    return _error_payload("unknown command %r" % command)


if __name__ == "__main__":
    sys.exit(main())
