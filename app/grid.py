"""Grid helpers (PROJECT.md §4: cells stored as a 2D array, ``cells[y][x]``).

Also contains the hand-authored built-in sample map (Iteration 1) so the map
view has data before detection (Iteration 3) exists. Plain Python + the
stdlib :class:`Grid` dataclass — no third-party dependencies.
"""

from __future__ import annotations

from typing import Any

from app.models import Grid

# ---------------------------------------------------------------------------
# Built-in sample dungeon — hand-authored, 16 x 12 (Iteration 1).
#
# Legend:  W = wall   . = floor   D = doorway
#
# Layout: a border wall plus two interior vertical walls (col 5 full height,
# col 10 top half) and one horizontal wall (row 7, cols 6-13). The three
# doorways — (5,5), (10,4), (9,7) — are gaps in those walls and keep every
# region connected:
#
#   W W W W W W W W W W W W W W W W
#   W . . . . W . . . . W . . . . W
#   W . . . . W . . . . W . . . . W
#   W . . . . W . . . . W . . . . W
#   W . . . . W . . . . D . . . . W
#   W . . . . D . . . . W . . . . W
#   W . . . . W . . . . W . . . . W
#   W . . . . W W W W D W W W W . W
#   W . . . . W . . . . . . . . . W
#   W . . . . W . . . . . . . . . W
#   W . . . . W . . . . . . . . . W
#   W W W W W W W W W W W W W W W W
# ---------------------------------------------------------------------------

SAMPLE_MAP_ID = "sample-dungeon"

_SAMPLE_MAP_LINES: list[str] = [
    "WWWWWWWWWWWWWWWW",  # y=0
    "W....W....W....W",  # y=1
    "W....W....W....W",  # y=2
    "W....W....W....W",  # y=3
    "W....W....D....W",  # y=4  doorway (10,4)
    "W....D....W....W",  # y=5  doorway (5,5)
    "W....W....W....W",  # y=6
    "W....WWWWDWWWW.W",  # y=7  doorway (9,7)
    "W....W.........W",  # y=8
    "W....W.........W",  # y=9
    "W....W.........W",  # y=10
    "WWWWWWWWWWWWWWWW",  # y=11
]

_CHAR_TO_CELL: dict[str, str] = {"W": "wall", ".": "floor", "D": "doorway"}


def build_sample_map(name: str = "Sample Dungeon") -> Grid:
    """Build the built-in 16x12 sample dungeon (hand-authored)."""
    width = len(_SAMPLE_MAP_LINES[0])
    for row in _SAMPLE_MAP_LINES:
        if len(row) != width:
            raise ValueError(f"sample map rows must be {width} chars wide: {row!r}")
    cells: list[list[str]] = [
        [_CHAR_TO_CELL[ch] for ch in row] for row in _SAMPLE_MAP_LINES
    ]
    return Grid(name=name, width=width, height=len(_SAMPLE_MAP_LINES), cells=cells)


# ---------------------------------------------------------------------------
# Cell helpers
# ---------------------------------------------------------------------------


def in_bounds(grid: Grid, x: int, y: int) -> bool:
    """True if ``(x, y)`` is a valid cell coordinate of ``grid``."""
    return 0 <= x < grid.width and 0 <= y < grid.height


def get_cell(grid: Grid, x: int, y: int) -> str:
    """Return the cell type at ``(x, y)``. Raises ``IndexError`` if out of bounds."""
    if not in_bounds(grid, x, y):
        raise IndexError(
            f"({x}, {y}) out of bounds for {grid.width}x{grid.height} grid"
        )
    return grid.cells[y][x]


def set_cell(grid: Grid, x: int, y: int, cell_type: str) -> str:
    """Set the cell at ``(x, y)`` and return the new value.

    Mutates ``grid`` in place (the GM ``paint`` action in Iteration 4 will
    route through here). Raises ``IndexError`` if out of bounds.
    """
    if not in_bounds(grid, x, y):
        raise IndexError(
            f"({x}, {y}) out of bounds for {grid.width}x{grid.height} grid"
        )
    grid.cells[y][x] = cell_type
    return cell_type


# ---------------------------------------------------------------------------
# (De)serialization
# ---------------------------------------------------------------------------


def to_dict(grid: Grid) -> dict[str, Any]:
    """Plain-dict form of a grid (what ``GET /api/maps/{id}`` returns, minus
    the registry-level ``id``/``entities``/``players`` keys)."""
    return grid.to_dict()


def from_dict(data: dict[str, Any]) -> Grid:
    """Rebuild a :class:`Grid` from its dict form (validated)."""
    return Grid.from_dict(data)
