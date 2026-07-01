"""
nature_match.py  -  live nature pull for a freshly drawn boundary.

Registers /analyze_nature on the match server. Given a drawn geometry it runs the SAME
three pullers the corpus used (green/blue via Overture S3, air via OpenAQ, canopy via ETH
COG over /vsicurl), reading each puller's per-SAD metrics JSON back, then returns a nature
block in the exact shape the Compare tab + map panel read.

No reimplementation: it calls each puller's main() exactly as pull_nature_corpus.py does,
so live values match the corpus by construction. Canopy is gated (valid_pixels >= 500 AND
valid_fraction >= 0.10); first draw in a 3-degree ETH tile is slow (vsicurl window read),
repeats in the same region are faster as the OS/file cache warms.

Wired into sad_match_server.make_app via one call: nature_register(app, data_dir, code_dir,
key_path, release).
"""
from __future__ import annotations
import glob
import importlib.util
import json
import os
import sys
import time
import datetime as dt

CANOPY_MIN_PIXELS = 500
CANOPY_MIN_FRACTION = 0.10
DEFAULT_RELEASE = "2026-06-17.0"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(mod, argv):
    old = sys.argv
    try:
        sys.argv = ["x"] + argv
        rc = mod.main()
        return 0 if rc in (0, None) else rc
    except SystemExit as e:
        return 0 if e.code in (0, None) else e.code
    except Exception as e:
        print("    ! nature pull error:", e)
        return 1
    finally:
        sys.argv = old


def _latest_metrics(data_dir, sad, prefix):
    d = os.path.join(data_dir, "derived", "per_sad", sad)
    hits = sorted(glob.glob(os.path.join(d, prefix + "_*.json")),
                  key=lambda p: os.path.getmtime(p))
    if not hits:
        return {}
    try:
        return json.load(open(hits[-1], "r", encoding="utf-8")).get("metrics", {})
    except Exception:
        return {}


def _num(v):
    try:
        if v is None:
            return None
        f = float(v)
        return f if f == f else None  # drop NaN
    except (TypeError, ValueError):
        return None


def _read_key(key_path):
    try:
        with open(key_path, "r", encoding="utf-8") as f:
            k = f.read().strip()
        return k or None
    except Exception:
        return None


def analyze_nature(geom, data_dir, code_dir, key_path, release):
    """Run the three pullers on a drawn geometry; return the nature block."""
    token = dt.datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + str(os.getpid())
    sad = "drawn_live_" + token
    per_sad = os.path.join(data_dir, "derived", "per_sad", sad)
    os.makedirs(per_sad, exist_ok=True)
    bnd = os.path.join(per_sad, "sad_boundary.geojson")
    fc = {"type": "FeatureCollection",
          "features": [{"type": "Feature", "properties": {}, "geometry": geom}]}
    with open(bnd, "w", encoding="utf-8") as f:
        json.dump(fc, f)

    notes = []
    # green / blue (Overture S3)
    try:
        ov_mod = _load(os.path.join(code_dir, "pull_nature_overture.py"), "ov_live")
        rc = _run(ov_mod, ["--data-dir", data_dir, "--sad", sad, "--boundary", bnd,
                           "--release", release, "--apply"])
        if rc != 0:
            notes.append("green/blue pull rc=%s" % rc)
    except Exception as e:
        notes.append("green/blue load failed: %s" % e)

    # air (OpenAQ) - set the key in env the same way the runner does
    key = _read_key(key_path)
    if key:
        os.environ["OPENAQ_API_KEY"] = key
        try:
            air_mod = _load(os.path.join(code_dir, "pull_air_openaq.py"), "air_live")
            rc = _run(air_mod, ["--data-dir", data_dir, "--sad", sad, "--boundary", bnd, "--apply"])
            if rc != 0:
                notes.append("air pull rc=%s (district may have no monitors)" % rc)
        except Exception as e:
            notes.append("air load failed: %s" % e)
    else:
        notes.append("no OpenAQ key at %s; air skipped" % key_path)

    # canopy (ETH COG via vsicurl). slow on first draw in a tile region.
    try:
        can_mod = _load(os.path.join(code_dir, "pull_canopy_height_cog.py"), "can_live")
        rc = _run(can_mod, ["--data-dir", data_dir, "--sad", sad, "--boundary", bnd, "--apply"])
        if rc != 0:
            notes.append("canopy pull rc=%s (tile may be unresolved / warming)" % rc)
    except Exception as e:
        notes.append("canopy load failed: %s" % e)

    ov = _latest_metrics(data_dir, sad, "nature_overture")
    air = _latest_metrics(data_dir, sad, "nature_air")
    can = _latest_metrics(data_dir, sad, "nature_canopy")

    valid_px = _num(can.get("valid_pixels"))
    valid_fr = _num(can.get("valid_fraction"))
    reliable = (valid_px is not None and valid_px >= CANOPY_MIN_PIXELS
                and valid_fr is not None and valid_fr >= CANOPY_MIN_FRACTION)

    block = {
        "canopy": {
            "mean_canopy_m": _num(can.get("mean_height_canopy_m")),
            "p90_m": _num(can.get("p90_m")),
            "tall_share": _num(can.get("tall_share")),
            "canopy_area_share": _num(can.get("canopy_area_share")),
            "valid_fraction": valid_fr,
            "valid_pixels": valid_px,
            "height_reliable": bool(reliable),
        },
        "green_blue": {
            "open_green_share": _num(ov.get("open_green_share")),
            "green_area_share": _num(ov.get("green_area_share")),
            "water_area_share": _num(ov.get("water_area_share")),
            "nearest_park_m": _num(ov.get("nearest_park_m")),
            "nearest_water_m": _num(ov.get("nearest_water_m")),
            "tree_points": _num(ov.get("tree_points")),
        },
        "air": {
            "pm25": _num(air.get("pm25")),
            "pm10": _num(air.get("pm10")),
            "no2": _num(air.get("no2")),
            "o3": _num(air.get("o3")),
            "n_monitors": _num(air.get("n_monitors")),
            "nearest_monitor_m": _num(air.get("nearest_monitor_m")),
            "measured_at": air.get("measured_at"),
            "no_monitor_in_radius": air.get("no_monitor_in_radius"),
        },
        "sources": {
            "green_blue": "Overture Maps base theme (S3), release " + str(release),
            "air": "OpenAQ v3 (median across monitors in radius)",
            "canopy": "ETH/Lang 10m Global Canopy Height 2020 (COG)",
        },
        "notes": notes,
    }
    return block, sad


def nature_register(app, data_dir, code_dir, key_path, release=DEFAULT_RELEASE):
    """Attach /analyze_nature to an existing Flask app."""
    from flask import request, jsonify

    @app.route("/analyze_nature", methods=["POST", "OPTIONS"])
    def analyze_nature_route():
        if request.method == "OPTIONS":
            return ("", 204)
        try:
            body = request.get_json(force=True)
            geom = body.get("geometry")
            if not geom:
                return jsonify({"ok": False, "error": "No geometry in request."}), 400
            t0 = time.time()
            block, sad = analyze_nature(geom, str(data_dir), str(code_dir),
                                        str(key_path), str(body.get("release", release)))
            return jsonify({"ok": True, "nature": block, "sad_id": sad,
                            "elapsed_s": round(time.time() - t0, 1)}), 200
        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({"ok": False, "error": str(e)}), 500

    print("  /analyze_nature ready (live green/blue + air + canopy on drawn boundary)")
    return app
