#!/usr/bin/env python3
"""Read-only, offline analysis of an already archived FRT HTTP Raw capture.

No requests to Uber Eats; only an existing local tar.gz archive is read.
All counts are based on the actual archived body bytes, not crawler estimates.
"""
import collections
import hashlib
import heapq
import json
import os
import re
import sys
import tarfile
import time
from pathlib import Path

CATALOG = re.compile(
    r"(?:^|/)catalog/([0-9a-f-]{36})/"
    r".*?-section-([0-9]+)-([0-9a-f-]{36})-"
    r"sub-(none|[0-9a-f-]{36})-"
)
VALID_ID = re.compile(r"^[0-9a-f-]{20,}$")


def category(path):
    if path.startswith("feed/"):
        return "feed"
    if "/catalog/" in "/" + path:
        return "catalog"
    if path.startswith("stores/"):
        return "getStoreV1"
    if "section-refresh/" in path:
        return "metadata_refresh"
    return "other"


def percentile(numbers, rate):
    if not numbers:
        return None
    ordered = sorted(numbers)
    return ordered[round((len(ordered) - 1) * rate)]


def summary_counts(rows):
    result = {}
    for name, data in sorted(rows.items()):
        values = data.pop("sizes", [])
        data["min_bytes"] = min(values) if values else 0
        data["median_bytes"] = percentile(values, .5)
        data["p95_bytes"] = percentile(values, .95)
        data["max_bytes"] = max(values) if values else 0
        result[name] = dict(data)
    return result


