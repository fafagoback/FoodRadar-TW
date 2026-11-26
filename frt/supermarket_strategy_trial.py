#!/usr/bin/env python3
"""FRT: offline supermarket catalog cost/coverage experiment.

Reads only the previously archived HF capture.tar.gz, never contacts Uber Eats.
Reports baselines vs actual subsets of captured native category pages.
Oracle subset savings are NOT claims of a safe future live update strategy.
"""
import collections
import hashlib
import json
import re
import sys
import tarfile
import time
from pathlib import Path

CATALOG = re.compile(r"(?:^|/)catalog/([0-9a-f-]{36})/.*?-section-([0-9]+)-([0-9a-f-]{36})-sub-(none|[0-9a-f-]{36})-")
# The actual 15 largest catalog archives found by the earlier complete HF audit.
CANDIDATES = [
    "754df551-af4b-48ad-8014-7ba99e6c9e98",
    "8406b8c1-c02d-4ea7-b24a-c05547619a65",
    "768d633d-c24d-47cc-8b6f-607f521f4426",
    "a973247c-4bf5-4fdd-8f4a-91bc6a9ed92f",
    "0ed026f9-8f03-513e-9589-6aa6ea280ed5",
    "dc6e6ca7-2f53-4ba8-a398-d6a4404407a6",
    "d75563ea-a72b-4691-817c-1b5354427326",
    "fc673ba5-5b0f-5003-9659-609729c31114",
    "17680a59-bf1b-4508-ad3a-82d6c4d3a496",
    "17c3f464-4178-47f9-b50a-0de4183e1f5e",
    "54b6106a-8f4b-48ad-8014-7ba99e6c9e98",
    "f14aefa0-b0de-451f-9d8a-41e39726ad45",
    "c296e7e7-504d-41ec-83c7-4998b561c234",
    "d3c5f77f-0705-430b-a038-806e16e28a11",
    "cba593d8-69e3-4f90-8521-eed74b0ec0ad",
]
GROCERY_MARKERS = ("家樂福", "全聯", "全聯福利中心", "大潤發", "愛買", "美廉社", "頂好",
                   "好市多", "carrefour", "px mart", "pxmart", "costco", "rt-mart",
                   "supermarket", "超市", "mart", "美廉")
PRICE_WORDS = ("price", "promo", "offer", "discount", "coupon", "bogo", "purchase", "deal",
               "availability", "soldout", "sold_out", "outofstock", "original",
               "quantity", "inventory", "unitprice", "stock")
CONTEXT_WORDS = {"sectionuuid", "subsectionuuid", "catalogsectionuuid", "sectiontitle",
                 "displayorder", "position", "rank"}

def json_hash(obj):
    return hashlib.blake2b(
        json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf8"),
        digest_size=12).hexdigest()

def value_projection(value):
    """Preserve every nested subtree under price/promotion/availability keys."""
    if isinstance(value, dict):
        return {k: v if any(t in k.lower() for t in PRICE_WORDS) else value_projection(v)
                for k, v in value.items()
                if any(t in k.lower() for t in PRICE_WORDS) or value_projection(v) not in (None, {}, [])}
    if isinstance(value, list):
        nested = [value_projection(v) for v in value]
        return [v for v in nested if v not in (None, {}, [])]
    return None

def item_signatures(item):
    sku = item.get("uuid") or item.get("itemUuid") or item.get("id")
    if not isinstance(sku, str) or not sku:
        return None
    # Use exact item JSON for the strict reference, plus business-field semantic hash.
    named = {k: v for k, v in item.items() if k.lower() in ("title", "name", "description", "quantity", "subtitle")}
    named.update(value_projection(item) or {})
    return sku, (sku, json_hash(named)), (sku, json_hash(item))

def section_key(match):
    _, idx, section, sub = match.groups()
    return (idx, section, sub)

def new_bucket():
    return {"pages": 0, "bytes": 0, "unique": set(), "business": set(), "exact": set(),
            "occurrences": 0, "failed": 0, "terminal": 0, "names": set()}

def flatten(group):
    sku, business, exact = set(), set(), set()
    for bucket in group:
        sku.update(bucket["unique"])
        business.update(bucket["business"])
        exact.update(bucket["exact"])
    return sku, business, exact

