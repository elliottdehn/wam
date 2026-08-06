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
import math
import os
import sys

import numpy as np

from . import checks as wchecks
from . import mesh as wmesh
from . import parser as wparser
from . import skeleton as wskel


SIL_VIEWS = {"front": 0.0, "threequarter": 38.0, "side": 90.0, "back": 180.0}
SIL_H, SIL_W = 128, 256


def silhouette_mask(V, T, yaw_deg, res_h=SIL_H, res_w=SIL_W):
    """Binary silhouette of a model, normalized for size but not for shape.

    Each model is scaled so its projected height is 1 and stood on the same
    ground line, then rasterized orthographically. Absolute size is removed
    because `height()` already checks that; aspect ratio is deliberately
    *kept*, because a tall narrow unit and a squat wide one are exactly the
    pair you want to read apart at distance.
    """
    a = np.radians(yaw_deg)
    ca, sa = np.cos(a), np.sin(a)
    x = V[:, 0] * ca - V[:, 2] * sa            # orthographic: no perspective,
    y = V[:, 1]                                # so distance cannot skew it
    h = float(y.max() - y.min())
    if h < 1e-9:
        return np.zeros((res_h, res_w), dtype=bool)
    sx = (x - 0.5 * (x.max() + x.min())) / h
    sy = (y - y.min()) / h
    px = (sx + 1.0) * 0.5 * res_w              # x spans [-1, 1]
    py = (1.0 - sy) * res_h                    # y spans [0, 1], ground at foot
    mask = np.zeros((res_h, res_w), dtype=bool)
    for (i, j, k) in T:
        xs = np.array([px[i], px[j], px[k]])
        ys = np.array([py[i], py[j], py[k]])
        x0, x1 = int(max(0, np.floor(xs.min()))), int(min(res_w - 1, np.ceil(xs.max())))
        y0, y1 = int(max(0, np.floor(ys.min()))), int(min(res_h - 1, np.ceil(ys.max())))
        if x1 < x0 or y1 < y0:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5,
                             np.arange(y0, y1 + 1) + 0.5)
        d = ((ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2]))
        if abs(d) < 1e-9:
            continue
        w0 = ((ys[1] - ys[2]) * (gx - xs[2]) + (xs[2] - xs[1]) * (gy - ys[2])) / d
        w1 = ((ys[2] - ys[0]) * (gx - xs[2]) + (xs[0] - xs[2]) * (gy - ys[2])) / d
        w2 = 1 - w0 - w1
        hit = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        if hit.any():
            mask[y0:y1 + 1, x0:x1 + 1] |= hit
    return mask


def _bone_frame(b):
    """(origin, orthonormal basis, length) for one bone."""
    d = np.asarray(b.dir, dtype=float)
    d = d / max(np.linalg.norm(d), 1e-12)
    side, other = wmesh._frame(d)
    return np.asarray(b.head, dtype=float), np.stack([side, other, d], axis=1), \
        max(float(b.len), 1e-9)


def retarget(V, skin, worn_bones, host_bones, part_name):
    """Rebind geometry authored against one skeleton onto another.

    A worn model declares its own skeleton only so it can be authored and
    inspected on its own. What makes it *equipment* is that composition
    throws that skeleton away and re-solves the geometry against the wearer's
    — by bone name, with each bone's own rotation, position and length ratio.
    One plate set therefore fits every body that uses the same bone names,
    and stretches to a longer forearm or a broader shoulder instead of
    floating off it.
    """
    missing = sorted({n for sk in skin for n, _ in sk} - set(host_bones))
    if missing:
        raise wchecks.CheckError(
            "%s is skinned to bone(s) the wearer does not have: %s — worn "
            "models bind by bone name, so both skeletons must agree on them"
            % (part_name, ", ".join(missing)))
    frames = {}
    for name in {n for sk in skin for n, _ in sk}:
        wh, wr, wl = _bone_frame(worn_bones[name])
        hh, hr, hl = _bone_frame(host_bones[name])
        frames[name] = (wh, wr, wl, hh, hr, hl)
    out = np.zeros_like(V)
    for i, sk in enumerate(skin):
        total = sum(w for _, w in sk) or 1.0
        p = np.zeros(3)
        for name, w in sk:
            wh, wr, wl, hh, hr, hl = frames[name]
            local = (wr.T @ (V[i] - wh)) * (hl / wl)
            p += (w / total) * (hh + hr @ local)
        out[i] = p
    return out