def audit(archive):
    started = time.monotonic()
    counts = collections.defaultdict(lambda: {
        "files": 0, "bytes": 0, "exact_duplicate_files": 0,
        "exact_duplicate_bytes": 0, "sizes": []
    })
    overall = collections.Counter()
    content_hashes = {}
    top_biggest = []
    stores_with_catalog = set()
    current_store = None
    store_seen_skus = set()
    store_item_occurrences = 0
    sections = {}
    per_store_summary = []
    failed_catalog_bodies = 0
    parsed_catalog = 0
    no_uuid_items = 0
    missing_catalog_path_matches = 0
    # Per-store deduplication avoids keeping nationwide SKU sets in RAM.
    def flush_store():
        nonlocal sections, store_seen_skus, store_item_occurrences
        if current_store is None:
            return
        overall["unique_skus_by_store_sum"] += len(store_seen_skus)
        overall["sku_occurrences_by_store_sum"] += store_item_occurrences
        overall["repeated_sku_occurrences"] += max(0, store_item_occurrences - len(store_seen_skus))
        parent_child_pairs = 0
        parent_child_overlap = 0
        for section in sections.values():
            parent, child = section["parent"], section["child"]
            if parent and child:
                parent_child_pairs += 1
                common = len(parent & child)
                parent_child_overlap += common
                overall["parent_child_overlapping_sku_pairs"] += common
                overall["parent_unique_skus_in_pairs"] += len(parent)
                overall["child_unique_skus_in_pairs"] += len(child)
                overall["parent_pages_in_pairs"] += section["parent_pages"]
                overall["child_pages_in_pairs"] += section["child_pages"]
                overall["parent_bytes_in_pairs"] += section["parent_bytes"]
                overall["child_bytes_in_pairs"] += section["child_bytes"]
                if parent <= child:
                    overall["parent_child_pairs_parent_covered"] += 1
                    overall["parent_pages_in_covered_pairs"] += section["parent_pages"]
                    overall["parent_bytes_in_covered_pairs"] += section["parent_bytes"]
                else:
                    overall["parent_child_pairs_missing_items"] += 1
        overall["parent_child_pairs"] += parent_child_pairs
        per_store_summary.append({
            "store": current_store,
            "catalog_pages": overall_store_pages[0],
            "catalog_bytes": overall_store_pages[1],
            "distinct_skus": len(store_seen_skus),
            "item_occurrences": store_item_occurrences,
            "parent_child_pairs": parent_child_pairs,
            "parent_child_sku_overlap": parent_child_overlap,
        })
        sections = {}
        store_seen_skus = set()
        store_item_occurrences = 0

    overall_store_pages = [0, 0]
    with tarfile.open(archive, "r|gz") as pack:
        for member in pack:
            if not member.isfile() or not member.name.endswith(".body"):
                continue
            path = member.name
            if path.startswith("/") or ".." in Path(path).parts:
                raise RuntimeError("Unsafe archive member")
            stream = pack.extractfile(member)
            if stream is None:
                raise RuntimeError("Missing archive member")
            body = stream.read()
            size = len(body)
            if size != member.size:
                raise RuntimeError("Archive member truncated")
            tag = category(path)
            bucket = counts[tag]
            bucket["files"] += 1
            bucket["bytes"] += size
            bucket["sizes"].append(size)
            overall["files"] += 1
            overall["raw_bytes"] += size
            digest = hashlib.sha256(body).digest()
            if digest in content_hashes:
                bucket["exact_duplicate_files"] += 1
                bucket["exact_duplicate_bytes"] += size
                overall["exact_duplicate_files"] += 1
                overall["exact_duplicate_bytes"] += size
            else:
                content_hashes[digest] = (tag, size)
            if len(top_biggest) < 15:
                heapq.heappush(top_biggest, (size, path))
            elif size > top_biggest[0][0]:
                heapq.heapreplace(top_biggest, (size, path))
            if tag == "catalog":
                match = CATALOG.search(path)
                if match is None:
                    missing_catalog_path_matches += 1
                else:
                    sid, idx, section_id, sub = match.groups()
                    if current_store is not None and sid != current_store:
                        flush_store()
                        overall_store_pages = [0, 0]
                    if current_store != sid:
                        current_store = sid
                    stores_with_catalog.add(sid)
                    overall_store_pages[0] += 1
                    overall_store_pages[1] += size
                    try:
                        doc = json.loads(body)
                        rows = (doc.get("data") or {}).get("catalog")
                        if not isinstance(rows, list):
                            raise ValueError("No usable catalog")
                    except (ValueError, TypeError, AttributeError):
                        failed_catalog_bodies += 1
                        continue
                    parsed_catalog += 1
                    key = idx + ":" + section_id
                    section = sections.setdefault(key, {
                        "parent": set(), "child": set(), "parent_pages": 0,
                        "child_pages": 0, "parent_bytes": 0, "child_bytes": 0
                    })
                    dest = "parent" if sub == "none" else "child"
                    section[dest + "_pages"] += 1
                    section[dest + "_bytes"] += size
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        payload = row.get("payload") or {}
                        standard = payload.get("standardItemsPayload") or {}
                        items = standard.get("catalogItems") or []
                        if not isinstance(items, list):
                            continue
                        for item in items:
                            if not isinstance(item, dict):
                                continue
                            sku = item.get("uuid") or item.get("itemUuid") or item.get("id")
                            if not isinstance(sku, str) or not sku:
                                no_uuid_items += 1
                                continue
                            store_seen_skus.add(sku)
                            store_item_occurrences += 1
                            section[dest].add(sku)
            if overall["files"] % 50000 == 0:
                print("AUDIT_PROGRESS=" + json.dumps({
                    "files": overall["files"], "raw_gb": round(overall["raw_bytes"]/1e9,3),
                    "elapsed_s": round(time.monotonic()-started,1)
                }), flush=True)
    flush_store()
    largest_stores = sorted(per_store_summary, key=lambda r:r["catalog_bytes"], reverse=True)[:15]
    result = {
        "archive": str(archive), "archive_bytes": Path(archive).stat().st_size,
        "elapsed_seconds": round(time.monotonic()-started,1),
        "counts": summary_counts(counts),
        "total": dict(overall),
        "catalog_stores": len(stores_with_catalog),
        "parsed_catalog_bodies": parsed_catalog,
        "non_catalog_or_failed_json_bodies_in_catalog": failed_catalog_bodies,
        "catalog_path_unmatched": missing_catalog_path_matches,
        "items_without_uuid": no_uuid_items,
        "largest_files": [{"bytes":n,"path":p} for n,p in sorted(top_biggest,reverse=True)],
        "largest_catalog_stores": largest_stores,
        "per_store_catalog_stats": {
            "stores": len(per_store_summary),
            "stores_with_20plus_pages": sum(r["catalog_pages"] >= 20 for r in per_store_summary),
            "pages_from_20plus_stores": sum(r["catalog_pages"] for r in per_store_summary if r["catalog_pages"] >= 20),
            "bytes_from_20plus_stores": sum(r["catalog_bytes"] for r in per_store_summary if r["catalog_pages"] >= 20),
        },
        "warning": "A parent page covered by child pages is NOT automatically removable: it may reveal child IDs or validate completeness. Exact duplicate bodies do NOT prove that two API requests could safely be skipped across dates."
    }
    return result


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: audit_raw_archive.py CAPTURE.tar.gz")
    result = audit(Path(sys.argv[1]))
    Path("frt-raw-audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
    print("FRT_RAW_AUDIT_RESULT=" + json.dumps(result, ensure_ascii=False), flush=True)
    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step:
        with open(step, "a", encoding="utf8") as file:
            file.write("# FRT 原始封存檔｜離線稽核\n\n")
            file.write("本稽核只讀既有 HF Raw，未向 UE 發出任何 API 請求。\n\n")
            file.write("| 類型 | 檔案數 | 解壓後 GB | SHA 完全相同的重複檔案 |\n|---|---:|---:|---:|\n")
            for name, v in result["counts"].items():
                file.write(f"| {name} | {v['files']:,} | {v['bytes']/1e9:.3f} | {v['exact_duplicate_files']:,} |\n")
            t = result["total"]
            file.write(f"\n總計 {t['files']:,} 份，{t['raw_bytes']/1e9:.3f} GB；完全相同回應 {t.get('exact_duplicate_files',0):,} 份、{t.get('exact_duplicate_bytes',0)/1e9:.3f} GB。\n")
            file.write(f"\n商品出現次數 {t.get('sku_occurrences_by_store_sum',0):,}，按單店商品 UUID 去重後 {t.get('unique_skus_by_store_sum',0):,}，重複 {t.get('repeated_sku_occurrences',0):,} 次。\n")
            file.write(f"\n父子分類同時存在 {t.get('parent_child_pairs',0):,} 組，父分類 SKU 全被子分類覆蓋 {t.get('parent_child_pairs_parent_covered',0):,} 組。\n")
            file.write("\n注意：上述「可覆蓋」只證實資料重疊，不代表不做父分類請求仍然能找到全部子分類。\n")


if __name__ == "__main__":
    main()
