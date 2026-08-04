"""Compose town props into a square: hero renders + merged viewer JSON."""
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wam import parser as wp, skeleton as ws, mesh as wm, render as wr
from wam import texture as wtex

# (model, x, z, rotY degrees)
LAYOUT = [
    ("cottage", -7.0, 3.0, 25),
    ("inn", 7.5, 1.5, -30),
    ("tower", -10.0, -6.5, 0),
    ("well", 0.0, 0.0, 0),
    ("stall", 4.2, -4.5, 40),
    ("cart", -3.5, -3.4, -65),
    ("lamppost", 2.2, 2.6, 0),
    ("lamppost", -2.6, -2.0, 0),
    ("barrel", 5.9, -1.6, 0),
    ("barrel", 6.5, -2.3, 30),
    ("barrel", -5.4, -1.0, 10),
    ("crate", 6.6, -0.7, 15),
    ("crate", -5.9, -0.2, -12),
    ("sacks", 3.3, -6.3, 70),
    ("fence", -1.4, -8.6, 0),
    ("fence", 0.05, -8.6, 0),
    ("fence", 1.5, -8.6, 0),
    ("tree", -6.5, -8.5, 0),
    ("tree", 9.5, -6.5, 40),
    ("tree", -9.5, 7.5, 80),
]

_cache = {}
CELL = 512
GRID = 4
RES = CELL * GRID
mega = np.zeros((RES, RES, 3))
slot_of = {}


def _next_slot(name):
    slot_of[name] = len(slot_of)
    return slot_of[name]


def load(name):
    if name not in _cache:
        model = wp.parse_file("models/town/%s.wam" % name)
        bones, order = ws.solve(model)
        mesh = wm.build(model, bones)
        V, T, M = mesh.arrays()
        atlas, auv = wtex.bake_atlas(model, mesh, V, T, M, res=CELL)
        if atlas is None:
            # flat-color fallback atlas: one texel block per material
            atlas = np.zeros((CELL, CELL, 3))
            auv = np.zeros((len(V), 2))
            nm = max(len(mesh.materials), 1)
            bw = CELL // nm
            vmat = np.zeros(len(V), dtype=int)
            for (ta, tb, tc), mi in zip(T, M):
                vmat[ta] = vmat[tb] = vmat[tc] = mi
            for mi, (mn, rgb) in enumerate(mesh.materials):
                atlas[:, mi * bw:(mi + 1) * bw] = rgb
                sel = vmat == mi
                auv[sel, 0] = (mi + 0.5) * bw / CELL
                auv[sel, 1] = 0.5
        si = _next_slot(name)
        sx, sy = si % GRID, si // GRID
        mega[sy * CELL:(sy + 1) * CELL, sx * CELL:(sx + 1) * CELL] = atlas
        auv_g = (auv * CELL + np.array([sx * CELL, sy * CELL])) / RES
        _cache[name] = (model, order, mesh, auv_g)
    return _cache[name]


def rot_y(deg):
    a = math.radians(deg)
    return np.array([[math.cos(a), 0, math.sin(a)],
                     [0, 1, 0],
                     [-math.sin(a), 0, math.cos(a)]])


allV, allT, allM = [], [], []
mats = []          # (name, rgb)
mat_index = {}
bones_out = []     # dicts for viewer
skins = []
voff = 0

# ground disc
GROUND = (0.42, 0.47, 0.33)
gi = mat_index.setdefault("ground", len(mats))
mats.append(("ground", GROUND))
bones_out.append(dict(n="world", p=-1, h=[0.0, 0.0, 0.0]))
R = 15.0
seg = 28
rings_n = 5
cverts = [[0.0, 0.0, 0.0]]
for r in range(1, rings_n + 1):
    rr = R * r / rings_n
    for i in range(seg):
        a = 2 * math.pi * i / seg
        cverts.append([rr * math.cos(a), 0.0, rr * math.sin(a)])
gv = np.array(cverts)
allV.append(gv)
skins.extend([[0, 1.0, -1]] * len(cverts))
def gidx(r, i):
    return 1 + (r - 1) * seg + (i % seg)
for i in range(seg):
    allT.append([0, gidx(1, i + 1), gidx(1, i)])
    allM.append(gi)
for r in range(1, rings_n):
    for i in range(seg):
        allT.append([gidx(r, i), gidx(r, i + 1), gidx(r + 1, i + 1)])
        allM.append(gi)
        allT.append([gidx(r, i), gidx(r + 1, i + 1), gidx(r + 1, i)])
        allM.append(gi)
