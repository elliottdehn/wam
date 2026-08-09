#!/usr/bin/env python3
"""Cinematic: staging, cameras, and the lint.

    python3 tests/test_cinematic.py

The lint assertions are the point. Every one of them corresponds to a retake
somebody actually shot: a camera under the terrain, geometry through the lens,
a subject too small to read, a subject behind something else, and the edge of
the world in frame.
"""
import os
import shutil
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wam import cinematic as C  # noqa: E402
from wam.parser import WamError  # noqa: E402

fails = []
TOTAL = 0


def check(label, cond, detail=""):
    global TOTAL
    TOTAL += 1
    print(("PASS  " if cond else "FAIL  ") + label
          + ("\n        " + detail if detail and not cond else ""))
    if not cond:
        fails.append(label)


TMP = tempfile.mkdtemp(prefix="wamcine")
MODEL = os.path.join(TMP, "figure.wam")
with open(MODEL, "w") as f:
    f.write("""model figure
  height 2.0
  style chunky
  marker crown at=(0,0.98,0)

palette
  hide #2c3336 rough=0.8

skeleton
  root pelvis at 0.5
  bone spine parent=pelvis dir=up len=0.3

parts
  loft body bones=spine..spine material=hide
    ring 0.00 w=0.30 d=0.30
    ring 1.00 w=0.12 d=0.12
    cap start=dome end=dome

animations
  anim idle loop dur=2.0
    ch spine pitch 0%=0 50%=-10 100%=0
""")


WALL = os.path.join(TMP, "wall.wam")
with open(WALL, "w") as f:
    f.write("""model wall
  height 3.0
  style chunky

palette
  stone #6a6560 rough=0.9

skeleton
  root base at 0.0
  bone post parent=base dir=up len=1.0

parts
  loft slab bones=post..post material=stone
    ring 0.00 w=2.0 d=0.12
    ring 1.00 w=2.0 d=0.12
    cap start=flat end=flat
""")


def cine(body, name="t"):
    path = os.path.join(TMP, name + ".cine")
    with open(path, "w") as f:
        f.write(body)
    return path


def build(name, eye=None, look="figure", fog="fog auto", checks="", extra=""):
    """A one-shot cinematic. Built by concatenation rather than % formatting,
    because a literal % in `0%=(...)` and a format specifier are the same
    character and the first version of this file silently replaced nothing."""
    eye = eye or "eye 0%=(0,1.5,-6) 100%=(0,1.4,-5)"
    return "\n".join([
        "cinematic " + name,
        "  aspect 2.39",
        "  fps 4",
        "  size 160",
        "",
        "scene s",
        "  light elevation=40 azimuth=120",
        "  actor " + MODEL + " at=(0,0,0) anim=idle phase=0 shadow",
        extra,
        "  ground extend",
        "  " + fog,
        "",
        "shot a dur=0.5 scene=s",
        "  " + eye,
        "  look at=" + look,
        "  fov 40",
        checks,
        "",
    ])


def run(body, name):
    out = os.path.join(TMP, "out_" + name)
    _, warns, infos = C.compile_cine(cine(body, name), out, quiet=True)
    return warns, infos, out


CHECKS = "  checks\n    assert clearance > 0.5"

# ---- a clean shot warns about nothing ---------------------------------------
w, i, out = run(build("clean", checks=CHECKS), "clean")
check("a well-framed shot produces no warnings", not w, "; ".join(w))
check("frames are written", os.path.exists(os.path.join(out, "a", "0000.png")))
check("a contact sheet is written", os.path.exists(os.path.join(out, "a_contact.png")))
check("an assembly list is written", os.path.exists(os.path.join(out, "assembly.txt")))

# ---- models are staged in metres, not height-fractions ----------------------
film = C.parse_cine(cine(build("units"), "units"))
cache = {}
sc = C.Scene(film["scenes"]["s"], film, lambda p: cache.setdefault(p, C.Loaded(p)))
staged = sc.subject("figure")
raw = cache[MODEL].V
unit_h = float(raw[:, 1].max() - raw[:, 1].min())
want = unit_h * cache[MODEL].model.height
got = float(staged[:, 1].max() - staged[:, 1].min())
check("a staged model is scaled by its declared height",
      abs(got - want) < 1e-6,
      "staged %.3f, expected unit %.3f x height %.1f = %.3f"
      % (got, unit_h, cache[MODEL].model.height, want))

# ---- the lint catches each retake -------------------------------------------
def warned(body, name, needle):
    w, _, _ = run(body, name)
    return any(needle in x for x in w), w


hit, w = warned(build("under", eye="eye 0%=(0,~-3,-6) 100%=(0,~-3,-5)"),
                "under", "under the ground")
check("camera under the ground is caught", hit, "; ".join(w) or "no warnings")

hit, w = warned(build("through", eye="eye 0%=(0,1,-6) 100%=(0,1,0.4)"),
                "through", "cross the near plane")
