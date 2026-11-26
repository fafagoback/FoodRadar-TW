#!/usr/bin/env python3
"""Two-call, no-retry Uber consumer in-store search test for one PX Mart location.

This does not attempt to bypass access controls or infer hidden endpoints.
Stops on 403, 429, other errors. Does not write API payloads anywhere.
"""
import json, requests, urllib.parse, time, hashlib, os
from pathlib import Path

SID="db0e33e1-2eb6-5ac6-954d-fdedda8fe7b4"
LAT=25.115
LON=121.520
COUNTY="台北市"
LIMIT=5*1024*1024

def session():
    s=requests.Session()
    s.trust_env=True
    s.headers.update({
        "User-Agent":"Mozilla/5.0 (compatible; FoodRadarTW-Research/1.0; single-store bounded research)",
        "Accept":"application/json",
        "Accept-Language":"zh-TW,zh;q=0.9",
        "Content-Type":"application/json",
        "x-csrf-token":"x",
    })
    ref=f"{COUNTY}座標點#foodradar"
    cookie={"address":{"address1":ref,"address2":"","aptOrSuite":"","city":COUNTY,
            "country":"TW","postalCode":"100","region":""},"latitude":LAT,"longitude":LON,
            "reference":ref,"referenceType":"google_places","type":"google_places"}
    s.cookies.set("uev2.loc",urllib.parse.quote(json.dumps(cookie,ensure_ascii=False)),
                  domain=".ubereats.com")
    return s

def item_summary(obj):
    d=obj.get("data") if isinstance(obj,dict) else {}
    if not isinstance(d,dict):d={}
    catalog=d.get("catalogSectionsMap") or {}
    sku={}
    promotions=0
    for groups in catalog.values() if isinstance(catalog,dict) else []:
        for group in groups if isinstance(groups,list) else []:
            if not isinstance(group,dict):continue
            items=((group.get("payload") or {}).get("standardItemsPayload") or {}).get("catalogItems") or []
            for it in items:
                if not isinstance(it,dict):continue
                uuid=it.get("uuid") or it.get("itemUuid") or it.get("id")
                if uuid:
                    sku[str(uuid)] = it.get("price")
                    if it.get("itemPromotionAnalytics") or it.get("promotion"):
                        promotions+=1
    return {
        "catalog_section_keys":len(catalog),
        "catalog_unique_skus":len(sku),
        "catalog_items_with_numeric_price":sum(type(v) in (int,float) for v in sku.values()),
        "promotional_items_observed":promotions,
        "data_keys": sorted(d.keys())[:60],
        "hasMore":d.get("hasMore"),
        "paging_info":d.get("pagingInfo"),
        "app_status":obj.get("status") if isinstance(obj,dict) else None,
        "error_code":d.get("code"),
        "error_message":str(d.get("message") or "")[:120],
    }

def one_request(s,is_grocery):
    payload={"diningMode":"DELIVERY","sectionUUIDs":None,"storeUUIDs":[SID],
             "userQuery":"","isGrocery":is_grocery,"entrypointContext":"IN_STORE_SEARCH"}
    try:
        with s.post("https://www.ubereats.com/api/getInStoreSearchV1",
                    json=payload,stream=True,timeout=(8,20),allow_redirects=False) as r:
            code=r.status_code
            if code!=200:
                return {"http":code,"isGrocery":is_grocery},False
            size=0
            chunks=[]
            for chunk in r.iter_content(chunk_size=65536):
                size+=len(chunk)
                if size>LIMIT:
                    return {"http":code,"isGrocery":is_grocery,
                            "truncated":True,"observed_bytes":size},False
                chunks.append(chunk)
            try:
                obj=json.loads(b"".join(chunks))
            except Exception as e:
                return {"http":code,"isGrocery":is_grocery,"error":type(e).__name__},False
            return {"http":code,"isGrocery":is_grocery,"observed_bytes":size,
                    **item_summary(obj)},True
    except requests.exceptions.RequestException as exc:
        return {"isGrocery":is_grocery,"transport_error":type(exc).__name__},False

def main():
    results=[]
    with session() as s:
        for v in (False,True):
            result,ok=one_request(s,v)
            results.append(result)
            if not ok:break
            time.sleep(1.2)
    report={
        "source":"Uber Eats same-origin consumer getInStoreSearchV1",
        "store":"全聯福利中心 北投明德",
        "store_uuid":SID,
        "coordinates":{"lat":LAT,"lon":LON},
        "requests_to_UE":len(results),
        "max_requests":2,
        "results":results,
        "interpretation":"Empty query may be unsupported or only return a featured/search subset; only equality to independently validated full store inventory would prove complete catalog."
    }
    Path("frt-ue-single-store-search-probe.json").write_text(
        json.dumps(report,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print("FRT_UE_SINGLE_STORE_SEARCH="+json.dumps(report,ensure_ascii=False),flush=True)

if __name__=="__main__":
    main()
