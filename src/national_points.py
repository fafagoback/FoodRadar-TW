"""Load only the original UE grid, with no coordinate generation or filtering."""
import csv,hashlib
from pathlib import Path
from discovery_coverage import validate_points

ORIGINAL_SEED_BLOB = "3748e9410f76e90217c4e1d1dcd41929076654c7"
ORIGINAL_POINT_COUNT = 1558

def load_seed_points(seed):
    seed=Path(seed)
    raw=seed.read_bytes()
    if hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest()!=ORIGINAL_SEED_BLOB:
        raise ValueError("Original UE seed file changed; no implicit point changes allowed")
    with seed.open(encoding="utf-8-sig",newline="") as f:
        points=[{"id":str(int(row["id"])),"latitude":float(row["latitude"]),
                 "longitude":float(row["longitude"]),"county":row["county"]}
                for row in csv.DictReader(f)]
    if len(points)!=ORIGINAL_POINT_COUNT:
        raise ValueError("Expected exactly 1558 original UE points")
    return validate_points(points)
