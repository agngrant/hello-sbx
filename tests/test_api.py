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
        self.assertEqual(
            set(data.keys()),
            {"id", "name", "width", "height", "image", "cells",
             "entities", "players"},
        )

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
        self.assertEqual(set(data.keys()),
                         {"id", "name", "width", "height", "cells", "thumbnail"})
        self.assertEqual(data["name"], "Upload Test")
        self.assertEqual((data["width"], data["height"]), (16, 12))
        self.assertEqual(len(data["cells"]), 12)
        self.assertTrue(all(len(r) == 16 for r in data["cells"]))
        # at least one doorway detected (the gap at (8,5))
        doors = [(x, y) for y in range(12) for x in range(16)
                 if data["cells"][y][x] == "doorway"]
        self.assertTrue(doors, "expected a detected doorway")
        # the thumbnail is a decodable PNG data-URL
        self.assertTrue(data["thumbnail"].startswith("data:image/png;base64,"))
        new_png = base64.b64decode(data["thumbnail"].split(",", 1)[1])
        self.assertTrue(new_png.startswith(b"\x89PNG"))

        # new map appears in the list
        after = self._map_ids()
        new_ids = after - before
        self.assertEqual(new_ids, {data["id"]})
        # ... and the detail endpoint serves the same grid
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
        # Key set is EXACTLY the upload key set (byte-identical shape).
        self.assertEqual(set(gen_data.keys()),
                         {"id", "name", "width", "height", "cells", "thumbnail"})
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
        self.assertEqual(set(data.keys()),
                         {"id", "name", "width", "height", "cells", "thumbnail"})
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


if __name__ == "__main__":
    unittest.main()
