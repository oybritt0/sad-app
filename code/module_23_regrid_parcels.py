#!/usr/bin/env python3
"""
Module 23: Regrid parcels per SAD.

Pulls parcels from the Regrid Parcel API v2 for each SAD boundary, caches the
raw GeoJSON, and computes a planner-relevant summary (zoning breakdown, use
codes, ownership concentration, year-built histogram, vacant/exempt shares,
building density). Outputs are read into rec.parcels by build_compare_manifest.

The Regrid trial token has a hard cap on total parcel records returned
(2,000 for the Premium bundle), so this module:
  - Calls account/usage before each pull (free per Regrid's docs).
  - Skips a district whose pull would drop remaining records below the reserve.
  - Logs every spend to data/_shared/regrid_ledger.json.
  - Stops gracefully when the budget approaches zero.

Two run modes:

  Single SAD:
    python module_23_regrid_parcels.py --sad-dir "<path>/02_..."

  By similarity, Detroit-pivot:
    python module_23_regrid_parcels.py \\
      --data-dir "<path>/data" \\
      --start-from 32_District-Detroit_Detroit-MI

  (processes pivot first, then SADs in ascending distance-matrix order)
"""
from __future__ import annotations
import argparse
import json
import math
import os
import sys
import time
from collections import Counter
from pathlib import Path

import requests

# ── Trial token (Premium + matched buildings + zoning); env REGRID_TOKEN overrides ──
DEFAULT_REGRID_TOKEN = (
    "eyJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJyZWdyaWQuY29tIiwiaWF0IjoxNzgwOTUzOTA2LCJleHAi"
    "OjE3ODM1NDU5MDYsInUiOjgxMDI2NCwiZyI6MjMxNTMsImNhcCI6InBhOnRzOnBzOmJmOm1hOnR5"
    "OmVvOnpvOnNiIn0.b_0FidjHBiuhlcOqyLmCQ7FVR6zEdJ6SzDSVU5OQ1uA"
)
REGRID_API = "https://app.regrid.com/api/v2"
PAGE_LIMIT = 1000           # max parcels per page (Regrid default)
MIN_BUDGET_RESERVE = 50     # never drop below this many records
TRIAL_PARCEL_CAP = 2000     # trial token hard cap on parcel records
HTTP_TIMEOUT = 60
RATE_RETRIES = 3
RATE_BASE = 2


def regrid_token():
    return os.environ.get("REGRID_TOKEN") or DEFAULT_REGRID_TOKEN


# ── HTTP helpers with rate-limit backoff ────────────────────────────────────────
def _request(method, url, **kwargs):
    for i in range(RATE_RETRIES + 1):
        try:
            r = requests.request(method, url, timeout=HTTP_TIMEOUT, **kwargs)
        except requests.exceptions.RequestException as e:
            if i < RATE_RETRIES:
                wait = RATE_BASE ** (i + 1)
                print(f"    network error: {e}; retry in {wait}s")
                time.sleep(wait)
                continue
            raise
        if r.status_code == 429 and i < RATE_RETRIES:
            wait = RATE_BASE ** (i + 1)
            print(f"    rate-limited; waiting {wait}s")
            time.sleep(wait)
            continue
        if not r.ok:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        return r.json()
    return None


def account_usage(token):
    """Returns {used, remaining, raw}. The OpenAPI spec puts billed parcel
    records at usage.cycle_usage.results; remaining is derived against the
    trial cap since the API doesn't report it directly."""
    try:
        data = _request("GET", f"{REGRID_API}/usage", params={"token": token})
    except Exception as e:
        print(f"    (couldn't read /usage: {e})")
        return {"used": None, "remaining": None, "raw": None}
    inner = data.get("usage", {}) if isinstance(data, dict) else {}
    cycle = inner.get("cycle_usage", {}) if isinstance(inner, dict) else {}
    used = cycle.get("results")
    if used is None:
        # fall back to any of the older flat key names just in case
        used = (inner.get("parcel_records_used") or inner.get("results")
                or inner.get("used"))
    try:
        used = int(used) if used is not None else None
    except (TypeError, ValueError):
        used = None
    remaining = (TRIAL_PARCEL_CAP - used) if used is not None else None
    return {"used": used, "remaining": remaining, "raw": data}


