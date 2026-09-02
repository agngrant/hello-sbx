"""Imaging tests (stdlib unittest; Iteration 3).

Covers: PNG encode→decode round-trip (exact pixels), Otsu on a bimodal
image, nearest-neighbor resize, the 3×3 majority filter, signature
rejection, and hand-built 24/32-bit BMP decoding.
"""

from __future__ import annotations

import struct
import unittest
import zlib
from typing import Callable

from app.imaging import (
    decode_image,
    encode_png,
    median3x3,
    otsu_threshold,
    resize_nearest,
    to_gray,
)


def _rgb8_pattern() -> list[list[tuple[int, int, int, int]]]:
    """Deterministic 8x8 RGB pattern: every pixel unique, channels distinct.

    Returned as full RGBA (alpha 255) so the round-trip assertion is an
    exact buffer equality (decode always emits RGBA with alpha default 255).
    """
    rows = []
    for y in range(8):
        rows.append(
            list(
                ((y * 32 + x * 17) % 256, (y * 71 + x * 53) % 256, (y * 13 + x * 97) % 256, 255)
                for x in range(8)
            )
        )
    return rows


class TestPngRoundtrip(unittest.TestCase):
    def test_encode_decode_exact_pixels(self):
        pattern = _rgb8_pattern()
        png = encode_png(8, 8, pattern)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn(b"IHDR", png)
        self.assertIn(b"IDAT", png)
        self.assertIn(b"IEND", png)
        width, height, rows = decode_image(png)
        self.assertEqual((width, height), (8, 8))
        self.assertEqual(rows, pattern)  # exact 8-bit RGB equality (a → 255)
        for row in rows:
            for px in row:
                self.assertEqual(px[3], 255)

    def test_encode_rgba_drops_alpha(self):
        rgba = [
            [(255, 0, 0, 0), (0, 255, 0, 128), (0, 0, 255, 255)],
            [(10, 20, 30, 5), (40, 50, 60, 60), (70, 80, 90, 200)],
        ]
        png = encode_png(3, 2, rgba)
        width, height, rows = decode_image(png)
        self.assertEqual(width, 3)
        self.assertEqual(height, 2)
        self.assertEqual(rows[0][0], (255, 0, 0, 255))
        self.assertEqual(rows[1][2], (70, 80, 90, 255))

    def test_chunk_crcs_are_valid(self):
        # Corrupting any payload byte must be caught by the CRC check.
        png = bytearray(encode_png(2, 2, [[(1, 2, 3)] * 2, [(4, 5, 6)] * 2]))
        # IDAT payload starts after signature(8) + IHDR(8+13) = 29; the IDAT
        # header is 12 bytes → first payload byte at 29 + 12 + 8.
        idat_payload_start = 8 + (8 + 13) + 8 + 12
        png[idat_payload_start] ^= 0xFF
        with self.assertRaises(ValueError):
            decode_image(bytes(png))

    def test_bad_dimensions_rejected(self):
        with self.assertRaises(ValueError):
            encode_png(0, 4, [])
        with self.assertRaises(ValueError):
            encode_png(2, 2, [[(0, 0, 0)] * 2])  # missing row
        with self.assertRaises(ValueError):
            encode_png(2, 1, [[(0, 0, 0), (300, 0, 0)]])  # channel > 255


# ---------------------------------------------------------------------------
# BUG-004 regression: multi-channel (RGB/RGBA) PNG decoding with the Average
# and Paeth filters. The old code indexed the "up"/"up-left" reference pixels
# by the *pixel* byte offset ``x`` instead of the per-channel offset ``x + c``,
# so only channel 0 decoded correctly and the green/blue channels were
# corrupted. ``encode_png`` (every other fixture) writes filter 0 (None) so
# these branches were never exercised. Here we hand-craft a PNG whose
# scanlines use the named filters and assert an EXACT round-trip.
# ---------------------------------------------------------------------------


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _png_chunk_b(ctype: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + ctype + payload
            + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF))


