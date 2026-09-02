"""Image codec + pixel operations (PROJECT.md §7) — now a thin Pillow wrapper.

The hand-rolled ``zlib``/``struct`` PNG codec, Paeth predictor, and BMP reader
are gone. Decoding and encoding are delegated to :mod:`PIL` (``Image.open`` /
``Image.save``); the two *algorithmic* pixel ops (Otsu and the 3×3 majority
filter) are kept verbatim because Pillow has no equivalent (its
``MedianFilter`` uses a plain 3×3 median, which disagrees with the
center-wins-tie + out-of-bounds-as-0 rule this app pins in its tests), and
``to_gray`` keeps the exact BT.601 formula the tests pin.

Public API (unchanged signatures / return shapes):

* :func:`decode_image`   — PNG/BMP bytes → ``(w, h, rows)`` of ``(r,g,b,a)``.
* :func:`encode_png`     — ``(r,g,b[,a])`` rows → 8-bit RGB PNG bytes.
* :func:`to_gray`        — RGBA rows → luma rows (BT.601).
* :func:`resize_nearest` — nearest-neighbor resize.
* :func:`otsu_threshold` — global Otsu (maximal inter-class variance).
* :func:`median3x3`      — 3×3 majority (center wins ties, OOB = 0).

Malformed input raises :class:`ValueError`, matching the old stdlib decoder:
Pillow's ``OSError``/``UnidentifiedImageError``/``zlib.error`` are translated
to :class:`ValueError`; non-PNG/BMP formats, interlaced PNGs, and BMP bit
depths other than 24/32 are rejected.
"""

from __future__ import annotations

import io
import struct

from PIL import Image


# ---------------------------------------------------------------------------
# Decode / encode
# ---------------------------------------------------------------------------


def _bmp_bit_count(data: bytes) -> int:
    """Read the bits-per-pixel field from a BMP info header.

    BITMAPCOREHEADER (size 12) → offset 22; BITMAPINFOHEADER/V4/V5 (>= 40) →
    offset 28.
    """
    if len(data) < 18:
        raise ValueError("BMP truncated: missing header")
    (hdr_size,) = struct.unpack_from("<I", data, 14)
    if hdr_size == 12:
        (bits,) = struct.unpack_from("<H", data, 22)
    elif hdr_size >= 40:
        (bits,) = struct.unpack_from("<H", data, 28)
    else:
        raise ValueError(f"BMP unsupported header length {hdr_size}")
    return bits


def decode_image(data: bytes) -> tuple[int, int, list[list[tuple[int, int, int, int]]]]:
    """Decode an image (PNG or BMP) into a uniform 8-bit RGBA buffer.

    Returns ``(width, height, rows)`` where ``rows`` is a list of ``height``
    rows, each a list of ``width`` ``(r, g, b, a)`` tuples (alpha defaults to
    255 for opaque images). Raises :class:`ValueError` for unsupported or
    malformed input: empty data, non-PNG/BMP signatures, interlaced PNGs,
    BMP bit depths other than 24/32, and any corrupted/truncated stream.
    """
    if not data:
        raise ValueError("empty image data")
    try:
        im = Image.open(io.BytesIO(data))
        fmt = im.format
        if fmt not in ("PNG", "BMP"):
            raise ValueError("not a supported image: expected PNG or BMP signature")
        if fmt == "PNG" and im.info.get("interlace"):
            raise ValueError("PNG interlace != 0 is not supported")
        if fmt == "BMP":
            bits = _bmp_bit_count(data)
            if bits not in (24, 32):
                raise ValueError(f"BMP bit depth {bits} not supported (want 24 or 32)")
        im.load()
        width, height = im.size
        buf = im.convert("RGBA").tobytes()
        rows: list[list[tuple[int, int, int, int]]] = []
        for y in range(height):
            base = (y * width) * 4
            rows.append(
                [
                    (buf[base + x * 4], buf[base + x * 4 + 1],
                     buf[base + x * 4 + 2], buf[base + x * 4 + 3])
                    for x in range(width)
                ]
            )
        return width, height, rows
    except ValueError:
        raise
    except Exception as exc:  # OSError / UnidentifiedImageError / zlib.error ...
        raise ValueError(f"cannot decode image: {exc}") from exc


def encode_png(
    width: int,
    height: int,
    rows: list[tuple[int, int, int] | tuple[int, int, int, int]],
) -> bytes:
    """Encode 8-bit RGB pixels as a PNG (alpha dropped → opaque RGB).

    ``rows`` must have ``height`` rows of ``width`` pixels; each pixel is
    ``(r, g, b)`` or ``(r, g, b, a)``. Raises :class:`ValueError` for
    non-positive dimensions, a row-count/row-length mismatch, a pixel with
    fewer than 3 channels, or a channel outside 0..255.
    """
    if width <= 0 or height <= 0:
        raise ValueError("PNG dimensions must be positive")
    if len(rows) != height:
        raise ValueError(f"expected {height} rows, got {len(rows)}")
    px: list[tuple[int, int, int]] = []
    for y in range(height):
        row = rows[y]
        if len(row) != width:
            raise ValueError(f"row {y} has {len(row)} pixels, expected {width}")
        for p in row:
            if len(p) < 3:
                raise ValueError(f"bad pixel {p!r} in row {y}")
            r, g, b = int(p[0]), int(p[1]), int(p[2])
            if not (0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255):
                raise ValueError(f"pixel channel out of range in row {y}: {p!r}")
            px.append((r, g, b))
    im = Image.new("RGB", (width, height))
    im.putdata(px)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Pixel operations
