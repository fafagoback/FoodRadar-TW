"""Remove obsolete FRT GitHub Actions registrations and all their completed runs.

Only the three production workflows are retained. This script does not touch
production runs, source archives, HF data, crawler-state or any other repo.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

KEEP = frozenset({
    ".github/workflows/crawl.yml",
    ".github/workflows/pages.yml",
    ".github/workflows/cleanup.yml",
})
REPO = os.environ.get("GH_REPO", "")
TOKEN = os.environ.get("GH_TOKEN", "")
if REPO != "fafagoback/FoodRadar-TW" or not TOKEN:
    raise SystemExit("Refusing to prune any non-FRT repository or unauthenticated session")
actual = {p.as_posix() for p in Path(".github/workflows").glob("*.yml")}
actual.update(p.as_posix() for p in Path(".github/workflows").glob("*.yaml"))
if actual != KEEP:
    raise SystemExit("Refusing cleanup: main workflow inventory differs: "+repr(sorted(actual)))

def api(method, endpoint, *, optional=False):
    url="https://api.github.com/repos/"+REPO+"/"+endpoint
    request=Request(url,method=method,headers={
        "Authorization": "Bearer "+TOKEN,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "FRT-Workflow-Prune",
    })
    for attempt in range(5):
        try:
            with urlopen(request, timeout=30) as response:
                body=response.read()
                return json.loads(body) if body else {}
        except HTTPError as exc:
            if optional and exc.code in (404,410,422):
                return {"skipped_http":exc.code}
            if exc.code in (403,429,500,502,503) and attempt<4:
                time.sleep(min(30,3*(attempt+1)))
                continue
            detail=exc.read().decode("utf-8","replace")[:450]
            raise RuntimeError(f"{method} {endpoint}: HTTP {exc.code} {detail}") from exc
        except (URLError,TimeoutError) as exc:
            if attempt==4:
                raise
            time.sleep(min(30,3*(attempt+1)))

def list_all(endpoint,key):
    out=[]
    for page in range(1,21):
        result=api("GET",f"{endpoint}{'&' if '?' in endpoint else '?'}per_page=100&page={page}")
        portion=result.get(key,[])
        out.extend(portion)
        if len(portion)<100:
            return out
    raise RuntimeError("Pagination exceeded 2000 records: "+endpoint)

workflows=list_all("actions/workflows","workflows")
registered={int(w["id"]):w for w in workflows}
keep_ids={wid for wid,w in registered.items() if w.get("path") in KEEP}
if len({registered[k].get("path") for k in keep_ids}) != 3:
    raise RuntimeError("Could not identify all three protected production workflows")
stale={wid:w for wid,w in registered.items() if wid not in keep_ids}
runs=list_all("actions/runs","workflow_runs")
obsolete=[run for run in runs if (
    run.get("workflow_id") not in keep_ids
    or run.get("path","").split("@",1)[0] not in KEEP
)]
blocked=[{"id":r["id"],"name":r.get("name"),"status":r.get("status")} for r in obsolete
         if r.get("status")!="completed"]
if blocked:
    raise RuntimeError("Refusing to remove active workflows: "+json.dumps(blocked))
deleted=[]
for run in obsolete:
    api("DELETE",f"actions/runs/{run['id']}")
    deleted.append({"id":run["id"],"workflow_id":run["workflow_id"],"name":run.get("name")})
    print("FRT_OBSOLETE_RUN_DELETED="+str(run["id"]),flush=True)
disabled=[]
for wid,w in stale.items():
    if w.get("state")=="active":
        result=api("PUT",f"actions/workflows/{wid}/disable",optional=True)
        disabled.append({"id":wid,"name":w.get("name"),"result":"disabled" if not result else result})
        print("FRT_OBSOLETE_WORKFLOW_DISABLED="+str(wid),flush=True)
remaining_runs=list_all("actions/runs","workflow_runs")
unexpected=[{"id":r["id"],"name":r.get("name"),"path":r.get("path")}
            for r in remaining_runs if (
                r.get("workflow_id") not in keep_ids
                or r.get("path","").split("@",1)[0] not in KEEP
            )]
if unexpected:
    raise RuntimeError("Obsolete runs still present: "+json.dumps(unexpected))
summary={
    "production_workflows":sorted(KEEP),
    "preserved_workflow_ids":sorted(keep_ids),
    "obsolete_workflow_registrations":len(stale),
    "deleted_obsolete_runs":len(deleted),
    "disabled_obsolete_workflows":disabled,
    "remaining_production_runs":len(remaining_runs),
    "verification":"No obsolete workflow run remains",
}
print("FRT_OBSOLETE_WORKFLOWS_FINAL="+json.dumps(summary,ensure_ascii=False),flush=True)
if os.getenv("GITHUB_STEP_SUMMARY"):
    with open(os.environ["GITHUB_STEP_SUMMARY"],"a",encoding="utf-8") as fp:
        fp.write("### FRT Actions 舊流程刪除驗收\n\n")
        fp.write("| 項目 | 結果 |\n|---|---:|\n")
        for label,key in [("刪除舊執行紀錄","deleted_obsolete_runs"),
                          ("非正式 workflow 註冊數","obsolete_workflow_registrations"),
                          ("保留正式流程","production_workflows"),
                          ("保留正式執行紀錄","remaining_production_runs")]:
            fp.write(f"| {label} | {len(summary[key]) if key=='production_workflows' else summary[key]} |\n")
