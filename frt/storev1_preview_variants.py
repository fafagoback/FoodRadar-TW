#!/usr/bin/env python3
"""Test whether larger sfNuggetCount produces the whole native UE store menu.

Exactly one store, 3 requests max, no retries, no production writes, no
challenge bypass. First failed response stops the trial.
"""
import json,requests,urllib.parse,time
from pathlib import Path
SID="54b6106a-8f4b-48ad-8014-7ba334513ee2"
LAT,LON=22.63312,120.35507
headers={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Safari/537.36",
 "Accept":"application/json","Accept-Language":"zh-TW,zh;q=0.9,en-US;q=0.8",
 "Content-Type":"application/json","x-csrf-token":"x"}
reference="高雄市座標點#foodradar"
location={"address":{"address1":reference,"address2":"","aptOrSuite":"",
 "city":"高雄市","country":"TW","postalCode":"100","region":""},
 "latitude":LAT,"longitude":LON,"reference":reference,
 "referenceType":"google_places","type":"google_places"}
session=requests.Session()
session.cookies.set("uev2.loc",urllib.parse.quote(json.dumps(location,ensure_ascii=False)),domain=".ubereats.com")
rows=[]
for count in (24,500,2000):
    if rows:time.sleep(1.6)
    row={"sfNuggetCount_requested":count}
    try:
        with session.post("https://www.ubereats.com/api/getStoreV1",
                    json={"storeUuid":SID,"sfNuggetCount":count},
                    headers=headers,stream=True,timeout=(10,28),allow_redirects=False) as resp:
            row["http"]=resp.status_code
            if resp.status_code!=200:
                rows.append(row);break
            raw=bytearray()
            for chunk in resp.iter_content(65536):
                raw.extend(chunk)
                if len(raw)>10_000_000:break
            row["response_bytes_read"]=len(raw)
            if len(raw)>10_000_000:
                row["error"]="BODY_TOO_LARGE_BOUNDED";rows.append(row);break
            envelope=json.loads(raw)
            data=envelope.get("data") if isinstance(envelope,dict) else {}
            if not isinstance(data,dict):data={}
            row["app_status"]=envelope.get("status")
            row["app_error"]=str(data.get("code") or "")+" "+str(data.get("message") or "")[:80]
            if row["app_status"]=="failure":
                rows.append(row);break
            groups=data.get("catalogSectionsMap") or {}
            skus=set();catalog_occurrences=0
            for entry in groups.values() if isinstance(groups,dict) else []:
                for group in entry if isinstance(entry,list) else []:
                    items=((group.get("payload") or {}).get("standardItemsPayload") or {}).get("catalogItems") or []
                    for item in items:
                        if isinstance(item,dict):
                            sku=item.get("uuid") or item.get("itemUuid")
                            if sku:
                                skus.add(sku);catalog_occurrences+=1
            row["native_catalog_distinct_skus"]=len(skus)
            row["native_catalog_occurrences"]=catalog_occurrences
            row["aisle_sections"]=sum(len(v) for v in (data.get("aisles") or {}).values() if isinstance(v,list))
            row["navigation_sections"]=len(data.get("sections") or [])
            row["catalogSectionPagingInfo"]=data.get("catalogSectionPagingInfo")
            row["getStoreV1_is_preview"]=bool(data.get("isPreview"))
            rows.append(row)
    except requests.RequestException as err:
        row["transport_error"]=type(err).__name__;rows.append(row);break
    except Exception as err:
        row["unexpected_error"]=type(err).__name__;rows.append(row);break
report={"store":"大全聯 鳳山店","source":"UE getStoreV1", "existing_UE_full_menu_baseline_skus":16753,
        "requests_made":len(rows),"requests_limit":3,"no_UE_retries":True,
        "no_production_writes":True,"results":rows}
Path("frt-storev1-preview-variants.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
print("FRT_STOREV1_PREVIEW_VARIANTS="+json.dumps(report,ensure_ascii=False),flush=True)
