#!/usr/bin/env python3
"""Verify why four real PX grocery category branches need parent-page scans.

Read archived UE Raw only. Test whether all known child UUIDs actually exist
anywhere in getStoreV1 JSON and find the earliest parent page discovering them.
No live UE calls and no prod writes.
"""
import collections,json,re,sys,tarfile,time,os
from pathlib import Path
SID="54b6106a-8f4b-48ad-8014-7ba334513ee2"
CATALOG=re.compile(r"(?:^|/)catalog/([0-9a-f-]{36})/.*?-section-([0-9]+)-([0-9a-f-]{36})-sub-(none|[0-9a-f-]{36})-")
PAGE=re.compile(r"-page-(\d+)-offset-(\d+)-")
def find_uuid_paths(node, targets, path="", hits=None,depth=0):
    if hits is None: hits=collections.defaultdict(list)
    if depth>30:return hits
    if isinstance(node,dict):
        for k,v in node.items():
            if k in targets and len(hits[k])<4: hits[k].append(path+"."+str(k)+"[key]")
            find_uuid_paths(v,targets,path+"."+str(k),hits,depth+1)
    elif isinstance(node,list):
        for i,v in enumerate(node):
            find_uuid_paths(v,targets,path+"["+str(i)+"]",hits,depth+1)
    elif isinstance(node,str):
        for value in targets:
            if value in node and len(hits[value])<4:hits[value].append(path)
    return hits
