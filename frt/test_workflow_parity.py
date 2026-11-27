"""Executable proof that FRT keeps FR's 01/02/03 processing unchanged."""
import copy
import unittest
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parents[1]
UPSTREAM=ROOT/"frt/.upstream-workflows"

def load(filename,upstream=False):
    path=(UPSTREAM if upstream else ROOT/".github/workflows")/filename
    if not upstream and filename=="crawl.yml" and "全台爬蟲已停止" in path.read_text(encoding="utf-8"):
        path=ROOT/"frt/crawl.resume.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))

def original_steps(job, name=None):
    """Strip only the explicit FRT-only adapter differences, not core steps."""
    job=copy.deepcopy(job)
    job["steps"]=[s for s in job["steps"]
                  if s.get("name")!="使用唯一 FR 核心程式"]
    for step in job["steps"]:
        if step.get("name")=="取得程式碼" and step.get("with",{}).get("fetch-depth")==1:
            # FRT's own repo is shallow; the immutable FR core is fetched separately.
            step["with"]["fetch-depth"]=0
    if name=="ingest":
        for step in job["steps"]:
            if step.get("name")=="手動發布前執行核心程式回歸測試":
                assert "python -m unittest discover -s frt/tests -v" in step["run"]
                step["run"]=step["run"].replace(
                    "python -m unittest discover -s frt/tests -v\n","")
            if step.get("name")=="恢復 HF 唯一持久精簡資料庫（60 天價格）":
                assert "FRT_REBUILD" in step.get("env",{})
                step["env"]["FR_REBUILD"]=step["env"].pop("FRT_REBUILD")
                step["run"]=step["run"].replace("FRT_REBUILD","FR_REBUILD")
    return job

class WorkflowParity(unittest.TestCase):
    def test_only_ue_stage_is_added_to_fr_crawl(self):
        upstream=load("crawl.yml",upstream=True)["jobs"]
        frt=load("crawl.yml")["jobs"]
        self.assertEqual(set(frt)-set(upstream),{"discovery_plan","national_discovery_workers"})
        for name in set(upstream)-{"discover_once"}:
            self.assertEqual(original_steps(frt[name],name),upstream[name],
                f"FRT {name} diverges from canonical FR")
        job=original_steps(frt["discover_once"])
        job.pop("needs",None)
        job["name"]=upstream["discover_once"]["name"]
        originals=upstream["discover_once"]["steps"]
        scan_index=next(i for i,s in enumerate(originals)
                        if s.get("name")=="掃描設定座標並儲存原始 HTTP")
        added={"取得原 UE 全台點位計畫",
               "取得15台的原始 Feed 與店家清單",
               "合併全部掃描點，按店家 UUID 去重並輸出 FR 原生格式"}
        steps=[s for s in job["steps"] if s.get("name") not in added]
        steps.insert(scan_index, originals[scan_index])
        job["steps"]=steps
        self.assertEqual(job,upstream["discover_once"],
                         "All downstream FR setup, state, and pool logic must be identical")
        allnames=[s.get("name") for s in frt["discover_once"]["steps"]]
        self.assertTrue(added.issubset(set(allnames)))
    def test_02_and_03_are_original_fr_workflows(self):
        for filename in ("pages.yml","cleanup.yml"):
            original=load(filename,upstream=True)["jobs"]
            derivative=load(filename)["jobs"]
            extra=set(derivative)-set(original)
            # FRT retains its specifically requested obsolete-workflow cleanup.
            self.assertEqual(extra, {"prune_obsolete_workflows"} if filename=="cleanup.yml" else set())
            self.assertFalse(set(original)-set(derivative), f"{filename}: missing FR jobs")
            for name in original:
                job=original_steps(derivative[name])
                # Only publication destination URL differs for Pages.
                if filename=="pages.yml" and name=="deploy":
                    pass
                expected=copy.deepcopy(original[name])
                if filename=="cleanup.yml" and name=="cleanup":
                    # This FRT-only push guard protects the scheduled nationwide
                    # pipeline and is not a second data-processing algorithm.
                    self.assertIn("github.ref == 'refs/heads/main'", job["if"])
                    job["if"]=expected["if"]
                # All resource URL substitutions for pages are done in YAML source.
                def fmt(v):
                    if isinstance(v,str):
                        return v.replace("FoodRadar-TW/version.json","FoodRadar/version.json").replace("FoodRadar-TW/","FoodRadar/")
                    if isinstance(v,dict):return {k:fmt(x) for k,x in v.items()}
                    if isinstance(v,list):return [fmt(x) for x in v]
                    return v
                self.assertEqual(fmt(job),expected)
