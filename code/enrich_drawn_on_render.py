#!/usr/bin/env python3
"""Run M3d on every drawn district on the Render disk, in place."""
from __future__ import annotations
import argparse, subprocess, sys, json
from pathlib import Path


def enriched_stats(geojson_path):
    try:
        d = json.loads(geojson_path.read_text())
        feats = d.get("features", [])
        total = len(feats)
        nsi = sum(1 for x in feats if x.get("properties", {}).get("occtype") is not None)
        fema = sum(1 for x in feats if x.get("properties", {}).get("fema_height") is not None)
        return total, nsi, fema
    except Exception:
        return None, None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="/data")
    ap.add_argument("--code-dir", default="/app/code")
    ap.add_argument("--pattern", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--python", default="/opt/venv/bin/python3")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    code_dir = Path(args.code_dir)
    m3d = code_dir / "module_3d_building_attrs.py"
    if not m3d.exists():
        sys.exit(f"M3d module not found at {m3d}")

    if args.pattern:
        candidates = sorted(d for d in data_dir.iterdir()
                            if d.is_dir() and args.pattern.lower() in d.name.lower())
    else:
        candidates = sorted(d for d in data_dir.iterdir()
                            if d.is_dir() and ("drawn" in d.name.lower()
                                               or "pittsburgh" in d.name.lower()))

    print(f"found {len(candidates)} candidate districts")
    if not candidates:
        return

    n_ok = n_skip = n_fail = 0
    for i, sad_dir in enumerate(candidates, 1):
        name = sad_dir.name
        src = sad_dir / "source"
        der = sad_dir / "derived"
        attrs_marker = der / "buildings_attrs.geojson"
        enriched = der / "buildings_enriched.geojson"

        if not (src / "buildings.geojson").exists() or not (src / "image_extent.geojson").exists():
            print(f"[{i}/{len(candidates)}] SKIP {name} (missing source inputs)")
            n_skip += 1
            continue
        if not enriched.exists():
            print(f"[{i}/{len(candidates)}] SKIP {name} (no buildings_enriched.geojson)")
            n_skip += 1
            continue
        if attrs_marker.exists() and not args.force:
            t, nsi, fema = enriched_stats(enriched)
            print(f"[{i}/{len(candidates)}] SKIP {name} (already enriched; total={t}, NSI={nsi}, FEMA={fema})")
            n_skip += 1
            continue

        print(f"[{i}/{len(candidates)}] RUN  {name} ...", flush=True)
        cmd = [args.python, str(m3d), "--source", str(src), "--derived", str(der)]
        try:
            r = subprocess.run(cmd, cwd=str(code_dir), capture_output=True, text=True, timeout=600)
            if r.returncode == 0:
                t, nsi, fema = enriched_stats(enriched)
                pn = (100 * nsi // t) if t else 0
                pf = (100 * fema // t) if t else 0
                print(f"        OK  total={t}, NSI={nsi} ({pn}%), FEMA={fema} ({pf}%)")
                n_ok += 1
            else:
                tail = (r.stderr or r.stdout or "").strip().splitlines()[-3:]
                print(f"        FAIL rc={r.returncode}: {' | '.join(tail)}")
                n_fail += 1
        except subprocess.TimeoutExpired:
            print(f"        FAIL timeout")
            n_fail += 1
        except Exception as e:
            print(f"        FAIL {type(e).__name__}: {e}")
            n_fail += 1

    print(f"\n=== done: OK={n_ok}, SKIP={n_skip}, FAIL={n_fail} ===")


if __name__ == "__main__":
    main()