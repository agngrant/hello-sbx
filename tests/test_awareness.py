"""Awareness overlay tests (stdlib unittest; Iteration 4).

Covers PROJECT.md §5: relation by the target's team, team colors with
explicit-override handling, GM-sees-all (labeled, unmasked) and
player-dots (self excluded, unlabeled).
"""

from __future__ import annotations

import unittest

from app.awareness import build_awareness, overlay_color, relation_of
from app.models import Entity, Player


def ent(eid: str, team: str, kind: str = "player", color: str | None = None,
        x: int = 0, y: int = 0) -> Entity:
    return Entity(id=eid, name=f"{team}-{eid}", kind=kind, team=team,
                  x=x, y=y, color=color)


def items_by_id(items: list[dict]) -> dict[str, dict]:
    return {item["entity_id"]: item for item in items}


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
        gm = Player(id="gm1", name="Gamemaster", role="gm", entity_id="e1")
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
        # The GM's own entity is included, at its position, with its name.
        self.assertEqual(by_id["e1"]["name"], "party-e1")
        self.assertEqual((by_id["e1"]["x"], by_id["e1"]["y"]), (1, 1))

    def test_gm_without_own_entity_still_sees_all(self):
        gm = Player(id="gm1", name="Gamemaster", role="gm", entity_id=None)
        entities = {
            "e1": ent("e1", "party"),
            "e2": ent("e2", "neutral", kind="npc"),
        }
        self.assertEqual(len(build_awareness(gm, entities)), 2)


class TestBuildAwarenessPlayer(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
