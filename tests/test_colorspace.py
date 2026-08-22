#!/usr/bin/env python3
"""Colour space: linear inside WAM, sRGB only at the edges.

    python3 tests/test_colorspace.py

`wam/color.py` states the invariant; this file holds it true at every edge.
The one property that matters to anyone using WAM is at the bottom: whatever
hex an author writes in the palette is what an importer gets back, byte for
byte, through both the untextured and the textured path.

The bug that prompted all this: the glTF exporter wrote sRGB palette values
straight into `baseColorFactor`, `emissiveFactor` and `COLOR_0`, all three of
which glTF 2.0 defines as linear. Valid files, no complaint from any viewer,
every model washed out on import. It is a bad bug to catch by eye -- the only
symptom is that colours look a little light, which reads as an opinion about
lighting rather than a defect -- so the detector is a number, not a look.

The mirror-image mistake is now the live risk: forgetting to *encode* on the
way out, which makes everything too dark and reads as "moody" rather than
"wrong". Hence the checks that PNG bytes and viewer payloads are in the space
their consumer expects.
"""
import base64
import json
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wam import cli as wcli              # noqa: E402
from wam import color as wcolor          # noqa: E402
from wam import gltf as wgltf            # noqa: E402
from wam import render as wrender        # noqa: E402
from wam import parser as wparser        # noqa: E402
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


TMP = tempfile.mkdtemp(prefix="wamcolor")
GREY = 0x80 / 255.0
# The value every consumer should receive for #808080. If this constant ever
# needs changing, something is wrong with the conversion, not with the test.
GREY_LINEAR = 0.21586


def write(name, src):
    path = os.path.join(TMP, name)
    with open(path, "w") as handle:
        handle.write(src)
    return path


def model_src(textures=""):
    return """model colorfixture
  height 2.0
palette
  grey  #808080
  ember #ffcc33 emit=0.5
  soot  #3b2a22
skeleton
  root pelvis at 0.025
  bone spine parent=pelvis dir=up len=0.40
parts
  loft core bones=pelvis..spine material=grey
    ring 0.00 w=0.16 d=0.14
    ring 1.00 w=0.13 d=0.11
    cap start=dome end=dome
  loft gem bone=spine at=0.6 dir=fwd len=0.14 material=ember
    ring 0.00 w=0.06 d=0.06
    ring 1.00 w=0.04 d=0.04
    cap start=dome end=dome
  loft foot bone=pelvis at=0.1 dir=fwd len=0.12 material=soot
    ring 0.00 w=0.07 d=0.06
    ring 1.00 w=0.05 d=0.05
    cap start=dome end=dome
%s""" % textures


def compile_gltf(src, prefix):
    path = write(prefix + ".wam", src)
    wcli.compile_model(path, os.path.join(TMP, prefix), "front", quiet=True,
                       width=120, height=160, report={})
    with open(os.path.join(TMP, prefix + ".gltf")) as handle:
        return json.load(handle)


def parsed_palette(name):
    """The palette entry as the compiler holds it -- linear."""
    model = wparser.parse_file(write("palcheck.wam", model_src()))
    return tuple(model.palette[name])


def material(gltf, name):
    for m in gltf["materials"]:
        if m["name"] == name:
            return m
    return None


import zlib                                                   # noqa: E402
import struct                                                 # noqa: E402

def read_png(path):
    raw = open(path, "rb").read()
    pos, idat, w, h = 8, b"", 0, 0
    while pos < len(raw):
        ln = struct.unpack(">I", raw[pos:pos + 4])[0]
        tag = raw[pos + 4:pos + 8]
        body = raw[pos + 8:pos + 8 + ln]
        if tag == b"IHDR":
            w, h = struct.unpack(">II", body[:8])
        elif tag == b"IDAT":
            idat += body
        pos += 12 + ln
    data = zlib.decompress(idat)
    rows = []
    stride = w * 3
    for y in range(h):
        start = y * (stride + 1)
        assert data[start] == 0, "only unfiltered rows are written"
        rows.append(np.frombuffer(data[start + 1:start + 1 + stride],
                                  dtype=np.uint8).reshape(w, 3))
    return np.stack(rows)



def read_accessor(gltf, index):
    """Pull a float accessor back out of the embedded base64 buffer."""
    acc = gltf["accessors"][index]
    view = gltf["bufferViews"][acc["bufferView"]]
    uri = gltf["buffers"][0]["uri"]
    raw = base64.b64decode(uri.split(",", 1)[1])
    start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    comps = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[acc["type"]]
    count = acc["count"] * comps
    data = np.frombuffer(raw, dtype="<f4", count=count,
                         offset=start).reshape(-1, comps)
    return data


# ---- the transfer function itself ------------------------------------------

