"""
refresh_geo_manifest.py

Standalone extract of the server's _refresh_geo_manifest. Reads
  <data-dir>/_compare_ui/compare_manifest.json
adds bbox / centroid / lng / lat to each district from its sad_boundary geojson,
and writes
  <data-dir>/_compare_ui/compare_manifest_geo.json
which is the file the Compare dashboard actually reads.

Every other field on each record is copied through untouched, so program_real
(once build_compare_manifest.py is patched and rerun) rides into the geo file for
free and the donut flips to BY AREA. Run this right after build_compare_manifest.py.

Writes by default (the geo file is a regenerated artifact; the server overwrites it
on every save). Backs up any existing geo file first. Use --check to preview only.

Usage (QGIS bundled Python):
  $bat = "C:\\Program Files\\QGIS 3.40.11\\bin\\python-qgis-ltr.bat"
  & $bat refresh_geo_manifest.py --data-dir "C:\\Users\\jmeyers\\Desktop\\Detroit_Test\\data"
  & $bat refresh_geo_manifest.py --data-dir "...\\data" --check   # preview, no write
"""
import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path


def _walk(c, xs, ys):
    if isinstance(c, (list, tuple)):
        if len(c) >= 2 and isinstance(c[0], (int, float)) and isinstance(c[1], (int, float)):
            xs.append(c[0]); ys.append(c[1])
        else:
            for e in c:
                _walk(e, xs, ys)


def main():
    ap = argparse.ArgumentParser(description="Refresh compare_manifest_geo.json from compare_manifest.json")
    ap.add_argument("--data-dir", type=Path,
                    default=Path(r"C:\Users\jmeyers\Desktop\Detroit_Test\data"))
    ap.add_argument("--check", action="store_true", help="preview only, do not write")
    args = ap.parse_args()

    ui = args.data_dir / "_compare_ui"
    base_p = ui / "compare_manifest.json"
    geo_p = ui / "compare_manifest_geo.json"
    if not base_p.is_file():
        print(f"ABORT: {base_p} not found. Run build_compare_manifest.py first.")
        return

    man = json.loads(base_p.read_text(encoding="utf-8"))
    sads = man.get("sads", [])
    placed = skipped = real = 0

    for r in sads:
        if isinstance(r.get("program_real"), dict) and r["program_real"]:
            real += 1
        bpath = (r.get("artifacts") or {}).get("sad_boundary")
        if not bpath:
            skipped += 1
            continue
        gp = args.data_dir / bpath
        if not gp.is_file():
            skipped += 1
            continue
        try:
            gj = json.loads(gp.read_text(encoding="utf-8"))
            xs, ys = [], []
            feats = gj.get("features") if isinstance(gj, dict) else None
            geoms = ([fe.get("geometry") for fe in feats] if feats
                     else [gj.get("geometry") if isinstance(gj, dict) and gj.get("geometry") else gj])
            for g in geoms:
                if isinstance(g, dict) and g.get("coordinates") is not None:
                    _walk(g["coordinates"], xs, ys)
            if xs and ys:
                r["bbox"] = [min(xs), min(ys), max(xs), max(ys)]
                r["centroid"] = [(min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0]
                r["lng"] = r["centroid"][0]
                r["lat"] = r["centroid"][1]
                placed += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"  WARN {r.get('sad_id')}: {e}")
            skipped += 1

    print(f"districts: {len(sads)} | geo placed: {placed} | skipped: {skipped} | carry program_real: {real}/{len(sads)}")
    if real == 0:
        print("  note: program_real not on any record yet. Patch + rerun build_compare_manifest.py to get BY AREA.")

    if args.check:
        print("--check: no file written.")
        return

    if geo_p.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = geo_p.with_name(f"compare_manifest_geo_BACKUP_{stamp}.json")
        shutil.copy2(geo_p, bak)
        print(f"backup: {bak.name}")
    geo_p.write_text(json.dumps(man), encoding="utf-8")
    print(f"wrote {geo_p}")


if __name__ == "__main__":
    main()
