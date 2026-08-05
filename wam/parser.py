
"""Parser for the WAM (WoW-ish Art Model) language.

Line-oriented format. Sections: model, palette, skeleton, parts, animations.
See SPEC.md for the full grammar.
"""
import re


class WamError(Exception):
    def __init__(self, msg, line_no=None, line=None):
        self.line_no = line_no
        self.line = line
        loc = " (line %d: %r)" % (line_no, line) if line_no else ""
        super().__init__(msg + loc)


DIR_WORDS = ("up", "down", "fwd", "back", "side", "in")
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
        self.palette = {}          # name -> (r,g,b) floats 0..1
        self.textures = {}         # name -> {base:(r,g,b), ops:[...]}
        self.bones = []            # list of dicts
        self.pins = []             # list of dicts: bone -> absolute head/tail
        self.parts = []            # list of dicts
        self.poses = {}            # name -> {bone: {pitch,yaw,roll}}
        self.anims = []            # list of dicts
        self.checks = []           # list of dicts: assert / measure
        self.source_path = None


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
            g = dict(name=tokens[1], bone=kv.get("bone"))
            if g["bone"] is None:
                raise WamError("group needs bone=", line_no, line)
            if "dir" in kv:
                g["dir"] = kv["dir"]
            for f in flags:
                if f in DIR_WORDS:
                    g["dir"] = f
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
            elif kw == "style":
                model.style = tokens[1]
            else:
                raise WamError("unknown model directive %r" % kw, line_no, line)

        elif section == "palette":
            if len(tokens) != 2:
                raise WamError("palette entry: <name> #rrggbb", line_no, line)
            model.palette[kw] = _hex_color(tokens[1], line_no, line)

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
                b = dict(name=tokens[1], parent=kv.get("parent"),
                         dir=kv.get("dir"), mirror=mirror, line_no=line_no)
                for f in flags:
                    if f in DIR_WORDS:
                        b["dir"] = f
                for k in ("pitch", "yaw", "roll", "tilt", "len", "at", "side", "fwd", "up"):
                    if k in kv:
                        b[k] = _num(kv[k], line_no, line)
                if "offset" in kv:
                    b["offset"] = _vec(kv["offset"], line_no, line)
                for k in ("head", "tail"):
                    if k in kv:
                        b["pin_" + k] = _vec(kv[k], line_no, line)
                if b["parent"] is None:
                    raise WamError("bone %r needs parent=" % b["name"], line_no, line)
                if b.get("len") is None or "len" not in b:
                    raise WamError("bone %r needs len=" % b["name"], line_no, line)
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
            rest = line.strip()[len(kw):].strip()
            if kw == "measure":
                if not tokens[1:]:
                    raise WamError("measure <label> <expr>", line_no, line)
                label = tokens[1]
                expr = rest[len(label):].strip()
                if not expr:
                    raise WamError("measure %r needs an expression" % label,
                                   line_no, line)
                model.checks.append(dict(kind="measure", label=label,
                                         expr=expr, line_no=line_no))
            elif kw == "noclip":
                # noclip [<parts> [vs <parts>]] [in=<anim|*>] [depth=<d>]
                #        [except=<a>+<b>,...]
                words, kv = [], {}
                for t in tokens[1:]:
                    if "=" in t and not t.startswith("("):
                        k, v = t.split("=", 1)
                        kv[k] = v
                    else:
                        words.append(t)

                def _csv(items):
                    out = []
                    for it in items:
                        out.extend(x for x in it.split(",") if x)
                    return out

                strict = "strict" in words
                words = [w for w in words if w != "strict"]
                if "vs" in words:
                    i = words.index("vs")
                    subject, against = _csv(words[:i]), _csv(words[i + 1:])
                    if not subject or not against:
                        raise WamError("noclip <parts> vs <parts>", line_no, line)
                else:
                    subject, against = _csv(words) or None, None
                exempt = []
                for pair in _csv([kv["except"]] if "except" in kv else []):
                    halves = pair.split("+")
                    if len(halves) != 2:
                        raise WamError("noclip except= takes pairs joined by "
                                       "'+', like except=hand.r+sword",
                                       line_no, line)
                    exempt.append(frozenset(halves))
                model.checks.append(dict(
                    kind="noclip", subject=subject, against=against,
                    strict=strict,
                    anim=kv.get("in"),
                    depth=_num(kv["depth"], line_no, line) if "depth" in kv
                    else 0.005,
                    exempt=exempt, line_no=line_no))
            elif kw == "assert":
                # Both sides are full expressions: a bound is as often another
                # measurement ("the skull ends ahead of the ribcage") as it is
                # a number. The tolerance form needs spaces around `+-`, or it
                # would be indistinguishable from `a + (-b)`.
                m = re.match(r"^(?P<expr>.+?)\s+in\s+(?P<lo>.+?)\.\.(?P<hi>.+)$",
                             rest)
                if m:
                    model.checks.append(dict(
                        kind="assert", expr=m.group("expr").strip(), op="in",
                        lo=m.group("lo").strip(), hi=m.group("hi").strip(),
                        line_no=line_no))
                    continue
                m = re.match(r"^(?P<expr>.+?)\s*(?P<op><=|>=|==|<|>)\s*"
                             r"(?P<val>.+?)"
                             r"(?:\s+\+-\s+(?P<tol>.+))?$", rest)
                if not m:
                    raise WamError("assert <expr> in <lo>..<hi> | "
                                   "assert <expr> <op> <expr> [+- <tol>]",
                                   line_no, line)
                model.checks.append(dict(
                    kind="assert", expr=m.group("expr").strip(),
                    op=m.group("op"), val=m.group("val").strip(),
                    tol=(m.group("tol") or "0").strip(), line_no=line_no))
            else:
                raise WamError("unknown checks directive %r" % kw, line_no, line)

    return model


def parse_file(path):
    with open(path) as f:
        return parse(f.read(), path)