def evaluate(selected, all_buckets, baseline):
    buckets = [all_buckets[k] for k in selected]
    sku, business, exact = flatten(buckets)
    bsku, bbus, bex = baseline
    return {
        "requests": sum(b["pages"] for b in buckets),
        "raw_bytes": sum(b["bytes"] for b in buckets),
        "unique_skus": len(sku),
        "missing_skus": len(bsku - sku),
        "business_value_signatures": len(business),
        "missing_business_signatures": len(bbus - business),
        "exact_item_signatures": len(exact),
        "missing_exact_item_signatures": len(bex - exact),
        "request_reduction": sum(b["pages"] for b in all_buckets.values()) - sum(b["pages"] for b in buckets)
    }

def analyze(sid, row):
    groups = row["groups"]
    baseline = flatten(groups.values())
    by_parent = collections.defaultdict(list)
    for key in groups:
        by_parent[key[:2]].append(key)
    allkeys = set(groups)
    parent_only = {k for k in groups if k[2] == "none"}
    leaf_first = {k for k in groups if k[2] != "none"}
    # If a category has no native child response, retain its parent by default.
    for keys in by_parent.values():
        if not any(k[2] != "none" for k in keys):
            leaf_first.update(keys)
    # Offline oracle: add parent pages if a child group's price/promo/name signatures
    # do not cover the observed parent. This uses the old full capture as a reference.
    oracle = set(leaf_first)
    uncovered = []
    for keys in by_parent.values():
        parent = [k for k in keys if k[2] == "none"]
        child = [k for k in keys if k[2] != "none"]
        if not parent or not child:
            continue
        parent_biz = flatten(groups[k] for k in parent)[1]
        child_biz = flatten(groups[k] for k in child)[1]
        missing = parent_biz - child_biz
        if missing:
            oracle.update(parent)
            uncovered.append({"section": keys[0][1], "business_signatures_missing": len(missing)})
    # A second offline, cross-category set-cover lower-cost estimate. It cannot
    # predict future categories, prices or out-of-catalog missing products.
    greedy = set()
    remaining = set(baseline[1])
    while remaining:
        choices = [(len(groups[k]["business"] & remaining) / max(1, groups[k]["pages"]),
                    len(groups[k]["business"] & remaining), k)
                   for k in allkeys - greedy]
        if not choices:
            break
        score, n, best = max(choices)
        if n == 0:
            break
        greedy.add(best)
        remaining -= groups[best]["business"]
    plans = {
        "baseline": evaluate(allkeys, groups, baseline),
        "parents_only": evaluate(parent_only, groups, baseline),
        "leaf_first": evaluate(leaf_first, groups, baseline),
        "leaf_first_plus_oracle_parent_repair": evaluate(oracle, groups, baseline),
        "oracle_partition_set_cover": evaluate(greedy, groups, baseline)
    }
    return {
        "store_uuid": sid,
        "store_name": row.get("name", ""),
        "store_type": row.get("type", ""),
        "category_partitions": len(groups),
        "parent_sections": len(by_parent),
        "catalog_failed_bodies": row["failed"],
        "unverified_or_missing_terminal_partitions": sum(
            not b["terminal"] for b in groups.values() if b["pages"] > 0),
        "parent_sections_requiring_repair": len(uncovered),
        "parent_repair_details": uncovered[:20],
        "captured_getStoreV1": row.get("getstore", {}),
        "plans": plans,
        "warning": "Only an offline reconstruction of already captured data; zero missing signatures does not prove fresh/live catalog completeness or legal API access."
    }

