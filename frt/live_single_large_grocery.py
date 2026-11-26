#!/usr/bin/env python3
"""Single-store UE live native complete supermarket menu: bounded trial only.

Never run this as a nationwide job. Uses 1.5 s minimum gaps between RPC calls,
390 total RPC cap including metadata, zero retries on 403/429/rejection, no
upstream deep pagination, no data uploads or production writes.
Known former 4 deep sections get only parent preview page, then genuine
native child UUID partitions. Other sections use pinned FR capture_catalog.
"""
import collections
import gzip
import tarfile
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
import requests
from catalog_complete import (
    _section_ids,_subsections_for,_native_subsections_from_catalog,_catalog_skus,
    _catalog_entries,capture_catalog,merge_catalog_pages,CATALOG_API
)
from menu_core import attach_delivery_location,convert_api_data_to_schema
from snapshot_validation import validate_document

SID="54b6106a-8f4b-48ad-8014-7ba334513ee2"
URL="https://www.ubereats.com/tw/store/%E5%A4%A7%E5%85%A8%E8%81%AF-%E9%B3%B3%E5%B1%B1%E5%BA%97/VLYQao9LSK2AFHujNFE-4g"
LAT,LON=22.63312,120.35507
MAX_CALLS=390
MIN_GAP=1.5
DEEP_EXPECTED={
    "f62042c6-3d53-58ad-8ac8-49ef0083a4d3":14,
    "320eb37d-3bdd-5547-bf21-a908e8394621":12,
    "7e846669-ef62-5139-baa5-834083527d46":8,
    "5a1ea087-b3cc-5f78-95f8-86c2d53dceea":10,
}
HEADERS={
    "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Safari/537.36",
    "Accept":"application/json","Accept-Language":"zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type":"application/json","x-csrf-token":"x"
}

class StopAll(RuntimeError):pass
class BoundedSession(requests.Session):
    """Hard cap and fail closed on any UE rejection; no automatic retry."""
    def __init__(self):
        super().__init__()
        self.count=0
        self.last_call=0.0
        self.total_bytes=0
        self.events=[]
        self.max_attempts=MAX_CALLS
        self.trust_env=True
    def post(self,url,**kwargs):
        if self.count>=MAX_CALLS:
            raise StopAll("Global single-store API request budget exceeded")
        remaining=MIN_GAP-(time.monotonic()-self.last_call)
        if remaining>0:time.sleep(remaining)
        self.count+=1
        self.last_call=time.monotonic()
        try:
            response=super().post(url,allow_redirects=False,**kwargs)
        except requests.RequestException as exc:
            raise StopAll("Transport error; do not retry: "+type(exc).__name__) from exc
        self.total_bytes+=len(response.content)
        event={"n":self.count,"endpoint":url.rsplit("/",1)[-1],"http":response.status_code}
        self.events.append(event)
        if response.status_code in (401,403,429) or response.status_code>=500:
            raise StopAll("UE rejected request: "+str(event))
        if response.status_code!=200:
            raise StopAll("Unexpected UE response: "+str(event))
        try: doc=response.json()
        except ValueError as exc:raise StopAll("Non-JSON UE response, stop") from exc
        if isinstance(doc,dict) and doc.get("status")=="failure":
            d=doc.get("data") or {}
            raise StopAll("UE application rejection; stop: code="+str(d.get("code")))
        return response

def save_body(root,relative,response):
    rel=Path(relative)
    if rel.is_absolute() or ".." in rel.parts:raise StopAll("Unsafe page path")
    path=root/rel
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(response.content)
    return relative

def doc_item_summary(doc):
    unique={}
    offers_by_kind=collections.Counter()
    price_valid=collections.Counter()
    for section in doc["hasMenu"]["hasMenuSection"]:
        for item in section.get("hasMenuItem") or []:
            sid=item.get("identifier")
            if not sid:continue
            unique.setdefault(sid,item)
    for item in unique.values():
        p=item.get("verified_price") or {}
        price_valid[p.get("status")]+=1
        offers_by_kind[p.get("kind")]+=1
    return {"unique_product_ids":len(unique),"price_verification_statuses":dict(price_valid),
            "offer_types":dict(offers_by_kind),
            "products_with_native_ue_menu_price":sum(
                type((i.get("verified_price") or {}).get("menu_price_cents")) is int
                for i in unique.values())}

