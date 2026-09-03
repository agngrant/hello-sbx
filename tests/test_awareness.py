"""Awareness overlay tests (stdlib unittest; Iteration 4 → three-tier §5).

Covers PROJECT.md §5: relation by the target's team, team colors with
explicit-override handling, GM-sees-all (labeled, unmasked, never
filtered — even with a grid), the player three-tier visibility model
(FULL on line of sight / APPROXIMATE within APPROX_RADIUS without LOS /
INVISIBLE beyond), the legacy no-grid radar, self-exclusion and
anchor-missing behavior.
"""

from __future__ import annotations

import unittest

from app.awareness import (
    APPROX_BLOCK,
    APPROX_RADIUS,
    AWARENESS_MAX,
    AWARENESS_MIN,
    build_awareness,
    overlay_color,
    relation_of,
)
from app.models import Entity, Grid, Player


def ent(eid: str, team: str, kind: str = "player", color: str | None = None,
        x: int = 0, y: int = 0) -> Entity:
    return Entity(id=eid, name=f"{team}-{eid}", kind=kind, team=team,
                  x=x, y=y, color=color)


def items_by_id(items: list[dict]) -> dict[str, dict]:
    return {item["entity_id"]: item for item in items}


def make_grid(rows: list[list[str]], name: str = "test") -> Grid:
    """Build a :class:`Grid` from a list of rows of cell strings
    (``"floor"``, ``"wall"``, ``"doorway"``)."""
    height = len(rows)
    width = len(rows[0])
    for row in rows:
        if len(row) != width:
            raise ValueError(f"ragged test grid: {rows!r}")
    return Grid(name=name, width=width, height=height, cells=[list(r) for r in rows])


class TestRelationOf(unittest.TestCase):
    def test_party_is_friend(self):
        self.assertEqual(relation_of(ent("a", "party")), "friend")

    def test_neutral_is_neutral(self):
        self.assertEqual(relation_of(ent("b", "neutral", kind="npc")), "neutral")

    def test_hostile_is_enemy(self):
        self.assertEqual(relation_of(ent("c", "hostile", kind="enemy")), "enemy")


class TestOverlayColor(unittest.TestCase):
    def test_team_colors(self):
        self.assertEqual(overlay_color("friend", ent("a", "party")), "green")
        self.assertEqual(overlay_color("neutral", ent("b", "neutral")), "white")
        self.assertEqual(overlay_color("enemy", ent("c", "hostile")), "red")

    def test_explicit_color_wins(self):
        blue = ent("a", "hostile", kind="enemy", color="blue")
        self.assertEqual(overlay_color("enemy", blue), "blue")
        self.assertEqual(overlay_color("friend", ent("b", "party", color="purple")),
                         "purple")

    def test_explicit_override_beats_gm_true_color(self):
        # build_awareness uses the same rule for GMs: explicit color wins.
        gm = Player(id="g", name="Gamer", role="gm", entity_id=None)
        marked = ent("h", "hostile", kind="enemy", color="blue")
        (item,) = build_awareness(gm, {"h": marked})
        self.assertEqual(item["color"], "blue")
        self.assertTrue(item["label"])


