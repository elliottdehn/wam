#!/usr/bin/env python3
"""The `cinematic` acceptance test, run end to end.

    python3 tests/test_acceptance_cine.py

This is the criterion the feature was requested against, in its own words: the
file compiles to frames with zero warnings, and deliberately breaking it —
camera under terrain, push through the table, look target occluded — produces
a named, measured warning instead of a surreal render.

It is slower than `test_cinematic.py` because it compiles a real zone. That is
the point: the fast suite stages onto a flat floor, and "under the terrain"
means something different when there is terrain.
"""
import os
import shutil
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wam import cinematic as C  # noqa: E402
from wam import zone as Z  # noqa: E402

fails = []
TOTAL = 0


def check(label, cond, detail=""):
    global TOTAL
    TOTAL += 1
    print(("PASS  " if cond else "FAIL  ") + label
          + ("\n        " + detail if detail and not cond else ""))
    if not cond:
        fails.append(label)


TMP = tempfile.mkdtemp(prefix="wamaccept")


def put(name, text):
    path = os.path.join(TMP, name)
    with open(path, "w") as f:
        f.write(text)
    return path


ZONE = put("vale.zone", """zone vale
  size 260 200
camera at=(0,-70) look=(0,40) height=6

textures
  texture grass base=#6f7c43 noise=#5b6a38 scale=9
  texture rock  base=#7b7369 noise=#645d55 scale=6

terrain
  base height=3
  rim height=40 width=52 wobble=14
  hill at=(0,40) radius=44 height=14

surface
  grass
  rock where slope>30
""")

KNIGHT = put("knight.wam", """model knight
  height 1.9
  style chunky

palette
  plate #4a5058 rough=0.5
  cloth #7d3b32 rough=0.9

skeleton
  root pelvis at 0.52
  bone spine parent=pelvis dir=up len=0.16
  bone chest parent=spine dir=up len=0.14
  bone neck  parent=chest dir=up len=0.05
  bone head  parent=neck  dir=up len=0.11
  mirror
    bone thigh parent=pelvis side=0.055 dir=down len=0.26
    bone shin  parent=thigh dir=down len=0.26
  end

parts
  loft torso bones=pelvis..neck material=plate
    ring 0.00 w=0.20 d=0.14
    ring 0.55 w=0.24 d=0.16
    ring 1.00 w=0.17 d=0.12
    cap start=dome end=dome
  loft skull bones=head..head material=plate
    ring 0.00 w=0.14 d=0.15
    ring 1.00 w=0.13 d=0.14
    cap start=dome end=dome
  mirror
    loft leg bones=thigh..shin material=cloth
      ring 0.00 w=0.13 d=0.13
      ring 1.00 w=0.09 d=0.10
      cap start=dome end=dome

animations
  anim strike loop dur=1.4
    ch chest yaw 0%=-14 50%=16 100%=-14
    ch head  yaw 0%=8   50%=-8 100%=8
""")

# The thing the camera flies through. Wide, flat, and directly in the way,
# which is exactly the shape that used to vanish instead of clipping.
TABLE = put("table.wam", """model table
  height 0.95
  style chunky

palette
  oak #6b4f33 rough=0.85

skeleton
  root foot at 0.0
  bone post parent=foot dir=up len=1.0

parts
  loft top bones=post..post material=oak
    ring 0.88 w=2.6 d=1.1
    ring 1.00 w=2.6 d=1.1
    cap start=flat end=flat
""")

CROWN = put("crown.wam", """model crown
  height 2.4
  style chunky
  marker break at=(0.46,0.5,0)

palette
  gold #b08a3c rough=0.35

skeleton
  root hub at 0.5
  bone arc parent=hub dir=up len=0.5

parts
  loft band bones=arc..arc material=gold
    ring 0.00 w=1.0 d=1.0
    ring 1.00 w=0.9 d=0.9
    cap start=flat end=flat
""")


def film(body, name):
    return C.compile_cine(put(name + ".cine", body),
                          os.path.join(TMP, "out_" + name), quiet=True)


print("compiling the zone (this is the slow part) ...")
Z.compile_zone(ZONE, os.path.join(TMP, "out", "vale"))


def scene(extra="", fog="  fog auto", pitch="pitch=90"):
    return "\n".join([
        "scene s",
        "  light elevation=38 azimuth=130",
        "  zone " + ZONE,
        "  place " + CROWN + " at=(0,9,12) " + pitch + " scale=3",
        "  actor " + KNIGHT + " at=(-1,0,-8) yaw=95 anim=strike phase=0.0 shadow",
        "  actor " + KNIGHT + " at=(1.4,0,-6.8) yaw=62 anim=strike phase=0.45"
        " as=second shadow",
        extra,
        fog,
    ])


HEAD = "cinematic acceptance\n  aspect 2.39\n  fps 6\n  size 320\n"

# Built from parts rather than by editing a template string. An earlier version
# of this file broke each variant with .replace() and two of the three silently
# matched nothing, so three "detected" cases were the clean scene passing.
def shot(eye, look="crown", checks=(), name="a", extra="", pitch="pitch=90"):
    body = [HEAD, scene(extra=extra, pitch=pitch), "",
            "shot %s dur=1.2 scene=s" % name, "  " + eye,
            "  look at=" + look, "  fov 40"]
    if checks:
        body.append("  checks")
        body += ["    assert " + c for c in checks]
    return "\n".join(body + [""])


