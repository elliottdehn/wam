"""Render a model as a flat silhouette, and at thumbnail size.

Shading hides shape. A lit render gives you colour, material, occlusion and a
gradient running down every limb, and the eye accepts all of it as "detail" —
so a limb with no taper looks tapered and a generic shape reads as fine. The
question the design pass actually asks is what this character reads as when
none of that is available: an outline, at the size a player first sees it.

Measured on this repo's own models: the hero reads at 24px as an anonymous
humanoid, indistinguishable from any other biped, while the scorpion is
identifiable at once from its raised tail and claws. Neither fact is visible
in the shaded sheet.

    python3 scripts/silhouette.py my.wam                  # sheet + thumb strip
    python3 scripts/silhouette.py my.wam --thumbs 24,32,48,64
    python3 scripts/silhouette.py my.wam --views front,side
    python3 scripts/silhouette.py my.wam --anim walk --frames 6

The thumbnail row is the part that matters and the part that gets skipped. A
silhouette at full panel size flatters everything; the same silhouette at 32px
is where "a humanoid" and "this specific character" stop being the same
picture. Read the smallest one first.

Framing matches `wam.cli` exactly (one shared camera distance across views), so
a silhouette panel can be cropped against the corresponding shaded panel with
`crop.py --box/--box2` and the two will line up.
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wam import parser as wparser          # noqa: E402
from wam import skeleton as wskel          # noqa: E402
from wam import mesh as wmesh              # noqa: E402
from wam import animation as wanim         # noqa: E402
from wam import render as wrender          # noqa: E402
from wam.cli import VIEW_ANGLES, shared_framing   # noqa: E402

INK = (0.10, 0.10, 0.12)
PAPER = (1.0, 1.0, 1.0)


def silhouette_view(V, T, yaw, width, height, center, dist):
    """One panel as a true binary mask: covered or not, nothing in between.

    Handing the renderer a single dark material is not enough — it still lights
    the surface, so the chest reads lighter than the arm beside it and the
    shape information you were trying to withhold comes straight back through
    the shading. Every covered pixel is therefore flattened to one ink value
    after the fact, which is the only way to guarantee the test is about
    outline alone.
    """
    flat = np.zeros(len(T), dtype=int)
    img = wrender.render_view(V, T, flat, [INK], yaw_deg=yaw,
                              width=width, height=height,
                              center=center, dist=dist, bg=PAPER)
    lum = img[..., :3].mean(axis=2)
    covered = lum < 0.55                      # ink is 0.10, paper is 1.0
    out = np.empty_like(img[..., :3])
    out[...] = PAPER
    out[covered] = INK
    return out


def downscale(img, target_h):
    """Box-filter to `target_h` tall, then hold at 1px so it stays visible.

    Kept deliberately crude — a nice resample would anti-alias the shape back
    into legibility, which is exactly the flattery being tested for.
    """
    h, w = img.shape[:2]
    scale = max(1, int(round(h / float(target_h))))
    small = img[: (h // scale) * scale, : (w // scale) * scale]
    small = small.reshape(h // scale, scale, w // scale, scale, -1).mean(axis=(1, 3))
    return small


def upscale(img, factor):
    return np.repeat(np.repeat(img, factor, axis=0), factor, axis=1)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="silhouette")
    ap.add_argument("input")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--views", default="front,threequarter,side,back")
    ap.add_argument("--thumbs", default="24,32,48",
                    help="thumbnail heights in px; '' to skip (default 24,32,48)")
    ap.add_argument("--width", type=int, default=360)
    ap.add_argument("--height", type=int, default=460)
    ap.add_argument("--anim", default=None)
    ap.add_argument("--frames", type=int, default=6)
    args = ap.parse_args(argv)

    model = wparser.parse_file(args.input)
    bones, order = wskel.solve(model)
    mesh = wmesh.build(model, bones)
    V, T, _ = mesh.arrays()
    if not len(V):
        sys.exit("model has no geometry")

    out = args.out or os.path.join(
        "out", os.path.splitext(os.path.basename(args.input))[0] + "_sil")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    views = [v for v in args.views.split(",") if v]
    yaws = [VIEW_ANGLES.get(v, 0) for v in views]

    if args.anim:
        anim = next((a for a in model.anims if a["name"] == args.anim), None)
        if anim is None:
            sys.exit("no such anim: %s" % args.anim)
        posed = []
        for i in range(args.frames):
            ph = i / args.frames if anim["loop"] else i / max(args.frames - 1, 1)
            rots = wanim.anim_rotations_at(model, bones, anim, ph)
            posed.append(wanim.skin_verts(mesh, bones, order, rots))
        # frame against every pose at once, or the figure appears to scale
        allpose = np.concatenate(posed)
        center, dist = shared_framing(allpose, yaws, args.width, args.height)
        rows = [wrender.hstack_views(
            [silhouette_view(P, T, y, args.width, args.height, center, dist)
             for P in posed]) for y in yaws]
        sheet = rows[0] if len(rows) == 1 else wrender.vstack_views(rows)
    else:
        center, dist = shared_framing(V, yaws, args.width, args.height)
        sheet = wrender.hstack_views(
            [silhouette_view(V, T, y, args.width, args.height, center, dist)
             for y in yaws])

    wrender.write_png(out + ".png", sheet)
    written = [out + ".png"]

    thumbs = [int(t) for t in args.thumbs.split(",") if t.strip()]
    if thumbs:
        # every thumbnail blown back up to a common height, so they sit in one
        # image at their true relative legibility instead of separate files
        big = max(thumbs) * 6
        cols = []
        for t in thumbs:
            small = downscale(sheet, t)
            cols.append(upscale(small, max(1, int(round(big / small.shape[0])))))
        strip = wrender.vstack_views(cols)
        wrender.write_png(out + "_thumbs.png", strip)
        written.append(out + "_thumbs.png")

    for p in written:
        print("wrote %s" % p)
    if thumbs:
        print("thumbnail rows, top to bottom: %s"
              % ", ".join("%dpx" % t for t in thumbs))
    print("Read the smallest row first. If it reads as a generic member of "
          "its archetype, the shape is the problem — no amount of surface "
          "detail will fix it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
