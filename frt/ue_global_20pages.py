#!/usr/bin/env python3
"""Three-page max, native UE unfiltered catalog continuation test.
No retry / no bypass / no persistence of UE payload / no write to prod.
"""
import json, requests, urllib.parse, time
from pathlib import Path
SID="db0e33e1-2eb6-5ac6-954d-fdedda8fe7b4"; COUNTY="台北市"
LAT=25.115;LON=121.520;MAX=8*1024*1024
s=requests.Session()
s.headers.update({"User-Agent":"Mozilla/5.0 (compatible; FoodRadarTW-Research/1.0; bounded pagination)",
    "Accept":"application/json","Accept-Language":"zh-TW,zh;q=0.9","Content-Type":"application/json",
    "x-csrf-token":"x"})
ref=COUNTY+"座標點#foodradar"
loc={"address":{"address1":ref,"address2":"","aptOrSuite":"","city":COUNTY,"country":"TW",
      "postalCode":"100","region":""},"latitude":LAT,"longitude":LON,
     "reference":ref,"referenceType":"google_places","type":"google_places"}
s.cookies.set("uev2.loc",urllib.parse.quote(json.dumps(loc,ensure_ascii=False)),domain=".ubereats.com")
offset=0; pages=[]; known=set(); total_bytes=0
for i in range(20):
    payload={"sortAndFilters":None,
        "storeFilters":{"storeUuid":SID,"sectionUuids":None,"subsectionUuids":None},
        "pagingInfo":{"enabled":True,"offset":offset},"source":"NV_L2_CATALOG"}
    try:
        with s.post("https://www.ubereats.com/api/getCatalogPresentationV2",
                    json=payload,stream=True,timeout=(10,25),allow_redirects=False) as r:
            if r.status_code != 200:
                pages.append({"http":r.status_code,"offset_requested":offset});break
            buf=bytearray()
            for block in r.iter_content(chunk_size=65536):
                buf.extend(block)
                if len(buf)>MAX:break
            if len(buf)>MAX:
                pages.append({"http":r.status_code,"error":"TOO_LARGE","bytes_read":len(buf)});break
            obj=json.loads(buf)
    except Exception as exc:
        pages.append({"offset_requested":offset,"error":type(exc).__name__});break
    data=obj.get("data") if isinstance(obj,dict) else {}
    if not isinstance(data,dict): data={}
    if obj.get("status")!="success":
        pages.append({"offset_requested":offset,"app_status":obj.get("status"),
                      "error_code":data.get("code"),"message":str(data.get("message") or "")[:120]});break
    ids=set(); priced=set();categories=set()
    for row in data.get("catalog") or []:
        if not isinstance(row,dict):continue
        categories.add(str(row.get("catalogSectionUUID") or ""))
        items=((row.get("payload") or {}).get("standardItemsPayload") or {}).get("catalogItems") or []
        for it in items:
            if not isinstance(it,dict):continue
            uid=it.get("uuid") or it.get("itemUuid") or it.get("id")
            if uid:
                ids.add(uid)
                if type(it.get("price")) in (int,float):priced.add(uid)
    paging=data.get("pagingInfo") or {}
    nxt=paging.get("offset"); more=paging.get("hasMore")
    pages.append({"offset_requested":offset,"offset_returned":nxt,"hasMore":more,
        "sku_count":len(ids),"new_skus":len(ids-known),"duplicate_skus":len(ids&known),
        "sku_priced":len(priced),"category_ids":len(categories),"category_id_sample":sorted(categories)[:5],
        "response_bytes":len(buf)})
    known.update(ids);total_bytes+=len(buf)
    if more is not True or not isinstance(nxt,int) or nxt<=offset:break
    offset=nxt;time.sleep(1.6)
result={"store":"全聯福利中心 北投明德","store_uuid":SID,"endpoint":"getCatalogPresentationV2",
        "section_filter":None,"paging_enabled":True,"actual_calls":len(pages),
        "max_calls":20,"total_unique_skus":len(known),"bytes":total_bytes,
        "pages":pages,
        "caution":"No category may return a subset; cannot infer complete whole-store coverage from hasMore or limited pages."}
Path("frt-global-catalog-20pages.json").write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n",encoding="utf8")
print("FRT_GLOBAL_20PAGES="+json.dumps(result,ensure_ascii=False),flush=True)
