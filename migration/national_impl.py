from pathlib import Path
Path("src/national_points.py").write_text(r'''
"""Reuse FRT's established coordinate grid; fill uncovered districts with FR's pinned plan."""
import csv
from shapely.geometry import Point, shape
from shapely.strtree import STRtree
from discovery_coverage import clean, validate_points

def combine_seed_points(document, native, seed):
    geometries=[]; identities=[]
    for feature in document["features"]:
        props={str(k).upper():v for k,v in feature["properties"].items()}
        county=clean(props.get("COUNTYNAME") or props.get("COUNTY") or props.get("COUNTY_NAME") or "")
        town=clean(props.get("TOWNNAME") or props.get("TOWN") or props.get("TOWN_NAME") or props.get("NAME") or "")
        geom=shape(feature["geometry"])
        if not geom.is_valid:
            from shapely import make_valid
            geom=make_valid(geom)
        geometries.append(geom); identities.append((county,town))
    tree=STRtree(geometries)
    seen=set(); coverage=set(); points=[]
    with seed.open(encoding="utf-8-sig",newline="") as f:
        for row in csv.DictReader(f):
            if row.get("is_on_land","TRUE").upper()!="TRUE":continue
            lat,lon=float(row["latitude"]),float(row["longitude"])
            key=(lat,lon)
            if key in seen:continue
            seen.add(key)
            source_county=clean(row["county"]).replace("桃園縣","桃園市")
            matches=sorted([identities[int(i)] for i in tree.query(Point(lon,lat),predicate="covered_by")])
            county,town=next((a for a in matches if a[0]==source_county),matches[0] if matches else (source_county,""))
            if town:coverage.add((county,town))
            points.append({"id":"legacy-"+row["id"],"latitude":lat,"longitude":lon,"county":county,"town":town})
    if not points:raise ValueError("FRT seed plan is empty")
    missing={(p["county"],p["town"]) for p in native}-coverage
    for p in native:
        if (p["county"],p["town"]) in missing:
            # Include all original FR interior points for an uncovered district.
            points.append(p)
    out=validate_points(sorted(points,key=lambda p:p["id"]))
    if {(p["county"],p["town"]) for p in out if p.get("town")} != {(p["county"],p["town"]) for p in native}:
        raise ValueError("National township coverage changed")
    return out
'''.lstrip(),encoding="utf-8")
Path("src/national_discovery.py").write_text(r'''
"""15-way national Feed discovery using unchanged FR capture and global UUID identity."""
import argparse, hashlib, json, os, shutil
from pathlib import Path
from discovery_coverage import get_points, validate_points, merge_store_points
from distributed_crawl import ROOT, discover, save_shared_discovery
from menu_core import canonical_store_uuid

N=15
def read(p):return json.loads(p.read_text(encoding="utf-8"))
def write(p,doc):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(doc,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
def identity():return {"run_id":os.getenv("GITHUB_RUN_ID","local"),"source_sha":os.getenv("GITHUB_SHA","local")}
def checksum(p):
    with p.open("rb") as f:return hashlib.file_digest(f,"sha256").hexdigest()
def checksums(raw):return {p.relative_to(raw).as_posix():checksum(p) for p in sorted(raw.rglob("*.body"))}
def plan(config,root=ROOT):
    points=get_points(config)
    if len(points)<N:raise ValueError("National plan has fewer than 15 points")
    doc={**identity(),"scope":"taiwan","points":points,"workers":N}
    write(root/"data/national-plan.json",doc)
    print(f"FRT_PLAN_POINTS={len(points)}; WORKERS={N}",flush=True)
    return doc
def load_plan(root):
    p=root/"data/national-plan.json"; doc=read(p)
    if doc.get("workers")!=N or doc.get("scope")!="taiwan" or any(doc.get(k)!=v for k,v in identity().items()):
        raise ValueError("Wrong national discovery plan identity")
    validate_points(doc["points"])
    return doc,checksum(p)
def worker(config,index,root=ROOT):
    if not 0<=index<N:raise ValueError("Invalid discovery worker")
    plan_doc,digest=load_plan(root)
    points=plan_doc["points"][index::N]
    base=root/"data/national-worker"
    raw=base/"raw";raw.mkdir(parents=True,exist_ok=False)
    coverage={}
    stores,pages=discover(config,raw,index,coverage,plan=points,allow_empty=True)
    write(base/"part.json",{**identity(),"plan_sha256":digest,"index":index,"coverage":coverage,
          "pages":pages,"stores":list(stores.values()),"raw_checksums":checksums(raw)})
    print(f"FRT_DISCOVERY_WORKER={index} POINTS={len(points)} STORES={len(stores)} PAGES={pages}",flush=True)
def merge(config,root=ROOT):
    plan_doc,digest=load_plan(root)
    plan_points=plan_doc["points"]
    parts=root/"data/discovery-parts"; folder=root/"data/discovery"; raw=folder/"raw"
    if {p.name for p in parts.iterdir()}!={f"frt-discovery-{i}" for i in range(N)}:
        raise ValueError("Missing or unexpected national discovery workers")
    if folder.exists():raise FileExistsError(folder)
    raw.mkdir(parents=True)
    (raw/"feed").mkdir()
    stores={}; reports={}; total=0
    for index in range(N):
        part=parts/f"frt-discovery-{index}"; doc=read(part/"part.json")
        expected=plan_points[index::N]; coverage=doc["coverage"]
        if (doc.get("index")!=index or doc.get("plan_sha256")!=digest or
            any(doc.get(k)!=v for k,v in identity().items()) or coverage["scope"]!="taiwan" or
            coverage["plan"]!=expected or [r["point_id"] for r in coverage["reports"]]!=[p["id"] for p in expected]):
            raise ValueError(f"Wrong discovery shard {index}")
        if any(type(r["pages"]) is not int or r["pages"]<1 for r in coverage["reports"]) or sum(r["pages"] for r in coverage["reports"])!=doc["pages"]:
            raise ValueError(f"Incomplete discovery pages {index}")
        source=part/"raw"; actual=checksums(source)
        if not actual or actual!=doc["raw_checksums"]:raise ValueError(f"Raw body checksum mismatch {index}")
        expected_prefix=f"feed/shard-{index:02d}/"
        expected_ids={p["id"] for p in expected}
        if any(not name.startswith(expected_prefix) for name in actual):raise ValueError("Wrong Raw partition")
        if {name.split("/")[2] for name in actual}!=expected_ids:raise ValueError("Missing point HTTP evidence")
        for item in doc["stores"]:
            sid=canonical_store_uuid(item["store_uuid"],item["store_url"])
            if sid!=item["store_uuid"] or not item.get("discovered_points") or any(p["point_id"] not in expected_ids for p in item["discovered_points"]):
                raise ValueError("Invalid store provenance")
            if sid in stores:merge_store_points(stores[sid],item)
            else:stores[sid]=item
        for report in coverage["reports"]:
            if report["point_id"] in reports:raise ValueError("Duplicate scan point")
            reports[report["point_id"]]=report
        total+=doc["pages"]
        shutil.move(str(source/"feed"/f"shard-{index:02d}"),str(raw/"feed"/f"shard-{index:02d}"))
    if set(reports)!={p["id"] for p in plan_points}:raise ValueError("Incomplete national point coverage")
    coverage={"scope":"taiwan","plan":plan_points,"reports":[reports[p["id"]] for p in plan_points]}
    result=save_shared_discovery(config,root=root,dynamic=True,prepared=(stores,total,coverage))
    print(f"FRT_GLOBAL_UNIQUE_STORES={len(stores)}; POINTS={len(reports)}; PAGES={total}",flush=True)
    return result
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("command",choices=["plan","worker","merge"])
    ap.add_argument("--index",type=int)
    a=ap.parse_args();cfg=read(ROOT/"config.json")
    if a.command=="plan":plan(cfg)
    elif a.command=="worker":worker(cfg,a.index)
    else:merge(cfg)
if __name__=="__main__":main()
'''.lstrip(),encoding="utf-8")
Path("tests/test_national_points.py").write_text(r'''
import sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
from national_points import combine_seed_points
class NationalPoints(unittest.TestCase):
    def test_existing_grid_and_missing_district(self):
        features=[]
        for county,town,x in [("臺北市","甲區",121),("金門縣","乙區",118)]:
            features.append({"properties":{"COUNTYNAME":county,"TOWNNAME":town},"geometry":{"type":"Polygon","coordinates":[[[x,24],[x+.1,24],[x+.1,24.1],[x,24.1],[x,24]]]}})
        native=[{"id":"native-a","county":"臺北市","town":"甲區","latitude":24.05,"longitude":121.05},
                {"id":"native-b","county":"金門縣","town":"乙區","latitude":24.05,"longitude":118.05}]
        with tempfile.TemporaryDirectory() as t:
            seed=Path(t)/"points.csv"
            seed.write_text("id,latitude,longitude,is_on_land,county\n1,24.02,121.02,TRUE,台北市\n2,24.02,121.02,TRUE,台北市\n")
            out=combine_seed_points({"features":features},native,seed)
        self.assertEqual([p["id"] for p in out],["legacy-1","native-b"])
        self.assertEqual(out[0]["town"],"甲區")
'''.lstrip(),encoding="utf-8")
