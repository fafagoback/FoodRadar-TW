"""FRT-only regression checks for the immutable nationwide Feed merge.

Run after frt/bootstrap.py has provided the pinned FR core.
The fixture is synthetic; it never accesses UE, Supabase, HF or GitHub.
"""
import hashlib
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from frt import national_discovery as adapter


class NationalMergeIOTests(unittest.TestCase):
    def make_parts(self, root):
        points = []
        for index in range(adapter.WORKERS):
            point = {
                "id": str(index), "latitude": 24.0 + index / 1000,
                "longitude": 121.0, "county": "Test",
            }
            points.append(point)
            part = root / "data" / "discovery-parts" / f"frt-discovery-{index}"
            relative = f"feed/shard-{index:02d}/{index}/page-0.body"
            body = part / "raw" / relative
            body.parent.mkdir(parents=True)
            payload = f"immutable-feed-{index}".encode()
            body.write_bytes(payload)
            store_uuid = str(uuid.UUID(int=index + 1))
            record = {
                **adapter.identity(), "plan_sha256": "mock-plan-sha", "index": index,
                "coverage": {
                    "scope": "custom", "plan": [point],
                    "reports": [{"point_id": str(index), "pages": 1, "stores": 1}],
                },
                "pages": 1,
                "stores": [{
                    "store_uuid": store_uuid, "store_url": "https://example.invalid/store",
                    "discovered_points": [{"point_id": str(index)}],
                }],
                "raw_checksums": {relative: hashlib.sha256(payload).hexdigest()},
            }
            (part / "part.json").write_text(json.dumps(record), encoding="utf-8")
        return points

    def merge_with_stubs(self, root, points):
        with patch.object(adapter, "read_plan", return_value=({"points": points}, "mock-plan-sha")), \
             patch.object(adapter, "canonical_store_uuid", side_effect=lambda key, url: key), \
             patch.object(adapter, "latest_raw_workload_weights", return_value={}), \
             patch.object(adapter, "checksums", wraps=adapter.checksums) as hash_pass:
            result = adapter.merge({"latitude": 24.0, "longitude": 121.0}, root=root)
            return result, hash_pass.call_count

    def test_exact_hash_pass_once_per_input_shard_and_compact_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            points = self.make_parts(root)
            result, passes = self.merge_with_stubs(root, points)
            self.assertEqual(passes, adapter.WORKERS)
            self.assertEqual(len(result["stores"]), adapter.WORKERS)
            self.assertEqual(result["feed_pages"], adapter.WORKERS)
            self.assertEqual(len(result["raw_checksums"]), adapter.WORKERS)
            manifest = root / "data" / "discovery" / "discovery.json"
            self.assertEqual(json.loads(manifest.read_text(encoding="utf-8")), result)
            self.assertEqual(manifest.stat().st_size, len(manifest.read_bytes()))
            self.assertNotIn(b"\\n  ", manifest.read_bytes())
            for relative, expected in result["raw_checksums"].items():
                data = (root / "data" / "discovery" / "raw" / relative).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), expected)

    def test_corrupted_shard_refused_before_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            points = self.make_parts(root)
            body = root / "data" / "discovery-parts" / "frt-discovery-0" / "raw" / "feed" / "shard-00" / "0" / "page-0.body"
            body.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                self.merge_with_stubs(root, points)
            self.assertFalse((root / "data" / "discovery" / "discovery.json").exists())

    def test_wrong_shard_coordinate_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            points = self.make_parts(root)
            file = root / "data" / "discovery-parts" / "frt-discovery-0" / "part.json"
            record = json.loads(file.read_text(encoding="utf-8"))
            record["stores"][0]["discovered_points"][0]["point_id"] = "other"
            file.write_text(json.dumps(record), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "another coordinate partition"):
                self.merge_with_stubs(root, points)
