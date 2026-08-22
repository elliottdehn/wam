"""Parser for the WAM audio language (`.wama`).

Line-oriented and indentation-scoped, like `.wam`. Sections at column 0:
``audio`` (header), ``voices``, ``song <name>``, ``sound <name>``.
See AUDIO_SPEC.md for the grammar this accepts.

The parser's only job is to turn text into a checked structure. Every musical
or acoustic decision -- what a "bright pluck" is, where an eighth note lands,
how loud a layer ends up -- belongs to the compiler, not to this file.
"""
import os
import re

from . import notation as wnotation
from .notation import WamAudioError, MODES, NOTE_PC  # noqa: F401


# ------------------------------------------------------------- vocabularies

# Named tempi, so a piece can be "walk" instead of a number nobody can picture.
TEMPI = {"funeral": 52, "slow": 66, "walk": 84, "medium": 104,
         "brisk": 126, "fast": 148, "frantic": 176}

# Named durations for one-shots. Relative-first: the author says how big the
# sound is, and every layer inside it is a percentage of that.
LENGTHS = {"tiny": 0.12, "short": 0.35, "medium": 0.8, "long": 1.6, "huge": 3.2}

# Named registers, so pitch decisions stay discrete for non-musical sounds.
REGISTERS = {"sub": 45.0, "low": 110.0, "mid": 330.0, "high": 900.0,
             "air": 2600.0, "sparkle": 6000.0}

DURATION_BEATS = {"w": 4.0, "h": 2.0, "q": 1.0, "e": 0.5, "s": 0.25, "t": 0.125}

ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7}

DRUM_PIECES = {"k": "kick", "s": "snare", "h": "hat", "H": "openhat",
               "t": "tom", "T": "lowtom", "c": "crash", "r": "ride"}

# Fields a tone is made of. `tones` blocks set them by name; voices and layers
# can override any of them inline for a one-off without inventing a name.
TONE_FIELDS = {"tilt": float, "harm": float, "noise": float,
               "odd": bool, "thin": bool, "spread": float}

# The synth spells two of them differently; keep the author-facing words short.
TONE_FIELD_ALIASES = {"odd": "odd_only", "spread": "detune"}

VOICE_KEYS = ("source", "tone", "env", "level", "octave", "detune", "space",
              "echo", "drive", "pan", "damp", "glide", "cut",
              "bank", "file", "root") + tuple(TONE_FIELDS)

SWEEP_TARGETS = ("pitch", "center", "level")


# ------------------------------------------------------------------ helpers


def _num(tok, line_no, line, what="a number"):
    t = tok.strip()
    pct = t.endswith("%")
    if pct:
        t = t[:-1]
    try:
        v = float(t)
    except ValueError:
        raise WamAudioError("expected %s, got %r" % (what, tok), line_no, line)
    return v / 100.0 if pct else v


def _split_kv(tokens):
    """Split tokens into (positional, kv-dict) -- `.wam`'s convention."""
    pos, kv = [], {}
    for t in tokens:
        if "=" in t and not t.startswith("("):
            k, v = t.split("=", 1)
            kv[k.strip()] = v.strip()
        else:
            pos.append(t)
    return pos, kv


def _check_keys(kv, allowed, line_no, line, what):
    for k in kv:
        if k not in allowed:
            raise WamAudioError(
                "unknown %s option %r (allowed: %s)" % (what, k, ", ".join(sorted(allowed))),
                line_no, line)


def parse_freq(tok, line_no, line):
    """A named register, a note name like `a2`, or a raw frequency in Hz."""
    t = tok.strip().lower()
    if t in REGISTERS:
        return REGISTERS[t]
    m = re.match(r"^([a-g])([b#]?)(-?\d)$", t)
    if m:
        pc = NOTE_PC[m.group(1)] + {"b": -1, "#": 1, "": 0}[m.group(2)]
        midi = 12 * (int(m.group(3)) + 1) + pc
        return 440.0 * 2.0 ** ((midi - 69) / 12.0)
    if t.endswith("hz"):
        t = t[:-2]
    try:
        return float(t)
    except ValueError:
        raise WamAudioError(
            "expected a register (%s), a note like a2, or a frequency; got %r"
            % ("/".join(REGISTERS), tok), line_no, line)


def _flag(tok, line_no, line):
    t = tok.strip().lower()
    if t in ("on", "yes", "true", "1"):
        return True
    if t in ("off", "no", "false", "0"):
        return False
    raise WamAudioError("expected on/off, got %r" % tok, line_no, line)