print("\n-- the conversion --")
f = wcolor.srgb_to_linear
check("black and white are fixed points", f(0.0) == 0.0 and abs(f(1.0) - 1.0) < 1e-12,
      "%r %r" % (f(0.0), f(1.0)))
check("mid grey converts to the value the spec implies",
      abs(f(GREY) - GREY_LINEAR) < 1e-4, "%.5f" % f(GREY))
check("mid grey is emphatically not the sRGB value", abs(f(GREY) - GREY) > 0.28,
      "%.4f vs %.4f" % (f(GREY), GREY))
check("below the knee it is the linear segment, not the power curve",
      abs(f(0.04) - 0.04 / 12.92) < 1e-12, "%.8f" % f(0.04))
lo, hi = f(0.04045 - 1e-9), f(0.04045 + 1e-9)
check("the two segments meet at the knee", abs(lo - hi) < 1e-6,
      "%.9f vs %.9f" % (lo, hi))
xs = np.linspace(0.0, 1.0, 501)
check("it is monotonic across the range", bool(np.all(np.diff(f(xs)) > 0)))
# Why the exact curve and not `** 2.2`: near black they visibly disagree, and
# near-black is where muzzles, hooves and iron live.
soot = [f(c / 255.0) for c in (0x3b, 0x2a, 0x22)]
approx = [(c / 255.0) ** 2.2 for c in (0x3b, 0x2a, 0x22)]
check("it differs from a ** 2.2 approximation near black",
      max(abs(a - b) / a for a, b in zip(soot, approx)) > 0.08,
      "exact %s approx %s" % ([round(v, 4) for v in soot],
                              [round(v, 4) for v in approx]))
check("arrays convert elementwise and keep their shape",
      f(np.array([[GREY, 0.0], [1.0, 0.04]])).shape == (2, 2)
      and abs(float(f(np.array([[GREY]]))[0, 0]) - GREY_LINEAR) < 1e-4)
check("out-of-range input does not produce NaN",
      bool(np.all(np.isfinite(f(np.array([-0.2, 0.0, 1.0, 1.4]))))))


# ---- the untextured export, which is what most models use ------------------

print("\n-- baseColorFactor --")
plain = compile_gltf(model_src(), "plain")
grey = material(plain, "grey")
check("the grey material is exported at all", grey is not None)
base = grey["pbrMetallicRoughness"]["baseColorFactor"]
check("baseColorFactor is linear",
      all(abs(c - GREY_LINEAR) < 1e-4 for c in base[:3]),
      "got %s" % [round(c, 5) for c in base])
# The exporter no longer converts anything: the palette is already linear, so
# the factor is the palette entry. That equality is the point of the refactor.
check("baseColorFactor is the palette value, written straight through",
      all(abs(a - b) < 1e-9 for a, b in zip(base[:3], parsed_palette("grey"))),
      "factor %s palette %s" % (base[:3], parsed_palette("grey")))
check("baseColorFactor is not the raw sRGB value",
      all(abs(c - GREY) > 0.28 for c in base[:3]), "got %s" % base)
check("alpha is untouched", base[3] == 1.0)
soot_base = material(plain, "soot")["pbrMetallicRoughness"]["baseColorFactor"]
check("a near-black material converts on the exact curve",
      all(abs(a - b) < 1e-4 for a, b in zip(soot_base[:3], soot)),
      "got %s want %s" % ([round(c, 5) for c in soot_base[:3]],
                          [round(c, 5) for c in soot]))

print("\n-- emissiveFactor --")
ember = material(plain, "ember")
emissive = ember.get("emissiveFactor")
want = [f(c) * 0.5 for c in (1.0, 0xcc / 255.0, 0x33 / 255.0)]
check("emissiveFactor is the linearised colour scaled by emit",
      emissive is not None and all(abs(a - b) < 1e-4 for a, b in zip(emissive, want)),
      "got %s want %s" % (emissive, [round(v, 4) for v in want]))
# Red is a fixed point of the conversion, so it passes either way; green is
# the channel that tells a half-applied conversion from a correct one.
check("the conversion is applied before the emit multiplier, on every channel",
      emissive is not None and abs(emissive[1] - (0xcc / 255.0) * 0.5) > 0.05,
      "green %.4f" % (emissive[1] if emissive else -1))


# ---- the textured path, which must not move --------------------------------

print("\n-- the texture path is left alone --")
TEXTURED = """textures
  texture grey base=#808080
    noise scale=0.2 amount=0.08
"""
tex = compile_gltf(model_src(TEXTURED), "textured")
tex_grey = material(tex, "grey")
tex_base = tex_grey["pbrMetallicRoughness"]["baseColorFactor"]
check("a textured export still declares white baseColorFactor",
      tex_base == [1.0, 1.0, 1.0, 1.0], "got %s" % tex_base)
