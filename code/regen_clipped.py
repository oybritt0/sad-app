#!/usr/bin/env python3
"""Regenerate derived buildings + enrich for clipped drawn districts.
For each district where source/buildings.geojson was clipped but
derived/buildings_enriched.geojson is still stale (larger), re-run:
  make_drawn_buildings -> convert_enriched_buildings (M5geo) -> M3d
"""
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path

PY = "/opt/venv/bin/python3"
CODE = "/app/code"
DATA = "/data"

# The 13 clipped districts (everything the clip touched)
TARGETS = [
    "46_Drawn-district_East-Lansing",
    "47_Drawn-district_Kalamazoo",
    "49_Drawn-district_Durham",
    "50_Drawn-district_Tulsa",
    "53_Drawn-district_Omaha",
    "54_Drawn-district_Des-Moines",
    "55_Drawn-district_Cleveland",
    "56_Drawn-district_Rochester",
    "57_Drawn-district_Nashville-Davidson-metropolitan-government-balance",
    "58_Drawn-district_Oklahoma-City",
    "59_Drawn-district_Philadelphia",
    "60_Drawn-district_Philadelphia",
    "61_Drawn-district_Fort-Wayne",
]

def count(p):
    try:
        return len(json.load(open(p))["features"])
    except Exception:
        return None

def run(cmd):
    r = subprocess.run(cmd, cwd=CODE, capture_output=True, text=True, timeout=600)
    return r.returncode, (r.stdout or "") + (r.stderr or "")

ok = fail = 0
for i, sad in enumerate(TARGETS, 1):
    der = f"{DATA}/{sad}/derived"
    src = f"{DATA}/{sad}/source"
    src_n = count(f"{src}/buildings.geojson")
    der_n_before = count(f"{der}/buildings_enriched.geojson")
    print(f"[{i}/{len(TARGETS)}] {sad}  (source={src_n}, derived_before={der_n_before})", flush=True)

    # Skip if already regenerated (derived matches source)
    if src_n is not None and der_n_before == src_n:
        print(f"        already regenerated (derived={der_n_before}), skipping")
        ok += 1
        continue

    # 1. make_drawn_buildings -> gpkg from clipped source
    rc, out = run([PY, "make_drawn_buildings.py", "--data-dir", DATA, "--sad", sad])
    if rc != 0:
        print(f"        FAIL make_drawn_buildings: {out.strip().splitlines()[-2:]}")
        fail += 1
        continue
    # 2. M5geo -> geojson from gpkg
    rc, out = run([PY, "convert_enriched_buildings.py", "--derived", der])
    if rc != 0:
        print(f"        FAIL M5geo: {out.strip().splitlines()[-2:]}")
        fail += 1
        continue
    # 3. M3d -> enrich
    rc, out = run([PY, "module_3d_building_attrs.py", "--source", src, "--derived", der])
    if rc != 0:
        print(f"        FAIL M3d: {out.strip().splitlines()[-2:]}")
        fail += 1
        continue

    # report
    der_n_after = count(f"{der}/buildings_enriched.geojson")
    d = json.load(open(f"{der}/buildings_enriched.geojson"))
    feats = d["features"]
    nsi = sum(1 for x in feats if x["properties"].get("occtype") is not None)
    fema = sum(1 for x in feats if x["properties"].get("fema_height") is not None)
    pn = (100*nsi//der_n_after) if der_n_after else 0
    pf = (100*fema//der_n_after) if der_n_after else 0
    print(f"        OK  derived={der_n_after}, NSI={nsi} ({pn}%), FEMA={fema} ({pf}%)")
    ok += 1

print(f"\n=== regen done: OK={ok}, FAIL={fail} ===")