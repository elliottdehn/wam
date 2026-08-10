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
  marker break at=(0,1.05,0)

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

# ---- pixels and the scene palette -------------------------------------------
# The renderer shades and fogs, so a lit surface never lands on its palette
# RGB exactly. The distance is still small and the wrong material is far away,
# which is what makes a bound writable — and `hex` is there to calibrate it.
_, infos, _ = run(build("colour", checks="  checks\n"
                        "    assert color(0.5, 0.55, hide) < 0.2\n"
                        "    assert color(0.05, 0.05, sky.top) < 0.05\n"
                        "    assert color(0.05, 0.05, hide) > 0.3\n"
                        "    measure centre hex(0.5, 0.55)\n"
                        "    measure corner hex(0.05, 0.05) at 100%"),
                  "colour")
check("a pixel can be measured against a scene palette colour",
      any("color(0.5, 0.55, hide)" in m and ", ok)" in m for m in infos),
      "; ".join(m for m in infos if "color(" in m) or "not measured")
check("sky is nameable too",
      any("color(0.05, 0.05, sky.top)" in m and ", ok)" in m for m in infos),
      "; ".join(m for m in infos if "sky.top" in m) or "not measured")
check("and the wrong colour is far away",
      any("color(0.05, 0.05, hide)" in m and ", ok)" in m for m in infos),
      "; ".join(m for m in infos if "0.05, hide" in m) or "not measured")
corner = [m for m in infos if "corner = " in m]
check("a pixel reads out as hex at a named moment",
      len(corner) == 1 and corner[0].strip().endswith(("0", "1", "2", "3", "4",
                                                       "5", "6", "7", "8", "9",
                                                       "a", "b", "c", "d", "e",
                                                       "f"))
      and corner[0].count("#") == 1, "; ".join(corner) or "not measured")
centre = [m for m in infos if "centre = " in m]
check("and as the whole strip when no moment is named",
      len(centre) == 1 and centre[0].count("#") == C.LINT_PHASES,
      "; ".join(centre) or "not measured")

# A colour name the scene does not have is an error that lists what it does.
try:
    run(build("badcolour", checks="  checks\n"
              "    assert color(0.5, 0.5, chartreuse) < 0.1"), "badcolour")
    check("an unknown palette colour is an error", False, "no error raised")
except WamError as e:
    check("an unknown palette colour is an error",
          "chartreuse" in str(e) and "hide" in str(e), str(e))

# Arithmetic on a colour is a mistake worth naming.
try:
    run(build("hexmath", checks="  checks\n"
              "    assert hex(0.5, 0.5) * 2 > 1"), "hexmath")
    check("arithmetic on a colour is refused", False, "no error raised")
except WamError as e:
    check("arithmetic on a colour is refused", "colour, not a number" in str(e),
          str(e))

# ---- names Python happens to reserve ----------------------------------------
# `crown.break` is the canonical marker example in CINEMATIC_SPEC.md, and it
# crashed the evaluator: the check grammar rides on ast.parse and `.break` is a
# syntax error. Aiming at one was always fine, so the fixture that declared a
# `break` marker never caught it — only putting it inside a check does.
_, infos, _ = run(build("kw", look="figure.break",
                        checks="  checks\n"
                               "    assert visible(figure.break) > 0.999\n"
                               "    assert frames(figure.break) < 0.01\n"
                               "    measure seen visible(figure.break)"),
                  "kw")
check("a marker named after a Python keyword can be asserted on",
      any("visible(figure.break)" in m and ", ok)" in m for m in infos),
      "; ".join(m for m in infos if "break" in m) or "not measured")
check("and it can be aimed at",
      any("seen = 1.0000" in m for m in infos), "; ".join(infos))

# The same hazard in the leading position: an instance can be given as=break.
_, infos, _ = run("\n".join([
    "cinematic kw2", "  aspect 2.39", "  fps 4", "  size 160", "",
    "scene s", "  light elevation=40 azimuth=120",
    "  actor " + MODEL + " at=(0,0,0) anim=idle phase=0 as=break",
    "  ground extend", "  fog auto", "",
    "shot a dur=0.5 scene=s", "  eye 0%=(0,1.5,-6) 100%=(0,1.4,-5)",
    "  look at=break", "  fov 40",
    "  checks", "    assert visible(break) > 0.999",
    "    assert visible(break.body) > 0.999", ""]), "kw2")
check("an instance named after a keyword can be asserted on",
      any("visible(break)" in m and ", ok)" in m for m in infos),
      "; ".join(m for m in infos if "break" in m) or "not measured")
check("including a part beneath it",
      any("visible(break.body)" in m and ", ok)" in m for m in infos),
      "; ".join(m for m in infos if "break.body" in m) or "not measured")

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

# ---- a scene can say what the sky and the air are like ----------------------
# `#rrggbb` had to stop being a comment for any of this to be sayable.
film_s = C.parse_cine(cine("\n".join([
    "cinematic dusk", "  aspect 2.39", "  fps 4", "  size 160", "",
    "scene s",
    "  sky top=#1b2740 horizon=#c4784a   # dusk, and this really is a comment",
    "  fog color=#2a2f42 start=30 end=260 max=0.7",
    "  actor " + MODEL + " at=(0,0,0) anim=idle phase=0",
    "  ground extend", "",
    "shot a dur=0.5 scene=s", "  eye 0%=(0,1.5,-6)", "  look at=figure",
    "  fov 40", ""]), "dusk"))