class TestBuildAwarenessGmSeesAll(unittest.TestCase):
    def test_gm_gets_all_labeled_items(self):
        # The GM is a pure controller: it has NO entity of its own
        # (entity_id is None) — the GM branch never references
        # viewer.entity_id, so it simply sees everything, labeled.
        gm = Player(id="gm1", name="Gamemaster", role="gm", entity_id=None)
        entities = {
            "e1": ent("e1", "party", kind="player", x=1, y=1),
            "e2": ent("e2", "neutral", kind="npc", x=2, y=2),
            "e3": ent("e3", "hostile", kind="enemy", x=3, y=3),
        }
        items = build_awareness(gm, entities)
        self.assertEqual(len(items), 3)
        by_id = items_by_id(items)
        self.assertEqual(set(by_id), {"e1", "e2", "e3"})
        # All labeled, carrying name + kind, unmasked true colors.
        for item in items:
            self.assertTrue(item["label"])
            self.assertIn("name", item)
            self.assertIn("kind", item)
        self.assertEqual(by_id["e1"]["color"], "green")
        self.assertEqual(by_id["e2"]["color"], "white")
        self.assertEqual(by_id["e3"]["color"], "red")
        # Every listed entity is someone else's token, at its position,
        # with its name (there is no own item for the GM to exclude).
        self.assertEqual(by_id["e1"]["name"], "party-e1")
        self.assertEqual((by_id["e1"]["x"], by_id["e1"]["y"]), (1, 1))

    def test_gm_unaffected_by_grid_even_beyond_approx_radius(self):
        # The grid changes NOTHING for the GM: an entity far behind a
        # wall (well beyond APPROX_RADIUS) is still FULL + labeled.
        gm = Player(id="gm1", name="Gamemaster", role="gm", entity_id=None)
        grid = make_grid([
            ["wall", "floor", "floor", "floor", "floor", "floor", "floor"],
            ["wall", "floor", "floor", "wall", "wall", "floor", "wall"],
            ["wall", "floor", "floor", "wall", "wall", "floor", "wall"],
            ["wall", "floor", "floor", "floor", "floor", "floor", "floor"],
        ])
        entities = {
            "A": ent("A", "party", x=1, y=1),
            "FAR": ent("FAR", "hostile", kind="enemy", x=5, y=2),  # wall row, cheb 4
        }
        items = build_awareness(gm, entities, grid)
        by_id = items_by_id(items)
        self.assertEqual(set(by_id), {"A", "FAR"})  # far entity NOT filtered
        self.assertEqual(by_id["FAR"]["color"], "red")
        self.assertTrue(by_id["FAR"]["label"])
        self.assertEqual(by_id["FAR"]["name"], "hostile-FAR")
        self.assertIn("kind", by_id["FAR"])
        self.assertNotIn("approximate", by_id["FAR"])


class TestBuildAwarenessPlayerLegacyNoGrid(unittest.TestCase):
    """Legacy radar: NO grid supplied → every other entity as an
    unlabeled colored dot (pass-through walls, unchanged)."""

    def setUp(self):
        self.viewer = Player(id="p1", name="Alice", role="player", entity_id="A")
        self.entities = {
            "A": ent("A", "party", x=0, y=0),        # the viewer's own character
            "B": ent("B", "party", x=1, y=0),        # friend
            "C": ent("C", "neutral", kind="npc", x=2, y=0),  # neutral NPC
            "D": ent("D", "hostile", kind="enemy", x=3, y=0),  # enemy
        }

    def test_excludes_self(self):
        items = build_awareness(self.viewer, self.entities)
        by_id = items_by_id(items)
        self.assertEqual(len(items), 3)
        self.assertEqual(set(by_id), {"B", "C", "D"})

    def test_colors_and_unlabeled_dots(self):
        by_id = items_by_id(build_awareness(self.viewer, self.entities))
        self.assertEqual(by_id["B"]["color"], "green")
        self.assertEqual(by_id["C"]["color"], "white")
        self.assertEqual(by_id["D"]["color"], "red")
        for item in by_id.values():
            self.assertFalse(item["label"])
            self.assertNotIn("name", item)
            self.assertNotIn("kind", item)
        self.assertEqual((by_id["B"]["x"], by_id["B"]["y"]), (1, 0))

    def test_output_sorted_by_entity_id(self):
        items = build_awareness(self.viewer, self.entities)
        self.assertEqual([i["entity_id"] for i in items], ["B", "C", "D"])

    def test_party_member_marked_hostile_shows_red(self):
        # §5: "A party member marked hostile by the GM then shows red."
        traitor = ent("T", "hostile", kind="player", x=5, y=5)
        by_id = items_by_id(build_awareness(self.viewer, {**self.entities, "T": traitor}))
        self.assertEqual(by_id["T"]["color"], "red")

    def test_neutral_entity_shows_white(self):
        neutral_npc = ent("N", "neutral", kind="npc", x=6, y=6)
        by_id = items_by_id(build_awareness(self.viewer, {**self.entities, "N": neutral_npc}))
        self.assertEqual(by_id["N"]["color"], "white")

    def test_empty_entity_set(self):
        self.assertEqual(build_awareness(self.viewer, {}), [])