class ModelSet:
    def __init__(self):
        self.name = "set"
        self.entries = []          # (alias, path)
        self.checks = []
        self.every = []            # checks run inside each member's namespace
        self.compositions = []     # dicts: name, base, grafts[]
        self.source_path = None


def parse(text, path=None):
    ms = ModelSet()
    ms.source_path = path
    base = os.path.dirname(path or ".")
    section = None
    cur = None
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
        if kw == "compose":
            if len(tokens) < 2:
                raise wparser.WamError("compose needs a name", line_no, line)
            cur = dict(name=tokens[1], base=None, grafts=[], line_no=line_no)
            ms.compositions.append(cur)
            section = "compose"
            continue
        if kw in ("models", "checks", "every"):
            section = kw
            cur = None
            continue
        if section == "models":
            if len(tokens) != 2:
                raise wparser.WamError("models entry: <alias> <path.wam>",
                                       line_no, line)
            ms.entries.append((tokens[0], os.path.join(base, tokens[1])))
        elif section == "compose":
            _, kv, flags = wparser._split_kv(tokens[1:], line_no, line)
            if kw == "base":
                cur["base"] = tokens[1]
            elif kw == "graft":
                g = dict(alias=tokens[1], line_no=line_no, dir=kv.get("dir"),
                         fuse=kv.get("fuse", "name"), prefix=kv.get("prefix"),
                         align=kv.get("align"), face=kv.get("face"))
                if g["fuse"] not in ("name", "none"):
                    raise wparser.WamError("fuse= must be name|none",
                                           line_no, line)
                for f in flags:
                    if f in wparser.DIR_WORDS:
                        g["dir"] = f
                    elif f == "overlap":
                        g["overlap"] = True
                if "across" in kv:
                    g["across"] = (wparser._vec(kv["across"], line_no, line)
                                   if kv["across"].startswith("(")
                                   else kv["across"])
                for f in flags:
                    if f in ("along", "against"):
                        g[f] = True
                for k in ("at", "pitch", "yaw", "tilt", "spin", "scale"):
                    if k in kv:
                        g[k] = wparser._num(kv[k], line_no, line)
                if "offset" in kv:
                    g["offset"] = wparser._vec(kv["offset"], line_no, line)
                if "to" in kv:
                    if kv["to"].startswith("("):
                        g["world"] = wparser._vec(kv["to"], line_no, line)
                    else:
                        g["bone"], _, t = kv["to"].partition(":")
                        if t:
                            g["at"] = wparser._num(t, line_no, line)
                cur["grafts"].append(g)
            else:
                raise wparser.WamError("compose takes base/graft, got %r"
                                       % kw, line_no, line)
        elif section in ("checks", "every"):
            target = ms.checks if section == "checks" else ms.every
            target.append(wparser.parse_check(kw, line, tokens, line_no))
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
        self._masks = {}
        self._funcs = None

    @property
    def funcs(self):
        """This model's own check vocabulary, for delegated part queries."""
        if self._funcs is None:
            self._funcs = wchecks.build_functions(self.env)
        return self._funcs

    def mask(self, yaw):
        if yaw not in self._masks:
            self._masks[yaw] = silhouette_mask(self.V, self.T, yaw)
        return self._masks[yaw]

    def extent(self, axis, verts=None):
        v = self.V if verts is None else verts
        return float(v[:, axis].max() - v[:, axis].min()) * self.scale


