"""Cameras, staging and shot lint: `python3 -m wam.cinematic film.cine`.

The founding rule applies here exactly as it does to a model. The author makes
discrete, named, relative decisions — where a camera starts and ends, what it
looks at, how long the shot runs — and the compiler does every continuous one:
interpolation, easing, ground heights, framing, occlusion, and telling you what
it decided.

The lint is the point. Film work fails in ways that are invisible until you
watch the frames: a camera two metres under the terrain, a subject four percent
of frame height, a look target behind a wall. Each of those is one measurement,
and measuring 2,160 frames after the fact is not a workflow.

Deliberately not here: audio, grading, titles, encoding, shadows, depth of
field. Those are post, they compose with ffmpeg, and the renderer's flat
honesty is the aesthetic.
"""
import math
import os
import re
import sys

import numpy as np

from . import animation as wanim
from . import checks as wchecks
from . import mesh as wmesh
from . import parser as wparser
from . import render as wr
from . import skeleton as wskel
from . import texture as wtexture
from .parser import WamError

SCENE_SCHEMA = 1

EASINGS = {
    "linear": lambda t: t,
    "smooth": lambda t: t * t * (3 - 2 * t),
    "in": lambda t: t * t,
    "out": lambda t: 1 - (1 - t) * (1 - t),
}


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

def _num(tok, line_no, line):
    try:
        return float(tok)
    except ValueError:
        raise WamError("expected a number, got %r" % tok, line_no, line)


def _vec(tok, line_no, line):
    """(x,y,z), with a leading ~ on y meaning 'absolute, not ground-relative'."""
    t = tok.strip()
    if not (t.startswith("(") and t.endswith(")")):
        raise WamError("expected (x,y,z), got %r" % tok, line_no, line)
    parts = [p.strip() for p in t[1:-1].split(",")]
    if len(parts) != 3:
        raise WamError("expected three numbers in %r" % tok, line_no, line)
    absolute = parts[1].startswith("~")
    if absolute:
        parts[1] = parts[1][1:]
    return np.array([_num(p, line_no, line) for p in parts]), absolute


def _kv(tokens, line_no, line):
    out = {}
    for t in tokens:
        if "=" not in t:
            raise WamError("expected key=value, got %r" % t, line_no, line)
        k, v = t.split("=", 1)
        out[k] = v
    return out


def _pct(key):
    """`0%` / `100%` -> 0.0 / 1.0, or None if it is not a percent key."""
    if key.endswith("%"):
        try:
            return float(key[:-1]) / 100.0
        except ValueError:
            return None
    return None


def parse_cine(path):
    """Parse a `.cine` into {name, aspect, fps, size, scenes, shots}."""
    with open(path) as f:
        raw = f.read().replace("\\\n", " ")

    film = dict(name=None, aspect=16 / 9, fps=24, size=1440,
                scenes={}, shots=[])
    scene = None
    shot = None
    section = None

    for line_no, line in enumerate(raw.splitlines(), 1):
        # `#` starts a comment, but `#rrggbb` is a colour — the .wam
        # parser has drawn that distinction since colours existed, and
        # a scene could not name a sky without it
        body = re.split(r"#(?![0-9a-fA-F]{6}\b)", line, 1)[0].rstrip()
        if not body.strip():
            continue
        indented = body[0].isspace()
        tok = body.split()
        kw = tok[0]

        if not indented:
            if kw == "cinematic":
                film["name"] = tok[1] if len(tok) > 1 else "film"
                scene = shot = None
                section = "film"
            elif kw == "scene":
                if len(tok) < 2:
                    raise WamError("scene needs a name", line_no, line)
                scene = dict(name=tok[1], zone=None, places=[], actors=[],
                             ground=None, fog="auto", light=None, line=line_no)
                film["scenes"][tok[1]] = scene
                shot = None
                section = "scene"
            elif kw == "shot":
                if len(tok) < 2:
                    raise WamError("shot needs a name", line_no, line)
                kv = _kv(tok[2:], line_no, line)
                shot = dict(name=tok[1], dur=float(kv.get("dur", 4)),
                            scene=kv.get("scene"), fov=40.0,
                            eye=[], look=None, orbit=None,
                            cut="hard", dissolve=0.0, checks=[],
                            line=line_no)
                film["shots"].append(shot)
                scene = None
                section = "shot"
            else:
                raise WamError("unknown top-level directive %r" % kw, line_no, line)
            continue

        # ---- indented body ------------------------------------------------
        if section == "film":
            if kw == "aspect":
                film["aspect"] = _num(tok[1], line_no, line)
            elif kw == "fps":
                film["fps"] = _num(tok[1], line_no, line)
            elif kw == "size":
                film["size"] = int(_num(tok[1], line_no, line))
            else:
                raise WamError("cinematic does not understand %r — it takes "
                               "aspect, fps and size" % kw, line_no, line)

        elif section == "scene":
            if kw == "zone":
                scene["zone"] = tok[1]
            elif kw in ("place", "actor"):
                flags = [t for t in tok[2:] if "=" not in t]
                kv = _kv([t for t in tok[2:] if "=" in t], line_no, line)
                at, _ = _vec(kv.get("at", "(0,0,0)"), line_no, line)
                entry = dict(model=tok[1], at=at,
                             yaw=float(kv.get("yaw", 0)),
                             pitch=float(kv.get("pitch", 0)),
                             roll=float(kv.get("roll", 0)),
                             scale=float(kv.get("scale", 1)),
                             anim=kv.get("anim"),
                             phase=float(kv.get("phase", 0)),
                             **{"as": kv.get("as")},
                             shadow="shadow" in flags,
                             ground="at" not in kv or not kv["at"].split(",")[1].strip().startswith("~"),
                             line=line_no)
                (scene["actors"] if kw == "actor" else scene["places"]).append(entry)
            elif kw == "light":
                kv = _kv(tok[1:], line_no, line)
                lg = {}
                if "sun" in kv:
                    lg["sun"] = _vec(kv["sun"], line_no, line)[0]
                if "elevation" in kv or "azimuth" in kv:
                    el = math.radians(float(kv.get("elevation", 45)))
                    az = math.radians(float(kv.get("azimuth", 0)))
                    lg["sun"] = np.array([math.cos(el) * math.sin(az),
                                          math.sin(el),
                                          math.cos(el) * math.cos(az)])
                if "fill" in kv:
                    lg["fill"] = _vec(kv["fill"], line_no, line)[0]
                if "ambient" in kv:
                    lg["ambient"] = (float(kv["ambient"]),
                                     float(kv.get("key", 0.60)),
                                     float(kv.get("fillstrength", 0.16)))
                scene["light"] = lg
            elif kw == "ground":
                scene["ground"] = tok[1] if len(tok) > 1 else "extend"
            elif kw == "fog":
                if len(tok) > 1 and "=" in tok[1]:
                    fkv = _kv(tok[1:], line_no, line)
                    scene["fog"] = "custom"
                    scene["fogspec"] = dict(
                        color=wparser._hex_color(fkv["color"], line_no, line),
                        start=float(fkv.get("start", 40.0)),
                        end=float(fkv.get("end", 400.0)),
                        max=float(fkv.get("max", 0.55)))
                else:
                    scene["fog"] = tok[1] if len(tok) > 1 else "auto"
            elif kw == "sky":
                # A staged scene took the renderer's daylight gradient and had
                # no way to say otherwise, so every interior — a throne room
                # under a mountain, a vault, a hold — was lit by a blue sky
                # you could see between the columns.
                skv = _kv(tok[1:], line_no, line)
                scene["sky"] = (wparser._hex_color(skv["top"], line_no, line),
                                wparser._hex_color(skv["horizon"], line_no, line))
            else:
                raise WamError("scene does not understand %r — it takes zone, "
                               "place, actor, ground, sky, light and fog"
                               % kw, line_no, line)

        elif section == "shot":
            if kw == "eye":
                if tok[1].startswith("orbit"):
                    shot["orbit"] = _kv(tok[2:], line_no, line)
                else:
                    ease = "linear"
                    for t in tok[1:]:
                        if t.startswith("ease="):
                            ease = t.split("=", 1)[1]
                            continue
                        k, v = t.split("=", 1)
                        p = _pct(k)
                        if p is None:
                            raise WamError("eye keys are percentages like "
                                           "0%%=(x,y,z), got %r" % k, line_no, line)
                        pos, absolute = _vec(v, line_no, line)
                        shot["eye"].append((p, pos, absolute))
                    if ease not in EASINGS:
                        raise WamError("unknown ease %r — one of %s"
                                       % (ease, ", ".join(sorted(EASINGS))),
                                       line_no, line)
                    shot["ease"] = ease
            elif kw == "look":
                kv = _kv(tok[1:], line_no, line)
                if "at" in kv:
                    shot["look"] = kv["at"]
                else:
                    raise WamError("look needs at=<target>", line_no, line)
            elif kw == "fov":
                # `54..28` swings the lens across the shot. A camera that only
                # translates can dolly and it can orbit, and it cannot do the
                # one move where the world changes shape around a subject that
                # stays put — the pull-back whose scale keeps getting worse.
                shot["fov"] = tok[1] if ".." in tok[1] else _num(tok[1], line_no, line)
            elif kw == "cut":
                shot["cut"] = tok[1] if len(tok) > 1 else "hard"
            elif kw == "dissolve":
                shot["cut"] = "dissolve"
                shot["dissolve"] = _num(tok[1].split("=")[-1], line_no, line)
            elif kw == "checks":
                shot["_in_checks"] = True
            elif kw == "assert":
                shot["checks"].append((line_no, " ".join(tok[1:])))
            elif kw == "measure":
                shot["checks"].append((line_no, "measure " + " ".join(tok[1:])))
            else:
                raise WamError("shot does not understand %r" % kw, line_no, line)

    if film["name"] is None:
        raise WamError("no `cinematic <name>` line", 1, "")
    if not film["shots"]:
        raise WamError("a cinematic with no shots renders nothing", 1, "")
    for sh in film["shots"]:
        if sh["scene"] is None:
            raise WamError("shot %r has no scene=" % sh["name"], sh["line"], "")
        if sh["scene"] not in film["scenes"]:
            raise WamError("shot %r names scene %r, which is not defined"
                           % (sh["name"], sh["scene"]), sh["line"], "")
        if not sh["eye"] and not sh["orbit"]:
            raise WamError("shot %r has no camera: give it eye keys or "
                           "eye orbit" % sh["name"], sh["line"], "")
    return film