def _extract_features(resp):
    """Pull the feature list out of any plausible response shape."""
    if not isinstance(resp, dict):
        return []
    p = resp.get("parcels")
    if isinstance(p, dict) and "features" in p:
        return p.get("features") or []
    if resp.get("type") == "FeatureCollection":
        return resp.get("features") or []
    if isinstance(resp.get("results"), list):
        return resp["results"]
    if isinstance(resp.get("features"), list):
        return resp["features"]
    return []


def _feature_id(feat):
    """A stable id for pagination (offset_id in Regrid v2)."""
    if not isinstance(feat, dict):
        return None
    if "id" in feat:
        return feat["id"]
    props = feat.get("properties") or {}
    if "ogc_fid" in props:
        return props["ogc_fid"]
    fields = props.get("fields") or {}
    return fields.get("ogc_fid") or fields.get("id")


def probe_count(token, geom):
    """How many parcels lie in this polygon? Uses return_count=true plus
    return_parcels=false — the response carries the count without returning
    parcel records, so it doesn't bill against the trial cap. Returns an int,
    or None if the API didn't report a count."""
    body = {"token": token, "geojson": geom,
            "return_count": True, "return_parcels": False}
    try:
        resp = _request("POST", f"{REGRID_API}/parcels/query", json=body)
    except Exception as e:
        print(f"    probe failed: {e}")
        return None
    if not isinstance(resp, dict):
        return None
    for path in (("parcels", "count"), ("count",), ("total",),
                 ("parcels", "total"), ("results_count",)):
        cur = resp
        ok = True
        for k in path:
            if not isinstance(cur, dict) or k not in cur:
                ok = False; break
            cur = cur[k]
        if ok and isinstance(cur, (int, float)):
            return int(cur)
    return None


def fetch_parcels_for_polygon(token, geom, budget_remaining):
    """Paginate parcels/query for the polygon, respecting the budget."""
    features = []
    offset_id = None
    while True:
        body = {"token": token, "geojson": geom, "limit": PAGE_LIMIT}
        if offset_id is not None:
            body["offset_id"] = offset_id
        if budget_remaining is not None:
            remaining_for_call = max(0, budget_remaining - len(features))
            if remaining_for_call <= MIN_BUDGET_RESERVE:
                print(f"    budget reserve reached at {len(features)} features; stopping pagination")
                break
            body["limit"] = min(PAGE_LIMIT, remaining_for_call)
        resp = _request("POST", f"{REGRID_API}/parcels/query", json=body)
        new = _extract_features(resp)
        if not new:
            break
        features.extend(new)
        if len(new) < body["limit"]:
            break   # last page
        oid = _feature_id(new[-1])
        if not oid:
            break
        offset_id = oid
        time.sleep(0.2)   # be polite
    return features


# ── Summary aggregation ────────────────────────────────────────────────────────
def _prop(feat, *keys):
    """Pull a property, checking properties[] then properties.fields[]."""
    if not isinstance(feat, dict):
        return None
    props = feat.get("properties") or {}
    fields = props.get("fields") or {}
    for k in keys:
        v = props.get(k) if k in props else fields.get(k)
        if v is not None and v != "":
            return v
    return None


def _gini(values):
    """Gini coefficient on a list of non-negative values (sorted ascending)."""
    vals = sorted(float(v) for v in values if v is not None and v > 0)
    n = len(vals)
    if n == 0:
        return None
    if n == 1:
        return 0.0
    s = sum(vals)
    if s == 0:
        return 0.0
    cum = sum(i * v for i, v in enumerate(vals, 1))
    return (2 * cum) / (n * s) - (n + 1) / n


def _shannon(counts):
    total = sum(counts)
    if total == 0:
        return None
    return -sum((c / total) * math.log(c / total) for c in counts if c > 0)


