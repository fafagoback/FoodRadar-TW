#!/usr/bin/env python3
"""Single direct UE category request without category filters; no retries or bypass."""
import requests,json,urllib.parse,time
from pathlib import Path
SID="db0e33e1-2eb6-5ac6-954d-fdedda8fe7b4"
COUNTY="台北市"; LAT=25.115; LON=121.520
MAX=16*1024*1024
s=requests.Session()
s.headers.update({"User-Agent":"Mozilla/5.0 (compatible; FoodRadarTW-Research/1.0; one request)",
                  "Accept":"application/json","Accept-Language":"zh-TW,zh;q=0.9",
                  "Content-Type":"application/json","x-csrf-token":"x"})
ref=COUNTY+"座標點#foodradar"
data={"address":{"address1":ref,"address2":"","aptOrSuite":"","city":COUNTY,
                 "country":"TW","postalCode":"100","region":""},
      "latitude":LAT,"longitude":LON,"reference":ref,
      "referenceType":"google_places","type":"google_places"}
s.cookies.set("uev2.loc",urllib.parse.quote(json.dumps(data,ensure_ascii=False)),domain=".ubereats.com")
payload={"sortAndFilters":None,
         "storeFilters":{"storeUuid":SID,"sectionUuids":None,"subsectionUuids":None},
         "pagingInfo":{"enabled":True,"offset":0,"pageSize":500},
         "source":"NV_L2_CATALOG"}
rpt={"store":"全聯福利中心 北投明德","store_uuid":SID,
     "endpoint":"getCatalogPresentationV2",
     "category_filter":None,"pagination_enabled":True,"requested_page_size":500,"UE_requests":1,"max_requests":1}
try:
    with s.post("https://www.ubereats.com/api/getCatalogPresentationV2",
                json=payload,stream=True,timeout=(10,25),allow_redirects=False) as r:
        rpt["http"]=r.status_code
        if r.status_code==200:
            content=bytearray()
            for b in r.iter_content(65536):
                content.extend(b)
                if len(content)>MAX:break
            rpt["observed_bytes"]=len(content)
            rpt["truncated"]=len(content)>MAX
            if not rpt["truncated"]:
                obj=json.loads(content)
                d=obj.get("data") if isinstance(obj,dict) else {}
                if not isinstance(d,dict):d={}
                skus=set()
                priced=set()
                for group in d.get("catalog") or []:
                    if not isinstance(group,dict):continue
                    items=((group.get("payload") or {}).get("standardItemsPayload") or {}).get("catalogItems") or []
                    for item in items:
                        if not isinstance(item,dict):continue
                        sku=item.get("uuid") or item.get("itemUuid") or item.get("id")
                        if sku:
                            skus.add(sku)
                            if type(item.get("price")) in (int,float):priced.add(sku)
                rpt.update({"app_status":obj.get("status"),
                            "app_code":d.get("code"),"app_message":str(d.get("message") or "")[:180],
                            "catalog_unique_skus":len(skus),"priced_skus":len(priced),
                            "paging_info":d.get("pagingInfo"),
                            "data_keys":list(d)[:20]})
except Exception as e:
    rpt["transport_error"]=type(e).__name__
    rpt["transport_message"]=str(e)[:100]
Path("frt-ue-pagesize500.json").write_text(json.dumps(rpt,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
print("FRT_UE_PAGESIZE500="+json.dumps(rpt,ensure_ascii=False),flush=True)
