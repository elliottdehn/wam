"""Canonical camera views and deterministic render-manifest helpers.

This module is the single extension point for named or custom turnaround
views. Keeping parsing here prevents one tool from silently treating an
unknown view as ``front`` while another tool renders a different angle.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
import re


STANDARD_VIEW_ANGLES = {
    "front": 0.0,
    "threequarter": 38.0,
    "side": 90.0,
    "threequarter_back": 142.0,
    "back": 180.0,
    "side_r": 270.0,
}
DEFAULT_TURNAROUND = (
    "front",
    "threequarter",
    "side",
    "threequarter_back",
    "back",
)

_VIEW_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class ViewSpecError(ValueError):
    """Report an invalid, empty, unknown, or duplicate view specification."""


@dataclass(frozen=True)
class ViewSpec:
    """A stable view identifier and its orbit-camera angles in degrees."""

    id: str
    yaw: float
    pitch: float = 10.0

    def as_dict(self):
        """Return the JSON-safe public representation of this view."""
        return {"id": self.id, "yaw": self.yaw, "pitch": self.pitch}


def _finite_angle(value, label):
    try:
        angle = float(value)
    except (TypeError, ValueError):
        raise ViewSpecError("%s must be a number, got %r" % (label, value))
    if not math.isfinite(angle):
        raise ViewSpecError("%s must be finite, got %r" % (label, value))
    return angle


def parse_view(token):
    """Parse a named view or ``id:yaw[:pitch]`` custom view.

    Custom IDs make output filenames and manifests stable. Pitch is restricted
    away from +/-90 degrees because the orbit basis becomes ambiguous at the
    poles and produces misleading inspection renders.
    """
    if isinstance(token, ViewSpec):
        return token
    text = str(token).strip()
    if not text:
        raise ViewSpecError("view names cannot be empty")
    if text in STANDARD_VIEW_ANGLES:
        return ViewSpec(text, STANDARD_VIEW_ANGLES[text])

    fields = text.split(":")
    if len(fields) not in (2, 3):
        choices = ", ".join(STANDARD_VIEW_ANGLES)
        raise ViewSpecError(
            "unknown view %r; use one of %s or id:yaw[:pitch]" %
            (text, choices))
    view_id = fields[0]
    if not _VIEW_ID.fullmatch(view_id):
        raise ViewSpecError(
            "custom view id %r must use letters, digits, '_' or '-'" % view_id)
    yaw = _finite_angle(fields[1], "yaw") % 360.0
    pitch = _finite_angle(fields[2], "pitch") if len(fields) == 3 else 10.0
    if not -89.0 < pitch < 89.0:
        raise ViewSpecError("pitch must be between -89 and 89 degrees")
    return ViewSpec(view_id, yaw, pitch)


def parse_views(value):
    """Return an ordered, non-empty list of unique :class:`ViewSpec` values."""
    raw = value.split(",") if isinstance(value, str) else list(value)
    if not raw:
        raise ViewSpecError("at least one view is required")
    specs = [parse_view(item) for item in raw]
    ids = [spec.id for spec in specs]
    folded_ids = [view_id.casefold() for view_id in ids]
    # Windows paths are case-insensitive, so case-only IDs would overwrite a
    # prior panel even though two distinct manifest entries were requested.
    duplicates = sorted({
        ids[index] for index, folded in enumerate(folded_ids)
        if folded_ids.count(folded) > 1
    }, key=str.casefold)
    if duplicates:
        raise ViewSpecError("duplicate view id(s): %s" % ", ".join(duplicates))
    return specs


def sha256_file(path):
    """Hash a file without retaining model or reference data in memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path, base_dir):
    absolute = os.path.abspath(path)
    try:
        return os.path.relpath(absolute, base_dir).replace(os.sep, "/")
    except ValueError:
        # Keep manifests usable when source and output are on different
        # Windows drives; os.path.relpath cannot represent that relationship.
        return absolute.replace(os.sep, "/")


def write_render_manifest(path, source, specs, image_paths, sheet_path,
                          width, height, center, distance, warnings=None,
                          infos=None, build_profile=None):
    """Write the deterministic sidecar consumed by Codex and other agents.

    The sidecar deliberately omits timestamps and prefers relative paths so
    identical layouts produce reviewable manifests across machines. A source
    on another Windows drive necessarily remains absolute. File hashes let an
    agent verify that it inspected the exact render named by the manifest.
    """
    base_dir = os.path.dirname(os.path.abspath(path)) or os.getcwd()
    source_entry = None
    if source:
        source_entry = {
            "path": _relative(source, base_dir),
            "sha256": sha256_file(source),
        }
    rendered = []
    for spec, image_path in zip(specs, image_paths):
        rendered.append({
            **spec.as_dict(),
            "file": _relative(image_path, base_dir),
            "sha256": sha256_file(image_path),
            "width": int(width),
            "height": int(height),
        })
    payload = {
        "schemaVersion": 1,
        "source": source_entry,
        "framing": {
            "center": [float(v) for v in center] if center is not None else None,
            "distance": float(distance) if distance is not None else None,
        },
        "views": rendered,
        "sheet": ({
            "file": _relative(sheet_path, base_dir),
            "sha256": sha256_file(sheet_path),
        } if sheet_path else None),
        "diagnostics": {
            "warnings": list(warnings or []),
            "infos": list(infos or []),
        },
    }
    if build_profile is not None:
        # The editor bridge reuses this explicit recipe instead of guessing
        # which options originally produced a user's current deliverables.
        payload["buildProfile"] = build_profile
    os.makedirs(base_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as output:
        json.dump(payload, output, indent=2, sort_keys=True)
        output.write("\n")
    return payload
