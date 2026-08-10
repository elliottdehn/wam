#!/usr/bin/env python3
"""Mirroring: a mirrored pair lands on opposite sides.

    python3 tests/test_mirror.py

A `mirror` block builds both copies against the `.l` bones and reflects the
finished vertices of the second across X=0. That is the entire mechanism, and
anything that *also* pre-negates the aim cancels it. Two places did:
`part_dir_on_bone` negated the x of a bone-relative aim, so `across=`/`aim=`/
`along=` never flipped and both copies landed on the same side; `around_origin`
negated the polar angle, which composed with the vertex reflection into a
reflection about the diagonal, so a mirrored eye came out with its x and z
swapped.

Neither had a test. The plain `dir=` path never pre-negated, which is why
`dir=side` always worked and made the bug look like an authoring trap.
"""
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wam import checks as wchecks  # noqa: E402
from wam import mesh as wmesh  # noqa: E402
from wam import parser as wparser  # noqa: E402
from wam import skeleton as wskel  # noqa: E402

fails = []
TOTAL = 0


def check(label, cond, detail=""):
    global TOTAL
    TOTAL += 1
    print(("PASS  " if cond else "FAIL  ") + label
          + ("\n        " + detail if detail and not cond else ""))
    if not cond:
        fails.append(label)


SRC = """model mirrortest
  height 2.0
  style smooth
palette
  a #999999
skeleton
  root pelvis at 0.5
  bone spine parent=pelvis dir=up len=0.30
parts
  loft core bones=pelvis..spine material=a
    ring 0.00 w=0.10 d=0.10
    ring 1.00 w=0.10 d=0.10
    cap start=dome end=dome
  mirror
    loft byacross bone=spine at=0.40 across=side len=0.25 material=a
      ring 0.00 w=0.04 d=0.04
      ring 1.00 w=0.02 d=0.02
      cap start=none end=dome
  end
  mirror
    loft byaim bone=spine at=0.55 aim=60:side len=0.25 material=a
      ring 0.00 w=0.04 d=0.04
      ring 1.00 w=0.02 d=0.02
      cap start=none end=dome
  end
  mirror
    loft bydir bone=spine at=0.70 dir=side len=0.25 material=a
      ring 0.00 w=0.04 d=0.04
      ring 1.00 w=0.02 d=0.02
      cap start=none end=dome
  end
  mirror
    attach eye bone=spine kind=eye at=0.90 around=40 on=core size=0.03 material=a
  end
checks
  assert x(byacross.l) + x(byacross.r) == 0 +- 0.001
  assert x(byaim.l)    + x(byaim.r)    == 0 +- 0.001
  assert x(bydir.l)    + x(bydir.r)    == 0 +- 0.001
  assert x(eye.l)      + x(eye.r)      == 0 +- 0.001
  assert z(eye.l)      - z(eye.r)      == 0 +- 0.001
"""

TMP = tempfile.mkdtemp(prefix="wammirror")
path = os.path.join(TMP, "mirrortest.wam")
with open(path, "w") as f:
    f.write(SRC)

model = wparser.parse_file(path)
bones, order = wskel.solve(model)
mesh = wmesh.build(model, bones)
V, T, M = mesh.arrays()


def centre(key):
    v0, v1 = mesh.part_ranges[key]
    p = V[v0:v1]
    return (p.min(axis=0) + p.max(axis=0)) / 2


# ---- a mirrored pair is symmetric about X=0, however its aim was stated ----
# `dir=` is the control: it never pre-negated, so it always worked. If it ever
# fails alongside the others the reflection itself has broken, not the aim.
for name, why in (("bydir", "a world aim (the control)"),
                  ("byacross", "a bone-relative `across=`"),
                  ("byaim", "a bone-relative `aim=`"),
                  ("eye", "a polar `around=`")):
    l, r = centre(name + ".l"), centre(name + ".r")
    check("%s mirrors across the centreline — %s" % (name, why),
          abs(l[0] + r[0]) < 1e-3,
          "l.x %+.4f, r.x %+.4f, sum %+.4f" % (l[0], r[0], l[0] + r[0]))
    check("%s keeps its other two axes — %s" % (name, why),
          abs(l[1] - r[1]) < 1e-3 and abs(l[2] - r[2]) < 1e-3,
          "l=(%+.4f,%+.4f,%+.4f) r=(%+.4f,%+.4f,%+.4f)" % (*l, *r))

# The failing case put both copies on the *same* side, which is only visible if
# the pair is actually off-centre to begin with.
bx = centre("byacross.l")
check("the fixture's mirrored parts are genuinely off-centre",
      abs(bx[0]) > 0.05, "x %+.4f" % bx[0])

# ---- and the model's own `checks` block agrees ------------------------------
# Written in the language's own idiom as well as measured here, because a
# mirrored pair is exactly the thing an author should be able to assert about.
failures, _ = wchecks.evaluate(model, bones, mesh, V)
check("the model's own checks pass", not failures,
      "; ".join(str(f) for f in failures))
check("the fixture actually has checks to run", len(model.checks) == 5,
      "%d checks" % len(model.checks))

import shutil  # noqa: E402
shutil.rmtree(TMP, ignore_errors=True)
print("\n%d passed, %d failed" % (TOTAL - len(fails), len(fails)))
sys.exit(1 if fails else 0)