# ---------------------------------------------------------------------------
# Player three-tier visibility model (grid supplied)
# ---------------------------------------------------------------------------

def tier_grid() -> Grid:
    """10×8 test map. Row layout (x = 0..9):

        y0  wall wall wall wall wall wall wall wall wall wall
        y1  W    .    .    .    .    W    .    .    .    W
        y2  W    .    W    W    .    .    W    .    W    W     <- wall (8,2)
        y3  W    .    .    W    .    .    W    .    .    W     <- wall (3,3)
        y4  W    .    .    .    .    .    W    .    .    W
        y5  W    .    .    .    W    .    .    .    .    W     <- wall (4,5)
        y6  W    .    .    .    W    W    W    W    W    W     <- wall (4,6)
        y7  wall wall wall wall wall wall wall wall wall wall

    Viewer O at (1,4). Hand-verified with Bresenham (app.pathfinding):
      * B (1,2)  — vertical line, all floor      → FULL
      * E (4,4)  — horizontal line, all floor    → FULL
      * F (2,3)  — line (1,4)→(2,3), no walls    → FULL
      * C (8,2)  — line crosses wall (3,3)       → NO LOS, cheb 7 → INVISIBLE
      * D (9,1)  — line crosses wall (3,3)       → NO LOS, cheb 8 → INVISIBLE
      * H (9,4)  — line crosses wall (6,4)       → NO LOS, cheb 8 → INVISIBLE
      * H' (4,2) — line crosses wall (3,3)       → NO LOS, cheb 3 → APPROX
      * C' (4,3) — line crosses wall (3,3)       → NO LOS, cheb 3 → APPROX
    """
    rows = [
        ["wall"] * 10,
        ["wall", "floor", "floor", "floor", "floor", "wall", "floor", "floor", "floor", "wall"],
        ["wall", "floor", "wall", "wall", "floor", "floor", "wall", "floor", "wall", "wall"],
        ["wall", "floor", "floor", "wall", "floor", "floor", "wall", "floor", "floor", "wall"],
        ["wall", "floor", "floor", "floor", "floor", "floor", "wall", "floor", "floor", "wall"],
        ["wall", "floor", "floor", "floor", "wall", "floor", "floor", "floor", "floor", "wall"],
        ["wall", "floor", "floor", "floor", "wall", "wall", "wall", "wall", "wall", "wall"],
        ["wall"] * 10,
    ]
    return make_grid(rows)


class TestPlayerTierFull(unittest.TestCase):
    def setUp(self):
        self.grid = tier_grid()
        self.viewer = Player(id="p1", name="Alice", role="player", entity_id="A")
        self.entities = {
            "A": ent("A", "party", x=1, y=4),
            "B": ent("B", "party", kind="player", x=1, y=2),   # LOS, friendly
            "E": ent("E", "hostile", kind="enemy", x=4, y=4),  # LOS, enemy
            "F": ent("F", "neutral", kind="npc", x=2, y=3),    # LOS, neutral
        }

    def test_los_entity_is_full_with_name_kind_label_and_color(self):
        items = build_awareness(self.viewer, self.entities, self.grid)
        by_id = items_by_id(items)
        # Own token excluded; every LOS entity present and FULL.
        self.assertEqual(len(items), 3)
        self.assertNotIn("A", by_id)
        # FULL item = identical shape to the GM item: exact position, color,
        # name, kind, label True.
        self.assertEqual(by_id["B"]["color"], "green")
        self.assertEqual(by_id["E"]["color"], "red")
        self.assertEqual(by_id["F"]["color"], "white")
        self.assertEqual(by_id["B"]["name"], "party-B")
        self.assertEqual(by_id["B"]["kind"], "player")
        self.assertTrue(by_id["B"]["label"])
        self.assertEqual((by_id["B"]["x"], by_id["B"]["y"]), (1, 2))  # exact
        for item in items:
            self.assertFalse(item.get("approximate"))
            self.assertIn("name", item)
            self.assertIn("kind", item)
            self.assertTrue(item["label"])

    def test_full_item_shape_matches_gm_item(self):
        gm = Player(id="gm1", name="G", role="gm", entity_id=None)
        player_items = items_by_id(
            build_awareness(self.viewer, self.entities, self.grid))
        gm_items = items_by_id(
            build_awareness(gm, {**self.entities}, self.grid))
        for eid in ("B", "E", "F"):
            self.assertEqual(player_items[eid], gm_items[eid])

    def test_explicit_color_override_wins_in_full_items(self):
        self.entities["E"].color = "blue"
        by_id = items_by_id(build_awareness(self.viewer, self.entities, self.grid))
        self.assertEqual(by_id["E"]["color"], "blue")