def run():
    started=time.monotonic()
    report={
        "store":"大全聯 鳳山店","store_uuid":SID,"mode":"ONE_STORE_LIVE_BOUNDED",
        "source":"UE getStoreV1 + getCatalogPresentationV2 only",
        "all_raw_prices_native_UE":True,"request_limit":MAX_CALLS,
        "min_interval_seconds":MIN_GAP,
        "no_automatic_retries_on_errors":True,
        "no_production_writes":True,
        "complete":False
    }
    s=BoundedSession()
    root=Path(tempfile.mkdtemp(prefix="frt-grocery-single-live-"))
    try:
        ok=attach_delivery_location(s,{
            "discovered_points":[{"lat":LAT,"lon":LON,"county":"高雄市"}]})
        if not ok:raise StopAll("Invalid delivery location")
        base=s.post("https://www.ubereats.com/api/getStoreV1",
            json={"storeUuid":SID,"sfNuggetCount":24},headers=HEADERS,timeout=30)
        save_body(root,f"stores/{SID}-live-getStoreV1.body",base)
        data=base.json().get("data") or {}
        if not isinstance(data,dict) or "大全聯" not in str(data.get("title") or ""):
            raise StopAll("Store identity/name not verified from UE getStoreV1")
        sections=_section_ids(data)
        if len(sections)<20:raise StopAll("Suspiciously small actual native section roster")
        if (data.get("catalogSectionPagingInfo") or {}).get("hasMore") is True:
            raise StopAll("Store section roster itself hasMore; cannot claim complete")
        report["store_name_from_UE"]=data.get("title")
        report["getStoreV1_section_count"]=len(sections)
        report["expected_previous_skus"]=16476
        page_refs=[]
        inspected=[]
        deep_seen=set()
        for index,section in enumerate(sections):
            section_stage={"index":index,"section_uuid":section}
            if section in DEEP_EXPECTED:
                deep_seen.add(section)
                # One parent preview uncovers missing child UUIDs without
                # asking for parent offset 60..960 or crossing offset 1020.
                payload={"sortAndFilters":None,
                    "storeFilters":{"storeUuid":SID,"sectionUuids":[section],"subsectionUuids":None},
                    "pagingInfo":{"enabled":True,"offset":0},"source":"NV_L2_CATALOG"}
                r=s.post(CATALOG_API,json=payload,headers=HEADERS,timeout=30)
                preview=r.json()
                _,groups=_catalog_entries(preview)
                known=set(_subsections_for(data,section))
                appeared=set(_native_subsections_from_catalog([preview],section))
                children=sorted((known|appeared)-{section})
                expected=DEEP_EXPECTED[section]
                if len(children)<expected:
                    raise StopAll("Not enough native children for section "+section+
                                  ": "+str(len(children))+"/"+str(expected))
                preview_skus=_catalog_skus([preview])
                path=f"catalog/{SID}/live-section-{index:03d}-{section}-parent-preview-0000.body"
                save_body(root,path,r)
                page_refs.append({"path":path,"section_uuid":section,"mode":"parent-preview"})
                child_pages=[]
                child_bodies=[]
                for child in children:
                    result=capture_catalog(
                        s,SID,data,root,HEADERS,save_body,
                        section_indices=[index],subsection_path=[child],store_attempt=1)
                    if result.get("catalog_complete") is not True:
                        raise StopAll("Native child not terminal "+child)
                    child_pages.extend(result["catalog_responses"])
                    for rec in result["catalog_responses"]:
                        child_bodies.append(json.loads((root/rec["path"]).read_bytes()))
                child_skus=_catalog_skus(child_bodies)
                if preview_skus-child_skus:
                    raise StopAll("Deep parent preview has "+
                        str(len(preview_skus-child_skus))+
                        " SKUs omitted from native children; refuse incomplete store")
                page_refs.extend(child_pages)
                section_stage.update(mode="preview_plus_native_children",
                    children=len(children),parent_preview_skus=len(preview_skus),
                    child_unique_skus=len(child_skus),pages=len(child_pages)+1)
            else:
                # Normal, terminal native category pages with production checks.
                result=capture_catalog(s,SID,data,root,HEADERS,save_body,
                    section_indices=[index],store_attempt=1)
                if result.get("catalog_complete") is not True:
                    raise StopAll("Native section incomplete: "+section)
                page_refs.extend(result["catalog_responses"])
                section_stage.update(mode="native_terminal_parent",pages=len(result["catalog_responses"]))
            inspected.append(section_stage)
            print("FRT_LIVE_GROCERY_PROGRESS="+json.dumps({
                "sections_finished":len(inspected),"sections_total":len(sections),
                "UE_requests_so_far":s.count,"catalog_page_refs":len(page_refs),
                "last_section":section_stage
            },ensure_ascii=False),flush=True)
        if deep_seen!=set(DEEP_EXPECTED):
            raise StopAll("Known deep sections absent from current native roster")
        # Persist a replayable immutable raw capture BEFORE menu decoding.
        native_archive=Path("frt-live-px-fengshan-ue-native-pages.tar.gz")
        with tarfile.open(native_archive,"w:gz",compresslevel=3) as output:
            for file in sorted(root.rglob("*.body")):
                output.add(file,arcname=file.relative_to(root).as_posix())
        report["native_archive"]=str(native_archive)
        report["native_archive_bytes"]=native_archive.stat().st_size
        pages=[json.loads((root/rec["path"]).read_bytes()) for rec in page_refs]
        doc=convert_api_data_to_schema(merge_catalog_pages(data,pages),URL)
        validate_document(doc)
        summary=doc_item_summary(doc)
        # Save actual native UE prices and offers, not a simulated count.
        out=Path("frt-live-px-fengshan-full-menu.json.gz")
        with gzip.open(out,"wt",encoding="utf-8",compresslevel=7) as z:
            json.dump(doc,z,ensure_ascii=False,separators=(",",":"))
        report.update({"complete":True,"structural_completion":True,
            "independent_upstream_inventory_verified":False,
            "full_menu_artifact":str(out),"compressed_menu_bytes":out.stat().st_size,
            "sections_finished":len(inspected),"actual_catalog_page_refs":len(page_refs),
            "products":summary,"section_reports":inspected})
    except Exception as exc:
        traceback.print_exc()
        report.update({"complete":False,"failure_reason":str(exc)[:300],
                       "exception_type":type(exc).__name__})
    finally:
        # Persist every UE response even if menu conversion crashes.
        evidence=Path("frt-live-px-fengshan-ue-native-pages.tar.gz")
        if not evidence.exists():
            try:
                with tarfile.open(evidence,"w:gz",compresslevel=3) as tar:
                    for p in sorted(root.rglob("*.body")):
                        tar.add(p,arcname=p.relative_to(root).as_posix())
                report["native_archive"]=str(evidence)
                report["native_archive_bytes"]=evidence.stat().st_size
            except Exception as archive_error:
                report["native_archive_error"]=type(archive_error).__name__
        report.update({"UE_requests_total":s.count,
                       "UE_response_content_bytes":s.total_bytes,
                       "last_response_events":s.events[-8:],
                       "elapsed_seconds":round(time.monotonic()-started,1)})
        Path("frt-live-one-store-report.json").write_text(
            json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
        print("FRT_LIVE_ONE_STORE_RESULT="+json.dumps({
            k:v for k,v in report.items() if k!="section_reports"},ensure_ascii=False),flush=True)
        step=os.environ.get("GITHUB_STEP_SUMMARY")
        if step:
            with open(step,"a",encoding="utf8") as f:
                f.write("# FRT 真實 UE 大全聯鳳山單店即時完整菜單驗證\n\n")
                f.write(f'- 結果：{"PASS" if report["complete"] else "FAIL"}\n')
                f.write(f'- UE 允許內的實際請求：{s.count}（上限 {MAX_CALLS}）\n')
                f.write(f'- 失敗原因：{report.get("failure_reason","無")}\n')
                f.write(f'- 商品：{report.get("products",{}).get("unique_product_ids","無法確認")}\n')
                f.write("- 原始商品與價格來源：只使用 UE\n")
                f.write("- 注意：結構完整不代表 UE 額外提供獨立全店商品總數驗證。\n")
        s.close()
    return 0 if report["complete"] else 2

if __name__=="__main__":
    sys.exit(run())
