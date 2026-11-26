#!/usr/bin/env python3
"""FRT large-grocery complete-menu offline replay using real pinned FR production ETL.

Not just SKU counts: reconstruct full FRT web schema twice, check actual
SKU/value/offer/provenance differences, native section discovery and completeness.
Read ONLY existing immutable HF Raw capture; no Uber API requests or prod writes.
"""
import collections
import hashlib
import json
import os
import re
import sys
import tarfile
import time
from pathlib import Path

SID="54b6106a-8f4b-48ad-8014-7ba334513ee2"
REGEX=re.compile(
    r"(?:^|/)catalog/([0-9a-f-]{36})/.*?-section-([0-9]+)-([0-9a-f-]{36})-sub-(none|[0-9a-f-]{36})-"
)
STORE_URL="https://www.ubereats.com/tw/store/offline-replay/"+SID

def stable(obj):
    return json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str)

def item_projection(item,mode):
    """Normalized final production schema projection, excluding occurrence-only section IDs."""
    quote=item.get("verified_price") or {}
    quote={k:v for k,v in quote.items() if k!="source_hash"}
    web=item.get("catalog_web") or {}
    web={k:v for k,v in web.items() if k not in ("section_uuid","subsection_uuid")}
    if mode=="price":
        out={"offers":item.get("offers"),"verified_price":quote,
             "web_price_offer_availability":{k:v for k,v in web.items()
                if k not in ("image_url",)}}
    else:
        out={"name":item.get("name"),"description":item.get("description"),
             "offers":item.get("offers"),"verified_price":quote,"catalog_web":web}
    return stable(out)

def item_map(doc,mode):
    sku_to_signatures=collections.defaultdict(set)
    section_memberships=collections.defaultdict(set)
    source_hashes=collections.defaultdict(set)
    for section in doc["hasMenu"]["hasMenuSection"]:
        for item in section["hasMenuItem"]:
            sku=item.get("identifier")
            if not sku: continue
            sku_to_signatures[sku].add(item_projection(item,mode))
            for key in ("section_uuid","subsection_uuid"):
                value=(item.get("catalog_web") or {}).get(key)
                if value:section_memberships[sku].add(key+":"+str(value))
            quote=item.get("verified_price") or {}
            if quote.get("source_hash"):source_hashes[sku].add(quote["source_hash"])
    return sku_to_signatures,section_memberships,source_hashes

def diff(base,trial):
    base_keys,trial_keys=set(base),set(trial)
    mismatched=[sku for sku in sorted(base_keys & trial_keys) if base[sku]!=trial[sku]]
    return {"baseline_skus":len(base_keys),"trial_skus":len(trial_keys),
            "missing_skus":len(base_keys-trial_keys),
            "unexpected_skus":len(trial_keys-base_keys),
            "mismatched_existing_skus":len(mismatched),
            "mismatch_examples":mismatched[:8]}

def catalog_items(doc):
    value=doc.get("data") or {}
    rows=value.get("catalog") or []
    return sum(len(((row.get("payload") or {}).get("standardItemsPayload") or {}).get("catalogItems") or [])
               for row in rows if isinstance(row,dict))

