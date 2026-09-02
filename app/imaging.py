"""Image codec + pixel operations (PROJECT.md §7) — pure standard library.

Stack: ``zlib`` + ``struct`` + ``bytes``. No third-party packages.

Decoding (signature sniffed from the leading bytes):

* **PNG** — non-interlaced, 8/16-bit, color types 0 (gray), 2 (rgb),
  3 (palette), 4 (gray+alpha), 6 (rgba). 16-bit samples keep the high byte.
  Every scanline is unfiltered (filter types 0–4). Result is a uniform
  8-bit RGBA buffer: ``rows[y][x] == (r, g, b, a)`` ints, alpha default 255.
* **BMP** — 24-bit and 32-bit ``BI_RGB`` files, bottom-up rows, padded to
  4-byte boundaries. Result is RGB rows (``a`` fixed to 255).

Encoding: :func:`encode_png` writes an 8-bit RGB (alpha=255) PNG from
``rows[y][x] == (r, g, b[, a])`` — the tests use it to synthesize fixtures
so detection round-trips are exact (no resize ambiguity).

Pixel ops (pure Python): :func:`to_gray` (ITU-R BT.601 luma),
:func:`resize_nearest`, :func:`otsu_threshold`, :func:`median3x3`.

All functions raise :class:`ValueError` with a descriptive message on
malformed input.
"""

from __future__ import annotations

import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
BMP_SIGNATURE = b"BM"

#: (color_type, bits_per_sample) -> bytes per pixel in the *raw* stream.
_PNG_CHANNELS = {
    (0, 8): 1,
    (2, 8): 3,
    (3, 8): 1,
    (4, 8): 2,
    (6, 8): 4,
    (0, 16): 1,
    (2, 16): 3,
    (4, 16): 2,
    (6, 16): 4,
}


def _paeth_predictor(a: int, b: int, c: int) -> int:
    """PNG Paeth predictor (RFC 2083): best guess among left ``a``, up ``b``,
    and up-left ``c`` by minimum absolute difference (ties: a, then b, then c)."""
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def decode_image(data: bytes) -> tuple[int, int, list[list[tuple[int, int, int, int]]]]:
    """Decode an image (PNG or BMP, sniffed by signature).

    Returns ``(width, height, rows)`` where ``rows`` is a list of ``height``
    rows, each a list of ``width`` ``(r, g, b, a)`` 8-bit ints (alpha
    defaults to 255). Raises :class:`ValueError` for unsupported or
    malformed input.
    """
    if not data:
        raise ValueError("empty image data")
    if data.startswith(PNG_SIGNATURE):
        return _decode_png(data)
    if data.startswith(BMP_SIGNATURE):
        return _decode_bmp(data)
    raise ValueError("not a supported image: expected PNG or BMP signature")


# ---------------------------------------------------------------------------
# PNG decode
# ---------------------------------------------------------------------------


def _png_chunk(data: bytes, off: int) -> tuple[str, bytes, int]:
    """Read one chunk at ``off``; return (type, payload, next_offset)."""
    if off + 8 > len(data):
        raise ValueError("PNG truncated: cannot read chunk header")
    (length,) = struct.unpack(">I", data[off : off + 4])
    ctype = data[off + 4 : off + 8]
    try:
        name = ctype.decode("ascii")
    except UnicodeDecodeError:
        raise ValueError(f"PNG bad chunk type {ctype!r}") from None
    end = off + 12 + length  # 4 len + 4 type + payload + 4 crc
    if end > len(data):
        raise ValueError(f"PNG chunk {name!r} truncated")
    crc_stored = struct.unpack(">I", data[off + 8 + length : end])[0]
    crc_calc = zlib.crc32(ctype + data[off + 8 : off + 8 + length]) & 0xFFFFFFFF
    if crc_stored != crc_calc:
        raise ValueError(f"PNG chunk {name!r}: CRC mismatch")
    return name, data[off + 8 : off + 8 + length], end