cache_s = {}
sc_s = C.Scene(film_s["scenes"]["s"], film_s,
               lambda p: cache_s.setdefault(p, C.Loaded(p)))
check("a scene can name its own sky",
      C.to_hex(sc_s.sky[0]) == "#1b2740" and C.to_hex(sc_s.sky[1]) == "#c4784a",
      "%s / %s" % (C.to_hex(sc_s.sky[0]), C.to_hex(sc_s.sky[1])))
check("a trailing comment is still a comment", film_s["name"] == "dusk",
      film_s["name"])
check("a scene can name its own fog",
      sc_s.fog is not None and C.to_hex(sc_s.fog["color"]) == "#2a2f42"
      and sc_s.fog["start"] == 30 and sc_s.fog["end"] == 260
      and sc_s.fog["max"] == 0.7, str(sc_s.fog))
check("and that fog is in the palette",
      sc_s.palette.get("fog") is not None
      and C.to_hex(sc_s.palette["fog"]) == "#2a2f42", str(sc_s.palette.get("fog")))

# ---- the lens can move ------------------------------------------------------
film_f = C.parse_cine(cine(build("lens", eye="eye 0%=(0,1.5,-6) 100%=(0,1.5,-6)")
                           .replace("  fov 40", "  fov 28..62"), "lens"))
cam_f = C.Camera(film_f["shots"][0], sc_s)
check("a bare fov holds", C.Camera(C.parse_cine(cine(build("hold"), "hold"))
                                   ["shots"][0], sc_s).fov_at(0.5) == 40)
check("`a..b` swings the lens across the shot",
      abs(cam_f.fov_at(0.0) - 28) < 1e-9 and abs(cam_f.fov_at(1.0) - 62) < 1e-9
      and 28 < cam_f.fov_at(0.5) < 62,
      "%.2f -> %.2f -> %.2f" % (cam_f.fov_at(0), cam_f.fov_at(0.5), cam_f.fov_at(1)))

w, infos_z, _ = run(build("zoom", eye="eye 0%=(0,1.5,-6) 100%=(0,1.5,-6)",
                          checks="  checks\n"
                                 "    assert frames(figure) at 0% > frames(figure) at 100%")
                    .replace("  fov 40", "  fov 28..62"), "zoom")
check("and the framing follows the lens, not just the camera",
      any("frames(figure) at 0%" in m and ", ok)" in m for m in infos_z),
      "; ".join(w) or "not measured")
# A locked camera whose lens is moving does not render identical frames.
check("a zoom is not a still", not any("nothing moves" in x for x in w),
      "; ".join(w))

# ---- atlas bands: both coordinates get remapped -----------------------------
# Only v was, which is invisible while every contributor is the same width. A
# zone bakes 4096 and a model bakes 1024, so a staged model's u still spanned
# the whole sheet and three quarters of every chart sampled empty space.
class _FakeScene(C.Scene):
    def __init__(self):
        self.colors = []
        self.uv_rows = []


wide = np.zeros((64, 400, 3), dtype=np.float32)
narrow = np.zeros((32, 100, 3), dtype=np.float32)
Vq = np.zeros((4, 3))
Tq = np.array([[0, 1, 2]])
uvq = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
fs = _FakeScene()
fs._merge([(Vq, Tq, np.zeros(1, int), uvq, wide, None),
           (Vq, Tq, np.zeros(1, int), uvq, narrow, None)])
check("a merged atlas is as wide as its widest contributor",
      fs.atlas.shape[1] == 400, str(fs.atlas.shape))
check("a narrower chart's u is remapped into the band it actually occupies",
      abs(fs.uv[4:, 0].max() - 100 / 400) < 1e-9,
      "u max %.4f, expected %.4f" % (fs.uv[4:, 0].max(), 100 / 400))
check("and the widest contributor's u is left alone",
      abs(fs.uv[:4, 0].max() - 1.0) < 1e-9, "u max %.4f" % fs.uv[:4, 0].max())

# ---- an asserted subject stands the ambient warning down --------------------
# A reveal that begins on the back of someone's head is a shot working as
# written, and asserting that it *is* hidden at 0% was itself what warned.
w, _, _ = run(build("reveal", extra=WALL_LINE,
                    checks="  checks\n    assert visible(figure) at 0% < 0.25"),
              "reveal")
check("asserting a subject is hidden at a moment silences the occlusion warning",
      not any("is occluded" in x for x in w), "; ".join(w))

w, _, _ = run(build("stillwarns", extra=WALL_LINE,
                    checks="  checks\n    assert clearance > 0.1"), "stillwarns")
check("and without that assertion it still speaks",
      any("is occluded" in x for x in w), "; ".join(w) or "no warnings")

w, _, _ = run(build("bare", extra=WALL_LINE,
                    checks="  checks\n    assert visible(figure) > 0.0"), "bare")
check("a bare assertion does not silence it, because it means the worst frame",
      any("is occluded" in x for x in w), "; ".join(w) or "no warnings")

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
