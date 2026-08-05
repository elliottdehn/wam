"""Cross-model checks: hold a whole cast to one scale.

A model's own `checks` section can only ever see itself, so the properties
that only exist *between* models have nowhere to live: that an ogre is half
again a human, that no two silhouettes read the same at distance, that every
unit in a set shares a poly budget. Those get discovered when the models
finally stand in one scene, which is far too late.

A `.wamset` file names a set of models and asserts over them together:

    set bestiary

    models
      human   models/human.wam
      ogre    models/ogre.wam
      kodo    models/kodo.wam

    checks
      assert height(ogre) / height(human) in 1.4..1.8
      assert length(kodo) > height(kodo)
      assert fill(human) > 0.9
      measure tallest max(height(ogre), height(kodo))

**Dimensions here are meters, not the height-fractions used inside a model.**
That distinction is the whole point of the file. A model declares a `height`
in meters and expresses every length as a fraction of it, but the geometry
does not necessarily fill that unit: a model declaring 2.35 m whose bbox
spans 0.92 of a unit really stands 2.17 m tall. Comparing declared heights
would call that a 2.35 and be wrong by 8%, and the error compounds against
the next model, which is exactly the drift nobody notices until two units
are side by side.
"""
import argparse
import os
import sys

import numpy as np

from . import checks as wchecks
from . import mesh as wmesh
from . import parser as wparser
from . import skeleton as wskel


SIL_VIEWS = {"front": 0.0, "threequarter": 38.0, "side": 90.0, "back": 180.0}
SIL_H, SIL_W = 128, 256


def silhouette_mask(V, T, yaw_deg, res_h=SIL_H, res_w=SIL_W):
    """Binary silhouette of a model, normalized for size but not for shape.

    Each model is scaled so its projected height is 1 and stood on the same
    ground line, then rasterized orthographically. Absolute size is removed
    because `height()` already checks that; aspect ratio is deliberately
    *kept*, because a tall narrow unit and a squat wide one are exactly the
    pair you want to read apart at distance.
    """
    a = np.radians(yaw_deg)
    ca, sa = np.cos(a), np.sin(a)
    x = V[:, 0] * ca - V[:, 2] * sa            # orthographic: no perspective,
    y = V[:, 1]                                # so distance cannot skew it
    h = float(y.max() - y.min())
    if h < 1e-9:
        return np.zeros((res_h, res_w), dtype=bool)
    sx = (x - 0.5 * (x.max() + x.min())) / h
    sy = (y - y.min()) / h
    px = (sx + 1.0) * 0.5 * res_w              # x spans [-1, 1]
    py = (1.0 - sy) * res_h                    # y spans [0, 1], ground at foot
    mask = np.zeros((res_h, res_w), dtype=bool)
    for (i, j, k) in T:
        xs = np.array([px[i], px[j], px[k]])
        ys = np.array([py[i], py[j], py[k]])
        x0, x1 = int(max(0, np.floor(xs.min()))), int(min(res_w - 1, np.ceil(xs.max())))
        y0, y1 = int(max(0, np.floor(ys.min()))), int(min(res_h - 1, np.ceil(ys.max())))
        if x1 < x0 or y1 < y0:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5,
                             np.arange(y0, y1 + 1) + 0.5)
        d = ((ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2]))
        if abs(d) < 1e-9:
            continue
        w0 = ((ys[1] - ys[2]) * (gx - xs[2]) + (xs[2] - xs[1]) * (gy - ys[2])) / d
        w1 = ((ys[2] - ys[0]) * (gx - xs[2]) + (xs[0] - xs[2]) * (gy - ys[2])) / d
        w2 = 1 - w0 - w1
        hit = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        if hit.any():
            mask[y0:y1 + 1, x0:x1 + 1] |= hit
    return mask


class ModelSet:
    def __init__(self):
        self.name = "set"
        self.entries = []          # (alias, path)
        self.checks = []
        self.every = []            # checks run inside each member's namespace
        self.source_path = None


def parse(text, path=None):
    ms = ModelSet()
    ms.source_path = path
    base = os.path.dirname(path or ".")
    section = None
    for line_no, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        tokens = line.split()
        kw = tokens[0]
        if kw == "set":
            if len(tokens) < 2:
                raise wparser.WamError("set needs a name", line_no, line)
            ms.name = tokens[1]
            section = "set"
            continue
        if kw in ("models", "checks", "every"):
            section = kw
            continue
        if section == "models":
            if len(tokens) != 2:
                raise wparser.WamError("models entry: <alias> <path.wam>",
                                       line_no, line)
            ms.entries.append((tokens[0], os.path.join(base, tokens[1])))
        elif section in ("checks", "every"):
            target = ms.checks if section == "checks" else ms.every
            target.append(wparser.parse_check(kw, line, tokens, line_no))
        else:
            raise wparser.WamError("directive before any section", line_no, line)
    return ms


def parse_file(path):
    with open(path) as f:
        return parse(f.read(), path)


class Compiled:
    """One compiled model, plus the meters view of it."""

    def __init__(self, alias, model, bones, mesh):
        self.alias = alias
        self.model = model
        self.bones = bones
        self.mesh = mesh
        self.V, self.T, self.M = mesh.arrays()
        self.scale = float(model.height)       # declared meters per unit
        self.env = wchecks.Env(model, bones, mesh, self.V)
        self._masks = {}

    def mask(self, yaw):
        if yaw not in self._masks:
            self._masks[yaw] = silhouette_mask(self.V, self.T, yaw)
        return self._masks[yaw]

    def extent(self, axis, verts=None):
        v = self.V if verts is None else verts
        return float(v[:, axis].max() - v[:, axis].min()) * self.scale