def _build_png(w: int, h: int, channels: int, color_type: int,
               recon: list[list[int]], filters: list[int]) -> bytes:
    """Assemble a non-interlaced 8-bit PNG from per-byte ``recon`` scanlines.

    ``recon[y][b]`` is the true reconstructed byte at scanline ``y``, byte ``b``
    (length ``w * channels``). For each scanline we compute the PNG *raw*
    (filtered) bytes from ``recon`` using the given filter — mirroring the
    decoder's reconstruction logic branch-for-branch so the round-trip is
    exact — prefix with the filter byte, deflate everything into IDAT, and
    wrap in valid chunks.
    """
    stride = w * channels
    raw = bytearray()
    prev = None  # integer view of the previous reconstructed scanline
    for y in range(h):
        ftype = filters[y]
        cur = recon[y]
        raw.append(ftype)
        for b in range(stride):
            left = cur[b - channels] if b >= channels else 0
            up = prev[b] if prev is not None else 0
            upleft = (prev[b - channels]
                      if (prev is not None and b >= channels) else 0)
            if ftype == 0:
                f = 0
            elif ftype == 1:      # Sub
                f = left
            elif ftype == 2:      # Up
                f = up
            elif ftype == 3:      # Average
                f = (left + up) >> 1
            else:                 # 4: Paeth
                f = _paeth(left, up, upleft)
            raw.append((cur[b] - f) & 0xFF)
        prev = list(cur)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, color_type, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk_b(b"IHDR", ihdr)
            + _png_chunk_b(b"IDAT", zlib.compress(bytes(raw), 9))
            + _png_chunk_b(b"IEND", b""))


class TestMultiChannelPngFilters(unittest.TestCase):
    """BUG-004: RGB/RGBA decode must be exact for the Average + Paeth filters."""

    def _pattern(self, w: int, h: int, channels: int) -> list[list[int]]:
        """Deterministic per-byte scanlines in the image's channel order
        (R,G,B[,A]). Small, smooth gradients keep the raw (filtered) values
        small and unambiguous for the round-trip, while still differing per
        channel so an offset bug corrupts green/blue but not red."""
        rows = []
        for y in range(h):
            row = []
            for x in range(w):
                for c in range(channels):
                    row.append((11 * x + 23 * y + 5 * c) % 256)
            rows.append(row)
        return rows

    def _flat(self, rows, channels) -> list[int]:
        """Flatten decoded RGBA rows to the first ``channels`` bytes/px."""
        out = []
        for px_row in rows:
            for px in px_row:
                out.extend(px[c] for c in range(channels))
        return out

    def test_rgb_average_roundtrip(self):
        w, h = 4, 4
        recon = self._pattern(w, h, 3)
        png = _build_png(w, h, 3, 2, recon, [3, 0, 3, 3])
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        _, _, rows = decode_image(png)
        self.assertEqual(self._flat(rows, 3), [b for row in recon for b in row])

    def test_rgb_paeth_roundtrip(self):
        w, h = 4, 4
        recon = self._pattern(w, h, 3)
        png = _build_png(w, h, 3, 2, recon, [4, 0, 4, 4])
        _, _, rows = decode_image(png)
        self.assertEqual(self._flat(rows, 3), [b for row in recon for b in row])

    def test_rgba_average_and_paeth_roundtrip(self):
        w, h = 4, 3
        recon = self._pattern(w, h, 4)
        # Mix every filter across the scanlines (first is None since there is
        # no up/left on row 0; the rest vary to hit the unfilter branches).
        png = _build_png(w, h, 4, 6, recon, [0, 3, 4])
        _, _, rows = decode_image(png)
        self.assertEqual(self._flat(rows, 4), [b for row in recon for b in row])

    def test_sub_filter_multi_channel(self):
        # Sub was always correct, but pin it for RGB too (no regressions).
        w, h = 4, 2
        recon = self._pattern(w, h, 3)
        png = _build_png(w, h, 3, 2, recon, [1, 1])
        _, _, rows = decode_image(png)
        self.assertEqual(self._flat(rows, 3), [b for row in recon for b in row])

    def test_paeth_fixes_green_blue_not_red(self):
        """A narrow, targeted round-trip proof of the BUG-004 index bug.

        A 2x2 RGB image whose green channel is large and distinct from red
        (so the old ``prev_row[x]`` = channel-0 indexing would corrupt green
        and blue while red looked fine). Average and Paeth must both be exact.
        """
        # row0: (10, 200, 5), (30, 40, 90)
        # row1: (12, 202, 7), (32, 42, 92)   -> green/blue differ from red
        recon = [
            [10, 200, 5, 30, 40, 90],
            [12, 202, 7, 32, 42, 92],
        ]
        for ftype in (3, 4):  # Average and Paeth both must be exact
            png = _build_png(2, 2, 3, 2, recon, [0, ftype])
            _, _, rows = decode_image(png)
            self.assertEqual(self._flat(rows, 3), [b for row in recon for b in row])
            # Spot-check the specific pixels (green/blue must survive the bug).
            self.assertEqual(rows[0][0][:3], (10, 200, 5))
            self.assertEqual(rows[1][1][:3], (32, 42, 92))


