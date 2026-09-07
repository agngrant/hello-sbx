"""Save/load persistence (save-load spec §4) — stdlib ``json``/``os`` only.

A save is ONE self-contained JSON bundle in ``saves/<id>.json`` under the
repo root (spec F1/F3, A6): the save *record* (``id``, ``name``,
``map_name``, ``width``, ``height``, ``created_at``, ``entity_count``) plus
the COMPLETE map state — the grid (name/width/height/cells/image + the
recorded ``doors``/``safe`` objects) and every entity, each carrying
``owner_name``: the saved controlling player's NAME (the stable identity
across restarts, F2) or ``null`` for a GM-controlled token.

* **Write** (:func:`save_bundle`) is atomic — temp file + ``os.replace`` —
  so a crashed write can never leave a half-written ``.json`` (A6).
  Overwriting an existing id replaces that file (E2).
* **Read** (:func:`load_bundle`) validates the WHOLE bundle (§4.3); any
  violation raises :class:`ValueError`, which the REST layer answers with a
  clean ``404 {"error": "save not found: <id>"}`` (E3/A12: corrupt files
  are surfaced, never fatal; the file is left on disk untouched).
* **List** (:func:`list_saves`) scans ``saves/*.json`` — the top-level keys
  of each file ARE the record; a missing directory ⇒ empty list (E1), a
  file that fails to parse is listed with ``"corrupt": true`` (A12).

Loading a bundle NEVER touches a live session (F1): it hands back a fresh
:class:`~app.models.Grid` + the entity list; registering the map in
``maps_registry`` and the GM's ``use_map`` are the route/session's job.
The image FILE is not persisted — only its source name (A7).

All functions are synchronous and safe to call from the FastAPI route
threadpool (file I/O is tiny; no locking beyond the atomic replace).
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from app.models import ENTITY_KINDS, TEAMS, Grid

# ---------------------------------------------------------------------------
# Location (spec §4.1: REPO_ROOT/saves, i.e. the repo root's sibling of app/)
# ---------------------------------------------------------------------------

#: Repo root = the parent of the ``app/`` package.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: One JSON file per save; created lazily on first write, never deleted.
#: (Module-global on purpose: tests redirect it to a temp dir.)
SAVES_DIR = os.path.join(REPO_ROOT, "saves")

# A11: grid bounds (the upload/generate caps); a bundle with out-of-range
# dimensions is corrupt (E3).
MIN_EDGE = 1
MAX_EDGE = 60

# Max length of the display/lobby name (§4.3 entity validation).
MAX_ENTITY_NAME = 24

# A save id is "<slug>-<ts>" (spec §4.2); the slug is lowercase alnum runs
# joined by "-", max 32 chars, min 1 (fallback "save"). Only ids matching
# this charset are ever accepted for load/delete (keeps the REST path param
# a bare filename — no traversal).
_ID_SLUG_RE = re.compile(r"[^a-z0-9]+")
_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _slug(name: str) -> str:
    """The save-id slug: lowercase, non-alphanumerics → ``-``, max 32
    chars, min 1 (fallback ``save``) — the same convention as
    ``app.main.slug_map_id`` (spec §4.2)."""
    slug = _ID_SLUG_RE.sub("-", (name or "").lower()).strip("-")
    return slug[:32] or "save"


def id_for_name(name: str) -> str:
    """``"<slug>-<ts>"`` — a fresh, timestamped save id (spec §4.2)."""
    return f"{_slug(name)}-{int(time.time())}"


def fresh_save_id(name: str) -> str:
    """A save id that does NOT collide with an existing save file (E2
    "Save as new" always yields a distinct row, even when two saves land
    in the same wall-clock second): the base ``<slug>-<ts>`` id bumped on
    its timestamp until the file name is free."""
    save_id = id_for_name(name)
    slug, _, ts = save_id.rpartition("-")
    try:
        ts = int(ts)
    except ValueError:
        ts = int(time.time())
    while os.path.exists(os.path.join(SAVES_DIR, f"{slug}-{ts}.json")):
        ts += 1
    return f"{slug}-{ts}"


def _validate_id(save_id: Any) -> None:
    """Raise :class:`ValueError` unless ``save_id`` is a bare, safe filename
    stem (never ``.``/``..``, never containing a path separator)."""
    if (
        not isinstance(save_id, str)
        or not save_id
        or save_id in (".", "..")
        or not _ID_RE.match(save_id)
    ):
        raise ValueError(f"save not found: {save_id!s}")


def _save_path(save_id: str) -> str:
    _validate_id(save_id)
    return os.path.join(SAVES_DIR, f"{save_id}.json")


def _now_iso() -> str:
    """A16: ISO-8601, local time, second precision."""
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


def list_saves() -> list[dict]:
    """The save records, newest first (``created_at`` desc, then ``id``
    desc) — derived by scanning ``saves/*.json`` (spec §4.1: the file's
    top-level keys ARE the record; no manifest, no in-memory index).

    A missing directory ⇒ ``[]`` (E1). A file that is not a JSON object is
    listed with ``"corrupt": true`` and ``None`` fields (A12 — the UI shows
    ⚠ + Delete only); the file is left on disk.
    """
    if not os.path.isdir(SAVES_DIR):
        return []
    out: list[dict[str, Any]] = []
    for fn in sorted(os.listdir(SAVES_DIR)):
        if not fn.endswith(".json"):
            continue
        save_id = fn[: -len(".json")]
        rec: Any = None
        try:
            with open(os.path.join(SAVES_DIR, fn), "r", encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, ValueError):
            rec = None
        if not isinstance(rec, dict):
            out.append({
                "id": save_id,
                "name": None,
                "map_name": None,
                "width": None,
                "height": None,
                "created_at": None,
                "entity_count": None,
                "corrupt": True,
            })
            continue
        out.append({
            "id": save_id,
            "name": rec.get("name"),
            "map_name": rec.get("map_name"),
            "width": rec.get("width"),
            "height": rec.get("height"),
            "created_at": rec.get("created_at"),
            "entity_count": rec.get("entity_count"),
        })
    # Newest first: created_at desc (ISO-8601 sorts chronologically),
    # then id desc (spec §5.1). Missing timestamps sort last.
    out.sort(
        key=lambda r: (str(r.get("created_at") or ""), str(r.get("id") or "")),
        reverse=True,
    )
    return out


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def save_bundle(record: dict[str, Any], grid: Grid, entities: list[dict[str, Any]]) -> str:
    """Write ONE save bundle (spec §4.3) and return the save id.

    ``record`` carries the record fields (``name``/``map_name``/
    ``created_at`` and, for an explicit overwrite, ``id`` — E2); the
    bundle's ``width``/``height``/``entity_count`` are always taken from
    the actual grid/entities so the file can never disagree with itself.
    ``entities`` are the entity wire dicts, each already carrying
    ``owner_name`` (the saved controlling player's name or ``None``).

    The write is atomic (temp file in the same directory + ``os.replace``);
    saving an existing id replaces that file (E2 "Overwrite").
    """
    save_id = record.get("id")
    if not isinstance(save_id, str) or not save_id:
        # No explicit overwrite id: generate a FRESH id (bumped so a
        # same-second re-save never silently replaces the previous save —
        # E2 "Save as new" always yields a distinct row).
        save_id = fresh_save_id(record.get("name") or "")
    grid_dict = grid.to_dict()
    bundle: dict[str, Any] = {
        "id": save_id,
        "name": record.get("name"),
        "map_name": record.get("map_name", grid_dict.get("name")),
        "width": grid_dict["width"],
        "height": grid_dict["height"],
        "created_at": record.get("created_at") or _now_iso(),
        "entity_count": len(entities),
        "grid": grid_dict,
        "entities": entities,
    }
    os.makedirs(SAVES_DIR, exist_ok=True)  # created lazily on first write
    path = _save_path(save_id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return save_id


# ---------------------------------------------------------------------------
# Read (validate everything — any violation ⇒ ValueError ⇒ REST 404)
# ---------------------------------------------------------------------------


def _as_strict_int(value: Any) -> bool:
    """True iff ``value`` is a real int (bools rejected)."""
    return not isinstance(value, bool) and isinstance(value, int)


def _validated_grid(bundle: dict[str, Any]) -> Grid:
    """Rebuild + validate the bundle's grid (spec §4.3).

    ``width``/``height`` must be ints in 1–60 (A11); ``cells`` a
    ``height``×``width`` matrix over ``floor|wall|doorway``; ``doors``/
    ``safe`` keys must be well-formed in-bounds ``"<x>,<y>"`` on a
    ``doorway`` cell with a valid state char — exactly the
    ``Grid.__post_init__`` rules (the legacy safe ``"C"`` → ``"U"``
    coercion applies via ``Grid.from_dict``, as on the wire).
    """
    g = bundle.get("grid")
    if not isinstance(g, dict):
        raise ValueError("bundle has no grid object")
    w, h = g.get("width"), g.get("height")
    if not _as_strict_int(w) or not (MIN_EDGE <= w <= MAX_EDGE):
        raise ValueError(f"bundle grid width {w!r} is not an int in 1-60")
    if not _as_strict_int(h) or not (MIN_EDGE <= h <= MAX_EDGE):
        raise ValueError(f"bundle grid height {h!r} is not an int in 1-60")
    cells = g.get("cells")
    if not isinstance(cells, list) or len(cells) != h:
        raise ValueError(f"bundle grid cells must be a {h}-row matrix")
    for row in cells:
        if not isinstance(row, list) or len(row) != w:
            raise ValueError(f"bundle grid rows must be {w} cells wide")
    try:
        grid = Grid.from_dict(g)  # validates cells/doors/safe (__post_init__)
    except (ValueError, KeyError, TypeError):
        raise ValueError("bundle grid is invalid") from None
    if grid.width != w or grid.height != h:
        raise ValueError("bundle grid dimensions disagree with cells")
    return grid


def _validated_entities(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate the bundle's entity list (spec §4.3).

    Each entity needs: a non-empty string ``id`` (unique across the
    bundle), a string ``name`` ≤ 24 chars, ``kind`` in
    ``player|npc|enemy|gm_character`` (legacy tolerated), ``team`` in
    ``party|neutral|hostile``, int ``x``/``y``, and ``color``/``owner_name``
    string-or-null. Position BOUNDS are NOT a load failure: an entity on a
    wall or out of bounds is re-placed when it joins the session (E12 —
    the ``use_map`` fallback chain), so a hand-edited bundle never fails
    the load for that reason.
    """
    ents = bundle.get("entities", [])
    if ents is None:
        ents = []
    if not isinstance(ents, list):
        raise ValueError("bundle entities must be a list")
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for e in ents:
        if not isinstance(e, dict):
            raise ValueError("bundle entity is not an object")
        eid = e.get("id")
        if not isinstance(eid, str) or not eid:
            raise ValueError("bundle entity id must be a non-empty string")
        if eid in seen_ids:
            raise ValueError(f"duplicate bundle entity id {eid!r}")
        seen_ids.add(eid)
        name = e.get("name")
        if not isinstance(name, str) or not name or len(name) > MAX_ENTITY_NAME:
            raise ValueError("bundle entity name must be a string of 1-24 chars")
        kind = e.get("kind")
        if kind not in ENTITY_KINDS:
            raise ValueError(f"bundle entity kind {kind!r} is invalid")
        team = e.get("team")
        if team not in TEAMS:
            raise ValueError(f"bundle entity team {team!r} is invalid")
        x, y = e.get("x"), e.get("y")
        if not _as_strict_int(x) or not _as_strict_int(y):
            raise ValueError("bundle entity x/y must be integers")
        color = e.get("color")
        if color is not None and not isinstance(color, str):
            raise ValueError("bundle entity color must be a string or null")
        owner_name = e.get("owner_name")
        if owner_name is not None and not isinstance(owner_name, str):
            raise ValueError("bundle entity owner_name must be a string or null")
        out.append({
            "id": eid,
            "name": name,
            "kind": kind,
            "team": team,
            "x": x,
            "y": y,
            "color": color,
            "owner_name": owner_name,
        })
    return out


def load_bundle(save_id: str) -> tuple[Grid, list[dict[str, Any]]]:
    """Read + fully validate ``saves/<save_id>.json`` (spec §4.3).

    Returns ``(grid, entities)`` — a fresh :class:`Grid` (doors/safe
    validated exactly like the wire; the legacy safe ``"C"`` → ``"U"``
    coercion applies) and the entity list, each entity carrying its
    ``owner_name`` (string or ``None``) for the join-rebind step
    (``GameSession.join``).

    Raises :class:`ValueError` on a missing file, unparseable JSON, or ANY
    schema violation (E3/A12 — the route answers the same clean
    ``404 save not found`` for both missing and corrupt; the file is left
    on disk untouched, nothing is partially registered).
    """
    try:
        with open(_save_path(save_id), "r", encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        raise ValueError(f"save not found: {save_id}") from None
    except OSError:
        raise ValueError(f"save not found: {save_id}") from None
    try:
        bundle = json.loads(raw)
    except ValueError:
        raise ValueError(f"save not found: {save_id}") from None
    if not isinstance(bundle, dict):
        raise ValueError(f"save not found: {save_id}")
    grid = _validated_grid(bundle)
    # The record's top-level width/height must agree with the grid (a
    # truncated/hand-edited bundle with a dimension mismatch is corrupt —
    # AC10c/E3: reject the whole load, register nothing).
    if _as_strict_int(bundle.get("width")) and bundle["width"] != grid.width:
        raise ValueError("bundle width disagrees with grid")
    if _as_strict_int(bundle.get("height")) and bundle["height"] != grid.height:
        raise ValueError("bundle height disagrees with grid")
    entities = _validated_entities(bundle)
    return grid, entities


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def delete_save(save_id: str) -> None:
    """Delete ``saves/<save_id>.json``.

    Raises :class:`FileNotFoundError` when the save does not exist (the
    route answers ``404 save not found``). Deleting a save never affects a
    map already loaded from it (AC18 — the loaded map is an independent
    copy in ``maps_registry``).
    """
    path = _save_path(save_id)  # ValueError on an unsafe id (route → 404)
    os.remove(path)  # FileNotFoundError propagates