class TestPlayerTierApproximate(unittest.TestCase):
    def setUp(self):
        self.grid = tier_grid()
        self.viewer = Player(id="p1", name="Alice", role="player", entity_id="A")

    def test_no_los_within_radius_is_approximate_quantized_no_identity(self):
        # C' (4,3): Bresenham from (1,4) crosses wall (3,3) → no LOS;
        # chebyshev max(3, 1) = 3 ≤ 4 → approximate.
        entities = {"A": ent("A", "party", x=1, y=4),
                    "C": ent("C", "neutral", kind="npc", x=4, y=3)}
        (item,) = build_awareness(self.viewer, entities, self.grid)
        # Exact shape: surrogate id, quantized position, approximate flag,
        # label False — and NOTHING else (no color/name/kind/team/real id).
        self.assertEqual(
            set(item), {"entity_id", "x", "y", "approximate", "label"})
        self.assertTrue(item["approximate"])
        self.assertFalse(item["label"])
        self.assertNotIn("color", item)
        self.assertNotIn("name", item)
        self.assertNotIn("kind", item)
        self.assertNotIn("team", item)
        self.assertNotEqual(item["entity_id"], "C")
        self.assertEqual(item["entity_id"], "<approx-1>")
        # Block ORIGIN: (4,3) // 2 → (2, 1).
        self.assertEqual((item["x"], item["y"]), (4 // APPROX_BLOCK, 3 // APPROX_BLOCK))

    def test_two_approx_items_get_distinct_deterministic_surrogates(self):
        entities = {
            "A": ent("A", "party", x=1, y=4),
            "C": ent("C", "neutral", kind="npc", x=4, y=3),    # wall (3,3) on line, cheb 3
            "H": ent("H", "hostile", kind="enemy", x=4, y=2),  # wall (3,3) on line, cheb 3
        }
        items = build_awareness(self.viewer, entities, self.grid)
        self.assertEqual(len(items), 2)
        self.assertTrue(all(i["approximate"] for i in items))
        # Surrogates are distinct and deterministic (sorted-entity order).
        self.assertEqual(
            [i["entity_id"] for i in items], ["<approx-1>", "<approx-2>"])
        self.assertEqual((items[0]["x"], items[0]["y"]), (2, 1))  # C block
        self.assertEqual((items[1]["x"], items[1]["y"]), (2, 1))  # H block
        for i in items:
            self.assertNotIn("C", [i["entity_id"]])
            self.assertNotIn("H", [i["entity_id"]])

    def test_full_and_approx_coexist(self):
        entities = {
            "A": ent("A", "party", x=1, y=4),
            "B": ent("B", "party", kind="player", x=1, y=2),   # full (LOS)
            "C": ent("C", "neutral", kind="npc", x=8, y=2),    # invisible
            "E": ent("E", "hostile", kind="enemy", x=4, y=4),  # full (LOS)
            "H": ent("H", "neutral", kind="npc", x=4, y=2),    # approx (wall (3,3))
        }
        items = build_awareness(self.viewer, entities, self.grid)
        by_id = items_by_id(items)
        self.assertEqual(set(by_id), {"B", "E", "<approx-1>"})
        self.assertTrue(by_id["B"]["label"])
        self.assertIn("name", by_id["B"])
        self.assertTrue(by_id["<approx-1>"]["approximate"])
        self.assertEqual((by_id["<approx-1>"]["x"], by_id["<approx-1>"]["y"]), (2, 1))


class TestPlayerTierInvisible(unittest.TestCase):
    def setUp(self):
        self.grid = tier_grid()
        self.viewer = Player(id="p1", name="Alice", role="player", entity_id="A")

    def test_no_los_beyond_radius_is_absent(self):
        entities = {
            "A": ent("A", "party", x=1, y=4),
            "C": ent("C", "hostile", kind="enemy", x=8, y=2),  # wall (3,3), cheb 7
            "D": ent("D", "hostile", kind="enemy", x=9, y=1),  # wall (3,3), cheb 8
            "H": ent("H", "neutral", kind="npc", x=9, y=4),    # wall (6,4), cheb 8
        }
        items = build_awareness(self.viewer, entities, self.grid)
        self.assertEqual(items, [])

    def test_doorway_passes_line_of_sight(self):
        # Walled grid with a doorway strictly BETWEEN the viewer O (1,1) and
        # the target D (3,1). A1 (door-features): a CLOSED door on the O→D
        # line blocks LOS (D is APPROXIMATE, within 4 squares), so OPEN the
        # (2,1) door first to preserve the original "D is FULL on LOS" pin.
        g = make_grid([
            ["wall", "floor", "floor", "floor", "wall"],
            ["wall", "floor", "doorway", "floor", "wall"],
            ["wall", "wall", "wall", "wall", "wall"],
        ])
        viewer = Player(id="p1", name="A", role="player", entity_id="A")
        entities = {
            "A": ent("A", "party", x=1, y=1),
            "D": ent("D", "hostile", kind="enemy", x=3, y=1),
        }
        # Closed (default) door: LOS blocked → D is APPROXIMATE (within the
        # default radius), no identity — the door blocks sight; awareness is
        # unchanged in code (it inherits the door-aware LOS).
        (closed_item,) = build_awareness(viewer, entities, g)
        self.assertTrue(closed_item["approximate"])
        self.assertNotIn("name", closed_item)
        # Open the door: clear LOS → D is FULL (named, labeled, red).
        g.doors = {"2,1": "O"}
        (item,) = build_awareness(viewer, entities, g)
        self.assertEqual(item["entity_id"], "D")
        self.assertTrue(item["label"])
        self.assertEqual(item["color"], "red")


class TestPlayerTierRadiusBoundary(unittest.TestCase):
    """Chebyshev exactly APPROX_RADIUS → approximate; APPROX_RADIUS+1 → absent."""

    def setUp(self):
        # 4×9 grid: floor everywhere except the wall COLUMN x=2 (and the
        # border). Anchor O at (1,1); targets sit in the floor column x=3,
        # so every O→E line must cross the wall column (no LOS) while the
        # Chebyshev distance is controlled purely by y.
        rows = [["wall"] * 4 for _ in range(9)]
        for y in range(1, 8):
            rows[y] = ["wall", "floor", "wall", "floor"]
        self.grid = make_grid(rows)
        self.viewer = Player(id="p1", name="A", role="player", entity_id="A")

    def test_at_radius_is_approximate(self):
        # E at (3, 1 + APPROX_RADIUS): Bresenham crosses wall (2,3) → no LOS,
        # chebyshev == APPROX_RADIUS exactly → still approximate.
        e = ent("E", "neutral", kind="npc", x=3, y=1 + APPROX_RADIUS)
        (item,) = build_awareness(self.viewer,
                                  {"A": ent("A", "party", x=1, y=1), "E": e},
                                  self.grid)
        self.assertTrue(item["approximate"])
        self.assertEqual(item["entity_id"], "<approx-1>")

    def test_beyond_radius_is_absent(self):
        e = ent("E", "neutral", kind="npc", x=3, y=1 + APPROX_RADIUS + 1)
        items = build_awareness(self.viewer,
                                {"A": ent("A", "party", x=1, y=1), "E": e},
                                self.grid)
        self.assertEqual(items, [])


class TestPlayerTierAnchoring(unittest.TestCase):
    def setUp(self):
        self.grid = tier_grid()
        self.entities = {
            "A": ent("A", "party", x=1, y=4),
            "B": ent("B", "party", kind="player", x=1, y=2),  # LOS from (1,4)
        }

    def test_own_token_never_appears_even_with_grid(self):
        items = build_awareness(
            Player(id="p1", name="Alice", role="player", entity_id="A"),
            self.entities, self.grid)
        self.assertNotIn("A", [i["entity_id"] for i in items])

    def test_deleted_own_entity_sees_nothing(self):
        # The player's own entity was deleted: no anchor → sees nothing
        # (even entities that were previously visible with LOS).
        viewer = Player(id="p1", name="Alice", role="player", entity_id="A")
        without_own = {eid: e for eid, e in self.entities.items() if eid != "A"}
        self.assertEqual(build_awareness(viewer, without_own, self.grid), [])

    def test_anchor_moved_recomputes_tiers(self):
        # Same entity B, two anchors: from O=(1,4) B has clear LOS (full);
        # from O2=(4,4) the line is blocked by wall (3,3) and the distance
        # is cheb 3 → B is APPROXIMATE. The old "previously seen" mechanism
        # would have kept B full; the new model is stateless.
        b = ent("B", "party", kind="player", x=1, y=2)
        o = ent("O", "party", x=1, y=4)
        o2 = ent("O2", "party", x=4, y=4)
        a = Player(id="p1", name="Alice", role="player", entity_id="O")
        (full,) = build_awareness(a, {"O": o, "B": b}, self.grid)
        self.assertEqual(full["entity_id"], "B")
        self.assertTrue(full["label"])  # full with LOS from (1,4)
        a2 = Player(id="p2", name="Bob", role="player", entity_id="O2")
        (approx,) = build_awareness(a2, {"O2": o2, "B": b}, self.grid)
        self.assertTrue(approx["approximate"])  # recomputed, no memory


class TestPlayerTierLosCornerCut(unittest.TestCase):
    """Adversarial regression (LOS corner-cut, awareness check #3a):

    A sight line must not "cut" through the zero-width gap between two wall
    corners.  When a player's LOS to a nearby entity is broken ONLY by such a
    pinch (no wall lies on the Bresenham line itself), the entity must be
    APPROXIMATE — never FULL — even though it is within the approx radius.
    This is the awareness-layer counterpart of the ``has_line_of_sight``
    corner-cut rule (mirroring the movement no-corner-cut rule).
    """

    @classmethod
    def setUpClass(cls):
        # Walls at (1,0) and (0,1) pinch the origin corner.
        rows = [
            ["floor", "wall", "floor", "floor", "floor"],
            ["wall", "floor", "floor", "floor", "floor"],
            ["floor", "floor", "floor", "floor", "floor"],
        ]
        cls.grid = make_grid(rows)

    def _player_entities(self, target):
        o = ent("O", "party", kind="player", x=0, y=0)
        return {"O": o, target.id: target}

    def test_diagonal_pinch_within_radius_is_approximate_not_full(self):
        # Enemy at (1,1): the single diagonal step (0,0)->(1,1) touches the
        # two wall corners (1,0) and (0,1). Chebyshev 1 (within radius) and
        # no wall on the line, but the corner cut blocks LOS -> APPROX.
        e = ent("E", "hostile", kind="enemy", x=1, y=1)
        (item,) = build_awareness(
            Player(id="p", name="A", role="player", entity_id="O"),
            self._player_entities(e), self.grid)
        self.assertTrue(item["approximate"])
        self.assertEqual(item["entity_id"], "<approx-1>")
        self.assertNotIn("name", item)
        self.assertNotIn("color", item)

    def test_diagonal_pinch_within_radius_2_is_approximate_not_full(self):
        # Enemy at (2,2): the first diagonal step of the (0,0)->(2,2) line is
        # the same pinch. Chebyshev 2 (within radius) -> APPROX, not FULL.
        e = ent("E", "hostile", kind="enemy", x=2, y=2)
        (item,) = build_awareness(
            Player(id="p", name="A", role="player", entity_id="O"),
            self._player_entities(e), self.grid)
        self.assertTrue(item["approximate"])
        self.assertNotIn("name", item)

    def test_clear_diagonal_within_radius_is_full(self):
        # Control: an OPEN diagonal (no wall corners to cut) within the radius
        # keeps LOS -> FULL (labeled, named).  This is what a wrong fix
        # (over-blocking every diagonal) would break.
        grid = make_grid([
            ["floor", "floor", "floor"],
            ["floor", "floor", "floor"],
            ["floor", "floor", "floor"],
        ])
        o = ent("O", "party", kind="player", x=0, y=0)
        e = ent("E", "hostile", kind="enemy", x=2, y=2)
        (item,) = build_awareness(
            Player(id="p", name="A", role="player", entity_id="O"),
            {"O": o, "E": e}, grid)
        self.assertFalse(item.get("approximate"))
        self.assertTrue(item["label"])
        self.assertEqual(item["name"], "hostile-E")


# ---------------------------------------------------------------------------
# Per-player awareness radius (GM-adjustable 0–20; docs/design/
# awareness-ring.md §2/§3): the approximate tier uses the VIEWER's own
# radius (default APPROX_RADIUS = 4); LOS (FULL) and the GM view are
# never affected by it.
# ---------------------------------------------------------------------------


class TestPlayerAwarenessRadius(unittest.TestCase):
    """The approximate tier's range is ``viewer.awareness_radius``.

    Grid: a solid wall COLUMN x=2 with open floor columns x=1/x=3 —
    anchor O at (1,1), targets at (3, y) ALWAYS lack line of sight
    (every O→E line crosses the wall column) while the Chebyshev
    distance is controlled purely by y.
    """

    def setUp(self):
        rows = [["wall"] * 4 for _ in range(9)]
        for y in range(1, 8):
            rows[y] = ["wall", "floor", "wall", "floor"]
        self.grid = make_grid(rows)

    def _player(self, radius=None):
        if radius is None:
            return Player(id="p1", name="A", role="player", entity_id="A")
        return Player(id="p1", name="A", role="player", entity_id="A",
                      awareness_radius=radius)

    def _entities(self, y):
        return {"A": ent("A", "party", x=1, y=1),
                "E": ent("E", "neutral", kind="npc", x=3, y=y)}

    def test_default_radius_behavior_unchanged(self):
        # No field given → the default 4: no-LOS at chebyshev EXACTLY 4 is
        # still approximate (as before this feature); at chebyshev 5 it is
        # still invisible.
        viewer = self._player()
        self.assertEqual(viewer.awareness_radius, APPROX_RADIUS)
        (item,) = build_awareness(viewer, self._entities(1 + APPROX_RADIUS),
                                  self.grid)
        self.assertTrue(item["approximate"])
        self.assertEqual(
            build_awareness(viewer, self._entities(1 + APPROX_RADIUS + 1),
                            self.grid),
            [])

    def test_radius_zero_has_no_approximate_tier_but_los_stays_full(self):
        viewer = self._player(0)
        # A no-LOS neighbor at chebyshev 1 is INVISIBLE now (radius 0 =
        # LOS-only perception)…
        self.assertEqual(
            build_awareness(viewer, self._entities(2), self.grid), [])
        # …while a clear-line-of-sight contact stays FULL — the radius
        # never gates sight.
        open_grid = make_grid([["floor"] * 4 for _ in range(4)])
        (full,) = build_awareness(
            viewer,
            {"A": ent("A", "party", x=1, y=1),
             "F": ent("F", "hostile", kind="enemy", x=2, y=1)},
            open_grid)
        self.assertEqual(full["entity_id"], "F")
        self.assertTrue(full["label"])
        self.assertIn("name", full)
        self.assertNotIn("approximate", full)

    def test_radius_twenty_reaches_entity_at_chebyshev_twenty(self):
        # Bigger grid (floors y=1..22): E at (3,21) is chebyshev 20 from
        # O(1,1) with no LOS — approximate at radius 20, absent at 19.
        rows = [["wall"] * 4 for _ in range(24)]
        for y in range(1, 23):
            rows[y] = ["wall", "floor", "wall", "floor"]
        big = make_grid(rows)
        entities = {"A": ent("A", "party", x=1, y=1),
                    "E": ent("E", "neutral", kind="npc", x=3, y=21)}
        (item,) = build_awareness(self._player(20), entities, big)
        self.assertTrue(item["approximate"])
        self.assertEqual(build_awareness(self._player(19), entities, big), [])

    def test_boundary_radius_seven_exactly_seven_approx_eight_invisible(self):
        # Floors y=1..9: E at (3,8) is chebyshev 7, E at (3,9) chebyshev 8
        # (both no LOS). With radius 7: the 7-entity is approximate, the
        # 8-entity is invisible.
        rows = [["wall"] * 4 for _ in range(11)]
        for y in range(1, 10):
            rows[y] = ["wall", "floor", "wall", "floor"]
        big = make_grid(rows)
        viewer = self._player(7)
        (item,) = build_awareness(
            viewer,
            {"A": ent("A", "party", x=1, y=1),
             "E": ent("E", "neutral", kind="npc", x=3, y=8)},
            big)
        self.assertTrue(item["approximate"])
        self.assertEqual(
            build_awareness(
                viewer,
                {"A": ent("A", "party", x=1, y=1),
                 "E": ent("E", "neutral", kind="npc", x=3, y=9)},
                big),
            [])

    def test_gm_ignores_the_radius(self):
        # The GM branch is unchanged: a GM with radius 0 (the most
        # filtering value a player can have) still sees a far no-LOS
        # entity in FULL — never approximate, never filtered.
        gm = Player(id="gm1", name="G", role="gm", entity_id=None,
                    awareness_radius=0)
        items = build_awareness(
            gm,
            {"A": ent("A", "party", x=1, y=1),
             "FAR": ent("FAR", "hostile", kind="enemy", x=3, y=8)},
            self.grid)
        by_id = items_by_id(items)
        self.assertEqual(set(by_id), {"A", "FAR"})  # far entity NOT filtered
        self.assertTrue(by_id["FAR"]["label"])
        self.assertEqual(by_id["FAR"]["name"], "hostile-FAR")
        self.assertNotIn("approximate", by_id["FAR"])

    def test_non_int_radius_falls_back_to_default(self):
        # Defensive: a non-int/None radius (e.g. a corrupted Player) is
        # treated as the default APPROX_RADIUS — chebyshev 4 still approx.
        viewer = self._player()
        viewer.awareness_radius = None  # type: ignore[assignment]
        (item,) = build_awareness(viewer, self._entities(1 + APPROX_RADIUS),
                                  self.grid)
        self.assertTrue(item["approximate"])


class TestPlayerAwarenessRadiusModel(unittest.TestCase):
    """``Player.awareness_radius`` data model: default, to_dict, and the
    from_dict clamp to [0, 20] (missing/invalid → default 4)."""

    def test_constants(self):
        self.assertEqual(AWARENESS_MIN, 0)
        self.assertEqual(AWARENESS_MAX, 20)
        self.assertEqual(APPROX_RADIUS, 4)

    def test_default_and_to_dict(self):
        p = Player(id="p1", name="A", role="player", entity_id="e1")
        self.assertEqual(p.awareness_radius, 4)
        self.assertEqual(p.to_dict()["awareness_radius"], 4)
        p.awareness_radius = 7
        self.assertEqual(p.to_dict()["awareness_radius"], 7)

    def test_from_dict_missing_and_none_default_to_four(self):
        d = {"id": "p1", "name": "A", "role": "player"}
        self.assertEqual(Player.from_dict(d).awareness_radius, 4)
        self.assertEqual(
            Player.from_dict({**d, "awareness_radius": None}).awareness_radius,
            4)

    def test_from_dict_roundtrip_and_clamp(self):
        d = {"id": "p1", "name": "A", "role": "player"}
        for value in (0, 4, 7, 20):
            with self.subTest(value=value):
                p = Player.from_dict({**d, "awareness_radius": value})
                self.assertEqual(p.awareness_radius, value)
        # Out of range is silently CLAMPED on read…
        self.assertEqual(
            Player.from_dict({**d, "awareness_radius": 99}).awareness_radius,
            AWARENESS_MAX)
        self.assertEqual(
            Player.from_dict({**d, "awareness_radius": -3}).awareness_radius,
            AWARENESS_MIN)
        # …and non-numeric garbage falls back to the default.
        self.assertEqual(
            Player.from_dict({**d, "awareness_radius": "abc"}).awareness_radius,
            4)


if __name__ == "__main__":
    unittest.main()
