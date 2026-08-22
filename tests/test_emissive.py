#!/usr/bin/env python3
"""Emissive materials: `emit=` on a palette colour.

    python3 tests/test_emissive.py

Emission scales the material's own colour and is added *after* shading, so a
declared glow survives the side facing away from the key light. That is the
whole reason to declare it, and it is the property that a naive
implementation -- folding emission into the shade term -- silently loses.

The factor has to reach everything that can show it: the sheet renderer, the
glTF, the standalone viewer, and the cinematic renderer. A setting the author
cannot see is a trap, and the film is where a glow matters most.
"""
import json
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wam import cli as wcli  # noqa: E402
from wam import color as wcolor  # noqa: E402
from wam import cinematic as wcine  # noqa: E402
from wam import parser as wparser  # noqa: E402
from wam import viewer_export as wviewer  # noqa: E402

fails = []
TOTAL = 0


def check(label, cond, detail=""):
    global TOTAL
    TOTAL += 1
    print(("PASS  " if cond else "FAIL  ") + label
          + ("\n        " + detail if detail and not cond else ""))
    if not cond:
        fails.append(label)


def model_src(rune="#ffcc33 emit=0.8"):
    return """model emitfixture
  height 2.0
palette
  shell #444a55
  rune  %s
skeleton
  root pelvis at 0.025
  bone spine parent=pelvis dir=up len=0.40
parts
  loft core bones=pelvis..spine material=shell
    ring 0.00 w=0.16 d=0.14
    ring 1.00 w=0.13 d=0.11
    cap start=dome end=dome
  loft gem bone=spine at=0.6 dir=fwd len=0.14 material=rune
    ring 0.00 w=0.06 d=0.06
    ring 1.00 w=0.04 d=0.04
    cap start=dome end=dome
animations
  anim idle loop dur=1.0
    ch spine pitch 0%%=0 100%%=0
""" % rune


TMP = tempfile.mkdtemp(prefix="wamemit")


def write(name, src):
    path = os.path.join(TMP, name)
    with open(path, "w") as handle:
        handle.write(src)
    return path


def compile_to(src, prefix, **kw):
    path = write(os.path.basename(prefix) + ".wam", src)
    report = {}
    wcli.compile_model(path, os.path.join(TMP, prefix), "front", quiet=True,
                       width=200, height=260, report=report, **kw)
    return path, report


# ---- the parser accepts it, and rejects what it should ---------------------
model = wparser.parse_file(write("parse.wam", model_src()))
check("emit= is parsed onto the material",
      model.material_pbr["rune"]["emit"] == 0.8,
      repr(model.material_pbr.get("rune")))
check("a colour that declares only emit still defaults metal and rough",
      model.material_pbr["rune"]["metal"] == 0.0
      and model.material_pbr["rune"]["rough"] == 0.9,
      repr(model.material_pbr.get("rune")))
check("a colour that declares nothing stays absent",
      "shell" not in model.material_pbr)

for bad, why in (("#ffcc33 emit=1.5", "above 1"),
                 ("#ffcc33 emit=-0.2", "below 0"),
                 ("#ffcc33 glow=0.5", "not a real key")):
    try:
        wparser.parse_file(write("bad.wam", model_src(bad)))
        check("emit %s is rejected" % why, False, "parsed without error")
    except wparser.WamError as error:
        check("emit %s is rejected" % why, True, str(error))


# ---- it reaches the sheet, and survives shadow -----------------------------
_, lit_report = compile_to(model_src("#ffcc33 emit=0.0"), "off")
_, glow_report = compile_to(model_src("#ffcc33 emit=0.8"), "on")


def warm_luma(prefix):
    from PIL import Image
    img = np.asarray(Image.open(os.path.join(TMP, prefix + "_view_front.png"))
                     .convert("RGB")).astype(int)
    warm = img[..., 0] > img[..., 2] + 30       # the gem is the only warm thing
    return float(img[warm].mean()) if warm.any() else 0.0


off, on = warm_luma("off"), warm_luma("on")
check("emission brightens the material in the rendered sheet", on > off + 20,
      "emit=0 mean %.1f, emit=0.8 mean %.1f" % (off, on))
check("declaring emission raises no warning on a colour that can carry it",
      not [w for w in glow_report["warnings"] if "emit" in w],
      "; ".join(glow_report["warnings"]))

# The property a naive implementation loses: folding emission into the shade
# term would let the glow go dark where the key light does not reach.
DARK = """model darkside
  height 2.0
palette
  rune #ffffff emit=%s
skeleton
  root pelvis at 0.025
  bone spine parent=pelvis dir=up len=0.40
parts
  loft ball bones=pelvis..spine material=rune
    ring 0.00 w=0.18 d=0.18
    ring 0.50 w=0.20 d=0.20
    ring 1.00 w=0.16 d=0.16
    cap start=dome end=dome
"""


def shadow_floor(emit, prefix):
    from PIL import Image
    compile_to(DARK % emit, prefix)
    img = np.asarray(Image.open(os.path.join(TMP, prefix + "_view_front.png"))
                     .convert("RGB")).astype(int)
    body = np.abs(img - 255).max(axis=2) > 12
    return float(np.percentile(img[body].mean(axis=1), 2))


unlit, glowing = shadow_floor("0.0", "dark0"), shadow_floor("0.6", "dark6")
check("emission survives the side facing away from the key light",
      glowing > unlit + 40,
      "shadowed 2%% of pixels: %.1f unlit, %.1f at emit=0.6" % (unlit, glowing))