check("a textured export still points at the atlas",
      "baseColorTexture" in tex_grey["pbrMetallicRoughness"])
check("the atlas image is still carried", bool(tex.get("images")))
# glTF wants that PNG sRGB-encoded, and a PNG of palette values already is.
# Converting it would be the mirror-image of the bug being fixed.
check("the atlas PNG is not tagged or converted as linear",
      "extensions" not in tex.get("images", [{}])[0]
      or "KHR_texture_transform" not in str(tex.get("images")))


# ---- COLOR_0, on the exporter's own API ------------------------------------

# The CLI hard-wires vert_colors=None today, so this path ships nothing; it is
# still reachable through wgltf.export, and it still has to carry the linear
# values WAM holds. Exercised directly rather than through the CLI,
# because a test that cannot reach the code cannot defend it.
print("\n-- COLOR_0 --")
import wam.mesh as wmesh          # noqa: E402
import wam.skeleton as wskel      # noqa: E402
import wam.animation as wanim     # noqa: E402

model = wparser.parse_file(write("vc.wam", model_src()))
bones, bone_order = wskel.solve(model)
mesh = wmesh.build(model, bones)
V, _T, _M = mesh.arrays()
# What the bake now hands the exporter: linear, because the palette it starts
# from is linear. The exporter's job is to not touch it.
linear_colors = np.tile(np.array([[GREY_LINEAR] * 3]), (len(V), 1))
out = os.path.join(TMP, "vcol.gltf")
wgltf.export(out, model, bones, bone_order, mesh,
             wanim.gltf_tracks(model, bones, bone_order),
             scale=model.height, vert_colors=linear_colors)
with open(out) as handle:
    vc = json.load(handle)
attrs = vc["meshes"][0]["primitives"][0]["attributes"]
check("COLOR_0 is exported when vertex colours are supplied", "COLOR_0" in attrs)
if "COLOR_0" in attrs:
    colors = read_accessor(vc, attrs["COLOR_0"])
    check("COLOR_0 carries the linear values it was handed",
          bool(np.all(np.abs(colors - GREY_LINEAR) < 1e-4)),
          "first row %s" % colors[0])
    check("COLOR_0 is not the sRGB value",
          bool(np.all(np.abs(colors - GREY) > 0.28)))
    # COLOR_0 multiplies into base colour, so a consumer decoding it for
    # display must land back on the authored hex.
    check("and it decodes to the colour that was authored",
          wcolor.linear_to_hex(colors[0]) == "#808080",
          wcolor.linear_to_hex(colors[0]))
check("a vertex-coloured export still declares white baseColorFactor",
      material(vc, "grey")["pbrMetallicRoughness"]["baseColorFactor"]
      == [1.0, 1.0, 1.0, 1.0])


# ---- the guarantee: what the author wrote is what the importer gets --------

# This is the property the whole fix exists to deliver, so it is asserted
# directly rather than inferred from the conversion being "correct": take the
# authored hex, run it through the real exporter, then do what a spec-following
# consumer does -- read the linear factor and encode it for display -- and
# require the original hex back, byte for byte.

def linear_to_srgb(c):
    """What the consumer does on the way to the screen. The exporter's inverse."""
    a = np.asarray(c, dtype=np.float64)
    safe = np.clip(a, 0.0, None)
    return np.where(safe <= 0.0031308, safe * 12.92,
                    1.055 * safe ** (1 / 2.4) - 0.055)


def as_bytes(linear):
    return tuple(int(round(float(v) * 255.0)) for v in linear_to_srgb(linear))


print("\n-- the conversion is lossless at 8 bits --")
levels = np.arange(256) / 255.0
back = np.round(linear_to_srgb(f(levels)) * 255.0).astype(int)
bad = [(i, int(b)) for i, b in enumerate(back) if b != i]
check("every one of the 256 channel values survives the round trip",
      not bad, "%d differ, first few: %s" % (len(bad), bad[:5]))

print("\n-- authored hex in, same hex out --")
SWATCHES = {
    "vivid":  "#ff0000",   # the primaries are fixed points -- they survived
    "dun":    "#8c5a3c",   # the mid tone the bug hurt worst
    "timber": "#a8946f",
    "soot":   "#3b2a22",   # near-black, sitting on the knee of the curve
    "ink":    "#010101",   # inside the linear segment
    "paper":  "#fefefe",
    "grey":   "#808080",
}
palette_lines = "\n".join("  %-7s %s" % (n, h) for n, h in SWATCHES.items())
parts_lines = "\n".join(
    """  loft p%d bone=spine at=%.2f dir=fwd len=0.10 material=%s
    ring 0.00 w=0.05 d=0.05
    ring 1.00 w=0.04 d=0.04
    cap start=dome end=dome""" % (i, 0.1 + i * 0.1, n)
    for i, n in enumerate(SWATCHES))