def build_functions(models):
    """The cross-model vocabulary. Every length is in meters."""

    def resolve(ref):
        """`ogre` -> whole model; `ogre.hoof.l` -> that part inside it."""
        name = wchecks._name_of(ref)
        head, _, rest = name.partition(".")
        if head not in models:
            raise wchecks.CheckError(
                "no model named %r in this set (have: %s)"
                % (head, ", ".join(sorted(models))))
        return models[head], rest or None

    def verts_of(ref):
        c, part = resolve(ref)
        if part is None:
            return c, c.V
        return c, c.env.part_verts(wchecks._Ref(part))

    def axis(ref, i):
        c, v = verts_of(ref)
        return c.extent(i, v)

    def counts(ref, what):
        c, part = resolve(ref)
        if part is not None:
            raise wchecks.CheckError("%s() takes a model, not a part" % what)
        if what == "tris":
            return float(len(c.T))
        if what == "verts":
            return float(len(c.V))
        if what == "materials":
            return float(len(c.mesh.materials))
        if what == "bonecount":
            return float(len(c.bones))
        return float(len(c.mesh.part_ranges))

    def ground(ref):
        c, v = verts_of(ref)
        return float(v[:, 1].min()) * c.scale

    def top(ref):
        c, v = verts_of(ref)
        return float(v[:, 1].max()) * c.scale

    def declared(ref):
        c, _ = resolve(ref)
        return c.scale

    def fill(ref):
        """Fraction of its declared height the geometry actually spans.

        A model that reads 0.85 here is 15% shorter than it claims, and every
        cross-model ratio computed from its declared height is wrong by that
        much. Worth asserting near 1.0 on every model in a set.
        """
        c, _ = resolve(ref)
        return float(c.V[:, 1].max() - min(c.V[:, 1].min(), 0.0))

    def silhouette(a, b, view=None):
        """How distinguishable two models are by outline alone, 0..1.

        1 - IoU of their normalized silhouettes: 0 means the same outline,
        and larger means easier to tell apart at a glance. This is the check
        no single model can make about itself — two units can each be fine
        and still be indistinguishable at fifty metres.
        """
        name = wchecks._name_of(view) if view is not None else "threequarter"
        if name not in SIL_VIEWS:
            raise wchecks.CheckError(
                "silhouette view must be one of %s" % "|".join(sorted(SIL_VIEWS)))
        yaw = SIL_VIEWS[name]
        ca, _ = resolve(a)
        cb, _ = resolve(b)
        ma, mb = ca.mask(yaw), cb.mask(yaw)
        union = int((ma | mb).sum())
        if not union:
            return 0.0
        return 1.0 - int((ma & mb).sum()) / union

    return {
        "silhouette": silhouette,
        "height": lambda r: axis(r, 1),
        "width": lambda r: axis(r, 0),
        "depth": lambda r: axis(r, 2),
        "length": lambda r: axis(r, 2),
        "span": lambda r: max(axis(r, i) for i in range(3)),
        "ground": ground,
        "top": top,
        "declared": declared,
        "fill": fill,
        "tris": lambda r: counts(r, "tris"),
        "verts": lambda r: counts(r, "verts"),
        "materials": lambda r: counts(r, "materials"),
        "bonecount": lambda r: counts(r, "bonecount"),
        "parts": lambda r: counts(r, "parts"),
        "abs": lambda v: abs(wchecks._number(v)),
        "min": lambda *vs: min(wchecks._number(v) for v in vs),
        "max": lambda *vs: max(wchecks._number(v) for v in vs),
    }


def compile_set(path, quiet=False):
    ms = parse_file(path)
    models = {}
    for alias, mpath in ms.entries:
        if alias in models:
            raise wparser.WamError("duplicate model alias %r" % alias)
        model = wparser.parse_file(mpath)
        bones, _ = wskel.solve(model)
        mesh = wmesh.build(model, bones)
        models[alias] = Compiled(alias, model, bones, mesh)

    funcs = build_functions(models)
    scalars = {"pi": float(np.pi), "models": float(len(models))}
    failures, measurements = wchecks.run_checks(ms.checks, scalars, funcs)

    # `every` runs ordinary model-level checks inside each member's own
    # namespace, so shared conventions live in one file instead of being
    # copy-pasted into the top of every model's `checks` section.
    for alias in sorted(models):
        c = models[alias]
        _, mscalars, mfuncs, noclip = wchecks.namespace(
            c.model, c.bones, c.mesh, c.V)
        f, m = wchecks.run_checks(ms.every, mscalars, mfuncs, noclip=noclip)
        failures.extend("[%s] %s" % (alias, x) for x in f)
        measurements.extend("[%s] %s" % (alias, x) for x in m)

    if not quiet:
        for alias in sorted(models):
            c = models[alias]
            print("info: %-16s %5.2f m tall, %5.2f wide, %5.2f long "
                  "(declared %.2f, fills %.0f%%)"
                  % (alias, c.extent(1), c.extent(0), c.extent(2), c.scale,
                     100.0 * funcs["fill"](wchecks._Ref(alias))))
        for f in failures:
            print("WARN: check failed — %s" % f)
        for m in measurements:
            print("info: check: %s" % m)
    return ms, models, failures


def main(argv=None):
    ap = argparse.ArgumentParser(prog="wam.modelset")
    ap.add_argument("input", help="a .wamset file")
    args = ap.parse_args(argv)
    try:
        _, _, failures = compile_set(args.input)
    except wparser.WamError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
