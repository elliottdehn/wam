
"""Parser for the WAM (WoW-ish Art Model) language.

Line-oriented format. Sections: model, palette, skeleton, parts, animations.
See SPEC.md for the full grammar.
"""
import math
import re


class WamError(Exception):
    def __init__(self, msg, line_no=None, line=None):
        self.line_no = line_no
        self.line = line
        loc = " (line %d: %r)" % (line_no, line) if line_no else ""
        super().__init__(msg + loc)


DIR_WORDS = ("up", "down", "fwd", "back", "side", "in", "left", "right")
CAP_WORDS = ("flat", "dome", "point", "none")
SHAPE_WORDS = ("round", "squarish", "box")
FRAME_WORDS = ("auto", "up", "fwd", "side")


def _num(tok, line_no, line):
    tok = tok.strip()
    pct = tok.endswith("%")
    if pct:
        tok = tok[:-1]
    try:
        v = float(tok)
    except ValueError:
        raise WamError("expected a number, got %r" % tok, line_no, line)
    return v / 100.0 if pct else v


def _vec(tok, line_no, line):
    m = re.match(r"^\(([^)]*)\)$", tok)
    if not m:
        raise WamError("expected a tuple like (x,y,z), got %r" % tok, line_no, line)
    parts = [p for p in m.group(1).split(",") if p.strip() != ""]
    vals = [_num(p, line_no, line) for p in parts]
    while len(vals) < 3:
        vals.append(0.0)
    return tuple(vals[:3])


def _split_kv(tokens, line_no, line):
    """Split tokens into (positional, kv-dict, flags)."""
    pos, kv, flags = [], {}, []
    for t in tokens:
        if "=" in t and not t.startswith("("):
            k, v = t.split("=", 1)
            kv[k] = v
        elif re.match(r"^[a-zA-Z_][\w.*]*$", t) and t not in ("",):
            flags.append(t)
        else:
            pos.append(t)
    return pos, kv, flags


class Model:
    def __init__(self):
        self.name = "model"
        self.height = 2.0
        self.style = "chunky"
        self.shading = "smooth"   # or "faceted": per-part flags override
        self.girth = 1.0           # multiplies every cross-section
        self.reach = 1.0           # multiplies every length along a path
        self.palette = {}          # name -> (r,g,b) floats 0..1
        self.material_pbr = {}     # name -> dict(metal, rough)
        self.textures = {}         # name -> {base:(r,g,b), ops:[...]}
        self.bones = []            # list of dicts
        self.pins = []             # list of dicts: bone -> absolute head/tail
        self.anchors = {}          # name -> dict(pos, dir, pitch, yaw, tilt)
        self.markers = {}          # name -> (x, y, z) in the model's own space
        self.hold = None           # dict(anchor, point, edge, carry) or None
        self.sides = {}            # name -> local direction that side faces
        self.parts = []            # list of dicts
        self.poses = {}            # name -> {bone: {pitch,yaw,roll}}
        self.anims = []            # list of dicts
        self.checks = []           # list of dicts: assert / measure
        self.warnings = []         # ambient parse-time complaints
        self.source_path = None


# Every key each directive understands. A key outside its set is a typo, and
# a typo that is silently dropped is the worst failure mode the language has:
# the model compiles, looks plausible, and quietly ignores what you asked for.
KNOWN_KEYS = {
    "bone": {"parent", "dir", "pitch", "yaw", "roll", "tilt", "len", "at", "to",
             "bend",
             "side", "fwd", "up", "offset", "head", "tail"},
    "pin": {"at", "head", "tail"},
    "part": {"material", "bones", "bone", "dir", "shape", "kind", "on", "press",
             "frame", "refaxis", "skin", "material_arc", "anchor", "trailing",
             "at", "len", "pitch", "yaw", "tilt", "size", "w", "d", "h",
             "sides", "taper", "inset", "scallop", "thickness", "steps",
             "usteps", "offset", "rest", "layer", "push"},
    "ring": {"t", "w", "d", "fwd", "side", "up", "roll", "wtop", "wbot",
             "dtop", "dbot", "shape", "material", "material_arc", "follow",
             "skin"},
    "seg": {"len", "r", "up", "down", "fwd", "back", "curl", "roll",
            "material", "skin"},
    "rib": {"bones", "bone", "from", "inset", "start", "skin"},
    "group": {"bone", "dir", "at", "pitch", "yaw", "tilt", "spin", "across",
              "aim", "offset"},
    "anchor": {"at", "dir", "pitch", "yaw", "tilt"},
    "marker": {"at"},
    "side": {"dir", "pitch", "yaw", "tilt"},
    "hold": {"point", "edge", "carry"},
}


