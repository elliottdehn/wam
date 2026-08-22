"""Music notation for `.wama` songs: pitches, durations, chords, bars.

The score layer is written the way sheet music is written, in a spelling close
enough to LilyPond that an author already knows it: `d'4` is the D above
middle C for a quarter note, `<d' f' a'>2` is a chord for a half, `r4` is a
quarter rest, and `|` starts a bar. That is a deliberate choice about who
authors these files -- notation is a standard with centuries of use and an
enormous written corpus, so an author writing it is drawing on knowledge they
already have instead of inventing a private encoding.

Three properties of real notation are worth having and are implemented here:

* **Bars are checked.** Every bar must add up to the meter. A bar that does
  not is an error naming what it got and what it needed, which catches the
  single most common transcription slip before anything is rendered -- and
  the author of a WAM file cannot hear the result, so a rhythm that silently
  drifts a beat would otherwise go unnoticed until someone listened.
* **The key signature applies.** In `key d minor`, a written `b` is B flat,
  because that is what it means on a staff. An explicit accidental overrides
  it and, as in reading practice, holds for the rest of the bar in that
  octave.
* **Durations are sticky.** `c'8 d' e' f'` is four eighths, as in LilyPond.
  Bar checking is what makes that safe: forget a duration change and the bar
  stops adding up.
"""
import re



class WamAudioError(Exception):
    """Every failure in the audio language, from either layer."""

    def __init__(self, msg, line_no=None, line=None):
        self.line_no = line_no
        self.line = line
        loc = " (line %d: %r)" % (line_no, line) if line_no else ""
        super().__init__(msg + loc)


MODES = {
    "major":    (0, 2, 4, 5, 7, 9, 11),
    "minor":    (0, 2, 3, 5, 7, 8, 10),
    "dorian":   (0, 2, 3, 5, 7, 9, 10),
    "phrygian": (0, 1, 3, 5, 7, 8, 10),
    "lydian":   (0, 2, 4, 6, 7, 9, 11),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "harmonic": (0, 2, 3, 5, 7, 8, 11),
    "pentatonic": (0, 3, 5, 7, 10),
    "chromatic": tuple(range(12)),
}

NOTE_PC = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}


# Letter -> semitone above C, and back. Notation is spelled in letters, so
# every alteration is measured against the letter's natural pitch.
LETTERS = "cdefgab"
LETTER_PC = dict(NOTE_PC)

# LilyPond absolute octaves: `c` is C3, `c'` is middle C, `c,` is C2.
MIDDLE_C = 60
BASE_OCTAVE_MIDI = MIDDLE_C - 12          # the MIDI note of a bare `c`

# Note values. The number is the denominator: 4 is a quarter, 8 an eighth.
NOTE_VALUES = (1, 2, 4, 8, 16, 32, 64)

ACCIDENTAL_SUFFIX = {
    "is": 1, "isis": 2,           # LilyPond: cis, cisis
    "es": -1, "eses": -2,         # bes, beses
    "s": -1,                      # as/es shorthand for aes/ees
    "#": 1, "##": 2, "x": 2,
    "b": -1, "bb": -2,
    "!": 0,                       # explicit natural
}

# Drum pieces, in the same shape as pitches: `k8` is a kick eighth. `r` is a
# rest here as it is everywhere else in notation, so the ride is `R`.
DRUM_PIECES = {"k": "kick", "s": "snare", "h": "hat", "H": "openhat",
               "t": "tom", "T": "lowtom", "c": "crash", "R": "ride"}

# Dynamics, as gains. A score marks how loud to play, and without that the
# only way to shape a piece is the mixer -- which applies to a whole staff for
# a whole song and cannot say "this section is quiet and the next one is not".
# Written as LilyPond writes them: `\\p`, `\\mf`, `\\ff`.
DYNAMICS = {"ppp": 0.13, "pp": 0.22, "p": 0.36, "mp": 0.55,
            "mf": 0.72, "f": 1.0, "ff": 1.25, "fff": 1.5}

