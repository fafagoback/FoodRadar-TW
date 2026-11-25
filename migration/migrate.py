#!/usr/bin/env python3
"""Build FRT from a pinned FR checkout, with minimal national-only adaptations."""
from pathlib import Path
import json, sys, subprocess, shutil
root=Path.cwd()
def patch(p,old,new):
    p=Path(p); s=p.read_text(encoding="utf-8")
    if old not in s: raise RuntimeError(f"Missing anchor in {p}: {old[:100]}")
    p.write_text(s.replace(old,new,1),encoding="utf-8")
# Keep the internal FR wire/schema names stable. Only replace external resource names.
for p in root.rglob("*"):
    if not p.is_file() or ".git" in p.parts or p.suffix not in {".py",".js",".mjs",".cjs",".json",".md",".yml",".html",".txt"}:continue
    if ".github/workflows" in p.as_posix() or "migration" in p.relative_to(root).parts:continue
    try:s=p.read_text(encoding="utf-8")
    except UnicodeError:continue
    new=s.replace("hub-google/FoodRadar","hub-google/Foodradar-TW").replace("fafagoback/FoodRadar","fafagoback/FoodRadar-TW").replace("fafagoback.github.io/FoodRadar","fafagoback.github.io/FoodRadar-TW").replace("/functions/v1/fr-task-pool","/functions/v1/frt-task-pool").replace("foodradar:task-pool","foodradar-tw:task-pool")
    if new!=s:p.write_text(new,encoding="utf-8")
config=Path("config.json")
cfg=json.loads(config.read_text())
cfg.update(hf_repo_id="hub-google/Foodradar-TW",hf_folder="Raw",discovery_scope="taiwan",national_seed_file="data/seeds/taiwan_scan_points_3km_land_only.csv")
config.write_text(json.dumps(cfg,indent=2,ensure_ascii=False)+"\n")
# Add original 1,558 FRT grid points to the existing pinned FR national coverage.
# Existing points are the primary plan; FR points fill missing townships and islands.
p=Path("src/discovery_coverage.py")
patch(p,"    return township_points(r.json(),config.get(\"discovery_grid_spacing\",.04))",
'''    document=r.json()
    native=township_points(document,config.get("discovery_grid_spacing",.04))
    if config.get("national_seed_file"):
        from national_points import combine_seed_points
        return combine_seed_points(document,native,Path(config["national_seed_file"]))
    return native''')
p=Path("src/distributed_crawl.py")
patch(p,"def discover(config: dict, raw: Path, index: int, coverage=None):\n    plan=get_points(config)",
'''def discover(config: dict, raw: Path, index: int, coverage=None, *, plan=None, allow_empty=False):
    plan=get_points(config) if plan is None else validate_points(plan)''')
patch(p,"    if not unique or not total_pages:raise RuntimeError(\"Feed discovery returned no complete store list\")",
'''    if not total_pages or (not unique and not allow_empty):raise RuntimeError("Feed discovery returned no complete store list")''')
patch(p,"def save_shared_discovery(config: dict, root: Path = ROOT, dynamic=False):",
        "def save_shared_discovery(config: dict, root: Path = ROOT, dynamic=False, prepared=None):")
patch(p,'''    if folder.exists():
        raise FileExistsError(f"Refusing to overwrite existing discovery: {folder}")
    raw = folder / "raw"
    raw.mkdir(parents=True)
    coverage={}
    stores, pages = discover(config, raw, index=0, coverage=coverage)''',
'''    raw = folder / "raw"
    if prepared is None:
        if folder.exists():
            raise FileExistsError(f"Refusing to overwrite existing discovery: {folder}")
        raw.mkdir(parents=True)
        coverage={}
        stores, pages = discover(config, raw, index=0, coverage=coverage)
    else:
        if not raw.is_dir() or (folder/"discovery.json").exists():
            raise ValueError("Prepared discovery directory missing or already committed")
        stores,pages,coverage=prepared
        if not stores or not pages:raise ValueError("No nationwide discovery result")''')
patch(p,'len({(p["county"],p["town"]) for p in points})!=368',
        'len({(clean(p["county"]),p["town"]) for p in points if p.get("town")})!=368')
# Browser has no single-point test-mode restriction.
for f in ("scripts/configure_web.py","web/config.js"):
    p=Path(f); s=p.read_text();p.write_text(s.replace("SINGLE_POINT: true","SINGLE_POINT: false"))
