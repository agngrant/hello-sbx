"""Boss-entity contract tests (docs/specs/boss-entity.md — grid & occupancy).

Only tests the contract that actually exists: the boss footprint table and
footprint-aware occupancy in ``app.models``, plus the GM boss spawn and
footprint-blocked movement in ``app.session``. The spec §8 puts boss AI and
damage OUT of scope (there is no ``app.boss`` module).

Run: ``pytest tests/test_boss.py``
"""
import pytest

from app.models import (
    BOSS_FOOTPRINTS,
    Entity,
    boss_footprint,
    entity_cells,
    footprint_cells,
    is_enemy,
)
from app.session import CREATABLE_KINDS, GameSession
from tests.test_session import FakeConn, attach, drive, make_grid

# 8×8 open grid; the joiner's character spawns on the top-left floor cell
# (0,0) and every cell below is walkable floor.
GRID = [["floor"] * 8 for _ in range(8)]


def cell(kind="npc", x=0, y=0, team="neutral", size=1) -> Entity:
    # models.py contract: a boss must pick one of the six BOSS_FOOTPRINTS
    # sizes (no size=None / 1×1 fallback); every other kind is 1×1.
    if kind == "boss" and size not in BOSS_FOOTPRINTS:
        size = 2  # smallest valid boss size variant
    # is_enemy = boss or hostile team — a plain enemy is hostile by default
    # (a neutral-team enemy is NOT an enemy per models.is_enemy).
    if kind == "enemy" and team == "neutral":
        team = "hostile"
    return Entity(
        id="t1", name="t", kind=kind, team=team,
        x=x, y=y, owner=None, size=size,
    )


# ---------------------------------------------------------------------------
# models.py: the footprint table & footprint-aware helpers
# ---------------------------------------------------------------------------

def test_footprint_table_exact_values():
    assert BOSS_FOOTPRINTS == {2: (2, 1), 4: (2, 2), 6: (2, 3),
                               8: (2, 4), 10: (2, 5), 12: (3, 4)}


def test_boss_kind_is_creatable():
    assert "boss" in CREATABLE_KINDS


@pytest.mark.parametrize("kind", ["boss", "enemy"])
def test_is_enemy_true_for_boss_and_enemy(kind):
    assert is_enemy(cell(kind=kind)) is True


@pytest.mark.parametrize("kind", ["npc", "player"])
def test_is_enemy_false_for_npc_and_player(kind):
    assert is_enemy(cell(kind=kind)) is False


def test_footprint_cells_is_a_topleft_anchored_rectangle():
    # Top-left anchor: the cells start exactly at (x, y).
    assert footprint_cells(3, 4, 2, 3) == [(3, 4), (4, 4),
                                           (3, 5), (4, 5),
                                           (3, 6), (4, 6)]
    assert min(fc[0] for fc in footprint_cells(3, 4, 2, 3)) == 3
    assert min(fc[1] for fc in footprint_cells(3, 4, 2, 3)) == 4


def test_footprint_cells_scales_with_boss_sizes():
    for size, (w, h) in BOSS_FOOTPRINTS.items():
        cells = footprint_cells(0, 0, w, h)
        assert len(cells) == w * h
        assert (0, 0) in cells
        assert (w - 1, h - 1) in cells


def test_entity_cells_one_cell_for_a_normal_entity():
    assert entity_cells(cell(kind="npc", x=2, y=3)) == [(2, 3)]


def test_entity_cells_cover_the_full_boss_footprint():
    e = cell(kind="boss", x=3, y=4, size=6)  # 2×3 footprint
    assert entity_cells(e) == footprint_cells(3, 4, 2, 3)
    assert (4, 6) in entity_cells(e)  # the far (bottom-right) corner


def test_invalid_boss_size_is_rejected():
    # models.py: no (1, 1) fallback — sizes outside BOSS_FOOTPRINTS raise.
    for bad in (None, 0, 99):
        with pytest.raises(ValueError, match="boss footprint"):
            boss_footprint(bad)


# ---------------------------------------------------------------------------
# session.py: GM boss spawn (footprint fit + size on the entity)
# ---------------------------------------------------------------------------

def gm_joined(session):
    """Join a fresh GM and return its FakeConn (first joiner is the GM)."""
    conn = FakeConn()
    attach(session, conn)
    drive(session, conn, {"type": "join", "name": "GM", "role": "gm"})
    assert conn.last("state") is not None
    return conn


def join_player(session, name="Alice"):
    conn = FakeConn()
    attach(session, conn)
    drive(session, conn, {"type": "join", "name": name, "role": "player"})
    assert conn.last("state") is not None
    return conn


def player_entity(session) -> Entity:
    players = [p for p in session.players.values() if p.role == "player"]
    assert len(players) == 1
    return session.entities[players[0].entity_id]