# Lead-sheet chord qualities, as intervals above the root.
CHORD_QUALITIES = {
    "": (0, 4, 7), "maj": (0, 4, 7), "M": (0, 4, 7),
    "m": (0, 3, 7), "min": (0, 3, 7), "-": (0, 3, 7),
    "7": (0, 4, 7, 10), "maj7": (0, 4, 7, 11), "M7": (0, 4, 7, 11),
    "m7": (0, 3, 7, 10), "min7": (0, 3, 7, 10),
    "dim": (0, 3, 6), "dim7": (0, 3, 6, 9), "o": (0, 3, 6),
    "aug": (0, 4, 8), "+": (0, 4, 8),
    "sus2": (0, 2, 7), "sus4": (0, 5, 7), "sus": (0, 5, 7),
    "6": (0, 4, 7, 9), "m6": (0, 3, 7, 9),
    "9": (0, 4, 7, 10, 14), "add9": (0, 4, 7, 14),
}


# ------------------------------------------------------------ key signature


def key_signature(key, line_no=None, line=None):
    """Which letters the key alters, derived from the mode rather than a table.

    Spelling a scale is the same operation for every diatonic mode: walk the
    letters up from the tonic, and whatever alteration each one needs to land
    on the next scale pitch *is* the key signature. Doing it this way means
    dorian and mixolydian get correct signatures for free, instead of only the
    two modes a hard-coded table would have covered.
    """
    intervals = MODES[key["mode"]]
    if len(intervals) != 7:
        # Pentatonic and chromatic do not spell as seven letters; nothing is
        # altered by default and the author writes accidentals as needed.
        return {}
    letter = key.get("letter")
    if letter is None:
        return {}
    start = LETTERS.index(letter)
    sig = {}
    for step, semis in enumerate(intervals):
        name = LETTERS[(start + step) % 7]
        want = (key["root"] + semis) % 12
        alter = (want - LETTER_PC[name]) % 12
        if alter > 6:
            alter -= 12
        if alter:
            sig[name] = alter
    return sig


# ------------------------------------------------------------------ pitches


_PITCH = re.compile(r"^([a-g])(isis|eses|is|es|s|##|#|bb|b|x|!)?((?:'|,)*)(!?)$")


def parse_pitch(tok, line_no=None, line=None):
    """`bes'` / `fis,,` / `c` -> (letter, explicit alteration or None, octave).

    Octave is in LilyPond's absolute convention: no mark is the octave below
    middle C, each `'` goes up one and each `,` down one.
    """
    m = _PITCH.match(tok)
    if not m:
        raise WamAudioError(
            "expected a pitch like c' or bes or fis,, got %r" % tok, line_no, line)
    letter, acc, marks = m.group(1), m.group(2), m.group(3)
    if m.group(4):                     # `f'!` as well as `f!'`
        acc = "!"
    octave = marks.count("'") - marks.count(",")
    alter = None if acc is None else ACCIDENTAL_SUFFIX[acc]
    return letter, alter, octave


def pitch_to_midi(letter, alter, octave):
    return BASE_OCTAVE_MIDI + LETTER_PC[letter] + alter + 12 * octave


# ---------------------------------------------------------------- durations


_DURATION = re.compile(r"^(\d+)(\.*)$")


def parse_duration(tok, meter_beats_per_whole=4.0, line_no=None, line=None):
    """`4` -> 1 beat, `4.` -> 1.5, `8` -> 0.5. Returns beats."""
    m = _DURATION.match(tok)
    if not m:
        raise WamAudioError("expected a note value like 4, 8 or 4., got %r" % tok,
                            line_no, line)
    value = int(m.group(1))
    if value not in NOTE_VALUES:
        raise WamAudioError(
            "note value %d is not one of %s"
            % (value, ", ".join(str(v) for v in NOTE_VALUES)), line_no, line)
    beats = meter_beats_per_whole / value
    dots = len(m.group(2))
    return beats * (2.0 - 0.5 ** dots)


def duration_name(beats, meter_beats_per_whole=4.0):
    """Beats -> the closest note value, for error messages that read musically."""
    for value in NOTE_VALUES:
        for dots, factor in ((0, 1.0), (1, 1.5), (2, 1.75)):
            if abs(meter_beats_per_whole / value * factor - beats) < 1e-6:
                return "%d%s" % (value, "." * dots)
    return "%g beats" % beats


# ------------------------------------------------------------ chord symbols


