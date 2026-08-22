"""The one module that knows what colour space anything is in.

**Every colour inside WAM is linear.** sRGB exists at exactly two edges: the
hex an author types, and the pixels a viewer looks at. `hex_to_linear` is the
way in, `linear_to_srgb` is the way out, and nothing between them needs to
think about it.

That invariant is worth the refactor it cost, for two reasons.

The first is correctness of the light. Shading multiplies a colour by an
illumination factor, and that product only means anything in linear space --
it is "how much light comes back", which is a physical quantity. Multiplying
sRGB values by a shade factor, as the renderer used to, darkens mid tones far
faster than real light does; it is why flat-lit stylised work drifts muddy the
moment anything curves away from the key.

The second is that it removes a whole class of bug rather than guarding
against it. glTF defines `baseColorFactor`, `emissiveFactor` and `COLOR_0` as
linear, and the exporter used to write sRGB palette values straight into them:
valid files, no complaint from any viewer, every model washed out on import in
Blender, Godot, three.js and Unity. The fix was a conversion at the exporter,
which works but leaves the next linear-consuming exporter free to make the
same mistake. With colours linear all the way through, that exporter writes
the value it already has and is correct by default.

The cost, paid once and visible in the sheet renders: everything shaded now
resolves lighter in the mid tones than it used to, because the mid tones were
previously being crushed by maths done in the wrong space.
"""
import re

import numpy as np


def srgb_to_linear(c):
    """sRGB -> linear. Accepts a scalar or any array shape.

    The exact transfer function, not a `** 2.2` approximation: the linear
    segment below 0.04045 is where near-black colours live -- muzzles, hooves,
    iron -- and the approximation visibly misses them.
    """
    a = np.asarray(c, dtype=np.float64)
    safe = np.clip(a, 0.0, None)
    lin = np.where(safe <= 0.04045, safe / 12.92,
                   ((safe + 0.055) / 1.055) ** 2.4)
    return float(lin) if lin.ndim == 0 else lin


def linear_to_srgb(c):
    """linear -> sRGB. The exact inverse of `srgb_to_linear`.

    Every one of the 256 8-bit channel values survives the round trip, which
    `tests/test_colorspace.py` holds true: what an author writes is what an
    importer gets back.
    """
    a = np.asarray(c, dtype=np.float64)
    safe = np.clip(a, 0.0, None)
    enc = np.where(safe <= 0.0031308, safe * 12.92,
                   1.055 * safe ** (1.0 / 2.4) - 0.055)
    return float(enc) if enc.ndim == 0 else enc


_HEX = re.compile(r"^#([0-9a-fA-F]{6})$")


def parse_hex(tok):
    """`#rrggbb` -> sRGB 0..1, or None if it is not a colour at all.

    Kept separate from the conversion so callers that need the authored value
    itself -- naming an edit material after the colour someone picked, say --
    can have it without reaching for the inverse function.
    """
    m = _HEX.match(tok.strip()) if isinstance(tok, str) else None
    if not m:
        return None
    h = m.group(1)
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def hex_to_linear(tok):
    """`#rrggbb` -> linear 0..1, or None. The way colour enters WAM."""
    srgb = parse_hex(tok)
    return None if srgb is None else tuple(srgb_to_linear(c) for c in srgb)


def linear_to_hex(rgb):
    """linear 0..1 -> `#rrggbb`. For names and messages, not for pixels."""
    enc = linear_to_srgb(np.asarray(rgb, dtype=np.float64)[:3])
    return "#%02x%02x%02x" % tuple(int(round(float(v) * 255.0)) for v in enc)


def encode_image(img):
    """A linear float image -> 8-bit sRGB, ready to be written as a PNG.

    Every PNG WAM writes goes through here, which is what makes "the renderer
    works in linear" true rather than aspirational.
    """
    return (np.clip(linear_to_srgb(np.asarray(img, dtype=np.float64)), 0.0, 1.0)
            * 255.0 + 0.5).astype(np.uint8)
