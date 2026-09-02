"""Wall/doorway detection (PROJECT.md §7) — pure standard library.

Pipeline: decode → grayscale → (resize) → Otsu threshold → binarize →
3×3 majority → classify walls/floors → doorway heuristic.

The result is a *suggestion*: the GM remains the editor of record and fixes
cells via the paint endpoint / UI.

Public API:

* :func:`detect_grid` — image bytes → :class:`app.models.Grid`.
* :func:`classify_doors` — pure doorway classifier over an existing
  ``floor``/``wall`` cell grid (unit-testable without any image).
* :func:`grid_to_thumbnail_png` — render a grid as a small PNG (walls dark,
  floor light, doorway amber) via :func:`app.imaging.encode_png`.
"""

from __future__ import annotations

import base64

from app.imaging import (
    decode_image,
    encode_png,
    median3x3,
    otsu_threshold,
    resize_nearest,
    to_gray,
)
from app.models import Grid

#: Max grid edge for the default (no cols/rows given) auto-scale.
MAX_GRID_EDGE = 60
#: Minimum grid edge after auto-scaling.
MIN_GRID_EDGE = 4
#: If more than this fraction of cells became walls, auto-invert the map.
WALL_FRACTION_AUTO_INVERT = 0.6

# Thumbnail palette: walls dark, floor light, doorway amber.
_THUMB_WALL = (43, 48, 60, 255)
_THUMB_FLOOR = (239, 233, 220, 255)
_THUMB_DOOR = (217, 119, 6, 255)


def _scaled_grid_size(image_w: int, image_h: int) -> tuple[int, int]:
    """Scale so ``max(w, h) <= MAX_GRID_EDGE``, preserving aspect ratio
    (integer, each edge at least ``MIN_GRID_EDGE``)."""
    m = max(image_w, image_h)
    if m <= MAX_GRID_EDGE:
        return image_w, image_h
    cols = max(MIN_GRID_EDGE, round(image_w * MAX_GRID_EDGE / m))
    rows = max(MIN_GRID_EDGE, round(image_h * MAX_GRID_EDGE / m))
    return cols, rows


def classify_doors(
    cells: list[list[str]],
    width: int,
    height: int,
) -> list[list[str]]:
    """Pure doorway classifier (PROJECT.md §7 step 7).

    Input cells contain only ``"floor"``/``"wall"`` (any other value, e.g. a
    pre-existing ``"doorway"``, is treated as floor). A *floor* cell becomes
    ``"doorway"`` when it is a gap in a wall: it has walls on two **opposite**
    orthogonal neighbours — (up and down) or (left and right). Out-of-bounds
    neighbours count as *not* a wall, so gaps at the map border are not
    flagged. Returns a new grid; the input is not mutated.
    """
    out: list[list[str]] = []
    for y in range(height):
        out_row: list[str] = []
        append = out_row.append
        for x in range(width):
            c = cells[y][x]
            if c != "floor":
                append(c)
                continue
            up = y > 0 and cells[y - 1][x] == "wall"
            down = y < height - 1 and cells[y + 1][x] == "wall"
            left = x > 0 and cells[y][x - 1] == "wall"
            right = x < width - 1 and cells[y][x + 1] == "wall"
            if (up and down) or (left and right):
                append("doorway")
            else:
                append("floor")
        out.append(out_row)
    return out


def _wall_cells(
    image_w: int, image_h: int, b: list[list[int]]
) -> list[list[str]]:
    """Binary 0/1 grid (1 ⇒ pixel is in the wall class) → wall/floor cells."""
    return [
        [("wall" if b[y][x] == 1 else "floor") for x in range(image_w)]
        for y in range(image_h)
    ]


def detect_grid(
    image_bytes: bytes,
    name: str = "Map",
    cols: int | None = None,
    rows: int | None = None,
    dark_is_wall: bool = True,
) -> Grid:
    """Detect walls + doorways from a PNG/BMP image and build a :class:`Grid`.

    Steps (PROJECT.md §7): decode → gray → target size (given ``cols``/
    ``rows`` or auto-scale so ``max(w,h) <= 60``, min edge 4, preserving
    aspect) → nearest-neighbor resize → Otsu threshold → binarize (dark =
    wall when ``dark_is_wall``) → 3×3 majority → wall/floor cells → doorway
    heuristic → auto-invert if > 60% of cells became walls (flip
    wall↔floor and re-derive doorways).
    """
    width, height, pixel_rows = decode_image(image_bytes)

    if cols is not None or rows is not None:
        if not (
            isinstance(cols, int) and isinstance(rows, int)
            and cols >= 1 and rows >= 1
        ):
            raise ValueError("cols and rows must both be positive integers")
        grid_w, grid_h = int(cols), int(rows)
    else:
        grid_w, grid_h = _scaled_grid_size(width, height)

    gray = to_gray(pixel_rows)
    resized = resize_nearest(gray, grid_w, grid_h)

    t = otsu_threshold(resized)
    # Otsu boundary: the "low" (dark) class is [0, t), the "high" class is
    # [t, 255]. A flat image yields t=127, which binarizes to all-floor.
    binary = [
        [(1 if (g < t) == dark_is_wall else 0) for g in row]
        for row in resized
    ]
    smoothed = median3x3(binary)

    cells = _wall_cells(grid_w, grid_h, smoothed)
    cells = classify_doors(cells, grid_w, grid_h)

    # Auto-invert: > 60% walls ⇒ the map is probably inverted (light walls on
    # dark background). Flip wall<->floor and re-derive the doorways.
    n_cells = grid_w * grid_h
    wall_frac = sum(1 for row in cells for c in row if c == "wall") / n_cells
    if wall_frac > WALL_FRACTION_AUTO_INVERT:
        # True swap: wall->floor, floor->wall (old doorways reset to floor
        # and re-derived below).
        cells = [
            [
                "floor" if c == "wall"
                else ("wall" if c == "floor" else "floor")
                for c in row
            ]
            for row in cells
        ]
        cells = classify_doors(cells, grid_w, grid_h)

    return Grid(name=name, width=grid_w, height=grid_h, cells=cells)


def grid_to_thumbnail_png(grid: Grid, cell_px: int = 4) -> str:
    """Render a grid as a base64 PNG data-URL: walls dark, floor light,
    doorway amber (one solid ``cell_px`` square per grid cell)."""
    if cell_px < 1:
        raise ValueError("cell_px must be >= 1")
    palette = {
        "wall": _THUMB_WALL,
        "floor": _THUMB_FLOOR,
        "doorway": _THUMB_DOOR,
    }
    rows: list[tuple[int, int, int]] = []
    for _y in range(grid.height * cell_px):
        src_y = min(grid.height - 1, _y // cell_px)
        row: list[tuple[int, int, int]] = []
        for _x in range(grid.width * cell_px):
            src_x = min(grid.width - 1, _x // cell_px)
            r, g, b, _a = palette[grid.cells[src_y][src_x]]
            row.append((r, g, b))
        rows.append(row)
    png = encode_png(grid.width * cell_px, grid.height * cell_px, rows)
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")