# ---- glTF, viewer, and the lint --------------------------------------------
with open(os.path.join(TMP, "on.gltf")) as handle:
    gltf = json.load(handle)
rune = [m for m in gltf["materials"] if m["name"] == "rune"]
check("the glTF carries emissiveFactor", bool(rune) and "emissiveFactor" in rune[0],
      json.dumps(rune))
if rune and "emissiveFactor" in rune[0]:
    # Two things have to be right here, and only one of them is obvious.
    #
    # The obvious one: the factor is the colour scaled by emit, not the raw
    # colour. The other: glTF's emissiveFactor is LINEAR, and so is the
    # palette by the time it reaches the exporter -- the conversion happens
    # once when the hex is parsed. So this is the palette value scaled by a
    # plain multiplier, with no conversion at the exporter at all.
    #
    # #ffcc33 at emit=0.8 -> linear (1.0, 0.6038, 0.0331) -> x0.8 ->
    # (0.800, 0.483, 0.026). Those are the numbers a consumer must receive,
    # and they did not change when the pipeline moved to linear internally --
    # only the place responsible for producing them did.
    #
    # Assert all three channels. Red is a fixed point of the conversion
    # (1.0 stays 1.0), so a one-channel check passes just as happily on a
    # half-applied conversion as on a correct one.
    want = [wcolor.srgb_to_linear(c) * 0.8
            for c in (1.0, 0xcc / 255.0, 0x33 / 255.0)]
    got = rune[0]["emissiveFactor"]
    check("emissiveFactor is the linearised colour scaled by emit",
          len(got) == 3 and all(abs(a - b) < 0.002 for a, b in zip(got, want)),
          "got %s want %s" % (got, [round(v, 4) for v in want]))
    check("emissiveFactor is not the raw sRGB colour scaled by emit",
          abs(got[1] - (0xcc / 255.0) * 0.8) > 0.05,
          "green channel %.4f is the un-converted value" % got[1])

with open(os.path.join(TMP, "off.gltf")) as handle:
    plain = json.load(handle)
check("emit=0 exports no emissiveFactor at all",
      all("emissiveFactor" not in m for m in plain["materials"]),
      json.dumps([m.get("emissiveFactor") for m in plain["materials"]]))

viewer_json = os.path.join(TMP, "viewer.json")
wviewer.export(write("vx.wam", model_src()), viewer_json)
with open(viewer_json) as handle:
    blob = json.load(handle)
entry = [m for m in blob["mats"] if m["name"] == "rune"]
check("the viewer blob carries emit", bool(entry) and entry[0].get("emit") == 0.8,
      json.dumps(entry))
# A model that never mentions emission must keep the blob it already had, so
# the key is omitted rather than written as zero. Verified byte-for-byte
# against the previous build for a metal/rough model at the time of writing.
plain_json = os.path.join(TMP, "plain_viewer.json")
wviewer.export(write("px.wam", model_src("#ffcc33")), plain_json)
with open(plain_json) as handle:
    plain_blob = json.load(handle)
check("a material with no emission carries no emit key at all",
      all("emit" not in m for m in plain_blob["mats"]),
      json.dumps([m for m in plain_blob["mats"] if "emit" in m]))

_, dim = compile_to(model_src("#ffcc33 emit=0.01"), "dim")
check("a glow too faint to read is warned about",
      any("emit" in w for w in dim["warnings"]),
      "; ".join(dim["warnings"]) or "no warnings at all")
_, subtle = compile_to(model_src("#ffcc33 emit=0.05"), "subtle")
check("a deliberately subtle but visible glow is left alone",
      not [w for w in subtle["warnings"] if "emit" in w],
      "; ".join(subtle["warnings"]))


# ---- and the film, where a glow matters most -------------------------------
# A cinematic merges several models plus ground and shadow into one mesh, so
# its per-material factors are a separate list that has to stay in lockstep
# with its colours. An off-by-one there would light the wrong material.
model_path = write("glow.wam", model_src())
cine_path = write("glow.cine", "\n".join([
    "cinematic g", "  aspect 2.39", "  fps 4", "  size 160", "",
    "scene s", "  light elevation=40 azimuth=120",
    "  actor %s at=(0,0,0) anim=idle phase=0 shadow" % model_path,
    "  ground extend", "  fog auto", "",
    "shot a dur=0.5 scene=s", "  eye 0%=(0,1.5,-6) 100%=(0,1.4,-5)",
    "  look at=glow", "  fov 40", ""]))
film = wcine.parse_cine(cine_path)
cache = {}
scene = wcine.Scene(film["scenes"]["s"], film,
                    lambda p: cache.setdefault(p, wcine.Loaded(p)))
check("the scene's PBR list is parallel to its colours",
      len(scene.pbr) == len(scene.colors),
      "%d colours, %d pbr entries" % (len(scene.colors), len(scene.pbr)))
emissive = [i for i, props in enumerate(scene.pbr) if props and props[2] > 0]
check("exactly the emissive material is marked emissive in the scene",
      len(emissive) == 1
      and max(scene.colors[emissive[0]]) > 0.9
      and scene.colors[emissive[0]][2] < 0.5,
      "indexes %s -> colours %s" % (emissive, [scene.colors[i] for i in emissive]))
check("ground and shadow declare nothing",
      scene.pbr[-1] is None and scene.pbr[-2] is None,
      repr(scene.pbr[-2:]))

import shutil  # noqa: E402
shutil.rmtree(TMP, ignore_errors=True)
print("\n%d passed, %d failed" % (TOTAL - len(fails), len(fails)))
sys.exit(1 if fails else 0)
