#!/usr/bin/env python3
"""Only FRT addition to FR: scan the original UE points using FR Feed API.

The output is the native FR shared-feed-v1 contract. All later tasks and
algorithms are delegated to unmodified FR modules.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from discovery_coverage import get_points,validate_points,merge_store_points
from distributed_crawl import latest_raw_workload_weights
from feed_core import scan_single_point
from menu_core import canonical_store_uuid

WORKERS=15
SCAN_THREADS_PER_RUNNER=2

def load(path):return json.loads(Path(path).read_text(encoding="utf-8"))
def write(path,value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
def digest(path):
    with Path(path).open("rb") as f:return hashlib.file_digest(f,"sha256").hexdigest()
def checksums(root):
    root=Path(root)
    return {p.relative_to(root).as_posix():digest(p) for p in sorted(root.rglob("*.body"))}
def identity():
    return {"run_id":os.getenv("GITHUB_RUN_ID","local"),
            "source_sha":os.getenv("GITHUB_SHA","local")}

def plan(config,root=ROOT):
    points=get_points(config)
    if len(points)!=1558:
        raise ValueError("FRT must scan exactly the 1,558 original UE points")
    data={**identity(),"scope":"custom","points":points,"workers":WORKERS}
    write(root/"data/national-plan.json",data)
    print(f"FRT_ORIGINAL_UE_COORDINATES={len(points)}",flush=True)
    return data

def read_plan(root):
    path=root/"data/national-plan.json"
    data=load(path)
    if (data.get("workers")!=WORKERS or data.get("scope")!="custom"
        or any(data.get(k)!=v for k,v in identity().items())):
        raise ValueError("FRT national plan from wrong run or invalid scope")
    validate_points(data["points"])
    if len(data["points"])!=1558:
        raise ValueError("Wrong number of UE points")
    return data,digest(path)

def _scan(point,index,config,raw):
    return scan_single_point(point,max_pages=config["max_pages"],
        raw_dir=raw/"feed"/f"shard-{index:02d}"/point["id"])

def worker(config,index,root=ROOT):
    if type(index) is not int or not 0<=index<WORKERS:
        raise ValueError("Invalid FRT discovery worker")
    source,sha=read_plan(root)
    points=source["points"][index::WORKERS]
    base=root/"data/national-worker"
    raw=base/"raw"
    raw.mkdir(parents=True,exist_ok=False)
    results={}
    with ThreadPoolExecutor(max_workers=SCAN_THREADS_PER_RUNNER) as pool:
        futures={pool.submit(_scan,p,index,config,raw):p for p in points}
        for future in as_completed(futures):
            point=futures[future]
            pid,found,pages=future.result()
            if pid!=point["id"] or not isinstance(pages,int) or pages<1:
                raise RuntimeError("Incomplete FRT UE point: "+point["id"])
            results[pid]=(found,pages)
    unique={};reports=[];pages=0
    for point in points:
        found,n=results[point["id"]]
        pages+=n
        reports.append({"point_id":point["id"],"pages":n,"stores":len(found)})
        for item in found:
            sid=canonical_store_uuid(item["store_uuid"],item["store_url"])
            try:sid=str(uuid.UUID(sid))
            except (TypeError,ValueError) as exc:
                raise ValueError("Invalid canonical UUID in scanned UE store") from exc
            if sid not in unique:
                unique[sid]={**item,"store_uuid":sid}
            else:
                merge_store_points(unique[sid],item)
    raw_hashes=checksums(raw)
    if {p["id"] for p in points}!={p.split("/")[2] for p in raw_hashes}:
        raise ValueError("Missing original HTTP capture from one or more UE points")
    write(base/"part.json",{**identity(),"plan_sha256":sha,"index":index,
        "coverage":{"scope":"custom","plan":points,"reports":reports},
        "pages":pages,"stores":list(unique.values()),"raw_checksums":raw_hashes})
    print(f"FRT_UE_SHARD={index} POINTS={len(points)} UNIQUE_STORES={len(unique)} PAGES={pages}",flush=True)

def merge(config,root=ROOT):
    master,sha=read_plan(root)
    points=master["points"]
    parts=root/"data/discovery-parts"
    folder=root/"data/discovery"
    raw=folder/"raw"
    if folder.exists():raise FileExistsError(folder)
    expected_dirs={f"frt-discovery-{i}" for i in range(WORKERS)}
    if not parts.is_dir() or {p.name for p in parts.iterdir()}!=expected_dirs:
        raise ValueError("All 15 FRT UE scans must be present")
    raw.mkdir(parents=True)
    (raw/"feed").mkdir()
    stores={};reports={};pages=0
    for index in range(WORKERS):
        subset=points[index::WORKERS]
        part=parts/f"frt-discovery-{index}"
        obj=load(part/"part.json")
        coverage=obj["coverage"]
        if (obj.get("index")!=index or obj.get("plan_sha256")!=sha
            or any(obj.get(k)!=v for k,v in identity().items())
            or coverage.get("scope")!="custom" or coverage.get("plan")!=subset
            or [q["point_id"] for q in coverage["reports"]]!=[p["id"] for p in subset]):
            raise ValueError(f"FRT scan shard {index}: wrong run/coordinates")
        if any(type(q["pages"]) is not int or q["pages"]<1 for q in coverage["reports"]):
            raise ValueError(f"FRT scan shard {index}: truncated point")
        if sum(q["pages"] for q in coverage["reports"])!=obj["pages"]:
            raise ValueError("FRT Feed scan pagination mismatch")
        part_raw=part/"raw"
        actual=checksums(part_raw)
        if not actual or actual!=obj["raw_checksums"]:
            raise ValueError(f"FRT scan shard {index}: immutable HTTP hash mismatch")
        if any(not name.startswith(f"feed/shard-{index:02d}/") for name in actual):
            raise ValueError("FRT Feed HTTP shard mixup")
        if {name.split("/")[2] for name in actual}!={p["id"] for p in subset}:
            raise ValueError("Missing raw HTTP evidence for UE coordinate")
        for item in obj["stores"]:
            sid=canonical_store_uuid(item["store_uuid"],item["store_url"])
            if sid!=item["store_uuid"] or not item.get("discovered_points"):
                raise ValueError("Invalid FRT scanned store UUID/provenance")
            if any(p["point_id"] not in {q["id"] for q in subset}
                   for p in item["discovered_points"]):
                raise ValueError("FRT store from another coordinate partition")
            if sid in stores:merge_store_points(stores[sid],item)
            else:stores[sid]=item
        for report in coverage["reports"]:
            if report["point_id"] in reports:
                raise ValueError("Duplicate UE scan point")
            reports[report["point_id"]]=report
        pages+=obj["pages"]
        shutil.move(str(part_raw/"feed"/f"shard-{index:02d}"),
                    str(raw/"feed"/f"shard-{index:02d}"))
    if not stores or set(reports)!={p["id"] for p in points}:
        raise ValueError("Incomplete FRT nationwide store roster")
    bodies=checksums(raw)
    section_weights={}
    estimates=latest_raw_workload_weights(config,section_weights)
    feed={
        "ownership":None, "owner_count":WORKERS, "assignment_mode":"dynamic",
        "workload_weights":estimates,"section_workload_weights":section_weights,
        "format":"foodradar-shared-feed-v1", **identity(),
        "latitude":config["latitude"],"longitude":config["longitude"],
        "feed_pages":pages,"stores":[stores[sid] for sid in sorted(stores)],
        "raw_checksums":bodies,
        "discovery_coverage":{"scope":"custom","plan":points,
            "reports":[reports[p["id"]] for p in points]},
    }
    location=folder/"discovery.json"
    location.write_text(json.dumps(feed,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"FRT_UE_UNIQUE_STORES={len(stores)} POINTS={len(points)} PAGES={pages} FR_FORMAT=foodradar-shared-feed-v1",flush=True)
    return feed

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("command",choices=["plan","worker","merge"])
    parser.add_argument("--index",type=int)
    args=parser.parse_args()
    config=load(ROOT/"config.json")
    if args.command=="plan":plan(config)
    elif args.command=="worker":worker(config,args.index)
    else:merge(config)
if __name__=="__main__":main()
