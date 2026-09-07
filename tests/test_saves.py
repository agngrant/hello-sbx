"""Save/load tests (save-load spec, backend scope).

Two layers:

* **Bundle I/O** (``app.saves``): write → read round-trip, record fields,
  corrupt/missing handling, delete, list ordering, atomic-overwrite, and
  the real-persistence property (a written file is read back from disk by a
  FRESH in-process load — no in-memory state involved, E8/AC6).
* **Session-level name rebind** (``app.session.GameSession`` with fake
  connections, same harness as ``tests/test_session.py``): matching-name
  rebind at the saved position, orphans stay GM-controlled, duplicate
  owner_name first-join-wins, no-match fresh token (exactly today's
  behavior), and the ``use_map`` roster rebuild that tags loaded entities
  for the rebind.

The module-level ``app.saves.SAVES_DIR`` is redirected to a per-test temp
directory (created lazily by ``save_bundle``, never deleted by it — so the
test cleanup owns it).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import unittest

from app import saves as save_store
from app.models import ENTITY_KINDS, Entity, Grid, Player, TEAMS
from app.session import GameSession
from tests.test_session import FakeConn, attach, drive


def make_grid(rows: list[list[str]], name: str = "Save Test Map") -> Grid:
    height = len(rows)
    width = len(rows[0])
    return Grid(name=name, width=width, height=height, cells=[list(r) for r in rows])


# A 5x5 test grid: a wall column at x=2 with a doorway at (2,2); the saved
# owner's token sits at (1,1), the orphan's at (3,3), a GM NPC at (1,3).
_ROWS = [
    ["wall", "wall", "wall", "wall", "wall"],
    ["wall", "floor", "wall", "wall", "wall"],
    ["wall", "floor", "doorway", "floor", "wall"],
    ["wall", "floor", "wall", "floor", "wall"],
    ["wall", "wall", "wall", "wall", "wall"],
]


def sample_entities() -> list[dict]:
    """The three entities a typical save of the test session would hold."""
    return [
        {"id": "e1", "name": "Alice", "kind": "player", "team": "party",
         "x": 1, "y": 1, "color": None, "owner_name": "Alice"},
        {"id": "e2", "name": "Bob", "kind": "player", "team": "party",
         "x": 3, "y": 3, "color": None, "owner_name": None},  # orphan
        {"id": "e3", "name": "Goblin", "kind": "enemy", "team": "hostile",
         "x": 1, "y": 3, "color": "#f76707", "owner_name": None},
    ]


class SavesIOTestCase(unittest.TestCase):
    """Bundle read/write against a redirected, per-test temp saves dir."""

    def setUp(self) -> None:
        self._orig_dir = save_store.SAVES_DIR
        self._tmp = tempfile.mkdtemp(prefix="saves-test-")
        # E1: the dir is created lazily on first write — remove the
        # mkdtemp-created dir so "missing dir ⇒ empty list" is real.
        os.rmdir(self._tmp)
        save_store.SAVES_DIR = self._tmp

    def tearDown(self) -> None:
        save_store.SAVES_DIR = self._orig_dir
        shutil.rmtree(self._tmp, ignore_errors=True)

    # -- helpers -------------------------------------------------------------

    def write_save(self, label: str = "Act Three — Crypt",
                   grid: Grid | None = None,
                   entities: list[dict] | None = None) -> str:
        grid = grid or make_grid(_ROWS)
        entities = sample_entities() if entities is None else entities
        save_id = save_store.id_for_name(label)
        record = {
            "name": label,
            "map_name": grid.name,
            "created_at": "2025-01-01T12:00:00",
        }
        return save_store.save_bundle(record, grid, entities)

    # -- write / list --------------------------------------------------------

    def test_missing_dir_lists_empty(self):
        # E1: the dir is created lazily on first write; before that, [].
        self.assertEqual(save_store.list_saves(), [])
        self.assertFalse(os.path.isdir(self._tmp))

    def test_save_bundle_writes_file_with_record_and_state(self):
        save_id = self.write_save()
        # The file is on disk and parses (real persistence — read by a
        # separate open, not in-memory state).
        path = os.path.join(self._tmp, f"{save_id}.json")
        self.assertTrue(os.path.isfile(path))
        with open(path, "r", encoding="utf-8") as f:
            bundle = json.load(f)
        # Record fields (spec 4.3) are top-level:
        self.assertEqual(bundle["id"], save_id)
        self.assertEqual(bundle["name"], "Act Three — Crypt")
        self.assertEqual(bundle["map_name"], "Save Test Map")
        self.assertEqual((bundle["width"], bundle["height"]), (5, 5))
        self.assertEqual(bundle["created_at"], "2025-01-01T12:00:00")
        self.assertEqual(bundle["entity_count"], 3)
        # The complete map state:
        self.assertEqual(bundle["grid"]["cells"], [list(r) for r in _ROWS])
        self.assertEqual(bundle["grid"]["name"], "Save Test Map")
        self.assertEqual(bundle["grid"]["image"], None)
        # Every entity carries owner_name (the saved player's name / null):
        self.assertEqual(bundle["entities"], sample_entities())

    def test_list_saves_sorted_created_at_desc_then_id_desc(self):
        save_store.save_bundle(
            {"name": "Old", "created_at": "2024-01-01T00:00:00"},
            make_grid(_ROWS), sample_entities())
        save_store.save_bundle(
            {"name": "New", "created_at": "2025-06-01T00:00:00"},
            make_grid(_ROWS), sample_entities())
        # Same timestamp, two ids → id desc breaks the tie.
        save_store.save_bundle(
            {"name": "Tie-B", "created_at": "2025-01-01T12:00:00"},
            make_grid(_ROWS), sample_entities())
        save_store.save_bundle(
            {"name": "Tie-A", "created_at": "2025-01-01T12:00:00"},
            make_grid(_ROWS), sample_entities())
        listing = save_store.list_saves()
        # created_at desc (New 2025-06 > Tie 2025-01 > Old 2024); ties
        # broken by id desc (tie-b > tie-a).
        self.assertEqual([r["name"] for r in listing],
                         ["New", "Tie-B", "Tie-A", "Old"])
        # Record shape (spec 5.1): exactly these keys.
        self.assertEqual(
            set(listing[0].keys()),
            {"id", "name", "map_name", "width", "height",
             "created_at", "entity_count"})

    def test_save_id_is_slug_and_timestamp(self):
        save_id = self.write_save("My Save!")
        slug, _, ts = save_id.rpartition("-")
        self.assertEqual(slug, "my-save")
        self.assertTrue(ts.isdigit())
        # Fallback slug for a name with no alphanumerics:
        self.assertTrue(save_store.id_for_name("!!!").startswith("save-"))

    def test_overwrite_same_id_replaces_file(self):
        save_id = self.write_save("Conflict")
        # Same id again (E2 "Overwrite"): the file is replaced in place.
        new_grid = make_grid(_ROWS, name="Renamed Map")
        save_store.save_bundle(
            {"id": save_id, "name": "Conflict (v2)",
             "map_name": "Renamed Map", "created_at": "2026-01-01T00:00:00"},
            new_grid, sample_entities()[:1])
        self.assertEqual(len(save_store.list_saves()), 1)
        bundle = save_store.load_bundle(save_id)
        self.assertEqual(bundle[0].name, "Renamed Map")
        self.assertEqual(len(bundle[1]), 1)
        listing = save_store.list_saves()[0]
        self.assertEqual(listing["name"], "Conflict (v2)")
        self.assertEqual(listing["entity_count"], 1)

    def test_same_name_save_is_distinct_save(self):
        # E2 "Save as new": a fresh id per save (timestamped) — two saves
        # with the same label coexist.
        id1 = self.write_save("T1")
        id2 = self.write_save("T1")
        self.assertNotEqual(id1, id2)
        self.assertEqual({r["id"] for r in save_store.list_saves()},
                         {id1, id2})
        self.assertEqual({r["name"] for r in save_store.list_saves()},
                         {"T1"})

    # -- read (round-trip + validation) --------------------------------------

    def test_load_bundle_round_trip(self):
        save_id = self.write_save()
        grid, entities = save_store.load_bundle(save_id)
        self.assertIsInstance(grid, Grid)
        self.assertEqual(grid.name, "Save Test Map")
        self.assertEqual((grid.width, grid.height), (5, 5))
        self.assertEqual(grid.cells, [list(r) for r in _ROWS])
        self.assertEqual(grid.image, None)
        self.assertEqual(entities, sample_entities())
        # owner_name survives the round trip verbatim:
        by_id = {e["id"]: e for e in entities}
        self.assertEqual(by_id["e1"]["owner_name"], "Alice")
        self.assertIsNone(by_id["e2"]["owner_name"])
        self.assertIsNone(by_id["e3"]["owner_name"])

    def test_load_bundle_with_doors_and_safe_round_trip(self):
        # E4/AC12: doors + safe + mixed entities survive byte-for-byte.
        grid = make_grid(_ROWS, name="Doory")
        grid.doors = {"2,2": "O"}
        grid.safe = None
        ents = [
            {"id": "e1", "name": "Alice", "kind": "player", "team": "party",
             "x": 1, "y": 1, "color": None, "owner_name": "Alice"},
            {"id": "e2", "name": "Scribe", "kind": "npc", "team": "neutral",
             "x": 3, "y": 1, "color": "#f76707", "owner_name": None},
        ]
        save_id = self.write_save("Doory", grid, ents)
        g2, e2 = save_store.load_bundle(save_id)
        self.assertEqual(g2.doors, {"2,2": "O"})
        self.assertEqual(e2, ents)
        # The grid is behaviorally identical (the open door is walkable):
        self.assertEqual(g2.door_state_at(2, 2), "O")

    def test_load_missing_save_raises(self):
        with self.assertRaises(ValueError):
            save_store.load_bundle("nope")

    def _raw(self, save_id: str, content: str) -> str:
        # Write an arbitrary (possibly invalid) file straight into the
        # saves dir (recreated first — save_bundle creates it lazily and
        # the corrupt-file tests bypass it).
        os.makedirs(self._tmp, exist_ok=True)
        path = os.path.join(self._tmp, f"{save_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_corrupt_json_raises_and_file_untouched(self):
        # E3/AC10(b): unparseable JSON → ValueError; the file is left on
        # disk untouched (the route answers a clean 404).
        bad_path = self._raw("bad", "{not json")
        with self.assertRaises(ValueError):
            save_store.load_bundle("bad")
        self.assertEqual(open(bad_path).read(), "{not json")
        # The corrupt file is listed, flagged, and the rest of the list is
        # still served (A12: surfaced, not fatal).
        listing = save_store.list_saves()
        self.assertEqual(len(listing), 1)
        self.assertTrue(listing[0]["corrupt"])
        self.assertEqual(listing[0]["id"], "bad")

    def test_not_json_object_raises(self):
        self._raw("arr", "[1, 2, 3]")
        with self.assertRaises(ValueError):
            save_store.load_bundle("arr")

    def test_truncated_bundle_height_mismatch_raises(self):
        # E3/AC10(c): valid JSON, but height disagrees with the cells rows
        # → corrupt, and NO partial registration can happen (the route
        # never reaches the registry — the load fails first).
        self._raw("trunc", json.dumps({
            "id": "trunc", "name": "t", "map_name": "t",
            "width": 5, "height": 4,   # wrong: rows are 5
            "created_at": "2025-01-01T00:00:00", "entity_count": 0,
            "grid": {"name": "t", "width": 5, "height": 4,
                     "cells": _ROWS, "image": None},
            "entities": [],
        }))
        with self.assertRaises(ValueError):
            save_store.load_bundle("trunc")

    def test_entity_on_wall_is_still_loadable(self):
        # E12: an entity parked on a wall (hand-edited bundle) does NOT
        # fail the load — re-placement happens at session-join time (the
        # use_map fallback chain), not at load time.
        ents = sample_entities()
        ents[0]["x"], ents[0]["y"] = 0, 0  # (0,0) is a wall
        save_id = self.write_save("wall-ent", entities=ents)
        grid, loaded = save_store.load_bundle(save_id)
        self.assertEqual((loaded[0]["x"], loaded[0]["y"]), (0, 0))
        self.assertEqual(grid.cells[0][0], "wall")

    def test_validation_rejects_bad_entity_fields(self):
        cases = {
            "bad kind": {**sample_entities()[0], "kind": "goblin"},
            "bad team": {**sample_entities()[0], "team": "blue"},
            "non-int x": {**sample_entities()[0], "x": "one"},
            "bool x": {**sample_entities()[0], "x": True},
            "non-string color": {**sample_entities()[0], "color": 3},
            "bad owner_name": {**sample_entities()[0], "owner_name": 5},
            "long name": {**sample_entities()[0], "name": "x" * 25},
            "duplicate id": None,
        }
        for label, e in cases.items():
            if e is None:
                ents = [sample_entities()[0], sample_entities()[0]]
            else:
                ents = [e]
            with self.subTest(case=label):
                with self.assertRaises(ValueError):
                    save_store.load_bundle(
                        self._write_ents(ents))

    def _write_ents(self, ents: list[dict]) -> str:
        save_id = save_store.id_for_name("case")
        save_store.save_bundle(
            {"name": "case", "created_at": "2025-01-01T00:00:00"},
            make_grid(_ROWS), ents)
        return save_id

    def test_delete_missing_raises(self):
        with self.assertRaises(FileNotFoundError):
            save_store.delete_save("nope")

    def test_delete_removes_only_that_file(self):
        id1 = self.write_save("One")
        id2 = self.write_save("Two")
        save_store.delete_save(id1)
        self.assertEqual([r["id"] for r in save_store.list_saves()], [id2])
        self.assertFalse(os.path.exists(os.path.join(self._tmp,
                                                     f"{id1}.json")))

    # -- real persistence (restart simulation) -------------------------------

    def test_file_survives_and_is_loadable_without_memory(self):
        # The point of the feature (AC6/E8): a save written to disk is
        # loadable afterwards even when ALL in-memory knowledge is gone.
        # We write, then load by a SEPARATE code path that only touches the
        # filesystem — proving the file (not memory) is the source of truth.
        save_id = self.write_save()
        grid, entities = save_store.load_bundle(save_id)  # fresh read
        self.assertEqual(grid.name, "Save Test Map")
        self.assertEqual(len(entities), 3)
        # And the disk file parses standalone (json.load, no app state):
        with open(os.path.join(self._tmp, f"{save_id}.json"),
                  "r", encoding="utf-8") as f:
            standalone = json.load(f)
        self.assertEqual(standalone["id"], save_id)
        self.assertEqual(len(standalone["entities"]), 3)


class RebindGridTest(unittest.TestCase):
    """A fresh session seeded the way a save-loaded map produces one."""

    def make_loaded_session(self, owner_names: dict) -> GameSession:
        """A session whose entities are GM-controlled, tagged with the
        given per-id owner_name (exactly what use_map of a loaded save
        produces)."""
        s = GameSession("t", make_grid(_ROWS))
        s.join(FakeConn(), "GM", "gm")
        specs = [
            ("e1", "Alice", "player", "party", 1, 1),
            ("e2", "Bob", "player", "party", 3, 3),
            ("e3", "Goblin", "enemy", "hostile", 1, 3),
        ]
        for eid, name, kind, team, x, y in specs:
            s.entities[eid] = Entity(
                id=eid, name=name, kind=kind, team=team, x=x, y=y,
                owner=None, owner_name=owner_names.get(eid))
        return s


class TestNameRebind(RebindGridTest):
    """F2/E6/E7/AC4/AC7/AC8: the join rebind rules."""

    def test_matching_name_rebinds_at_saved_position(self):
        s = self.make_loaded_session({"e1": "Alice"})
        conn = FakeConn()
        player, err = s.join(conn, "Alice", "player")
        self.assertIsNone(err)
        # Bound to the SAVED token (not a fresh spawn):
        self.assertEqual(player.entity_id, "e1")
        ent = s.entities["e1"]
        self.assertEqual(ent.owner, player.id)
        self.assertEqual((ent.x, ent.y), (1, 1))  # saved position
        self.assertEqual(ent.name, "Alice")
        # The owner_name tag is cleared (the badge disappears, E7):
        self.assertIsNone(ent.owner_name)
        # Orphans are untouched — still GM-controlled:
        self.assertIsNone(s.entities["e2"].owner)
        self.assertIsNone(s.entities["e3"].owner)

    def test_orphan_stays_gm_controlled(self):
        # Bob's token has owner_name=None (a GM-controlled orphan): Bob
        # joining does NOT rebind it (owner_name must match the name) and
        # Bob gets a fresh token instead (E6 — but here we only assert the
        # orphan is untouched and a fresh spawn happened).
        s = self.make_loaded_session({})  # nothing tagged
        conn = FakeConn()
        player, err = s.join(conn, "Bob", "player")
        self.assertIsNone(err)
        self.assertNotEqual(player.entity_id, "e2")  # not the orphan
        self.assertIsNone(s.entities["e2"].owner)    # orphan stays GM's
        fresh = s.entities[player.entity_id]
        self.assertEqual(fresh.kind, "player")
        self.assertEqual(fresh.team, "party")
        self.assertEqual(fresh.owner, player.id)
        # Fresh token landed on a free floor cell (today's spawn behavior):
        self.assertIn(s.grid.cells[fresh.y][fresh.x], ("floor", "doorway"))
        self.assertNotIn((fresh.x, fresh.y),
                         {(e.x, e.y) for e in s.entities.values() if e.id != fresh.id})

    def test_no_match_gets_fresh_token_exactly_as_today(self):
        # E6/AC8: a name matching NO saved entity gets a fresh token; the
        # saved entities are untouched.
        s = self.make_loaded_session({"e1": "Alice"})
        before = {eid: (e.x, e.y, e.owner) for eid, e in s.entities.items()}
        conn = FakeConn()
        player, err = s.join(conn, "Zed", "player")
        self.assertIsNone(err)
        self.assertEqual(player.entity_id, "e4")  # next id after e1..e3
        self.assertEqual(s.entities["e4"].kind, "player")
        self.assertEqual(s.entities["e4"].owner, player.id)
        after = {eid: (e.x, e.y, e.owner) for eid, e in s.entities.items()
                 if eid != "e4"}
        self.assertEqual(after, before)

    def test_duplicate_owner_name_first_join_wins(self):
        # E5/E7/AC7: two entities share owner_name "Alice" → the FIRST in
        # dict order binds; the second stays GM-controlled with the tag
        # intact; exactly one entity has owner == the player id.
        s = self.make_loaded_session({"e1": "Alice", "e3": "Alice"})
        conn = FakeConn()
        player, err = s.join(conn, "Alice", "player")
        self.assertIsNone(err)
        self.assertEqual(player.entity_id, "e1")
        self.assertEqual(s.entities["e1"].owner, player.id)
        self.assertIsNone(s.entities["e1"].owner_name)  # winner's tag cleared
        self.assertIsNone(s.entities["e3"].owner)       # orphan stays null
        self.assertEqual(s.entities["e3"].owner_name, "Alice")  # tag intact
        owners = [e.owner for e in s.entities.values()]
        self.assertEqual(owners.count(player.id), 1)  # no double-bind (A13)

    def test_name_matching_is_case_sensitive(self):
        # A9: "alice" does NOT bind the "Alice"-tagged entity.
        s = self.make_loaded_session({"e1": "Alice"})
        conn = FakeConn()
        player, err = s.join(conn, "alice", "player")
        self.assertIsNone(err)
        self.assertNotEqual(player.entity_id, "e1")
        self.assertIsNone(s.entities["e1"].owner)
        self.assertEqual(s.entities["e1"].owner_name, "Alice")

    def test_reattach_beats_rebind(self):
        # A connected player's same-name rejoin re-attaches (existing
        # behavior) and does NOT rebind anything — even when an
        # unclaimed entity carries that name (the re-attach pass runs
        # first).
        s = self.make_loaded_session({"e1": "Alice"})
        first = FakeConn()
        p1, err = s.join(first, "Alice", "player")
        self.assertIsNone(err)
        self.assertEqual(p1.entity_id, "e1")
        # Reconnect with a NEW connection (re-attach):
        second = FakeConn()
        p2, err = s.join(second, "Alice", "player")
        self.assertIsNone(err)
        self.assertIs(p2, p1)  # same Player record, re-attached
        self.assertEqual(p2.entity_id, "e1")
        # And a fresh name still gets a fresh token (nothing was re-bound):
        third = FakeConn()
        p3, err = s.join(third, "Bob", "player")
        self.assertIsNone(err)
        self.assertNotEqual(p3.entity_id, "e2")
        self.assertIsNone(s.entities["e2"].owner)

    def test_rebind_survives_snapshot_and_welcome(self):
        # The rebind is visible in the per-viewer snapshot: the GM's
        # entities list shows owner = the new player id; the joiner's
        # you_entity is the saved token at its saved position (the "YOU"
        # ring is driven purely by you.entity_id, spec 6.1).
        s = self.make_loaded_session({"e1": "Alice"})
        gm_s = FakeConn()
        gm, _ = s.join(gm_s, "GM", "gm")
        attach(s, gm_s)
        conn = FakeConn()
        player, err = s.join(conn, "Alice", "player")
        self.assertIsNone(err)
        gm_state = s.state_for(gm)
        alice_item = next(e for e in gm_state["entities"] if e["id"] == "e1")
        self.assertEqual(alice_item["owner"], player.id)
        # to_dict stays frozen: NO owner_name key on the wire (A10/AC16):
        self.assertNotIn("owner_name", alice_item)
        # The joiner's own snapshot: you_entity = the saved token at its
        # saved position (the YOU ring's data source):
        joiner_state = s.state_for(player)
        self.assertEqual(joiner_state["you_entity"]["id"], "e1")
        self.assertEqual(joiner_state["you_entity"]["x"], 1)
        self.assertEqual(joiner_state["you_entity"]["y"], 1)
        # And the welcome frame the joiner is sent (welcome_for = the
        # payload _announce_join broadcasts on a successful join) carries
        # you.entity_id = the saved token's id:
        welcome = s.welcome_for(player)
        self.assertEqual(welcome["type"], "welcome")
        self.assertEqual(welcome["you"]["entity_id"], "e1")
        self.assertEqual(welcome["you"]["id"], player.id)

    def test_rebind_welcome_carries_rebound_flag(self):
        # BUG-014: the client's "your character ... has been restored." toast
        # can only fire when the server tells the client the join was a save
        # rebind — a fresh spawn and a rebind produce byte-identical
        # you/you_entity/players on the frozen wire. That signal is the
        # ADDITIVE welcome-only you.rebound flag (§7.5): TRUE on a
        # name-matching rebind, absent/false on a fresh spawn and on a live
        # same-name re-attach (which re-uses the existing Player record).
        s = self.make_loaded_session({"e1": "Alice"})
        gm_s = FakeConn()
        gm, _ = s.join(gm_s, "GM", "gm")
        attach(s, gm_s)
        # A name matching an unclaimed saved token -> rebind -> flag set.
        alice, err = s.join(FakeConn(), "Alice", "player")
        self.assertIsNone(err)
        self.assertEqual(s.welcome_for(alice)["you"]["rebound"], True)
        # A fresh name (no saved match) -> fresh spawn -> flag not set.
        zed, err = s.join(FakeConn(), "Zed", "player")
        self.assertIsNone(err)
        self.assertFalse(s.welcome_for(zed)["you"]["rebound"])
        # A connected player's same-name re-attach is NOT a rebind.
        re, err = s.join(FakeConn(), "Alice", "player")
        self.assertIsNone(err)
        self.assertIs(re, alice)
        self.assertFalse(s.welcome_for(re)["you"]["rebound"])
        # GM (no token): flag is a no-op (false).
        self.assertFalse(s.welcome_for(gm)["you"]["rebound"])


class TestUseMapLoadRoster(RebindGridTest):
    """§5.3/§6.1: use_map of a save-loaded map rebuilds the roster fresh,
    GM-controlled, tagged with owner_name — and then the join rebinds."""

    def test_use_map_registers_loaded_entities_and_rebinds_on_join(self):
        from app.main import maps_registry

        grid = make_grid(_ROWS, name="Loaded")
        saved = [
            {"id": "e1", "name": "Alice", "kind": "player", "team": "party",
             "x": 1, "y": 1, "color": None, "owner_name": "Alice"},
            {"id": "e2", "name": "Bob", "kind": "player", "team": "party",
             "x": 3, "y": 3, "color": None, "owner_name": None},
            {"id": "e3", "name": "Goblin", "kind": "enemy", "team": "hostile",
             "x": 1, "y": 3, "color": None, "owner_name": None},
        ]
        map_id = "smap-load-test"
        maps_registry[map_id] = {
            "grid": grid, "entities": {}, "players": {},
            "loaded_entities": saved,
        }
        try:
            s = GameSession("t", make_grid(_ROWS, name="Old"))
            gm_s = FakeConn()
            gm, _ = s.join(gm_s, "GM", "gm")
            attach(s, gm_s)
            # A connected player with a token on the OLD map:
            p_old = FakeConn()
            old_player, _ = s.join(p_old, "Old", "player")
            attach(s, p_old)
            self.assertIsNotNone(old_player.entity_id)

            reply = drive(s, gm_s, {"type": "use_map", "map_id": map_id})
            self.assertIsNone(reply)
            # The loaded roster is in place: fresh, GM-controlled, tagged.
            # (The connected player's token already holds id "e1" from the
            # old map, so the loaded "e1" is re-id'd — rebind is by NAME,
            # never by id; e2/e3 have no collision and keep their ids.)
            for d in saved:
                if d["id"] == "e1":
                    alice_ents = [e for e in s.entities.values()
                                  if e.owner_name == "Alice"]
                    self.assertEqual(len(alice_ents), 1)
                    ent = alice_ents[0]
                    alice_id = ent.id
                else:
                    ent = s.entities.get(d["id"])
                self.assertIsNotNone(ent, f"{d['id']} not rebuilt")
                self.assertIsNone(ent.owner)
                self.assertEqual(ent.owner_name, d["owner_name"])
                self.assertEqual((ent.x, ent.y), (d["x"], d["y"]))
            # The connected player's token was KEPT (re-placed if needed):
            kept = s.entities.get(old_player.entity_id)
            self.assertIsNotNone(kept, "connected player's token dropped")
            self.assertEqual(kept.owner, old_player.id)
            # Now the rebind: Alice joins → binds the saved token (by name):
            conn = FakeConn()
            alice, err = s.join(conn, "Alice", "player")
            self.assertIsNone(err)
            self.assertEqual(alice.entity_id, alice_id)
            self.assertEqual(s.entities[alice_id].owner, alice.id)
            self.assertIsNone(s.entities[alice_id].owner_name)
            # Orphans stay GM-controlled:
            self.assertIsNone(s.entities["e2"].owner)
            self.assertIsNone(s.entities["e3"].owner)
        finally:
            maps_registry.pop(map_id, None)

    def test_use_map_keeps_connected_tokens_and_avoids_id_collision(self):
        # E10: a connected player's token id (e1) collides with a loaded
        # entity id (e1) → the loaded one is re-id'd (e1-2); the connected
        # player keeps its token; the rebind is by NAME, not id.
        from app.main import maps_registry

        grid = make_grid(_ROWS, name="Loaded2")
        saved = [
            {"id": "e1", "name": "NewAlice", "kind": "player", "team": "party",
             "x": 1, "y": 1, "color": None, "owner_name": "NewAlice"},
        ]
        map_id = "smap-load-collide"
        maps_registry[map_id] = {
            "grid": grid, "entities": {}, "players": {},
            "loaded_entities": saved,
        }
        try:
            s = GameSession("t", make_grid(_ROWS, name="Old2"))
            gm_s = FakeConn()
            gm, _ = s.join(gm_s, "GM", "gm")
            attach(s, gm_s)
            p_old = FakeConn()
            old_player, _ = s.join(p_old, "Keep", "player")
            attach(s, p_old)
            self.assertEqual(old_player.entity_id, "e1")  # first token = e1

            reply = drive(s, gm_s, {"type": "use_map", "map_id": map_id})
            self.assertIsNone(reply)
            # The connected player kept e1:
            self.assertIn("e1", s.entities)
            self.assertEqual(s.entities["e1"].owner, old_player.id)
            # The loaded token was re-id'd to e1-2 and tagged for rebind:
            self.assertIn("e1-2", s.entities)
            self.assertEqual(s.entities["e1-2"].owner_name, "NewAlice")
            self.assertIsNone(s.entities["e1-2"].owner)
            # NewAlice joins → binds the re-id'd token:
            conn = FakeConn()
            new_alice, err = s.join(conn, "NewAlice", "player")
            self.assertIsNone(err)
            self.assertEqual(new_alice.entity_id, "e1-2")
            self.assertEqual(s.entities["e1-2"].owner, new_alice.id)
        finally:
            maps_registry.pop(map_id, None)


class TestSaveSnapshotOwnership(unittest.TestCase):
    """The save-time owner_name computation (spec 4.3) — the exact
    transform the POST /api/saves route applies under the lock."""

    def _owner_name_map(self, s: GameSession) -> dict:
        out = {}
        for e in s.entities.values():
            p = s.players.get(e.owner) if e.owner else None
            out[e.id] = p.name if p else None
        return out

    def test_owner_name_is_controlling_player_name(self):
        s = GameSession("t", make_grid(_ROWS))
        gm, _ = s.join(FakeConn(), "GM", "gm")
        alice, _ = s.join(FakeConn(), "Alice", "player")
        s.entities["e9"] = Entity(
            id="e9", name="Goblin", kind="enemy", team="hostile",
            x=3, y=1, owner=None)
        mapping = self._owner_name_map(s)
        # Alice's token → "Alice" (the player's name, not the entity's).
        self.assertEqual(mapping[alice.entity_id], "Alice")
        # GM-controlled → None:
        self.assertIsNone(mapping["e9"])

    def test_owner_name_null_when_player_id_stale(self):
        # A stale player id (owner no longer exists) → None, never a crash.
        s = GameSession("t", make_grid(_ROWS))
        s.join(FakeConn(), "GM", "gm")
        s.entities["e1"] = Entity(
            id="e1", name="Ghost", kind="npc", team="neutral",
            x=1, y=1, owner="p404")  # no such player
        self.assertIsNone(self._owner_name_map(s)["e1"])


if __name__ == "__main__":
    unittest.main()