class _Composite:
    """A stand-in model object for a composition: the base's rig, merged parts."""

    def __init__(self, base_model, parts):
        self.name = base_model.name
        self.height = base_model.height
        self.style = base_model.style
        self.palette = dict(base_model.palette)
        self.textures = dict(getattr(base_model, "textures", {}))
        self.anims = base_model.anims          # the wearer drives the motion
        self.poses = base_model.poses
        self.parts = parts
        self.markers = {}
        self.anchors = dict(getattr(base_model, "anchors", {}) or {})
        self.checks = []


def _prefixed_parts(alias, model):
    out = []
    for p in model.parts:
        q = dict(p)
        q["name"] = "%s.%s" % (alias, p["name"])
        if q.get("on"):
            q["on"] = "%s.%s" % (alias, p["on"])
        out.append(q)
    return out


def _affine_fuse(worn, host):
    """Affine taking geometry bound to `worn` onto the same-named `host`."""
    wh, wr, wl = _bone_frame(worn)
    hh, hr, hl = _bone_frame(host)
    A = (hl / wl) * (hr @ wr.T)
    return A, hh - A @ wh


def _join_frame(g, base, c):
    """Affine placing a grafted model's own space into the base's.

    With `align=<anchor>` the placement is *solved*: the model's named anchor
    frame is mapped onto the host bone's frame, so a grip meets a fist by
    construction. Without one, the caller is back to aiming by hand, which is
    where props end up floating beside the hand that should hold them.
    """
    scale = g.get("scale", c.scale / base.scale)
    if g.get("align"):
        name = g["align"]
        anchor = c.model.anchors.get(name)
        if anchor is None:
            raise wchecks.CheckError(
                "graft %r: no anchor %r in that model (it has: %s)"
                % (g["alias"], name,
                   ", ".join(sorted(c.model.anchors)) or "none"))
        host = base.bones.get(g.get("bone"))
        if host is None:
            raise wchecks.CheckError("graft %r: align= needs to=<bone>"
                                     % g["alias"])
        src = wskel.resolve_dir(anchor["dir"], anchor.get("pitch", 0.0),
                                anchor.get("yaw", 0.0), anchor.get("tilt", 0.0))
        # the anchor axis may be stated relative to the host bone rather than
        # aligned with it: "perpendicular to the wrist" is a relationship
        rel = wskel.bone_relative_dir(host, g, "graft %r" % g["alias"])
        if rel is not None:
            dst = rel
        else:
            dst = np.asarray(host.dir, dtype=float)
            dst = dst / max(np.linalg.norm(dst), 1e-12)
        R = wmesh._align_rotation(src, dst)
        if g.get("spin"):
            R = wskel.rot_axis(dst, g["spin"]) @ R
        for k, fn in (("pitch", wskel.rot_x), ("yaw", wskel.rot_y),
                      ("tilt", wskel.rot_z)):
            if g.get(k):
                R = fn(g[k]) @ R
        if g.get("face"):
            # The last free number in a placement is the roll about the aim
            # axis, and guessing it is how a blade ends up edge-on or upside
            # down. Naming a marker and the way it should point solves it:
            # spin until that marker lies as near the target as it can.
            mk, _, hint = g["face"].partition(":")
            hint = hint or "up"
            markers = getattr(c.model, "markers", {}) or {}
            if mk not in markers:
                raise wchecks.CheckError(
                    "graft %r: no marker %r (it has: %s)"
                    % (g["alias"], mk, ", ".join(sorted(markers)) or "none"))
            h = (np.array(wskel.BASE_DIRS[hint], dtype=float)
                 if hint in wskel.BASE_DIRS else np.asarray(hint, dtype=float))
            t = h - float(h @ dst) * dst
            v = R @ (np.asarray(markers[mk], dtype=float)
                     - np.asarray(anchor["pos"], dtype=float))
            v = v - float(v @ dst) * dst
            if np.linalg.norm(t) < 1e-6:
                raise wchecks.CheckError(
                    "graft %r: face=%s:%s points along the aim axis, so it "
                    "says nothing about the roll around it — pick a direction "
                    "across the aim, not along it" % (g["alias"], mk, hint))
            if np.linalg.norm(v) < 1e-6:
                raise wchecks.CheckError(
                    "graft %r: marker %r sits on the aim axis, so spinning "
                    "does not move it — pick a marker off to one side"
                    % (g["alias"], mk))
            v /= np.linalg.norm(v)
            t /= np.linalg.norm(t)
            ang = math.degrees(math.atan2(float(np.cross(v, t) @ dst),
                                          float(v @ t)))
            R = wskel.rot_axis(dst, ang) @ R
        A = scale * R
        T = host.point_at(g.get("at", 1.0)) + np.asarray(
            g.get("offset", (0.0, 0.0, 0.0)), dtype=float)
        return A, T - A @ np.asarray(anchor["pos"], dtype=float), host
    if g.get("dir"):
        v = wskel.resolve_dir(g["dir"], g.get("pitch", 0.0), g.get("yaw", 0.0),
                              g.get("tilt", 0.0))
        R = wmesh._align_rotation(np.array([0.0, 1.0, 0.0]), v)
        R = wskel.rot_axis(v, g.get("spin", 0.0)) @ R
    else:
        R = (wskel.rot_y(g.get("yaw", 0.0)) @ wskel.rot_x(g.get("pitch", 0.0))
             @ wskel.rot_z(g.get("tilt", 0.0)))
    if g.get("world") is not None:
        T = np.asarray(g["world"], dtype=float)
        host = None
    else:
        name = g.get("bone")
        host = base.bones.get(name) if name else None
        if name and host is None:
            raise wchecks.CheckError("graft %r: the base has no bone %r"
                                     % (g["alias"], name))
        T = (host.point_at(g.get("at", 1.0)) if host is not None
             else np.zeros(3))
    T = T + np.asarray(g.get("offset", (0.0, 0.0, 0.0)), dtype=float)
    A = scale * R
    root = np.zeros(3)
    for b in c.bones.values():
        if b.parent is None:
            root = np.asarray(b.head, dtype=float)
            break
    return A, T - A @ root, host


