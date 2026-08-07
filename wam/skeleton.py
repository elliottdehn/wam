"""Skeleton solver: turns parsed bone directives into world-space bones.

Coordinate system: +Y up, +Z forward (the character faces +Z), +X = the
character's left. Mirroring reflects across the X=0 plane.

Angles: pitch is a right-hand rotation about +X (top tips toward +Z/forward),
yaw is about +Y (tips toward the character's left).  All lengths are fractions
of the model height.
"""
import math
import numpy as np

from .parser import WamError

BASE_DIRS = {
    "up": (0, 1, 0), "down": (0, -1, 0),
    "fwd": (0, 0, 1), "back": (0, 0, -1),
    "side": (1, 0, 0), "in": (-1, 0, 0),
    # the character's own left/right, for saying which way a thing faces
    "left": (1, 0, 0), "right": (-1, 0, 0),
}


def rot_x(deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def rot_y(deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def rot_z(deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def rot_axis(axis, deg):
    axis = np.asarray(axis, dtype=float)
    n = np.linalg.norm(axis)
    if n < 1e-9:
        return np.eye(3)
    x, y, z = axis / n
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    C = 1 - c
    return np.array([
        [c + x * x * C, x * y * C - z * s, x * z * C + y * s],
        [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
        [z * x * C - y * s, z * y * C + x * s, c + z * z * C]])


def _perp_frame(d):
    """A stable (side, up) pair perpendicular to a direction."""
    d = np.asarray(d, dtype=float)
    d = d / max(np.linalg.norm(d), 1e-12)
    ref = np.array([0.0, 0.0, 1.0]) if abs(d[2]) < 0.9 else np.array([0.0, 1.0, 0.0])
    up = ref - float(ref @ d) * d
    n = np.linalg.norm(up)
    if n < 1e-9:
        ref = np.array([1.0, 0.0, 0.0])
        up = ref - float(ref @ d) * d
        n = np.linalg.norm(up)
    up = up / n
    return np.cross(up, d), up


class Bone:
    """A bone is a *frame*, not just a segment.

    Direction alone cannot say which way a hand's palm faces, so anything
    gripped by it has to be oriented against world axes instead — and those
    stop being right the moment the arm is reposed. `roll=` twists the bone
    about its own axis to fix its `up`, giving `bone.up` / `bone.side` /
    `bone.along` to aim against.
    """

    def __init__(self, name, parent, head, direction, length, roll=0.0):
        self.name = name
        self.parent = parent          # Bone or None
        self.head = np.asarray(head, dtype=float)
        self.dir = np.asarray(direction, dtype=float)
        self.len = float(length)
        self.roll = float(roll)
        self.children = []
        side, up = _perp_frame(self.dir)
        if roll:
            R = rot_axis(self.dir, roll)
            side, up = R @ side, R @ up
        self.side = side
        self.up = up

    def axis(self, name):
        return {"along": self.dir / max(np.linalg.norm(self.dir), 1e-12),
                "up": self.up, "side": self.side}.get(name)

    @property
    def tail(self):
        return self.head + self.dir * self.len

    def point_at(self, t):
        return self.head + self.dir * self.len * t


def resolve_dir(spec, pitch=0.0, yaw=0.0, tilt=0.0):
    if spec is None:
        raise WamError("bone needs a dir= (up/down/fwd/back/side/in)")
    if spec not in BASE_DIRS:
        raise WamError("unknown dir %r" % spec)
    v = np.array(BASE_DIRS[spec], dtype=float)
    v = rot_y(yaw) @ rot_x(pitch) @ rot_z(tilt) @ v
    return v / np.linalg.norm(v)


CHANNELS = ("pitch", "yaw", "roll", "tilt")


def parse_bend(spec, who):
    """`bend=` into {channel: {allowed signs}}.

    A joint is not merely "an axis it turns about" — a knee turns about one
    axis and only one way along it, and a knee bent the other way is the
    single most recognisable broken-rig silhouette there is. So each entry is
    optionally signed: `-pitch` permits negative pitch only, bare `pitch`
    permits both. Channels left out entirely are forbidden.
    """
    allowed = {}
    for tok in str(spec).split(","):
        tok = tok.strip()
        if not tok:
            continue
        signs = {1, -1}
        if tok[0] in "+-":
            signs = {1 if tok[0] == "+" else -1}
            tok = tok[1:].strip()
        if tok not in CHANNELS:
            raise WamError(
                "bone %r: bend=%r — %r is not a channel (%s), optionally "
                "prefixed with + or - to allow one direction only"
                % (who, spec, tok, ", ".join(CHANNELS)))
        allowed.setdefault(tok, set()).update(signs)
    if not allowed:
        raise WamError("bone %r: bend= is empty; omit it to leave the joint "
                       "free, or name the channels it may turn about" % who)
    return allowed


def mirror_bend(allowed):
    """The same joint on the other side.

    Animation mirrors by flipping yaw, roll and tilt (pitch is preserved), so
    a constraint written once for the authored side has to flip with it or the
    `.r` limb is silently limited backwards.
    """
    out = {}
    for chan, signs in allowed.items():
        out[chan] = signs if chan == "pitch" else {-s for s in signs}
    return out


def reach_len(spec, origin, d, bones, suffix, who):
    """Length that carries a bone from `origin` along `d` to a landmark.

    A hem is not a length, it is a place: "down to mid-calf". Written as
    `len=0.22` that place is only correct for the one body it was tuned on,
    and nothing notices when it stops being true — a cape authored on a 1.95m
    hero drags on the floor of a 2.6m dragon, and a skirt lands on the knee
    because the number happened to equal the thigh.

    The solve is a projection, not a strict rendezvous: the bone keeps the
    direction it was given and extends until it is *level with* the landmark
    along its own axis. That is what "reaches the calf" means for cloth that
    also has to hang in a particular direction.
    """
    spec = str(spec).strip()
    if spec == "ground":
        if abs(d[1]) < 1e-6:
            raise WamError("bone %r: to=ground, but it points along the "
                           "ground and so never arrives" % who)
        return float((0.0 - origin[1]) / d[1])
    ref, _, tspec = spec.partition(":")
    try:
        t = float(tspec) if tspec else 1.0
    except ValueError:
        raise WamError("bone %r: to=%r — expected <bone>[:0..1] or 'ground'"
                       % (who, spec))
    target = None
    for cand in (ref + suffix, ref):
        if cand in bones:
            target = bones[cand]
            break
    if target is None:
        raise WamError(
            "bone %r: to=%r names no bone defined before it. A landmark has "
            "to already exist to be measured against — move it earlier, or "
            "add it to this model's skeleton stub if it lives on the body "
            "this one is worn by." % (who, ref))
    p = target.point_at(t) if target.len > 0 else target.head
    return float(np.dot(np.asarray(p, float) - np.asarray(origin, float), d))


def solve(model):
    """Return dict name -> Bone (mirror blocks expanded to .l / .r)."""
    bones = {}
    order = []
    pins = {}
    used_pins = set()
    for p in getattr(model, "pins", ()):
        pins.setdefault(p["bone"], {}).update(
            {k: v for k, v in p.items() if k in ("head", "tail")})

    def flip(v):
        return np.array([-v[0], v[1], v[2]])

    def pin_for(b, emitted_name, mirrored):
        """Absolute head/tail for this bone, or {}. Keys: head, tail.

        A pin written against the emitted name (`wingtip.r`) is taken
        literally; one written against the authored name inside a `mirror`
        block is reflected for the `.r` copy like every other offset.
        """
        spec = dict(head=b.get("pin_head"), tail=b.get("pin_tail"))
        spec = {k: v for k, v in spec.items() if v is not None}
        reflect = mirrored
        if emitted_name in pins:
            spec = dict(pins[emitted_name])
            used_pins.add(emitted_name)
            reflect = False
        elif b["name"] in pins:
            spec = dict(pins[b["name"]])
            used_pins.add(b["name"])
        out = {}
        for k, v in spec.items():
            v = np.asarray(v, dtype=float)
            out[k] = flip(v) if reflect else v
        return out

    def find_parent(ref, suffix):
        if ref is None:
            return None
        for candidate in (ref + suffix, ref):
            if candidate in bones:
                return bones[candidate]
        raise WamError("bone parent %r not defined (must appear earlier)" % ref)

    def make(b, suffix, mirrored):
        name = b["name"] + suffix
        if name in bones:
            raise WamError("duplicate bone %r" % name)
        pin = pin_for(b, name, mirrored)
        if b.get("parent") is None:  # root
            head = np.asarray(b["root_pos"], dtype=float)
            if "head" in pin:
                head = pin["head"]
            bone = Bone(name, None, head, (0, 1, 0), b.get("len", 0.0))
            if "tail" in pin:
                bone.head = pin["tail"] - bone.dir * bone.len
        else:
            parent = find_parent(b["parent"], suffix)
            at = b.get("at", 1.0)
            head = parent.point_at(at) if parent.len > 0 else parent.head.copy()
            # sugar offsets: side -> +X, up -> +Y, fwd -> +Z
            off = np.array(b.get("offset", (0, 0, 0)), dtype=float)
            off[0] += b.get("side", 0.0)
            off[1] += b.get("up", 0.0)
            off[2] += b.get("fwd", 0.0)
            d = resolve_dir(b.get("dir") or "up", b.get("pitch", 0.0), b.get("yaw", 0.0), b.get("tilt", 0.0))
            if mirrored:
                off = flip(off)
                d = flip(d)
            # A pin overrides where the parent chain would have put this bone,
            # so upstream edits (a longer spine) leave it exactly where it is.
            blen = b.get("len", 0.0)
            if "head" in pin:
                origin = pin["head"]
            elif "tail" in pin:
                origin = pin["tail"] - d * blen
            else:
                origin = head + off
            if b.get("to") is not None:
                blen = reach_len(b["to"], origin, d, bones, suffix, name)
                if blen <= 1e-6:
                    raise WamError(
                        "bone %r: to=%r lies behind it (solved length %.3f) — "
                        "the landmark is in the opposite direction from the "
                        "one this bone points"
                        % (name, b["to"], blen))
            roll = b.get("roll", 0.0)
            bone = Bone(name, parent, origin, d, blen,
                        roll=-roll if mirrored else roll)
            if b.get("bend") is not None:
                lim = parse_bend(b["bend"], name)
                bone.bend = mirror_bend(lim) if mirrored else lim
            parent.children.append(bone)
        bones[name] = bone
        order.append(bone)
        return bone

    for b in model.bones:
        if b.get("mirror"):
            make(b, ".l", mirrored=False)
            make(b, ".r", mirrored=True)
        else:
            make(b, "", mirrored=False)

    unused = [n for n in pins if n not in used_pins]
    if unused:
        raise WamError("pin refers to unknown bone(s): %s" % ", ".join(sorted(unused)))

    return bones, order


def bone_relative_dir(bone, spec, what="aim"):
    """A direction stated *relative to a bone* rather than to the world.

    "The sword is perpendicular to the wrist" is a relationship, and writing
    it as pitch/yaw/tilt turns it into three numbers that are only correct
    for one rest pose — repose the arm and the sword silently drifts off
    square. Naming the relationship instead keeps it true by construction.

    `along` / `against` run with or counter to the bone; `across=<hint>` is
    perpendicular to it, in the half-plane nearest the hint. Those are only
    0 and 90 degrees, which is not enough: a wrist points down, so *every*
    `across` hint yields a horizontal blade and there is no way to angle one
    downward. `aim=<deg>:<hint>` is the general form — that many degrees off
    the bone axis, leaning toward the hint — with `along` and `across` as its
    0 and 90.
    """
    d = np.asarray(bone.dir, dtype=float)
    d = d / max(np.linalg.norm(d), 1e-12)
    if spec.get("along"):
        return d
    if spec.get("against"):
        return -d
    if spec.get("aim"):
        deg, _, hint = str(spec["aim"]).partition(":")
        try:
            deg = float(deg)
        except ValueError:
            raise WamError("%s: aim= wants <degrees>:<direction>, got %r"
                           % (what, spec["aim"]))
        h = axis_hint(bone, hint or "fwd", what)
        h = h - float(h @ d) * d
        n = float(np.linalg.norm(h))
        if n < 1e-6:
            raise WamError(
                "%s: aim=%s leans toward a direction parallel to bone %r, "
                "which does not say which way to tilt — pick one across it"
                % (what, spec["aim"], bone.name))
        a = math.radians(deg)
        return math.cos(a) * d + math.sin(a) * (h / n)
    hint = spec.get("across")
    if hint is None:
        return None
    h = axis_hint(bone, hint, what)
    perp = h - float(h @ d) * d
    n = float(np.linalg.norm(perp))
    if n < 1e-6:
        raise WamError(
            "%s: across=%s is parallel to bone %r, so 'perpendicular to it' "
            "names a whole circle of directions — pick a hint that leans "
            "across the bone, not along it"
            % (what, hint if isinstance(hint, str) else tuple(np.round(h, 3)),
               bone.name))
    return perp / n


def best_fit_rotation(pairs):
    """Rotation best satisfying several (source dir -> target dir) wishes.

    Orientation is three degrees of freedom, and saying "this side faces
    left" spends only one of them. Stating two or three sides over-determines
    the problem, and the honest answer is the rotation that comes closest to
    all of them at once rather than obeying one and silently ignoring the
    rest. This is Wahba's problem; the SVD gives the least-squares fit.
    """
    B = np.zeros((3, 3))
    for src, dst, w in pairs:
        s = np.asarray(src, dtype=float)
        d = np.asarray(dst, dtype=float)
        s = s / max(np.linalg.norm(s), 1e-12)
        d = d / max(np.linalg.norm(d), 1e-12)
        B += w * np.outer(d, s)
    U, _, Vt = np.linalg.svd(B)
    D = np.diag([1.0, 1.0, float(np.sign(np.linalg.det(U @ Vt)))])
    return U @ D @ Vt


def axis_hint(bone, hint, what="aim"):
    """Resolve a direction hint: a world word, `bone.up|side|along`, or a vector."""
    if isinstance(hint, str):
        if hint in BASE_DIRS:
            return np.array(BASE_DIRS[hint], dtype=float)
        if hint.startswith("bone."):
            v = bone.axis(hint[5:])
            if v is None:
                raise WamError("%s: bone axes are bone.along, bone.up and "
                               "bone.side, not %r" % (what, hint))
            return np.asarray(v, dtype=float)
        raise WamError("%s: unknown direction %r — use a world word, a "
                       "bone.<axis>, or a vector" % (what, hint))
    return np.asarray(hint, dtype=float)


def resolve_bone_ref(bones, ref, suffix=""):
    """Resolve a bone reference, preferring the suffixed (mirrored) variant."""
    if suffix:
        cand = ref if ref.endswith((".l", ".r")) else ref + suffix
        if cand in bones:
            return bones[cand]
    if ref in bones:
        return bones[ref]
    raise WamError("unknown bone %r" % ref)


def chain_bones(bones, first_ref, last_ref, suffix=""):
    """Walk from last up parents to first; return the chain in order."""
    first = resolve_bone_ref(bones, first_ref, suffix)
    last = resolve_bone_ref(bones, last_ref, suffix)
    chain = [last]
    cur = last
    while cur is not first:
        cur = cur.parent
        if cur is None:
            raise WamError("bones %s..%s are not an ancestor chain" %
                           (first_ref, last_ref))
        chain.append(cur)
    return list(reversed(chain))
