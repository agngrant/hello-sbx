"""REST API tests (stdlib unittest + http.client; Iteration 1/2/3).

Each test class spins up its own ``ThreadingHTTPServer`` on an ephemeral
port (``127.0.0.1:0``) in a daemon thread via setUp/tearDown.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import threading
import unittest

os.environ.setdefault("LITTLEDUNGEONS_QUIET_LOGS", "1")

from app.grid import build_sample_map
from app.imaging import encode_png
from app.models import Grid
from app.server import ThreadingHTTPServer


class ServerTestCase(unittest.TestCase):
    """Base: isolated LittleDungeons server on a free port."""

    @classmethod
    def setUpClass(cls):
        # The ``ThreadingHTTPServer`` here is the drop-in adapter in
        # ``app.server`` (a uvicorn Server running the FastAPI app in a
        # background thread). It accepts and ignores the legacy handler
        # argument, so this boot code is shape-identical to the old stdlib
        # server. Passing ``None`` for the handler makes that explicit.
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
        cls.httpd.daemon_threads = True
        cls.httpd.handle_error = lambda *a, **k: None  # quiet test server
        cls.host, cls.port = cls.httpd.server_address[:2]
        cls.thread = threading.Thread(
            target=cls.httpd.serve_forever, daemon=True, name="littedungeons-test"
        )
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def request(self, method: str, path: str, body: bytes | None = None,
                headers: dict[str, str] | None = None):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            data = resp.read()
            hdrs = {k.lower(): v for k, v in resp.getheaders()}
            return resp.status, hdrs, data
        finally:
            conn.close()

    def post_json(self, path: str, obj: dict) -> tuple[int, dict]:
        body = json.dumps(obj).encode("utf-8")
        status, _, data = self.request(
            "POST", path, body=body,
            headers={"Content-Type": "application/json"},
        )
        return status, json.loads(data)

    def get_json(self, path: str):
        status, headers, data = self.request("GET", path)
        return status, headers, json.loads(data)


class TestHealth(ServerTestCase):
    def test_health_ok(self):
        status, headers, body = self.get_json("/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"status": "ok"})
        self.assertIn("application/json", headers.get("content-type", ""))


class TestMapList(ServerTestCase):
    def test_lists_sample_map(self):
        status, headers, body = self.get_json("/api/maps")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers.get("content-type", ""))
        self.assertIn("maps", body)
        maps = body["maps"]
        self.assertIsInstance(maps, list)
        self.assertGreaterEqual(len(maps), 1)
        sample = next(m for m in maps if m["id"] == "sample-dungeon")
        self.assertEqual(sample["name"], "Sample Dungeon")
        self.assertEqual(sample["width"], 16)
        self.assertEqual(sample["height"], 12)
        # Summary items carry exactly the §8 shape.
        self.assertEqual(set(sample.keys()), {"id", "name", "width", "height"})


class TestMapDetail(ServerTestCase):
    def test_get_sample_map(self):
        status, headers, data = self.get_json("/api/maps/sample-dungeon")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers.get("content-type", ""))
        self.assertEqual(data["id"], "sample-dungeon")
        self.assertEqual(data["name"], "Sample Dungeon")
        self.assertEqual(data["width"], 16)
        self.assertEqual(data["height"], 12)
        self.assertEqual(len(data["cells"]), 12)
        self.assertTrue(all(len(row) == 16 for row in data["cells"]))
        self.assertEqual(data["cells"][0][0], "wall")
        # doorways survive the trip
        self.assertEqual(data["cells"][5][5], "doorway")
        self.assertEqual(data["cells"][4][10], "doorway")
        self.assertEqual(data["cells"][7][9], "doorway")
        # Iteration 4/5 will populate these
        self.assertEqual(data["entities"], [])
        self.assertEqual(data["players"], [])

    def test_detail_shape(self):
        _, _, data = self.get_json("/api/maps/sample-dungeon")
        # Additive `doors` (door-features spec §8.2/A9/AC10): the sample map
        # has 3 doorways, so the full door object (all L by default) is present.
        self.assertEqual(
            set(data.keys()),
            {"id", "name", "width", "height", "image", "cells",
             "entities", "players", "doors"},
        )
        self.assertEqual(data["doors"], {"5,5": "L", "10,4": "L", "9,7": "L"})

    def test_grid_matches_model(self):
        _, _, data = self.get_json("/api/maps/sample-dungeon")
        grid = {
            "name": data["name"],
            "width": data["width"],
            "height": data["height"],
            "cells": data["cells"],
            "image": data["image"],
        }
        self.assertEqual(grid, build_sample_map().to_dict())

    def test_unknown_map_404(self):
        status, headers, data = self.get_json("/api/maps/nope")
        self.assertEqual(status, 404)
        self.assertEqual(data, {"error": "not found"})


class TestStatic(ServerTestCase):
    def test_index_html_served_at_root(self):
        status, headers, data = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("content-type", ""))
        body = data.decode("utf-8")
        self.assertIn("<!DOCTYPE html>", body)
        self.assertIn('id="map-canvas"', body)
        self.assertIn('id="lobby-view"', body)

    def test_static_assets(self):
        status, headers, data = self.request("GET", "/style.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", headers.get("content-type", ""))

        status, headers, data = self.request("GET", "/app.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers.get("content-type", ""))
        self.assertIn("LittleDungeons", data.decode("utf-8"))

    def test_missing_file_404(self):
        status, _, data = self.request("GET", "/nope.html")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(data), {"error": "not found"})

    def test_path_traversal_blocked(self):
        status, _, _ = self.request("GET", "/../PROJECT.md")
        self.assertEqual(status, 404)


class TestUploadPaint(ServerTestCase):
    """Iteration 3: POST /api/maps/upload (JSON base64) + POST /api/maps/{id}/paint."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # 16x12 fixture: dark walls (1px border + 2px interior wall col 8-9)
        # with a 1-cell gap at (8,5) — encodes to a doorway deterministically.
        w, h = 16, 12
        cls.rows = [
            [
                (0, 0, 0) if (
                    x in (0, w - 1) or y in (0, h - 1)
                    or (x == 8 and 1 <= y <= 10 and (x, y) != (8, 5))
                    or (x == 9 and 1 <= y <= 10 and (x, y) not in ((9, 4), (9, 5), (9, 6)))
                ) else (255, 255, 255)
                for x in range(w)
            ]
            for y in range(h)
        ]
        cls.png_b64 = base64.b64encode(encode_png(w, h, cls.rows)).decode("ascii")

    def _map_ids(self) -> set[str]:
        status, _, body = self.get_json("/api/maps")
        self.assertEqual(status, 200)
        return {m["id"] for m in body["maps"]}

    def test_upload_creates_map_with_doorway(self):
        before = self._map_ids()
        status, data = self.post_json("/api/maps/upload", {
            "name": "Upload Test",
            "image_b64": self.png_b64,
            "cols": 16,
            "rows": 12,
            "dark_is_wall": True,
        })
        self.assertEqual(status, 200)
        # Additive `doors` (door-features spec §8.2/A9/AC10): a fresh upload
        # has its detected doorways all L (default), so the full door object
        # is present with every doorway -> "L".
        self.assertEqual(set(data.keys()),
                         {"id", "name", "width", "height", "cells",
                          "thumbnail", "doors"})
        self.assertEqual(data["name"], "Upload Test")
        self.assertEqual((data["width"], data["height"]), (16, 12))
        self.assertEqual(len(data["cells"]), 12)
        self.assertTrue(all(len(r) == 16 for r in data["cells"]))
        # at least one doorway detected (the gap at (8,5))
        doorway_cells = [(x, y) for y in range(12) for x in range(16)
                         if data["cells"][y][x] == "doorway"]
        self.assertTrue(doorway_cells, "expected a detected doorway")
        # every detected doorway is a door, all L (closed+locked by default)
        self.assertEqual(
            data["doors"],
            {f"{x},{y}": "L" for (x, y) in doorway_cells},
        )
        # the thumbnail is a decodable PNG data-URL
        self.assertTrue(data["thumbnail"].startswith("data:image/png;base64,"))
        new_png = base64.b64decode(data["thumbnail"].split(",", 1)[1])
        self.assertTrue(new_png.startswith(b"\x89PNG"))

        # new map appears in the list
        after = self._map_ids()
        new_ids = after - before
        self.assertEqual(new_ids, {data["id"]})
        # ... and the detail endpoint serves the same grid + door states
        status, _, detail = self.get_json(f"/api/maps/{data['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["cells"], data["cells"])
        self.assertEqual(detail["name"], "Upload Test")
        self.assertEqual(detail["entities"], [])
        self.assertEqual(detail["players"], [])

    def test_upload_id_slug_and_uniqueness(self):
        s1, d1 = self.post_json("/api/maps/upload",
                                {"name": "Crypt Hall", "image_b64": self.png_b64})
        s2, d2 = self.post_json("/api/maps/upload",
                                {"name": "Crypt Hall", "image_b64": self.png_b64})
        self.assertEqual(s1, 200)
        self.assertEqual(s2, 200)
        self.assertEqual(d1["id"], "crypt-hall")
        self.assertNotEqual(d1["id"], d2["id"])
        self.assertIn(d2["id"], self._map_ids())
        self.assertIn(d1["id"], self._map_ids())

    def test_upload_default_autoscale(self):
        # No cols/rows → 16x12 already fits in the max-60 budget: 1:1 grid.
        status, data = self.post_json("/api/maps/upload",
                                      {"name": "auto", "image_b64": self.png_b64})
        self.assertEqual(status, 200)
        self.assertEqual((data["width"], data["height"]), (16, 12))

    def test_upload_errors_400(self):
        # not an image
        status, data = self.post_json("/api/maps/upload",
                                      {"name": "x", "image_b64": "bm90IGEgaW1hZ2U="})
        self.assertEqual(status, 400)
        self.assertIn("error", data)
        # bad base64
        status, data = self.post_json("/api/maps/upload",
                                      {"name": "x", "image_b64": "!!!not-b64!!"})
        self.assertEqual(status, 400)
        # missing name
        status, data = self.post_json("/api/maps/upload", {"image_b64": self.png_b64})
        self.assertEqual(status, 400)
        # missing image
        status, data = self.post_json("/api/maps/upload", {"name": "x"})
        self.assertEqual(status, 400)

    def test_paint_cell_and_detail_reflects_it(self):
        # fresh map so the assertions don't depend on other tests' uploads
        status, up = self.post_json("/api/maps/upload",
                                    {"name": "paint me", "image_b64": self.png_b64,
                                     "cols": 16, "rows": 12})
        self.assertEqual(status, 200)
        map_id = up["id"]
        # paint a known floor cell -> doorway
        status, data = self.post_json(f"/api/maps/{map_id}/paint",
                                      {"x": 2, "y": 3, "cell_type": "doorway"})
        self.assertEqual(status, 200)
        self.assertEqual(data, {"ok": True, "x": 2, "y": 3, "cell_type": "doorway"})
        status, _, detail = self.get_json(f"/api/maps/{map_id}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["cells"][3][2], "doorway")
        # paint it back to wall
        status, data = self.post_json(f"/api/maps/{map_id}/paint",
                                      {"x": 2, "y": 3, "cell_type": "wall"})
        self.assertEqual(status, 200)
        _, _, detail = self.get_json(f"/api/maps/{map_id}")
        self.assertEqual(detail["cells"][3][2], "wall")

    def test_paint_errors(self):
        # unknown map → 404
        status, data = self.post_json("/api/maps/nope/paint",
                                      {"x": 0, "y": 0, "cell_type": "floor"})
        self.assertEqual(status, 404)
        self.assertEqual(data, {"error": "not found"})
        # out of bounds → 400
        status, data = self.post_json("/api/maps/sample-dungeon/paint",
                                      {"x": 99, "y": 0, "cell_type": "floor"})
        self.assertEqual(status, 400)
        # bad cell type → 400
        status, data = self.post_json("/api/maps/sample-dungeon/paint",
                                      {"x": 1, "y": 1, "cell_type": "lava"})
        self.assertEqual(status, 400)
        # non-integer coordinate → 400
        status, data = self.post_json("/api/maps/sample-dungeon/paint",
                                      {"x": "1", "y": 1, "cell_type": "floor"})
        self.assertEqual(status, 400)


class TestGenerateMap(ServerTestCase):
    """generated-maps spec §5/§9: POST /api/maps/generate (C9/C10)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Upload reference for the C10 key-set comparison: a 16x12 fixture
        # with a 1-cell gap at (8,5) — encodes to a doorway deterministically
        # (same fixture as TestUploadPaint).
        w, h = 16, 12
        cls.rows = [
            [
                (0, 0, 0) if (
                    x in (0, w - 1) or y in (0, h - 1)
                    or (x == 8 and 1 <= y <= 10 and (x, y) != (8, 5))
                    or (x == 9 and 1 <= y <= 10 and (x, y) not in ((9, 4), (9, 5), (9, 6)))
                ) else (255, 255, 255)
                for x in range(w)
            ]
            for y in range(h)
        ]
        cls.png_b64 = base64.b64encode(encode_png(w, h, cls.rows)).decode("ascii")

    def _map_ids(self) -> set[str]:
        status, _, body = self.get_json("/api/maps")
        self.assertEqual(status, 200)
        return {m["id"] for m in body["maps"]}

    # -- C9: validation errors (exact §5.1 strings, first failure wins) ----

    def test_validation_errors(self):
        cases = [
            ({"name": "x", "cols": 7, "rows": 10}, "'cols' must be an integer in 8-60"),
            ({"name": "x", "cols": 10, "rows": 61}, "'rows' must be an integer in 8-60"),
            ({"name": "x", "cols": "24", "rows": 10}, "'cols' must be an integer in 8-60"),
            ({"name": "x", "cols": True, "rows": 10}, "'cols' must be an integer in 8-60"),
            ({"name": "", "cols": 10, "rows": 10}, "'name' must be a non-empty string"),
            ({"cols": 10, "rows": 10}, "'name' must be a non-empty string"),
            ({"name": "  ", "cols": 10, "rows": 10}, "'name' must be a non-empty string"),
            ({"name": 42, "cols": 10, "rows": 10}, "'name' must be a non-empty string"),
            ({"name": "x", "cols": 10, "rows": 10, "seed": "abc"}, "'seed' must be an integer"),
            ({"name": "x", "cols": 10, "rows": 10, "seed": True}, "'seed' must be an integer"),
            ({"name": "x", "cols": 24.0, "rows": 10}, "'cols' must be an integer in 8-60"),
            ({"name": "x", "cols": 10, "rows": None}, "'rows' must be an integer in 8-60"),
        ]
        for body, expected_msg in cases:
            status, data = self.post_json("/api/maps/generate", body)
            self.assertEqual(status, 400, f"{body!r}")
            self.assertEqual(data, {"error": expected_msg}, f"{body!r}")

    def test_non_object_and_malformed_body(self):
        # Non-object JSON body (a list parses fine but is not an object).
        status, data = self.post_json("/api/maps/generate", [{"name": "x"}])
        self.assertEqual(status, 400)
        self.assertEqual(data, {"error": "request body must be a JSON object"})
        # Malformed JSON → shared helper's message.
        status, _, raw = self.request(
            "POST", "/api/maps/generate",
            body=b"{not json", headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw), {"error": "request body must be JSON"})

    # -- C10: success response shape -------------------------------------

    def test_success_shape_mirrors_upload(self):
        status, gen_data = self.post_json("/api/maps/generate", {
            "name": "The Deep Warrens", "cols": 24, "rows": 16, "seed": 1337,
        })
        self.assertEqual(status, 200)
        # Key set is EXACTLY the upload key set (byte-identical shape); both
        # now carry the additive `doors` object (door-features spec §8.2/A9).
        self.assertEqual(set(gen_data.keys()),
                         {"id", "name", "width", "height", "cells",
                          "thumbnail", "doors"})
        # C2/AC10: a fresh generated map has every carved doorway "L" (locked).
        gen_doors = [(x, y) for y in range(gen_data["height"])
                     for x in range(gen_data["width"])
                     if gen_data["cells"][y][x] == "doorway"]
        self.assertGreaterEqual(len(gen_doors), 3)
        self.assertEqual(gen_data["doors"],
                         {f"{x},{y}": "L" for (x, y) in gen_doors})
        status, up_data = self.post_json("/api/maps/upload", {
            "name": "keyset-ref", "image_b64": self.png_b64, "cols": 16, "rows": 12,
        })
        self.assertEqual(status, 200)
        self.assertEqual(set(gen_data.keys()), set(up_data.keys()))

        # C1: exact dimensions in the response.
        self.assertEqual((gen_data["width"], gen_data["height"]), (24, 16))
        self.assertEqual(len(gen_data["cells"]), 16)
        self.assertTrue(all(len(row) == 24 for row in gen_data["cells"]))
        # name is trimmed and stored.
        self.assertEqual(gen_data["name"], "The Deep Warrens")
        # id is a slug.
        self.assertEqual(gen_data["id"], "the-deep-warrens")
        # thumbnail: PNG data-URL that decodes to a PNG.
        self.assertTrue(gen_data["thumbnail"].startswith("data:image/png;base64,"))
        self.assertTrue(base64.b64decode(
            gen_data["thumbnail"].split(",", 1)[1]).startswith(b"\x89PNG"))

    def test_seed_null_is_unseeded(self):
        # §3.5: "null / omitted ⇒ unseeded" — an explicit "seed": null must
        # behave exactly like an omitted seed and generate normally.
        status, data = self.post_json("/api/maps/generate", {
            "name": "seednull", "cols": 10, "rows": 10, "seed": None,
        })
        self.assertEqual(status, 200)
        self.assertEqual((data["width"], data["height"]), (10, 10))
        # Additive `doors` present (every carved doorway all L).
        self.assertEqual(set(data.keys()),
                         {"id", "name", "width", "height", "cells",
                          "thumbnail", "doors"})
        self.assertIn("seednull", self._map_ids())

    def test_name_trimming_and_id_registration(self):
        # Leading/trailing whitespace is trimmed from the stored name and
        # the slug is built from the trimmed value.
        status, data = self.post_json("/api/maps/generate", {
            "name": "  Spaced Hall  ", "cols": 12, "rows": 12, "seed": 0,
        })
        self.assertEqual(status, 200)
        self.assertEqual(data["name"], "Spaced Hall")
        self.assertEqual(data["id"], "spaced-hall")
        # Registered: the new id appears in GET /api/maps ...
        self.assertIn(data["id"], self._map_ids())
        # ... and GET /api/maps/{id} returns the same cells with image: null.
        status, _, detail = self.get_json(f"/api/maps/{data['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["cells"], data["cells"])
        self.assertEqual(detail["name"], "Spaced Hall")
        self.assertIsNone(detail["image"], "generated maps have no source image")
        # A second generate with the same name gets the -2 suffix (upload
        # id rules apply to generated maps too).
        status, second = self.post_json("/api/maps/generate", {
            "name": "Spaced Hall", "cols": 12, "rows": 12, "seed": 0,
        })
        self.assertEqual(status, 200)
        self.assertEqual(second["id"], "spaced-hall-2")
        self.assertIn(second["id"], self._map_ids())
        # Same seed + size → identical cells regardless of which id.
        self.assertEqual(second["cells"], data["cells"])


class TestSafeDoorRest(ServerTestCase):
    """AC10(b): the additive `safe` object in REST map objects — present
    (disjoint from `doors`) only when safe doors exist; absent (byte-
    identical to today) for a map with none. No new REST route.

    A fresh ``sample-dungeon`` grid has NO safe doors, so its detail is
    byte-identical to the pre-feature build (no ``safe`` key, ``doors``
    unchanged — the existing exact key-set assertions keep passing). To prove
    the additive path we register a FRESH grid (unique id) with a safe door
    and assert the detail carries ``safe`` disjoint from ``doors`` — without
    mutating the shared sample-dungeon grid that other tests pin."""

    def test_sample_map_detail_has_no_safe_key(self):
        # Regression: the shared sample dungeon has no safe doors → the
        # ``safe`` key is ABSENT (byte-identical to the frozen shape), and
        # ``doors`` is unchanged (all three L by default — or whatever a
        # prior WS test left; the point is NO safe key, disjoint from doors).
        _, _, data = self.get_json("/api/maps/sample-dungeon")
        self.assertNotIn("safe", data)
        self.assertIn("doors", data)
        # whatever doors exist, none of the safe door cells can overlap:
        # (there are no safe cells, so all doorway keys are in doors)
        self.assertIsInstance(data["doors"], dict)

    def test_fresh_upload_and_generate_have_no_safe_key(self):
        # AC10(b): fresh upload/generate responses are unchanged (no safe
        # key) — safe doors are GM-authored, never detected/generated.
        status, data = self.post_json("/api/maps/generate", {
            "name": "Safe-less Gen", "cols": 10, "rows": 10, "seed": 7,
        })
        self.assertEqual(status, 200)
        self.assertNotIn("safe", data)
        self.assertIn("doors", data)

    def test_registered_grid_with_safe_door_carrirs_safe_disjoint(self):
        from app.main import maps_registry
        # Register a fresh 3x3 grid with one doorway; mark it a safe door
        # (locked — the fresh-mark default; the wire now carries L/U/O).
        g = Grid(name="SafeRest", width=3, height=3,
                 cells=[["wall"] * 3,
                        ["wall", "floor", "doorway"],
                        ["wall"] * 3],
                 safe={"2,1": "L"})
        map_id = "safe-rest-proof"
        maps_registry[map_id] = {
            "grid": g, "entities": {}, "players": {}}
        try:
            status, _, data = self.get_json(f"/api/maps/{map_id}")
            self.assertEqual(status, 200)
            # The additive `safe` object is present with the safe door's
            # state, and `doors` SKIPS the safe cell (disjoint, jointly
            # covering the doorway cells).
            self.assertEqual(data.get("safe"), {"2,1": "L"})
            # the single doorway is the safe door → doors has no entry for
            # it (it is covered by `safe` instead).
            self.assertEqual(data.get("doors"), {})
            self.assertNotIn("2,1", data.get("doors", {}))
        finally:
            maps_registry.pop(map_id, None)

    def test_legacy_c_safe_door_loads_as_u_on_rest(self):
        # AC7 end-to-end: a grid loaded from a pre-redesign payload
        # (map.safe containing the legacy "C") carries "U" (unlocked
        # closed) on the wire — never "C".
        from app.main import maps_registry
        legacy = Grid(name="SafeLegacy", width=3, height=3,
                      cells=[["wall"] * 3,
                             ["wall", "floor", "doorway"],
                             ["wall"] * 3]).to_dict()
        legacy["safe"] = {"2,1": "C"}
        g = Grid.from_dict(legacy)  # the only migration point
        self.assertEqual(g.safe, {"2,1": "U"})
        map_id = "safe-legacy-proof"
        maps_registry[map_id] = {
            "grid": g, "entities": {}, "players": {}}
        try:
            status, _, data = self.get_json(f"/api/maps/{map_id}")
            self.assertEqual(status, 200)
            self.assertEqual(data.get("safe"), {"2,1": "U"})
            self.assertNotIn("C", list(data.get("safe", {}).values()))
        finally:
            maps_registry.pop(map_id, None)

    def test_rest_paint_over_safe_door_deletes_safe(self):
        # AC10/§8.2: painting floor/wall over a safe door deletes the safe
        # record server-side (shared sync point); a subsequent GET reflects
        # it. The REST paint response shape is unchanged (frozen).
        from app.main import maps_registry
        g = Grid(name="SafePaint", width=3, height=3,
                 cells=[["wall"] * 3,
                        ["wall", "floor", "doorway"],
                        ["wall"] * 3],
                 safe={"2,1": "O"})
        map_id = "safe-paint-proof"
        maps_registry[map_id] = {
            "grid": g, "entities": {}, "players": {}}
        try:
            # paint the safe doorway back to floor → the safe record is gone
            status, data = self.post_json(
                f"/api/maps/{map_id}/paint",
                {"x": 2, "y": 1, "cell_type": "floor"})
            self.assertEqual(status, 200)
            # the response shape is FROZEN (no safe echo):
            self.assertEqual(
                set(data.keys()), {"ok", "x", "y", "cell_type"})
            status, _, detail = self.get_json(f"/api/maps/{map_id}")
            self.assertEqual(status, 200)
            self.assertNotIn("safe", detail)  # no safe doors → key omitted
            self.assertEqual(detail["cells"][1][2], "floor")
        finally:
            maps_registry.pop(map_id, None)


class TestSaves(ServerTestCase):
    """Save-load spec §5: the additive REST routes (backend scope).

    * ``GET /api/saves`` — any role, no join required (list from saves/ scan)
    * ``POST /api/saves`` — GM only (401 player-only session, 409 no session,
      400 bad body/name); 200 = the save record + the bundle written to disk
    * ``POST /api/saves/{id}/load`` — GM only; missing/corrupt → 404; 200 =
      a fresh registry map id (the GM opens it via the existing ``use_map``)
    * ``DELETE /api/saves/{id}`` — GM only; missing → 404

    Role source (spec A8): the GM of the in-memory ``default`` session. The
    server runs in THIS process (the ThreadingHTTPServer adapter wraps the
    FastAPI app in a background thread of the same process), so the tests
    build the ``default`` session directly via ``app.main`` — deterministic
    and independent of WS join ordering. ``sessions["default"]`` is cleared
    after every test so no state leaks between tests.

    ``app.saves.SAVES_DIR`` is redirected to a per-test temp dir (created
    lazily by save_bundle; removed in teardown) so the repo-root saves/ is
    never touched.
    """

    def setUp(self):
        import app.saves as save_store
        from app.main import get_session as _gs
        from app.grid import build_sample_map
        from app.models import Player

        self._orig_dir = save_store.SAVES_DIR
        self._tmp = f"/tmp/ld-saves-test-{os.getpid()}-{id(self)}"
        save_store.SAVES_DIR = self._tmp
        # A deterministic default session on a FRESH sample-dungeon grid,
        # with a GM present (the save routes' role source, spec A8).
        self._session = _gs("default")
        self._session.grid = build_sample_map()
        self._session.players.clear()
        self._session.entities.clear()
        self._gm = Player(id="gm-0", name="GM", role="gm")
        self._session.players[self._gm.id] = self._gm

    def tearDown(self):
        import shutil
        import app.saves as save_store
        from app.main import sessions

        save_store.SAVES_DIR = self._orig_dir
        shutil.rmtree(self._tmp, ignore_errors=True)
        # Leave the default session empty (its grid may now be a map loaded
        # by a test; reset to a fresh sample so other consumers see a grid).
        from app.grid import build_sample_map
        if "default" in sessions:
            sessions["default"].players.clear()
            sessions["default"].entities.clear()
            sessions["default"].grid = build_sample_map()

    # -- helpers ------------------------------------------------------------

    def _players(self):
        from app.main import sessions
        return sessions["default"].players

    def _add_player(self, name: str, entity_id: str | None = None):
        from app.models import Player

        p = Player(id=f"pl-{name}", name=name, role="player")
        p.entity_id = entity_id
        self._players()[p.id] = p
        return p

    def _make_player_only(self):
        # A8: a session that exists with players but NO GM (a player-only
        # session) -> 401 for save/load/delete.
        del self._players()[self._gm.id]
        self._add_player("Loner")

    def _list(self):
        status, _, body = self.get_json("/api/saves")
        self.assertEqual(status, 200)
        return body

    def _post_save(self, **body):
        return self.post_json("/api/saves", body)

    def _load(self, save_id: str):
        status, _, data = self.request("POST", f"/api/saves/{save_id}/load")
        return status, json.loads(data)

    # -- GET /api/saves (any role, no join) ----------------------------------

    def test_list_empty_when_no_dir(self):
        # E1: no saves dir → {"saves": []}; no session/join needed at all.
        self.assertEqual(self._list(), {"saves": []})

    def test_list_shape_and_order(self):
        status, d1 = self._post_save(name="First")
        self.assertEqual(status, 200)
        status, d2 = self._post_save(name="Second")
        self.assertEqual(status, 200)
        listing = self._list()["saves"]
        self.assertEqual([r["id"] for r in listing], [d2["id"], d1["id"]])
        self.assertEqual(
            set(listing[0].keys()),
            {"id", "name", "map_name", "width", "height",
             "created_at", "entity_count"},
        )
        # Record reflects the live session (sample dungeon, 16x12).
        self.assertEqual(listing[0]["map_name"], "Sample Dungeon")
        self.assertEqual((listing[0]["width"], listing[0]["height"]), (16, 12))
        self.assertEqual(listing[0]["name"], "Second")

    def test_list_flags_corrupt_file(self):
        # A12: a non-JSON file in saves/ is listed with "corrupt": true.
        os.makedirs(self._tmp, exist_ok=True)
        with open(os.path.join(self._tmp, "corrupt.json"), "w") as f:
            f.write("{oops")
        listing = self._list()["saves"]
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0]["id"], "corrupt")
        self.assertTrue(listing[0]["corrupt"])

    # -- POST /api/saves ------------------------------------------------------

    def test_gm_save_writes_bundle_to_disk(self):
        # AC1: 200 record correct AND the file is on disk with the full
        # state (grid + entities carrying owner_name = the player's NAME).
        from app.models import Entity
        from app.main import sessions

        s = sessions["default"]
        self._add_player("Alice", entity_id="e1")
        s.entities["e1"] = Entity(id="e1", name="Alice", kind="player",
                                  team="party", x=1, y=1, owner="pl-Alice")
        status, data = self._post_save(name="Act One")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["name"], "Act One")
        self.assertEqual(data["map_name"], "Sample Dungeon")
        self.assertEqual((data["width"], data["height"]), (16, 12))
        self.assertEqual(data["entity_count"], 1)
        self.assertTrue(data["created_at"])
        self.assertTrue(data["id"].startswith("act-one-"))
        # Real persistence — the file exists and parses from disk:
        path = os.path.join(self._tmp, f"{data['id']}.json")
        self.assertTrue(os.path.isfile(path))
        with open(path, "r", encoding="utf-8") as f:
            bundle = json.load(f)
        self.assertEqual(bundle["id"], data["id"])
        self.assertEqual(bundle["name"], "Act One")
        self.assertEqual(bundle["map_name"], "Sample Dungeon")
        self.assertEqual(bundle["width"], 16)
        self.assertEqual(bundle["height"], 12)
        self.assertEqual(bundle["entity_count"], 1)
        self.assertEqual(len(bundle["entities"]), 1)
        ent = bundle["entities"][0]
        self.assertEqual(ent["id"], "e1")
        self.assertEqual(ent["x"], 1)
        self.assertEqual(ent["y"], 1)
        self.assertEqual(ent["kind"], "player")
        self.assertEqual(ent["team"], "party")
        self.assertEqual(ent["owner"], "pl-Alice")
        # owner_name is the controlling player's NAME (not the id):
        self.assertEqual(ent["owner_name"], "Alice")

    def test_gm_save_captures_gm_controlled_entities_with_null_owner_name(self):
        from app.models import Entity
        from app.main import sessions

        s = sessions["default"]
        s.entities["g1"] = Entity(id="g1", name="Goblin", kind="enemy",
                                  team="hostile", x=5, y=5, owner=None)
        status, data = self._post_save()
        self.assertEqual(status, 200)
        with open(os.path.join(self._tmp, f"{data['id']}.json"),
                  "r", encoding="utf-8") as f:
            bundle = json.load(f)
        ent = next(e for e in bundle["entities"] if e["id"] == "g1")
        self.assertIsNone(ent["owner_name"])  # GM-controlled → null
        # No label ⇒ the record's name falls back to the map name.
        self.assertEqual(data["name"], "Sample Dungeon")

    def test_gm_save_snapshot_is_frozen_copy(self):
        # AC17: creating a save must not mutate the live session — the grid
        # object is not swapped and the entity is not moved by the save.
        from app.models import Entity
        from app.main import sessions

        s = sessions["default"]
        self._add_player("Alice", entity_id="e1")
        s.entities["e1"] = Entity(id="e1", name="Alice", kind="player",
                                  team="party", x=1, y=1, owner="pl-Alice")
        pos_before = (s.entities["e1"].x, s.entities["e1"].y)
        grid_before = s.grid
        status, _ = self._post_save(name="Frozen")
        self.assertEqual(status, 200)
        self.assertIs(s.grid, grid_before)          # grid not swapped
        self.assertEqual((s.entities["e1"].x, s.entities["e1"].y),
                         pos_before)                # entity not moved

    def test_save_name_default_and_trim(self):
        status, data = self._post_save(name="  Padded  ")
        self.assertEqual(status, 200)
        self.assertEqual(data["name"], "Padded")
        self.assertTrue(data["id"].startswith("padded-"))

    def test_save_overwrite_by_explicit_id(self):
        # E2 "Overwrite": an explicit id replaces that file in place.
        _, d1 = self._post_save(name="V1")
        status, d2 = self._post_save(name="V2", id=d1["id"])
        self.assertEqual(status, 200)
        self.assertEqual(d2["id"], d1["id"])
        listing = self._list()["saves"]
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0]["id"], d1["id"])
        self.assertEqual(listing[0]["name"], "V2")

    def test_save_as_new_same_name_is_distinct(self):
        # E2 "Save as new": same label twice → two distinct ids.
        _, d1 = self._post_save(name="T1")
        _, d2 = self._post_save(name="T1")
        self.assertNotEqual(d1["id"], d2["id"])
        self.assertEqual(
            {r["id"] for r in self._list()["saves"]}, {d1["id"], d2["id"]})

    def test_save_validation_errors(self):
        # (body, want_status, want_data); a dict/list is JSON-encoded, a
        # string is sent RAW as the body.
        cases = [
            ({"name": 42}, 400, {"error": "'name' must be a string"}),
            ({"name": "x" * 41}, 400, {"error": "'name' must be a string"}),
            ([1, 2, 3], 400, {"error": "request body must be a JSON object"}),
            ({"name": "x", "id": 7}, 400, {"error": "'id' must be a non-empty string"}),
            ({"name": "x", "id": "../etc"}, 400, {"error": "'id' must be a valid save id"}),
        ]
        for body, want_status, want_data in cases:
            with self.subTest(body=body):
                if isinstance(body, (dict, list)):
                    status, data = self.post_json("/api/saves", body)
                else:
                    status, _, raw = self.request("POST", "/api/saves",
                                                  body=body.encode(),
                                                  headers={"Content-Type": "application/json"})
                    data = json.loads(raw)
                self.assertEqual(status, want_status)
                self.assertEqual(data, want_data)

    def test_save_malformed_json_400(self):
        status, _, raw = self.request("POST", "/api/saves", body=b"{not json",
                                      headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw), {"error": "request body must be JSON"})

    def test_save_no_session_409(self):
        # A8/E9: no default session at all → 409, clear + actionable.
        from app.main import sessions
        sessions.pop("default", None)
        status, data = self.post_json("/api/saves", {"name": "x"})
        self.assertEqual(status, 409)
        self.assertEqual(data, {"error":
            "no active map session — join as GM and open a map first"})

    # -- GM-only perms (AC11) -------------------------------------------------

    def _player_only_session(self):
        # A8: a session exists with NO GM (a player-only session) -> 401.
        # Drop the GM that setUp added; keep a player so a session exists.
        del self._players()[self._gm.id]
        self._add_player("Loner")

    def test_save_player_only_401(self):
        self._player_only_session()
        status, data = self.post_json("/api/saves", {"name": "x"})
        self.assertEqual(status, 401)
        self.assertEqual(data, {"error": "only the GM can save"})

    def test_load_player_only_401(self):
        self._player_only_session()
        status, data = self._load("nope")
        self.assertEqual(status, 401)
        self.assertEqual(data, {"error": "only the GM can load"})

    def test_delete_player_only_401(self):
        self._player_only_session()
        status, _, raw = self.request("DELETE", "/api/saves/nope")
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(raw), {"error": "only the GM can delete"})

    def test_load_and_delete_allowed_without_session(self):
        # A8: with NO session at all, load/delete are permitted (a GM may
        # load before players arrive). We make a save file directly and
        # load it with no session present.
        import app.saves as save_store
        from app.grid import build_sample_map

        sessions = __import__("app.main", fromlist=["sessions"]).sessions
        sessions.pop("default", None)
        sid = save_store.save_bundle(
            {"name": "Pre", "created_at": "2025-01-01T00:00:00"},
            build_sample_map(), [])
        status, data = self._load(sid)
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["save_id"], sid)
        status, _, raw = self.request("DELETE", f"/api/saves/{sid}")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw), {"ok": True})

    # -- POST /api/saves/{id}/load -------------------------------------------

    def test_load_missing_404(self):
        # AC10(a): missing id → 404 clean shape.
        status, data = self._load("nope")
        self.assertEqual(status, 404)
        self.assertEqual(data, {"error": "save not found: nope"})

    def test_load_corrupt_404_and_healthy(self):
        # AC10(b/c): unparseable + truncated → 404 (same shape as missing),
        # no crash, server healthy, file untouched, list still works.
        os.makedirs(self._tmp, exist_ok=True)
        # (b) unparseable
        with open(os.path.join(self._tmp, "bad.json"), "w") as f:
            f.write("{not json")
        status, data = self._load("bad")
        self.assertEqual(status, 404)
        self.assertEqual(data, {"error": "save not found: bad"})
        self.assertEqual(open(os.path.join(self._tmp, "bad.json")).read(),
                         "{not json")  # untouched
        # (c) valid JSON, height mismatch
        with open(os.path.join(self._tmp, "trunc.json"), "w") as f:
            json.dump({"id": "trunc", "name": "t", "width": 2, "height": 1,
                       "grid": {"name": "t", "width": 2, "height": 2,
                                "cells": [["floor"] * 2, ["floor"] * 2]},
                       "entities": []}, f)
        status, data = self._load("trunc")
        self.assertEqual(status, 404)
        self.assertEqual(data, {"error": "save not found: trunc"})
        # no partial registration (trunc produced no map)
        status, _, maps = self.get_json("/api/maps")
        self.assertNotIn("trunc", [m["id"] for m in maps["maps"]])
        # server still healthy + list still works (corrupt flagged):
        status, _, health = self.get_json("/health")
        self.assertEqual(status, 200)
        self.assertEqual(health, {"status": "ok"})
        self.assertTrue(any(r.get("corrupt") for r in self._list()["saves"]))

    def test_load_registers_fresh_independent_map(self):
        # AC17/A10: load → fresh smap-* registry id; two loads of the same
        # save → two DISTINCT maps; mutating one doesn't touch the other.
        from app.main import maps_registry

        _, s1 = self._post_save(name="RoundTrip")
        status, d1 = self._load(s1["id"])
        self.assertEqual(status, 200)
        self.assertTrue(d1["ok"])
        self.assertIn("smap-", d1["id"])
        self.assertEqual(d1["save_id"], s1["id"])
        self.assertEqual(d1["name"], "Sample Dungeon")
        self.assertEqual((d1["width"], d1["height"]), (16, 12))
        # Load again → a DIFFERENT fresh id (independent copy).
        status, d2 = self._load(s1["id"])
        self.assertEqual(status, 200)
        self.assertNotEqual(d1["id"], d2["id"])
        # The registered map serves the saved grid.
        status, _, detail = self.get_json(f"/api/maps/{d1['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["name"], "Sample Dungeon")
        # Independence: paint one copy, the other is untouched.
        self.post_json(f"/api/maps/{d1['id']}/paint",
                       {"x": 1, "y": 1, "cell_type": "wall"})
        status, _, detail2 = self.get_json(f"/api/maps/{d2['id']}")
        self.assertEqual(detail2["cells"][1][1], "floor")  # untouched
        # The save file itself is untouched by the load + paint.
        self.assertTrue(os.path.isfile(
            os.path.join(self._tmp, f"{s1['id']}.json")))
        # cleanup (avoid registry growth across tests)
        maps_registry.pop(d1["id"], None)
        maps_registry.pop(d2["id"], None)

    # -- DELETE /api/saves/{id} ------------------------------------------------

    def test_delete_removes_save(self):
        # AC18: delete → 200, file gone, list omits it, re-delete → 404.
        _, d = self._post_save(name="Doomed")
        status, _, raw = self.request("DELETE", f"/api/saves/{d['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw), {"ok": True})
        self.assertFalse(os.path.exists(
            os.path.join(self._tmp, f"{d['id']}.json")))
        self.assertEqual(self._list()["saves"], [])
        status, _, raw = self.request("DELETE", f"/api/saves/{d['id']}")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(raw),
                         {"error": f"save not found: {d['id']}"})

    def test_delete_missing_404(self):
        status, _, raw = self.request("DELETE", "/api/saves/nope")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(raw), {"error": "save not found: nope"})


if __name__ == "__main__":
    unittest.main()