def build_composition(spec, models, warn):
    """Merge a base model with everything grafted into it.

    Wearing and holding are not primitives, they are outcomes. The one
    operation is grafting a skeleton into another: bones that match by name
    **fuse**, and their geometry is re-solved onto the host's version of that
    bone; bones with no match **join** the hierarchy as new children at the
    declared point. Armour is the all-fused case, a weapon the all-joined
    case, and everything interesting is a mixture — a cape whose spine bones
    fuse while its own sway bones come along, a rider that keeps its entire
    rig and animations while its root rides a saddle.
    """
    base = models.get(spec["base"])
    if base is None:
        raise wchecks.CheckError("compose %r: no base model %r"
                                 % (spec["name"], spec["base"]))
    out = wmesh.MeshOut()
    parts, seen_colors, held, worn = [], {}, [], []
    markers = {}
    bones = dict(base.bones)
    anims = [dict(a) for a in base.model.anims]
    poses = dict(base.model.poses)

    def merge(alias, c, verts, skins):
        remap = {}
        for mi, (mname, rgb) in enumerate(c.mesh.materials):
            prev = seen_colors.get(mname)
            if prev is not None and prev != tuple(rgb):
                warn("material %r means %s in one model and %s in another — "
                     "composed models share materials by name, so one of them "
                     "will silently repaint the other" % (mname, prev, tuple(rgb)))
            seen_colors.setdefault(mname, tuple(rgb))
            remap[mi] = out.material(mname, rgb)
        v0 = len(out.verts)
        for i, pt in enumerate(verts):
            out.add_vert(pt, skins[i], uv=c.mesh.uvs[i])
        for ti, (a, b, cc) in enumerate(c.mesh.tris):
            out.add_tri(a + v0, b + v0, cc + v0, remap[c.mesh.tri_mat[ti]])
        for name, (r0, r1) in c.mesh.part_ranges.items():
            out.part_ranges["%s.%s" % (alias, name)] = (r0 + v0, r1 + v0)
        out.double_sided_materials |= c.mesh.double_sided_materials
        parts.extend(_prefixed_parts(alias, c.model))

    merge(spec["base"], base, base.V, base.mesh.skin)
    for nm, pos in (getattr(base.model, "markers", {}) or {}).items():
        markers[nm] = np.asarray(pos, dtype=float)

    for g in spec["grafts"]:
        alias = g["alias"]
        c = models.get(alias)
        if c is None:
            raise wchecks.CheckError("compose %r: no model %r to graft"
                                     % (spec["name"], alias))
        A0, t0, host = _join_frame(g, base, c)
        fuse = g.get("fuse", "name") == "name"

        # A model that declares an anchor is telling you where it meets
        # something. Placing it by hand anyway is how a weapon ends up
        # floating beside the fist that should hold it — so say where the
        # anchor actually landed relative to the joint it was aimed at.
        if host is not None and c.model.anchors and not g.get("align"):
            target = host.point_at(g.get("at", 1.0))
            landed = min(
                (float(np.linalg.norm(A0 @ np.asarray(a["pos"], dtype=float)
                                      + t0 - target)), nm)
                for nm, a in c.model.anchors.items())
            if landed[0] > 0.03:
                warn("graft %r was aimed by hand, but that model declares an "
                     "anchor %r which landed %.3f from %s — use align=%s to "
                     "have the placement solved instead of guessed"
                     % (alias, landed[1], landed[0], g.get("bone"), landed[1]))

        # decide, per bone, whether it fuses with the host or joins as new
        namemap, xform, fused_any = {}, {}, False
        for name, b in c.bones.items():
            if fuse and name in base.bones:
                namemap[name] = name
                xform[name] = _affine_fuse(b, base.bones[name])
                fused_any = True
            else:
                new = "%s.%s" % (g.get("prefix") or alias, name) \
                    if name in bones else name
                namemap[name] = new
                xform[name] = (A0, t0)
        for name, b in c.bones.items():
            if namemap[name] in bones:
                continue
            A, t = xform[name]
            parent = (bones.get(namemap[b.parent.name]) if b.parent is not None
                      else host)
            d = A @ np.asarray(b.dir, dtype=float)
            n = float(np.linalg.norm(d))
            nb = wskel.Bone(namemap[name], parent, A @ np.asarray(b.head) + t,
                            d / n if n > 1e-12 else b.dir,
                            float(b.len) * float(np.linalg.norm(A[:, 2])))
            if parent is not None:
                parent.children.append(nb)
            bones[namemap[name]] = nb

        if not fused_any and not g.get("overlap"):
            held.append((alias, g.get("bone")))
        if fused_any and not g.get("overlap"):
            worn.append(alias)
        V = np.zeros_like(c.V)
        for i, sk in enumerate(c.mesh.skin):
            total = sum(w for _, w in sk) or 1.0
            acc = np.zeros(3)
            for name, w in sk:
                A, t = xform[name]
                acc += (w / total) * (A @ c.V[i] + t)
            V[i] = acc
        skins = [[(namemap[n], w) for n, w in sk] for sk in c.mesh.skin]
        merge(alias, c, V, skins)
        # markers ride along, so "the tip of the blade" stays addressable
        # after the sword has been placed on someone
        for nm, pos in (getattr(c.model, "markers", {}) or {}).items():
            A, t = xform.get(list(c.bones)[0], (A0, t0))
            markers["%s.%s" % (alias, nm)] = A0 @ np.asarray(pos, dtype=float) + t0

        # the grafted model's own motion comes with it, remapped onto the
        # names its bones ended up with
        taken = {a["name"] for a in anims}
        for a in c.model.anims:
            a2 = dict(a)
            a2["name"] = a["name"] if a["name"] not in taken \
                else "%s.%s" % (alias, a["name"])
            a2["channels"] = [dict(ch, bone=namemap.get(ch["bone"], ch["bone"]))
                              for ch in a["channels"]]
            anims.append(a2)
        for pname, pose in c.model.poses.items():
            key = pname if pname not in poses else "%s.%s" % (alias, pname)
            poses[key] = dict(pose, bones={namemap.get(b, b): r
                                           for b, r in pose["bones"].items()})
    model = _Composite(base.model, parts)
    model.markers = markers
    model.anims = anims
    model.poses = poses
    comp = Compiled(spec["name"], model, bones, out)
    comp.base_alias = spec["base"]
    comp.held = held
    comp.worn = worn
    return comp


