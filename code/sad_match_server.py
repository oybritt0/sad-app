"""
sad_match_server.py  â€”  "Draw a district" backend (census-first v1)

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
pulls per draw â€” the larger buildout â€” and is layered on later. Nothing here
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
import datetime
import json
import subprocess
import sys
import threading
import traceback
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import shape

# Reuse Module 4 â€” this file lives next to it in code/
sys.path.insert(0, str(Path(__file__).resolve().parent))
import module_04_census_pull as m4  # noqa: E402
import module_04c_municipal_context as m4c  # noqa: E402
import module_overture_places as ovp  # noqa: E402
import module_area_extract as mae  # noqa: E402
import hashlib  # noqa: E402

try:
    from flask import Flask, request, jsonify, send_from_directory, redirect, abort
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
            # Empty persistent disk (first boot before data upload). Start with
            # an empty corpus so the service stays up to serve the viewer and
            # accept the data upload; it loads for real on the next restart.
            print(f"  [corpus] no compare_manifest at {man}; starting EMPTY (upload data, then restart)")
            self.m = {"sads": []}
        else:
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
            # Empty corpus (no data yet). Keep the service alive with empty
            # matching state instead of crashing; matching simply returns
            # nothing until data is uploaded and the service restarts.
            print("  [corpus] no SADs with census summaries; matching disabled until data is present")
            self.feat_present = []
            self.cols = {}
            self.mean = {}
            self.std = {}
            return
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


def bg_geojson_for_polygon(poly, year: int, api_key: str) -> dict:
    """Per-block-group GeoJSON (with shading metrics) for any polygon â€” used by
    the census /extract layer at the acquisition extent (city or SAD)."""
    minlon, minlat, maxlon, maxlat = poly.bounds
    bgs = m4.fetch_block_groups_for_bbox((minlon, minlat, maxlon, maxlat), year=year)
    bgs = bgs[bgs.intersects(poly)].copy()
    if bgs.empty:
        return {"type": "FeatureCollection", "features": []}
    acs = m4.fetch_acs_for_block_groups(bgs, year=year, api_key=api_key)
    bgs = bgs.merge(acs, on="GEOID", how="left")
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
             "total_pop": _num(row.get("total_pop"))}
        for kk, arr in derived.items():
            val = arr[i]
            p[kk] = round(float(val), 1) if np.isfinite(val) else None
        feats.append({"type": "Feature", "properties": p,
                      "geometry": json.loads(gpd.GeoSeries([g]).to_json())["features"][0]["geometry"]})
    return {"type": "FeatureCollection", "features": feats}


def analyze_polygon(geom_json: dict, name: str, year: int, api_key: str, corpus: Corpus) -> dict:
    poly = shape(geom_json)
    if poly.is_empty or poly.area == 0:
        return {"ok": False, "error": "Empty polygon."}
    minlon, minlat, maxlon, maxlat = poly.bounds

    # 1) block groups intersecting the drawn polygon (reuse M4's countyâ†’BG pull)
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

    # 4) weighted demographic profile (M4's own summary â†’ identical schema)
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

    # 5b) municipal-scope census for the drawn area's host city (best-effort)
    municipal = None
    try:
        c = poly.centroid
        municipal = m4c.municipal_summary_for_point(c.x, c.y, year, api_key)
    except Exception:
        municipal = None

    # 6) rank existing SADs + percentiles for the rose
    matches = corpus.rank(profile)
    pct = corpus.percentiles(profile)

    return {
        "ok": True,
        "name": name or "Drawn district",
        "method": "demographic_similarity",
        "profile": profile,
        "profile_municipal": (municipal or {}).get("summary"),
        "municipality": (municipal or {}).get("municipality"),
        "percentiles": pct,
        "blockgroups_geojson": {"type": "FeatureCollection", "features": feats},
        "matches": matches,
        "note": "Similarity is demographic only (ACS). Morphometric form matching is the next layer.",
    }


_EXTENT_CACHE = {}
_LAYER_CACHE = {}
def _area_key(geom, extent):
    return hashlib.md5((json.dumps(geom, sort_keys=True) + '|' + extent).encode()).hexdigest()


def _robust_rmtree(path: Path, attempts: int = 5):
    """Delete a folder tree, tolerant of Windows file locks and read-only files.
    Returns (removed, last_error). The SAD boundary is removed FIRST so the
    viewer drops the district on the next manifest rebuild even if some stray
    derived file stays locked (e.g. open in QGIS)."""
    import shutil, stat, time, os
    try:
        b = path / 'source' / 'sad_boundary.geojson'
        if b.exists():
            os.chmod(b, stat.S_IWRITE)
            b.unlink()
    except Exception:
        pass

    def onerror(func, p, exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass

    last = None
    for _ in range(attempts):
        if not path.exists():
            return True, None
        try:
            shutil.rmtree(path, onerror=onerror)
        except Exception as e:
            last = e
        if not path.exists():
            return True, None
        time.sleep(0.5)
    return (not path.exists()), last


def _rebuild_viewer_manifest(data_dir: Path) -> bool:
    """Regenerate data/_ui/manifest.json so the viewer reflects added/removed districts."""
    try:
        import subprocess
        bum = Path(__file__).resolve().parent / 'build_ui_manifest.py'
        r = subprocess.run([sys.executable, str(bum), '--data-dir', str(data_dir)],
                           cwd=str(Path(__file__).resolve().parent),
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', env=_utf8_env(), timeout=300)
        if r.returncode != 0:
            print("  [manifest rebuild] failed:\n", r.stderr[-800:])
        return r.returncode == 0
    except Exception as e:
        print("  [manifest rebuild] error:", e)
        return False


_CODE_DIR = Path(__file__).resolve().parent


def _utf8_env():
    """Child env forcing UTF-8 so module output with non-ASCII glyphs doesn't
    crash under Windows cp1252."""
    import os
    e = os.environ.copy()
    e.setdefault('PYTHONUTF8', '1')
    e.setdefault('PYTHONIOENCODING', 'utf-8')
    return e


def _integration_status_path(data_dir: Path, sad_id: str) -> Path:
    return data_dir / sad_id / 'derived' / '_integration_status.json'


def _write_status(data_dir: Path, sad_id: str, **kw):
    try:
        p = _integration_status_path(data_dir, sad_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        cur = {}
        if p.exists():
            try:
                cur = json.loads(p.read_text())
            except Exception:
                cur = {}
        kw['updated'] = datetime.datetime.utcnow().isoformat() + 'Z'
        cur.update(kw)
        p.write_text(json.dumps(cur, indent=2))
    except Exception as e:
        print("  [integrate] status write failed:", e)


def _integrate_district(data_dir: Path, sad_id: str):
    """Run the full integration chain for one freshly-saved district, in a
    background thread: per-SAD analysis (M1..M22) -> cross-SAD embedding
    (M7/M8/M9/M10) -> compare manifest -> viewer manifest. Each step's success
    is recorded so the UI can poll /integration_status."""
    py, dd = sys.executable, str(data_dir)
    steps = [
        ('per-SAD analysis',
         [py, 'batch_run_pipeline.py', '--data-dir', dd, '--stage', 'per-sad', '--sads', sad_id]),
        ('cross-SAD embedding',
         [py, 'batch_run_pipeline.py', '--data-dir', dd, '--stage', 'cross-sad']),
        ('compare manifest',
         [py, 'build_compare_manifest.py', '--data-dir', dd]),
        ('viewer manifest',
         [py, 'build_ui_manifest.py', '--data-dir', dd]),
    ]
    _write_status(data_dir, sad_id, state='running', step='starting',
                  started=datetime.datetime.utcnow().isoformat() + 'Z', error=None)
    for name, cmd in steps:
        _write_status(data_dir, sad_id, state='running', step=name)
        print(f"  [integrate {sad_id}] {name}...")
        try:
            r = subprocess.run(cmd, cwd=str(_CODE_DIR), capture_output=True,
                               text=True, encoding='utf-8', errors='replace',
                               env=_utf8_env())
        except Exception as e:
            _write_status(data_dir, sad_id, state='failed', step=name, error=str(e))
            print(f"  [integrate {sad_id}] {name} errored: {e}")
            return
        if r.returncode != 0:
            tail = (r.stdout or '')[-500:] + (r.stderr or '')[-500:]
            print(f"  [integrate {sad_id}] {name} FAILED:\n{tail}")
            _write_status(data_dir, sad_id, state='failed', step=name,
                          error=f'step "{name}" failed (see server console)')
            return
    _write_status(data_dir, sad_id, state='done', step='complete',
                  finished=datetime.datetime.utcnow().isoformat() + 'Z')
    print(f"  [integrate {sad_id}] complete \u2014 district fully integrated")


def _integrate_async(data_dir: Path, sad_id: str):
    threading.Thread(target=_integrate_district, args=(data_dir, sad_id),
                     daemon=True).start()


def _resume_interrupted(data_dir: Path):
    """On startup (Render), resume any district whose integration was cut off by
    a restart -- status still 'queued' or 'running'. Re-runs the chain in a
    thread so a redeploy mid-draw doesn't strand a district. Gated on
    SAD_RESUME_ON_BOOT so it never fires during local use."""
    import os as _os
    if _os.environ.get('SAD_RESUME_ON_BOOT', '') != '1':
        return
    try:
        for sd in sorted(data_dir.iterdir()):
            if not sd.is_dir():
                continue
            p = _integration_status_path(data_dir, sd.name)
            if not p.exists():
                continue
            try:
                st = json.loads(p.read_text())
            except Exception:
                continue
            if st.get('state') in ('queued', 'running'):
                print(f"  [resume] {sd.name} was '{st.get('state')}' -> re-integrating")
                _write_status(data_dir, sd.name, state='queued', step='queued (resumed)')
                _integrate_async(data_dir, sd.name)
    except Exception as e:
        print(f"  [resume] scan failed: {e}")

def make_app(data_dir: Path, api_key: str, year: int) -> "Flask":
    app = Flask(__name__)
    # --- Basic auth (shared password). Disabled if BASIC_AUTH_USER/PASS env
    # vars are unset, so local dev keeps working without a prompt.
    from flask import Response as _Response, request as _req
    def _sad_check_auth(u, p):
        import os
        eu = os.environ.get('BASIC_AUTH_USER', '')
        ep = os.environ.get('BASIC_AUTH_PASS', '')
        if not eu or not ep:
            return True
        return u == eu and p == ep
    @app.before_request
    def _sad_require_basic_auth():
        # /health is exempt so Render's health probe stays green.
        if _req.path == '/health':
            return None
        a = _req.authorization
        if a and _sad_check_auth(a.username, a.password):
            return None
        return _Response('Authentication required', 401,
                         {'WWW-Authenticate': 'Basic realm="SAD Toolkit"'})
    # --- end basic auth ---
    corpus = Corpus(data_dir)
    _resume_interrupted(data_dir)
    print(f"  corpus loaded: {len(corpus.records)} SADs Â· matching on {len(corpus.feat_present)} demographic dims")

    @app.after_request
    def cors(resp):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        return resp

    @app.route("/health")
    def health():
        return jsonify({"ok": True, "sads": len(corpus.records), "dims": corpus.feat_present})

    # ---- Static serving (single-service deploy) ----------------------------
    # Serve the viewer + data files straight from data_dir, the same tree
    # ui_server.py serves locally. One service then covers viewer + API.
    _NOCACHE = {"Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache", "Expires": "0"}

    def _serve(relpath):
        # prevent path traversal; resolve under data_dir only
        base = data_dir.resolve()
        target = (base / relpath).resolve()
        if not str(target).startswith(str(base)):
            abort(403)
        # directory -> serve its index.html (mirrors ui_server.py indexing)
        if target.is_dir():
            idx = target / 'index.html'
            if idx.is_file():
                target = idx
            else:
                abort(404)
        if not target.is_file():
            abort(404)
        resp = send_from_directory(str(target.parent), target.name)
        for k, v in _NOCACHE.items():
            resp.headers[k] = v
        return resp

    @app.route("/")
    def _root():
        return redirect("/_ui/index.html")

    @app.route("/_ui/")
    def _ui_index():
        return _serve("_ui/index.html")

    # everything else: serve as a file under data_dir (viewer assets, manifests,
    # per-district geojsons, _compare_ui, _shared, place cards, etc.)
    @app.route("/<path:relpath>")
    def _static_any(relpath):
        return _serve(relpath)

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

    @app.route("/places", methods=["POST", "OPTIONS"])
    def places():
        """On-demand Overture POIs for a drawn (or any) polygon â€” ROD-tool engine."""
        if request.method == "OPTIONS":
            return ("", 204)
        try:
            body = request.get_json(force=True)
            geom = body.get("geometry")
            if not geom:
                return jsonify({"ok": False, "error": "No geometry."}), 400
            fc = ovp.places_in_polygon(geom, release=body.get("release"),
                                       categories=body.get("categories"),
                                       min_confidence=float(body.get("min_confidence", 0.0)))
            return jsonify({"ok": True, "places": fc, "summary": ovp.category_summary(fc)})
        except Exception as e:
            traceback.print_exc()
            return jsonify({"ok": False, "error": f"Overture query failed: {e}"}), 500

    @app.route("/extract", methods=["POST", "OPTIONS"])
    def extract():
        """Pull one OSM/POI layer for a drawn area's resolved extent (cached)."""
        if request.method == "OPTIONS":
            return ("", 204)
        try:
            body = request.get_json(force=True)
            geom = body.get("geometry")
            layer = body.get("layer")
            extent = "sad"  # forced: drawn districts always pull at SAD scale (city would OOM the worker)
            if not geom or not layer:
                return jsonify({"ok": False, "error": "Need geometry and layer."}), 400
            key = _area_key(geom, extent)
            ent = _EXTENT_CACHE.get(key)
            if not ent:
                poly, info = mae.resolve_extent(geom, extent, year, api_key)
                ent = {"poly": poly, "info": info}
                _EXTENT_CACHE[key] = ent
            lkey = key + ":" + layer
            fc = _LAYER_CACHE.get(lkey)
            if fc is None:
                if layer == 'walkshed':
                    fc = mae.walkshed_polygon(geom)        # isochrone from drawn centroid
                elif layer == 'census':
                    fc = bg_geojson_for_polygon(ent["poly"], year, api_key)   # BGs over the extent
                else:
                    fc = mae.extract_layer(ent["poly"], layer)
                _LAYER_CACHE[lkey] = fc
            return jsonify({"ok": True, "layer": layer, "extent": ent["info"],
                            "geojson": fc, "meta": mae.layer_meta(fc)})
        except Exception as e:
            traceback.print_exc()
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/save_area", methods=["POST", "OPTIONS"])
    def save_area():
        """Persist a drawn area (its pulled layers) as a new district folder."""
        if request.method == "OPTIONS":
            return ("", 204)
        try:
            body = request.get_json(force=True)
            geom = body.get("geometry")
            extent = "sad"  # forced: drawn districts always pull at SAD scale (city would OOM the worker)
            name = (body.get("name") or "").strip()
            if not name:
                # No fallback to "Drawn district" placeholder; we got bitten by
                # stuck-state recovery on auto-named districts. Use a timestamp
                # so each unnamed draw is unique and traceable.
                from datetime import datetime as _dt
                name = "Drawn-" + _dt.utcnow().strftime("%Y%m%d-%H%M%S")
            if not geom:
                return jsonify({"ok": False, "error": "No geometry."}), 400
            key = _area_key(geom, extent)
            ent = _EXTENT_CACHE.get(key)
            if not ent:
                poly, info = mae.resolve_extent(geom, extent, year, api_key)
                ent = {"poly": poly, "info": info}
                _EXTENT_CACHE[key] = ent
            layers = {lk[len(key) + 1:]: fc for lk, fc in _LAYER_CACHE.items() if lk.startswith(key + ":")}
            # ensure a complete set so the saved district has every viewer layer
            for ly in ('buildings', 'parks', 'parking', 'highways', 'pois', 'transit'):
                if not layers.get(ly):
                    try: layers[ly] = mae.extract_layer(ent["poly"], ly)
                    except Exception: layers[ly] = None
            if not layers.get('walkshed'):
                try: layers['walkshed'] = mae.walkshed_polygon(geom)
                except Exception: layers['walkshed'] = None
            if not layers.get('census'):
                try: layers['census'] = bg_geojson_for_polygon(ent["poly"], year, api_key)
                except Exception: layers['census'] = None
            sad_id = mae.save_district(name, geom, ent["poly"], ent["info"], data_dir, layers, year, api_key)
            rebuilt = _rebuild_viewer_manifest(data_dir)
            # Fully integrate in the background: per-SAD -> cross-SAD embedding
            # -> manifests. The district is viewable immediately (boundary);
            # its metrics, embedding, typology suggestion and M20-22 layers fill
            # in as the chain completes. Poll /integration_status?sad_id=...
            _write_status(data_dir, sad_id, state='queued', step='queued')
            _integrate_async(data_dir, sad_id)
            return jsonify({"ok": True, "sad_id": sad_id, "extent": ent["info"],
                            "viewer_rebuilt": rebuilt, "integration_started": True})
        except Exception as e:
            traceback.print_exc()
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/delete_district", methods=["POST", "OPTIONS"])
    def delete_district():
        """Delete a map-created district (guarded: only folders with an
        extent.json marker, so original corpus districts can't be removed)."""
        if request.method == "OPTIONS":
            return ("", 204)
        try:
            sad_id = (request.get_json(force=True) or {}).get("sad_id", "")
            if not sad_id or '/' in sad_id or '\\' in sad_id or '..' in sad_id:
                return jsonify({"ok": False, "error": "Invalid id."}), 400
            d = data_dir / sad_id
            if not d.is_dir():
                return jsonify({"ok": False, "error": "District not found."}), 404
            if not (d / 'source' / 'extent.json').exists():
                return jsonify({"ok": False, "error": "Only map-created districts can be deleted."}), 403
            removed, err = _robust_rmtree(d)
            rebuilt = _rebuild_viewer_manifest(data_dir)
            if not removed:
                return jsonify({
                    "ok": False, "sad_id": sad_id, "viewer_rebuilt": rebuilt,
                    "error": ("Some files are locked (usually QGIS or another program "
                              "has them open). The district was removed from the viewer "
                              "list, but its folder couldn't be fully deleted \u2014 close "
                              "other programs and delete the leftover folder by hand. ("
                              + str(err) + ")")}), 409
            return jsonify({"ok": True, "sad_id": sad_id, "viewer_rebuilt": rebuilt})
        except Exception as e:
            traceback.print_exc()
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/integration_status", methods=["GET", "POST", "OPTIONS"])
    def integration_status():
        if request.method == "OPTIONS":
            return ("", 204)
        if request.method == "GET":
            sad_id = request.args.get("sad_id", "")
        else:
            sad_id = (request.get_json(force=True) or {}).get("sad_id", "")
        if not sad_id or '/' in sad_id or '\\' in sad_id or '..' in sad_id:
            return jsonify({"ok": False, "error": "Invalid id."}), 400
        p = _integration_status_path(data_dir, sad_id)
        if not p.exists():
            return jsonify({"ok": True, "state": "unknown", "sad_id": sad_id})
        try:
            return jsonify({"ok": True, "sad_id": sad_id, **json.loads(p.read_text())})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    return app


def main():
    ap = argparse.ArgumentParser(description="Draw-a-district match server (census-first v1)")
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--api-key", default=None, help="Census API key (else CENSUS_API_KEY env, else built-in default)")
    ap.add_argument("--year", type=int, default=m4.DEFAULT_ACS_YEAR)
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    import os
    api_key = args.api_key or os.environ.get("CENSUS_API_KEY") or DEFAULT_CENSUS_KEY
    os.environ["CENSUS_API_KEY"] = api_key  # so M4's internal lookups are satisfied

    app = make_app(args.data_dir.resolve(), api_key, args.year)
    print(f"\n  Draw-a-district server on http://localhost:{args.port}  (POST /analyze)")
    print("  Leave this running; use the comparison tool's â€œDraw a districtâ€ button.\n")
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()