def parse_tone_overrides(kv, line_no, line):
    """Pull the tone fields out of a kv dict, in the synth's own spelling.

    Returns {} when the author overrode nothing, which keeps the named preset
    intact rather than pinning it to today's default numbers.
    """
    out = {}
    for word, caster in TONE_FIELDS.items():
        if word not in kv:
            continue
        raw = kv[word]
        val = _flag(raw, line_no, line) if caster is bool else _num(raw, line_no, line)
        out[TONE_FIELD_ALIASES.get(word, word)] = val
    return out


def parse_key(tokens, line_no, line):
    if not tokens:
        raise WamAudioError("key needs a root and a mode, e.g. `key d minor`", line_no, line)
    root = tokens[0].lower()
    # Both spellings: `bes` and `fis` as the score layer writes them, and
    # `bb`/`f#` as most people type them.
    m = re.match(r"^([a-g])(isis|eses|is|es|##|#|bb|b|)$", root)
    if not m:
        raise WamAudioError("unknown key root %r" % tokens[0], line_no, line)
    alter = {"is": 1, "isis": 2, "es": -1, "eses": -2,
             "#": 1, "##": 2, "b": -1, "bb": -2, "": 0}[m.group(2)]
    pc = (NOTE_PC[m.group(1)] + alter) % 12
    mode = (tokens[1].lower() if len(tokens) > 1 else "major")
    if mode not in MODES:
        raise WamAudioError("unknown mode %r (have: %s)" % (mode, ", ".join(MODES)),
                            line_no, line)
    return {"root": pc, "mode": mode, "letter": m.group(1), "alter": alter,
            "text": "%s %s" % (tokens[0], mode)}


def parse_tempo(tok, line_no, line):
    t = tok.strip().lower()
    if t in TEMPI:
        return float(TEMPI[t])
    v = _num(t, line_no, line, "a tempo (bpm or a name)")
    if not 20.0 <= v <= 300.0:
        raise WamAudioError("tempo %g is outside 20..300 bpm" % v, line_no, line)
    return v


def parse_length(tokens, line_no, line):
    """`length short` or `length 0.4 s` or `length 40%` (of the parent)."""
    if not tokens:
        raise WamAudioError("length needs a value", line_no, line)
    t = tokens[0].strip().lower()
    if t in LENGTHS:
        return {"kind": "abs", "value": LENGTHS[t], "text": t}
    if t.endswith("%"):
        return {"kind": "rel", "value": _num(t, line_no, line), "text": t}
    v = _num(t, line_no, line, "a length")
    if len(tokens) > 1 and tokens[1].lower() in ("ms", "msec"):
        v /= 1000.0
    return {"kind": "abs", "value": v, "text": "%gs" % v}


# ---------------------------------------------------------------- note text


def parse_chord_symbol(tok, line_no, line):
    """`i`, `VI`, `V7`, `viio`, `iv:2` -- a step in the key, held for n bars.

    The chord is built by stacking thirds inside the declared mode, so a
    numeral always lands in the key without the author spelling any notes.
    Case is an override on top of that: lowercase asks for a minor triad,
    uppercase for a major one. That is how a minor key gets its major V --
    the cadence every minor-key piece wants and the mode does not supply.
    Suffixes: `7` adds the seventh, `o` diminishes, `+` augments.
    """
    raw = tok
    bars = 1.0
    if ":" in tok:
        tok, held = tok.split(":", 1)
        bars = _num(held, line_no, line, "a bar count")
    quality = None
    if tok.endswith("o"):
        quality, tok = "dim", tok[:-1]
    elif tok.endswith("+"):
        quality, tok = "aug", tok[:-1]
    seventh = tok.endswith("7")
    if seventh:
        tok = tok[:-1]
    key = tok.lower()
    if key not in ROMAN:
        raise WamAudioError(
            "expected a roman numeral i..vii (optionally 7, o, + and :bars), got %r"
            % raw, line_no, line)
    if quality is None and tok.isalpha():
        quality = "major" if tok.isupper() else "minor"
    return {"degree": ROMAN[key], "seventh": seventh, "quality": quality,
            "bars": bars, "text": raw}


