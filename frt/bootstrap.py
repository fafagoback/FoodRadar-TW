#!/usr/bin/env python3
"""Activate the exact, pinned FR source in an FRT Actions workspace.

FR is the *only* source of application code. This repo retains only the UE
coordinate grid, the small national discovery adapter and environment values.
No FR source files are committed to FRT.
"""
import csv
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "config.json"
ORIGIN = "https://github.com/fafagoback/FoodRadar.git"
EXPECTED_POINTS = 1558

def points_from_ue_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as stream:
        records = list(csv.DictReader(stream))
    if len(records) != EXPECTED_POINTS:
        raise ValueError(f"Original UE scan grid must contain {EXPECTED_POINTS} rows; got {len(records)}")
    result = []
    ids = set()
    for row in records:
        ident = str(int(row["id"]))
        if ident in ids:
            raise ValueError(f"Duplicate UE coordinate ID {ident}")
        ids.add(ident)
        result.append({"id": ident, "latitude": float(row["latitude"]),
                       "longitude": float(row["longitude"]), "county": row["county"]})
    return result

def checkout_fr(destination, sha):
    subprocess.run(["git", "init", "-q", str(destination)], check=True)
    subprocess.run(["git", "-C", str(destination), "fetch", "-q", "--depth=1", ORIGIN, sha], check=True)
    subprocess.run(["git", "-C", str(destination), "checkout", "-q", "--detach", "FETCH_HEAD"], check=True)
    actual = subprocess.check_output(["git", "-C", str(destination), "rev-parse", "HEAD"], text=True).strip()
    if actual != sha:
        raise RuntimeError("Fetched FR source commit differs from pinned ref")

def replace_file(path, replacements):
    p = ROOT/path
    original = p.read_text(encoding="utf-8")
    content = original
    for old, new in replacements:
        if old not in content:
            raise RuntimeError(f"FR source contract changed; missing {old!r} in {path}")
        content = content.replace(old, new)
    p.write_text(content, encoding="utf-8")

