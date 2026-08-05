"""Crop and pair up images for reference comparison.

Comparing a whole render sheet against a whole reference image only ever
compares silhouettes: at that scale a head 20% too large, a pauldron sitting
two inches too low, and a hand with the wrong taper all look fine. This cuts
matching regions out of both and puts them side by side at the same height,
so the comparison happens at a scale where the difference is visible.

    # tile an image into a labelled grid, to pick regions to look at
    python3 scripts/crop.py ref.jpg --grid 3x3 -o out/crops

    # one region from each image, side by side, upscaled
    python3 scripts/crop.py ref.jpg out/model_sheet.png \\
        --box 0.30,0.00,0.70,0.35 --box2 0.05,0.02,0.20,0.30 -o out/cmp_head.png

Boxes are normalized (x0,y0,x1,y1 in 0..1) so they do not depend on either
image's pixel size. `--box2` aims the second image separately, which you
almost always need: the reference is framed differently from the render.
"""
import argparse
import os
import sys

try:
    from PIL import Image
except ImportError:                                    # pragma: no cover
    sys.exit("crop.py needs Pillow: python3 -m pip install pillow")


def _box(spec, w, h):
    try:
        x0, y0, x1, y1 = (float(v) for v in spec.split(","))
    except ValueError:
        raise SystemExit("--box wants x0,y0,x1,y1 normalized to 0..1, got %r"
                         % spec)
    px = (int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h))
    if px[2] <= px[0] or px[3] <= px[1]:
        raise SystemExit("--box %r is empty" % spec)
    return px


def _scaled(img, height):
    return img.resize((max(1, round(img.width * height / img.height)), height),
                      Image.LANCZOS)


def grid(path, cols, rows, out_dir):
    img = Image.open(path).convert("RGB")
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for r in range(rows):
        for c in range(cols):
            box = (img.width * c // cols, img.height * r // rows,
                   img.width * (c + 1) // cols, img.height * (r + 1) // rows)
            dst = os.path.join(out_dir, "r%dc%d.png" % (r, c))
            _scaled(img.crop(box), 480).save(dst)
            written.append(dst)
    return written


def compare(paths, boxes, height, pad, out_path):
    tiles = []
    for path, spec in zip(paths, boxes):
        img = Image.open(path).convert("RGB")
        tiles.append(_scaled(img.crop(_box(spec, img.width, img.height))
                             if spec else img, height))
    total_w = sum(t.width for t in tiles) + pad * (len(tiles) + 1)
    sheet = Image.new("RGB", (total_w, height + 2 * pad), (28, 28, 32))
    x = pad
    for t in tiles:
        sheet.paste(t, (x, pad))
        x += t.width + pad
    sheet.save(out_path)
    return out_path


def main(argv=None):
    ap = argparse.ArgumentParser(prog="crop")
    ap.add_argument("images", nargs="+", help="reference first, render second")
    ap.add_argument("--grid", default=None, help="tile one image, e.g. 3x3")
    ap.add_argument("--box", default=None, help="x0,y0,x1,y1 in 0..1")
    ap.add_argument("--box2", default=None, help="box for the second image")
    ap.add_argument("--height", type=int, default=560)
    ap.add_argument("--pad", type=int, default=8)
    ap.add_argument("-o", "--out", default="out/crop.png")
    args = ap.parse_args(argv)

    if args.grid:
        cols, _, rows = args.grid.partition("x")
        for p in grid(args.images[0], int(cols), int(rows or cols), args.out):
            print(p)
        return 0

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    boxes = [args.box] + [args.box2 or args.box] * (len(args.images) - 1)
    print(compare(args.images, boxes, args.height, args.pad, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