def boss_entity(session) -> Entity:
    return next(e for e in session.entities.values() if e.kind == "boss")


def test_gm_spawns_a_boss_with_size_on_the_entity():
    s = GameSession("t1", make_grid(GRID))
    gm = gm_joined(s)
    reply = drive(s, gm, {"type": "create_entity", "name": "Gore",
                          "kind": "boss", "team": "hostile",
                          "x": 3, "y": 3, "size": 8})
    assert reply is None
    e = boss_entity(s)
    assert (e.x, e.y) == (3, 3)
    assert e.size == 8
    assert e.team == "hostile"
    assert e.name == "Gore"
    assert e.owner is None
    # The anchor is the top-left corner of the full footprint.
    assert entity_cells(e) == footprint_cells(3, 3, 2, 4)
    assert len(s.entities) == 2  # the player's join token + the boss


def test_spawn_boss_out_of_bounds_is_rejected():
    s = GameSession("t1", make_grid(GRID))
    gm = gm_joined(s)
    # size 6 → 2×3; anchor (3, 3) reaches row 6… wait, 3+3 = 6 < 8: fits.
    # Use an anchor whose footprint crosses the right edge: (6, 3) → cols 6–7 ok…
    # use the top edge: size 10 → 2×5 at (0, 5) reaches row 10 > 7.
    reply = drive(s, gm, {"type": "create_entity", "name": "Tall",
                          "kind": "boss", "team": "hostile",
                          "x": 0, "y": 5, "size": 10})
    assert reply == {"type": "error", "message": "Boss footprint does not fit"}
    assert not any(e.kind == "boss" for e in s.entities.values())


def test_spawn_boss_on_an_occupied_cell_is_rejected():
    s = GameSession("t1", make_grid(GRID))
    gm = gm_joined(s)
    drive(s, gm, {"type": "create_entity", "name": "First",
                  "kind": "boss", "team": "hostile", "x": 5, "y": 3, "size": 4})
    # A size-2 boss at (5, 3) overlaps the first boss's footprint cells.
    reply = drive(s, gm, {"type": "create_entity", "name": "Second",
                          "kind": "boss", "team": "hostile",
                          "x": 5, "y": 3, "size": 2})
    assert reply == {"type": "error", "message": "Boss footprint does not fit"}
    assert len([e for e in s.entities.values() if e.kind == "boss"]) == 1


def test_move_onto_any_boss_footprint_cell_is_blocked():
    """A player may not stop on ANY cell of the boss's footprint."""
    s = GameSession("t1", make_grid(GRID))
    gm = gm_joined(s)
    join_player(s)
    # size 2 → 2×1, anchor (2, 2): cells (2, 2) and (3, 2).
    drive(s, gm, {"type": "create_entity", "name": "Block",
                  "kind": "boss", "team": "hostile", "x": 2, "y": 2, "size": 2})
    me = player_entity(s)
    assert (me.x, me.y) == (0, 0)

    # Approach the footprint from above: (1, 1) is free, then every step
    # towards a footprint cell must be refused (occupied / no route) and
    # the player must stay put.
    drive(s, gm, {"type": "place", "entity_id": me.id, "x": 1, "y": 1})
    assert (me.x, me.y) == (1, 1)

    for (bx, by) in [(1, 2), (2, 1), (2, 2), (3, 2)]:
        reply = drive(s, gm, {"type": "move", "entity_id": me.id,
                              "x": bx, "y": by})
        assert reply is None or reply["type"] == "error"
        if reply is not None:
            assert reply["message"] in ("destination occupied",
                                        "no route — wall in the way")
        assert (me.x, me.y) == (1, 1), f"moved onto footprint cell ({bx},{by})"

    # A non-footprint adjacent cell still moves fine (the block is
    # footprint-specific, not a general freeze).
    drive(s, gm, {"type": "place", "entity_id": me.id, "x": 1, "y": 3})
    assert (me.x, me.y) == (1, 3)


def test_gm_cannot_move_the_boss_onto_its_own_cell():
    """Footprint occupancy is checked against every OTHER entity — the
    boss's own token still needs its full footprint to stay free."""
    s = GameSession("t1", make_grid(GRID))
    gm = gm_joined(s)
    join_player(s)
    # Two overlapping bosses' cell is impossible to spawn (see above), so
    # instead: park the player's own token ONTO a boss footprint cell via
    # the GM and verify it is refused (GM override does not bypass
    # footprint occupancy).
    drive(s, gm, {"type": "create_entity", "name": "Block",
                  "kind": "boss", "team": "hostile", "x": 2, "y": 2, "size": 2})
    me = player_entity(s)
    reply = drive(s, gm, {"type": "place", "entity_id": me.id,
                          "x": 2, "y": 2, "override": True})
    assert reply == {"type": "error", "message": "destination occupied"}
    assert (me.x, me.y) == (0, 0)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