CLEAN_EYE = "eye 0%=(0,8,-18) 100%=(0,6,-12) ease=smooth"
CLEAN = shot(CLEAN_EYE, checks=("frames(crown) > 0.25", "clearance > 1.0",
                                "visible(crown) > 0.9"))

# ---- the file itself ---------------------------------------------------------
_, warns, infos = film(CLEAN, "clean")
check("the acceptance file compiles with zero warnings", not warns,
      "; ".join(warns))
check("it renders frames",
      os.path.exists(os.path.join(TMP, "out_clean", "a", "0000.png")))
check("it says what it measured",
      any("occupies" in m for m in infos) and any("clears geometry" in m for m in infos),
      "; ".join(infos))
# `as=` gives the second copy of a model a name of its own, so it can be aimed
# at and measured instead of being shadowed by the first.
_, warns_s, infos_s = film(
    shot("eye 0%=(0,3,-9) 100%=(0,3,-8)", look="second",
         checks=("visible(second) > 0.9",), name="two"), "second")
check("`as=` names the second copy of a model so it can be measured",
      any("visible(second)" in m and ", ok)" in m for m in infos_s),
      "; ".join(warns_s + [m for m in infos_s if "second" in m]) or "not measured")

# Two copies and one name between them is not resolved silently.
DUPES = "\n".join([HEAD, scene().replace(" as=second", ""), "",
                   "shot a dur=1.2 scene=s", "  " + CLEAN_EYE,
                   "  look at=crown", "  fov 40", ""])
check("the duplicate-name fixture really does drop the alias",
      "as=second" not in DUPES)
_, _, infos_d = film(DUPES, "dupes")
check("two models answering to one name is called out",
      any("answer to 'knight'" in m for m in infos_d), "; ".join(infos_d))

# ---- full orientation, which is what replaced the composite hack -------------
def crown_box(pitch):
    f = C.parse_cine(put("orient_%s.cine" % pitch.replace("=", ""),
                         "\n".join([HEAD, scene(pitch=pitch), "",
                                    "shot a dur=0.2 scene=s",
                                    "  eye 0%=(0,8,-18)", "  look at=crown",
                                    "  fov 40", ""])))
    cache = {}
    sc = C.Scene(f["scenes"]["s"], f, lambda p: cache.setdefault(p, C.Loaded(p)))
    V = sc.subject("crown")
    return V.max(axis=0) - V.min(axis=0)


up, flat = crown_box("pitch=0"), crown_box("pitch=90")
# Tipping the ring onto its side swaps its height with its depth. Yaw alone
# could never do this, and hanging a vertical ring flat over a city is what
# used to need a numpy rotation and a sky-difference composite.
check("a placed model can be pitched flat, not just yawed",
      abs(flat[1] - up[2]) < 1e-6 and abs(flat[2] - up[1]) < 1e-6
      and abs(up[1] - up[2]) > 1.0,
      "upright extents %s, pitched extents %s"
      % (np.round(up, 3), np.round(flat, 3)))

# ---- break it three ways -----------------------------------------------------
_, warns, _ = film(shot("eye 0%=(0,~-4,-18) 100%=(0,~-4,-12)"), "under")
check("camera under the terrain is named and measured",
      any("under the ground" in w and "m at its deepest" in w for w in warns),
      "; ".join(warns) or "no warnings")

_, warns, _ = film(shot("eye 0%=(0,1.0,-20) 100%=(0,1.0,-11.6) ease=linear",
                        extra="  place " + TABLE + " at=(0,0,-12)"), "through")
check("pushing through the table is named and measured",
      any("cross the near plane" in w and "triangle(s)" in w for w in warns),
      "; ".join(warns) or "no warnings")

_, warns, _ = film(
    shot("eye 0%=(0,8,-18) 100%=(0,7,-16) ease=smooth",
         extra="  place " + TABLE + " at=(0,7,-14) pitch=90 scale=4",
         checks=("visible(crown) > 0.9",)), "occluded")
check("an occluded look target is named and measured",
      any("is occluded" in w and "hidden at" in w for w in warns),
      "; ".join(warns) or "no warnings")
check("and the occlusion assertion fails with its number",
      any("check failed" in w and "visible(crown)" in w for w in warns),
      "; ".join(warns) or "no warnings")

# ---- a marker is aimable, which is what replaced the ember-triangle scan -----
FRACTURE = "\n".join([
    HEAD, scene(), "",
    "shot f dur=1.2 scene=s",
    "  eye orbit around=crown.break radius=22..17 arc=-12 height=9..5",
    "  look at=crown.break",
    "  fov 34",
    "  checks",
    "    assert clearance > 1.0",
    # `break` is a Python keyword and the check grammar rides on ast.parse.
    # Aiming at the marker never went through the parser, so only asserting on
    # it exercises the path that used to crash.
    "    measure fracture_seen visible(crown.break)", ""])
_, warns, infos = film(FRACTURE, "fracture")
check("a marker can be orbited and aimed at", not warns, "; ".join(warns))
check("and measured, despite being named after a Python keyword",
      any("fracture_seen =" in m for m in infos), "; ".join(infos))
check("the marker shot renders",
      os.path.exists(os.path.join(TMP, "out_fracture", "f", "0000.png")))

shutil.rmtree(TMP, ignore_errors=True)
print("\n%d passed, %d failed" % (TOTAL - len(fails), len(fails)))
sys.exit(1 if fails else 0)
