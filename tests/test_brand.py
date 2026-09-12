"""The brand assets are a shipped interface, so hold them to the spec.

WHY THIS IS A SUITE AND NOT A README NOTE. Since core 2026.3 a custom
integration serves its own brand images: `homeassistant/components/brands`
looks for a top-level `brand/` directory (`Integration.has_branding` is
literally `"brand" in self._top_level_files`) and returns those bytes from
`/api/brands/integration/<domain>/<image>` before it ever asks the CDN. HACS's
own `brands` validator does the same, keying on `brand/icon.png` when
`content_in_root` is set. Nothing in either path VALIDATES the file -- a
257x256 icon, an opaque white rectangle, or a JPEG renamed to .png all get
served exactly as committed, and the only place the mistake shows up is in a
user's sidebar.

WHAT IT CHECKS, and why each one is a defect somebody would otherwise ship:

  * the set is exactly what the proxy will look for, and every name is one
    core's ALLOWED_IMAGES accepts -- a typo like `dark-icon.png` is not an
    error anywhere, it is a file that is never read.
  * 1:1 aspect and 256/512 exactly, per the brands image specification.
  * colour type 6 (truecolour+alpha). Alpha is not decoration here: the same
    bytes are composited onto a light card and a dark one.
  * the corners are actually transparent and the mark is TRIMMED to its
    bounding box. This is the one the header cannot see and the one the
    original cut of these files got wrong -- a white-background JPEG converted
    naively keeps its matte, and a matte is a white box drawn around the logo
    on every dark surface.
  * the light and dark variants differ. `dark_icon.png` falling back to a copy
    of `icon.png` is worse than not shipping one: it reports a dark-optimised
    asset that is not.

STDLIB ONLY, like every suite here -- `zlib` plus the PNG spec's own filters,
about forty lines, rather than a Pillow dependency the CI `tests` job
deliberately does not have.
"""

from __future__ import annotations

import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import PKG_DIR, run_suite

BRAND_DIR = os.path.join(PKG_DIR, "brand")

#: Exactly the files this repo ships. `logo*.png` is deliberately absent: the
#: mark is square, so the brands specification says to ship the icon alone and
#: core's own IMAGE_FALLBACKS resolves `logo.png` -> `icon.png` for us. A
#: byte-identical second copy would be weight with no effect.
EXPECTED = {
    "icon.png": 256,
    "icon@2x.png": 512,
    "dark_icon.png": 256,
    "dark_icon@2x.png": 512,
}