def parse_note_token(tok, line_no, line):
    """One phrase token.

    Forms: `5` degree, `b3` flattened degree, `1^` octave up, `5_` octave down,
    `[1,b3,5]` chord, `.` rest, `~` tie to the previous note, any of them with
    a `:q` / `:e.` duration suffix and a trailing `!` accent.
    """
    raw = tok
    ev = {"dur": None, "accent": 1.0, "raw": raw}
    if ":" in tok:
        tok, dur = tok.split(":", 1)
        dotted = dur.endswith(".")
        if dotted:
            dur = dur[:-1]
        if dur not in DURATION_BEATS:
            raise WamAudioError(
                "unknown duration %r (use w h q e s t, optionally dotted)" % dur,
                line_no, line)
        ev["dur"] = DURATION_BEATS[dur] * (1.5 if dotted else 1.0)
    while tok.endswith("!"):
        ev["accent"] *= 1.35
        tok = tok[:-1]
    while tok.endswith("?"):
        ev["accent"] *= 0.7
        tok = tok[:-1]
    if tok == ".":
        ev["kind"] = "rest"
        return ev
    if tok == "~":
        ev["kind"] = "tie"
        return ev
    if tok.startswith("*"):
        # `*` is the chord sounding now; `*3` is its third. A track written
        # this way follows the progression without restating it.
        rest = tok[1:]
        octv = 0
        while rest and rest[-1] in "^_":
            octv += 1 if rest[-1] == "^" else -1
            rest = rest[:-1]
        if rest == "":
            ev["kind"] = "chord_here"
            ev["octave"] = octv
            return ev
        if rest not in ("1", "3", "5", "7"):
            raise WamAudioError(
                "chord tone must be *, *1, *3, *5 or *7, got %r" % raw, line_no, line)
        ev["kind"] = "chord_tone"
        ev["tone_index"] = {"1": 0, "3": 1, "5": 2, "7": 3}[rest]
        ev["octave"] = octv
        return ev
    if tok.startswith("["):
        if not tok.endswith("]"):
            raise WamAudioError("unclosed chord %r" % raw, line_no, line)
        ev["kind"] = "chord"
        ev["degrees"] = [_degree(p, line_no, line)
                         for p in tok[1:-1].split(",") if p.strip()]
        if not ev["degrees"]:
            raise WamAudioError("empty chord %r" % raw, line_no, line)
        return ev
    ev["kind"] = "note"
    ev["degrees"] = [_degree(tok, line_no, line)]
    return ev


def _degree(tok, line_no, line):
    """`b3^` -> {degree:3, alter:-1, octave:+1}. Scale degrees, never Hz."""
    t = tok.strip()
    octv = 0
    while t and t[-1] in "^_":
        octv += 1 if t[-1] == "^" else -1
        t = t[:-1]
    alter = 0
    while t and t[0] in "b#":
        alter += -1 if t[0] == "b" else 1
        t = t[1:]
    if not t.isdigit() or not 1 <= int(t) <= 9:
        raise WamAudioError("expected a scale degree 1..9, got %r" % tok, line_no, line)
    return {"degree": int(t), "alter": alter, "octave": octv}


def parse_drum_token(tok, line_no, line):
    ev = {"accent": 1.0, "raw": tok, "dur": None}
    if ":" in tok:
        tok, dur = tok.split(":", 1)
        dotted = dur.endswith(".")
        if dotted:
            dur = dur[:-1]
        if dur not in DURATION_BEATS:
            raise WamAudioError("unknown duration %r" % dur, line_no, line)
        ev["dur"] = DURATION_BEATS[dur] * (1.5 if dotted else 1.0)
    while tok.endswith("!"):
        ev["accent"] *= 1.35
        tok = tok[:-1]
    while tok.endswith("?"):
        ev["accent"] *= 0.7
        tok = tok[:-1]
    if tok == ".":
        ev["kind"] = "rest"
        return ev
    if tok not in DRUM_PIECES:
        raise WamAudioError(
            "unknown drum piece %r (have: %s and `.`)"
            % (tok, " ".join(sorted(DRUM_PIECES))), line_no, line)
    ev["kind"] = "drum"
    ev["piece"] = DRUM_PIECES[tok]
    return ev


# ------------------------------------------------------------- block reader