check("flying the camera through geometry is caught", hit, "; ".join(w) or "no warnings")

# The subject has to be named for it to be measured — say so by asserting on it.
hit, w = warned(build("offscreen", look="(40,1,0)",
                      checks="  checks\n    assert frames(figure) > 0.01"),
                "offscreen", "leaves frame")
check("a subject leaving frame is caught", hit, "; ".join(w) or "no warnings")

hit, w = warned(build("edge", fog="fog off"), "edge",
                "edge of the ground is in frame")
check("seeing the edge of the world is caught", hit, "; ".join(w) or "no warnings")

w, _, _ = run(build("small", checks="  checks\n    assert frames(figure) > 0.9"), "small")
check("a failing shot check is reported with its measurement",
      any("check failed" in x and "frames(figure)" in x for x in w), "; ".join(w))

# ---- occlusion: "is anything standing in front of this" ---------------------
# An unobstructed subject has to measure exactly 1.0. Anything less and there
# is no threshold an author can write for "not occluded", which is the whole
# point of the measurement.
WALL_LINE = "  place " + WALL + " at=(0,0,-2)"
_, infos, _ = run(build("clearview", checks="  checks\n    assert visible(figure) > 0.999"),
                  "clearview")
clear = [m for m in infos if "visible(figure)" in m]
check("an unobstructed subject is fully visible",
      any(", ok)" in m for m in clear), "; ".join(clear) or "not measured")

hit, w = warned(build("hidden", extra=WALL_LINE,
                      checks="  checks\n    assert visible(figure) > 0.5"),
                "hidden", "is occluded")
check("a subject behind something else is caught", hit, "; ".join(w) or "no warnings")
check("the occlusion check fails with its measurement",
      any("check failed" in x and "visible(figure)" in x for x in w),
      "; ".join(w) or "no warnings")

# The same assertion has to work on a part and on a marker, not just a whole
# model — "or whatever", in the words of the request. `crown` is a marker above
# the head and `body` is a part; both are in the clear.
_, infos, _ = run(build("parts", look="figure.crown",
                        checks="  checks\n"
                               "    assert visible(figure.body) > 0.999\n"
                               "    assert visible(figure.crown) > 0.999"),
                  "parts")
for sel in ("figure.body", "figure.crown"):
    check("visible(%s) resolves and passes" % sel,
          any("visible(%s)" % sel in m and ", ok)" in m for m in infos),
          "; ".join(m for m in infos if sel in m) or "not measured")

# A point buried inside its own model is not visible, and saying so is the
# useful answer — `spine` is a bone whose head sits in the middle of the body.
w, _, _ = run(build("inside", checks="  checks\n    assert visible(figure.spine) > 0.5"),
              "inside")
check("a point inside its own model reads as hidden",
      any("check failed" in x and "visible(figure.spine)" in x for x in w),
      "; ".join(w) or "no warnings")

w, _, _ = run(build("parthidden", extra=WALL_LINE, look="figure.crown",
                    checks="  checks\n    assert visible(figure.body) > 0.5"),
              "parthidden")
check("a part behind something else fails its check",
      any("check failed" in x and "visible(figure.body)" in x for x in w),
      "; ".join(w) or "no warnings")

# ---- unknown targets are errors, not silent no-ops --------------------------
try:
    run(build("ghost", look="nobody"), "ghost")
    check("an unknown look target is an error", False, "no error raised")
except WamError as e:
    check("an unknown look target is an error", "nobody" in str(e))

try:
    run(build("ghostpart", look="figure.elbow"), "ghostpart")
    check("an unknown part of a known model is an error", False, "no error raised")
except WamError as e:
    check("an unknown part of a known model is an error",
          "elbow" in str(e) and "crown" in str(e), str(e))

# ---- the orbit camera --------------------------------------------------------
orb = "\n".join([
    "cinematic orbit", "  aspect 2.39", "  fps 4", "  size 160", "",
    "scene s",
    "  light elevation=40 azimuth=120",
    "  actor " + MODEL + " at=(0,0,0) anim=idle phase=0",
    "  ground extend", "  fog auto", "",
    "shot o dur=0.5 scene=s",
    "  eye orbit around=figure.crown radius=6..4 arc=-40 height=1.5..0.8",
    "  look at=figure.crown",
    "  fov 40", "",
])
w, _, oout = run(orb, "orbit")
check("an orbit camera renders without warnings", not w, "; ".join(w))
check("an orbit camera writes frames",
      os.path.exists(os.path.join(oout, "o", "0000.png")))

film_o = C.parse_cine(cine(orb, "orbit"))
cache_o = {}
sc_o = C.Scene(film_o["scenes"]["s"], film_o, lambda p: cache_o.setdefault(p, C.Loaded(p)))
cam_o = C.Camera(film_o["shots"][0], sc_o)
c = sc_o.target("figure.crown")
r0 = float(np.linalg.norm((cam_o.eye_at(0.0) - c)[[0, 2]]))
r1 = float(np.linalg.norm((cam_o.eye_at(1.0) - c)[[0, 2]]))
check("an orbit closes in over the move",
      abs(r0 - 6) < 1e-6 and abs(r1 - 4) < 1e-6, "%.3f -> %.3f" % (r0, r1))