def check_worn_cover(comp, warn, limit=0.005):
    """Worn geometry must not have the body bursting out through it.

    The classic is a thigh through a skirt. It is invisible to `covers`,
    which cannot distinguish the hem the legs are *supposed* to come out of
    from a hole they are bursting through, and invisible to `clip`, because a
    correct skirt and a leaking one both cross the body's surface somewhere.
    Only the span the garment occupies makes the question answerable.
    """
    if not getattr(comp, "worn", None):
        return
    funcs = comp.funcs
    base_parts = [k for k in comp.mesh.part_ranges
                  if k.split(".", 1)[0] == comp.base_alias]

    def bones_of(key):
        v0, v1 = comp.mesh.part_ranges[key]
        names = set()
        for i in range(v0, v1):
            names.update(n for n, w in comp.mesh.skin[i] if w > 0.05)
        return names

    def box(key):
        v0, v1 = comp.mesh.part_ranges[key]
        chunk = comp.V[v0:v1]
        return chunk.min(axis=0), chunk.max(axis=0)

    # Pair by spatial overlap, not by shared bones: a skirt hangs *over* the
    # thighs without being skinned to them, so bone kinship would never
    # compare the two parts that actually matter.
    base_box = {k: box(k) for k in base_parts}
    for alias in comp.worn:
        worst = (0.0, None, None)
        for gk in comp.mesh.part_ranges:
            if gk.split(".", 1)[0] != alias:
                continue
            glo, ghi = box(gk)
            for bk in base_parts:
                blo, bhi = base_box[bk]
                if np.any(ghi < blo) or np.any(bhi < glo):
                    continue        # no shared space at all
                d = funcs["leak"](wchecks._Ref(gk), wchecks._Ref(bk))
                if d > worst[0]:
                    worst = (d, bk, gk)
        if worst[0] > limit:
            warn("in %r the body breaks out through worn %r: %r pokes %.3f "
                 "outside %r within the span it covers — widen the garment "
                 "there, or narrow the body under it"
                 % (comp.alias, alias, worst[1], worst[0], worst[2]))