class _Lines:
    """Indentation-aware cursor over the source."""

    def __init__(self, text):
        self.items = []
        for i, raw in enumerate(text.splitlines(), 1):
            line = raw.split("#", 1)[0].rstrip()
            if not line.strip():
                continue
            indent = len(line) - len(line.lstrip(" "))
            if indent % 2:
                raise WamAudioError("indent must be a multiple of 2 spaces", i, raw)
            self.items.append((indent // 2, line.strip(), i, raw))
        self.pos = 0

    def peek(self):
        return self.items[self.pos] if self.pos < len(self.items) else None

    def next(self):
        item = self.items[self.pos]
        self.pos += 1
        return item

    def children(self, level):
        """Yield every following line deeper than `level`, at any depth."""
        while True:
            item = self.peek()
            if item is None or item[0] <= level:
                return
            yield self.next()


# ------------------------------------------------------------------- parser


def parse(text):
    lines = _Lines(text)
    doc = {"name": None, "rate": 44100, "master": -1.0, "key": None,
           "tempo": None, "tones": {}, "instruments": {}, "songs": [],
           "sounds": [], "dir": os.getcwd()}
    while lines.peek() is not None:
        level, line, no, raw = lines.next()
        if level != 0:
            raise WamAudioError("expected a section at column 0", no, raw)
        head = line.split()
        word = head[0]
        if word == "audio":
            _parse_header(doc, head, lines, no, raw)
        elif word == "tones":
            _parse_tones(doc, lines, level)
        elif word == "instruments":
            _parse_instruments(doc, lines, level)
        elif word == "song":
            doc["songs"].append(_parse_song(doc, head, lines, level, no, raw))
        elif word == "sound":
            doc["sounds"].append(_parse_sound(doc, head, lines, level, no, raw))
        else:
            raise WamAudioError(
                "unknown section %r (audio, tones, instruments, song, sound)" % word,
                no, raw)
    if doc["name"] is None:
        raise WamAudioError("file needs an `audio <name>` header")
    if not doc["songs"] and not doc["sounds"]:
        raise WamAudioError("file declares no song and no sound")
    return doc


def _parse_header(doc, head, lines, no, raw):
    if len(head) < 2:
        raise WamAudioError("audio needs a name", no, raw)
    doc["name"] = head[1]
    for level, line, ln, lraw in lines.children(0):
        tok = line.split()
        key = tok[0]
        if key == "rate":
            doc["rate"] = int(_num(tok[1], ln, lraw, "a sample rate"))
            if doc["rate"] not in (22050, 32000, 44100, 48000):
                raise WamAudioError("rate must be 22050, 32000, 44100 or 48000", ln, lraw)
        elif key == "key":
            doc["key"] = parse_key(tok[1:], ln, lraw)
        elif key == "tempo":
            doc["tempo"] = parse_tempo(tok[1], ln, lraw)
        elif key == "master":
            doc["master"] = _num(tok[1], ln, lraw, "a dBFS target")
        else:
            raise WamAudioError("unknown header field %r" % key, ln, lraw)


def _parse_tones(doc, lines, level):
    """`tone <name> [from=<other>] [tilt=..] [harm=..] [noise=..] [odd=on] ...`

    A name with no `from=` and no matching built-in starts from `plain`;
    reusing a built-in name overrides that built-in for this file only.
    """
    for lv, line, ln, raw in lines.children(level):
        tok = line.split()
        if tok[0] != "tone":
            raise WamAudioError("expected `tone <name> ...` inside tones", ln, raw)
        if len(tok) < 2:
            raise WamAudioError("tone needs a name", ln, raw)
        name = tok[1]
        pos, kv = _split_kv(tok[2:])
        if pos:
            raise WamAudioError("tone takes name=value fields, got %r" % pos[0], ln, raw)
        _check_keys(kv, tuple(TONE_FIELDS) + ("from",), ln, raw, "tone")
        base = kv.get("from", name)
        if name in doc["tones"]:
            raise WamAudioError("tone %r declared twice" % name, ln, raw)
        fields = parse_tone_overrides(kv, ln, raw)
        if not fields and "from" not in kv:
            raise WamAudioError("tone %r sets nothing; give it a field or a from="
                                % name, ln, raw)
        doc["tones"][name] = {"name": name, "base": base, "fields": fields, "line": ln}


def _parse_instruments(doc, lines, level):
    for lv, line, ln, raw in lines.children(level):
        tok = line.split()
        if tok[0] != "instrument":
            raise WamAudioError("expected `instrument <name> ...` inside instruments",
                                ln, raw)
        if len(tok) < 2:
            raise WamAudioError("instrument needs a name", ln, raw)
        name = tok[1]
        pos, kv = _split_kv(tok[2:])
        _check_keys(kv, VOICE_KEYS, ln, raw, "instrument")
        if pos:
            raise WamAudioError("instrument takes name=value options, got %r"
                                % pos[0], ln, raw)
        if "source" not in kv:
            raise WamAudioError("instrument %r needs a source=" % name, ln, raw)
        v = {"name": name, "source": kv["source"], "tone": kv.get("tone", "plain"),
             "env": kv.get("env", "pluck"), "level": 1.0, "octave": 0,
             "detune": 0.0, "space": kv.get("space", "none"),
             "echo": kv.get("echo", "none"), "drive": kv.get("drive", "none"),
             "pan": kv.get("pan", "center"), "damp": 0.5,
             "glide": kv.get("glide", "none"), "cut": kv.get("cut"),
             "bank": kv.get("bank"), "file": kv.get("file"),
             "root": kv.get("root"),
             "tone_fields": parse_tone_overrides(kv, ln, raw), "line": ln}
        for k, caster in (("level", float), ("octave", int),
                          ("detune", float), ("damp", float)):
            if k in kv:
                v[k] = _num(kv[k], ln, raw) if k != "octave" else int(kv[k])
        if name in doc["instruments"]:
            raise WamAudioError("instrument %r declared twice" % name, ln, raw)
        doc["instruments"][name] = v


STAFF_KEYS = ("instrument", "level", "octave", "pan", "space", "echo",
              "humanize", "curve", "cut")
STAFF_FLAGS = ("mute", "solo")

# Timing, loudness and tuning jitter, in that order: seconds, a fraction, and
# cents. A player is never exactly on the grid, never exactly as loud twice,
# and never exactly in tune -- a sequencer is all three, which is most of what
# makes a rendered part sound like a machine reading a list.
#
# The pitch column matters more than its size suggests. Perfect tuning is the
# loudest remaining tell that something was computed: real players and real
# strings drift a few cents against each other, and it is that drift beating
# between two sustained notes that the ear hears as "acoustic". A few cents is
# inaudible as pitch and obvious as texture.
HUMANIZE = {"off": (0.0, 0.0, 0.0),
            "light": (0.008, 0.07, 4.0),
            "loose": (0.022, 0.16, 11.0)}

CURVES = ("linear", "ease", "fast", "slow", "snap", "drop")


def _parse_level(text, ln, raw):
    """`level=60%` or `level=35%->90%`.

    A build is the shape of a piece, not a property of one note, so it belongs
    on the fader. The second form ramps across the whole song.
    """
    if "->" not in text:
        return _num(text, ln, raw, "a level"), None
    start, end = text.split("->", 1)
    return _num(start, ln, raw, "a level"), _num(end, ln, raw, "a level")


def _parse_song(doc, head, lines, level, no, raw):
    """A song is staves of voices, and a voice is bars of notation.

    The nesting is the one a score has: a staff is an instrument and a mix
    strip, the voices inside it are independent lines played at the same time
    by that instrument, and each voice is written out in bars.
    """
    if len(head) < 2:
        raise WamAudioError("song needs a name", no, raw)
    song = {"name": head[1], "key": doc["key"], "tempo": doc["tempo"],
            "meter": None, "bars": None, "feel": "straight", "space": "none",
            "staves": [], "checks": [], "loop": True, "line": no}
    staff = voice = phrase = None
    for lv, line, ln, lraw in lines.children(level):
        tok = line.split()
        key = tok[0]
        if lv == level + 1:
            staff = voice = phrase = None
            if key == "key":
                song["key"] = parse_key(tok[1:], ln, lraw)
            elif key == "tempo":
                song["tempo"] = parse_tempo(tok[1], ln, lraw)
            elif key == "meter":
                song["meter"] = wnotation.parse_meter(tok[1], ln, lraw)
            elif key == "bars":
                song["bars"] = int(_num(tok[1], ln, lraw, "a bar count"))
            elif key == "feel":
                if tok[1] not in ("straight", "swing", "shuffle"):
                    raise WamAudioError("feel is straight, swing or shuffle", ln, lraw)
                song["feel"] = tok[1]
            elif key == "space":
                song["space"] = tok[1]
            elif key == "loop":
                song["loop"] = tok[1] not in ("off", "no", "false")
            elif key == "staff":
                staff = _start_staff(song, doc, tok, ln, lraw)
            elif key == "checks":
                continue
            elif key == "assert":
                song["checks"].append(_parse_assert(tok[1:], ln, lraw))
            else:
                raise WamAudioError(
                    "unknown song field %r (key, tempo, meter, bars, feel, "
                    "space, loop, staff, checks)" % key, ln, lraw)
        elif lv == level + 2:
            phrase = None
            if key == "assert":
                song["checks"].append(_parse_assert(tok[1:], ln, lraw))
                continue
            if staff is None:
                raise WamAudioError("%r must sit inside a staff" % key, ln, lraw)
            if key != "voice":
                raise WamAudioError(
                    "a staff holds voices; %r is not one" % key, ln, lraw)
            voice = _start_voice(staff, tok, ln, lraw)
        else:
            if voice is None:
                raise WamAudioError("%r must sit inside a voice" % key, ln, lraw)
            if key == "phrase":
                if len(tok) < 2:
                    raise WamAudioError("phrase needs a name", ln, lraw)
                phrase = tok[1]
                if phrase in voice["phrases"]:
                    raise WamAudioError("phrase %r declared twice in voice %r"
                                        % (phrase, voice["name"]), ln, lraw)
                voice["phrases"][phrase] = []
            elif key == "dynamic":
                # A dynamic between phrases, so a phrase can be reused at
                # different volumes instead of being copied per section.
                if len(tok) < 2 or tok[1] not in wnotation.DYNAMICS:
                    raise WamAudioError(
                        "dynamic needs one of %s"
                        % " ".join(wnotation.DYNAMICS), ln, lraw)
                voice["play"].append({"dynamic": tok[1], "line": ln})
                phrase = None
            elif key == "play":
                for ref in tok[1:]:
                    if ref not in voice["phrases"]:
                        raise WamAudioError(
                            "play names %r, which this voice never declares"
                            % ref, ln, lraw)
                    voice["play"].append({"phrase": ref, "line": ln})
                phrase = None
            elif line.lstrip().startswith("|"):
                target = (voice["phrases"][phrase] if phrase is not None
                          else voice["lines"])
                target.append((line, ln, lraw))
            else:
                raise WamAudioError(
                    "expected a bar starting with `|`, a `phrase`, or a `play`; "
                    "got %r" % key, ln, lraw)
    if song["key"] is None:
        raise WamAudioError("song %r has no key (set one here or in the header)"
                            % song["name"], no, raw)
    if song["tempo"] is None:
        raise WamAudioError("song %r has no tempo" % song["name"], no, raw)
    if song["meter"] is None:
        song["meter"] = wnotation.parse_meter("4/4", no, raw)
    if not song["staves"]:
        raise WamAudioError("song %r has no staves" % song["name"], no, raw)
    _read_notation(song)
    return song


def _start_staff(song, doc, tok, ln, raw):
    """A staff: which instrument plays it, and where it sits in the mix."""
    if len(tok) < 2:
        raise WamAudioError("staff needs a name", ln, raw)
    pos, kv = _split_kv(tok[2:])
    _check_keys(kv, STAFF_KEYS, ln, raw, "staff")
    for flag in pos:
        if flag not in STAFF_FLAGS:
            raise WamAudioError("unknown staff flag %r (have: %s)"
                                % (flag, ", ".join(STAFF_FLAGS)), ln, raw)
    level, level_to = _parse_level(kv.get("level", "100%"), ln, raw)
    # `cut=800` is a fixed lowpass over the whole staff; `cut=400->9000` opens
    # it across the song. A filter opening is how dance music builds -- the
    # parts do not change, the amount of them you can hear does -- and there
    # was no way to say it before.
    cut = cut_to = None
    if "cut" in kv:
        text = kv["cut"]
        if "->" in text:
            a, b = text.split("->", 1)
            cut = parse_freq(a, ln, raw)
            cut_to = parse_freq(b, ln, raw)
        else:
            cut = parse_freq(text, ln, raw)
    staff = {"name": tok[1], "instrument": kv.get("instrument", tok[1]),
             "level": level, "level_to": level_to,
             "curve": kv.get("curve", "ease"),
             "cut": cut, "cut_to": cut_to,
             "octave": int(kv.get("octave", 0)), "pan": kv.get("pan"),
             "space": kv.get("space"), "echo": kv.get("echo"),
             "humanize": kv.get("humanize", "off"),
             "mute": "mute" in pos, "solo": "solo" in pos,
             "voices": [], "line": ln}
    if staff["humanize"] not in HUMANIZE:
        raise WamAudioError("humanize is one of %s" % ", ".join(HUMANIZE), ln, raw)
    if staff["curve"] not in CURVES:
        raise WamAudioError("curve is one of %s" % ", ".join(CURVES), ln, raw)
    if any(st["name"] == staff["name"] for st in song["staves"]):
        raise WamAudioError("staff %r declared twice" % staff["name"], ln, raw)
    # A drum staff reads its tokens as kit pieces rather than pitches. That is
    # true of the built-in `kit` and of any instrument declaring `source=kit`,
    # which is a bank of recordings keyed by piece.
    declared = doc["instruments"].get(staff["instrument"], {})
    staff["drums"] = (staff["instrument"] == "kit"
                      or declared.get("source") == "kit")
    song["staves"].append(staff)
    return staff


def _start_voice(staff, tok, ln, raw):
    if len(tok) < 2:
        raise WamAudioError("voice needs a name", ln, raw)
    pos, kv = _split_kv(tok[2:])
    _check_keys(kv, ("level", "octave", "pan"), ln, raw, "voice")
    voice = {"name": tok[1], "level": _num(kv.get("level", "100%"), ln, raw),
             "octave": int(kv.get("octave", 0)), "pan": kv.get("pan"),
             "lines": [], "phrases": {}, "play": [], "bars": [], "line": ln}
    if any(v["name"] == voice["name"] for v in staff["voices"]):
        raise WamAudioError("voice %r declared twice in staff %r"
                            % (voice["name"], staff["name"]), ln, raw)
    staff["voices"].append(voice)
    return voice


def _read_notation(song):
    """Turn every voice's text into bars of events, once the key is known."""
    for staff in song["staves"]:
        for voice in staff["voices"]:
            if voice["lines"] and voice["phrases"]:
                raise WamAudioError(
                    "voice %r writes bars directly and also declares phrases -- "
                    "pick one, or the order it plays in is anyone's guess"
                    % voice["name"], voice["line"])
            if voice["phrases"]:
                if not voice["play"]:
                    raise WamAudioError(
                        "voice %r declares phrases but never plays any"
                        % voice["name"], voice["line"])
                cut = {name: wnotation.parse_bars(text, song["key"], song["meter"],
                                                  staff["drums"])
                       for name, text in voice["phrases"].items()}
                pending = None
                for item in voice["play"]:
                    if "dynamic" in item:
                        pending = {"kind": "dynamic", "beats": 0.0, "tie": False,
                                   "accent": 1.0,
                                   "gain": wnotation.DYNAMICS[item["dynamic"]]}
                        continue
                    for bar in cut[item["phrase"]]:
                        if pending is not None:
                            bar = [pending] + list(bar)
                            pending = None
                        voice["bars"].append(bar)
            else:
                voice["bars"] = wnotation.parse_bars(
                    voice["lines"], song["key"], song["meter"], staff["drums"])
        if not any(v["bars"] for v in staff["voices"]):
            raise WamAudioError("staff %r has no music in it" % staff["name"],
                                staff["line"])


def _parse_sound(doc, head, lines, level, no, raw):
    if len(head) < 2:
        raise WamAudioError("sound needs a name", no, raw)
    snd = {"name": head[1], "length": {"kind": "abs", "value": 0.35, "text": "short"},
           "space": "none", "echo": "none", "drive": "none", "layers": [],
           "checks": [], "line": no}
    layer = None
    for lv, line, ln, lraw in lines.children(level):
        tok = line.split()
        key = tok[0]
        if lv == level + 1:
            layer = None
            if key == "length":
                snd["length"] = parse_length(tok[1:], ln, lraw)
                if snd["length"]["kind"] == "rel":
                    raise WamAudioError("a sound's own length cannot be a percentage",
                                        ln, lraw)
            elif key in ("space", "echo", "drive"):
                snd[key] = tok[1]
            elif key == "layer":
                layer = _start_layer(snd, tok, ln, lraw)
            elif key == "checks":
                continue
            elif key == "assert":
                snd["checks"].append(_parse_assert(tok[1:], ln, lraw))
            else:
                raise WamAudioError("unknown sound field %r" % key, ln, lraw)
        else:
            if key == "assert":
                snd["checks"].append(_parse_assert(tok[1:], ln, lraw))
                continue
            if layer is None:
                raise WamAudioError("%r must sit inside a layer" % key, ln, lraw)
            _parse_layer_line(layer, tok, ln, lraw)
    if not snd["layers"]:
        raise WamAudioError("sound %r has no layers" % snd["name"], no, raw)
    return snd


def _start_layer(snd, tok, ln, raw):
    if len(tok) < 2:
        raise WamAudioError("layer needs a name", ln, raw)
    pos, kv = _split_kv(tok[2:])
    _check_keys(kv, ("at",), ln, raw, "layer")
    layer = {"name": tok[1], "at": _num(kv.get("at", "0%"), ln, raw),
             "source": None, "tone": "plain", "env": "hit", "level": 1.0,
             "pitch": None, "length": {"kind": "rel", "value": 1.0, "text": "100%"},
             "sweeps": [], "cut": None, "drive": "none", "space": None,
             "repeat": None, "tone_fields": {}, "line": ln}
    snd["layers"].append(layer)
    return layer


def _parse_layer_line(layer, tok, ln, raw):
    key = tok[0]
    if key == "source":
        # `source metal bright harm=1.9` -- archetype, adjective, then any
        # field of that adjective the author wants to move for this layer only.
        pos, kv = _split_kv(tok[1:])
        _check_keys(kv, tuple(TONE_FIELDS), ln, raw, "source")
        layer["source"] = pos[0]
        if len(pos) > 1:
            layer["tone"] = pos[1]
        layer["tone_fields"].update(parse_tone_overrides(kv, ln, raw))
    elif key == "tone":
        pos, kv = _split_kv(tok[1:])
        _check_keys(kv, tuple(TONE_FIELDS), ln, raw, "tone")
        if pos:
            layer["tone"] = pos[0]
        layer["tone_fields"].update(parse_tone_overrides(kv, ln, raw))
    elif key == "env":
        layer["env"] = tok[1]
    elif key == "level":
        layer["level"] = _num(tok[1], ln, raw, "a level")
    elif key == "pitch":
        layer["pitch"] = parse_freq(tok[1], ln, raw)
    elif key == "length":
        layer["length"] = parse_length(tok[1:], ln, raw)
    elif key == "cut":
        # `cut low 1200` / `cut high 300` / `cut band 900 q=6`
        if len(tok) < 3:
            raise WamAudioError("cut needs `<low|high|band> <freq>`", ln, raw)
        if tok[1] not in ("low", "high", "band"):
            raise WamAudioError("cut kind is low, high or band", ln, raw)
        _, kv = _split_kv(tok[3:])
        layer["cut"] = {"kind": tok[1], "freq": parse_freq(tok[2], ln, raw),
                        "q": _num(kv.get("q", "4"), ln, raw)}
    elif key in ("drive", "space"):
        layer[key] = tok[1]
    elif key == "repeat":
        # `repeat 5 every 8%` -- a burst of copies, e.g. footsteps or a rattle.
        pos, kv = _split_kv(tok[1:])
        if len(pos) < 3 or pos[1] != "every":
            raise WamAudioError("repeat needs `<count> every <spacing>`", ln, raw)
        layer["repeat"] = {"count": int(_num(pos[0], ln, raw, "a repeat count")),
                           "every": _num(pos[2], ln, raw, "a spacing"),
                           "decay": _num(kv.get("decay", "80%"), ln, raw)}
    elif key == "sweep":
        layer["sweeps"].append(_parse_sweep(tok[1:], ln, raw))
    else:
        raise WamAudioError(
            "unknown layer line %r (source, tone, env, level, pitch, length, "
            "cut, sweep, drive, space, repeat)" % key, ln, raw)


def _parse_sweep(tok, ln, raw):
    """`sweep pitch high -> low curve=drop` -- named endpoints, named shape."""
    if len(tok) < 4 or "->" not in tok:
        raise WamAudioError("sweep needs `<target> <from> -> <to> [curve=...]`", ln, raw)
    target = tok[0]
    if target not in SWEEP_TARGETS:
        raise WamAudioError("sweep target is one of %s" % ", ".join(SWEEP_TARGETS), ln, raw)
    arrow = tok.index("->")
    rest, kv = _split_kv(tok[arrow + 1:])
    if arrow != 2 or not rest:
        raise WamAudioError("sweep needs exactly one value on each side of ->", ln, raw)
    _check_keys(kv, ("curve",), ln, raw, "sweep")
    curve = kv.get("curve", "linear")
    if curve not in ("linear", "ease", "fast", "slow", "snap", "drop"):
        raise WamAudioError("unknown curve %r" % curve, ln, raw)
    conv = (lambda t: _num(t, ln, raw)) if target == "level" else \
           (lambda t: parse_freq(t, ln, raw))
    return {"target": target, "from": conv(tok[1]), "to": conv(rest[0]), "curve": curve}


_ASSERT_RE = re.compile(r"^(\w+)\s*(<=|>=|==|~|<|>)\s*(-?[\d.]+)\s*(\w*)$")


def _parse_assert(tok, ln, raw):
    text = " ".join(tok)
    m = _ASSERT_RE.match(text)
    if not m:
        raise WamAudioError("assert reads `<metric> <op> <value> [unit]`, got %r" % text,
                            ln, raw)
    return {"metric": m.group(1), "op": m.group(2), "value": float(m.group(3)),
            "unit": m.group(4), "text": text, "line": ln}


def parse_file(path):
    with open(path, "r", encoding="utf-8") as fh:
        doc = parse(fh.read())
    # Sample banks are named relative to the file that asks for them, not to
    # whatever directory the compiler happens to be run from.
    doc["dir"] = os.path.dirname(os.path.abspath(path))
    return doc