# ---------------------------------------------------------------------------
# staging
# ---------------------------------------------------------------------------

def _rot(pitch, yaw, roll):
    """Full orientation, so a placed model is not stuck upright."""
    p, y, r = (math.radians(a) for a in (pitch, yaw, roll))
    Rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)],
                   [0, math.sin(p), math.cos(p)]])
    Ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0],
                   [-math.sin(y), 0, math.cos(y)]])
    Rz = np.array([[math.cos(r), -math.sin(r), 0],
                   [math.sin(r), math.cos(r), 0], [0, 0, 1]])
    return Ry @ Rx @ Rz


class Loaded:
    """One compiled model, kept so it can be posed without recompiling."""

    def __init__(self, path):
        self.model = wparser.parse_file(path)
        self.bones, self.order = wskel.solve(self.model)
        self.mesh = wmesh.build(self.model, self.bones)
        V, T, M = self.mesh.arrays()
        self.V, self.T, self.M = V, T, M
        self.atlas, self.uv = wtexture.bake_atlas(self.model, self.mesh, V, T, M)
        self.vcols = (None if self.atlas is not None else
                      wtexture.bake_vertex_colors(self.model, self.mesh, V, T, M))
        self.colors = [rgb for _, rgb in self.mesh.materials]
        # Emission is the reason a night shot can have lit windows or glowing
        # runes at all, so the film renderer needs the same factors the
        # turnaround sheet uses. None means "this colour declared nothing".
        props = getattr(self.model, "material_pbr", {}) or {}
        self.pbr = [((props[n]["metal"], props[n]["rough"],
                      props[n].get("emit", 0.0)) if n in props else None)
                    for n, _ in self.mesh.materials]
        self.markers = dict(getattr(self.model, "markers", {}) or {})
        # The checks language already knows how to turn a name into a point or
        # a vertex range. Reusing it is the whole reason `look at=crown.break`
        # and `assert visible(crown.break) > 0.5` cannot drift apart: they are
        # not two resolvers that happen to agree, they are one resolver.
        self.env = wchecks.Env(self.model, self.bones, self.mesh, self.V)

    def local_point(self, sub):
        """A marker, bone end or part centre, in the model's own unit space."""
        return np.asarray(self.env.point(sub), dtype=float)

    def local_range(self, sub):
        """Vertex range of a named part, or None if the name is not a part."""
        try:
            return self.env.part_range(sub)
        except wchecks.CheckError:
            return None

    def local_names(self):
        return (sorted(self.markers)
                + sorted(self.mesh.part_ranges)
                + sorted(self.bones))

    def anim(self, anim_name):
        a = next((x for x in self.model.anims if x["name"] == anim_name), None)
        if a is None:
            raise WamError("model %r has no anim %r — it has: %s"
                           % (self.model.name, anim_name,
                              ", ".join(x["name"] for x in self.model.anims)
                              or "none"), 0, "")
        return a

    def anim_dur(self, anim_name):
        """Seconds for one loop, so a shot plays the cycle at its own speed."""
        return float(self.anim(anim_name).get("dur", 1.0)) or 1.0

    def posed(self, anim_name, phase):
        if not anim_name:
            return self.V
        rots = wanim.anim_rotations_at(self.model, self.bones,
                                       self.anim(anim_name), phase % 1.0)
        return wanim.skin_verts(self.mesh, self.bones, self.order, rots)


@wr.quiet_fp
def _staged_verts(V, R, base):
    return V @ R.T + base