def run(archive):
    t0 = time.monotonic()
    lookup = set(CANDIDATES)
    targets = {sid: {"groups": {}, "failed": 0} for sid in CANDIDATES}
    totals = collections.Counter()
    with tarfile.open(archive, "r|gz") as tar:
        for member in tar:
            if not member.isfile() or not member.name.endswith(".body"):
                continue
            path = member.name
            if "/catalog/" in "/" + path:
                match = CATALOG.search(path)
                if not match or match.group(1) not in lookup:
                    continue
                sid = match.group(1)
                bucket = targets[sid]["groups"].setdefault(section_key(match), new_bucket())
                bucket["pages"] += 1
                bucket["bytes"] += member.size
                totals["candidate_catalog_pages"] += 1
                try:
                    with tar.extractfile(member) as fh:
                        data = json.load(fh)
                    value = data.get("data") if isinstance(data, dict) else None
                    if not isinstance(value, dict) or not isinstance(value.get("catalog"), list):
                        raise ValueError("No valid catalog")
                    paging = value.get("pagingInfo") or {}
                    if paging.get("hasMore") is False:
                        bucket["terminal"] += 1
                    for category in value["catalog"]:
                        if not isinstance(category, dict):
                            continue
                        items = (((category.get("payload") or {}).get("standardItemsPayload") or {}).get("catalogItems") or [])
                        for item in items:
                            if not isinstance(item, dict):
                                continue
                            sig = item_signatures(item)
                            if sig is None:
                                continue
                            sku, biz, exact = sig
                            bucket["unique"].add(sku)
                            bucket["business"].add(biz)
                            bucket["exact"].add(exact)
                            bucket["occurrences"] += 1
                except (ValueError, TypeError, AttributeError, KeyError, json.JSONDecodeError):
                    bucket["failed"] += 1
                    targets[sid]["failed"] += 1
            elif path.startswith("stores/"):
                for sid in CANDIDATES:
                    if path.startswith("stores/" + sid + "-"):
                        try:
                            with tar.extractfile(member) as fh:
                                res = json.load(fh)
                            data = res.get("data") if isinstance(res, dict) else {}
                            if not isinstance(data, dict):
                                continue
                            name = str(data.get("title") or data.get("name") or "")
                            if name:
                                targets[sid]["name"] = name
                                targets[sid]["type"] = str(data.get("storeType") or "")
                            if "getstore" not in targets[sid]:
                                targets[sid]["getstore"] = {
                                    "catalogSectionsMap_count": len(data.get("catalogSectionsMap") or {}),
                                    "aisle_categories_count": sum(len(v) for v in (data.get("aisles") or {}).values() if isinstance(v, list)),
                                    "known_native_sections": len(data.get("sections") or [])
                                }
                        except (ValueError, TypeError, AttributeError, json.JSONDecodeError):
                            pass
                        break
            if totals["candidate_catalog_pages"] and totals["candidate_catalog_pages"] % 1500 == 0 and totals.get("last_progress") != totals["candidate_catalog_pages"]:
                totals["last_progress"] = totals["candidate_catalog_pages"]
                print("SUPERMARKET_AUDIT_PROGRESS=" + json.dumps({
                    "matched_pages": totals["candidate_catalog_pages"],
                    "elapsed_seconds": round(time.monotonic() - t0)
                }), flush=True)
    candidates = [analyze(sid, r) for sid, r in targets.items() if r["groups"]]
    candidates.sort(key=lambda r: (
        0 if any(term in r["store_name"].lower() for term in GROCERY_MARKERS) else 1,
        r["catalog_failed_bodies"] > 0,
        -r["plans"]["baseline"]["requests"]))
    if not candidates:
        raise RuntimeError("None of the audited largest store UUIDs occur in the archive")
    chosen = candidates[0]
    result = {
        "source": "HF hub-google/Foodradar-TW/Raw/20261126072649/capture.tar.gz",
        "network_uber_api_requests": 0,
        "candidate_store_names": [{"name": r["store_name"], "store_uuid": r["store_uuid"],
                                   "pages": r["plans"]["baseline"]["requests"],
                                   "failed": r["catalog_failed_bodies"]} for r in candidates],
        "chosen_store": chosen,
        "elapsed_seconds": round(time.monotonic() - t0, 1)
    }
    Path("frt-supermarket-strategy-trial.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
    print("FRT_SUPERMARKET_TRIAL_RESULT=" + json.dumps(result, ensure_ascii=False), flush=True)
    with Path("frt-supermarket-strategy-summary.md").open("w", encoding="utf8") as f:
        f.write("# FRT 大型店家｜真實 HF Raw 離線策略實驗\n\n")
        f.write("本實驗沒有向 UE 呼叫API；只是從已取得的真實大型店家HTTP回應測試不同請求集合的資料覆蓋。\n\n")
        f.write("選定店家：%s (%s)\n\n" % (chosen["store_name"], chosen["store_uuid"]))
        f.write("| 策略 | 回應數 | 節省 | 遺漏SKU | 遺漏價格/優惠/商品描述簽章 | 遺漏完整商品JSON簽章 |\n")
        f.write("|---|---:|---:|---:|---:|---:|\n")
        for label, p in chosen["plans"].items():
            f.write(f'| {label} | {p["requests"]} | {p["request_reduction"]} | {p["missing_skus"]} | {p["missing_business_signatures"]} | {p["missing_exact_item_signatures"]} |\n')
        f.write("\n注意：Oracle 修復與集合覆蓋法用到了完整原始資料的先驗資訊，不能直接當作下一次爬取可預知的結果。\n")
    print("FRT_SUPERMARKET_TRIAL_SUMMARY="+Path("frt-supermarket-strategy-summary.md").read_text(),flush=True)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: supermarket_strategy_trial.py EXISTING_CAPTURE.tar.gz")
    run(Path(sys.argv[1]))