voff = len(cverts)
gslot = _next_slot("__ground__")
gsx, gsy = gslot % GRID, gslot // GRID
gy, gx = np.meshgrid(np.arange(CELL) + 0.5, np.arange(CELL) + 0.5, indexing="ij")
gp = np.stack([(gx / CELL * 2 - 1) * R, np.zeros_like(gx),
               (gy / CELL * 2 - 1) * R], axis=-1).reshape(-1, 3)
gn = wtex.value_noise3(gp / 2.2, seed=5.0).reshape(CELL, CELL)
gn2 = wtex.value_noise3(gp / 0.55, seed=9.0).reshape(CELL, CELL)
gcell = (np.array(GROUND)[None, None, :] *
         (1 + 0.16 * (gn[..., None] * 2 - 1) + 0.07 * (gn2[..., None] * 2 - 1)))
mega[gsy * CELL:(gsy + 1) * CELL, gsx * CELL:(gsx + 1) * CELL] = np.clip(gcell, 0, 1)
guv = np.zeros((len(cverts), 2))
gvarr = np.array(cverts)
guv[:, 0] = (gsx * CELL + (gvarr[:, 0] / (2 * R) + 0.5) * CELL) / RES
guv[:, 1] = (gsy * CELL + (gvarr[:, 2] / (2 * R) + 0.5) * CELL) / RES
all_uv = [guv]

for inst, (name, x, z, ry) in enumerate(LAYOUT):
    model, order, mesh, auv_g = load(name)
    V, T, M = mesh.arrays()
    V = (V * model.height) @ rot_y(ry).T + np.array([x, 0.0, z])
    all_uv.append(auv_g)
    allV.append(V)
    # materials
    remap = {}
    for mi, (mn, rgb) in enumerate(mesh.materials):
        key = (mn, rgb)
        if key not in mat_index:
            mat_index[key] = len(mats)
            mats.append((mn, rgb))
        remap[mi] = mat_index[key]
    boff = len(bones_out)
    bindex = {b.name: i for i, b in enumerate(order)}
    for b in order:
        h = (b.head * model.height) @ rot_y(ry).T + np.array([x, 0.0, z])
        bones_out.append(dict(n="%d_%s" % (inst, b.name),
                              p=(boff + bindex[b.parent.name]) if b.parent else -1,
                              h=[round(float(v), 4) for v in h]))
    for sk in mesh.skin:
        e = [[boff + bindex[bn], w] for bn, w in sk[:2]]
        if len(e) == 1:
            e.append([-1, 0.0])
        skins.append([e[0][0], round(e[0][1], 3), e[1][0]])
    for (a, b, c), mi in zip(T, M):
        allT.append([a + voff, b + voff, c + voff])
        allM.append(remap[int(mi)])
    voff += len(V)

V = np.concatenate(allV)
T = np.array(allT, dtype=np.int32)
M = np.array(allM, dtype=np.int32)
colors = [rgb for _, rgb in mats]
UV = np.concatenate(all_uv)

os.makedirs("out", exist_ok=True)
# margins tuned against the bounding-sphere fit; keep it
img = wr.render_view(V, T, M, colors, yaw_deg=205, pitch_deg=22, fit="sphere",
                     width=1100, height=700, fov_deg=30, margin=0.82, uv=UV, tex=mega)
wr.write_png("out/town_wide.png", img)
img = wr.render_view(V, T, M, colors, yaw_deg=155, pitch_deg=13, fit="sphere",
                     width=1100, height=700, fov_deg=24, margin=0.55, uv=UV, tex=mega)
wr.write_png("out/town_close.png", img)

data = dict(
    name="town",
    height=1.0,
    verts=[round(float(v), 4) for v in V.reshape(-1)],
    tris=[int(i) for i in T.reshape(-1)],
    triMat=[int(i) for i in M],
    mats=[dict(name=n, rgb=[round(c, 3) for c in rgb]) for n, rgb in mats],
    skin=skins,
    bones=bones_out,
    anims=[],
)
import base64
data["uv"] = [round(float(c), 4) for c in UV.reshape(-1)]
data["tex"] = ("data:image/png;base64,"
               + base64.b64encode(wr.png_bytes(mega)).decode())
with open("out/town_viewer.json", "w") as f:
    json.dump(data, f, separators=(",", ":"))
print("town composed: %d verts, %d tris, %d instances"
      % (len(V), len(T), len(LAYOUT)))
