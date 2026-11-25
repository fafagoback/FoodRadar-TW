"""Reuse FRT's established coordinate grid; fill uncovered districts with FR's pinned plan."""
import csv
from shapely.geometry import Point, shape
from shapely.strtree import STRtree
from discovery_coverage import clean, validate_points

def combine_seed_points(document, native, seed):
    geometries=[]; identities=[]
    for feature in document["features"]:
        props={str(k).upper():v for k,v in feature["properties"].items()}
        county=clean(props.get("COUNTYNAME") or props.get("COUNTY") or props.get("COUNTY_NAME") or "")
        town=clean(props.get("TOWNNAME") or props.get("TOWN") or props.get("TOWN_NAME") or props.get("NAME") or "")
        geom=shape(feature["geometry"])
        if not geom.is_valid:
            from shapely import make_valid
            geom=make_valid(geom)
        geometries.append(geom); identities.append((county,town))
    tree=STRtree(geometries)
    seen=set(); coverage=set(); points=[]
    with seed.open(encoding="utf-8-sig",newline="") as f:
        for row in csv.DictReader(f):
            if row.get("is_on_land","TRUE").upper()!="TRUE":continue
            lat,lon=float(row["latitude"]),float(row["longitude"])
            key=(lat,lon)
            if key in seen:continue
            seen.add(key)
            source_county=clean(row["county"]).replace("桃園縣","桃園市")
            matches=sorted([identities[int(i)] for i in tree.query(Point(lon,lat),predicate="covered_by")])
            county,town=next((a for a in matches if a[0]==source_county),matches[0] if matches else (source_county,""))
            if town:coverage.add((county,town))
            points.append({"id":"legacy-"+row["id"],"latitude":lat,"longitude":lon,"county":county,"town":town})
    if not points:raise ValueError("FRT seed plan is empty")
    missing={(p["county"],p["town"]) for p in native}-coverage
    for p in native:
        if (p["county"],p["town"]) in missing:
            # Include all original FR interior points for an uncovered district.
            points.append(p)
    out=validate_points(sorted(points,key=lambda p:p["id"]))
    if {(p["county"],p["town"]) for p in out if p.get("town")} != {(p["county"],p["town"]) for p in native}:
        raise ValueError("National township coverage changed")
    return out