class TestOtsu(unittest.TestCase):
    def test_bimodal_zero_and_200(self):
        # Half 0, half 200: the maximal-variance boundary must land between
        # the two modes.
        gray = [[0, 200, 0, 200] for _ in range(4)]
        t = otsu_threshold(gray)
        self.assertGreater(t, 50)
        self.assertLess(t, 150)

    def test_flat_image_falls_back(self):
        self.assertEqual(otsu_threshold([[7, 7, 7], [7, 7, 7]]), 127)
        self.assertEqual(otsu_threshold([[0]]), 127)
        self.assertEqual(otsu_threshold([]), 127)

    def test_two_mode_10_245(self):
        gray = [[10, 245, 245, 10] for _ in range(3)]
        t = otsu_threshold(gray)
        self.assertGreater(t, 10)
        self.assertLess(t, 245)


class TestResizeNearest(unittest.TestCase):
    def test_8x8_to_4x4_blocks(self):
        # Four 4x4 quadrants, each a constant value.
        gray = [
            [10] * 4 + [20] * 4 for _ in range(4)
        ] + [
            [30] * 4 + [40] * 4 for _ in range(4)
        ]
        out = resize_nearest(gray, 4, 4)
        self.assertEqual(
            out,
            [[10, 10, 20, 20], [10, 10, 20, 20], [30, 30, 40, 40], [30, 30, 40, 40]],
        )

    def test_upscale_duplicate(self):
        gray = [[1, 2], [3, 4]]
        out = resize_nearest(gray, 4, 4)
        self.assertEqual(len(out), 4)
        self.assertTrue(all(len(row) == 4 for row in out))
        # Nearest-neighbor upscale: each source pixel covers a 2x2 block.
        self.assertEqual(out[0][0], 1)
        self.assertEqual(out[0][1], 1)
        self.assertEqual(out[1][1], 1)
        self.assertEqual(out[1][2], 2)
        self.assertEqual(out[3][3], 4)

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            resize_nearest([[1, 2]], 0, 4)
        with self.assertRaises(ValueError):
            resize_nearest([], 4, 4)


class TestMedian3x3(unittest.TestCase):
    def test_removal_of_single_pixel_noise_dot(self):
        grid = [[0] * 7 for _ in range(7)]
        grid[3][3] = 1  # lone dark dot
        out = median3x3(grid)
        self.assertEqual(out, [[0] * 7 for _ in range(7)])

    def test_preserves_solid_block(self):
        grid = [[0] * 7 for _ in range(7)]
        for y in range(2, 5):
            for x in range(2, 5):
                grid[y][x] = 1
        out = median3x3(grid)
        self.assertEqual(
            out,
            [[0, 0, 0, 0, 0, 0, 0],
             [0, 0, 0, 0, 0, 0, 0],
             [0, 0, 1, 1, 1, 0, 0],
             [0, 0, 1, 1, 1, 0, 0],
             [0, 0, 1, 1, 1, 0, 0],
             [0, 0, 0, 0, 0, 0, 0],
             [0, 0, 0, 0, 0, 0, 0]],
        )

    def test_center_wins_border_tie(self):
        # 1-row strip: the middle cell has 3 in-bounds neighbours; with two
        # of them set and the center 0, the center wins the tie.
        out = median3x3([[0, 1, 1, 1, 0]])
        self.assertEqual(out[0][1], 0)  # center 0 kept (tie 2-1 vs OOB zeros)
        self.assertEqual(out[0][2], 1)  # center 1 with two ones → 3/3 ones
        self.assertEqual(out[0][3], 0)  # center 0 with two ones → tie, center wins


