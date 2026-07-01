"""
sad_match_server.py  —  "Draw a district" backend (census-first v1)

A small local API for the Synthesis Field comparison tool. Given a polygon the
user draws on the map, it:

  1. Pulls ACS 5-year block groups intersecting the polygon (reusing Module 4),
  2. Computes an area/population-weighted demographic profile for the drawn area
     (the same schema Module 4 writes per SAD),
  3. Ranks the existing SAD corpus by demographic similarity, and
  4. Returns the profile, the analyzed block groups (GeoJSON, for map shading),
     within-corpus percentiles (for the rose), and the nearest districts.

This is the honest v1: matching is on the DEMOGRAPHIC dimensions only. The full
27-D morphometric match (street/building/program form) needs live OSM/Overture
pulls per draw — the larger buildout — and is layered on later. Nothing here
overclaims: the response labels itself "demographic_similarity".

WHY A SERVER: the browser can't run pygris/ACS. This runs locally alongside the
static file server.

RUN  (from the code/ folder, with the pipeline's Python env):
    pip install flask
    python sad_match_server.py --data-dir ..\\data
    # serves http://localhost:8000

Then start the static server from the data root and open the comparison tool;
the "Draw a district" button talks to this endpoint.
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import shape

# Reuse Module 4 — this file lives next to it in code/
CODE = Path(__file__).resolve().parent
sys.path.insert(0, str(CODE))
import module_04_census_pull as m4  # noqa: E402

try:
    from flask import Flask, request, jsonify
except ImportError:
    sys.exit("This server needs Flask. Install it with:  pip install flask")

# A free, low-stakes Census key so the tool runs out of the box; override with
# --api-key or the CENSUS_API_KEY environment variable.
DEFAULT_CENSUS_KEY = "898991e530013650bd4d2a5aa8be191b92d14292"

# Demographic features used for similarity (present in both the corpus census
# summaries and the drawn-polygon profile). estimated_population is log-scaled.
DEMO_FEATURES = [
    "median_household_income_pop_weighted",
    "median_age_pop_weighted",
    "pct_renter_occupied",
    "pct_bachelors_or_higher",
    "estimated_population",
    "unemployment_rate",
    "pct_white", "pct_black", "pct_asian", "pct_hispanic",
]
LOG_FEATURES = {"estimated_population"}

# Rose axes shown for the drawn district (percentile within corpus).
ROSE_AXES = [
    ("Income", "median_household_income_pop_weighted"),
    ("Age", "median_age_pop_weighted"),
    ("Renter", "pct_renter_occupied"),
    ("Educ", "pct_bachelors_or_higher"),
    ("Pop", "estimated_population"),
    ("Unemp", "unemployment_rate"),
]

EQUAL_AREA = "EPSG:5070"  # CONUS Albers, for honest area ratios


class Corpus:
    """Loads the existing SADs' demographic profiles from the compare manifest."""
    def __init__(self, data_dir: Path):
        man = data_dir / "_compare_ui" / "compare_manifest.json"
        if not man.exists():
            raise FileNotFoundError(f"compare_manifest.json not found at {man} — run build_compare_manifest.py first")
        self.m = json.loads(man.read_text(encoding="utf-8"))
        self.records = []
        for r in self.m.get("sads", []):
            sad = (r.get("census") or {}).get("sad")
            if sad:
                self.records.append({
                    "sad_id": r.get("sad_id"), "sad_name": r.get("sad_name"),
                    "region": r.get("region"), "typology": r.get("typology"),
                    "place_card": (r.get("artifacts") or {}).get("place_card_png"),
                    "demo": sad,
                })
        if not self.records:
            raise ValueError("No SADs with census summaries in the manifest.")
        # corpus matrix + per-feature mean/std for z-scoring
        self.feat_present = [f for f in DEMO_FEATURES
                             if sum(1 for x in self.records if _num(x["demo"].get(f)) is not None) >= max(3, len(self.records) // 2)]
        cols = {f: np.array([_scaled(f, x["demo"].get(f)) for x in self.records], dtype=float) for f in self.feat_present}
        self.mean = {f: np.nanmean(cols[f]) for f in self.feat_present}
        self.std = {f: (np.nanstd(cols[f]) or 1.0) for f in self.feat_present}
        self.cols = cols  # for percentiles

    def zvec(self, demo: dict) -> dict:
        return {f: (_scaled(f, demo.get(f)) - self.mean[f]) / self.std[f]
                for f in self.feat_present if _num(demo.get(f)) is not None}

    def rank(self, demo: dict, k: int = 8):
        dz = self.zvec(demo)
        out = []
        for x in self.records:
            cz = self.zvec(x["demo"])
            shared = [f for f in dz if f in cz]
            if not shared:
                continue
            d = float(np.sqrt(sum((dz[f] - cz[f]) ** 2 for f in shared)) / np.sqrt(len(shared)))
            out.append({**{kk: x[kk] for kk in ("sad_id", "sad_name", "region", "typology", "place_card")},
                        "distance": round(d, 3), "dims_used": len(shared)})
        out.sort(key=lambda r: r["distance"])
        return out[:k]

    def percentiles(self, demo: dict) -> dict:
        pct = {}
        for label, f in ROSE_AXES:
            if f not in self.cols:
                continue
            v = _scaled(f, demo.get(f))
            if not np.isfinite(v):
                continue
            arr = self.cols[f][np.isfinite(self.cols[f])]
            if not len(arr):
                continue
            pct[label] = round(float((arr <= v).sum()) / len(arr), 3)
        return pct


def _num(v):
    try:
        f = float(v)
        return f if np.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _scaled(feature, v):
    n = _num(v)
    if n is None:
        return np.nan
    return float(np.log1p(n)) if feature in LOG_FEATURES else float(n)


def derive_bg_props(bgs: gpd.GeoDataFrame) -> list[dict]:
    """Per-block-group properties (with derived percentages) for map shading."""
    def ratio(a, b):
        a = pd.to_numeric(bgs.get(a), errors="coerce")
        b = pd.to_numeric(bgs.get(b), errors="coerce")
        return np.where((b > 0), 100.0 * a / b, np.nan)
    props = {}
    if {"renter_occupied", "occupied_total"} <= set(bgs.columns):
        props["pct_renter"] = ratio("renter_occupied", "occupied_total")
    if "edu_total_pop_25plus" in bgs.columns:
        edu = sum(pd.to_numeric(bgs[c], errors="coerce") for c in
                  ["edu_bachelors", "edu_masters", "edu_professional", "edu_doctorate"] if c in bgs.columns)
        props["pct_bachelors"] = np.where(pd.to_numeric(bgs["edu_total_pop_25plus"], errors="coerce") > 0,
                                          100.0 * edu / pd.to_numeric(bgs["edu_total_pop_25plus"], errors="coerce"), np.nan)
    return props


def analyze_polygon(geom_json: dict, name: str, year: int, api_key: str, corpus: Corpus) -> dict:
    poly = shape(geom_json)
    if poly.is_empty or poly.area == 0:
        return {"ok": False, "error": "Empty polygon."}
    minlon, minlat, maxlon, maxlat = poly.bounds

    # 1) block groups intersecting the drawn polygon (reuse M4's county→BG pull)
    bgs = m4.fetch_block_groups_for_bbox((minlon, minlat, maxlon, maxlat), year=year)
    poly_gdf = gpd.GeoSeries([poly], crs="EPSG:4326")
    bgs = bgs[bgs.intersects(poly)].copy()
    if bgs.empty:
        return {"ok": False, "error": "No census block groups intersect that area. Try drawing over a populated area in the U.S."}

    # 2) ACS for those block groups, merged on GEOID
    acs = m4.fetch_acs_for_block_groups(bgs, year=year, api_key=api_key)
    bgs = bgs.merge(acs, on="GEOID", how="left")

    # 3) intersection weights vs the DRAWN polygon (equal-area), for compute_summary
    bgs_ea = bgs.to_crs(EQUAL_AREA)
    poly_ea = poly_gdf.to_crs(EQUAL_AREA).iloc[0]
    inter = bgs_ea.geometry.intersection(poly_ea).area
    bg_area = bgs_ea.geometry.area.replace(0, np.nan)
    bgs["intersection_area_ratio"] = (inter.values / bg_area.values)
    bgs["fully_inside_bbox"] = bgs.geometry.within(poly).values

    # 4) weighted demographic profile (M4's own summary → identical schema)
    profile = m4.compute_summary(bgs, "drawn", name or "Drawn district", year)

    # 5) per-BG GeoJSON (4326) for shading the analyzed area
    derived = derive_bg_props(bgs)
    feats = []
    bgs4326 = bgs.to_crs("EPSG:4326")
    for i, (_, row) in enumerate(bgs4326.iterrows()):
        g = row.geometry
        if g is None or g.is_empty:
            continue
        p = {"GEOID": str(row.get("GEOID", "")),
             "median_household_income": _num(row.get("median_household_income")),
             "median_age": _num(row.get("median_age")),
             "total_pop": _num(row.get("total_pop")),
             "ratio": round(float(row.get("intersection_area_ratio") or 0), 3)}
        for kk, arr in derived.items():
            val = arr[i]
            p[kk] = round(float(val), 1) if np.isfinite(val) else None
        feats.append({"type": "Feature", "properties": p,
                      "geometry": json.loads(gpd.GeoSeries([g]).to_json())["features"][0]["geometry"]})

    # 6) rank existing SADs + percentiles for the rose
    matches = corpus.rank(profile)
    pct = corpus.percentiles(profile)

    return {
        "ok": True,
        "name": name or "Drawn district",
        "method": "demographic_similarity",
        "profile": profile,
        "percentiles": pct,
        "blockgroups_geojson": {"type": "FeatureCollection", "features": feats},
        "matches": matches,
        "note": "Similarity is demographic only (ACS). Morphometric form matching is the next layer.",
    }


# --- ported cache globals for _area() ---
_AREA = None
_AREA_ERR = None

def _area():
    global _AREA, _AREA_ERR
    if _AREA is None and _AREA_ERR is None:
        try:
            import module_area_extract as a
            _AREA = a
        except Exception as e:
            _AREA_ERR = f"{type(e).__name__}: {e}"
    return _AREA, _AREA_ERR


SAVE_TIMEOUT = 240  # seconds; a stalled layer pull is killed after this


def _rebuild_manifests(data_dir: Path):
    """After a save, refresh the viewer + compare manifests so the new district
    shows up in the list on the next page load."""
    for script in ("build_ui_manifest.py", "build_compare_manifest.py"):
        sp = CODE / script
        if not sp.exists():
            continue
        try:
            subprocess.run(
                [sys.executable, str(sp), "--data-dir", str(data_dir)],
                cwd=str(CODE), timeout=900,
                stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
            )
        except Exception as e:
            print(f"  WARN: {script} rebuild failed ({e}); district saved, list may need a manual rebuild.")
    _refresh_geo_manifest(data_dir)


def _refresh_geo_manifest(data_dir: Path):
    """Rebuild compare_manifest_geo.json (centroid/bbox per district from its
    boundary) - the file the map + compare tab actually read."""
    try:
        ui = data_dir / "_compare_ui"
        base_p = ui / "compare_manifest.json"
        if not base_p.exists():
            return
        man = json.loads(base_p.read_text(encoding="utf-8"))

        def walk(c, xs, ys):
            if isinstance(c, (list, tuple)):
                if len(c) >= 2 and isinstance(c[0], (int, float)) and isinstance(c[1], (int, float)):
                    xs.append(c[0]); ys.append(c[1])
                else:
                    for e in c:
                        walk(e, xs, ys)

        for r in man.get("sads", []):
            bpath = (r.get("artifacts") or {}).get("sad_boundary")
            if not bpath:
                continue
            gp = data_dir / bpath
            if not gp.is_file():
                continue
            try:
                gj = json.loads(gp.read_text(encoding="utf-8"))
                xs, ys = [], []
                feats = gj.get("features") if isinstance(gj, dict) else None
                geoms = ([fe.get("geometry") for fe in feats] if feats
                         else [gj.get("geometry") if isinstance(gj, dict) and gj.get("geometry") else gj])
                for g in geoms:
                    if isinstance(g, dict) and g.get("coordinates") is not None:
                        walk(g["coordinates"], xs, ys)
                if xs and ys:
                    r["bbox"] = [min(xs), min(ys), max(xs), max(ys)]
                    r["centroid"] = [(min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0]
                    r["lng"] = r["centroid"][0]
                    r["lat"] = r["centroid"][1]
            except Exception:
                pass
        (ui / "compare_manifest_geo.json").write_text(json.dumps(man), encoding="utf-8")
        print(f"  [rebuild] compare_manifest_geo.json refreshed ({len(man.get('sads', []))} districts)", flush=True)
    except Exception as e:
        print(f"  WARN: geo manifest rebuild failed ({e})")


# Typology blend suggester (M30). Lazy-imported so the server still starts if
# numpy/pandas or an embedding is missing; /suggest_typology reports the reason.
_SUGG = {"mod": None, "err": None}
_SUGG_CACHE = {}   # (embedding_name, k) -> (rows, found_features)


def _suggester():
    if _SUGG["mod"] is None and _SUGG["err"] is None:
        try:
            import typology_suggest as ts
            _SUGG["mod"] = ts
        except Exception as e:
            _SUGG["err"] = f"{type(e).__name__}: {e}"
    return _SUGG["mod"], _SUGG["err"]


def _resolve_sad(q, ids):
    """Match a Compare-tab id to an embedding row: exact, then name token, then
    unique substring."""
    if q in ids:
        return q
    ql = str(q or "").lower()
    for s in ids:
        toks = s.split("_")
        if len(toks) > 1 and toks[1].lower() == ql:
            return s
    cand = [s for s in ids if ql and ql in s.lower()]
    return cand[0] if len(cand) == 1 else None


def make_app(data_dir: Path, api_key: str, year: int, code_dir=None, nature_key_path=None, release="2026-06-17.0") -> "Flask":
    app = Flask(__name__)
    # --- serve the Compare UI same-origin (fixes 'match server not reachable') ---
    from flask import send_from_directory as _send_from_directory
    import os as _uios
    _UI_DIR = _uios.path.join(str(data_dir), "_compare_ui")
    @app.route("/_compare_ui/")
    def _serve_compare_ui_index():
        return _send_from_directory(_UI_DIR, "index.html")
    @app.route("/_compare_ui/<path:_p>")
    def _serve_compare_ui(_p):
        return _send_from_directory(_UI_DIR, _p)
    # --- end Compare UI static serve ---
    # (data-tree static serve is registered LAST, after all API routes, as a
    # robust file-existence fall-through — see end of make_app.)
    # --- ported layer-extract engine (module_area_extract) ---
    try:
        import module_area_extract as a
    except Exception as _e:
        a = None
        print('  WARN: module_area_extract import failed:', _e)
    # --- global CORS preflight shim (all routes; answers OPTIONS 204) ---
    from flask import make_response as _make_response
    @app.before_request
    def _cors_preflight():
        if request.method == "OPTIONS":
            _r = _make_response("", 204)
            _r.headers["Access-Control-Allow-Origin"] = "*"
            _r.headers["Access-Control-Allow-Headers"] = "Content-Type"
            _r.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
            return _r
    # --- end CORS preflight shim ---
    corpus = Corpus(data_dir)
    print(f"  corpus loaded: {len(corpus.records)} SADs · matching on {len(corpus.feat_present)} demographic dims")

    # Program-mix match (POI-based). Adds /analyze_program for the drawn district.
    try:
        import program_match as _pm
        _pm.register(app, data_dir, release="2026-05-20.0")
    except Exception as _e:
        print(f"  [warn] program match not wired: {_e}")

    @app.after_request
    def cors(resp):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        return resp

    @app.route("/health")
    def health():
        return jsonify({"ok": True, "sads": len(corpus.records), "dims": corpus.feat_present})

    @app.route("/analyze", methods=["POST", "OPTIONS"])
    def analyze():
        if request.method == "OPTIONS":
            return ("", 204)
        try:
            body = request.get_json(force=True)
            geom = body.get("geometry")
            if not geom:
                return jsonify({"ok": False, "error": "No geometry in request."}), 400
            result = analyze_polygon(geom, body.get("name", ""), int(body.get("year", year)), api_key, corpus)
            code = 200 if result.get("ok") else 422
            return jsonify(result), code
        except SystemExit as e:           # M4 calls sys.exit on hard failures
            return jsonify({"ok": False, "error": f"Census pull failed: {e}"}), 500
        except Exception as e:
            traceback.print_exc()
            return jsonify({"ok": False, "error": str(e)}), 500

    # nature-live: attach /analyze_nature if the module is present
    try:
        import importlib.util as _ilu, os as _os
        _cd = code_dir or _os.path.dirname(_os.path.abspath(__file__))
        _np = _os.path.join(_cd, "nature_match.py")
        if _os.path.isfile(_np):
            _spec = _ilu.spec_from_file_location("nature_match", _np)
            _nm = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_nm)
            _kp = nature_key_path or _os.path.join(_os.path.expanduser("~"), "openaq_key.txt")
            _nm.nature_register(app, data_dir, _cd, _kp, release)
        else:
            print("  (nature_match.py not found next to server; /analyze_nature disabled)")
    except Exception as _e:
        print("  (nature-live wiring skipped:", _e, ")")

    @app.route("/extract", methods=["POST", "OPTIONS"])
    def extract():
        if request.method == "OPTIONS":
            return ("", 204)
        a, err = _area()
        if not a:
            return jsonify({"ok": False, "error": f"Extract engine unavailable: {err}"}), 500
        try:
            b = request.get_json(force=True)
            geom = b.get("geometry")
            layer = b.get("layer")
            ext = b.get("extent", "city")
            if not geom or not layer:
                return jsonify({"ok": False, "error": "geometry and layer required"}), 400
            if layer == "walkshed":  # isochrone from the drawn boundary, not the acquisition extent
                fc = a.walkshed_polygon(geom)
                return jsonify({"ok": True, "geojson": fc,
                                "extent": {"kind": "sad", "name": "Drawn boundary"}})
            extent_poly, extent_info = a.resolve_extent(geom, ext, year, api_key)
            fc = a.extract_layer(extent_poly, layer)
            return jsonify({"ok": True, "geojson": fc, "extent": extent_info})
        except Exception as e:
            traceback.print_exc()
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/save_area", methods=["POST", "OPTIONS"])
    def save_area():
        if request.method == "OPTIONS":
            return ("", 204)
        a, err = _area()
        if not a:
            return jsonify({"ok": False, "error": f"Extract engine unavailable: {err}"}), 500
        try:
            b = request.get_json(force=True)
            geom = b.get("geometry")
            ext = "sad"
            name = b.get("name", "Drawn district")
            if not geom:
                return jsonify({"ok": False, "error": "geometry required"}), 400
            before = {p.name for p in data_dir.glob("*rawn-district*")}
            runner = CODE / "_save_runner.py"
            payload = json.dumps({"geom": geom, "ext": ext, "name": name,
                                  "year": year, "api_key": api_key, "data_dir": str(data_dir)})
            t0 = time.time()
            print(f"  [save_area] launching pull name={name!r} extent={ext} timeout={SAVE_TIMEOUT}s", flush=True)
            try:
                cp = subprocess.run([sys.executable, str(runner)], input=payload, text=True,
                                    capture_output=True, cwd=str(CODE), timeout=SAVE_TIMEOUT,
                                    env={**os.environ, "CENSUS_API_KEY": api_key})
            except subprocess.TimeoutExpired:
                for p in data_dir.glob("*rawn-district*"):
                    if p.name not in before:
                        shutil.rmtree(p, ignore_errors=True)
                print(f"  [save_area] TIMED OUT after {SAVE_TIMEOUT}s; removed partial", flush=True)
                return jsonify({"ok": False, "timeout": True, "error": f"Timed out after {SAVE_TIMEOUT // 60} min on a stalled layer."}), 504
            sad_id = None
            for line in (cp.stdout or "").splitlines():
                if line.startswith("SAD_ID="):
                    sad_id = line[len("SAD_ID="):].strip()
            if cp.returncode != 0 or not sad_id:
                print("  [save_area] runner FAILED:\n" + (cp.stderr or "")[-1800:], flush=True)
                return jsonify({"ok": False, "error": "Save failed during layer pull (see server window)."}), 500
            print(f"  [save_area] WROTE {sad_id} (+{time.time() - t0:.1f}s)", flush=True)
            try:
                _res = analyze_polygon(geom, name, int(year), api_key, corpus)
                _prof = _res.get("profile") if _res.get("ok") else None
                if _prof:
                    _dd = data_dir / sad_id / "derived"
                    _dd.mkdir(parents=True, exist_ok=True)
                    (_dd / "census_summary.json").write_text(json.dumps(_prof, indent=2), encoding="utf-8")
                    print(f"  [save_area] census written for {sad_id}", flush=True)
            except Exception:
                traceback.print_exc()
            threading.Thread(target=_rebuild_manifests, args=(data_dir,), daemon=True).start()
            return jsonify({"ok": True, "sad_id": sad_id, "rebuilding": True})
        except Exception as e:
            traceback.print_exc()
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/set_typology", methods=["POST", "OPTIONS"])
    def set_typology():
        if request.method == "OPTIONS":
            return ("", 204)
        try:
            b = request.get_json(force=True)
            sad_id = b.get("sad_id")
            typ = b.get("primary_typology")
            if not sad_id:
                return jsonify({"ok": False, "error": "sad_id required"}), 400
            d = data_dir / sad_id / "derived"
            d.mkdir(parents=True, exist_ok=True)
            p = d / "typology.json"
            cur = {}
            if p.exists():
                try:
                    cur = json.loads(p.read_text())
                except Exception:
                    cur = {}
            cur["primary_typology"] = typ
            p.write_text(json.dumps(cur, indent=2))
            return jsonify({"ok": True})
        except Exception as e:
            traceback.print_exc()
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/suggest_typology", methods=["GET", "POST", "OPTIONS"])
    def suggest_typology():
        """Typology blend for a district in the newest enriched embedding.
        Body/query: sad_id (folder id or short name), optional k (default 7).
        Omit sad_id to get the whole corpus."""
        if request.method == "OPTIONS":
            return ("", 204)
        ts, err = _suggester()
        if not ts:
            return jsonify({"ok": False, "error": f"Suggester unavailable: {err}"}), 500
        try:
            b = request.get_json(silent=True) or {}
            qargs = request.args
            sad_id = b.get("sad_id") or qargs.get("sad_id")
            try:
                k = int(b.get("k") or qargs.get("k") or ts.DEFAULT_K)
            except (TypeError, ValueError):
                k = ts.DEFAULT_K
            emb = ts.newest_enriched(data_dir)
            if not emb:
                return jsonify({"ok": False, "error": "no enriched embedding found; run consolidate first"}), 503
            key = (emb.name, k)
            if key not in _SUGG_CACHE:
                rows, found, _ = ts.blend_suggestions(data_dir, k, emb_dir=emb)
                _SUGG_CACHE[key] = (rows, found)
            rows, found = _SUGG_CACHE[key]
            if sad_id:
                hit = _resolve_sad(sad_id, [r["sad_id"] for r in rows])
                if not hit:
                    return jsonify({"ok": False, "error": f"district not in embedding: {sad_id}"}), 404
                row = next(r for r in rows if r["sad_id"] == hit)
                return jsonify({"ok": True, "embedding": emb.name, "k": k,
                                "features_used": len(found), "suggestion": row})
            return jsonify({"ok": True, "embedding": emb.name, "k": k,
                            "features_used": len(found), "count": len(rows),
                            "suggestions": rows})
        except Exception as e:
            traceback.print_exc()
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/places", methods=["POST", "OPTIONS"])
    def places():
        if request.method == "OPTIONS":
            return ("", 204)
        a, err = _area()
        if not a:
            return jsonify({"ok": False, "error": f"Extract engine unavailable: {err}"}), 500
        try:
            b = request.get_json(force=True)
            geom = b.get("geometry")
            ext = b.get("extent", "sad")
            if not geom:
                return jsonify({"ok": False, "error": "geometry required"}), 400
            extent_poly, extent_info = a.resolve_extent(geom, ext, year, api_key)
            fc = a.extract_layer(extent_poly, "pois")
            return jsonify({"ok": True, "geojson": fc, "extent": extent_info})
        except Exception as e:
            traceback.print_exc()
            return jsonify({"ok": False, "error": str(e)}), 500

    # --- static front-end + data tree (registered LAST so it never shadows an
    # API route; Werkzeug prefers the specific static routes above, and this GET
    # catch-all only serves paths that resolve to a real file under data/). ---
    from flask import redirect as _redirect
    _DATA_ROOT = str(data_dir)

    @app.route("/")
    def _root():
        return _redirect("/_compare_ui/map.html")

    @app.route("/<path:_dp>", methods=["GET"])
    def _serve_data_root(_dp):
        _target = _uios.path.join(_DATA_ROOT, _dp)
        if _uios.path.isdir(_target):
            _dp = _dp.rstrip("/") + "/index.html"
            _target = _uios.path.join(_DATA_ROOT, _dp)
        if not _uios.path.isfile(_target):
            from flask import abort as _abort
            _abort(404)
        return _send_from_directory(_DATA_ROOT, _dp)
    # --- end static serve ---

    return app


def main():
    ap = argparse.ArgumentParser(description="Draw-a-district match server (census-first v1)")
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--api-key", default=None, help="Census API key (else CENSUS_API_KEY env, else built-in default)")
    ap.add_argument("--year", type=int, default=m4.DEFAULT_ACS_YEAR)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--code-dir", default=None, help="dir holding nature_match.py + pullers (default: next to this server)")
    ap.add_argument("--nature-key", default=None, help="path to OpenAQ key file (default: ~/openaq_key.txt)")
    args = ap.parse_args()

    import os
    api_key = args.api_key or os.environ.get("CENSUS_API_KEY") or DEFAULT_CENSUS_KEY
    os.environ["CENSUS_API_KEY"] = api_key  # so M4's internal lookups are satisfied

    import os as _os
    _code_dir = args.code_dir or _os.path.dirname(_os.path.abspath(__file__))
    app = make_app(args.data_dir.resolve(), api_key, args.year, code_dir=_code_dir, nature_key_path=args.nature_key)
    print(f"\n  Draw-a-district server on http://localhost:{args.port}  (POST /analyze)")
    print("  Leave this running; use the comparison tool's “Draw a district” button.\n")
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
