import csv,sys,tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
from national_points import load_seed_points
from discovery_coverage import get_points
from national_discovery import plan

class NationalPoints(unittest.TestCase):
    def test_original_ue_grid_exactly(self):
        cfg=json.loads((ROOT/"config.json").read_text())
        seed=ROOT/cfg["national_seed_file"]
        with seed.open(encoding="utf-8-sig",newline="") as f:
            original=[(str(int(r["id"])),float(r["latitude"]),float(r["longitude"]),r["county"]) for r in csv.DictReader(f)]
        with patch.dict("os.environ",{"FR_DISCOVERY_SCOPE":"taiwan"}),patch("requests.get",side_effect=AssertionError("No remote geometry required")):
            actual=get_points(cfg)
            with tempfile.TemporaryDirectory() as t:
                self.assertEqual(plan(cfg,root=Path(t))["points"],actual)
        self.assertEqual(len(actual),1558)
        self.assertEqual([(p["id"],p["latitude"],p["longitude"],p["county"]) for p in actual],original)
        self.assertEqual(len({p["county"] for p in actual}),20)

    def test_changed_seed_fails_closed(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/"points.csv";p.write_text("id,latitude,longitude,county\n1,24,121,台北市\n")
            with self.assertRaises(ValueError):load_seed_points(p)
