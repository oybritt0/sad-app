#!/usr/bin/env python3
"""Backfill census scopes (District + City) for already-saved drawn districts.

Drawn districts are saved without a persisted census summary — the batch M4 CLI
is unreliable for ad-hoc polygons, so their right-panel census scopes show
blank. This reads each drawn district's saved boundary and pulls ACS via the
same path the match server's /analyze endpoint uses, then writes:

    derived/census_summary.json            -> rec.census.sad        (District scope)
    derived/census_municipal_summary.json  -> rec.census.municipal  (City scope)

After running, rebuild the compare manifest so the scopes load on click:
    python build_compare_manifest.py --data-dir "...\\data"

(Metro scope is not produced for drawn districts and will read "—".)

Run from the code/ dir with the census key set, e.g. (PowerShell):
    $env:CENSUS_API_KEY = "..."
    python backfill_drawn_census.py --data-dir "...\\data"
    python backfill_drawn_census.py --data-dir "...\\data" --sads 43_Drawn-district_Ann-Arbor
    python backfill_drawn_census.py --data-dir "...\\data" --force
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import geopandas as gpd
from shapely.geometry import shape

import module_04_census_pull as m4
try:
    import module_04c_municipal_context as m4c
except Exception:
    m4c = None

EQUAL_AREA = "EPSG:5070"  # CONUS Albers, matches the server's area weighting


def summaries_for_boundary(geom_json, name, year, api_key):
    """(profile, municipal_payload) for a boundary, mirroring analyze_polygon's
    working ACS path. Returns (None, None) if no block groups intersect."""
    poly = shape(geom_json)
    if poly.is_empty or poly.area == 0:
        return None, None
    minlon, minlat, maxlon, maxlat = poly.bounds
    bgs = m4.fetch_block_groups_for_bbox((minlon, minlat, maxlon, maxlat), year=year)
    bgs = bgs[bgs.intersects(poly)].copy()
    if bgs.empty:
        return None, None
    acs = m4.fetch_acs_for_block_groups(bgs, year=year, api_key=api_key)
    bgs = bgs.merge(acs, on="GEOID", how="left")

    bgs_ea = bgs.to_crs(EQUAL_AREA)
    poly_ea = gpd.GeoSeries([poly], crs="EPSG:4326").to_crs(EQUAL_AREA).iloc[0]
    inter = bgs_ea.geometry.intersection(poly_ea).area
    bg_area = bgs_ea.geometry.area.replace(0, np.nan)
    bgs["intersection_area_ratio"] = inter.values / bg_area.values
    bgs["fully_inside_bbox"] = bgs.geometry.within(poly).values

    profile = m4.compute_summary(bgs, "drawn", name or "Drawn district", year)

    muni_payload = None
    if m4c is not None:
        try:
            c = poly.centroid
            mu = m4c.municipal_summary_for_point(c.x, c.y, year, api_key)
            if mu:
                muni_payload = {
                    "municipality": mu.get("municipality"),
                    "scope_summaries": {"municipal": mu.get("summary")},
                }
        except Exception as e:
            print(f"      (municipal scope skipped: {e})")
    return profile, muni_payload


def find_boundary(sad_dir: Path):
    """The drawn district's analysis boundary, as a bare GeoJSON geometry."""
    for rel in ("source/sad_boundary.geojson", "source/image_extent.geojson"):
        p = sad_dir / rel
        if not p.exists():
            continue
        gj = json.loads(p.read_text(encoding="utf-8"))
        if gj.get("type") == "FeatureCollection" and gj.get("features"):
            return gj["features"][0]["geometry"]
        if gj.get("type") == "Feature":
            return gj["geometry"]
        return gj  # already a geometry
    return None


def main():
    ap = argparse.ArgumentParser(description="Backfill census scopes for saved drawn districts.")
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--sads", default=None, help="comma list of SAD folder names; default = all *drawn* folders")
    ap.add_argument("--year", type=int, default=getattr(m4, "DEFAULT_ACS_YEAR", 2023))
    ap.add_argument("--force", action="store_true", help="rewrite even if census_summary.json already exists")
    args = ap.parse_args()

    api_key = os.environ.get("CENSUS_API_KEY")
    # If env key is missing or the Census API rejects it, fall back to the
    # known-working DEFAULT_CENSUS_KEY baked into sad_match_server.py.
    fallback = None
    try:
        import sad_match_server as _sms
        fallback = getattr(_sms, "DEFAULT_CENSUS_KEY", None)
    except Exception:
        pass
    if not api_key:
        if fallback:
            api_key = fallback
            print(f"CENSUS_API_KEY not set; using sad_match_server.DEFAULT_CENSUS_KEY.\n")
        else:
            print("Set CENSUS_API_KEY first (PowerShell: $env:CENSUS_API_KEY=\"...\").")
            return 1

    data = args.data_dir.resolve()
    if args.sads:
        names = [s.strip() for s in args.sads.split(",") if s.strip()]
    else:
        names = sorted(d.name for d in data.iterdir() if d.is_dir() and "drawn" in d.name.lower())
    if not names:
        print("No drawn districts found.")
        return 0

    print(f"Backfilling census for {len(names)} district(s), ACS {args.year}\n")
    ok = 0
    tried_fallback = False
    for nm in names:
        sd = data / nm
        out = sd / "derived" / "census_summary.json"
        if out.exists() and not args.force:
            print(f"  . {nm}: census_summary.json exists (use --force to redo)")
            continue
        geom = find_boundary(sd)
        if not geom:
            print(f"  ! {nm}: no source/sad_boundary.geojson")
            continue
        try:
            prof, muni = summaries_for_boundary(geom, nm, args.year, api_key)
        except (Exception, SystemExit) as e:
            msg = str(e)
            # Census returns an "Invalid Key" HTML page when the key is wrong,
            # and m4 calls sys.exit() on that — auto-retry once with
            # sad_match_server's known-working DEFAULT_CENSUS_KEY.
            if (not tried_fallback) and fallback and fallback != api_key and (
                'Invalid Key' in msg or 'non-JSON' in msg or 'Census API returned' in msg):
                print(f"  ! {nm}: Census rejected the env key; retrying with sad_match_server.DEFAULT_CENSUS_KEY.")
                api_key = fallback
                tried_fallback = True
                try:
                    prof, muni = summaries_for_boundary(geom, nm, args.year, api_key)
                except (Exception, SystemExit) as e2:
                    print(f"  ! {nm}: still failed after fallback: {e2}")
                    continue
            else:
                print(f"  ! {nm}: {e}")
                continue
        if not prof:
            print(f"  ! {nm}: no census block groups intersect the boundary")
            continue
        (sd / "derived").mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(prof, indent=2), encoding="utf-8")
        if muni:
            (sd / "derived" / "census_municipal_summary.json").write_text(
                json.dumps(muni, indent=2), encoding="utf-8")
        pop = prof.get("estimated_population")
        inc = prof.get("median_household_income_pop_weighted")
        print(f"  + {nm}: pop {pop} / income {inc} / municipal {'yes' if muni else 'no'}")
        ok += 1

    print(f"\nwrote {ok} census summary set(s).")
    print(f"now rebuild:  python build_compare_manifest.py --data-dir \"{data}\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