# ---------------------------------------------------------------------------


def to_gray(rows: list[list[tuple[int, int, int, int]]]) -> list[list[int]]:
    """Luminance gray: ``round(0.299*r + 0.587*g + 0.114*b)``, values 0..255.

    Kept as the exact BT.601 formula the tests pin (Pillow's ``convert("L")``
    agrees on the pinned values but uses integer floor, risking a 1-LSB drift
    on untested inputs — not worth the risk for a 3-line formula).
    """
    return [
        [min(255, round(0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2])) for p in row]
        for row in rows
    ]


def resize_nearest(
    gray: list[list[int]], cols: int, rows: int
) -> list[list[int]]:
    """Nearest-neighbor resize (Pillow ``Resampling.NEAREST``)."""
    if cols <= 0 or rows <= 0:
        raise ValueError("resize target must be positive")
    if not gray or not gray[0]:
        raise ValueError("cannot resize an empty image")
    sw, sh = len(gray[0]), len(gray)
    im = Image.new("L", (sw, sh))
    im.putdata([v for row in gray for v in row])
    out = im.resize((cols, rows), Image.Resampling.NEAREST).tobytes()
    return [list(out[y * cols:(y + 1) * cols]) for y in range(rows)]


def otsu_threshold(gray: list[list[int]]) -> int:
    """Otsu's threshold over the 0..255 histogram (maximizes inter-class
    variance). Returns the **class boundary** t in [0, 255]: pixels with
    ``value >= t`` form the "high" class, ``value < t`` the "low" class —
    so a bimodal image with modes at 0 and 200 yields t = 100 (the middle
    of the plateau of maximal-variance boundaries between the modes). A
    flat (single-value) image yields 127.

    Kept verbatim from the stdlib implementation: this is a domain algorithm
    (Pillow has no Otsu), and the plateau-midpoint behaviour is pinned by the
    detection tests.
    """
    hist = [0] * 256
    n = 0
    for row in gray:
        for v in row:
            if not (0 <= v <= 255):
                raise ValueError(f"gray value out of range: {v}")
            hist[v] += 1
            n += 1
    if n == 0:
        return 127
    total1 = 0
    for i, c in enumerate(hist):
        total1 += c * i
    best_var = -1.0
    best_lo = best_hi = 127  # extent of the maximal-variance plateau
    w0 = 0
    sum0 = 0.0
    for t in range(256):
        # Invariant at loop top: w0/sum0 describe values < t.
        w1 = n - w0  # values >= t
        if w0 > 0 and w1 > 0:
            mean0 = sum0 / w0
            mean1 = (total1 - sum0) / w1
            var = w0 * w1 * (mean0 - mean1) ** 2
            if var > best_var + 1e-9:
                best_var, best_lo, best_hi = var, t, t
            elif best_var >= 0.0 and abs(var - best_var) <= 1e-9:
                best_hi = t  # plateau extends (contiguous)
        w0 += hist[t]
        sum0 += t * hist[t]
    if best_var < 0:
        return 127  # flat image — a single class exists
    # Maximal-variance plateaus are contiguous; the middle of the plateau
    # splits the two modes symmetrically (e.g. modes 0/200 → 100).
    return (best_lo + best_hi) // 2


def median3x3(b: list[list[int]]) -> list[list[int]]:
    """3×3 majority filter on a 0/1 grid. The center pixel wins ties;
    out-of-bounds neighbours count as 0 (floor).

    Kept verbatim: Pillow's ``ImageFilter.MedianFilter(3)`` is a plain 3×3
    median (1 iff ≥5 ones) and does NOT reproduce the center-wins-tie rule
    (1 iff ones ≥4, or ones == 3 with center 1), which the tests pin.
    """
    h = len(b)
    if h == 0:
        return []
    w = len(b[0])
    out: list[list[int]] = []
    for y in range(h):
        out_row: list[int] = []
        append = out_row.append
        for x in range(w):
            center = b[y][x]
            ones = 0
            for dy in (-1, 0, 1):
                ny = y + dy
                if ny < 0 or ny >= h:
                    continue
                for dx in (-1, 0, 1):
                    nx = x + dx
                    if 0 <= nx < w and b[ny][nx]:
                        ones += 1
            append(1 if ones >= 4 else (1 if (ones == 3 and center) else 0))
        out.append(out_row)
    return out