class Scene:
    """A built stage: static geometry once, actors posed per frame.

    The whole reason this class exists is that rebuilding the merged mesh and
    repacking the atlas every frame is pure waste when only the pose and the
    camera move. Static geometry is concatenated once; actors keep their vertex
    slice so a frame is a few skinning calls and a copy.
    """

    def __init__(self, spec, film, loader):
        self.name = spec["name"]
        self.fog = None
        self.sky = spec.get("sky") or ((0.55, 0.68, 0.83), (0.85, 0.87, 0.83))
        lg = spec.get("light") or {}
        self.sun = lg.get("sun")
        self.fill = lg.get("fill")
        self.ambient = lg.get("ambient")
        self.shadows = []          # (source slice, shadow slice, base y)
        self.height_at = lambda x, z: 0.0
        self.floor = None          # y of a flat staged floor, if there is one
        self._edge_idx = None      # perimeter of the ground, for the edge lint
        self._targets = {}
        self._subjects = {}       # name -> (vertex start, end) for measurement
        self._instances = {}      # name -> the staged model, for sub-names
        self._ambiguous = {}      # name -> how many models answer to it
        self._shadow_specs = []
        self._last_V = None

        chunks = []          # (V, T, M, uv, colors_offset)
        self.colors = []
        self.pbr = []        # parallel to self.colors; None = nothing declared
        self.atlas = None
        self.uv_rows = []
        self.actors = []     # (Loaded, slice, transform, anim, phase)

        if spec["zone"]:
            self._load_zone(spec["zone"], chunks)

        staged = ([("place", e) for e in spec["places"]]
                  + [("actor", e) for e in spec["actors"]])
        for kind, entry in staged:
            mdl = loader(entry["model"])
            # A model's mesh is in fractions of its declared height, not
            # metres — `height` is the only value in a .wam that is. A zone is
            # in metres, so staging one without this makes every model a
            # thousandth of its size, which reads as "the model did not load".
            R = (_rot(entry["pitch"], entry["yaw"], entry["roll"])
                 * entry["scale"] * mdl.model.height)
            base = entry["at"].copy()
            if entry["ground"]:
                base[1] += self.height_at(base[0], base[2])
            V = _staged_verts(mdl.V, R, base)
            off = len(self.colors)
            start = sum(len(c[0]) for c in chunks)
            chunks.append((V, mdl.T, mdl.M + off, mdl.uv, mdl.atlas, mdl.vcols))
            key = entry.get("as") or os.path.splitext(
                os.path.basename(entry["model"]))[0]
            if key in self._instances:
                # Two copies of one model and one name between them. Aiming at
                # the first and saying nothing is how you frame the wrong
                # knight, so name the ambiguity rather than resolving it.
                self._ambiguous.setdefault(key, 1)
                self._ambiguous[key] += 1
            else:
                self._instances[key] = dict(mdl=mdl, R=R, base=base, start=start,
                                            count=len(V))
                self._subjects[key] = (start, start + len(V))
                self._targets[key] = (V.min(axis=0) + V.max(axis=0)) / 2.0
            self.colors.extend(mdl.colors)
            self.pbr.extend(mdl.pbr)
            if entry.get("shadow"):
                self._shadow_specs.append((start, start + len(V),
                                           float(base[1])))
            if kind == "actor":
                self.actors.append(dict(model=mdl, start=start, R=R, base=base,
                                        anim=entry["anim"], phase=entry["phase"]))

        self._merge(chunks)
        if spec["ground"] and not spec["zone"]:
            self._add_ground()
        self._add_shadows()
        if spec["fog"] == "auto" and self.fog is None:
            # The horizon colour, so a staged shot fades into the sky instead
            # of ending at a visible edge. "The terrain just stops" was a
            # viewer-reported bug, which makes it the compiler's job.
            # Reaches full strength *before* the ground's rim, so the floor
            # fades into the sky instead of ending at a visible line. A fog
            # that caps below 1.0 leaves the rim permanently faintly visible,
            # which is both the original bug and a lint that never shuts up.
            self.fog = dict(color=tuple(self.sky[1]), start=0.12 * GROUND_R,
                            end=0.88 * GROUND_R, max=1.0)
        elif spec["fog"] == "custom":
            # An explicit fog wins over the zone's own, because the author who
            # wrote it out is the one saying what this scene's air is like.
            fs = spec["fogspec"]
            self.fog = dict(color=tuple(fs["color"]), start=fs["start"],
                            end=fs["end"], max=fs["max"])
        if spec.get("sky"):
            # After the zone, not before it. A zone carries the sky it was
            # built with, so setting this up front let the zone quietly
            # overwrite the one the author wrote down — and an interior scene
            # went back to being lit by a blue daylight gradient.
            self.sky = spec["sky"]
        self._build_palette()

    # -- construction -------------------------------------------------------

    def _build_palette(self):
        """Every colour in this scene that has a name worth asserting against.

        Materials come from the models that are actually staged, so the names
        an author writes in a check are the names they wrote in the `.wam`. A
        material name shared by two models with different colours is kept only
        in its qualified form — resolving it to whichever model was staged
        first would be a silent wrong answer about a colour.
        """
        self.palette = {}
        clash = set()
        for key, inst in self._instances.items():
            for mname, rgb in inst["mdl"].mesh.materials:
                rgb = tuple(float(c) for c in rgb)
                self.palette["%s.%s" % (key, mname)] = rgb
                if mname in self.palette and self.palette[mname] != rgb:
                    clash.add(mname)
                else:
                    self.palette[mname] = rgb
        for mname in clash:
            self.palette.pop(mname, None)
        self.palette["sky.top"] = tuple(float(c) for c in self.sky[0])
        self.palette["sky.horizon"] = tuple(float(c) for c in self.sky[1])
        if self.fog is not None:
            self.palette["fog"] = tuple(float(c) for c in self.fog["color"])

    def _load_zone(self, path, chunks):
        """Stage a compiled zone's terrain and props.

        A `.cine` names the `.zone` source, not the build product, because the
        author should not have to know where the zone compiler puts things.
        Look in the places it actually puts them.
        """
        stem = os.path.splitext(os.path.basename(path))[0] + "_scene.npz"
        here = os.path.dirname(os.path.abspath(path))
        tried = [os.path.join("out", stem), os.path.join(here, "out", stem),
                 os.path.join(here, stem),
                 os.path.splitext(path)[0] + "_scene.npz"]
        npz = next((p for p in tried if os.path.exists(p)), None)
        if npz is None:
            raise WamError("no compiled zone for %s — run `python3 -m wam.zone "
                           "%s` first (looked in %s)"
                           % (path, path, ", ".join(tried)), 0, "")
        d = np.load(npz)
        schema = int(d["schema"]) if "schema" in d else 0
        if schema != SCENE_SCHEMA:
            raise WamError("scene dump %s is schema %d, this build reads %d — "
                           "recompile the zone" % (npz, schema, SCENE_SCHEMA), 0, "")
        chunks.append((d["V"], d["T"], d["M"], d["UV"], d["tex"], None))
        self.colors.extend([tuple(c) for c in d["colors"]])
        # A compiled zone dump carries colours only, so terrain declares none.
        self.pbr.extend([None] * len(d["colors"]))
        self.sky = (tuple(d["sky"][0]), tuple(d["sky"][1]))
        fr = d["fog_range"]
        self.fog = dict(color=tuple(d["fog_color"]), start=float(fr[0]),
                        end=float(fr[1]), max=float(fr[2]))
        H = d["heights"]
        gx, gz = d["grid_x"], d["grid_z"]

        def height_at(x, z):
            u = np.array([(x - gx[0]) / max(gx[1] - gx[0], 1e-9)])
            v = np.array([(z - gz[0]) / max(gz[1] - gz[0], 1e-9)])
            from .zone import bilinear
            return float(bilinear(H, u, v)[0])

        self.height_at = height_at
        self.terrain = (H, gx, gz)
        nz, nx = H.shape
        self._edge_idx = np.array(
            [j * nx + i for j in range(nz) for i in range(nx)
             if j in (0, nz - 1) or i in (0, nx - 1)], dtype=int)

    def _merge(self, chunks):
        """One vertex soup and one atlas. Atlases are stacked vertically and
        every chunk's v coordinate is remapped into its band."""
        if not chunks:
            self.V = np.zeros((0, 3)); self.T = np.zeros((0, 3), int)
            self.M = np.zeros(0, int); self.uv = None; self.atlas = None
            self.vcols = None
            return
        texd = [c[4] for c in chunks if c[4] is not None]
        width = max((t.shape[1] for t in texd), default=0)
        heights = [t.shape[0] for t in texd]
        total = sum(heights) or 1
        atlas = (np.zeros((total, width, 3), dtype=np.float32)
                 if texd else None)
        Vs, Ts, Ms, UVs, VCs = [], [], [], [], []
        voff = 0
        yoff = 0
        ti = 0
        for V, T, M, uv, tex, vcols in chunks:
            Vs.append(V); Ts.append(np.asarray(T) + voff); Ms.append(M)
            if atlas is not None:
                if tex is not None:
                    h, w = tex.shape[:2]
                    atlas[yoff:yoff + h, :w] = tex
                    u = np.asarray(uv, dtype=float).copy()
                    u[:, 0] = u[:, 0] * w / width
                    u[:, 1] = (u[:, 1] * h + yoff) / total
                    UVs.append(u)
                    yoff += h
                else:
                    UVs.append(np.zeros((len(V), 2)))
            VCs.append(vcols if vcols is not None else np.zeros((len(V), 3)))
            voff += len(V)
            ti += 1
        self.V = np.concatenate(Vs)
        self.T = np.concatenate(Ts)
        self.M = np.concatenate(Ms)
        self.atlas = atlas
        self.uv = np.concatenate(UVs) if atlas is not None else None
        self.vcols = None
        self._base_V = self.V.copy()

    def _add_ground(self):
        """A horizon-correct floor for staged shots with no zone.

        A polygon fan shades with visible diagonal seams and its rim is a
        visible cliff. This is a flat grid, large enough that its edge sits
        past the fog's end, so it fades out instead of stopping.
        """
        R = GROUND_R
        n = 16
        xs = np.linspace(-R, R, n)
        zs = np.linspace(-R, R, n)
        XX, ZZ = np.meshgrid(xs, zs)
        V = np.stack([XX.ravel(), np.zeros(XX.size), ZZ.ravel()], axis=1)
        tris = []
        for j in range(n - 1):
            for i in range(n - 1):
                a = j * n + i
                tris += [[a, a + n, a + 1], [a + 1, a + n, a + n + 1]]
        T = np.array(tris, dtype=int) + len(self.V)
        base = len(self.V)
        peri = [j * n + i for j in range(n) for i in range(n)
                if j in (0, n - 1) or i in (0, n - 1)]
        self._edge_idx = np.array([base + k for k in peri], dtype=int)
        self.floor = 0.0
        col = tuple(0.5 * np.array(self.sky[1]) + 0.5 * np.array([0.42, 0.44, 0.36]))
        self.colors.append(col)
        self.pbr.append(None)
        M = np.full(len(T), len(self.colors) - 1, dtype=int)
        self.V = np.concatenate([self.V, V])
        self.T = np.concatenate([self.T, T])
        self.M = np.concatenate([self.M, M])
        if self.uv is not None:
            self.uv = np.concatenate([self.uv, np.zeros((len(V), 2))])
        self._base_V = self.V.copy()

    def _add_shadows(self):
        """Project each flagged caster onto the ground along the sun.

        Not a shadow map: the caster's own triangles are flattened onto the
        floor and drawn dark, which is exact in silhouette, deterministic, and
        costs one matmul per frame. It suits a renderer whose whole aesthetic
        is flat honesty, and it degrades gracefully — a shape you recognise,
        not a soft blob.
        """
        if not self._shadow_specs:
            return
        sun = self.sun if self.sun is not None else np.array([-0.45, 0.85, 0.40])
        sun = np.asarray(sun, dtype=float)
        sun = sun / max(np.linalg.norm(sun), 1e-9)
        if sun[1] < 0.15:
            # A sun at or below the horizon projects a shadow to infinity.
            # Refusing is better than emitting geometry that runs off the world.
            raise WamError("light: the sun is at or below the horizon, so a "
                           "shadow cannot be projected — raise elevation above "
                           "about 9 degrees", 0, "")
        self.colors.append((0.0, 0.0, 0.0))
        self.pbr.append(None)
        shadow_mat = len(self.colors) - 1
        for v0, v1, base_y in self._shadow_specs:
            src = self._base_V[v0:v1]
            tri_mask = ((self.T >= v0) & (self.T < v1)).all(axis=1)
            tris = self.T[tri_mask] - v0
            start = len(self.V)
            flat = self._project_to_floor(src, sun, base_y)
            self.V = np.concatenate([self.V, flat])
            self.T = np.concatenate([self.T, tris + start])
            self.M = np.concatenate([self.M, np.full(len(tris), shadow_mat)])
            if self.uv is not None:
                self.uv = np.concatenate([self.uv, np.zeros((len(flat), 2))])
            self.shadows.append((v0, v1, start, base_y))
        self._base_V = self.V.copy()
        self._sun_dir = sun

    def _project_to_floor(self, pts, sun, base_y):
        """Slide each point down the sun direction until it meets the floor,
        then lift it a hair so it does not z-fight with the ground."""
        floor = np.full(len(pts), base_y, dtype=float)
        if getattr(self, "terrain", None) is not None:
            floor = np.array([self.height_at(p[0], p[2]) for p in pts])
        t = (pts[:, 1] - floor) / max(sun[1], 1e-6)
        out = pts - t[:, None] * sun[None, :]
        out[:, 1] = floor + 0.01
        return out

    # -- per frame ----------------------------------------------------------

    def pose(self, t, seconds=None):
        """Vertices for a frame. Only actors move; everything else is a copy.

        `t` is where we are in the shot, 0..1; `seconds` is how long the shot
        runs. An animation advances by wall-clock seconds over its own `dur`,
        not by shot fraction — otherwise a 1.4s walk cycle spread over a ten
        second shot plays once, at a seventh speed, and the only symptom is
        that everybody moves like they are underwater.
        """
        if not self.actors:
            self._last_V = self._base_V
            return self._base_V
        V = self._base_V.copy()
        for a in self.actors:
            mdl = a["model"]
            if a["anim"] and seconds is not None:
                loops = t * float(seconds) / mdl.anim_dur(a["anim"])
            else:
                loops = t
            local = mdl.posed(a["anim"], a["phase"] + loops)  # still unit-height
            V[a["start"]:a["start"] + len(local)] = _staged_verts(
                local, a["R"], a["base"])
        for v0, v1, sstart, base_y in self.shadows:
            V[sstart:sstart + (v1 - v0)] = self._project_to_floor(
                V[v0:v1], self._sun_dir, base_y)
        self._last_V = V
        return V

    def _split(self, sel):
        """`knight.gauntlet` -> the knight instance and `gauntlet`.

        The model name may itself contain dots, so the longest matching prefix
        wins rather than the first one.
        """
        parts = sel.split(".")
        for i in range(len(parts) - 1, 0, -1):
            head = ".".join(parts[:i])
            if head in self._instances:
                return self._instances[head], ".".join(parts[i:]), head
        return None, None, None

    def subject_range(self, sel):
        """Where a named subject lives in the merged soup, or None if it is a
        synthesized point rather than real geometry."""
        rng = self._subjects.get(sel)
        if rng is not None:
            return rng
        inst, sub, _ = self._split(sel)
        if inst is None:
            return None
        pr = inst["mdl"].local_range(sub) if sub else None
        if pr is None:
            return None
        return inst["start"] + pr[0], inst["start"] + pr[1]

    def resolves(self, sel):
        """Can this name be measured? Used to decide what a shot is about."""
        if sel in self._subjects:
            return True
        inst, sub, _ = self._split(sel)
        if inst is None:
            return False
        try:
            inst["mdl"].local_point(sub)
            return True
        except wchecks.CheckError:
            return inst["mdl"].local_range(sub) is not None

    def subject(self, sel):
        """Vertices belonging to a named model, or to a part of one.

        The same names `look at=` uses, so a shot check and a camera aim
        cannot drift apart by referring to different things. A name that
        resolves to a single point — a marker, a bone end — comes back as one
        vertex, which is what makes `visible(crown.break)` mean "can the camera
        see that spot" rather than "can it see the crown".
        """
        V = self._last_V if self._last_V is not None else self._base_V
        rng = self._subjects.get(sel)
        if rng is not None:
            a, b = rng
            return V[a:b]
        inst, sub, _ = self._split(sel)
        if inst is None:
            return None
        pr = inst["mdl"].local_range(sub)
        if pr is not None:
            a = inst["start"] + pr[0]
            b = inst["start"] + pr[1]
            return V[a:b]
        try:
            p = inst["mdl"].local_point(sub)
        except wchecks.CheckError:
            return None
        return (p @ inst["R"].T + inst["base"])[None, :]

    def target(self, name):
        """Resolve `look at=` against models, parts, bones, markers and points.

        Deliberately the same vocabulary as the model's own checks: an author
        who can write `assert gap(hand, hilt) < 0.01` should not have to learn
        a second naming scheme to point a camera at the hilt.
        """
        if name in self._targets:
            return self._targets[name]
        if name.startswith("("):
            return _vec(name, 0, "")[0]
        inst, sub, head = self._split(name)
        if inst is not None:
            try:
                p = inst["mdl"].local_point(sub)
            except wchecks.CheckError:
                raise WamError(
                    "look target %r: %r names no marker, bone or part of %r — "
                    "it has: %s" % (name, sub, head,
                                    ", ".join(inst["mdl"].local_names()) or "none"),
                    0, "")
            return p @ inst["R"].T + inst["base"]
        raise WamError("no look target %r in scene %r — known targets: %s "
                       "(or <model>.<marker|bone|part>, or a literal (x,y,z))"
                       % (name, self.name,
                          ", ".join(sorted(self._targets)) or "none"), 0, "")


