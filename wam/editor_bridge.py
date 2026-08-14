"""Serve one WAM viewer with a safe, local save-and-rebuild bridge.

The standalone viewer intentionally cannot write arbitrary files.  This module
is the opt-in boundary used by ``Launch-Latest-Version.cmd edit``: it accepts
only a fingerprinted layer for one WAM source, rebuilds in staging, and then
promotes the layer and generated artifacts together.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
import threading
from urllib.parse import parse_qs, urlparse
import webbrowser

from . import cli as wcli
from . import edits as wedges
from . import parser as wparser
from . import views as wviews


_PROFILE_VERSION = 1
_TRANSACTION_PREFIX = ".wam-edit-transaction-"
_MAX_REQUEST_BYTES = 4 * 1024 * 1024


class EditorBridgeError(ValueError):
    """A connected editor request cannot safely replace project artifacts."""


@dataclass(frozen=True)
class RebuildProfile:
    """The exact compiler options needed to refresh an existing output set."""

    out_prefix: Path
    views: tuple[str, ...]
    width: int
    height: int
    animation_name: str | None = None
    frames: int = 6
    animation_views: tuple[str, ...] | None = None
    bones: bool = False
    gltf: bool = True
    viewer: bool = True
    light: tuple[float, ...] | None = None


def _same_file(left: Path, right: Path) -> bool:
    """Compare resolved paths without requiring a Windows-only file handle."""
    try:
        return left.samefile(right)
    except OSError:
        return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))


def _safe_name(name: str) -> str:
    if not isinstance(name, str) or not name or Path(name).name != name:
        raise EditorBridgeError("transaction contains an unsafe artifact name")
    return name


def _write_json(path: Path, payload: object) -> None:
    """Replace a small metadata file atomically in its owning directory."""
    temporary = path.with_name(path.name + ".tmp-" + secrets.token_hex(6))
    with open(temporary, "w", encoding="utf-8", newline="\n") as output:
        json.dump(payload, output, indent=2, sort_keys=True)
        output.write("\n")
    os.replace(temporary, path)


def _view_tokens(items: object) -> tuple[str, ...]:
    if not isinstance(items, list) or not items:
        raise EditorBridgeError("build profile needs one or more views")
    tokens = []
    for item in items:
        if not isinstance(item, dict):
            raise EditorBridgeError("build profile view is invalid")
        try:
            token = "%s:%.12g:%.12g" % (item["id"], float(item["yaw"]),
                                          float(item["pitch"]))
        except (KeyError, TypeError, ValueError):
            raise EditorBridgeError("build profile view needs id, yaw, and pitch")
        tokens.append(token)
    # Reuse the compiler's path-safe identifier validation rather than keeping
    # a subtly different view grammar in the local server.
    return tuple("%s:%.12g:%.12g" % (view.id, view.yaw, view.pitch)
                 for view in wviews.parse_views(tokens))


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise EditorBridgeError("%s must be a positive integer" % label)
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise EditorBridgeError("%s must be a positive integer" % label)
    if result < 1:
        raise EditorBridgeError("%s must be a positive integer" % label)
    return result


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise EditorBridgeError("%s must be true or false" % label)
    return value


def _light_values(value: object) -> tuple[float, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) not in (3, 4, 5):
        raise EditorBridgeError("build profile light needs three to five numbers")
    try:
        light = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        raise EditorBridgeError("build profile light needs numbers")
    if not all(math.isfinite(item) for item in light):
        raise EditorBridgeError("build profile light needs finite numbers")
    return light


def _source_matches(manifest: dict, manifest_path: Path, source: Path) -> bool:
    entry = manifest.get("source")
    if not isinstance(entry, dict) or entry.get("sha256") != wedges.source_sha256(source):
        return False
    source_path = entry.get("path")
    if not isinstance(source_path, str) or not source_path:
        return False
    candidate = Path(source_path)
    if not candidate.is_absolute():
        candidate = manifest_path.parent / candidate
    return _same_file(candidate, source)


def _profile_from_manifest(manifest_path: Path, source: Path) -> RebuildProfile:
    try:
        with open(manifest_path, encoding="utf-8") as input_file:
            manifest = json.load(input_file)
    except (OSError, json.JSONDecodeError) as error:
        raise EditorBridgeError("cannot read render manifest %r: %s" %
                                (str(manifest_path), error))
    if not isinstance(manifest, dict) or not _source_matches(manifest, manifest_path, source):
        raise EditorBridgeError("render manifest does not belong to this WAM source")
    suffix = "_views.json"
    if not manifest_path.name.endswith(suffix):
        raise EditorBridgeError("render manifest name must end in _views.json")
    out_prefix = manifest_path.with_name(manifest_path.name[:-len(suffix)])
    raw = manifest.get("buildProfile")
    if raw is None:
        # Legacy manifests did not retain every command option.  They still
        # carry exact view cameras and panel sizes, so preserve those and use
        # normal compiler defaults for the omitted optional outputs.
        views = _view_tokens(manifest.get("views"))
        first = manifest["views"][0]
        width = _positive_int(first.get("width"), "render width")
        height = _positive_int(first.get("height"), "render height")
        if any(item.get("width") != width or item.get("height") != height
               for item in manifest["views"]):
            raise EditorBridgeError("legacy render manifest has mixed panel sizes")
        return RebuildProfile(out_prefix, views, width, height)
    if not isinstance(raw, dict) or raw.get("schemaVersion") != _PROFILE_VERSION:
        raise EditorBridgeError("render manifest has an unsupported build profile")
    animation = raw.get("animation")
    if animation is None:
        animation_name, frames, animation_views = None, 6, None
    elif isinstance(animation, dict) and isinstance(animation.get("name"), str):
        animation_name = animation["name"]
        frames = _positive_int(animation.get("frames"), "animation frames")
        animation_views = _view_tokens(animation.get("views"))
    else:
        raise EditorBridgeError("build profile animation is invalid")
    return RebuildProfile(
        out_prefix=out_prefix,
        views=_view_tokens(raw.get("views")),
        width=_positive_int(raw.get("width"), "render width"),
        height=_positive_int(raw.get("height"), "render height"),
        animation_name=animation_name,
        frames=frames,
        animation_views=animation_views,
        bones=_boolean(raw.get("bones", False), "build profile bones"),
        gltf=_boolean(raw.get("gltf", True), "build profile gltf"),
        viewer=_boolean(raw.get("viewer", True), "build profile viewer"),
        light=_light_values(raw.get("light")),
    )


def discover_profile(source: str | os.PathLike[str], out: str | None = None) -> RebuildProfile:
    """Find one compatible output set beside ``source`` or make a safe default."""
    source_path = Path(source).resolve()
    if not source_path.is_file() or source_path.suffix.lower() != ".wam":
        raise EditorBridgeError("edit needs an existing .wam source file")
    directory = source_path.parent
    if out is not None:
        candidate = Path(out)
        if not candidate.is_absolute():
            candidate = directory / candidate
        candidate = candidate.resolve()
        if candidate.parent != directory:
            raise EditorBridgeError("--out must stay in the WAM source directory")
        manifest_path = candidate.with_name(candidate.name + "_views.json")
        if manifest_path.is_file():
            return _profile_from_manifest(manifest_path, source_path)
        return RebuildProfile(candidate, tuple(wviews.DEFAULT_TURNAROUND), 480, 600)
    matches = []
    for manifest_path in sorted(directory.glob("*_views.json")):
        try:
            matches.append(_profile_from_manifest(manifest_path, source_path))
        except EditorBridgeError:
            continue
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise EditorBridgeError(
            "multiple render sets match this WAM; launch edit again with --out <prefix>")
    return RebuildProfile(source_path.with_suffix(""), tuple(wviews.DEFAULT_TURNAROUND), 480, 600)


class EditorWorkspace:
    """Own one source folder, its edit sidecar, and one serialised save flow."""

    def __init__(self, source: str | os.PathLike[str], out: str | None = None):
        self.source = Path(source).resolve()
        self.directory = self.source.parent
        self.profile = discover_profile(self.source, out)
        # The bridge exists to edit a viewer.  A legacy no-viewer recipe gains
        # a viewer at its next connected rebuild while all other settings stay.
        if not self.profile.viewer:
            self.profile = replace(self.profile, viewer=True)
        self.sidecar = self.source.with_suffix(".wamedit.json")
        self.lock = threading.Lock()
        self.recover_interrupted_transactions()

    @property
    def viewer_path(self) -> Path:
        return self.profile.out_prefix.with_suffix(".html")

    def _managed_artifacts(self) -> set[str]:
        base = self.profile.out_prefix.name
        fixed = {
            base + ".html", base + ".gltf", base + "_viewer.json",
            base + "_views.json", base + "_sheet.png", base + "_tex.png",
            base + "_bones.png",
        }
        names = set()
        for path in self.directory.iterdir():
            if not path.is_file():
                continue
            name = path.name
            if (name in fixed or
                    (name.startswith(base + "_view_") and name.endswith(".png")) or
                    (name.startswith(base + "_anim_") and name.endswith(".png"))):
                names.add(name)
        return names

    def _compile_stage(self, stage_prefix: Path, edits_path: Path | None) -> dict:
        report: dict = {}
        try:
            wcli.compile_model(
                str(self.source), str(stage_prefix), self.profile.views,
                anim_name=self.profile.animation_name, frames=self.profile.frames,
                bones_overlay=self.profile.bones, do_gltf=self.profile.gltf,
                quiet=True, width=self.profile.width, height=self.profile.height,
                anim_views=self.profile.animation_views, do_viewer=self.profile.viewer,
                light=self.profile.light, report=report,
                edits_path=str(edits_path) if edits_path else None,
            )
        except (OSError, ValueError, wparser.WamError) as error:
            raise EditorBridgeError("strict rebuild failed: %s" % error)
        warnings = report.get("warnings", [])
        if warnings:
            raise EditorBridgeError("strict rebuild rejected warnings: %s" %
                                    "; ".join(str(item) for item in warnings))
        return report

    @staticmethod
    def _artifact_names(report: dict, stage_dir: Path) -> set[str]:
        artifacts = report.get("artifacts")
        if not isinstance(artifacts, dict):
            raise EditorBridgeError("compiler did not report generated artifacts")
        names = set()
        root = stage_dir.resolve()
        for value in artifacts.values():
            values = value if isinstance(value, list) else [value]
            for item in values:
                if not item:
                    continue
                path = Path(item).resolve()
                if path.parent != root or not path.is_file():
                    raise EditorBridgeError("compiler produced an unsafe staged artifact")
                names.add(_safe_name(path.name))
        if not names:
            raise EditorBridgeError("compiler produced no staged artifacts")
        return names

    def _write_journal(self, transaction: Path, payload: dict) -> None:
        _write_json(transaction / "journal.json", payload)

    def _recover_transaction(self, transaction: Path) -> None:
        journal_path = transaction / "journal.json"
        try:
            with open(journal_path, encoding="utf-8") as input_file:
                journal = json.load(input_file)
        except (OSError, json.JSONDecodeError):
            shutil.rmtree(transaction, ignore_errors=True)
            return
        if (not isinstance(journal, dict) or journal.get("source") != str(self.source) or
                journal.get("schemaVersion") != 1):
            return
        state = journal.get("state")
        names = [_safe_name(name) for name in journal.get("names", [])]
        backup = transaction / "backup"
        if state != "committed":
            # A crash before the committed marker is treated as a failed save:
            # put the previous generation back, then remove any replacement
            # that has no previous generation to restore over it.
            #
            # The state decides what a *missing* backup means, and getting that
            # wrong destroys the very files this exists to protect.  In
            # "prepared" the backup loop was still running, so a file with no
            # backup is an original that never got copied and must be left
            # alone.  Only in "backed_up" has every original been moved, which
            # is what makes an un-backed-up file a newly promoted one.
            for name in names:
                previous = backup / name
                target = self.directory / name
                if previous.is_file():
                    os.replace(previous, target)
                elif state == "backed_up" and target.is_file():
                    target.unlink()
        shutil.rmtree(transaction, ignore_errors=True)

    def recover_interrupted_transactions(self) -> None:
        """Restore the last known-good artifact set after an interrupted save."""
        for transaction in self.directory.glob(_TRANSACTION_PREFIX + "*"):
            if transaction.is_dir():
                self._recover_transaction(transaction)

    def _promote(self, transaction: Path, names: set[str]) -> None:
        names = {_safe_name(name) for name in names}
        names.update(self._managed_artifacts())
        names = sorted(names)
        new_dir, backup = transaction / "new", transaction / "backup"
        backup.mkdir(exist_ok=True)
        journal = {
            "schemaVersion": 1,
            "source": str(self.source),
            "state": "prepared",
            "names": names,
        }
        self._write_journal(transaction, journal)
        try:
            for name in names:
                current = self.directory / name
                if current.is_file():
                    os.replace(current, backup / name)
            journal["state"] = "backed_up"
            self._write_journal(transaction, journal)
            for name in names:
                staged = new_dir / name
                if staged.is_file():
                    os.replace(staged, self.directory / name)
            journal["state"] = "committed"
            self._write_journal(transaction, journal)
        except OSError as error:
            self._recover_transaction(transaction)
            raise EditorBridgeError("could not replace generated artifacts: %s" % error)
        shutil.rmtree(transaction, ignore_errors=True)

    def _new_transaction(self) -> tuple[Path, Path]:
        transaction = Path(tempfile.mkdtemp(prefix=_TRANSACTION_PREFIX,
                                             dir=self.directory))
        new_dir = transaction / "new"
        new_dir.mkdir()
        return transaction, new_dir

    def _rebuild(self, layer: dict | None, persist_layer: bool) -> dict:
        transaction, new_dir = self._new_transaction()
        try:
            staged_layer = None
            if layer is not None:
                staged_layer = new_dir / self.sidecar.name
                _write_json(staged_layer, layer)
                # Fingerprint validation happens before the compiler writes a
                # replacement, so a stale browser page never touches outputs.
                try:
                    wedges.load_layer(staged_layer, self.source)
                except wedges.EditLayerError as error:
                    raise EditorBridgeError(str(error))
            elif self.sidecar.is_file():
                staged_layer = self.sidecar
                try:
                    wedges.load_layer(staged_layer, self.source)
                except wedges.EditLayerError as error:
                    raise EditorBridgeError(str(error))
            report = self._compile_stage(new_dir / self.profile.out_prefix.name,
                                         staged_layer)
            names = self._artifact_names(report, new_dir)
            if persist_layer:
                names.add(self.sidecar.name)
            self._promote(transaction, names)
            return report
        except Exception:
            if transaction.exists():
                shutil.rmtree(transaction, ignore_errors=True)
            raise

    def ensure_viewer(self) -> None:
        """Create a current viewer only when the selected output set lacks one."""
        if self.viewer_path.is_file():
            return
        with self.lock:
            if not self.viewer_path.is_file():
                self._rebuild(None, persist_layer=False)

    def save_layer(self, layer: object) -> dict:
        """Validate, strictly rebuild, and atomically save one browser layer."""
        if not isinstance(layer, dict):
            raise EditorBridgeError("save request needs one edit layer object")
        if not self.lock.acquire(blocking=False):
            raise EditorBridgeError("a save is already rebuilding this WAM source")
        try:
            report = self._rebuild(layer, persist_layer=True)
        finally:
            self.lock.release()
        return {
            "ok": True,
            "layer": self.sidecar.name,
            "viewer": self.viewer_path.name,
            "artifacts": report.get("artifacts", {}),
        }


class _EditorHTTPServer(ThreadingHTTPServer):
    """Threaded loopback host with a single workspace and random session key."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, workspace: EditorWorkspace, token: str):
        super().__init__(address, _EditorRequestHandler)
        self.workspace = workspace
        self.token = token