def compute_summary(features):
    if not features:
        return {"parcel_count": 0}

    acres = []
    use_descs = Counter(); use_codes = Counter()
    zoning_std = Counter(); zoning_raw = Counter()
    years = []
    owner_acres = {}
    bldg_per_acre = []
    has_bldg = 0; no_bldg = 0
    exempt = 0
    land_val_total = 0.0; land_val_n = 0
    bldg_val_total = 0.0; bldg_val_n = 0

    for f in features:
        ac = _prop(f, "ll_gisacre", "gisacre", "lot_acres")
        try:
            ac = float(ac) if ac is not None else None
        except (TypeError, ValueError):
            ac = None
        if ac and ac > 0:
            acres.append(ac)

        ud = _prop(f, "usedesc", "lbcs_function_desc", "use_description")
        if ud:
            use_descs[str(ud).strip()] += 1
        uc = _prop(f, "usecode", "lbcs_function", "use_code")
        if uc:
            use_codes[str(uc).strip()] += 1

        zs = _prop(f, "zoning_type", "lbcs_activity_desc", "standardized_zoning",
                   "zoning_subtype")
        if zs:
            zoning_std[str(zs).strip()] += 1
        zr = _prop(f, "zoning", "zoning_description", "zoning_id")
        if zr:
            zoning_raw[str(zr).strip()] += 1

        yb = _prop(f, "yearbuilt", "year_built", "ll_yearbuilt")
        try:
            yb = int(yb) if yb is not None else None
        except (TypeError, ValueError):
            yb = None
        if yb and 1700 < yb < 2050:
            years.append(yb)

        own = _prop(f, "owner")
        if own and ac:
            owner_acres[str(own).strip().upper()] = (
                owner_acres.get(str(own).strip().upper(), 0.0) + ac)

        bs = _prop(f, "bldg_sqft", "ll_bldg_footprint_sqft", "building_sqft",
                   "improvement_sqft")
        try:
            bs = float(bs) if bs is not None else None
        except (TypeError, ValueError):
            bs = None
        if bs and bs > 0:
            has_bldg += 1
            if ac and ac > 0:
                bldg_per_acre.append(bs / ac)
        else:
            no_bldg += 1

        if _prop(f, "tax_exempt", "exemption", "exempt"):
            exempt += 1

        lv = _prop(f, "land_value", "gisland_value", "ll_land_value")
        try:
            lv = float(lv) if lv is not None else None
        except (TypeError, ValueError):
            lv = None
        if lv:
            land_val_total += lv; land_val_n += 1
        bv = _prop(f, "bldg_value", "gisbldg_value", "improvement_value",
                   "ll_bldg_value")
        try:
            bv = float(bv) if bv is not None else None
        except (TypeError, ValueError):
            bv = None
        if bv:
            bldg_val_total += bv; bldg_val_n += 1

    n = len(features)
    total_ac = sum(acres) if acres else None

    yb_hist = {}
    yb_med = None
    if years:
        for y in years:
            dec = (y // 10) * 10
            yb_hist[str(dec)] = yb_hist.get(str(dec), 0) + 1
        ys = sorted(years)
        yb_med = ys[len(ys) // 2]

    owner_sorted = sorted(owner_acres.items(), key=lambda kv: -kv[1])
    owner_gini = _gini(list(owner_acres.values())) if owner_acres else None
    top5_share = ((sum(v for _, v in owner_sorted[:5]) / total_ac)
                  if (owner_sorted and total_ac) else None)
    zoning_div = _shannon(list(zoning_std.values())) if zoning_std else None
    mean_density = (sum(bldg_per_acre) / len(bldg_per_acre)) if bldg_per_acre else None
    vacant_share = (no_bldg / n) if n else None

    def _r(v, k=3):
        return round(v, k) if isinstance(v, (int, float)) and v == v else v

    return {
        "parcel_count": n,
        "total_acres": _r(total_ac, 2),
        "use_desc_top": [(k, v) for k, v in use_descs.most_common(10)],
        "use_code_top": [(k, v) for k, v in use_codes.most_common(10)],
        "zoning_standardized_top": [(k, v) for k, v in zoning_std.most_common(10)],
        "zoning_raw_top": [(k, v) for k, v in zoning_raw.most_common(10)],
        "zoning_diversity_shannon": _r(zoning_div),
        "year_built_median": yb_med,
        "year_built_histogram": yb_hist,
        "ownership_gini": _r(owner_gini),
        "top5_owner_share": _r(top5_share),
        "top_owners": [(k, round(v, 2)) for k, v in owner_sorted[:10]],
        "with_building_share": _r((has_bldg / n) if n else None),
        "vacant_share": _r(vacant_share),
        "tax_exempt_share": _r((exempt / n) if n else None),
        "mean_bldg_sqft_per_acre": _r(mean_density, 1),
        "mean_land_value_per_parcel": (round(land_val_total / land_val_n)
                                       if land_val_n else None),
        "mean_bldg_value_per_parcel": (round(bldg_val_total / bldg_val_n)
                                       if bldg_val_n else None),
        # Compact 5-dim vector for the parallel parcel-signal similarity
        "features": {
            "ownership_gini": owner_gini,
            "zoning_diversity": zoning_div,
            "year_built_median": yb_med,
            "vacant_share": vacant_share,
            "mean_bldg_density_per_acre": mean_density,
        },
    }


# ── Per-SAD orchestration ──────────────────────────────────────────────────────
def load_boundary(sad_dir):
    for rel in ("source/sad_boundary.geojson", "source/image_extent.geojson"):
        p = sad_dir / rel
        if not p.exists():
            continue
        gj = json.loads(p.read_text(encoding="utf-8"))
        if gj.get("type") == "FeatureCollection" and gj.get("features"):
            return gj["features"][0]["geometry"]
        if gj.get("type") == "Feature":
            return gj["geometry"]
        return gj
    return None


def update_ledger(data_dir, sad_id, pulled, remaining_after):
    led = data_dir / "_shared" / "regrid_ledger.json"
    led.parent.mkdir(parents=True, exist_ok=True)
    if led.exists():
        try:
            cur = json.loads(led.read_text(encoding="utf-8"))
        except Exception:
            cur = {"spends": []}
    else:
        cur = {"spends": []}
    cur.setdefault("spends", []).append({
        "sad_id": sad_id,
        "parcels_pulled": pulled,
        "remaining_after": remaining_after,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    led.write_text(json.dumps(cur, indent=2), encoding="utf-8")


def process_one(token, sad_dir, remaining_budget, force=False, probe_only=False):
    """Probe-then-pull for one SAD. `remaining_budget` is tracked locally to
    avoid hitting /usage on every district. Returns (result_code, records_used)."""
    sad_id = sad_dir.name
    out_dir = sad_dir / "derived" / "parcels"
    parcels_path = out_dir / "parcels.geojson"
    summary_path = out_dir / "parcels_summary.json"

    if parcels_path.exists() and summary_path.exists() and not force:
        print(f"  . {sad_id}: already pulled (use --force to redo)")
        return "SKIP", 0

    boundary = load_boundary(sad_dir)
    if not boundary:
        print(f"  ! {sad_id}: no source/sad_boundary.geojson")
        return "NO_BOUNDARY", 0

    # Probe first — free — to discover trial accessibility and exact cost
    count = probe_count(token, boundary)
    if count is None:
        # Probe didn't return a usable count; fall back to a small pull
        print(f"  ? {sad_id}: probe returned no count; falling back to a capped pull")
    elif count == 0:
        print(f"  - {sad_id}: 0 parcels (outside trial counties or boundary truly empty)")
        return "OUTSIDE_TRIAL", 0
    else:
        print(f"  >> {sad_id}: probe = {count} parcels")

    if probe_only:
        return ("PROBED" if count else "OUTSIDE_TRIAL", 0)

    if remaining_budget is not None and remaining_budget < MIN_BUDGET_RESERVE:
        print(f"     skip — remaining budget {remaining_budget} below reserve {MIN_BUDGET_RESERVE}")
        return "OUT_OF_BUDGET", 0
    if count is not None and remaining_budget is not None and count > remaining_budget - MIN_BUDGET_RESERVE:
        print(f"     skip — would need {count} of remaining {remaining_budget}")
        return "TOO_BIG_FOR_BUDGET", 0

    feats = fetch_parcels_for_polygon(token, boundary, budget_remaining=remaining_budget)
    if not feats:
        print(f"  ! {sad_id}: 0 parcels actually returned despite probe={count}")
        return "EMPTY", 0

    out_dir.mkdir(parents=True, exist_ok=True)
    fc = {
        "type": "FeatureCollection", "features": feats,
        "sad_id": sad_id,
        "pulled_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "regrid_v2",
    }
    parcels_path.write_text(json.dumps(fc), encoding="utf-8")

    summary = compute_summary(feats)
    summary["sad_id"] = sad_id
    summary["source"] = "regrid_v2"
    summary["pulled_at"] = fc["pulled_at"]
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"  + {sad_id}: {len(feats)} parcels, {summary.get('total_acres')} ac, "
          f"zoning entropy {summary.get('zoning_diversity_shannon')}, "
          f"ownership Gini {summary.get('ownership_gini')}")

    spent = len(feats)
    new_remaining = (remaining_budget - spent) if remaining_budget is not None else None
    update_ledger(sad_dir.parent, sad_id, spent, new_remaining)
    return "OK", spent


def similarity_order(data_dir, pivot):
    """Pivot first, then other SADs ordered ascending by distance to pivot."""
    mpath = data_dir / "_compare_ui" / "compare_manifest.json"
    if not mpath.exists():
        raise SystemExit(f"compare_manifest.json missing at {mpath}; rebuild it first")
    m = json.loads(mpath.read_text(encoding="utf-8"))
    dm = (m.get("embedding") or {}).get("distance_matrix") or {}
    if pivot not in dm:
        raise SystemExit(f"pivot {pivot} not present in distance_matrix")
    others = [(sid, d) for sid, d in dm[pivot].items() if sid != pivot]
    others.sort(key=lambda kv: kv[1])
    return [pivot] + [sid for sid, _ in others]


def main():
    ap = argparse.ArgumentParser(description="Regrid parcels per SAD.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--sad-dir", type=Path, help="single SAD folder")
    g.add_argument("--data-dir", type=Path,
                   help="all SADs in similarity order (requires --start-from)")
    ap.add_argument("--start-from", type=str,
                    help="pivot SAD id (e.g. 32_District-Detroit_Detroit-MI)")
    ap.add_argument("--force", action="store_true", help="re-pull even if cached")
    ap.add_argument("--max-sads", type=int, default=None,
                    help="stop after N successful pulls")
    ap.add_argument("--probe-only", action="store_true",
                    help="probe each district for parcel count, don't pull anything")
    args = ap.parse_args()

    token = regrid_token()
    u = account_usage(token)
    used = u.get("used")
    remaining = u.get("remaining")
    print(f"Regrid trial: used={used} / cap={TRIAL_PARCEL_CAP}  remaining={remaining}\n")

    if args.sad_dir:
        process_one(token, args.sad_dir.resolve(), remaining,
                    force=args.force, probe_only=args.probe_only)
        return 0

    if not args.start_from:
        raise SystemExit("--data-dir requires --start-from <pivot_sad_id>")
    data_dir = args.data_dir.resolve()
    order = similarity_order(data_dir, args.start_from)
    mode = "PROBE-ONLY" if args.probe_only else "PROBE + PULL"
    print(f"{mode}: {len(order)} SAD(s) in similarity order from {args.start_from}\n")

    pulled = 0
    accessible = []
    for sid in order:
        sad_dir = data_dir / sid
        if not sad_dir.is_dir():
            print(f"  ! {sid}: directory missing")
            continue
        result, spent = process_one(token, sad_dir, remaining,
                                    force=args.force, probe_only=args.probe_only)
        if result == "OUT_OF_BUDGET":
            print("\nBudget reserve hit — stopping.")
            break
        if result == "OK":
            pulled += 1
            if remaining is not None:
                remaining -= spent
        if result in ("PROBED", "OK"):
            accessible.append(sid)
        if args.max_sads and pulled >= args.max_sads:
            print(f"\nReached --max-sads={args.max_sads}; stopping.")
            break

    if args.probe_only:
        print(f"\nAccessible (in trial counties): {len(accessible)}")
        for sid in accessible:
            print(f"   {sid}")
    else:
        print(f"\nDone. {pulled} SAD(s) pulled this run.")
        print(f"Remaining budget: {remaining}")
        print("Next:  python build_compare_manifest.py --data-dir \"...\"")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