def run(path):
    from catalog_complete import _subsections_for,_native_subsections_from_catalog
    started=time.monotonic()
    groups=collections.defaultdict(lambda:{"parents":[],"children":set()})
    natives=[]
    with tarfile.open(path,"r|gz") as tar:
        for member in tar:
            if not member.isfile() or not member.name.endswith(".body"):continue
            name=member.name
            if SID not in name:continue
            match=CATALOG.search(name)
            if match:
                sid,idx,section,sub=match.groups()
                if sid!=SID:continue
                group=groups[(idx,section)]
                if sub!="none":
                    group["children"].add(sub)
                else:
                    try:
                        with tar.extractfile(member) as fh:doc=json.load(fh)
                        if doc.get("status") in ("success",None) and isinstance((doc.get("data") or {}).get("catalog"),list):
                            m=PAGE.search(name)
                            group["parents"].append({
                                "filename":name,"page":int(m.group(1)) if m else 9999,
                                "offset":int(m.group(2)) if m else 999999,
                                "data":doc})
                    except (ValueError,TypeError,AttributeError):pass
            elif name.startswith("stores/"+SID+"-"):
                try:
                    with tar.extractfile(member) as fh:doc=json.load(fh)
                    data=doc.get("data") if isinstance(doc,dict) else None
                    if isinstance(data,dict) and "大全聯" in str(data.get("title") or ""):natives.append(data)
                except (ValueError,TypeError,AttributeError):pass
    if not natives:raise RuntimeError("No valid original getStoreV1 captured")
    native=max(natives,key=lambda d:len(json.dumps(d,ensure_ascii=False)))
    reports=[]
    total_parent_pages=sum(len(g["parents"]) for g in groups.values())
    total_child_pages=0
    for (idx,section),group in sorted(groups.items()):
        known=set(_subsections_for(native,section))
        children=group["children"]
        parents=sorted(group["parents"],key=lambda x:(x["offset"],x["page"],x["filename"]))
        # A parent page may contain both native item and category rows.
        target=children-known
        found_paths=find_uuid_paths(native,target)
        in_native={c for c,paths in found_paths.items() if paths}
        recovered=set(known)
        prefix_needed=0
        discovered_by_preview={}
        for i,parent in enumerate(parents):
            vals=set(_native_subsections_from_catalog([parent["data"]],section))
            for child in vals-recovered:
                discovered_by_preview[child]={"parent_page_number":i+1,
                    "offset":parent["offset"]}
            recovered.update(vals)
            if children <= recovered:
                prefix_needed=i+1
                break
        if children and not children <= known and prefix_needed==0:
            prefix_needed=len(parents)
        reports.append({
            "section_uuid":section,
            "parent_pages_in_archive":len(parents),
            "native_subsections":len(children),
            "known_by_ordinary_getStoreV1_parser":len(children & known),
            "child_uuids_missing_from_ordinary_parser":len(children-known),
            "missing_child_uuids_present_elsewhere_in_getStoreV1":len(in_native),
            "native_field_paths_for_unknown_children":{
                child:found_paths[child] for child in sorted(in_native)},
            "parent_preview_pages_needed_to_see_every_archived_child":prefix_needed if children else 0,
            "all_archived_children_discoverable_from_metadata_plus_parent":children<=recovered,
            "discovery_first_seen_page_for_unknown":{
                child:discovered_by_preview.get(child)
                for child in sorted(children-known)},
            "last_parent_offset":max((p["offset"] for p in parents),default=None),
        })
    with_missing=[r for r in reports if r["child_uuids_missing_from_ordinary_parser"]]
    # The old comparison showed 358 child/terminal pages + parents for category
    # branches without children. Only branches with native children need extra
    # parent discovery requests.
    extra_prefix=sum(x["parent_preview_pages_needed_to_see_every_archived_child"] for x in with_missing)
    result={
        "store":"大全聯 鳳山店","store_uuid":SID,
        "using_existing_hf_archive":True,"UE_requests_this_test":0,
        "baseline_catalog_http_responses":426,
        "pure_leaf_first_http_responses":358,
        "additional_discovery_parent_pages_in_offline_oracle":extra_prefix,
        "hybrid_offline_oracle_http_responses":358+extra_prefix,
        "hybrid_offline_oracle_reduction":426-(358+extra_prefix),
        "sections_requiring_parent_discovery":len(with_missing),
        "unknown_child_uuid_count":sum(x["child_uuids_missing_from_ordinary_parser"] for x in with_missing),
        "children_present_in_other_native_fields_count":sum(x["missing_child_uuids_present_elsewhere_in_getStoreV1"] for x in with_missing),
        "details":with_missing,
        "limitations":[
            "The prefix page count is an offline oracle: actual future complete child roster is unknown until independently verified.",
            "A previously observed child UUID roster cannot prove no new categories appeared.",
            "This experiment makes no live UE calls and cannot certify independent inventory completeness."],
        "elapsed_seconds":round(time.monotonic()-started,1)
    }
    Path("frt-px-parent-discovery.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    print("FRT_PX_PARENT_DISCOVERY="+json.dumps(result,ensure_ascii=False),flush=True)
    summary=os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary,"a",encoding="utf8") as f:
            f.write("# FRT 大全聯：子分類取得方式實測（既有完整原始資料）\n\n")
            f.write(f'原始 426 次；子分類理論 358 次；辨識缺漏子分類需另取 {extra_prefix} 份父分類回應。\n\n')
            f.write("| 父分類 UUID | 原父分類分頁 | 初始缺少的子分類 | 最少父分類探索頁數 | 額外在 getStoreV1 找到 |\n|---|---:|---:|---:|---:|\n")
            for a in with_missing:
                f.write(f'| {a["section_uuid"]} | {a["parent_pages_in_archive"]} | {a["child_uuids_missing_from_ordinary_parser"]} | {a["parent_preview_pages_needed_to_see_every_archived_child"]} | {a["missing_child_uuids_present_elsewhere_in_getStoreV1"]} |\n')
            f.write("\n注意：這是離線舊資料全知比較，不能代替新鮮 UE 完整菜單驗證。\n")
if __name__=="__main__":
    if len(sys.argv)!=2:raise SystemExit("Usage: PYTHONPATH=src python frt/ue_parent_discovery_replay.py capture.tar.gz")
    run(Path(sys.argv[1]))