def main():
    settings = json.loads(CFG.read_text(encoding="utf-8"))
    sha = settings["fr_core_sha"]
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise ValueError("FR source must be an immutable 40-character SHA")
    seed = ROOT / settings["scan_points_csv"]
    points = points_from_ue_csv(seed)
    old = ROOT/".fr-source"
    if old.exists():
        raise RuntimeError("Refusing pre-existing FR checkout")
    try:
        checkout_fr(old, sha)
        # Make upstream workflow definitions available for contract tests only.
        # They are not committed to FRT or used as a second workflow implementation.
        canonical = ROOT/"frt"/".upstream-workflows"
        canonical.mkdir(exist_ok=True)
        for filename in ("crawl.yml","pages.yml","cleanup.yml"):
            shutil.copy2(old/".github/workflows"/filename,canonical/filename)
        # Copy the official FR runtime only; preserve FRT's 3 workflows,
        # coordinate CSV, repo settings, README and adapter.
        for name in ("src", "scripts", "web", "tests", "docs", "PRD.md", "PRICE_V2.md",
                     "PROJECT_SPEC.md", "WORKFLOWS.md", "requirements.txt",
                     "requirements-crawl.txt", ".github/actions"):
            source = old/name
            if not source.exists():
                raise RuntimeError(f"Missing FR runtime component: {name}")
            destination = ROOT/name
            if destination.exists():
                raise RuntimeError(f"FRT contains a duplicate FR component: {name}")
            if source.is_dir():
                shutil.copytree(source, destination)
            else:
                shutil.copy2(source, destination)
        shutil.copy2(ROOT/"frt/national_discovery.py", ROOT/"src/national_discovery.py")
        # FRT values only. Algorithms and data contracts stay in FR.
        for path in [
            "src/raw_capture.py", "scripts/bootstrap_hf_dataset.py",
            "scripts/audit_beef_raw_http.py", "scripts/audit_failed_http_raw.py",
            "scripts/audit_full_catalog.py", "scripts/audit_static_catalog.py",
            "scripts/inspect_price_provenance.py", "scripts/repair_hf_batch.py",
            "scripts/retest_catalog_failures.py",
        ]:
            p = ROOT/path
            if p.exists():
                text = p.read_text(encoding="utf-8")
                text = text.replace("hub-google/FoodRadar", settings["hf_repo_id"])
                p.write_text(text, encoding="utf-8")
        replace_file("src/sb_task_pool.py", [
            ("/functions/v1/fr-task-pool", "/functions/v1/frt-task-pool"),
            ("foodradar:task-pool", "foodradar-tw:task-pool")
        ])
        replace_file("scripts/fr_clear_task_pool.py", [
            ("/functions/v1/fr-task-pool-clear", "/functions/v1/frt-task-pool-clear"),
            ("foodradar:task-pool-clear", "foodradar-tw:task-pool-clear")
        ])
        # The FR benchmark is intentionally isolated per repository, just
        # like the real task pool. Never send FRT load tests to FR endpoints.
        replace_file("scripts/fr_pool_load_test.py", [
            ("/functions/v1/fr-task-pool-benchmark", "/functions/v1/frt-task-pool-benchmark"),
            ("foodradar:task-pool-benchmark", "foodradar-tw:task-pool-benchmark"),
        ])
        # FR retains legacy UberEat helper defaults for its own use, but FRT
        # must not accidentally read or write the older UE dataset.
        for path in ("src/export_static_snapshots.py", "scripts/spot_v2_hf.py"):
            replace_file(path, [("hub-google/UberEat", settings["hf_repo_id"])])
        # Never provision or modify the FR repository from an FRT runner.
        replace_file("scripts/provision_foodradar.py", [
            ("fafagoback/FoodRadar", "fafagoback/FoodRadar-TW"),
        ])
        replace_file("scripts/configure_web.py", [("SINGLE_POINT: true", "SINGLE_POINT: false")])
        replace_file("web/config.js", [("SINGLE_POINT: true", "SINGLE_POINT: false")])
        # Output/diagnostic labels only. FRT 02/03 names remain consistent.
        for path in ("scripts/action_summary.py", "scripts/cleanup_artifacts.py"):
            p = ROOT/path
            text = p.read_text(encoding="utf-8")
            for old_label,new_label in [
                ("FoodRadar 01｜資料爬取與更新", "FRT 01｜全台資料爬取與更新"),
                ("FoodRadar 02｜網站部署", "FRT 02｜網站部署"),
                ("FoodRadar 03｜儲存空間清理", "FRT 03｜儲存空間清理"),
                ("https://fafagoback.github.io/FoodRadar/", "https://fafagoback.github.io/FoodRadar-TW/"),
                ("fafagoback/FoodRadar", "fafagoback/FoodRadar-TW"),
            ]:
                text = text.replace(old_label,new_label)
            p.write_text(text,encoding="utf-8")
        # This FR-only migration test requires legacy FR Git commits not present
        # in FRT. FRT does not import FR checkpoints; test its own digest below.
        test_file = ROOT/"tests/test_incremental_checkpoint.py"
        source = test_file.read_text(encoding="utf-8")
        marker = "    def test_real_layout_migration_exact_contract(self):"
        if source.count(marker) != 1:
            raise RuntimeError("FR historical-contract test changed; review required")
        test_file.write_text(source.replace(marker,
            '    @unittest.skip("FRT has no FR legacy Git history; FRT current digest is tested separately")\n'
            + marker),encoding="utf-8")
        # FR native configuration is the authoritative structure; only FRT
        # scanning coordinates and storage identity are overlaid.
        frconfig=json.loads((old/"config.json").read_text(encoding="utf-8"))
        frconfig.update(hf_repo_id=settings["hf_repo_id"],hf_folder=settings["hf_folder"],
                        discovery_scope="custom", discovery_points=points, max_pages=0)
        (ROOT/"config.json").write_text(json.dumps(frconfig,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        (ROOT/"fr-core-revision.txt").write_text(sha+"\n",encoding="utf-8")
        print(json.dumps({"fr_upstream_sha":sha,"fetched_core":"FoodRadar","FRT_UE_points":len(points),
                         "hf_repo_id":settings["hf_repo_id"],"hf_folder":settings["hf_folder"],
                         "frt_adapter":"src/national_discovery.py"},
                         ensure_ascii=False), flush=True)
    finally:
        if old.exists():
            shutil.rmtree(old)

if __name__ == "__main__":
    main()