def check_held_props(comp, warn, limit=0.02):
    """A held prop must not pass through the body holding it.

    A weapon reversed into its owner's chest is the most common composition
    failure and the hardest to see in a render, because the blade reads as
    being *behind* the body rather than inside it. It is also unambiguous:
    something that joined the skeleton without fusing any bones is carried,
    not worn, and carried things do not intersect their carrier — except at
    the limb gripping them, which is excluded.
    """
    if not comp.held:
        return
    funcs = comp.funcs
    base_parts = [k for k in comp.mesh.part_ranges
                  if k.split(".", 1)[0] == comp.base_alias]
    for alias, host in comp.held:
        holding = set()
        if host is not None:
            for k in base_parts:
                v0, v1 = comp.mesh.part_ranges[k]
                names = set()
                for i in range(v0, v1):
                    names.update(n for n, w in comp.mesh.skin[i] if w > 0.05)
                if host in names:
                    holding.add(k)          # the limb gripping it: expected
        worst = (0.0, None, None)
        for pk in comp.mesh.part_ranges:
            if pk.split(".", 1)[0] != alias:
                continue
            for bk in base_parts:
                if bk in holding:
                    continue
                d = funcs["clip"](wchecks._Ref(pk), wchecks._Ref(bk))
                if d > worst[0]:
                    worst = (d, pk, bk)
        if worst[0] > limit:
            warn("in %r the held %r passes %.3f through the body: %r into %r "
                 "— a carried prop should never enter its carrier, so its aim "
                 "is reversed or swung across the body. If it is meant to "
                 "overlap (a rider straddling a mount), say so with the "
                 "`overlap` flag"
                 % (comp.alias, alias, worst[0], worst[1], worst[2]))


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

    def point(ref):
        """A point on a model, in meters: a part's center or a bone's head."""
        c, sub = resolve(ref)
        if sub is None:
            raise wchecks.CheckError(
                "%r is a whole model — name a part or bone inside it, like "
                "knight.hammer.shaft or knight.hand.r" % wchecks._name_of(ref))
        return c, c.env.point(wchecks._Ref(sub)) * c.scale

    def dist(a, b):
        ca, pa = point(a)
        cb, pb = point(b)
        if ca is not cb:
            raise wchecks.CheckError(
                "dist() compares points in one model — %r and %r are in "
                "different models, which share no space; compose them first"
                % (wchecks._name_of(a), wchecks._name_of(b)))
        return float(np.linalg.norm(pa - pb))

    def closer(a, b, c):
        """How much nearer `a` is to `b` than to `c`, in meters.

        Positive means a is nearer b. Placement bugs are usually relational —
        "the pommel should be nearer the hand than the tip is", "the edge
        should face away from the chest" — and those are unsayable in terms
        of angles but trivial in terms of which of two things is closer.
        """
        ca, pa = point(a)
        cb, pb = point(b)
        cc, pc = point(c)
        if not (ca is cb is cc):
            raise wchecks.CheckError(
                "closer() compares points in one model; %r, %r and %r are "
                "not all in the same one"
                % (wchecks._name_of(a), wchecks._name_of(b), wchecks._name_of(c)))
        return float(np.linalg.norm(pa - pc) - np.linalg.norm(pa - pb))

    def within(fname, a, b, extra=None):
        """Delegate a part-vs-part query to the model that owns both parts."""
        ca, pa = resolve(a)
        cb, pb = resolve(b)
        if pa is None or pb is None:
            raise wchecks.CheckError(
                "%s() compares two parts, like %s(knight.plate.cuirass, "
                "knight.body.torso)" % (fname, fname))
        if ca is not cb:
            raise wchecks.CheckError(
                "%s() compares parts of one model — %r and %r live in "
                "different models, so they share no space; put them in a "
                "`compose` block first" % (fname, wchecks._name_of(a),
                                           wchecks._name_of(b)))
        args = [wchecks._Ref(pa), wchecks._Ref(pb)]
        if extra is not None:
            args.append(extra)
        return ca.funcs[fname](*args)

    def silhouette(a, b, view=None):
        """How distinguishable two models are by outline alone, 0..1.

        1 - IoU of their normalized silhouettes: 0 means the same outline,
        and larger means easier to tell apart at a glance. This is the check
        no single model can make about itself — two units can each be fine
        and still be indistinguishable at fifty metres.
        """
        name = wchecks._name_of(view) if view is not None else "threequarter"
        if name not in SIL_VIEWS:
            raise wchecks.CheckError(
                "silhouette view must be one of %s" % "|".join(sorted(SIL_VIEWS)))
        yaw = SIL_VIEWS[name]
        ca, _ = resolve(a)
        cb, _ = resolve(b)
        ma, mb = ca.mask(yaw), cb.mask(yaw)
        union = int((ma | mb).sum())
        if not union:
            return 0.0
        return 1.0 - int((ma & mb).sum()) / union

    return {
        "silhouette": silhouette,
        "clip": lambda a, b, anim=None: within("clip", a, b, anim),
        "gap": lambda a, b, anim=None: within("gap", a, b, anim),
        "covers": lambda a, b: within("covers", a, b),
        "leak": lambda a, b: within("leak", a, b),
        "dist": dist,
        "closer": closer,
        "x": lambda r: float(point(r)[1][0]),
        "y": lambda r: float(point(r)[1][1]),
        "z": lambda r: float(point(r)[1][2]),
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


def render_composition(c, out_prefix, views=("front", "threequarter", "side"),
                       width=420, height=560):
    """Sheet + glTF for a composition, exactly as for a single model."""
    from . import render as wrender
    from . import gltf as wgltf
    from . import animation as wanim
    from . import cli as wcli
    V, T, M = c.mesh.arrays()
    colors = [rgb for _, rgb in c.mesh.materials]
    yaws = [wcli.VIEW_ANGLES.get(v, 0) for v in views]
    center, dist = wcli.shared_framing(V, yaws, width, height)
    imgs = [wrender.render_view(V, T, M, colors, yaw_deg=y, width=width,
                                height=height, center=center, dist=dist)
            for y in yaws]
    os.makedirs(os.path.dirname(out_prefix) or ".", exist_ok=True)
    wrender.write_png(out_prefix + "_sheet.png", wrender.hstack_views(imgs))
    order = list(c.bones.values())
    tracks = wanim.gltf_tracks(c.model, c.bones, order)
    wgltf.export(out_prefix + ".gltf", c.model, c.bones, order, c.mesh,
                 tracks, scale=c.model.height)
    return out_prefix + "_sheet.png"


def compile_set(path, quiet=False, out_dir=None):
    ms = parse_file(path)
    models = {}
    for alias, mpath in ms.entries:
        if alias in models:
            raise wparser.WamError("duplicate model alias %r" % alias)
        model = wparser.parse_file(mpath)
        bones, _ = wskel.solve(model)
        mesh = wmesh.build(model, bones)
        models[alias] = Compiled(alias, model, bones, mesh)

    notes = []
    for spec in ms.compositions:
        if spec["name"] in models:
            raise wparser.WamError("compose %r collides with a model alias"
                                   % spec["name"])
        try:
            comp = build_composition(spec, models, notes.append)
        except wchecks.CheckError as e:
            raise wparser.WamError("compose %r: %s" % (spec["name"], e))
        check_held_props(comp, notes.append)
        check_worn_cover(comp, notes.append)
        models[spec["name"]] = comp

    funcs = build_functions(models)
    scalars = {"pi": float(np.pi), "models": float(len(models))}

    def set_noclip(spec):
        """`noclip <member> [parts...]` — a sweep inside one model or
        composition. Set level has no shared space of its own, so the search
        has to name whose space to search."""
        subj = spec.get("subject") or []
        if not subj or subj[0] not in models:
            raise wchecks.CheckError(
                "at set level noclip names the model to search first, like "
                "`noclip knight` or `noclip knight sword` (have: %s)"
                % ", ".join(sorted(models)))
        c = models[subj[0]]
        return wchecks.run_noclip(c.env, dict(spec, subject=(subj[1:] or None)),
                                  c.funcs)

    failures, measurements = wchecks.run_checks(ms.checks, scalars, funcs,
                                                noclip=set_noclip)

    # `every` runs ordinary model-level checks inside each member's own
    # namespace, so shared conventions live in one file instead of being
    # copy-pasted into the top of every model's `checks` section.
    for alias in sorted(models):
        c = models[alias]
        _, mscalars, mfuncs, noclip = wchecks.namespace(
            c.model, c.bones, c.mesh, c.V)
        f, m = wchecks.run_checks(ms.every, mscalars, mfuncs, noclip=noclip)
        failures.extend("[%s] %s" % (alias, x) for x in f)
        measurements.extend("[%s] %s" % (alias, x) for x in m)

    if not quiet:
        for n in notes:
            print("WARN: %s" % n)
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
    if out_dir:
        for spec in ms.compositions:
            p = render_composition(models[spec["name"]],
                                   os.path.join(out_dir, spec["name"]))
            if not quiet:
                print("info: wrote %s" % p)
    return ms, models, failures


def main(argv=None):
    ap = argparse.ArgumentParser(prog="wam.modelset")
    ap.add_argument("input", help="a .wamset file")
    ap.add_argument("-o", "--out", default=None,
                    help="directory to render/export compositions into")
    args = ap.parse_args(argv)
    try:
        _, _, failures = compile_set(args.input, out_dir=args.out)
    except wparser.WamError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
