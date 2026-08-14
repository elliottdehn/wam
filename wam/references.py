"""Prepare multiple named reference images for an agent-guided WAM build.

The compiler never pretends to infer geometry directly from pixels. This
module preserves each reference as evidence: it records identity, role, view,
hash, and per-image crops so Codex can inspect several images without
compressing them into one lossy visual summary.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from . import views as wviews


_REFERENCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_KINDS = {"whole", "detail", "palette"}
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *("COM%d" % number for number in range(1, 10)),
    *("LPT%d" % number for number in range(1, 10)),
}


class ReferenceSpecError(ValueError):
    """Report invalid reference identifiers, files, roles, or grid options."""


class ReferenceArgumentParser(argparse.ArgumentParser):
    """Raise parse failures so Codex receives JSON instead of SystemExit."""

    def error(self, message):
        raise ReferenceSpecError(message)


def _assignment(value, label):
    key, separator, item = value.partition("=")
    key = key.strip()
    item = item.strip()
    if not separator or not key or not item:
        raise ReferenceSpecError("%s wants ID=VALUE, got %r" % (label, value))
    if not _REFERENCE_ID.fullmatch(key):
        raise ReferenceSpecError(
            "reference id %r must use letters, digits, '_' or '-'" % key)
    if key.upper() in _WINDOWS_RESERVED_NAMES:
        raise ReferenceSpecError(
            "reference id %r is a reserved Windows filename" % key)
    return key, item


def _unique_assignments(values, label):
    result = {}
    folded = {}
    for value in values or []:
        key, item = _assignment(value, label)
        normalized = key.casefold()
        if normalized in folded:
            raise ReferenceSpecError(
                "duplicate %s for %r and %r; IDs are case-insensitive" %
                (label, folded[normalized], key))
        result[key] = item
        folded[normalized] = key
    return result


def _grid_size(value):
    cols, separator, rows = value.lower().partition("x")
    try:
        cols = int(cols)
        rows = int(rows if separator else cols)
    except ValueError:
        raise ReferenceSpecError("--grid wants COLSxROWS, got %r" % value)
    if cols < 1 or rows < 1 or cols > 20 or rows > 20 or cols * rows > 100:
        raise ReferenceSpecError("--grid must contain between 1 and 100 cells")
    return cols, rows


def _relative(path, base_dir):
    absolute = os.path.abspath(path)
    try:
        return os.path.relpath(absolute, base_dir).replace(os.sep, "/")
    except ValueError:
        # Windows cannot form a relative path across drive letters. Retain a
        # usable normalized absolute path instead of aborting the whole run.
        return absolute.replace(os.sep, "/")


def _path_identity(path):
    """Return a portable, case-insensitive identity for collision checks."""
    return os.path.normcase(os.path.realpath(os.path.abspath(path))).casefold()


def _planned_outputs(reference_ids, out_dir, cols, rows):
    """Enumerate every path this run may overwrite before opening sources."""
    paths = [
        os.path.join(out_dir, "reference_board.png"),
        os.path.join(out_dir, "references.json"),
    ]
    for reference_id in reference_ids:
        for row in range(rows):
            for col in range(cols):
                paths.append(os.path.join(
                    out_dir, reference_id, "r%dc%d.png" % (row, col)))
    return paths


def _fit(image, width, height):
    image = image.copy()
    image.thumbnail((width, height), Image.Resampling.LANCZOS)
    return image


def _write_grid(image, reference_id, out_dir, cols, rows):
    tile_dir = os.path.join(out_dir, reference_id)
    os.makedirs(tile_dir, exist_ok=True)
    written = []
    for row in range(rows):
        for col in range(cols):
            box = (
                image.width * col // cols,
                image.height * row // rows,
                image.width * (col + 1) // cols,
                image.height * (row + 1) // rows,
            )
            tile = image.crop(box)
            tile.thumbnail((480, 480), Image.Resampling.LANCZOS)
            path = os.path.join(tile_dir, "r%dc%d.png" % (row, col))
            tile.save(path)
            written.append(path)
    return written


def _write_board(entries, out_path):
    """Write a labelled overview while keeping each full-resolution source separate."""
    cell_width, image_height, label_height, pad = 380, 320, 28, 10
    cols = min(3, len(entries))
    rows = (len(entries) + cols - 1) // cols
    board = Image.new(
        "RGB",
        (cols * cell_width + (cols + 1) * pad,
         rows * (image_height + label_height) + (rows + 1) * pad),
        (28, 28, 32),
    )
    draw = ImageDraw.Draw(board)
    for index, entry in enumerate(entries):
        row, col = divmod(index, cols)
        x = pad + col * (cell_width + pad)
        y = pad + row * (image_height + label_height + pad)
        preview = _fit(entry["image"], cell_width, image_height)
        px = x + (cell_width - preview.width) // 2
        py = y + (image_height - preview.height) // 2
        board.paste(preview, (px, py))
        label = "%s [%s]" % (entry["id"], entry["kind"])
        if entry["view"]:
            label += " view=%s" % entry["view"].id
        draw.text((x + 4, y + image_height + 6), label, fill=(245, 245, 245))
    board.save(out_path)


def prepare_references(reference_args, out_dir, view_args=None, kind_args=None,
                       grid="3x3"):
    """Validate, crop, and manifest an ordered set of named image references.

    Source images are read-only. A view is optional because detail and palette
    references often do not describe a whole-object camera angle.
    """
    reference_map = _unique_assignments(reference_args, "--reference")
    if not reference_map:
        raise ReferenceSpecError("at least one --reference ID=PATH is required")
    view_map = _unique_assignments(view_args, "--view")
    kind_map = _unique_assignments(kind_args, "--kind")
    reference_ids = {key.casefold(): key for key in reference_map}
    metadata_keys = list(view_map) + list(kind_map)
    unknown = sorted(key for key in metadata_keys
                     if key.casefold() not in reference_ids)
    if unknown:
        raise ReferenceSpecError(
            "metadata names unknown reference(s): %s" % ", ".join(unknown))
    for label, mapping in (("--view", view_map), ("--kind", kind_map)):
        folded = [key.casefold() for key in mapping]
        duplicate_targets = sorted({
            reference_ids[key.casefold()] for key in mapping
            if folded.count(key.casefold()) > 1
        }, key=str.casefold)
        if duplicate_targets:
            raise ReferenceSpecError(
                "%s repeats metadata for reference(s): %s" %
                (label, ", ".join(duplicate_targets)))

    cols, rows = _grid_size(grid)
    out_dir = os.path.abspath(out_dir)
    planned = {
        _path_identity(path): path
        for path in _planned_outputs(reference_map, out_dir, cols, rows)
    }
    # Refuse a self-overwriting layout before creating or writing anything.
    # This also keeps the recorded source hash truthful on Windows, where
    # paths that differ only by case identify the same file.
    for reference_id, path in reference_map.items():
        collision = planned.get(_path_identity(path))
        if collision is not None:
            raise ReferenceSpecError(
                "reference %r overlaps generated output %s; choose a "
                "different --out directory" % (reference_id, collision))

    os.makedirs(out_dir, exist_ok=True)
    loaded = []
    normalized_views = {
        reference_ids[key.casefold()]: value for key, value in view_map.items()
    }
    normalized_kinds = {
        reference_ids[key.casefold()]: value for key, value in kind_map.items()
    }
    for reference_id, path in reference_map.items():
        source_path = os.path.abspath(path)
        if not os.path.isfile(source_path):
            raise ReferenceSpecError("reference %r does not exist: %s" %
                                     (reference_id, source_path))
        kind = normalized_kinds.get(reference_id, "whole")
        if kind not in _KINDS:
            raise ReferenceSpecError(
                "kind for %r must be one of %s" %
                (reference_id, ", ".join(sorted(_KINDS))))
        view = (wviews.parse_view(normalized_views[reference_id])
                if reference_id in normalized_views else None)
        try:
            with Image.open(source_path) as source:
                # Phone and camera images frequently store their rotation in
                # EXIF rather than pixel order. Crops must follow the visible
                # orientation or an agent can infer the wrong model view.
                oriented = ImageOps.exif_transpose(source).convert("RGBA")
                # Composite transparency onto a neutral visible background.
                # Dropping alpha directly would turn invisible RGB payloads
                # into apparent evidence that was never visible to the user.
                background = Image.new("RGBA", oriented.size,
                                       (255, 255, 255, 255))
                image = Image.alpha_composite(background, oriented).convert("RGB")
        except (OSError, UnidentifiedImageError) as error:
            raise ReferenceSpecError(
                "reference %r is not a readable image: %s" %
                (reference_id, error))
        if image.width < cols or image.height < rows:
            raise ReferenceSpecError(
                "grid %dx%d exceeds %dx%d image %r; each crop must contain "
                "at least one pixel" %
                (cols, rows, image.width, image.height, reference_id))
        loaded.append({
            "id": reference_id,
            "path": source_path,
            "kind": kind,
            "view": view,
            "image": image,
        })

    manifest_entries = []
    for entry in loaded:
        tiles = _write_grid(entry["image"], entry["id"], out_dir, cols, rows)
        manifest_entries.append({
            "id": entry["id"],
            "kind": entry["kind"],
            "path": _relative(entry["path"], out_dir),
            "sha256": wviews.sha256_file(entry["path"]),
            "width": entry["image"].width,
            "height": entry["image"].height,
            "view": entry["view"].as_dict() if entry["view"] else None,
            "grid": {
                "columns": cols,
                "rows": rows,
                "files": [_relative(path, out_dir) for path in tiles],
            },
        })

    board_path = os.path.join(out_dir, "reference_board.png")
    _write_board(loaded, board_path)
    manifest_path = os.path.join(out_dir, "references.json")
    payload = {
        "schemaVersion": 1,
        "references": manifest_entries,
        "board": {
            "file": _relative(board_path, out_dir),
            "sha256": wviews.sha256_file(board_path),
        },
    }
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as output:
        json.dump(payload, output, indent=2, sort_keys=True)
        output.write("\n")
    return manifest_path, payload


def main(argv=None):
    ap = ReferenceArgumentParser(prog="wam-references")
    ap.add_argument(
        "--reference", action="append", required=True, metavar="ID=PATH",
        help="named source image; repeat for every view or detail image")
    ap.add_argument(
        "--view", action="append", default=[], metavar="ID=VIEW",
        help="optional named/custom view for a whole-object reference")
    ap.add_argument(
        "--kind", action="append", default=[], metavar="ID=KIND",
        help="whole, detail, or palette (default: whole)")
    ap.add_argument("--grid", default="3x3", help="crop grid per image")
    ap.add_argument("-o", "--out", default="out/references")
    try:
        args = ap.parse_args(argv)
        manifest_path, payload = prepare_references(
            args.reference, args.out, args.view, args.kind, args.grid)
    except (ReferenceSpecError, wviews.ViewSpecError) as error:
        print("ERROR: %s" % error, file=sys.stderr)
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        return 2
    print(json.dumps({
        "ok": True,
        "manifest": os.path.abspath(manifest_path),
        "referenceCount": len(payload["references"]),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