def run(path):
    # Official FR business rules are shared by the FRT runtime; this is not a
    # homemade simulator. The menu_core schema is exactly what FRT emits.
    from menu_core import convert_api_data_to_schema
    from catalog_complete import merge_catalog_pages,_subsections_for,_catalog_entries
    from snapshot_validation import validate_document
    started=time.monotonic()
    stores=[]
    pages=[]
    failures=[]
    with tarfile.open(path,"r|gz") as tar:
        for member in tar:
            if not member.isfile() or not member.name.endswith(".body"):continue
            name=member.name
            m=REGEX.search(name) if SID in name else None
            is_store=name.startswith("stores/"+SID+"-")
            if not m and not is_store:continue
            with tar.extractfile(member) as fh:
                raw=fh.read()
            try:
                obj=json.loads(raw)
            except Exception as err:
                failures.append({"path":name,"error":type(err).__name__})
                continue
            if is_store:
                data=obj.get("data") if isinstance(obj,dict) else {}
                if isinstance(data,dict) and data.get("title") and (
                    obj.get("status") in (None,"success")):
                    stores.append((name,data))
                continue
            sid,idx,section,sub=m.groups()
            if sid!=SID:continue
            try:
                d,items=_catalog_entries(obj)
                page={"path":name,"data":obj,"bytes":member.size,"group":(idx,section,sub),
                      "paging":d["pagingInfo"],"skus":catalog_items(obj)}
                pages.append(page)
            except Exception as err:
                failures.append({"path":name,"error":str(err)[:100]})
    if not stores:raise RuntimeError("Missing valid archived getStoreV1 response for test grocery")
    # The original store call is a preview; pick the largest valid captured
    # metadata object rather than guessing which may contain all native aisles.
    stores.sort(key=lambda n:len(stable(n[1])),reverse=True)
    store_path,native=stores[0]
    if "大全聯" not in str(native.get("title") or ""):
        raise RuntimeError("Store UUID unexpectedly resolved to a different retailer")
    # Keep real original order (sorted paths is deterministic in all retries).
    pages.sort(key=lambda p:p["path"])
    groups=collections.defaultdict(list)
    for p in pages:groups[p["group"][:2]].append(p)
    selected=[]
    parent_info=[]
    direct_unavailable=[]
    partitions_without_terminal=[]
    for (idx,section),rows in sorted(groups.items()):
        child=[p for p in rows if p["group"][2]!="none"]
        parent=[p for p in rows if p["group"][2]=="none"]
        used=child if child else parent
        selected.extend(used)
        actual_children=set(p["group"][2] for p in child)
        metadata_children=set(_subsections_for(native,section))
        if actual_children and not actual_children <= metadata_children:
            direct_unavailable.append({
                "section_uuid":section,
                "archive_child_count":len(actual_children),
                "known_from_getStoreV1":len(actual_children & metadata_children),
                "unknown_child_count":len(actual_children-metadata_children)})
        for sub,grp in collections.defaultdict(list,(
            (key,[p for p in used if p["group"][2]==key])
            for key in set(p["group"][2] for p in used)
        )).items():
            if not any(p["paging"].get("hasMore") is False for p in grp):
                partitions_without_terminal.append({
                    "section_uuid":section,"subsection_uuid":sub,"pages":len(grp)})
        parent_info.append({"section_uuid":section,"parent_pages":len(parent),
                            "child_pages":len(child),
                            "direct_child_metadata_available":not actual_children or actual_children<=metadata_children})
    baseline_pages=[p["data"] for p in pages]
    trial_pages=[p["data"] for p in selected]
    before=convert_api_data_to_schema(merge_catalog_pages(native,baseline_pages),STORE_URL)
    after=convert_api_data_to_schema(merge_catalog_pages(native,trial_pages),STORE_URL)
    validate_document(before)
    validate_document(after)
    bp,bsec,bhash=item_map(before,"price")
    tp,tsec,thash=item_map(after,"price")
    bf,_,_=item_map(before,"full")
    tf,_,_=item_map(after,"full")
    price_diff=diff(bp,tp)
    full_diff=diff(bf,tf)
    membership_diff=diff(bsec,tsec)
    hashes_diff=diff(bhash,thash)
    total_base=sum(p["bytes"] for p in pages)
    total_trial=sum(p["bytes"] for p in selected)
    # A false-positive "all products complete" is prohibited: unchanged
    # business values DO NOT establish future discoverability or completeness.
    exact_web_values_match=all(price_diff[k]==0 for k in (
       "missing_skus","unexpected_skus","mismatched_existing_skus"))
    can_plan_without_parent=not direct_unavailable
    all_terminal=not partitions_without_terminal
    report={
        "source":"HF dataset hub-google/Foodradar-TW/Raw/20261126072649/capture.tar.gz",
        "method":"Real FR production convert_api_data_to_schema() and quote_from_native_item() replay",
        "UE_requests_this_test":0,
        "store":{"uuid":SID,"name":native.get("title"),"original_store_raw_path":store_path,
                 "original_store_responses":len(stores)},
        "baseline":{"catalog_requests":len(pages),"catalog_raw_bytes":total_base,
                    "catalog_products_occurrences":sum(p["skus"] for p in pages)},
        "leaf_first":{"catalog_requests":len(selected),"catalog_raw_bytes":total_trial,
                      "catalog_products_occurrences":sum(p["skus"] for p in selected),
                      "request_reduction_pct":round(100*(len(pages)-len(selected))/len(pages),2)},
        "product_and_price_promo_availability":price_diff,
        "all_web_facing_product_fields_excluding_category_position":full_diff,
        "native_category_membership_evidence":membership_diff,
        "native_original_price_source_hashes":hashes_diff,
        "direct_discovery_from_getStoreV1":{
            "all_required_child_uuids_known_without_parent":can_plan_without_parent,
            "sections_missing_child_uuids":direct_unavailable,
            "parent_sections":len(groups)},
        "terminal_pagination":{
            "all_chosen_partitions_reached_terminal":all_terminal,
            "unknown_terminal_partitions":partitions_without_terminal[:35],
            "count_unknown_terminal_partitions":len(partitions_without_terminal)},
        "pass":{
            "same_final_product_price_promo_availability":exact_web_values_match,
            "same_all_business_web_fields":all(full_diff[k]==0 for k in (
                "missing_skus","unexpected_skus","mismatched_existing_skus")),
            "is_live_request_plan_independently_viable":can_plan_without_parent and all_terminal,
            "full_live_store_menu_verified":False},
        "warnings":[
            "Archived baseline has no independent upstream inventory proof.",
            "Archive-only subset cannot prove future live menu completeness without metadata for every child.",
            "Do not release a partial store capture as complete.",
            "Native hash/category evidence differences may affect provenance and actual website rendering."
        ],
        "elapsed_seconds":round(time.monotonic()-started,1),
        "read_errors":failures[:10]
    }
    Path("frt-grocery-production-replay.json").write_text(
        json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    print("FRT_GROCERY_PRODUCTION_REPLAY="+json.dumps(report,ensure_ascii=False),flush=True)
    summary=os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary,"a",encoding="utf8") as f:
            f.write("# FRT 真實大全聯原始資料：正式轉檔程式完整回放\n\n")
            f.write("**不對UE發送任何請求；重播已存 HF 的完整原始資料。**\n\n")
            f.write("| 指標 | 原方法 | 精簡分類候選 |\n|---|---:|---:|\n")
            f.write(f'| Catalog API 回應 | {len(pages)} | {len(selected)} |\n')
            f.write(f'| UE 原始回應 MB | {total_base/1e6:.2f} | {total_trial/1e6:.2f} |\n')
            f.write(f'| FRT 網站商品 UUID | {len(bp)} | {len(tp)} |\n')
            f.write(f'| 價格優惠可用性欄位差異商品 | - | {price_diff["mismatched_existing_skus"]} |\n')
            f.write(f'| 全部網站商品欄位差異商品 | - | {full_diff["mismatched_existing_skus"]} |\n')
            f.write(f'| 父分類原始資料才能取得的子分類 | - | {len(direct_unavailable)} 組 |\n')
            f.write(f'| 無法證實已到終點的分類 | - | {len(partitions_without_terminal)} 組 |\n\n')
            f.write("**不能將離線百分之百覆蓋認定為下一次真實能安全抓完。**\n")
    return report

if __name__=="__main__":
    if len(sys.argv)!=2:
        raise SystemExit("Usage: PYTHONPATH=src python frt/full_grocery_production_replay.py EXISTING_HF_CAPTURE.tar.gz")
    run(Path(sys.argv[1]))
