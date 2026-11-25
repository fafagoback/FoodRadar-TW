import csv,json,os,sys,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from national_discovery import WORKERS,plan,worker,merge,load
from discovery_coverage import get_points
from distributed_crawl import load_shared_discovery

class FRTOneSourceAcceptance(unittest.TestCase):
    def test_no_cross_project_resource_defaults(self):
        cfg=load(ROOT/"config.json")
        self.assertEqual(cfg["hf_repo_id"],"hub-google/Foodradar-TW")
        benchmark=(ROOT/"scripts/fr_pool_load_test.py").read_text()
        self.assertIn("/functions/v1/frt-task-pool-benchmark",benchmark)
        self.assertIn('foodradar-tw:task-pool-benchmark',benchmark)
        self.assertNotIn("/functions/v1/fr-task-pool-benchmark",benchmark)
        self.assertNotIn('foodradar:task-pool-benchmark',benchmark)
        for filename in ("src/export_static_snapshots.py","scripts/spot_v2_hf.py"):
            contents=(ROOT/filename).read_text()
            self.assertIn("hub-google/Foodradar-TW",contents)
            self.assertNotIn("hub-google/UberEat",contents)
        provision=(ROOT/"scripts/provision_foodradar.py").read_text()
        self.assertIn("fafagoback/FoodRadar-TW",provision)
        self.assertNotIn("fafagoback/FoodRadar'",provision)
        self.assertNotIn('fafagoback/FoodRadar"',provision)

    def test_exact_original_ue_coordinates(self):
        cfg=load(ROOT/"config.json")
        with open(ROOT/"data/seeds/taiwan_scan_points_3km_land_only.csv",newline="",encoding="utf-8-sig") as f:
            original=list(csv.DictReader(f))
        actual=get_points(cfg)
        self.assertEqual(len(actual),1558)
        self.assertEqual(len({p["id"] for p in actual}),1558)
        self.assertEqual([(p["id"],p["latitude"],p["longitude"],p["county"]) for p in actual],
            [(str(int(p["id"])),float(p["latitude"]),float(p["longitude"]),p["county"]) for p in original])
        self.assertEqual(cfg["hf_repo_id"],"hub-google/Foodradar-TW")
        self.assertEqual(cfg["discovery_scope"],"custom")
    def test_current_fr_compact_contract_for_isolated_frt(self):
        from state_checkpoint import direct_contract_digest,compatible_direct_contract
        contract=direct_contract_digest(ROOT)
        self.assertEqual(len(contract),64)
        self.assertTrue(compatible_direct_contract(
            {"format":"foodradar-checkpoint-v4","contract_digest":contract},ROOT))
        self.assertFalse(compatible_direct_contract(
            {"format":"foodradar-checkpoint-v4","contract_digest":"foreign"},ROOT))

    def test_15_way_concurrent_ue_scan_merges_into_genuine_fr_format(self):
        cfg=load(ROOT/"config.json")
        uid=str(uuid.UUID(int=123))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            # Use the exact real 1,558 coordinate manifest, but mocked HTTP responses.
            with patch.dict(os.environ,{"GITHUB_RUN_ID":"frt-test","GITHUB_SHA":"frt-test-sha"}):
                master=plan(cfg,root=root)
                self.assertEqual(len(master["points"]),1558)
                def fake_scan(point,index,config,raw):
                    rawfile=raw/"feed"/f"shard-{index:02d}"/point["id"]/"response.body"
                    rawfile.parent.mkdir(parents=True,exist_ok=True)
                    rawfile.write_bytes(point["id"].encode())
                    store={"store_uuid":uid,"store_url":"https://example.invalid/store/"+uid,
                        "store_lat":point["latitude"],"store_lon":point["longitude"],
                        "discovered_points":[{"point_id":point["id"],"lat":point["latitude"],
                            "lon":point["longitude"],"county":point["county"]}]}
                    return point["id"],[store],1
                with patch("national_discovery._scan",side_effect=fake_scan):
                    (root/"data/discovery-parts").mkdir(parents=True)
                    for i in range(WORKERS):
                        worker(cfg,i,root=root)
                        p=root/"data/national-worker"
                        p.rename(root/"data/discovery-parts"/f"frt-discovery-{i}")
                with patch("national_discovery.latest_raw_workload_weights",return_value={}):
                    result=merge(cfg,root=root)
                self.assertEqual(len(result["stores"]),1)
                self.assertEqual(result["owner_count"],15)
                self.assertEqual(result["feed_pages"],1558)
                self.assertEqual(len(result["raw_checksums"]),1558)
                self.assertEqual(len(result["stores"][0]["discovered_points"]),1558)
                stores,pages,_,owners=load_shared_discovery(
                    root/"data/discovery/discovery.json",cfg,root/"worker",-1)
                self.assertEqual(set(stores),{uid})
                self.assertEqual(pages,1558)
                self.assertIsNone(owners)