# ---- animations run at their own speed, not one loop per shot ---------------
# `pose(t)` used to advance the phase by the shot fraction, so a 2s cycle played
# exactly once whether the shot was one second or ten. The symptom is everybody
# moving like they are underwater, and no lint could see it.
film_p = C.parse_cine(cine(build("speed"), "speed"))
cache_p = {}
sc_p = C.Scene(film_p["scenes"]["s"], film_p,
               lambda p: cache_p.setdefault(p, C.Loaded(p)))
anim_dur = cache_p[MODEL].anim_dur("idle")
check("the test model's cycle is longer than the short shot", anim_dur > 1.0)


def spread(seconds):
    """How far the model gets from its opening pose over a shot this long."""
    ref = sc_p.pose(0.0, seconds)[sc_p._subjects["figure"][0]:].copy()
    far = 0.0
    for k in range(1, 9):
        V = sc_p.pose(k / 8.0, seconds)[sc_p._subjects["figure"][0]:]
        far = max(far, float(np.linalg.norm(V - ref, axis=1).max()))
    return far


short, long_ = spread(anim_dur / 4.0), spread(anim_dur)
check("a longer shot plays more of the cycle, not the same cycle slower",
      long_ > short * 1.5, "quarter-cycle %.4f, full cycle %.4f" % (short, long_))

# ---- the measurement vocabulary ----------------------------------------------
VOCAB = ("clearance > 0.1", "clearance(terrain) > 0.1", "lookdist > 1.0",
         "updot < 0.98", "inframe(figure) > 0.5", "centered(figure) in -1..1",
         "headroom(figure) in -1..1", "offscreen(figure) < 0.5",
         "grounded(figure) in -0.2..2.0", "facing(figure, camera) < 361",
         "speed < 100", "accel < 1000", "swing < 90", "roll == 0",
         "travel > 0.5", "moves_px(figure) > 0.0", "cycles(figure) > 0.0",
         "frames_written > 0", "nearcross < 1")
w, infos, _ = run(build("vocab", checks="  checks\n"
                        + "\n".join("    assert " + c for c in VOCAB)), "vocab")
for c in VOCAB:
    lhs = C._lhs_of(c)
    check("check vocabulary: %s" % lhs,
          any("check: %s =" % lhs in m for m in infos),
          "; ".join(w) or "not reported")

# `at N%` names a moment, so "the push-in actually pushes in" is assertable.
w, infos, _ = run(build("phased", eye="eye 0%=(0,1.5,-9) 100%=(0,1.5,-4)",
                        checks="  checks\n"
                               "    assert frames(figure) at 100% > frames(figure) at 0%\n"
                               "    measure grew frames(figure) at 100%"),
                  "phased")
check("a measurement can be taken at a named moment",
      any("frames(figure) at 100% =" in m for m in infos), "; ".join(w))
check("and a push-in can assert that it pushes in", not w, "; ".join(w))

# ---- a dead animation is caught before the frames are rendered ---------------
hit, w = warned(build("still", eye="eye 0%=(0,1.5,-400) 100%=(0,1.5,-399)"),
                "still", "barely moves")
check("an animation too small to read at this framing is caught", hit,
      "; ".join(w) or "no warnings")

NOANIM = build("locked").replace("anim=idle phase=0", "anim=idle phase=0")
hit, w = warned("\n".join([
    "cinematic locked", "  aspect 2.39", "  fps 4", "  size 160", "",
    "scene s", "  light elevation=40 azimuth=120",
    "  place " + MODEL + " at=(0,0,0)", "  ground extend", "  fog auto", "",
    "shot a dur=1.0 scene=s", "  eye 0%=(0,1.5,-6)", "  look at=figure",
    "  fov 40", ""]), "locked", "nothing moves")
check("a locked-off shot of nothing is caught", hit, "; ".join(w) or "no warnings")

# ---- determinism and resume --------------------------------------------------
_, _, o1 = run(build("det1"), "det1")
first = open(os.path.join(o1, "a", "0000.png"), "rb").read()
shutil.rmtree(o1)
_, _, o2 = run(build("det1"), "det1")
check("same file, same frames",
      first == open(os.path.join(o2, "a", "0000.png"), "rb").read())

_, warns2, infos = C.compile_cine(cine(build("det1"), "det1"), o2, quiet=True)
check("a second run skips shots that are already rendered",
      any("skipping" in m for m in infos))

shutil.rmtree(TMP, ignore_errors=True)
print("\n%d passed, %d failed" % (TOTAL - len(fails), len(fails)))
sys.exit(1 if fails else 0)