class _EditorRequestHandler(BaseHTTPRequestHandler):
    server: _EditorHTTPServer

    def log_message(self, format, *args):
        # The launcher already prints the URL and explicit save failures.  Do
        # not leak a session URL or token through the ordinary HTTP log.
        return

    def _token_matches(self, token: str | None) -> bool:
        return isinstance(token, str) and secrets.compare_digest(token, self.server.token)

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self) -> None:
        try:
            page = self.server.workspace.viewer_path.read_text(encoding="utf-8")
        except OSError as error:
            self._send_json(500, {"ok": False, "error": "cannot read viewer: %s" % error})
            return
        port = self.server.server_address[1]
        config = {
            "schemaVersion": 1,
            "saveUrl": "/api/save",
            "viewerUrl": "/?token=" + self.server.token,
            "token": self.server.token,
            "sourceName": self.server.workspace.source.name,
        }
        bridge = "<script>window.__WAM_EDITOR_BRIDGE__=%s;</script>" % json.dumps(config)
        page = page.replace("</head>", bridge + "</head>", 1)
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        token = parse_qs(parsed.query).get("token", [None])[0]
        if parsed.path == "/" and self._token_matches(token):
            self._send_html()
            return
        if parsed.path == "/health" and self._token_matches(token):
            self._send_json(200, {"ok": True, "source": self.server.workspace.source.name})
            return
        self._send_json(403, {"ok": False, "error": "local editor session is not authorised"})

    def do_POST(self):
        if urlparse(self.path).path != "/api/save":
            self._send_json(404, {"ok": False, "error": "unknown editor endpoint"})
            return
        if not self._token_matches(self.headers.get("X-WAM-Editor-Token")):
            self._send_json(403, {"ok": False, "error": "local editor session is not authorised"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > _MAX_REQUEST_BYTES:
                raise EditorBridgeError("save payload must be between 1 byte and 4 MiB")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            result = self.server.workspace.save_layer(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, EditorBridgeError,
                wedges.EditLayerError, OSError, ValueError) as error:
            self._send_json(400, {"ok": False, "error": str(error)})
            return
        self._send_json(200, result)


def run(source: str, out: str | None = None, port: int = 0,
        open_browser: bool = True) -> int:
    """Run one loopback session until Ctrl+C; never expose a network listener."""
    workspace = EditorWorkspace(source, out)
    workspace.ensure_viewer()
    token = secrets.token_urlsafe(32)
    server = _EditorHTTPServer(("127.0.0.1", int(port)), workspace, token)
    url = "http://127.0.0.1:%d/?token=%s" % (server.server_address[1], token)
    print("WAM connected editor: %s" % url)
    print("Source: %s" % workspace.source)
    print("Save all changes & rebuild writes %s beside the WAM source." % workspace.sidecar.name)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nWAM connected editor stopped.")
    finally:
        server.server_close()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="wam-editor",
        description="Open one local WAM editor with save-and-rebuild enabled.")
    parser.add_argument("source", help="the WAM source to edit")
    parser.add_argument("--out", help="existing output prefix beside the source WAM")
    parser.add_argument("--port", type=int, default=0,
                        help="loopback port (default: choose a free port)")
    parser.add_argument("--no-browser", action="store_true",
                        help="print the authenticated local URL without opening it")
    args = parser.parse_args(argv)
    try:
        return run(args.source, args.out, args.port, not args.no_browser)
    except EditorBridgeError as error:
        print("ERROR: %s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