# ---------------------------------------------------------------------------
# camera
# ---------------------------------------------------------------------------

class Camera:
    """Where the eye is and what it looks at, for any t in 0..1."""

    def __init__(self, shot, scene):
        self.shot = shot
        self.scene = scene
        self._fov = shot["fov"]
        self.fov = (float(str(self._fov).split("..")[0])
                    if isinstance(self._fov, str) else self._fov)
        self.ease = EASINGS[shot.get("ease", "linear")]
        self.keys = sorted(shot["eye"], key=lambda k: k[0])
        self.orbit = shot["orbit"]
        self.look_name = shot["look"]

    def fov_at(self, t):
        """The lens at phase t. A bare number holds; `a..b` interpolates.

        A camera that only translates can dolly and it can orbit; it cannot do
        the move where the subject holds still and the world changes shape
        around it.
        """
        if not isinstance(self._fov, str):
            return self._fov
        a, b = (float(x) for x in self._fov.split(".."))
        return a + (b - a) * self.ease(min(max(t, 0.0), 1.0))

    def _range(self, spec, t):
        """`175..145` interpolates; a bare number is constant."""
        if ".." in spec:
            a, b = (float(x) for x in spec.split(".."))
            return a + (b - a) * t
        return float(spec)

    def eye_at(self, t):
        if self.orbit:
            o = self.orbit
            c = self.scene.target(o["around"])
            radius = self._range(o.get("radius", "50"), t)
            arc = math.radians(float(o.get("arc", 0))) * t
            height = self._range(o.get("height", "0"), t)
            a0 = math.atan2(-1.0, 0.0)
            ang = a0 + arc
            return c + np.array([math.cos(ang) * radius, height,
                                 math.sin(ang) * radius])
        te = self.ease(min(max(t, 0.0), 1.0))
        ks = self.keys
        if len(ks) == 1:
            p, pos, absolute = ks[0]
            return self._ground(pos, absolute)
        for i in range(len(ks) - 1):
            p0, a, aa = ks[i]
            p1, b, ba = ks[i + 1]
            if te <= p1 or i == len(ks) - 2:
                span = max(p1 - p0, 1e-9)
                u = min(max((te - p0) / span, 0.0), 1.0)
                pa = self._ground(a, aa)
                pb = self._ground(b, ba)
                return pa + (pb - pa) * u
        return self._ground(ks[-1][1], ks[-1][2])

    def _ground(self, pos, absolute):
        """Heights are ground-relative unless the author wrote ~.

        Absolute y is almost never what someone means in a zone, and guessing
        it is how a camera ends up inside a mountain.
        """
        if absolute:
            return pos
        out = pos.copy()
        out[1] += self.scene.height_at(pos[0], pos[2])
        return out

    def look_at(self, t):
        return self.scene.target(self.look_name) if self.look_name else \
            self.eye_at(t) + np.array([0.0, 0.0, 1.0])


# ---------------------------------------------------------------------------
# measurement: the camera-space analogues of gap/clip
# ---------------------------------------------------------------------------

def _project(V, eye, look, fov, width, height):
    """Screen coords and depth, matching render_view's camera exactly."""
    f = np.asarray(look, dtype=float) - np.asarray(eye, dtype=float)
    f = f / max(np.linalg.norm(f), 1e-9)
    r = np.cross(f, np.array([0.0, 1.0, 0.0]))
    r = r / max(np.linalg.norm(r), 1e-9)
    u = np.cross(r, f)
    R = np.stack([r, u, -f])
    Vc = (V - np.asarray(eye, dtype=float)) @ R.T
    z = -Vc[:, 2]
    fl = 1.0 / math.tan(math.radians(fov) / 2)
    aspect = width / height
    with np.errstate(divide="ignore", invalid="ignore"):
        sx = (Vc[:, 0] * fl / aspect / np.where(z > 1e-6, z, 1e-6) * 0.5 + 0.5)
        sy = (0.5 - Vc[:, 1] * fl / np.where(z > 1e-6, z, 1e-6) * 0.5)
    return sx, sy, z


def frames(scene, sel, eye, look, fov, width, height):
    """Fraction of frame *height* a subject occupies. The subject-size check."""
    V = scene.subject(sel)
    if V is None or not len(V):
        return 0.0
    sx, sy, z = _project(V, eye, look, fov, width, height)
    vis = z > 1e-6
    if not vis.any():
        return 0.0
    return float(sy[vis].max() - sy[vis].min())


def inframe(scene, sel, eye, look, fov, width, height):
    """Fraction of the subject's screen box that is inside the frame.

    `offscreen` answers "has the centre left"; this answers "is any of it being
    cropped", which is the one that catches a head clipped by the frame edge
    while the body is comfortably inside.
    """
    V = scene.subject(sel)
    if V is None or not len(V):
        return 0.0
    sx, sy, z = _project(V, eye, look, fov, width, height)
    vis = z > 1e-6
    if not vis.any():
        return 0.0
    x0, x1 = float(sx[vis].min()), float(sx[vis].max())
    y0, y1 = float(sy[vis].min()), float(sy[vis].max())
    area = max(x1 - x0, 1e-9) * max(y1 - y0, 1e-9)
    ix = max(0.0, min(x1, 1.0) - max(x0, 0.0))
    iy = max(0.0, min(y1, 1.0) - max(y0, 0.0))
    return float(min(1.0, (ix * iy) / area))


def centered(scene, sel, eye, look, fov, width, height):
    """Horizontal placement, -1 at the left edge and +1 at the right."""
    V = scene.subject(sel)
    if V is None or not len(V):
        return 0.0
    sx, sy, z = _project(V.mean(axis=0)[None, :], eye, look, fov, width, height)
    if z[0] <= 1e-6:
        return 0.0
    return float((sx[0] - 0.5) * 2.0)


def headroom(scene, sel, eye, look, fov, width, height):
    """Space above the subject's top, as a fraction of frame height.

    Negative means the top is cropped, which is the difference between a tight
    frame and scalping someone.
    """
    V = scene.subject(sel)
    if V is None or not len(V):
        return 0.0
    sx, sy, z = _project(V, eye, look, fov, width, height)
    vis = z > 1e-6
    if not vis.any():
        return 0.0
    return float(sy[vis].min())