def _decode_png(data: bytes) -> tuple[int, int, list[list[tuple[int, int, int, int]]]]:
    off = len(PNG_SIGNATURE)
    if off >= len(data):
        raise ValueError("PNG truncated: missing chunks")

    width = height = 0
    bit_depth = color_type = compression = filter_method = interlace = None
    palette: list[tuple[int, int, int]] | None = None
    idat: list[bytes] = []
    seen_ihdr = False
    seen_iend = False

    while off < len(data):
        name, payload, off = _png_chunk(data, off)
        if name == "IHDR":
            if seen_ihdr:
                raise ValueError("PNG has duplicate IHDR chunk")
            if len(payload) != 13:
                raise ValueError(f"PNG IHDR must be 13 bytes, got {len(payload)}")
            (width, height, bit_depth, color_type,
             compression, filter_method, interlace) = struct.unpack(">IIBBBBB", payload)
            if not width or not height:
                raise ValueError("PNG IHDR: zero width or height")
            if compression != 0:
                raise ValueError(f"PNG unsupported compression method {compression}")
            if filter_method != 0:
                raise ValueError(f"PNG unsupported filter method {filter_method}")
            if interlace != 0:
                raise ValueError("PNG interlace != 0 is not supported")
            if bit_depth not in (8, 16):
                raise ValueError(f"PNG bit depth {bit_depth} is not supported (want 8 or 16)")
            channels = _PNG_CHANNELS.get((color_type, bit_depth))
            if channels is None:
                raise ValueError(f"PNG unsupported color type {color_type} (bit depth {bit_depth})")
            seen_ihdr = True
        elif name == "PLTE":
            if len(payload) % 3 != 0 or not (1 <= len(payload) // 3 <= 256):
                raise ValueError("PNG PLTE must hold 1..256 RGB triples")
            palette = [
                (payload[i], payload[i + 1], payload[i + 2])
                for i in range(0, len(payload), 3)
            ]
        elif name == "IDAT":
            idat.append(payload)
        elif name == "IEND":
            seen_iend = True
            break
        # ancillary chunks (gAMA, tRNS, tEXt, ...) are ignored by design

    if not seen_ihdr:
        raise ValueError("PNG missing IHDR chunk")
    if not seen_iend:
        raise ValueError("PNG missing IEND chunk")
    if not idat:
        raise ValueError("PNG missing IDAT data")
    if color_type == 3 and palette is None:
        raise ValueError("PNG color type 3 (palette) without a PLTE chunk")
    if color_type == 3 and bit_depth > 8:
        raise ValueError("PNG palette with bit depth > 8 is not supported")

    channels = _PNG_CHANNELS[(color_type, bit_depth)]
    bpp = channels * 2 if bit_depth == 16 else channels
    stride = width * bpp

    raw = zlib.decompress(b"".join(idat))
    expected = height * (stride + 1)  # one filter byte per scanline
    if len(raw) < expected:
        raise ValueError(
            f"PNG decompressed data too short: {len(raw)} < {expected} bytes"
        )
    # (extra trailing bytes are tolerated: some encoders pad)

    out: list[list[tuple[int, int, int, int]]] = []
    prev_row: list[int] | None = None  # integer view of the previous scanline
    for y in range(height):
        row_start = y * (stride + 1)
        ftype = raw[row_start]
        if ftype > 4:
            raise ValueError(f"PNG unknown filter type {ftype} (scanline {y})")
        cur = [0] * stride
        p_off = row_start + 1
        # Reconstruct the scanline one BYTE at a time (b is the byte offset).
        # Every filter reference is taken at the SAME byte offset ``b`` in the
        # current/previous scanline — which for an RGB/RGBA pixel is exactly
        # the per-channel offset (BUG-004: the old code indexed the
        # up/up-left references by the pixel offset ``x`` instead of ``x + c``,
        # corrupting the green/blue channels of RGB/RGBA images).  ``bpp`` is
        # the byte distance to the same channel of the previous pixel.
        bpp = channels * (2 if bit_depth == 16 else 1)
        for b in range(stride):
            raw_b = raw[p_off + b]
            left = cur[b - bpp] if b >= bpp else 0
            up = prev_row[b] if prev_row is not None else 0
            upleft = prev_row[b - bpp] if (prev_row is not None and b >= bpp) else 0
            if ftype == 1:      # Sub
                f = left
            elif ftype == 2:    # Up
                f = up
            elif ftype == 3:    # Average
                f = (left + up) >> 1
            elif ftype == 4:    # Paeth
                paeth = _paeth_predictor(left, up, upleft)
                f = paeth
            else:               # 0: None
                f = 0
            cur[b] = (raw_b + f) & 0xFF

        if bit_depth == 16:
            # Keep the high byte of each 16-bit sample.
            row_bytes = [
                cur[x * 2] for x in range(stride // 2)
            ]
            bytes_per_px = channels
        else:
            row_bytes = cur
            bytes_per_px = channels

        # Expand to uniform 8-bit RGBA.
        row_out: list[tuple[int, int, int, int]] = []
        row_out_append = row_out.append
        for x in range(width):
            i = x * bytes_per_px
            v = row_bytes[i]
            if color_type == 0:      # gray
                row_out_append((v, v, v, 255))
            elif color_type == 2:    # rgb
                row_out_append((v, row_bytes[i + 1], row_bytes[i + 2], 255))
            elif color_type == 3:    # palette index
                idx = v
                if palette is None or idx >= len(palette):
                    raise ValueError(f"PNG palette index {idx} out of range")
                r, g, b = palette[idx]
                row_out_append((r, g, b, 255))
            elif color_type == 4:    # gray + alpha
                row_out_append((v, v, v, row_bytes[i + 1]))
            else:                    # 6: rgb + a
                row_out_append((v, row_bytes[i + 1], row_bytes[i + 2], row_bytes[i + 3]))
        out.append(row_out)
        prev_row = cur if bit_depth == 16 else cur
    return width, height, out


# ---------------------------------------------------------------------------
# PNG encode (8-bit RGB, alpha forced to 255)
# ---------------------------------------------------------------------------


def _png_chunk_bytes(ctype: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + ctype
        + payload
        + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF)
    )


def encode_png(
    width: int,
    height: int,
    rows: list[tuple[int, int, int] | tuple[int, int, int, int]],
) -> bytes:
    """Encode 8-bit RGB pixels as a PNG (color_type=2, bit_depth=8).

    ``rows`` must have ``height`` rows of ``width`` pixels; each pixel is
    ``(r, g, b)`` or ``(r, g, b, a)`` (alpha is dropped — the encoder emits
    opaque RGB). Each scanline is written with a leading filter byte ``0``
    (None), the block is ``zlib.compress``-ed into a single IDAT chunk, and
    the file is ``signature + IHDR + IDAT + IEND`` with correct chunk
    lengths and CRC32s.
    """
    if width <= 0 or height <= 0:
        raise ValueError("PNG dimensions must be positive")
    if len(rows) != height:
        raise ValueError(f"expected {height} rows, got {len(rows)}")

    raw = bytearray()
    append = raw.append
    for y in range(height):
        row = rows[y]
        if len(row) != width:
            raise ValueError(f"row {y} has {len(row)} pixels, expected {width}")
        append(0)  # filter type: None
        for px in row:
            if not (0 <= len(px) <= 4 and len(px) >= 3):
                raise ValueError(f"bad pixel {px!r} in row {y}")
            r, g, b = px[0], px[1], px[2]
            if not all(0 <= c <= 255 for c in (r, g, b)):
                raise ValueError(f"pixel channel out of range in row {y}: {px!r}")
            append(r)
            append(g)
            append(b)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        PNG_SIGNATURE
        + _png_chunk_bytes(b"IHDR", ihdr)
        + _png_chunk_bytes(b"IDAT", zlib.compress(bytes(raw), 9))
        + _png_chunk_bytes(b"IEND", b"")
    )


