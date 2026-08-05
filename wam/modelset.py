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


class ModelSet:
    def __init__(self):
        self.name = "set"
        self.entries = []          # (alias, path)
        self.checks = []
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
        if kw in ("models", "checks"):
            section = kw
            continue
        if section == "models":
            if len(tokens) != 2:
                raise wparser.WamError("models entry: <alias> <path.wam>",
                                       line_no, line)
            ms.entries.append((tokens[0], os.path.join(base, tokens[1])))
        elif section == "checks":
            ms.checks.append(wparser.parse_check(kw, line, tokens, line_no))
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

    return {
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