def gap(scene, a, b):
    """Closest approach between two staged models, in metres."""
    A, B = scene.subject(a), scene.subject(b)
    if A is None or B is None or not len(A) or not len(B):
        return 0.0
    # Subsampled: this is a "do they interpenetrate" question, and the answer
    # does not change in the sixth decimal place.
    A = A[::max(1, len(A) // 400)]
    B = B[::max(1, len(B) // 400)]
    d = np.linalg.norm(A[:, None, :] - B[None, :, :], axis=2)
    return float(d.min())


def grounded(scene, sel):
    """How far the subject's lowest point sits above the ground beneath it.

    Zero is standing on the floor, negative is buried, positive is floating.
    """
    V = scene.subject(sel)
    if V is None or not len(V):
        return 0.0
    k = int(np.argmin(V[:, 1]))
    low = V[k]
    floor = scene.height_at(low[0], low[2])
    if getattr(scene, "terrain", None) is None and scene.floor is not None:
        floor = scene.floor
    return float(low[1] - floor)


def facing(scene, sel, eye):
    """Degrees between the way a staged model faces and the way to the camera.

    0 is looking straight down the lens, 180 is the back of the head. `yaw=`
    is a number the author already wrote; this says what it came out as once
    everything else was placed.
    """
    inst = scene._instances.get(sel)
    if inst is None:
        inst, _, _ = scene._split(sel)
    if inst is None:
        return 180.0
    fwd = inst["R"] @ np.array([0.0, 0.0, 1.0])
    fwd[1] = 0.0
    n = np.linalg.norm(fwd)
    if n < 1e-9:
        return 180.0
    fwd = fwd / n
    to_eye = np.asarray(eye, dtype=float) - inst["base"]
    to_eye[1] = 0.0
    n2 = np.linalg.norm(to_eye)
    if n2 < 1e-9:
        return 180.0
    return float(math.degrees(math.acos(
        float(np.clip(np.dot(fwd, to_eye / n2), -1.0, 1.0)))))


def offscreen(scene, sel, eye, look, fov, width, height):
    """How far outside the frame the subject's centre sits. 0 when inside."""
    V = scene.subject(sel)
    if V is None or not len(V):
        return 0.0
    sx, sy, z = _project(V.mean(axis=0)[None, :], eye, look, fov, width, height)
    if z[0] <= 1e-6:
        return 2.0                     # behind the camera is as off as it gets
    dx = max(0.0, abs(float(sx[0]) - 0.5) - 0.5)
    dy = max(0.0, abs(float(sy[0]) - 0.5) - 0.5)
    return float(max(dx, dy) * 2.0)


OCCLUDER_GRID = 128


def _depth_grid(V_posed, T, eye, look, fov, width, height, window=None):
    """Nearest depth per cell of a coarse screen grid.

    Splatting *vertices* is not enough: a wall is four corners and nothing in
    between, so a subject standing behind it would read as perfectly visible.
    Triangles that cover more than a cell are filled in properly; the rest are
    their corners, which is all they are.

    `window` is a screen-space box to resolve instead of the whole frame. A
    fixed grid over the frame gives a distant subject only a handful of cells,
    most of them on its own silhouette edge, and the resulting boundary error
    scales with how small the subject is — a visibility number that changes
    with focal length is not a measurement.
    """
    G = OCCLUDER_GRID
    depth = np.full((G, G), np.inf)
    if not len(T):
        return depth
    x0, y0, x1, y1 = window if window is not None else (0.0, 0.0, 1.0, 1.0)
    sx, sy, z = _project(V_posed, eye, look, fov, width, height)
    gx = (sx - x0) / max(x1 - x0, 1e-9) * (G - 1)
    gy = (sy - y0) / max(y1 - y0, 1e-9) * (G - 1)

    tri_x, tri_y, tri_z = gx[T], gy[T], z[T]
    front = (tri_z > 1e-6).all(axis=1)
    # Drop whatever cannot touch the window before doing any work on it. With a
    # window tight around a small subject, nearly every triangle in a zone would
    # otherwise count as "large" and go down the slow path.
    front &= ~((tri_x.min(axis=1) > G - 1) | (tri_x.max(axis=1) < 0)
               | (tri_y.min(axis=1) > G - 1) | (tri_y.max(axis=1) < 0))
    tri_x, tri_y, tri_z = tri_x[front], tri_y[front], tri_z[front]
    if not len(tri_x):
        return depth

    # Corners first — cheap, vectorized, and exact for small triangles.
    fx, fy, fz = tri_x.ravel(), tri_y.ravel(), tri_z.ravel()
    ok = (fx >= 0) & (fx <= G - 1) & (fy >= 0) & (fy <= G - 1)
    if ok.any():
        np.minimum.at(depth, (fy[ok].astype(int), fx[ok].astype(int)), fz[ok])

    # Then fill the ones big enough to hide something.
    spanx = tri_x.max(axis=1) - tri_x.min(axis=1)
    spany = tri_y.max(axis=1) - tri_y.min(axis=1)
    big = np.where((spanx > 1.5) | (spany > 1.5))[0]
    if len(big) > 20000:
        # Only reachable if a whole zone is jammed into one subject's window,
        # which means the numbers would be wrong rather than merely slow.
        big = big[np.argsort(-(spanx[big] * spany[big]))[:20000]]
    for k in big:
        x0 = max(int(np.floor(tri_x[k].min())), 0)
        x1 = min(int(np.ceil(tri_x[k].max())), G - 1)
        y0 = max(int(np.floor(tri_y[k].min())), 0)
        y1 = min(int(np.ceil(tri_y[k].max())), G - 1)
        if x1 < x0 or y1 < y0:
            continue
        ys, xs = np.mgrid[y0:y1 + 1, x0:x1 + 1]
        ax, ay = tri_x[k, 0], tri_y[k, 0]
        bx, by = tri_x[k, 1], tri_y[k, 1]
        cx, cy = tri_x[k, 2], tri_y[k, 2]
        den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(den) < 1e-9:
            continue
        w0 = ((by - cy) * (xs - cx) + (cx - bx) * (ys - cy)) / den
        w1 = ((cy - ay) * (xs - cx) + (ax - cx) * (ys - cy)) / den
        w2 = 1.0 - w0 - w1
        inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        if not inside.any():
            continue
        zz = w0 * tri_z[k, 0] + w1 * tri_z[k, 1] + w2 * tri_z[k, 2]
        sub = depth[y0:y1 + 1, x0:x1 + 1]
        np.minimum(sub, np.where(inside, zz, np.inf), out=sub)
    return depth


def visible(scene, sel, V_posed, eye, look, fov, width, height):
    """How much of what you would see of a subject is not blocked by something
    else. 1.0 is a clear view; 0.0 is completely hidden.

    Two definitions were wrong before this one. Sampling every vertex counts
    the far side of a solid object as occluded, which it always is, so a
    perfectly clear model measured about 0.7 and no author could pick a
    threshold. Excluding only the subject's own surface still counts its
    neighbours — a helmet hides the back of the head it is on.

    So the measurement is over the subject's *screen footprint*: rasterize the
    subject alone, rasterize everything else, and ask what fraction of the
    cells the subject covers are still nearest to the camera. That is the
    silhouette a viewer actually sees, it is 1.0 for anything unobstructed
    however it is built, and what it loses is exactly what got covered up.

    Being off the edge of frame is not occlusion — that is what `offscreen` and
    `frames` measure, and one number should fail for one reason.
    """
    rng = scene.subject_range(sel)
    eye = np.asarray(eye, dtype=float)
    if rng is None:
        # A marker or bone end: no geometry, so it is one point, and the whole
        # scene is a potential occluder.
        V = scene.subject(sel)
        if V is None or not len(V):
            return 0.0
        sx, sy, z = _project(V, eye, look, fov, width, height)
        if z[0] <= 1e-6 or not (0 <= sx[0] < 1 and 0 <= sy[0] < 1):
            return 0.0
        win = (sx[0] - 0.02, sy[0] - 0.02, sx[0] + 0.02, sy[0] + 0.02)
        depth = _depth_grid(V_posed, scene.T, eye, look, fov, width, height, win)
        G = OCCLUDER_GRID
        d = depth[int((sy[0] - win[1]) / 0.04 * (G - 1)),
                  int((sx[0] - win[0]) / 0.04 * (G - 1))]
        return 1.0 if z[0] <= d * 1.01 + 1e-3 else 0.0

    a, b = rng
    mine = ((scene.T >= a) & (scene.T < b)).all(axis=1)
    if not mine.any():
        return 0.0
    Vs = V_posed[a:b]
    sx, sy, z = _project(Vs, eye, look, fov, width, height)
    front = z > 1e-6
    if not front.any():
        return 0.0
    pad = 0.01
    win = (float(sx[front].min()) - pad, float(sy[front].min()) - pad,
           float(sx[front].max()) + pad, float(sy[front].max()) + pad)
    sub = _depth_grid(V_posed, scene.T[mine], eye, look, fov, width, height, win)
    covered = np.isfinite(sub)
    if not covered.any():
        return 0.0
    others = _depth_grid(V_posed, scene.T[~mine], eye, look, fov, width, height,
                         win)
    clear = covered & (sub <= others * 1.01 + 1e-3)
    return float(clear.sum()) / float(covered.sum())


def sees_edge(scene, V_posed, eye, look, fov, width, height):
    """Is the rim of the ground visible in frame?

    "The huntsman is standing on the edge of a void, the terrain just stops"
    was a viewer-reported bug, which is the worst kind: invisible to whoever
    framed the shot. The test is direct rather than clever — project the
    perimeter of the ground and ask whether any of it lands inside the frame
    without being fogged out. Fog hiding the rim is the intended outcome, not a
    failure, so a rim buried in fog does not count.

    Returns (fraction of the rim visible, side) where side names which way you
    are looking off the world.
    """
    idx = getattr(scene, "_edge_idx", None)
    if idx is None or not len(idx):
        return 0.0, None
    P = V_posed[idx]
    sx, sy, z = _project(P, eye, look, fov, width, height)
    inside = (z > 1e-6) & (sx >= 0) & (sx <= 1) & (sy >= 0) & (sy <= 1)
    if not inside.any():
        return 0.0, None
    if scene.fog is not None:
        f = scene.fog
        strength = np.clip((z - f["start"]) / max(f["end"] - f["start"], 1e-6),
                           0, 1) * f.get("max", 0.85)
        inside &= strength < 0.75        # fogged to near-nothing is not "seen"
    if not inside.any():
        return 0.0, None
    # In frame is not the same as visible. A zone's rim sits behind whatever
    # terrain the zone was built to put in the way, and warning about an edge
    # hidden behind a mountain is a lint that cries wolf on correct scenes.
    G = OCCLUDER_GRID
    depth = _depth_grid(V_posed, scene.T, eye, look, fov, width, height)
    gi = np.clip((sy * (G - 1)).astype(int), 0, G - 1)
    gj = np.clip((sx * (G - 1)).astype(int), 0, G - 1)
    inside &= z <= depth[gi, gj] * 1.02 + 1e-3
    if not inside.any():
        return 0.0, None
    seen = P[inside]
    c = V_posed.mean(axis=0)
    dx, dz = (seen[:, 0] - c[0]).mean(), (seen[:, 2] - c[2]).mean()
    side = ("east" if dx > 0 else "west") if abs(dx) > abs(dz) else \
           ("north" if dz > 0 else "south")
    return float(inside.mean()), side


def clearance(scene, V_posed, eye):
    """Closest approach between the camera and any surface, in metres.

    Negative when the camera is under the terrain, which is the surreal-render
    case: backfaces cull and you get sky below the horizon.
    """
    eye = np.asarray(eye, dtype=float)
    d = float(np.linalg.norm(V_posed - eye, axis=1).min()) if len(V_posed) else 1e9
    floor_y = None
    if getattr(scene, "terrain", None) is not None:
        floor_y = scene.height_at(eye[0], eye[2])
    elif getattr(scene, "floor", None) is not None:
        floor_y = scene.floor
    if floor_y is not None and eye[1] < floor_y:
        return -(floor_y - eye[1])
    return d


# ---------------------------------------------------------------------------
# shot lint and checks
# ---------------------------------------------------------------------------

LINT_PHASES = 8

# Radius of the staged floor. Auto fog is tuned against it so the rim is gone
# before you reach it.
GROUND_R = 600.0


class Series:
    """One measurement sampled across the shot.

    A bare name in a check means the worst frame, because that is the frame
    that ruins the shot. `at 40%` means that moment specifically, which is what
    you need to say "the push-in actually pushes in" or "the still part is
    still before the beat".
    """

    def __init__(self, ts, vals, worst="min"):
        self.ts = list(ts)
        self.vals = list(vals)
        self.worst = worst

    def value(self):
        if not self.vals:
            return 0.0
        return min(self.vals) if self.worst == "min" else max(self.vals)

    def at(self, t):
        if not self.vals:
            return 0.0
        t = min(max(t, self.ts[0]), self.ts[-1])
        i = int(np.searchsorted(self.ts, t))
        if i <= 0:
            return self.vals[0]
        if i >= len(self.ts):
            return self.vals[-1]
        span = self.ts[i] - self.ts[i - 1]
        u = 0.0 if span <= 0 else (t - self.ts[i - 1]) / span
        return self.vals[i - 1] + (self.vals[i] - self.vals[i - 1]) * u


_AT = re.compile(r"\s+at\s+(-?[\d.]+)\s*%")

# Python keywords are legal WAM names. `crown.break` is this spec's own
# canonical example of a marker, and the check grammar rides on ast.parse, so
# it has to survive a parser that disagrees. Every keyword token is mangled on
# the way in and each dotted component is unmangled on the way out.
#
# Mangling has to cover the leading identifier too, not just attributes: an
# actor can be given `as=break`, and `visible(break.tip)` is the same crash for
# the same reason.
_KW_MANGLE = "_kw_"
_KEYWORDS = frozenset(__import__("keyword").kwlist)
_KW_TOKEN = re.compile(r"\b(%s)\b" % "|".join(sorted(_KEYWORDS)))


_COLOR_CALL = re.compile(
    r"\bcolor\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*([\w.]+)\s*\)")
_HEX_CALL = re.compile(r"\bhex\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)")


def _numkey(v):
    """One spelling for a number in an env key, so the regex pre-scan and the
    parsed expression agree on what they are naming."""
    return "%g" % float(v)


def sample_px(img, x, y):
    """The rendered pixel at a screen position, 0..1 from the top left."""
    h, w = img.shape[:2]
    j = int(round(min(max(x, 0.0), 1.0) * (w - 1)))
    i = int(round(min(max(y, 0.0), 1.0) * (h - 1)))
    return np.asarray(img[i, j], dtype=float)


def to_hex(rgb):
    c = np.clip(np.asarray(rgb, dtype=float), 0.0, 1.0)
    return "#%02x%02x%02x" % tuple(int(round(v * 255)) for v in c)


class RGBSeries:
    """A sampled pixel colour. Reads out as hex, interpolates as colour."""

    def __init__(self, ts, vals):
        self.ts = list(ts)
        self.vals = [np.asarray(v, dtype=float) for v in vals]

    def at(self, t):
        t = min(max(t, self.ts[0]), self.ts[-1])
        i = int(np.searchsorted(self.ts, t))
        if i <= 0:
            return to_hex(self.vals[0])
        if i >= len(self.ts):
            return to_hex(self.vals[-1])
        span = self.ts[i] - self.ts[i - 1]
        u = 0.0 if span <= 0 else (t - self.ts[i - 1]) / span
        return to_hex(self.vals[i - 1] + (self.vals[i] - self.vals[i - 1]) * u)

    def value(self):
        # No single frame is the answer, so give the whole strip. It is one
        # line, and calibrating a `color()` bound is exactly what it is for.
        return " ".join(to_hex(v) for v in self.vals)


def _unmangle(name):
    """Undo the mangling, per dotted component and only where it applies.

    A blind `.replace(_KW_MANGLE, "")` would quietly rewrite an innocent part
    named `my_kw_thing`, so strip the prefix only when what is left is the
    keyword that put it there.
    """
    out = []
    for part in name.split("."):
        if part.startswith(_KW_MANGLE) and part[len(_KW_MANGLE):] in _KEYWORDS:
            part = part[len(_KW_MANGLE):]
        out.append(part)
    return ".".join(out)


def _resolve(env, key, at=None):
    v = env[key]
    if isinstance(v, (Series, RGBSeries)):
        return v.at(at) if at is not None else v.value()
    return v


def _number(v):
    """A colour readout is a string on purpose; arithmetic on it is a mistake
    worth naming rather than a TypeError from inside the evaluator."""
    if isinstance(v, str):
        raise WamError("%s is a colour, not a number — compare it with "
                       "color(x, y, <palette name>) instead of doing "
                       "arithmetic on it" % v, 0, "")
    return float(v)


def _value(text, env, line_no, shot_name, expr):
    """One side of an assertion, or a whole `measure` expression.

    Returns a float for every measurement except a colour readout, which is a
    string because that is what an author wants to see in the report.
    """
    import ast
    # `frames(x) at 100%` is not Python, so lift the phase out before
    # parsing and hand it to whatever the expression measured.
    text = text.strip()
    at = None
    m = _AT.search(text)
    if m:
        at = float(m.group(1)) / 100.0
        text = text[:m.start()] + text[m.end():]
    # A marker may carry a name Python reserves (`crown.break` is the
    # spec's own example), and the target vocabulary is deliberately one
    # resolver — so mangle keyword attributes past the parser and strip
    # the mangling when the dotted key is rebuilt for the env lookup.
    text = _KW_TOKEN.sub(lambda m: _KW_MANGLE + m.group(1), text)
    node = ast.parse(text.strip(), mode="eval").body

    def walk(n):
        if isinstance(n, ast.Constant):
            return float(n.value)
        if isinstance(n, ast.BinOp):
            a, b = _number(walk(n.left)), _number(walk(n.right))
            return {ast.Add: a + b, ast.Sub: a - b, ast.Mult: a * b,
                    ast.Div: a / b if b else float("inf")}[type(n.op)]
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
            return -walk(n.operand)
        if isinstance(n, ast.Name):
            key = _unmangle(n.id)
            if key not in env:
                raise WamError("shot %r: unknown value %r — known: %s"
                               % (shot_name, key, ", ".join(sorted(env))),
                               line_no, expr)
            return _resolve(env, key, at)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            fn = n.func.id
            keys = []
            for arg in n.args:
                if isinstance(arg, ast.Constant) and not isinstance(
                        arg.value, str):
                    keys.append(_numkey(arg.value))       # pixel coords
                elif isinstance(arg, ast.UnaryOp) and isinstance(
                        arg.op, ast.USub):
                    keys.append(_numkey(-arg.operand.value))
                else:
                    keys.append(_unmangle(arg.id if isinstance(arg, ast.Name)
                                          else ".".join(_dotted(arg))))
            full = "%s(%s)" % (_unmangle(fn), ", ".join(keys))
            if full not in env:
                raise WamError("shot %r: cannot measure %s — known: %s"
                               % (shot_name, full, ", ".join(sorted(env))),
                               line_no, expr)
            return _resolve(env, full, at)
        if isinstance(n, ast.Attribute):
            key = _unmangle(".".join(_dotted(n)))
            if key in env:
                return _resolve(env, key, at)
        raise WamError("shot %r: cannot evaluate %r" % (shot_name, expr),
                       line_no, expr)
    return walk(node)


def _measure(expr, env, line_no, shot_name):
    """A report-only readout, which may be a colour and so cannot be reached
    through a comparison."""
    return _value(expr, env, line_no, shot_name, expr)


def _eval_check(expr, env, line_no, shot_name):
    """Evaluate one shot assertion against measured values.

    Deliberately the same grammar the model checks use — `assert <expr> <op>
    <expr>` and `assert <expr> in lo..hi` — because an author should not have
    to learn a second assertion language to point it at a camera.
    """
    def value(text):
        return _value(text, env, line_no, shot_name, expr)

    if " in " in expr:
        lhs, rng = expr.split(" in ", 1)
        lo, hi = (float(x) for x in rng.strip().split(".."))
        got = _number(value(lhs))
        return got, lo <= got <= hi, "in %g..%g" % (lo, hi)
    for op in (">=", "<=", "==", ">", "<"):
        if op in expr:
            lhs, rhs = expr.split(op, 1)
            got, want = value(lhs), value(rhs)
            if isinstance(got, str) or isinstance(want, str):
                # Comparing colours: only equality means anything, and it means
                # the exact same hex.
                if op != "==":
                    raise WamError(
                        "shot %r: %r compares a colour with %s — a colour is "
                        "only ever == another colour. For 'close to', use "
                        "color(x, y, <palette name>) < <distance>"
                        % (shot_name, expr, op), line_no, expr)
                return got, str(got) == str(want), "== %s" % want
            good = {">": got > want, "<": got < want, ">=": got >= want,
                    "<=": got <= want, "==": abs(got - want) < 1e-9}[op]
            return got, good, "%s %g" % (op, want)
    raise WamError("shot %r: %r is not an assertion" % (shot_name, expr),
                   line_no, expr)


def _lhs_of(expr):
    """The measured side of an assertion, for the report line. Splitting on
    whitespace turns `gap(a, b) > 0.1` into `gap(a,`, which then reads as a
    different measurement than the one the author wrote."""
    if " in " in expr:
        return expr.split(" in ", 1)[0].strip()
    for op in (">=", "<=", "==", ">", "<"):
        if op in expr:
            return expr.split(op, 1)[0].strip()
    return expr.strip()


def _dotted(node):
    import ast
    out = []
    while isinstance(node, ast.Attribute):
        out.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        out.append(node.id)
    return list(reversed(out))


def lint_shot(shot, scene, cam, width, height, fps, warn, info):
    """Measure the shot before a single frame is rendered.

    Every retake this feature exists to prevent maps to one line here: a buried
    camera, geometry crossing the near plane, an occluded or off-frame subject,
    a subject too small to read.
    """
    # What this shot is *about*: whatever it aims at, plus whatever it asserts
    # on. Measuring every staged model instead would bury the subject under
    # background actors that are supposed to drift out of frame.
    named = set()
    if shot["look"] and scene.resolves(shot["look"]):
        named.add(shot["look"])
    for _, expr in shot["checks"]:
        for word in expr.replace("(", " ").replace(")", " ").replace(",", " ").split():
            if scene.resolves(word):
                named.add(word)

    # A subject the author has written a moment-qualified assertion about has
    # had its bar set deliberately, so the ambient warning stands down for it —
    # the same bargain `noclip` strikes in a model's checks. Not doing this made
    # the useful case unsayable: a reveal that begins on the back of someone's
    # head is a shot working exactly as written, and asserting that it *is*
    # hidden at 0% was itself what raised the warning.
    #
    # Only a moment-qualified assertion stands the warning down. A bare
    # `visible(x)` means the worst frame, which is the same thing the warning is
    # about, so there both should speak.
    asserted_visible, asserted_framed = set(), set()
    for _, expr in shot["checks"]:
        for m in re.finditer(
                r"visible\(\s*([A-Za-z_][\w.]*)\s*\)\s*at\s+[\d.]+\s*%", expr):
            asserted_visible.add(m.group(1))
        # and the same bargain for framing: a push-in that deliberately crops
        # its own subject is a shot, not a defect, and `inframe(x) at 0% > 0.9`
        # is the author saying where they wanted it whole.
        for m in re.finditer(
                r"(?:inframe|offscreen)\(\s*([A-Za-z_][\w.]*)\s*\)"
                r"\s*at\s+[\d.]+\s*%", expr):
            asserted_framed.add(m.group(1))

    # Pixels any check wants to look at. Sampling one means rendering the
    # frame, so this is opt-in: no colour check, no extra render.
    want_px, want_color = set(), []
    for _, expr in shot["checks"]:
        for m in _HEX_CALL.finditer(expr):
            want_px.add((float(m.group(1)), float(m.group(2))))
        for m in _COLOR_CALL.finditer(expr):
            want_px.add((float(m.group(1)), float(m.group(2))))
            want_color.append((float(m.group(1)), float(m.group(2)),
                               m.group(3)))
    for _, _, name in want_color:
        if name not in scene.palette:
            raise WamError(
                "shot %r: %r is not a colour in scene %r — it has: %s"
                % (shot["name"], name, scene.name,
                   ", ".join(sorted(scene.palette))), shot["line"], "")
    px_samples = {k: [] for k in want_px}

    # Pairs named together in one call, for gap(a, b).
    pairs = []
    for _, expr in shot["checks"]:
        for call in re.finditer(r"gap\(\s*([\w.]+)\s*,\s*([\w.]+)\s*\)", expr):
            if scene.resolves(call.group(1)) and scene.resolves(call.group(2)):
                pairs.append((call.group(1), call.group(2)))

    worst_clear = 1e9
    near_cross, under, offs, occl, edges = [], [], {}, {}, []
    sizes, agg = {}, {}
    ts, clears = [], []
    eyes, looks = [], []
    # Liveness: where each animated actor's vertices were at the top of the
    # shot, and the furthest any of them gets from there. Sampled, because the
    # question is "does this move at all", not "by exactly how much".
    live = {ai: dict(ref=None, travel=0.0, dist=1e9)
            for ai, a in enumerate(scene.actors) if a["anim"]}
    eye0 = None
    eye_travel = 0.0

    for i in range(LINT_PHASES):
        t = i / max(LINT_PHASES - 1, 1)
        pct = round(t * 100)
        V = scene.pose(t, shot["dur"])
        eye, look = cam.eye_at(t), cam.look_at(t)
        fov_t = cam.fov_at(t)
        ts.append(t)
        eyes.append(eye)
        looks.append(look)
        if eye0 is None:
            eye0 = eye
        eye_travel = max(eye_travel, float(np.linalg.norm(eye - eye0)))
        agg.setdefault("clearance(terrain)", []).append(
            eye[1] - (scene.height_at(eye[0], eye[2])
                      if getattr(scene, "terrain", None) is not None
                      else (scene.floor if scene.floor is not None else 0.0)))
        d = float(np.linalg.norm(np.asarray(look) - eye))
        agg.setdefault("lookdist", []).append(d)
        if d > 1e-9:
            f = (np.asarray(look) - eye) / d
            agg.setdefault("updot", []).append(abs(float(f[1])))
        for a, b in pairs:
            agg.setdefault("gap(%s, %s)" % (a, b), []).append(gap(scene, a, b))
        if px_samples:
            img = _render(scene, cam, V, t, width, height)
            for (x, y) in px_samples:
                px_samples[(x, y)].append(sample_px(img, x, y))
        for ai, rec in live.items():
            a = scene.actors[ai]
            v0 = a["start"]
            v1 = v0 + len(a["model"].V)
            pts = V[v0:v1:max(1, (v1 - v0) // 200)]
            if rec["ref"] is None:
                rec["ref"] = pts.copy()
            else:
                rec["travel"] = max(rec["travel"], float(
                    np.linalg.norm(pts - rec["ref"], axis=1).max()))
            rec["dist"] = min(rec["dist"],
                              float(np.linalg.norm(pts.mean(axis=0) - eye)))
        c = clearance(scene, V, eye)
        clears.append(c)
        worst_clear = min(worst_clear, c)
        if c < 0:
            under.append((pct, -c, eye))
        if len(scene.T) and c < 2.0:
            _, _, z = _project(V, eye, look, fov_t, width, height)
            zt = z[scene.T]
            n_str = int(((zt.min(axis=1) < 0.05) & (zt.max(axis=1) > 0.05)).sum())
            if n_str:
                near_cross.append((pct, n_str))
        frac, side = sees_edge(scene, V, eye, look, fov_t, width, height)
        if frac > 0:
            edges.append((pct, frac, side))
        for name in named:
            if scene.subject(name) is None:
                continue                  # a literal point has no geometry
            fr = frames(scene, name, eye, look, fov_t, width, height)
            sizes.setdefault(name, []).append(fr)
            off = offscreen(scene, name, eye, look, fov_t, width, height)
            if off > 0.02:
                offs.setdefault(name, []).append((pct, off))
            vis = visible(scene, name, V, eye, look, fov_t, width, height)
            if vis < 0.15:
                occl.setdefault(name, []).append((pct, 1 - vis))
            agg.setdefault("frames(%s)" % name, []).append(fr)
            agg.setdefault("visible(%s)" % name, []).append(vis)
            agg.setdefault("inframe(%s)" % name, []).append(
                inframe(scene, name, eye, look, fov_t, width, height))
            agg.setdefault("centered(%s)" % name, []).append(
                centered(scene, name, eye, look, fov_t, width, height))
            agg.setdefault("headroom(%s)" % name, []).append(
                headroom(scene, name, eye, look, fov_t, width, height))
            agg.setdefault("offscreen(%s)" % name, []).append(off)
            agg.setdefault("grounded(%s)" % name, []).append(
                grounded(scene, name))
            fac = facing(scene, name, eye)
            agg.setdefault("facing(%s)" % name, []).append(fac)
            # `facing(x, camera)` reads better in a check and is the spelling
            # people reach for; both name the same angle.
            agg.setdefault("facing(%s, camera)" % name, []).append(fac)

    # One line per problem, naming the worst frame. A warning per sampled phase
    # buries the shot that actually needs attention under twenty that do not.
    def span(items):
        return "%d%%..%d%%" % (items[0][0], items[-1][0]) if len(items) > 1 \
            else "%d%%" % items[0][0]

    if under:
        pct, depth, eye = max(under, key=lambda u: u[1])
        warn("shot %r %s: camera is under the ground, %.1fm at its deepest "
             "(%.0f,%.0f)" % (shot["name"], span(under), depth, eye[0], eye[2]))
    if near_cross:
        warn("shot %r %s: up to %d triangle(s) cross the near plane — clipped, "
             "not dropped, but the camera is passing through something"
             % (shot["name"], span(near_cross), max(n for _, n in near_cross)))
    if edges:
        pct, frac, side = max(edges, key=lambda e: e[1])
        warn("shot %r %s: the %s edge of the ground is in frame (%.0f%% of the "
             "rim visible at worst) — the world visibly stops. Pull the camera "
             "down or in, or bring the fog closer so the rim fades out"
             % (shot["name"], span(edges), side, frac * 100))
    for name, items in offs.items():
        if name in asserted_framed:
            continue
        pct, worst = max(items, key=lambda o: o[1])
        warn("shot %r %s: %r leaves frame (%.0f%% outside at %d%%)"
             % (shot["name"], span(items), name, worst * 100, pct))
    for name, items in occl.items():
        if name in asserted_visible:
            continue
        pct, worst = max(items, key=lambda o: o[1])
        warn("shot %r %s: %r is occluded (%.0f%% hidden at %d%%)"
             % (shot["name"], span(items), name, worst * 100, pct))

    # A dead animation is invisible in a still and obvious in motion, which is
    # the wrong way round: you find it after rendering the shot. Measure it in
    # pixels, because a 2mm twitch reads on a close-up and not at 80 metres,
    # and the author needs to know which one they have.
    px_per_m = height / (2.0 * math.tan(math.radians(cam.fov_at(0.5)) / 2))
    dead = []
    for ai, rec in live.items():
        a = scene.actors[ai]
        name = next((n for n, r in scene._subjects.items()
                     if r[0] == a["start"]), a["anim"])
        px = rec["travel"] * px_per_m / max(rec["dist"], 1e-6)
        if px < 1.0:
            dead.append((name, a["anim"], rec["travel"], px))
        else:
            info("shot %r: %r moves %.0f px over the shot (anim %r)"
                 % (shot["name"], name, px, a["anim"]))
    for name, anim_name, travel, px in dead:
        if travel <= 1e-9:
            warn("shot %r: %r plays anim %r and does not move at all — the "
                 "animation has no channels that reach this model's geometry"
                 % (shot["name"], name, anim_name))
        else:
            warn("shot %r: %r barely moves — anim %r travels %.3fm over the "
                 "shot, which is %.2f px at this framing. Give the shot longer "
                 "than %.2fs, move the camera closer, or hold the pose on "
                 "purpose with `place` instead of `actor`"
                 % (shot["name"], name, anim_name, travel, px, shot["dur"]))
    nframes = max(1, int(round(shot["dur"] * fps)))
    # No animated actors at all counts too: a locked camera over static
    # props is still the same picture N times.
    lens_moves = abs(cam.fov_at(0.0) - cam.fov_at(1.0)) > 1e-9
    if (len(dead) == len(live) and eye_travel < 1e-6 and nframes > 1
            and not lens_moves):
        warn("shot %r: nothing moves — the camera is locked off and every "
             "actor is still, so this renders %d identical frames"
             % (shot["name"], nframes))

    info("shot %r: camera clears geometry by %.1fm at its closest"
         % (shot["name"], worst_clear))
    for name, vals in sizes.items():
        info("shot %r: %r occupies %.0f%% of frame height at 0%%, %.0f%% at 100%%"
             % (shot["name"], name, vals[0] * 100, vals[-1] * 100))

    # ---- camera motion, as numbers you can put a bound on -------------------
    E = np.asarray(eyes, dtype=float)
    secs = max(float(shot["dur"]), 1e-6)
    steps = np.linalg.norm(np.diff(E, axis=0), axis=1) if len(E) > 1 else np.zeros(0)
    dt = secs / max(len(E) - 1, 1)
    speeds = steps / dt if len(steps) else np.zeros(1)
    accels = (np.abs(np.diff(speeds)) / dt) if len(speeds) > 1 else np.zeros(1)
    dirs = np.asarray(looks, dtype=float) - E
    nrm = np.linalg.norm(dirs, axis=1, keepdims=True)
    dirs = dirs / np.where(nrm < 1e-9, 1.0, nrm)
    swing = 0.0
    for k in range(1, len(dirs)):
        swing += math.degrees(math.acos(
            float(np.clip(np.dot(dirs[k], dirs[k - 1]), -1.0, 1.0))))
    dolly = float(steps.sum()) if len(steps) else 0.0

    env = {
        "travel": dolly,
        "speed": float(speeds.max()),
        "accel": float(accels.max()),
        "swing": swing,
        # World up is the camera's up in this renderer, by construction. It is
        # still worth exposing so "no accidental dutch angles" is assertable
        # rather than assumed.
        "roll": 0.0,
        "nearcross": float(max([n for _, n in near_cross], default=0)),
        "frames_written": float(nframes),
        "frame_w": float(width),
        "frame_h": float(height),
    }
    env["clearance"] = Series(ts, clears, "min")
    for k, vals in agg.items():
        # Which end is the bad end. Everything here is "more is worse" except
        # the framing and visibility measures, where less is.
        worst = "max" if k.split("(")[0] in ("offscreen", "facing") else "min"
        if k == "updot":
            worst = "max"
        env[k] = Series(ts, vals, worst)
    for ai, rec in live.items():
        a = scene.actors[ai]
        name = next((n for n, r in scene._subjects.items()
                     if r[0] == a["start"]), a["anim"])
        env["moves_px(%s)" % name] = (rec["travel"] * px_per_m
                                      / max(rec["dist"], 1e-6))
        env["cycles(%s)" % name] = secs / a["model"].anim_dur(a["anim"])
    for (x, y), vals in px_samples.items():
        env["hex(%s, %s)" % (_numkey(x), _numkey(y))] = RGBSeries(ts, vals)
    for x, y, name in want_color:
        want = np.asarray(scene.palette[name], dtype=float)
        env["color(%s, %s, %s)" % (_numkey(x), _numkey(y), name)] = Series(
            ts, [float(np.linalg.norm(v - want))
                 for v in px_samples[(x, y)]], "max")
    info("shot %r: camera travels %.2fm, peak %.2f m/s, look swings %.0f deg"
         % (shot["name"], dolly, float(speeds.max()), swing))
    return env


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def _render(scene, cam, V, t, width, height):
    """One frame. Shared by the renderer and by the lint, so a pixel a check
    measures is the same pixel the shot ships."""
    return wr.render_view(
        V, scene.T, scene.M, scene.colors,
        width=width, height=height, fov_deg=cam.fov_at(t),
        uv=scene.uv, tex=scene.atlas, sky=scene.sky, fog=scene.fog,
        mat_pbr=getattr(scene, "pbr", None),
        eye=cam.eye_at(t), look=cam.look_at(t),
        sun=scene.sun, fill=scene.fill, ambient=scene.ambient)


def _frame_size(film):
    """Long edge from `size`, the other from `aspect`. Letterboxing is the
    compiler's job, not a PIL script's."""
    a = film["aspect"]
    if a >= 1:
        return film["size"], max(1, int(round(film["size"] / a)))
    return max(1, int(round(film["size"] * a))), film["size"]


@wr.quiet_fp
def compile_cine(path, out_root=None, only=None, force=False, quiet=False):
    film = parse_cine(path)
    out_root = out_root or os.path.join("out", film["name"])
    width, height = _frame_size(film)
    warns, infos = [], []
    warn = warns.append
    info = infos.append

    cache = {}

    def loader(model_path):
        if model_path not in cache:
            cache[model_path] = Loaded(model_path)
        return cache[model_path]

    # Scenes are built once and reused by every shot that names them. This is
    # the whole performance story: only the pose and the camera move.
    built = {}
    manifest = []
    for shot in film["shots"]:
        if only and shot["name"] != only:
            continue
        sname = shot["scene"]
        if sname not in built:
            built[sname] = Scene(film["scenes"][sname], film, loader)
            for nm, count in sorted(built[sname]._ambiguous.items()):
                info("scene %r: %d models answer to %r, so it names the first "
                     "one — give the others as=<name> if you meant a different "
                     "one" % (sname, count, nm))
        scene = built[sname]
        cam = Camera(shot, scene)

        env = lint_shot(shot, scene, cam, width, height, film["fps"], warn, info)
        for line_no, expr in shot["checks"]:
            if expr.startswith("measure "):
                rest = expr[len("measure "):].split(None, 1)
                label, e = (rest + [rest[0]])[:2]
                got = _measure(e, env, line_no, shot["name"])
                info("shot %r: %s = %s" % (shot["name"], label, got
                     if isinstance(got, str) else "%.4f" % got))
                continue
            got, good, want = _eval_check(expr, env, line_no, shot["name"])
            if good:
                info("shot %r check: %s = %.4f (%s, ok)"
                     % (shot["name"], _lhs_of(expr), got, want))
            else:
                warn("shot %r: check failed — line %d: %s = %.4f, expected %s"
                     % (shot["name"], line_no, expr, got, want))

        n = max(1, int(round(shot["dur"] * film["fps"])))
        shot_dir = os.path.join(out_root, shot["name"])
        os.makedirs(shot_dir, exist_ok=True)
        done = os.path.join(shot_dir, "%04d.png" % (n - 1))
        if os.path.exists(done) and not force:
            info("shot %r: %d frames already rendered, skipping (--force to redo)"
                 % (shot["name"], n))
            manifest.append((shot, shot_dir, n))
            continue

        for i in range(n):
            t = i / max(n - 1, 1)
            V = scene.pose(t, shot["dur"])
            img = _render(scene, cam, V, t, width, height)
            wr.write_png(os.path.join(shot_dir, "%04d.png" % i), img)
        info("shot %r: %d frames -> %s" % (shot["name"], n, shot_dir))
        manifest.append((shot, shot_dir, n))

    for shot, shot_dir, n in manifest:
        _contact_sheet(shot_dir, n, os.path.join(out_root, shot["name"] + "_contact.png"))
    if not only:
        _assemble(manifest, out_root, film, info)

    if not quiet:
        for m in infos:
            print("info: " + m)
        for m in warns:
            print("WARN: " + m)
        print("cinematic %s: %d shot(s), %dx%d @ %gfps"
              % (film["name"], len(manifest), width, height, film["fps"]))
    return film, warns, infos


def _assemble(manifest, out_root, film, info):
    """Put the shots in narrative order and generate the transition frames.

    Frames are not copied — 2,000 duplicated PNGs to express an ordering would
    be silly. The output is an ffmpeg concat list, plus real generated frames
    for each dissolve, so the whole film is one command away without this
    module growing an encoder.
    """
    lines = []
    dis_dir = os.path.join(out_root, "_dissolve")
    made = 0
    for si, (shot, shot_dir, n) in enumerate(manifest):
        if shot["cut"] == "dissolve" and si > 0 and shot["dissolve"] > 0:
            prev_shot, prev_dir, prev_n = manifest[si - 1]
            k = max(1, int(round(shot["dissolve"] * film["fps"])))
            os.makedirs(dis_dir, exist_ok=True)
            for j in range(k):
                a = os.path.join(prev_dir, "%04d.png" % max(0, prev_n - k + j))
                b = os.path.join(shot_dir, "%04d.png" % min(j, n - 1))
                if not (os.path.exists(a) and os.path.exists(b)):
                    continue
                w = (j + 1) / (k + 1)
                blend = _read_png(a) * (1 - w) + _read_png(b) * w
                out = os.path.join(dis_dir, "%s_%s_%03d.png"
                                   % (prev_shot["name"], shot["name"], j))
                wr.write_png(out, blend)
                lines.append(out)
                made += 1
        for i in range(n):
            lines.append(os.path.join(shot_dir, "%04d.png" % i))
    listing = os.path.join(out_root, "assembly.txt")
    with open(listing, "w") as f:
        for p in lines:
            f.write("file '%s'\n" % os.path.abspath(p))
    info("assembly: %d frames in order%s -> %s"
         % (len(lines), " (%d dissolved)" % made if made else "", listing))
    info("       ffmpeg -f concat -safe 0 -r %g -i %s -pix_fmt yuv420p film.mp4"
         % (film["fps"], listing))


def _contact_sheet(shot_dir, n, out_path):
    """First / 25 / 50 / 75 / last, so auditing a shot is one image."""
    picks = sorted({0, n // 4, n // 2, (3 * n) // 4, n - 1})
    imgs = []
    for i in picks:
        p = os.path.join(shot_dir, "%04d.png" % i)
        if os.path.exists(p):
            imgs.append(_read_png(p))
    if imgs:
        wr.write_png(out_path, wr.hstack_views(imgs))


def _read_png(path):
    """Minimal PNG reader, so a contact sheet needs no new dependency."""
    import struct
    import zlib
    with open(path, "rb") as f:
        data = f.read()
    pos, w, h = 8, 0, 0
    idat = b""
    while pos < len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + ln]
        if tag == b"IHDR":
            w, h = struct.unpack(">II", payload[:8])
        elif tag == b"IDAT":
            idat += payload
        pos += 12 + ln
    raw = zlib.decompress(idat)
    out = np.zeros((h, w, 3), dtype=np.uint8)
    stride = w * 3
    prev = np.zeros(stride, dtype=np.int32)
    off = 0
    for y in range(h):
        ft = raw[off]; off += 1
        line = np.frombuffer(raw[off:off + stride], dtype=np.uint8).astype(np.int32)
        off += stride
        if ft == 1:
            for i in range(3, stride):
                line[i] = (line[i] + line[i - 3]) & 255
        elif ft == 2:
            line = (line + prev) & 255
        elif ft == 3:
            for i in range(stride):
                a = line[i - 3] if i >= 3 else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif ft == 4:
            for i in range(stride):
                a = line[i - 3] if i >= 3 else 0
                c = prev[i - 3] if i >= 3 else 0
                b = prev[i]
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        out[y] = line.reshape(w, 3)
        prev = line
    return out.astype(float) / 255.0


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="wam.cinematic",
                                 description="Render a .cine to frames.")
    ap.add_argument("input")
    ap.add_argument("-o", "--out")
    ap.add_argument("--shot", help="render only this shot")
    ap.add_argument("--force", action="store_true",
                    help="re-render shots that already have frames")
    args = ap.parse_args(argv)
    try:
        _, warns, _ = compile_cine(args.input, args.out, only=args.shot,
                                   force=args.force)
    except WamError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