# ---------------------------------------------------------------------------
# BMP decode (24/32-bit BI_RGB, bottom-up, 4-byte row padding)
# ---------------------------------------------------------------------------


def _decode_bmp(data: bytes) -> tuple[int, int, list[list[tuple[int, int, int, int]]]]:
    if len(data) < 14:
        raise ValueError("BMP truncated: missing header")
    (file_size,) = struct.unpack("<I", data[2:6])
    (offset,) = struct.unpack("<I", data[10:14])
    if len(data) < 6 + offset or file_size and len(data) < file_size:
        raise ValueError("BMP truncated: data shorter than declared file size")
    if offset < 14 or offset > len(data):
        raise ValueError("BMP bad pixel data offset")
    hdr_avail = offset - 14  # info header lives right after the 14-byte file header
    (first_word,) = struct.unpack("<I", data[14:18]) if hdr_avail >= 4 else (0,)

    if first_word in (40, 100, 108, 124):  # BITMAPINFOHEADER / V4 / V5
        if hdr_avail < 20:
            raise ValueError("BMP truncated: info header shorter than declared")
        (width, height, planes, bit_count) = struct.unpack("<iiHH", data[18:30])
        (compression,) = struct.unpack("<I", data[30:34])
    elif first_word == 12 or hdr_avail < 40:  # legacy BITMAPCOREHEADER (no size field)
        if hdr_avail < 12:
            raise ValueError("BMP unsupported header length")
        (width, height) = struct.unpack("<hh", data[14:18])
        (planes, bit_count) = struct.unpack("<HH", data[18:22])
        compression = 0  # BI_RGB is implied
    else:
        raise ValueError(f"BMP unsupported header (first word {first_word:#x})")

    if compression != 0:
        raise ValueError(f"BMP compression {compression} not supported (want BI_RGB)")
    if bit_count not in (24, 32):
        raise ValueError(f"BMP bit depth {bit_count} not supported (want 24 or 32)")
    if width <= 0:
        raise ValueError("BMP bad width")
    if height < 0:
        raise ValueError("BMP bad height")

    top_down = False
    abs_height = height
    if height < 0:  # BITMAPV5-style top-down flag in the header
        top_down = True
        abs_height = -height

    row_bytes = ((width * bit_count + 31) // 32) * 4  # padded to 4 bytes
    pix = data[offset : offset + abs_height * row_bytes]
    if len(pix) < abs_height * row_bytes:
        raise ValueError("BMP truncated: pixel data shorter than header declares")

    rows: list[list[tuple[int, int, int, int]]] = []
    for i in range(abs_height):
        src = i if top_down else (abs_height - 1 - i)  # bottom-up by default
        base = src * row_bytes
        row: list[tuple[int, int, int, int]] = []
        append = row.append
        for x in range(width):
            j = base + x * (bit_count // 8)
            b, g, r = pix[j], pix[j + 1], pix[j + 2]
            append((r, g, b, 255))
        rows.append(row)
    return width, abs_height, rows


# ---------------------------------------------------------------------------
# Pixel operations (pure Python)
# ---------------------------------------------------------------------------


def to_gray(rows: list[list[tuple[int, int, int, int]]]) -> list[list[int]]:
    """Luminance gray: ``round(0.299*r + 0.587*g + 0.114*b)``, values 0..255."""
    out: list[list[int]] = []
    for row in rows:
        out.append(
            [
                min(255, round(0.299 * px[0] + 0.587 * px[1] + 0.114 * px[2]))
                for px in row
            ]
        )
    return out


def resize_nearest(
    gray: list[list[int]], cols: int, rows: int
) -> list[list[int]]:
    """Nearest-neighbor resize mapping target pixel → source pixel by
    ``round(i * src / dst - 0.5)`` (half-integer grid centers, clamped)."""
    if cols <= 0 or rows <= 0:
        raise ValueError("resize target must be positive")
    if not gray or not gray[0]:
        raise ValueError("cannot resize an empty image")
    sw, sh = len(gray[0]), len(gray)
    out: list[list[int]] = []
    for y in range(rows):
        sy = min(sh - 1, max(0, round((y + 0.5) * sh / rows - 0.5)))
        src_row = gray[sy]
        out.append(
            [
                src_row[min(sw - 1, max(0, round((x + 0.5) * sw / cols - 0.5)))]
                for x in range(cols)
            ]
        )
    return out


def otsu_threshold(gray: list[list[int]]) -> int:
    """Otsu's threshold over the 0..255 histogram (maximizes inter-class
    variance). Returns the **class boundary** t in [0, 255]: pixels with
    ``value >= t`` form the "high" class, ``value < t`` the "low" class —
    so a bimodal image with modes at 0 and 200 yields t = 100 (the middle
    of the plateau of maximal-variance boundaries between the modes). A
    flat (single-value) image yields 127."""
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
    out-of-bounds neighbours count as 0 (floor)."""
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