class TestToGray(unittest.TestCase):
    def test_known_luma_values(self):
        rows = [[(255, 255, 255, 255), (0, 0, 0, 255), (255, 0, 0, 255)]]
        gray = to_gray(rows)
        self.assertEqual(gray, [[255, 0, round(0.299 * 255)]])

    def test_black_white_bounds(self):
        gray = to_gray([[(12, 12, 12, 255), (245, 245, 245, 255)]])
        self.assertEqual(gray[0][0], round(0.299 * 12 + 0.587 * 12 + 0.114 * 12))
        self.assertGreater(gray[0][1], 200)


def _bmp24(width: int, height: int, pixel: Callable[[int, int], tuple[int, int, int]]) -> bytes:
    """Build a 24-bit BI_RGB BMP bottom-up from pixel(x, y) -> (r, g, b),
    with each row padded to a 4-byte boundary."""
    rowbytes = ((width * 24 + 31) // 32) * 4
    pix = bytearray()
    for y in range(height - 1, -1, -1):  # bottom-up
        for x in range(width):
            r, g, b = pixel(x, y)
            pix += bytes((b, g, r))
        pix += b"\x00" * (rowbytes - width * 3)  # row padding
    info = struct.pack(
        "<IiiHHIIiiII",
        40, width, height, 1, 24, 0, len(pix), 2835, 2835, 0, 0,
    )
    header = struct.pack("<2sIHHI", b"BM", 14 + 40 + len(pix), 0, 0, 14 + 40)
    return header + info + bytes(pix)


def _bmp32(width: int, height: int, pixel: Callable[[int, int], tuple[int, int, int]]) -> bytes:
    """Build a 32-bit BI_RGB BMP bottom-up from pixel(x, y) -> (r, g, b)
    (the unused 4th byte is 0 — no row padding is needed at 32-bit)."""
    rowbytes = ((width * 32 + 31) // 32) * 4
    pix = bytearray()
    for y in range(height - 1, -1, -1):
        for x in range(width):
            r, g, b = pixel(x, y)
            pix += bytes((b, g, r, 0))
    info = struct.pack(
        "<IiiHHIIiiII",
        40, width, height, 1, 32, 0, len(pix), 2835, 2835, 0, 0,
    )
    header = struct.pack("<2sIHHI", b"BM", 14 + 40 + len(pix), 0, 0, 14 + 40)
    return header + info + bytes(pix)


class TestBmpDecode(unittest.TestCase):
    def test_24bit_pixels(self):
        # pixel(x, y) = (r=255-50x, g=30y, b=10+2y): exercises bottom-up
        # row order and BGR byte order.
        bmp = _bmp24(3, 2, lambda x, y: (255 - 50 * x, 30 * y, 10 + 2 * y))
        width, height, rows = decode_image(bmp)
        self.assertEqual((width, height), (3, 2))
        self.assertEqual(rows[0][0], (255, 0, 10, 255))
        self.assertEqual(rows[0][2], (155, 0, 10, 255))
        self.assertEqual(rows[1][0], (255, 30, 12, 255))
        self.assertEqual(rows[1][1], (205, 30, 12, 255))
        self.assertEqual(rows[1][2], (155, 30, 12, 255))

    def test_32bit_pixels(self):
        bmp = _bmp32(2, 1, lambda x, y: (10 + x, 20 + x, 30 + x))
        width, height, rows = decode_image(bmp)
        self.assertEqual((width, height), (2, 1))
        self.assertEqual(rows[0][0], (10, 20, 30, 255))
        self.assertEqual(rows[0][1], (11, 21, 31, 255))

    def test_16bit_rejected(self):
        pix = b"\x00" * 8
        info = struct.pack("<IiiHHIIiiII", 40, 2, 1, 1, 16, 0, 8, 2835, 2835, 0, 0)
        header = struct.pack("<2sIHHI", b"BM", 14 + 40 + 8, 0, 0, 14 + 40)
        with self.assertRaises(ValueError):
            decode_image(header + info + pix)


class TestDecodeRejection(unittest.TestCase):
    def test_rejects_non_image_bytes(self):
        for junk in (b"", b"hello world", b"JPEG\x00\x01", b"\x89PNG\r\n\x1a\nx"):
            with self.assertRaises(ValueError):
                decode_image(junk)

    def test_rejects_truncated_png(self):
        png = encode_png(4, 4, [[(1, 2, 3)] * 4 for _ in range(4)])
        with self.assertRaises(ValueError):
            decode_image(png[:40])


if __name__ == "__main__":
    unittest.main()