_CHORD_SYMBOL = re.compile(r"^([A-G])([#b]?)(.*)$")


def parse_chord_symbol(tok, line_no=None, line=None):
    """`Dm`, `A7`, `Bbmaj7` -> pitch classes above a root, or None.

    Uppercase is what distinguishes a chord symbol from a pitch: `d` is a
    note, `D` is a chord. That is the lead-sheet convention and it keeps the
    two unambiguous inside the same angle brackets.
    """
    m = _CHORD_SYMBOL.match(tok)
    if not m:
        return None
    root = LETTER_PC[m.group(1).lower()] + {"#": 1, "b": -1, "": 0}[m.group(2)]
    quality = m.group(3)
    if quality not in CHORD_QUALITIES:
        raise WamAudioError(
            "unknown chord quality %r in %r (have: %s)"
            % (quality, tok, ", ".join(sorted(q for q in CHORD_QUALITIES if q))),
            line_no, line)
    return root, CHORD_QUALITIES[quality]


# ------------------------------------------------------------------- voices


class BarState:
    """The reading state a musician carries: last duration, accidentals so far.

    Accidentals reset at every bar line, which is why this is rebuilt per bar.
    The last duration and the last chord carry across bars, because a player
    reading `q q q` in bar two is still repeating the chord from bar one.
    """

    def __init__(self, signature, last_duration=None, last_pitches=None):
        self.signature = signature
        self.accidentals = {}
        self.last_duration = last_duration
        self.last_pitches = last_pitches

    def alteration(self, letter, octave, explicit):
        """What this letter sounds as, given the signature and the bar so far."""
        if explicit is not None:
            # An accidental holds for the rest of the bar, in its own octave --
            # reading practice, and the reason `b!` then `b` are both natural.
            self.accidentals[(letter, octave)] = explicit
            return explicit
        if (letter, octave) in self.accidentals:
            return self.accidentals[(letter, octave)]
        return self.signature.get(letter, 0)


def split_tokens(text):
    """Split a bar into tokens, keeping `<...>` chords in one piece."""
    out, buf, depth = [], "", 0
    for ch in text:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
            if depth < 0:
                return None
        if ch.isspace() and depth == 0:
            if buf:
                out.append(buf)
                buf = ""
            continue
        buf += ch
    if depth != 0:
        return None
    if buf:
        out.append(buf)
    return out


_TRAILING = re.compile(r"([~!?]*)$")


def parse_event(tok, state, drums, line_no, line, whole=4.0):
    """One token -> an event dict, advancing the reading state."""
    ev = {"tie": False, "accent": 1.0}
    body = tok
    trail = _TRAILING.search(body).group(1)
    if trail:
        body = body[:len(body) - len(trail)]
    for ch in trail:
        if ch == "~":
            ev["tie"] = True
        elif ch == "!":
            ev["accent"] *= 1.35
        elif ch == "?":
            ev["accent"] *= 0.7

    if body.startswith("\\"):
        # A dynamic mark takes no time, so it does not disturb the bar sum --
        # it just changes how loud everything after it is played.
        name = body[1:]
        if name not in DYNAMICS:
            raise WamAudioError(
                "unknown dynamic %r (have: %s)"
                % (tok, " ".join("\\" + d for d in DYNAMICS)), line_no, line)
        return {"kind": "dynamic", "gain": DYNAMICS[name], "beats": 0.0,
                "tie": False, "accent": 1.0}

    if body.startswith("<"):
        if not body[1:].count(">"):
            raise WamAudioError("unclosed chord %r" % tok, line_no, line)
        inner, _, dur = body[1:].partition(">")
        ev.update(_chord(inner, state, line_no, line))
    else:
        m = re.match(r"^([^0-9]*)(\d*\.*)$", body)
        head, dur = (m.group(1), m.group(2)) if m else (body, "")
        if head == "r":
            ev["kind"] = "rest"
        elif head == "q" and not drums:
            if not state.last_pitches:
                raise WamAudioError(
                    "`q` repeats the chord before it, and there is none yet",
                    line_no, line)
            ev["kind"] = "chord" if len(state.last_pitches) > 1 else "note"
            ev["midis"] = list(state.last_pitches)
        elif drums:
            if head not in DRUM_PIECES:
                raise WamAudioError(
                    "unknown drum piece %r (have: %s, and r for a rest)"
                    % (head, " ".join(sorted(DRUM_PIECES))), line_no, line)
            ev["kind"] = "drum"
            ev["piece"] = DRUM_PIECES[head]
        else:
            letter, alter, octave = parse_pitch(head, line_no, line)
            ev["kind"] = "note"
            ev["midis"] = [pitch_to_midi(letter,
                                         state.alteration(letter, octave, alter),
                                         octave)]

    if ev.get("midis"):
        state.last_pitches = list(ev["midis"])
    if dur:
        ev["beats"] = parse_duration(dur, whole, line_no, line)
        state.last_duration = ev["beats"]
    elif state.last_duration is not None:
        ev["beats"] = state.last_duration
    else:
        raise WamAudioError(
            "%r has no duration and nothing before it to inherit one from -- "
            "the first note of a voice has to say its own note value"
            % tok, line_no, line)
    return ev