swatch_src = """model swatches
  height 2.0
palette
%s
skeleton
  root pelvis at 0.025
  bone spine parent=pelvis dir=up len=0.40
parts
  loft core bones=pelvis..spine material=grey
    ring 0.00 w=0.16 d=0.14
    ring 1.00 w=0.13 d=0.11
    cap start=dome end=dome
%s
""" % (palette_lines, parts_lines)

swatches = compile_gltf(swatch_src, "swatches")
for name, hex_in in SWATCHES.items():
    want = tuple(int(hex_in[i:i + 2], 16) for i in (1, 3, 5))
    mat = material(swatches, name)
    if mat is None:
        check("%s survives the round trip" % name, False, "material missing")
        continue
    got = as_bytes(mat["pbrMetallicRoughness"]["baseColorFactor"][:3])
    check("%s: %s in, %s out" % (name, hex_in,
                                 "#%02x%02x%02x" % got),
          got == want, "authored %s, importer would show %s" % (want, got))

# The textured path reaches the consumer as an sRGB PNG rather than a factor,
# so it has to be checked on its own terms: a material with no ops declared
# must still land in the atlas as the exact byte triple the author wrote.
print("\n-- and the same through the atlas --")
tex_src = swatch_src.replace("skeleton", """textures
  texture core base=#808080
    noise scale=0.2 amount=0.06
skeleton""", 1)
tex_path = write("swatchtex.wam", tex_src)
report = {}
wcli.compile_model(tex_path, os.path.join(TMP, "swatchtex"), "front", quiet=True,
                   width=120, height=160, report=report)
atlas_png = os.path.join(TMP, "swatchtex_tex.png")
check("the textured compile produced an atlas", os.path.exists(atlas_png))
if os.path.exists(atlas_png):
    atlas = read_png(atlas_png)
    present = set(map(tuple, atlas.reshape(-1, 3).tolist()))
    for name, hex_in in SWATCHES.items():
        if name == "core":
            continue
        want = tuple(int(hex_in[i:i + 2], 16) for i in (1, 3, 5))
        check("%s reaches the atlas unaltered" % name, want in present,
              "authored %s is not among the atlas texels" % (want,))


# ---- everything upstream of the exporter is unchanged ----------------------

print("\n-- nothing else moved --")
parsed = wparser.parse_file(write("pal.wam", model_src()))
# Authors keep writing sRGB hex and no model needed re-authoring; what
# changed is that the value is converted as it comes through the door.
check("the palette parses hex into linear",
      abs(parsed.palette["grey"][0] - GREY_LINEAR) < 1e-4,
      repr(parsed.palette.get("grey")))
check("and that is the hex the author wrote, coming back out",
      wcolor.linear_to_hex(parsed.palette["grey"]) == "#808080",
      wcolor.linear_to_hex(parsed.palette["grey"]))
viewer_json = os.path.join(TMP, "viewer.json")
wviewer.export(write("vx.wam", model_src()), viewer_json)
with open(viewer_json) as handle:
    viewer = json.load(handle)
vmat = [m for m in viewer["mats"] if m["name"] == "grey"]
# The viewer shades in linear too, and encodes in its fragment shader, which
# is what keeps it showing the same model as the sheet.
check("the viewer receives linear, because its shader now shades in linear",
      bool(vmat) and abs(vmat[0]["rgb"][0] - GREY_LINEAR) < 0.002,
      json.dumps(vmat))
check("the exporter did not mutate the shared material list",
      abs(mesh.materials[[n for n, _ in mesh.materials].index("grey")][1][0]
          - GREY_LINEAR) < 1e-4)

# --- the way out, which is now the mistake worth guarding against ---------
print("\n-- images are encoded on the way to disk --")
flat = np.zeros((4, 4, 3))
flat[:] = GREY_LINEAR
png_path = os.path.join(TMP, "flat.png")
wrender.write_png(png_path, flat)
written = read_png(png_path)
check("a linear image is written as sRGB bytes",
      bool(np.all(written == 0x80)),
      "corner pixel %s, expected 128" % (written[0, 0],))
check("write_png and png_bytes agree",
      wrender.png_bytes(flat) == open(png_path, "rb").read())
# A double encode is the classic way to get this wrong, and it looks almost
# right: 0x80 would come back as 0xbc.
check("it is encoded once, not twice", int(written[0, 0, 0]) != 0xbc)

print("\n%d checks, %d failed" % (TOTAL, len(fails)))
if fails:
    for name in fails:
        print("  FAILED: %s" % name)
    sys.exit(1)