# Workflow drafts are staged outside .github/workflows; promotion uses one atomic Git tree.
draft=Path("migration/workflows")
wf=draft/"crawl.yml"
s=wf.read_text()
s=s.replace("FoodRadar 01｜資料爬取與更新","FRT 01｜全台資料爬取與更新")
s=s.replace("    branches: [main]\n    paths: ['.github/workflows/crawl.yml']", "    branches: [main]\n    paths: ['.github/workflows/crawl.yml']")
start=s.index("      discovery_scope:\n"); end=s.index("      confirm_reset:\n",start)
s=s[:start]+s[end:]
s=s.replace("FR_DISCOVERY_SCOPE: ${{ inputs.discovery_scope || 'single' }}","FR_DISCOVERY_SCOPE: taiwan")
# The national production run must not be cancelled when a later scheduled run arrives.
start=s.index("concurrency:\n");end=s.index("jobs:\n",start)
s=s[:start]+"""concurrency:
  group: frt-crawl-production
  cancel-in-progress: false
"""+s[end:]
# Remove FR-only load-test routines, which are not part of production FRT.
start=s.index("  # 原生子分類"); end=s.index("  discover_once:",start)
s=s[:start]+s[end:]
# Put the existing FR regression job before any network requests and use 15 discovery runners.
start=s.index("  discover_once:")
national_jobs='''  discovery_plan:
    name: 全台掃描點位與回歸驗收
    if: ${{ github.event_name == 'schedule' || (github.event_name == 'workflow_dispatch' && inputs.mode == 'crawl') || (github.event_name == 'push' && contains(github.event.head_commit.message, 'FR_CRAWL_SPEED_TEST')) }}
    runs-on: ubuntu-latest
    timeout-minutes: 25
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: pip
          cache-dependency-path: requirements.txt
      - run: pip install -r requirements.txt
      - name: 執行回歸測試
        run: |
          env -u FR_DISCOVERY_SCOPE python -m unittest discover -s tests -v
          node --check web/app.js
          node --check web/static-catalog.js
      - name: 產生全台掃描點及15台分工
        run: python src/national_discovery.py plan
      - uses: actions/upload-artifact@v4
        with:
          name: frt-national-plan
          path: data/national-plan.json
          if-no-files-found: error
          retention-days: 3
  national_discovery_workers:
    name: "店家探索工作機 ${{ matrix.shard }}"
    needs: discovery_plan
    runs-on: ubuntu-latest
    timeout-minutes: 350
    strategy:
      fail-fast: false
      max-parallel: 15
      matrix:
        shard: [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14]
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: pip
          cache-dependency-path: requirements-crawl.txt
      - run: pip install -r requirements-crawl.txt
      - uses: actions/download-artifact@v4
        with:
          name: frt-national-plan
          path: data
      - name: 保存本台掃描點的原始 Feed HTTP
        run: python src/national_discovery.py worker --index ${{ matrix.shard }}
      - uses: actions/upload-artifact@v4
        with:
          name: frt-discovery-${{ matrix.shard }}
          path: data/national-worker
          if-no-files-found: error
          retention-days: 3
          compression-level: 1
'''
s=s[:start]+national_jobs+s[start:]
s=s.replace("    name: 統一探索設定範圍，建立 15 台共用任務池","    name: 全台店家合併去重，建立15台共用任務池\n    needs: national_discovery_workers",1)
start=s.index("      - name: 確認舊版執行已停止後才開始資料作業")
end=s.index("      - name: 取得程式碼",start)
s=s[:start]+s[end:]
start=s.index("      - name: 爬取前執行核心回歸測試",s.index("  discover_once:"))
end=s.index("      - name: 建立本次共用店家任務池",start)
s=s[:start]+'''      - uses: actions/download-artifact@v4
        with:
          name: frt-national-plan
          path: data
      - uses: actions/download-artifact@v4
        with:
          pattern: frt-discovery-*
          path: data/discovery-parts
      - name: 校驗全部點位、原始 HTTP 及 UUID 全台去重
        env:
          HF_TOKEN: ${{ secrets.HF_TOKEN }}
        run: python src/national_discovery.py merge
'''+s[end:]
# Keep the original FR menu, recovery, compact database, raw and deployment jobs intact.
wf.write_text(s)
for name,old,new in [("pages.yml","FoodRadar 02｜網站部署","FRT 02｜網站部署"),("cleanup.yml","FoodRadar 03｜儲存空間清理","FRT 03｜儲存空間清理")]:
    p=draft/name
    s=p.read_text().replace(old,new)
    p.write_text(s)
# Existing cleanup regex only handles FR names; also handle new national discovery artifacts
# but only after the original FR cleanup gate has verified Raw archival.
patch("scripts/cleanup_artifacts.py",'RAW = re.compile(r"(?:','RAW = re.compile(r"(?:frt-national-plan|frt-discovery-\\d+|')
Path("docs/FRT_MIGRATION.md").write_text("""# FRT / FoodRadar-TW
FR is the source of the Raw schema, dynamic menu workers, compact SQLite, price rules and static data contract.
FRT retains the original grid points; the pinned FR township geometry fills uncovered districts, including Kinmen and Lienchiang.
The 15 discovery workers produce the same FR Feed HTTP bodies and store entries. The reducer verifies every planned point, globally deduplicates canonical UUIDs and initializes the separate FRT Supabase queue.
HF target: hub-google/Foodradar-TW/Raw. The old hub-google/UberEat dataset is untouched; this version starts a clean FR-format history.
Schedule: 06:30 and 18:30 Taiwan time, then pages only after successful data/quality gate; cleanup at 03:00.
The first national run must validate capacity and speed; the FR static size safety limit is intentionally unchanged.
""")