def _chord(inner, state, line_no, line):
    """`<c' e' g'>` or `<Dm>` -> the notes it sounds."""
    parts = inner.split()
    if len(parts) == 1 and parts[0][:1].isupper():
        parsed = parse_chord_symbol(parts[0], line_no, line)
        if parsed is None:
            raise WamAudioError("unreadable chord %r" % inner, line_no, line)
        root, intervals = parsed
        # Voice it from the octave below middle C upward, which puts a plain
        # triad where a keyboard player's left hand would take it.
        base = BASE_OCTAVE_MIDI + root
        return {"kind": "chord", "midis": [base + i for i in intervals],
                "symbol": parts[0]}
    if not parts:
        raise WamAudioError("empty chord", line_no, line)
    midis = []
    for part in parts:
        letter, alter, octave = parse_pitch(part, line_no, line)
        midis.append(pitch_to_midi(letter, state.alteration(letter, octave, alter),
                                   octave))
    return {"kind": "chord", "midis": midis}


def parse_bars(lines, key, meter, drums=False, whole=4.0):
    """Parse `| ... | ...` lines into bars of events, checking each bar's length.

    `lines` is a list of (text, line_no, raw). Returns a list of bars, each a
    list of events. Ties are recorded on the events; the compiler merges them,
    because a tie can cross a bar line and bars are checked before that.
    """
    signature = key_signature(key)
    bar_beats = meter["beats"]
    bars = []
    last_duration = None
    last_pitches = None
    for text, line_no, raw in lines:
        chunks = [c.strip() for c in text.split("|")]
        for chunk in chunks:
            if not chunk:
                continue
            toks = split_tokens(chunk)
            if toks is None:
                raise WamAudioError("unbalanced < > in a bar", line_no, raw)
            state = BarState(signature, last_duration, last_pitches)
            events = [parse_event(t, state, drums, line_no, raw, whole)
                      for t in toks]
            last_duration = state.last_duration
            last_pitches = state.last_pitches
            total = sum(e["beats"] for e in events)
            if abs(total - bar_beats) > 1e-6:
                raise WamAudioError(
                    "this bar is %s, but %s is %g beats -- the bar holds %s and "
                    "needs %s"
                    % (duration_name(total, whole) if total < bar_beats * 4
                       else "%g beats" % total,
                       meter["text"], bar_beats,
                       "%g beats" % total, "%g" % bar_beats),
                    line_no, raw)
            bars.append(events)
    return bars


def parse_meter(tok, line_no=None, line=None):
    """`4/4`, `3/4`, `6/8` -> beats per bar and the beat unit."""
    m = re.match(r"^(\d+)/(\d+)$", tok.strip())
    if not m:
        raise WamAudioError("meter reads like 4/4 or 6/8, got %r" % tok,
                            line_no, line)
    count, unit = int(m.group(1)), int(m.group(2))
    if unit not in NOTE_VALUES:
        raise WamAudioError("meter denominator %d is not a note value" % unit,
                            line_no, line)
    # Beats are quarter notes throughout the compiler, so tempo always means
    # the same thing regardless of how the bar is written.
    return {"count": count, "unit": unit, "beats": count * (4.0 / unit),
            "text": tok.strip()}