def _check_keys(model, kind, kv, line_no, line):
    unknown = sorted(set(kv) - KNOWN_KEYS[kind])
    for k in unknown:
        near = sorted(KNOWN_KEYS[kind], key=lambda c: _similar(k, c))[:1]
        model.warnings.append(
            "line %d: %s does not understand %r%s — it was ignored, so "
            "whatever you meant by it did not happen"
            % (line_no, kind, k,
               (", did you mean %r?" % near[0]) if near and
               _similar(k, near[0]) < max(3, len(k) // 2) else ""))


def _similar(a, b):
    """Cheap edit distance, for suggesting the key someone meant."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _hex_color(tok, line_no, line):
    m = re.match(r"^#([0-9a-fA-F]{6})$", tok)
    if not m:
        raise WamError("expected color like #rrggbb, got %r" % tok, line_no, line)
    h = m.group(1)
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _skin_spec(tok, line_no, line):
    """`skin=bone[:w],bone[:w]...` -> normalized [(bone, weight), ...].

    Weights are relative; the compiler normalizes them so `a:2,b:1` and
    `a:0.667,b:0.333` mean the same thing.
    """
    entries = []
    for item in tok.split(","):
        item = item.strip()
        if not item:
            continue
        name, _, w = item.partition(":")
        if not name:
            raise WamError("skin= entry needs a bone name, got %r" % item,
                           line_no, line)
        entries.append((name, _num(w, line_no, line) if w else 1.0))
    if not entries:
        raise WamError("skin= needs at least one bone", line_no, line)
    total = sum(w for _, w in entries)
    if total <= 0:
        raise WamError("skin= weights must sum above zero", line_no, line)
    return [(n, w / total) for n, w in entries]


def _arc_spec(tok, line_no, line):
    """`material_arc=name:lo-hi,...` -> [(material, lo_deg, hi_deg), ...].

    Angles run around the ring: 0 deg is the +side axis (the one `w` spans),
    90 deg is the +depth axis (the one `d` spans). Arcs may wrap past 360.
    """
    arcs = []
    for item in tok.split(","):
        item = item.strip()
        if not item:
            continue
        m = re.match(r"^([\w.]+):(-?[\d.]+)-(-?[\d.]+)$", item)
        if not m:
            raise WamError("material_arc entry must be <material>:<lo>-<hi>, "
                           "got %r" % item, line_no, line)
        arcs.append((m.group(1), _num(m.group(2), line_no, line),
                     _num(m.group(3), line_no, line)))
    if not arcs:
        raise WamError("material_arc= needs at least one arc", line_no, line)
    return arcs


def _bone_point(tok, line_no, line, default_t=1.0):
    """`bone` or `bone:<t>` -> (bone name, fraction along it)."""
    name, _, t = tok.partition(":")
    if not name:
        raise WamError("expected a bone reference, got %r" % tok, line_no, line)
    return (name, _num(t, line_no, line) if t else default_t)


def _chain_ref(kv, line_no, line):
    """Pull bones=a..b or bone=x out of a kv dict -> (first, last) or None."""
    if "bones" in kv:
        m = re.match(r"^([\w.]+)\.\.([\w.]+)$", kv["bones"])
        if not m:
            raise WamError("bones=<a>..<b>", line_no, line)
        return (m.group(1), m.group(2))
    if "bone" in kv:
        return (kv["bone"], kv["bone"])
    return None


def parse_check(kw, line, tokens, line_no):
    """Parse one `checks` line into a check dict.

    Shared by models and by cross-model set files, so the two levels can
    never drift into different assertion grammars.
    """
    rest = line.strip()[len(kw):].strip()

    def csv(items):
        out = []
        for it in items:
            out.extend(x for x in it.split(",") if x)
        return out

    if kw == "measure":
        if not tokens[1:]:
            raise WamError("measure <label> <expr>", line_no, line)
        label = tokens[1]
        expr = rest[len(label):].strip()
        if not expr:
            raise WamError("measure %r needs an expression" % label,
                           line_no, line)
        return dict(kind="measure", label=label, expr=expr, line_no=line_no)

    if kw == "noclip":
        # noclip [<parts> [vs <parts>]] [strict] [in=<anim|*>] [depth=<d>]
        #        [except=<a>+<b>,...]
        words, kv = [], {}
        for t in tokens[1:]:
            if "=" in t and not t.startswith("("):
                k, v = t.split("=", 1)
                kv[k] = v
            else:
                words.append(t)
        strict = "strict" in words
        words = [w for w in words if w != "strict"]
        if "vs" in words:
            i = words.index("vs")
            subject, against = csv(words[:i]), csv(words[i + 1:])
            if not subject or not against:
                raise WamError("noclip <parts> vs <parts>", line_no, line)
        else:
            subject, against = csv(words) or None, None
        exempt = []
        for pair in csv([kv["except"]] if "except" in kv else []):
            halves = pair.split("+")
            if len(halves) != 2:
                raise WamError("noclip except= takes pairs joined by '+', "
                               "like except=hand.r+sword", line_no, line)
            exempt.append(frozenset(halves))
        return dict(kind="noclip", subject=subject, against=against,
                    strict=strict, anim=kv.get("in"),
                    depth=_num(kv["depth"], line_no, line) if "depth" in kv
                    else 0.005,
                    exempt=exempt, line_no=line_no)

    if kw == "assert":
        # Both sides are full expressions: a bound is as often another
        # measurement ("the skull ends ahead of the ribcage") as it is a
        # number. The tolerance form needs spaces around `+-`, or it would
        # be indistinguishable from `a + (-b)`.
        m = re.match(r"^(?P<expr>.+?)\s+in\s+(?P<lo>.+?)\.\.(?P<hi>.+)$", rest)
        if m:
            return dict(kind="assert", expr=m.group("expr").strip(), op="in",
                        lo=m.group("lo").strip(), hi=m.group("hi").strip(),
                        line_no=line_no)
        m = re.match(r"^(?P<expr>.+?)\s*(?P<op><=|>=|==|<|>)\s*"
                     r"(?P<val>.+?)(?:\s+\+-\s+(?P<tol>.+))?$", rest)
        if not m:
            raise WamError("assert <expr> in <lo>..<hi> | "
                           "assert <expr> <op> <expr> [+- <tol>]",
                           line_no, line)
        return dict(kind="assert", expr=m.group("expr").strip(),
                    op=m.group("op"), val=m.group("val").strip(),
                    tol=(m.group("tol") or "0").strip(), line_no=line_no)

    raise WamError("unknown checks directive %r" % kw, line_no, line)


def parse(text, path=None):
    model = Model()
    model.source_path = path
    section = None
    mirror = False
    cur_group = None
    cur_part = None
    cur_tex = None
    cur_pose = None
    cur_anim = None

    for line_no, raw in enumerate(text.splitlines(), 1):
        line = re.split(r"#(?![0-9a-fA-F]{6}\b)", raw, 1)[0].rstrip()
        if not line.strip():
            continue
        tokens = line.split()
        kw = tokens[0]

        if kw in ("model", "palette", "skeleton", "parts", "textures",
                  "animations", "checks"):
            section = kw
            mirror = False
            cur_group = None
            cur_part = cur_pose = cur_anim = None
            if kw == "model":
                if len(tokens) < 2:
                    raise WamError("model needs a name", line_no, line)
                model.name = tokens[1]
            continue

        if section is None:
            raise WamError("directive before any section", line_no, line)

        if kw == "mirror" and section in ("skeleton", "parts"):
            mirror = True
            continue
        if kw == "group" and section == "parts":
            if mirror:
                raise WamError("group inside mirror is not supported", line_no, line)
            if len(tokens) < 2:
                raise WamError("group needs a name", line_no, line)
            _, kv, flags = _split_kv(tokens[2:], line_no, line)
            _check_keys(model, "group", kv, line_no, line)
            g = dict(name=tokens[1], bone=kv.get("bone"))
            if g["bone"] is None:
                raise WamError("group needs bone=", line_no, line)
            if "dir" in kv:
                g["dir"] = kv["dir"]
            for f in flags:
                if f in DIR_WORDS:
                    g["dir"] = f
            if "across" in kv:
                g["across"] = (_vec(kv["across"], line_no, line)
                               if kv["across"].startswith("(") else kv["across"])
            if "aim" in kv:
                g["aim"] = kv["aim"]
            for f in flags:
                if f in ("along", "against"):
                    g[f] = True
            for k in ("at", "pitch", "yaw", "tilt", "spin"):
                if k in kv:
                    g[k] = _num(kv[k], line_no, line)
            if "offset" in kv:
                g["offset"] = _vec(kv["offset"], line_no, line)
            cur_group = g
            continue
        if kw == "end":
            if cur_group is not None:
                cur_group = None
            else:
                mirror = False
            continue

        if section == "model":
            if kw == "height":
                model.height = _num(tokens[1], line_no, line)
            elif kw in ("girth", "reach"):
                v = _num(tokens[1], line_no, line)
                if v <= 0:
                    raise WamError("%s must be above zero" % kw, line_no, line)
                setattr(model, kw, v)
            elif kw == "side":
                # Which way one named face of this model points, in its own
                # space. Composition then says where that side should end up
                # ("the shield's front faces left") without the author
                # deriving a single angle.
                if len(tokens) < 2:
                    raise WamError("side needs a name", line_no, line)
                _, kv, flags = _split_kv(tokens[2:], line_no, line)
                _check_keys(model, "side", kv, line_no, line)
                d = kv.get("dir")
                for f in flags:
                    if f in DIR_WORDS or f in ("left", "right"):
                        d = f
                if d is None:
                    raise WamError("side %r needs dir=" % tokens[1], line_no, line)
                model.sides[tokens[1]] = dict(
                    dir=d, **{k: _num(kv[k], line_no, line)
                              for k in ("pitch", "yaw", "tilt") if k in kv})
            elif kw == "marker":
                # A named point in the model's own space. Parts can only be
                # addressed by their bounding-box centre, which says nothing
                # useful about a long blade — a marker names the tip, the
                # edge, the pommel, so they can be reasoned about at all.
                if len(tokens) < 2:
                    raise WamError("marker needs a name", line_no, line)
                _, kv, _ = _split_kv(tokens[2:], line_no, line)
                if "at" not in kv:
                    raise WamError("marker %r needs at=(x,y,z)" % tokens[1],
                                   line_no, line)
                model.markers[tokens[1]] = _vec(kv["at"], line_no, line)
            elif kw == "hold":
                # How this model is carried, stated once by the model that
                # knows — the sword. Written in the composition instead, the
                # same three facts get re-derived by hand for every character
                # who picks it up, and they get re-derived wrong: a blade
                # reversed into a chest, or presenting its flat like a paddle.
                if len(tokens) < 2:
                    raise WamError("hold needs the name of a grip anchor",
                                   line_no, line)
                _, kv, _ = _split_kv(tokens[2:], line_no, line)
                _check_keys(model, "hold", kv, line_no, line)
                if "point" not in kv:
                    raise WamError(
                        "hold %r needs point=<marker> — the business end. "
                        "Without it nothing knows which way round the model "
                        "goes, which is exactly how blades end up reversed"
                        % tokens[1], line_no, line)
                model.hold = dict(anchor=tokens[1], point=kv["point"],
                                  edge=kv.get("edge"),
                                  carry=_num(kv["carry"], line_no, line)
                                  if "carry" in kv else 55.0,
                                  line_no=line_no)
            elif kw == "anchor":
                # A named frame on the model — the point and axis by which it
                # meets something else. `graft ... align=<name>` maps it onto
                # a host bone, so a grip lands in a fist by construction
                # instead of by a hand-guessed rotation and offset.
                if len(tokens) < 2:
                    raise WamError("anchor needs a name", line_no, line)
                _, kv, flags = _split_kv(tokens[2:], line_no, line)
                a = dict(pos=(0.0, 0.0, 0.0), dir="up")
                if "at" in kv:
                    a["pos"] = _vec(kv["at"], line_no, line)
                if "dir" in kv:
                    a["dir"] = kv["dir"]
                for f in flags:
                    if f in DIR_WORDS:
                        a["dir"] = f
                for k in ("pitch", "yaw", "tilt"):
                    if k in kv:
                        a[k] = _num(kv[k], line_no, line)
                model.anchors[tokens[1]] = a
            elif kw == "style":
                model.style = tokens[1]
            elif kw == "shading":
                if len(tokens) < 2 or tokens[1] not in ("smooth", "faceted"):
                    raise WamError("shading must be 'smooth' or 'faceted'",
                                   line_no, line)
                model.shading = tokens[1]
            else:
                raise WamError("unknown model directive %r" % kw, line_no, line)

        elif section == "palette":
            if len(tokens) < 2:
                raise WamError("palette entry: <name> #rrggbb [metal=0..1] "
                               "[rough=0..1]", line_no, line)
            model.palette[kw] = _hex_color(tokens[1], line_no, line)
            _, kv, _ = _split_kv(tokens[2:], line_no, line)
            unknown = sorted(set(kv) - {"metal", "rough"})
            if unknown:
                raise WamError(
                    "palette %r does not understand %s — a colour takes "
                    "metal= and rough=, both 0..1"
                    % (kw, ", ".join(repr(u) for u in unknown)), line_no, line)
            if kv:
                pbr = dict(metal=0.0, rough=0.9)
                for k in ("metal", "rough"):
                    if k in kv:
                        v = _num(kv[k], line_no, line)
                        if not 0.0 <= v <= 1.0:
                            raise WamError(
                                "palette %r: %s=%g is outside 0..1" % (kw, k, v),
                                line_no, line)
                        pbr[k] = v
                model.material_pbr[kw] = pbr

        elif section == "textures":
            if kw == "texture":
                if len(tokens) < 2:
                    raise WamError("texture needs a name", line_no, line)
                _, kv, _ = _split_kv(tokens[2:], line_no, line)
                if "base" not in kv:
                    raise WamError("texture needs base=#rrggbb", line_no, line)
                cur_tex = dict(base=_hex_color(kv["base"], line_no, line), ops=[])
                model.textures[tokens[1]] = cur_tex
            else:
                if not model.textures:
                    raise WamError("texture op before any texture", line_no, line)
                _, kv, flags = _split_kv(tokens[1:], line_no, line)
                op = dict(op=kw)
                for k, v in kv.items():
                    if v.startswith("#"):
                        op[k] = _hex_color(v, line_no, line)
                    elif re.match(r"^[a-zA-Z_]", v):
                        op[k] = v
                    else:
                        op[k] = _num(v, line_no, line)
                cur_tex["ops"].append(op)
            continue

        elif section == "skeleton":
            if kw == "root":
                if len(tokens) < 3 or tokens[2] != "at":
                    raise WamError("root <name> at <y | (x,y,z)>", line_no, line)
                loc = tokens[3]
                pos = _vec(loc, line_no, line) if loc.startswith("(") else (0.0, _num(loc, line_no, line), 0.0)
                model.bones.append(dict(name=tokens[1], parent=None, root_pos=pos,
                                        len=0.0, mirror=mirror, line_no=line_no))
            elif kw == "bone":
                if len(tokens) < 2:
                    raise WamError("bone needs a name", line_no, line)
                _, kv, flags = _split_kv(tokens[2:], line_no, line)
                _check_keys(model, "bone", kv, line_no, line)
                b = dict(name=tokens[1], parent=kv.get("parent"),
                         dir=kv.get("dir"), mirror=mirror, line_no=line_no)
                for f in flags:
                    if f in DIR_WORDS:
                        b["dir"] = f
                for k in ("pitch", "yaw", "roll", "tilt", "len", "at", "side", "fwd", "up"):
                    if k in kv:
                        b[k] = _num(kv[k], line_no, line)
                if "to" in kv:
                    b["to"] = kv["to"]
                if "bend" in kv:
                    b["bend"] = kv["bend"]
                if "offset" in kv:
                    b["offset"] = _vec(kv["offset"], line_no, line)
                for k in ("head", "tail"):
                    if k in kv:
                        b["pin_" + k] = _vec(kv[k], line_no, line)
                if b["parent"] is None:
                    raise WamError("bone %r needs parent=" % b["name"], line_no, line)
                if b.get("to") is None and ("len" not in b or b.get("len") is None):
                    raise WamError(
                        "bone %r needs len= or to=<landmark>" % b["name"],
                        line_no, line)
                if b.get("to") is not None and "len" in b:
                    raise WamError(
                        "bone %r has both len= and to= — to= solves the "
                        "length, so the len= would be discarded"
                        % b["name"], line_no, line)
                model.bones.append(b)
            elif kw == "pin":
                if len(tokens) < 2:
                    raise WamError("pin <bone> at=(x,y,z) | tail=(x,y,z)",
                                   line_no, line)
                _, kv, _ = _split_kv(tokens[2:], line_no, line)
                p = dict(bone=tokens[1], line_no=line_no)
                for src, dst in (("at", "head"), ("head", "head"),
                                 ("tail", "tail")):
                    if src in kv:
                        p[dst] = _vec(kv[src], line_no, line)
                if "head" not in p and "tail" not in p:
                    raise WamError("pin %r needs at=(x,y,z) or tail=(x,y,z)"
                                   % tokens[1], line_no, line)
                model.pins.append(p)
            else:
                raise WamError("unknown skeleton directive %r" % kw, line_no, line)

        elif section == "parts":
            if kw in ("loft", "sweep", "attach", "web"):
                if len(tokens) < 2:
                    raise WamError("%s needs a name" % kw, line_no, line)
                _, kv, flags = _split_kv(tokens[2:], line_no, line)
                _check_keys(model, "part", kv, line_no, line)
                p = dict(kind=kw, name=tokens[1], mirror=mirror, rings=[], segs=[],
                         ribs=[], cap_start="flat", cap_end="flat",
                         line_no=line_no)
                p["material"] = kv.get("material")
                for f in flags:
                    if f in SHAPE_WORDS:
                        p["shape"] = f
                    if f in DIR_WORDS:
                        p["dir"] = f
                    if f == "double_sided":
                        p["double_sided"] = True
                    if f in ("faceted", "smooth"):
                        p["faceted"] = (f == "faceted")
                if "bones" in kv:
                    m = re.match(r"^([\w.]+)\.\.([\w.]+)$", kv["bones"])
                    if not m:
                        raise WamError("bones=<a>..<b>", line_no, line)
                    p["chain"] = (m.group(1), m.group(2))
                if "bone" in kv:
                    p["bone"] = kv["bone"]
                if "dir" in kv:
                    p["dir"] = kv["dir"]
                if "shape" in kv:
                    p["shape"] = kv["shape"]
                if "kind" in kv:
                    p["prim"] = kv["kind"]
                if "on" in kv:
                    p["on"] = kv["on"]
                if "press" in kv:
                    if kv["press"] not in ("on", "off"):
                        raise WamError("press= must be on|off", line_no, line)
                    p["press"] = kv["press"] == "on"
                for f in flags:
                    if f == "nopress":
                        p["press"] = False
                if "frame" in kv:
                    if kv["frame"] not in FRAME_WORDS:
                        raise WamError("frame= must be one of %s"
                                       % "|".join(FRAME_WORDS), line_no, line)
                    p["frame"] = kv["frame"]
                if "refaxis" in kv:
                    p["refaxis"] = _vec(kv["refaxis"], line_no, line)
                if "skin" in kv:
                    p["skin"] = _skin_spec(kv["skin"], line_no, line)
                if "material_arc" in kv:
                    p["material_arc"] = _arc_spec(kv["material_arc"], line_no, line)
                if "anchor" in kv:
                    p["anchor"] = _bone_point(kv["anchor"], line_no, line)
                if "trailing" in kv:
                    if kv["trailing"] not in ("straight", "scallop"):
                        raise WamError("trailing= must be straight|scallop",
                                       line_no, line)
                    p["trailing"] = kv["trailing"]
                if "at" in kv and kv["at"].startswith("("):
                    p["gpos"] = _vec(kv.pop("at"), line_no, line)
                for k in ("rest", "push"):
                    if k in kv:
                        p[k] = kv[k]
                if "layer" in kv:
                    p["layer"] = _num(kv["layer"], line_no, line)
                for k in ("at", "len", "pitch", "yaw", "tilt", "size", "w", "d",
                          "h", "sides", "taper", "inset", "scallop",
                          "thickness", "steps", "usteps"):
                    if k in kv:
                        p[k] = _num(kv[k], line_no, line)
                if "offset" in kv:
                    p["offset"] = _vec(kv["offset"], line_no, line)
                if cur_group is not None:
                    p["group"] = cur_group
                model.parts.append(p)
                cur_part = p
            elif kw == "ring":
                if cur_part is None or cur_part["kind"] != "loft":
                    raise WamError("ring outside a loft", line_no, line)
                pos, kv, flags = _split_kv(tokens[1:], line_no, line)
                if "t" in kv:
                    t = _num(kv["t"], line_no, line)
                elif pos:
                    t = _num(pos[0], line_no, line)
                else:
                    raise WamError("ring needs t", line_no, line)
                _check_keys(model, "ring", kv, line_no, line)
                r = dict(t=t)
                for k in ("w", "d", "fwd", "side", "up", "roll",
                          "wtop", "wbot", "dtop", "dbot"):
                    if k in kv:
                        r[k] = _num(kv[k], line_no, line)
                if "shape" in kv:
                    r["shape"] = kv["shape"]
                if "material" in kv:
                    r["material"] = kv["material"]
                if "material_arc" in kv:
                    r["material_arc"] = _arc_spec(kv["material_arc"], line_no, line)
                if "follow" in kv:
                    r["follow"] = kv["follow"]
                if "skin" in kv:
                    r["skin"] = _skin_spec(kv["skin"], line_no, line)
                if "tip" in flags:
                    r["tip"] = True
                if "w" not in r:
                    if "wtop" in r and "wbot" in r:
                        r["w"] = (r["wtop"] + r["wbot"]) / 2.0
                    else:
                        raise WamError("ring needs w=", line_no, line)
                cur_part["rings"].append(r)
            elif kw == "seg":
                if cur_part is None or cur_part["kind"] != "sweep":
                    raise WamError("seg outside a sweep", line_no, line)
                _, kv, flags = _split_kv(tokens[1:], line_no, line)
                _check_keys(model, "seg", kv, line_no, line)
                s = {}
                for k in ("len", "r", "up", "down", "fwd", "back",
                          "curl", "roll"):
                    if k in kv:
                        s[k] = _num(kv[k], line_no, line)
                if "material" in kv:
                    s["material"] = kv["material"]
                if "skin" in kv:
                    s["skin"] = _skin_spec(kv["skin"], line_no, line)
                if "tip" in flags:
                    s["tip"] = True
                if "len" not in s or "r" not in s:
                    raise WamError("seg needs len= and r=", line_no, line)
                cur_part["segs"].append(s)
            elif kw == "rib":
                if cur_part is None or cur_part["kind"] != "web":
                    raise WamError("rib outside a web", line_no, line)
                _, kv, flags = _split_kv(tokens[1:], line_no, line)
                chain = _chain_ref(kv, line_no, line)
                if chain is None:
                    raise WamError("rib needs bones=<a>..<b> or bone=<x>",
                                   line_no, line)
                _check_keys(model, "rib", kv, line_no, line)
                rib = dict(chain=chain)
                if "from" in kv:
                    rib["from"] = _bone_point(kv["from"], line_no, line)
                for k in ("inset", "start"):
                    if k in kv:
                        rib[k] = _num(kv[k], line_no, line)
                if "skin" in kv:
                    rib["skin"] = _skin_spec(kv["skin"], line_no, line)
                cur_part["ribs"].append(rib)
            elif kw == "cap":
                if cur_part is None:
                    raise WamError("cap outside a part", line_no, line)
                _, kv, _ = _split_kv(tokens[1:], line_no, line)
                if "start" in kv:
                    cur_part["cap_start"] = kv["start"]
                if "end" in kv:
                    cur_part["cap_end"] = kv["end"]
            else:
                raise WamError("unknown parts directive %r" % kw, line_no, line)

        elif section == "animations":
            if kw == "pose":
                if len(tokens) < 2:
                    raise WamError("pose needs a name", line_no, line)
                if len(tokens) >= 4 and tokens[2] == "mirror":
                    model.poses[tokens[1]] = dict(mirror_of=tokens[3], bones={})
                else:
                    model.poses[tokens[1]] = dict(mirror_of=None, bones={})
                cur_pose = model.poses[tokens[1]]
                cur_anim = None
            elif kw == "anim":
                if len(tokens) < 2:
                    raise WamError("anim needs a name", line_no, line)
                _, kv, flags = _split_kv(tokens[2:], line_no, line)
                a = dict(name=tokens[1], loop="loop" in flags,
                         dur=_num(kv.get("dur", "1"), line_no, line),
                         keys=[], channels=[], mirrorphase=None)
                model.anims.append(a)
                cur_anim = a
                cur_pose = None
            elif kw == "key":
                if cur_anim is None:
                    raise WamError("key outside an anim", line_no, line)
                pos, kv, _ = _split_kv(tokens[1:], line_no, line)
                if not pos or "pose" not in kv:
                    raise WamError("key <pct> pose=<name|rest>", line_no, line)
                cur_anim["keys"].append((_num(pos[0], line_no, line), kv["pose"]))
            elif kw == "ch":
                if cur_anim is None:
                    raise WamError("ch outside an anim", line_no, line)
                if len(tokens) < 4:
                    raise WamError("ch <bone> <pitch|yaw|roll> <pct>=<deg>...", line_no, line)
                keys = []
                for t in tokens[3:]:
                    if "=" not in t:
                        raise WamError("channel key must be <pct>=<deg>", line_no, line)
                    kt, kvv = t.split("=", 1)
                    keys.append((_num(kt, line_no, line), _num(kvv, line_no, line)))
                cur_anim["channels"].append(dict(bone=tokens[1], chan=tokens[2], keys=keys))
            elif kw == "mirrorphase":
                if cur_anim is None:
                    raise WamError("mirrorphase outside an anim", line_no, line)
                cur_anim["mirrorphase"] = _num(tokens[1], line_no, line)
            elif cur_pose is not None:
                # bone rotation line inside a pose
                _, kv, _ = _split_kv(tokens[1:], line_no, line)
                rot = {}
                for k in ("pitch", "yaw", "roll", "tilt"):
                    if k in kv:
                        rot[k] = _num(kv[k], line_no, line)
                if "shift" in kv:
                    rot["shift"] = _vec(kv["shift"], line_no, line)
                cur_pose["bones"][kw] = rot
            else:
                raise WamError("unknown animations directive %r" % kw, line_no, line)

        elif section == "checks":
            model.checks.append(
                parse_check(kw, line, tokens, line_no))

    validate_hold(model)
    return apply_proportions(model)


def validate_hold(model):
    """The grip's declared axis must agree with where the business end is.

    This is the reversal that survives everything else. `anchor grip dir=up`
    is a claim about which way the model extends from the hand, and `point=`
    is where the business end actually sits; when the two disagree, every
    composition faithfully aims the declared axis and the blade comes out
    backwards. Nothing downstream can catch it — the grip lands in the fist,
    the aim resolves, no geometry intersects, and the "points into the
    wielder" test passes because a blade reversed *away* from the body is
    still farther from it than the grip is.

    It is also purely a property of the weapon, so it is answerable here,
    before any character has picked the thing up.
    """
    hold = getattr(model, "hold", None)
    if not hold:
        return
    from .skeleton import resolve_dir            # deferred: skeleton imports us
    a = model.anchors.get(hold["anchor"])
    p = model.markers.get(hold["point"])
    if a is None or p is None:
        return                                   # reported elsewhere
    import numpy as np
    d = resolve_dir(a.get("dir", "up"), a.get("pitch", 0.0),
                    a.get("yaw", 0.0), a.get("tilt", 0.0))
    v = np.asarray(p, dtype=float) - np.asarray(a.get("pos", (0, 0, 0)), float)
    n = float(np.linalg.norm(v))
    if n < 1e-9:
        raise WamError(
            "model %r: hold point=%r sits exactly on the grip, so nothing "
            "says which way round the model goes" % (model.name, hold["point"]))
    cos = float(np.dot(d, v / n))
    deg = math.degrees(math.acos(max(-1.0, min(1.0, cos))))
    if deg > 90.0:
        raise WamError(
            "model %r: anchor %r points %.0f degrees away from %r. The anchor "
            "declares which way this model extends from the hand and %r is "
            "its business end, so as written every graft will carry it "
            "backwards — the blade will point behind the wielder. Flip the "
            "anchor's dir=, or move the anchor to the other end."
            % (model.name, hold["anchor"], deg, hold["point"], hold["point"]))
    if deg > 40.0:
        model.warnings.append(
            "anchor %r points %.0f degrees off %r — the grip axis and the "
            "business end disagree enough that the carry angle will be that "
            "far out on every character holding it"
            % (hold["anchor"], deg, hold["point"]))


def apply_proportions(model):
    """Apply `girth` / `reach` to the authored numbers, before anything solves.

    Rescaling a model proportionally otherwise means editing every
    size-bearing token in the file — two hundred of them in a real character
    — which is exactly the mechanical edit that goes wrong silently. These
    scale the authored values instead, so the change is one number you can
    tune against a check.

    They are applied to the source numbers rather than to world space on
    purpose: a non-uniform scale of the finished mesh would shear limbs as
    they rotate, so an arm stretched along y would grow *thicker* rather than
    longer once it raised to horizontal. Scaling what the author wrote has no
    such artifact — the solver simply runs on different numbers.
    """
    g, r = model.girth, model.reach
    if g == 1.0 and r == 1.0:
        return model

    def mul(d, keys, f):
        for k in keys:
            if k in d and d[k] is not None:
                d[k] = d[k] * f

    for b in model.bones:
        mul(b, ("len",), r)
        if b.get("root_pos") is not None:
            # the skeleton grows about the ground, not about the root, so a
            # taller figure does not simply sink through the floor
            b["root_pos"] = tuple(v * r for v in b["root_pos"])
    for p in model.parts:
        mul(p, ("len",), r)
        mul(p, ("size", "w", "d", "h", "thickness"), g)
        for ring in p.get("rings", ()):
            mul(ring, ("w", "d", "wtop", "wbot", "dtop", "dbot"), g)
        for seg in p.get("segs", ()):
            mul(seg, ("len",), r)
            mul(seg, ("r",), g)
    return model


def parse_file(path):
    with open(path) as f:
        return parse(f.read(), path)
