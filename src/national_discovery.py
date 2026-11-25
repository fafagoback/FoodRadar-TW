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
