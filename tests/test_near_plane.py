#!/usr/bin/env python3
"""Near-plane clipping.

    python3 tests/test_near_plane.py

A triangle that straddles the camera plane used to be dropped whole. The
symptom is a piece of geometry silently vanishing for the middle of a push-in
and reappearing afterwards, which no lint catches and which you only notice if
you happen to be watching those frames.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wam import render as wr  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label + (
        "\n        " + detail if detail and not cond else ""))
    if not cond:
        fails.append(label)


# ---- a wall running from behind the camera to well in front of it -----------
V = np.array([[-4.0, -3.0, 6.0], [4.0, -3.0, 6.0],
              [4.0, 3.0, -2.0], [-4.0, 3.0, -2.0]])
T = np.array([[0, 1, 2], [0, 2, 3]])
M = np.array([0, 0])
colors = [(0.9, 0.2, 0.1)]

img = wr.render_view(V, T, M, colors, eye=(0, 0, 0), look=(0, 0, 10),
                     width=200, height=200, fov_deg=60, bg=(1, 1, 1))
drawn = int((img[:, :, 0] - img[:, :, 2] > 0.3).sum())
check("a triangle crossing the near plane still draws its front half",
      drawn > 1000, "%d pixels drawn" % drawn)

# ---- geometry entirely behind the camera must NOT draw ----------------------
Vb = V.copy()
Vb[:, 2] -= 12.0
img = wr.render_view(Vb, T, M, colors, eye=(0, 0, 0), look=(0, 0, 10),
                     width=200, height=200, fov_deg=60, bg=(1, 1, 1))
behind = int((img[:, :, 0] - img[:, :, 2] > 0.3).sum())
check("geometry wholly behind the camera draws nothing",
      behind == 0, "%d pixels drawn" % behind)

# ---- attributes must be interpolated at the cut, not inherited --------------
# Two materials, one per triangle. A clipped corner taking the wrong vertex's
# attributes shows up as the wrong colour bleeding across the seam.
Vc = np.array([[-4.0, -3.0, 6.0], [4.0, -3.0, 6.0],
               [4.0, 3.0, -2.0], [-4.0, 3.0, -2.0]])
Tc = np.array([[0, 1, 2], [0, 2, 3]])
Mc = np.array([0, 1])
two = [(0.9, 0.1, 0.1), (0.1, 0.1, 0.9)]
img = wr.render_view(Vc, Tc, Mc, two, eye=(0, 0, 0), look=(0, 0, 10),
                     width=200, height=200, fov_deg=60, bg=(1, 1, 1))
reds = int((img[:, :, 0] - img[:, :, 2] > 0.3).sum())
blues = int((img[:, :, 2] - img[:, :, 0] > 0.3).sum())
check("each clipped triangle keeps its own material",
      reds > 500 and blues > 500, "red %d, blue %d" % (reds, blues))

# ---- the orbit camera must be untouched -------------------------------------
# Nothing crosses the near plane when the camera is fitted outside the model,
# so this path has to come out exactly as it did before clipping existed.
Vo = np.array([[-1.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 2.0, 0.0]])
To = np.array([[0, 1, 2]])
a = wr.render_view(Vo, To, np.array([0]), colors, width=64, height=64)
b = wr.render_view(Vo, To, np.array([0]), colors, width=64, height=64)
check("orbit renders are deterministic", np.array_equal(a, b))
check("orbit renders draw the model",
      int((a[:, :, 0] - a[:, :, 2] > 0.3).sum()) > 100)

print("\n%d passed, %d failed" % (5 - len(fails), len(fails)))
sys.exit(1 if fails else 0)