#: homeassistant/components/brands/const.py ALLOWED_IMAGES. Anything outside
#: this set is silently a 404 from the proxy, so it must not be shipped.
ALLOWED_IMAGES = frozenset(
    {
        "icon.png",
        "logo.png",
        "icon@2x.png",
        "logo@2x.png",
        "dark_icon.png",
        "dark_logo.png",
        "dark_icon@2x.png",
        "dark_logo@2x.png",
    }
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
COLOUR_TYPE_RGBA = 6


def _chunks(data):
    """Yield (type, payload) over a PNG, validating the signature."""
    if not data.startswith(PNG_MAGIC):
        raise ValueError("not a PNG (bad signature)")
    pos = len(PNG_MAGIC)
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind = data[pos + 4 : pos + 8]
        yield kind, data[pos + 8 : pos + 8 + length]
        pos += 12 + length


def _decode(data):
    """Return (width, height, rows) with rows as flat RGBA bytearrays.

    Handles the subset this repo actually produces: 8-bit truecolour+alpha,
    no interlacing. Anything else raises rather than guessing, because a
    quiet reinterpretation is how a check stops checking.
    """
    header, idat = None, b""
    for kind, payload in _chunks(data):
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", payload)
        elif kind == b"IDAT":
            idat += payload
    if header is None:
        raise ValueError("no IHDR")
    width, height, depth, colour, compression, filt, interlace = header
    if (depth, colour) != (8, COLOUR_TYPE_RGBA):
        raise ValueError(f"expected 8-bit RGBA, got depth={depth} colour_type={colour}")
    if (compression, filt, interlace) != (0, 0, 0):
        raise ValueError("expected uncompressed-filter-0, non-interlaced PNG")

    raw = zlib.decompress(idat)
    stride = width * 4
    rows, prior = [], bytearray(stride)
    pos = 0
    for _ in range(height):
        method = raw[pos]
        line = bytearray(raw[pos + 1 : pos + 1 + stride])
        pos += 1 + stride
        # PNG per-scanline reconstruction filters, spec section 9.2.
        for i in range(stride):
            a = line[i - 4] if i >= 4 else 0
            b = prior[i]
            c = prior[i - 4] if i >= 4 else 0
            if method == 0:
                continue
            if method == 1:
                line[i] = (line[i] + a) & 0xFF
            elif method == 2:
                line[i] = (line[i] + b) & 0xFF
            elif method == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 0xFF
            elif method == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
            else:
                raise ValueError(f"unknown filter {method}")
        rows.append(line)
        prior = line
    return width, height, rows


def _alpha_bbox(width, height, rows):
    """Bounding box of every pixel with any opacity, or None if all clear."""
    top = left = None
    bottom = right = -1
    for y in range(height):
        row = rows[y]
        for x in range(width):
            if row[x * 4 + 3] == 0:
                continue
            if top is None:
                top = y
            bottom = y
            if left is None or x < left:
                left = x
            if x > right:
                right = x
    if top is None:
        return None
    return left, top, right, bottom


def _load(name, source=None):
    with open(os.path.join(BRAND_DIR, name), "rb") as handle:
        return _decode(source if source is not None else handle.read())


def check_the_expected_files_are_present_and_allowed():
    findings = []
    try:
        on_disk = {n for n in os.listdir(BRAND_DIR) if not n.startswith(".")}
    except FileNotFoundError:
        return ["brand/ does not exist -- core will not see this integration as branded"]
    for name in sorted(set(EXPECTED) - on_disk):
        findings.append(f"brand/{name} is missing")
    for name in sorted(on_disk - set(EXPECTED)):
        note = "" if name in ALLOWED_IMAGES else " and is not a name the proxy serves"
        findings.append(f"brand/{name} is shipped but undeclared{note}")
    return findings


def check_every_image_is_square_and_correctly_sized(read=_load):
    findings = []
    for name, expected in sorted(EXPECTED.items()):
        try:
            width, height, _ = read(name)
        except (OSError, ValueError) as err:
            findings.append(f"brand/{name} is unreadable: {err}")
            continue
        if width != height:
            findings.append(f"brand/{name} is {width}x{height}, not 1:1")
        elif width != expected:
            findings.append(f"brand/{name} is {width}px, expected {expected}px")
    return findings


def check_every_image_is_trimmed_and_transparent(read=_load):
    """No matte, and no transparent padding around the mark."""
    findings = []
    for name in sorted(EXPECTED):
        try:
            width, height, rows = read(name)
        except (OSError, ValueError) as err:
            findings.append(f"brand/{name} is unreadable: {err}")
            continue
        bbox = _alpha_bbox(width, height, rows)
        if bbox is None:
            findings.append(f"brand/{name} is fully transparent")
            continue
        left, top, right, bottom = bbox
        # Square-and-centred: the mark fills at least one axis edge to edge.
        if left > 0 and top > 0 and right < width - 1 and bottom < height - 1:
            findings.append(
                f"brand/{name} has transparent padding on every side "
                f"(bbox {bbox} in {width}x{height}) -- it is not trimmed"
            )
        opaque_corners = sum(
            rows[y][x * 4 + 3] != 0
            for y in (0, height - 1)
            for x in (0, width - 1)
        )
        if opaque_corners == 4:
            findings.append(
                f"brand/{name} has four opaque corners -- it still carries its "
                "background matte, which is a box around the logo on dark themes"
            )
    return findings


def check_the_dark_variant_is_actually_different(read=_load):
    """A `dark_` file identical to its light twin is a false claim."""
    findings = []
    for dark, light in (("dark_icon.png", "icon.png"), ("dark_icon@2x.png", "icon@2x.png")):
        try:
            _, _, dark_rows = read(dark)
            _, _, light_rows = read(light)
        except (OSError, ValueError) as err:
            findings.append(f"brand/{dark} vs brand/{light}: {err}")
            continue
        if dark_rows == light_rows:
            findings.append(
                f"brand/{dark} is pixel-identical to brand/{light} -- it reports "
                "a dark-optimised asset that is not one"
            )
    return findings


CHECKS = [
    ("the declared brand files are present and servable", check_the_expected_files_are_present_and_allowed),
    ("every brand image is square and correctly sized", check_every_image_is_square_and_correctly_sized),
    ("every brand image is trimmed and has transparency", check_every_image_is_trimmed_and_transparent),
    ("the dark variant differs from the light one", check_the_dark_variant_is_actually_different),
]

CASES = [(name, lambda check=check: {"findings": check()}, {"findings": []}) for name, check in CHECKS]


def _synthetic(width, height, pixel, colour_type=COLOUR_TYPE_RGBA):
    """Build a PNG in memory so the self-test can inject a real defect."""
    raw = b"".join(
        # Filter byte 0 (None) per scanline, then the raw RGBA samples.
        b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(width))
        for y in range(height)
    )
    def chunk(kind, payload):
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )
    return (
        PNG_MAGIC
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, colour_type, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _reader(**overrides):
    """A `read` that substitutes synthetic bytes for the named files."""
    def read(name):
        if name in overrides:
            return _decode(overrides[name])
        return _load(name)
    return read


_OPAQUE_WHITE = _synthetic(256, 256, lambda x, y: (255, 255, 255, 255))
_WRONG_SIZE = _synthetic(255, 256, lambda x, y: (0, 0, 0, 255))
_PADDED = _synthetic(
    256, 256, lambda x, y: (0, 0, 0, 255 if 40 < x < 200 and 40 < y < 200 else 0)
)

# Each mutation is a real defect of the class its check exists to catch, and
# each must be FOUND. A brand suite that cannot fail would pass just as
# happily on an empty directory.
FAIL_CASES = [
    (
        "self-test: a non-square icon must be found",
        lambda: {"findings": check_every_image_is_square_and_correctly_sized(_reader(**{"icon.png": _WRONG_SIZE}))},
        {"findings": []},
    ),
    (
        "self-test: an untrimmed, opaque-matte icon must be found",
        lambda: {"findings": check_every_image_is_trimmed_and_transparent(_reader(**{"icon.png": _OPAQUE_WHITE}))},
        {"findings": []},
    ),
    (
        "self-test: transparent padding on every side must be found",
        lambda: {"findings": check_every_image_is_trimmed_and_transparent(_reader(**{"icon.png": _PADDED}))},
        {"findings": []},
    ),
    (
        "self-test: a dark variant copied from the light one must be found",
        lambda: {
            "findings": check_the_dark_variant_is_actually_different(
                _reader(**{"dark_icon.png": open(os.path.join(BRAND_DIR, "icon.png"), "rb").read()})
            )
        },
        {"findings": []},
    ),
]


def main():
    return run_suite(CASES, FAIL_CASES)


if __name__ == "__main__":
    sys.exit(main())
