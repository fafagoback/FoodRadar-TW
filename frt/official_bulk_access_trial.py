#!/usr/bin/env python3
"""One-shot authorized access trial. No consumer scraping, retries or token logging."""
import json, os, urllib.request, urllib.error, urllib.parse
from pathlib import Path
STORE_ID="54b6106a-8f4b-48ad-8014-7ba334513ee2"  # 大全聯 鳳山店
LIMIT=4*1024*1024
def probe(url,headers):
    req=urllib.request.Request(url,headers=headers,method="GET")
    try:
        with urllib.request.urlopen(req,timeout=18) as res:
            b=res.read(LIMIT+1)
            return {"status":res.status,"mime":res.headers.get("Content-Type","")[:80],
                "bytes_read":len(b),"truncated":len(b)>LIMIT,
                "content_length":res.headers.get("Content-Length","")[:32]}
    except urllib.error.HTTPError as e:
        return {"status":e.code,"reason":str(e.reason)[:90]}
    except Exception as e:
        return {"error_kind":type(e).__name__,"reason":str(e)[:110]}
def main():
    token=os.getenv("FRT_UBER_EATS_MENU_ACCESS_TOKEN","").strip()
    h={"Accept":"application/json","Accept-Encoding":"identity","User-Agent":"FRT-ReadOnlyAccessTrial/1.0"}
    if token: h["Authorization"]="Bearer "+token
    uber=probe("https://api.uber.com/v2/eats/stores/"+STORE_ID+"/menus",h)
    uber.update({"test_store":"大全聯 鳳山店","store_uuid":STORE_ID,
        "authorized_token_configured":bool(token),"requests_made":1,
        "note":"Access needs authorized eats.store OAuth and store integration. Public UUID may not equal Marketplace integrated store ID."})
    feed=os.getenv("FRT_RETAILER_BULK_CATALOG_URL","").strip()
    feed_token=os.getenv("FRT_RETAILER_BULK_CATALOG_TOKEN","").strip()
    retailer={"feed_url_configured":bool(feed),"feed_token_configured":bool(feed_token),
        "requests_made":0}
    if feed:
        p=urllib.parse.urlparse(feed)
        if p.scheme!="https" or not p.hostname:
            retailer["result"]="REJECTED_NOT_HTTPS"
        else:
            headers={"Accept":"text/csv, application/zip, application/octet-stream","User-Agent":"FRT-ReadOnlyAccessTrial/1.0"}
            if feed_token: headers["Authorization"]="Bearer "+feed_token
            retailer["read_result"]=probe(feed,headers)
            retailer["requests_made"]=1
    else:
        retailer["result"]="NO_AUTHORIZED_RETAILER_FEED_CONFIGURED"
        retailer["note"]="Private retailer-to-Uber CSV/SFTP feed is not a public download."
    result={"type":"official-menu-and-retailer-bulk-access","UE_requests":0,
        "Uber_Developer_API_requests":1,"no_retries":True,"no_data_writes":True,
        "uber_menu":uber,"retailer_feed":retailer}
    Path("frt-official-bulk-access-trial.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    print("FRT_ACCESS_TRIAL="+json.dumps(result,ensure_ascii=False),flush=True)
    summary="# FRT 完整菜單 API / 零售商商品檔存取實測\n\n"
    summary+="大全聯鳳山店："+STORE_ID+"\n\n"
    summary+="## Uber Get Menu\n\n"+json.dumps(uber,ensure_ascii=False,indent=2)+"\n\n"
    summary+="## 零售商商品資料\n\n"+json.dumps(retailer,ensure_ascii=False,indent=2)+"\n\n"
    summary+="未經授權不能宣稱取得完整菜單；如果成功，仍需驗證商品數與資料欄位。\n"
    Path("frt-official-bulk-access-summary.md").write_text(summary,encoding="utf8")
    step=os.getenv("GITHUB_STEP_SUMMARY")
    if step:
        with open(step,"a",encoding="utf8") as fh: fh.write(summary)
if __name__=="__main__":main()
