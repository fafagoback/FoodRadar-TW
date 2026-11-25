import sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
from national_points import combine_seed_points
class NationalPoints(unittest.TestCase):
    def test_existing_grid_and_missing_district(self):
        features=[]
        for county,town,x in [("臺北市","甲區",121),("金門縣","乙區",118)]:
            features.append({"properties":{"COUNTYNAME":county,"TOWNNAME":town},"geometry":{"type":"Polygon","coordinates":[[[x,24],[x+.1,24],[x+.1,24.1],[x,24.1],[x,24]]]}})
        native=[{"id":"native-a","county":"臺北市","town":"甲區","latitude":24.05,"longitude":121.05},
                {"id":"native-b","county":"金門縣","town":"乙區","latitude":24.05,"longitude":118.05}]
        with tempfile.TemporaryDirectory() as t:
            seed=Path(t)/"points.csv"
            seed.write_text("id,latitude,longitude,is_on_land,county\n1,24.02,121.02,TRUE,台北市\n2,24.02,121.02,TRUE,台北市\n")
            out=combine_seed_points({"features":features},native,seed)
        self.assertEqual([p["id"] for p in out],["legacy-1","native-b"])
        self.assertEqual(out[0]["town"],"甲區")
