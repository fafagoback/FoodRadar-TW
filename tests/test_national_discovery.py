import hashlib,json,sys,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from national_discovery import N,write,checksum,merge,checksums,identity
from discovery_coverage import COUNTIES
from distributed_crawl import load_shared_discovery

class NationalDiscoveryTest(unittest.TestCase):
    def test_all_15_shards_merge_to_one_fr_store_and_preserve_all_http(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            counties=sorted(COUNTIES)
            points=[{"id":f"p-{i:04d}","county":counties[i%len(counties)],"town":f"district-{i}","latitude":24.0,"longitude":121.0} for i in range(368)]
            plan=root/"data/national-plan.json"
            write(plan,{**identity(),"scope":"taiwan","workers":N,"points":points})
            digest=checksum(plan)
            sid=str(uuid.UUID(int=1))
            for index in range(N):
                subset=points[index::N]
                part=root/"data/discovery-parts"/f"frt-discovery-{index}"
                raw=part/"raw"
                for p in subset:
                    f=raw/"feed"/f"shard-{index:02d}"/p["id"]/"feed.body"
                    f.parent.mkdir(parents=True)
                    f.write_bytes(p["id"].encode())
                item={"store_uuid":sid,"store_url":"https://example.invalid/store/"+sid,"store_lat":24.0,"store_lon":121.0,
                      "discovered_points":[{"point_id":p["id"],"lat":p["latitude"],"lon":p["longitude"],"county":p["county"]} for p in subset]}
                write(part/"part.json",{**identity(),"index":index,"plan_sha256":digest,"pages":len(subset),"stores":[item],
                      "coverage":{"scope":"taiwan","plan":subset,"reports":[{"point_id":p["id"],"pages":1,"stores":1} for p in subset]},
                      "raw_checksums":checksums(raw)})
            config={"latitude":24.0,"longitude":121.0,"max_pages":0,"discovery_scope":"taiwan"}
            with patch("distributed_crawl.latest_raw_workload_weights",return_value={}):
                result=merge(config,root=root)
            self.assertEqual(len(result["stores"]),1)
            self.assertEqual(result["feed_pages"],368)
            self.assertEqual(len(result["stores"][0]["discovered_points"]),368)
            self.assertEqual(len(result["raw_checksums"]),368)
            self.assertEqual(result["owner_count"],15)
            self.assertEqual(result["assignment_mode"],"dynamic")
            stores,pages,_,ownership=load_shared_discovery(root/"data/discovery/discovery.json",config,root/"scratch",-1)
            self.assertEqual(set(stores),{sid})
            self.assertEqual(pages,368)
            self.assertIsNone(ownership)
