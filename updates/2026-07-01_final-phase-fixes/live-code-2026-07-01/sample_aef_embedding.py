"""
sample_aef_embedding.py  -  SAD pretrained lens, Phase 0 (AlphaEarth Foundations)

Samples the Google/DeepMind AlphaEarth Foundations (AEF) 64-band annual
Satellite Embedding inside a SAD boundary and mean-pools it to ONE 64-vector
per district. No Google Earth Engine, no torch, no GCP project. Reads the COGs
the same way the canopy lens does: GDAL /vsicurl over HTTPS, clipped to the
boundary with gdal.Warp.

Access route: Source Cooperative (public AWS S3, anonymous, free), NOT the GCS
bucket. GCS is requester-pays and needs a billing project we do not have (same
reason GEE was abandoned). Source.coop hosts the identical dataset, full range
2017 to 2025, no auth.

  bucket : us-west-2.opendata.source.coop
  key    : tge-labs/aef/v1/annual/<year>/<utm_zone>/<basename>.tiff
  index  : tge-labs/aef/v1/annual/aef_index.csv   (WKT, year, utm_zone,
           utm_* bounds, wgs84_* bounds, path)
  https  : https://s3.us-west-2.amazonaws.com/us-west-2.opendata.source.coop/<key>

File facts (from the AEF GCS README):
  - each COG is 8192x8192 px, 64 channels (A00..A63), signed int8
  - NoData is -128; if masked in one channel it is masked in all
  - de-quantize to [-1, 1] with: ((v / 127.5) ** 2) * sign(v)
  - a pooled vector is sum(dequantized) then L2-normalized to unit length
    (this is exactly how AEF's own overviews are built)
  - source.coop COGs are "bottom-up"; gdal.Warp is geotransform-aware and
    writes a normalized output, so warping with the cutline sidesteps that.

Modes:
  --sad <folder>     sample one district (default: Detroit folder 32)
  --all              loop the corpus (folders 02..38, skips 01 and 39+ drawn)
  --probe            read one tile window for the chosen boundary and print the
                     pooled 64-vector norm to confirm access; writes nothing
Dry-run by default (resolves tiles, prints the plan). Add --apply to write.
Outputs are new-named and never overwrite:
  per-SAD : data\\derived\\per_sad\\<folder>\\aef_embedding_<stamp>.json
  corpus  : data\\derived\\aef_embedding_corpus_<stamp>.csv

Usage (invoked by the .ps1, QGIS bundled python):
  python-qgis-ltr.bat sample_aef_embedding.py --data-dir ...\\data --sad 32_District-Detroit_Detroit-MI --probe
  python-qgis-ltr.bat sample_aef_embedding.py --data-dir ...\\data --sad 32_District-Detroit_Detroit-MI --apply
  python-qgis-ltr.bat sample_aef_embedding.py --data-dir ...\\data --all --apply
"""
from __future__ import annotations
import argparse
import csv as _csv
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys

S3_BASE = "https://s3.us-west-2.amazonaws.com/us-west-2.opendata.source.coop"
INDEX_KEY = "tge-labs/aef/v1/annual/aef_index.csv"
INDEX_GPKG = "tge-labs/aef/v1/annual/aef_index.gpkg"
INDEX_PARQUET = "tge-labs/aef/v1/annual/aef_index.parquet"
NODATA = -128
DEQUANT_DIV = 127.5
NBANDS = 64


def _stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M")


def _load_gj(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _geom_of(gj: dict) -> dict:
    t = gj.get("type")
    if t == "FeatureCollection":
        return gj["features"][0]["geometry"]
    if t == "Feature":
        return gj["geometry"]
    return gj


def _coords(geom):
    t = geom["type"]
    cs = geom["coordinates"]
    if t == "Polygon":
        for ring in cs:
            for xy in ring:
                yield xy[0], xy[1]
    elif t == "MultiPolygon":
        for poly in cs:
            for ring in poly:
                for xy in ring:
                    yield xy[0], xy[1]


def _bbox(geom):
    xs, ys = [], []
    for x, y in _coords(geom):
        xs.append(x); ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def _boundary_path(data_dir: str, sad: str, explicit):
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    p = os.path.join(data_dir, sad, "source", "sad_boundary.geojson")
    if os.path.isfile(p):
        return p
    # tolerant fallback
    for pat in ("sad_boundary*.geojson", "*boundary*.geojson"):
        hits = sorted(glob.glob(os.path.join(data_dir, sad, "source", pat)))
        if hits:
            return hits[0]
    return None


def _corpus_folders(data_dir: str):
    out = []
    for p in sorted(glob.glob(os.path.join(data_dir, "*", "source", "sad_boundary.geojson"))):
        folder = os.path.basename(os.path.dirname(os.path.dirname(p)))
        m = re.match(r"^(\d+)_", folder)
        if not m:
            continue
        n = int(m.group(1))
        if 2 <= n <= 38:            # 01 deleted, 39+ are drawn test districts
            out.append(folder)
    return out


def _materialize_index_via_ogr(local_csv: str):
    """Fallback when the CSV index is not mirrored: read the gpkg or parquet
    index over /vsicurl with OGR and write out the CSV columns we need.
    Returns (ok, note)."""
    try:
        from osgeo import ogr, gdal
    except Exception as e:
        return False, "ogr unavailable: %s" % e
    gdal.SetConfigOption("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".gpkg,.parquet")
    want = ["year", "utm_zone", "wgs84_west", "wgs84_south", "wgs84_east", "wgs84_north", "path"]
    for blob in (INDEX_GPKG, INDEX_PARQUET):
        url = "/vsicurl/%s/%s" % (S3_BASE, blob)
        try:
            ds = ogr.Open(url)
            if ds is None:
                continue
            lyr = ds.GetLayer(0)
            defn = lyr.GetLayerDefn()
            have = {defn.GetFieldDefn(i).GetName().lower(): defn.GetFieldDefn(i).GetName()
                    for i in range(defn.GetFieldCount())}
            if not all(c in have for c in want):
                ds = None
                continue
            with open(local_csv, "w", encoding="utf-8", newline="") as f:
                w = _csv.writer(f); w.writerow(want)
                for feat in lyr:
                    w.writerow([feat.GetField(have[c]) for c in want])
            ds = None
            return True, "materialized from %s" % blob
        except Exception:
            continue
    return False, "gpkg and parquet index reads both failed"


def _download_index(cache_dir: str, force: bool):
    os.makedirs(cache_dir, exist_ok=True)
    local = os.path.join(cache_dir, "aef_index.csv")
    if os.path.isfile(local) and not force and os.path.getsize(local) > 500_000:
        return local, "cached"
    url = "%s/%s" % (S3_BASE, INDEX_KEY)
    # curl.exe ships with Windows and follows redirects to the real bytes,
    # exactly like the canopy tile fetch.
    cmd = ["curl.exe", "-L", "-f", "-s", "-S", "-o", local, url]
    try:
        subprocess.run(cmd, check=True)
    except Exception:
        pass
    if os.path.isfile(local) and os.path.getsize(local) > 500_000:
        return local, "downloaded csv"
    # CSV not present/too small: fall back to the gpkg/parquet index via OGR
    ok, note = _materialize_index_via_ogr(local)
    if ok and os.path.isfile(local) and os.path.getsize(local) > 100:
        return local, note
    return None, ("csv %s failed; %s" % (url, note))


def _index_rows_for_bbox(index_csv: str, bb, year: int):
    """Rows whose wgs84 bbox intersects the district bbox for the given year."""
    x0, y0, x1, y1 = bb
    hits = []
    with open(index_csv, "r", encoding="utf-8", newline="") as f:
        rd = _csv.DictReader(f)
        cols = {c.lower(): c for c in (rd.fieldnames or [])}
        need = ["year", "utm_zone", "wgs84_west", "wgs84_south", "wgs84_east", "wgs84_north"]
        miss = [c for c in need if c not in cols]
        if miss:
            return None, "index missing columns %s (have %s)" % (miss, rd.fieldnames)
        path_col = cols.get("path")
        for row in rd:
            try:
                if int(float(row[cols["year"]])) != year:
                    continue
                w = float(row[cols["wgs84_west"]]); s = float(row[cols["wgs84_south"]])
                e = float(row[cols["wgs84_east"]]); n = float(row[cols["wgs84_north"]])
            except Exception:
                continue
            if e < x0 or w > x1 or n < y0 or s > y1:
                continue           # no bbox overlap
            base = None
            if path_col and row.get(path_col):
                base = row[path_col].replace("\\", "/").split("/")[-1]
            hits.append({
                "utm_zone": row[cols["utm_zone"]].strip(),
                "basename": base,
                "wgs84": (w, s, e, n),
            })
    return hits, "ok"


def _tile_url(year: int, zone: str, basename: str) -> str:
    key = "tge-labs/aef/v1/annual/%d/%s/%s" % (year, zone, basename)
    return "%s/%s" % (S3_BASE, key)


def _dominant_zone(hits):
    """A 3km district sits in one UTM zone unless it straddles a 6-deg meridian.
    Keep only the zone that appears first / most, warn if we drop any."""
    zones = [h["utm_zone"] for h in hits if h["utm_zone"]]
    if not zones:
        return None, hits, []
    from collections import Counter
    top = Counter(zones).most_common(1)[0][0]
    keep = [h for h in hits if h["utm_zone"] == top]
    drop = [h for h in hits if h["utm_zone"] != top]
    return top, keep, drop


def _sample_one(gdal, np, boundary_path, tile_urls, canopy_probe=False):
    """Clip the AEF tiles to the boundary, dequantize, mean-pool to a 64-vector.
    Returns (vec64, valid_px, inside_px) or raises.

    gdal.Warp (and gdalbuildvrt) reject the source.coop bottom-up COGs
    ("positive NS resolution"), even for a single source, because the warper
    wraps the source in a VRT. So we do NOT warp. We open each tile directly
    (gdal.Open handles bottom-up), read only the boundary's pixel window,
    rasterize the reprojected boundary polygon into a mask on that same window
    grid, and mean-pool the masked, non-nodata pixels. Tiles are non-overlapping,
    so a boundary crossing two tiles splits cleanly and we just concatenate."""
    from osgeo import ogr, osr
    import json as _json
    import math as _math
    gdal.UseExceptions()
    gdal.SetConfigOption("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    gdal.SetConfigOption("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tiff,.tif")
    gdal.SetConfigOption("VSI_CACHE", "TRUE")

    gj = _load_gj(boundary_path)
    base_geom = ogr.CreateGeometryFromJson(_json.dumps(_geom_of(gj)))
    src_srs = osr.SpatialReference(); src_srs.ImportFromEPSG(4326)
    try:
        src_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    except Exception:
        pass

    cols_list = []
    valid_px = 0
    inside_px = 0
    opened = 0
    for u in tile_urls:
        ds = gdal.Open("/vsicurl/" + u)
        if ds is None or ds.RasterCount < NBANDS:
            ds = None
            continue
        gt = ds.GetGeoTransform(); proj = ds.GetProjection()
        RX, RY = ds.RasterXSize, ds.RasterYSize
        tgt_srs = osr.SpatialReference(); tgt_srs.ImportFromWkt(proj)
        try:
            tgt_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        except Exception:
            pass
        ct = osr.CoordinateTransformation(src_srs, tgt_srs)
        geom = base_geom.Clone(); geom.Transform(ct)     # boundary -> tile CRS
        minX, maxX, minY, maxY = geom.GetEnvelope()

        inv = gdal.InvGeoTransform(gt)
        if inv is None:
            ds = None
            continue
        pxs, pys = [], []
        for X, Y in ((minX, minY), (minX, maxY), (maxX, minY), (maxX, maxY)):
            pxs.append(inv[0] + X * inv[1] + Y * inv[2])
            pys.append(inv[3] + X * inv[4] + Y * inv[5])
        x0 = max(0, min(int(_math.floor(min(pxs))), RX))
        x1 = max(0, min(int(_math.ceil(max(pxs))), RX))
        y0 = max(0, min(int(_math.floor(min(pys))), RY))
        y1 = max(0, min(int(_math.ceil(max(pys))), RY))
        xs, ys = x1 - x0, y1 - y0
        if xs <= 0 or ys <= 0:
            ds = None
            continue
        arr = ds.ReadAsArray(x0, y0, xs, ys)             # (64, ys, xs)
        ds = None
        arr = np.asarray(arr)
        if arr.ndim == 2:
            arr = arr[None, :, :]
        arr = arr.astype("int16")

        wgt = (gt[0] + x0 * gt[1] + y0 * gt[2], gt[1], gt[2],
               gt[3] + x0 * gt[4] + y0 * gt[5], gt[4], gt[5])
        mask = None
        try:
            mds = gdal.GetDriverByName("MEM").Create("", xs, ys, 1, gdal.GDT_Byte)
            mds.SetGeoTransform(wgt); mds.SetProjection(proj)
            mem = ogr.GetDriverByName("Memory").CreateDataSource("m")
            lyr = mem.CreateLayer("l", srs=tgt_srs, geom_type=ogr.wkbPolygon)
            feat = ogr.Feature(lyr.GetLayerDefn()); feat.SetGeometry(geom)
            lyr.CreateFeature(feat)
            gdal.RasterizeLayer(mds, [1], lyr, burn_values=[1])
            mask = mds.ReadAsArray().astype(bool)
            mem = None; mds = None
        except Exception:
            mask = None

        flat = arr.reshape(NBANDS, -1)
        notnd = flat[0] != NODATA
        if mask is not None:
            inside = mask.reshape(-1)
            keep = inside & notnd
            inside_px += int(inside.sum())
        else:
            keep = notnd
            inside_px += int(keep.sum())
        vv = flat[:, keep]
        cols_list.append(vv)
        valid_px += int(vv.shape[1])
        opened += 1

    if opened == 0:
        raise RuntimeError("no AEF tiles opened for this boundary")
    allcols = np.hstack(cols_list).astype("float64") if cols_list else np.empty((NBANDS, 0))
    if allcols.shape[1] == 0:
        raise RuntimeError("no valid AEF pixels inside boundary (all nodata)")

    deq = ((allcols / DEQUANT_DIV) ** 2) * np.sign(allcols)   # de-quantize codes
    pooled = deq.sum(axis=1)                                    # sum -> (64,)
    norm = float(np.linalg.norm(pooled))
    vec64 = (pooled / (norm + 1e-9))                           # L2-normalize
    return vec64, valid_px, (inside_px if inside_px > 0 else valid_px)


def _write_outputs(data_dir, sad, vec64, meta, stamp):
    ddir = os.path.join(data_dir, "derived", "per_sad", sad)
    os.makedirs(ddir, exist_ok=True)
    jpath = os.path.join(ddir, "aef_embedding_%s.json" % stamp)
    if os.path.exists(jpath):
        jpath = jpath.replace(".json", "_%s.json" % _stamp())
    payload = {
        "band": "aef_embedding_64d",
        "sad_id": sad,
        "vector": [float(x) for x in vec64],
        "metrics": meta,
        "source": {
            "product": "AlphaEarth Foundations Satellite Embedding v1 annual, CC-BY 4.0",
            "attribution": "The AlphaEarth Foundations Satellite Embedding dataset is produced by Google and Google DeepMind.",
            "host": "Source Cooperative (us-west-2.opendata.source.coop)",
            "access": "GDAL /vsicurl COG clip, dequantized, sum then L2-normalized",
        },
        "pulled_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return jpath


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--sad", default="32_District-Detroit_Detroit-MI")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--year", type=int, default=2024)
    ap.add_argument("--boundary", default=None)
    ap.add_argument("--index-cache", default=None)
    ap.add_argument("--refresh-index", action="store_true")
    ap.add_argument("--probe", action="store_true", help="read one district, print vector norm, write nothing")
    ap.add_argument("--min-valid", type=int, default=200, help="flag thin sample below this many valid pixels")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out-csv", default=None)
    args = ap.parse_args()

    try:
        from osgeo import gdal  # noqa
        import numpy as np      # noqa
    except Exception as e:
        print("[FAIL] need osgeo.gdal + numpy from the QGIS python. detail:", e)
        return 2

    cache_dir = args.index_cache or os.path.join(args.data_dir, "derived", "_aef_index")
    index_csv, how = _download_index(cache_dir, args.refresh_index)
    if index_csv is None:
        print("[FAIL] could not get AEF index:", how)
        print("       tried: %s/%s" % (S3_BASE, INDEX_KEY))
        return 3
    print("[index] %s (%s, %.1f MB)" % (index_csv, how, os.path.getsize(index_csv) / 1e6))

    sads = _corpus_folders(args.data_dir) if args.all else [args.sad]
    if not sads:
        print("[FAIL] no district folders resolved under", args.data_dir)
        return 3
    print("[plan] year=%d  districts=%d  mode=%s" % (
        args.year, len(sads), "PROBE" if args.probe else ("APPLY" if args.apply else "DRY-RUN")))

    stamp = _stamp()
    rows = []
    ok = 0
    for sad in sads:
        bpath = _boundary_path(args.data_dir, sad, args.boundary if not args.all else None)
        if not bpath:
            print("  [skip] %s : no sad_boundary.geojson" % sad); continue
        geom = _geom_of(_load_gj(bpath)); bb = _bbox(geom)
        hits, note = _index_rows_for_bbox(index_csv, bb, args.year)
        if hits is None:
            print("  [FAIL] %s : %s" % (sad, note)); return 4
        if not hits:
            print("  [skip] %s : no %d tiles intersect bbox %s" % (sad, args.year, tuple(round(x,4) for x in bb))); continue
        zone, keep, drop = _dominant_zone(hits)
        urls = [_tile_url(args.year, h["utm_zone"], h["basename"]) for h in keep if h["basename"]]
        urls = [u for u in urls if u]
        if not urls:
            print("  [skip] %s : index rows had no filename (path column)" % sad); continue
        msg = "  [%s] zone=%s tiles=%d" % (sad, zone, len(urls))
        if drop:
            msg += " (dropped %d in other zones)" % len(drop)
        print(msg)
        for u in urls:
            print("        " + u)

        if not (args.apply or args.probe):
            continue
        try:
            vec64, valid_px, inside_px = _sample_one(gdal, np, bpath, urls)
        except Exception as e:
            print("        [FAIL] sample: %s" % e)
            if not args.all:
                return 5
            continue
        vfrac = round(valid_px / inside_px, 4) if inside_px else None
        thin = valid_px < args.min_valid
        vnorm = float(np.linalg.norm(vec64))
        print("        valid_px=%d inside_px=%d valid_frac=%s norm=%.4f%s" % (
            valid_px, inside_px, ("%.3f" % vfrac) if vfrac is not None else "na",
            vnorm, "  [THIN SAMPLE]" if thin else ""))
        meta = {"valid_pixels": valid_px, "inside_pixels": inside_px,
                "valid_fraction": vfrac, "thin_sample": bool(thin),
                "utm_zone": zone, "tile_count": len(urls), "year": args.year,
                "vector_norm": round(vnorm, 5)}
        if args.probe:
            continue
        if args.apply:
            jpath = _write_outputs(args.data_dir, sad, vec64, meta, stamp)
            print("        [wrote] %s" % jpath)
            row = {"sad_id": sad}
            for i in range(NBANDS):
                row["a%02d" % i] = float(vec64[i])
            row.update({"valid_pixels": valid_px, "valid_fraction": vfrac,
                        "thin_sample": int(thin), "utm_zone": zone,
                        "tile_count": len(urls), "year": args.year})
            rows.append(row); ok += 1

    if args.apply and rows:
        out_csv = args.out_csv or os.path.join(args.data_dir, "derived", "aef_embedding_corpus_%s.csv" % stamp)
        if os.path.exists(out_csv):
            out_csv = out_csv.replace(".csv", "_%s.csv" % _stamp())
        os.makedirs(os.path.dirname(out_csv), exist_ok=True)
        fields = ["sad_id"] + ["a%02d" % i for i in range(NBANDS)] + \
                 ["valid_pixels", "valid_fraction", "thin_sample", "utm_zone", "tile_count", "year"]
        with open(out_csv, "w", encoding="utf-8", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=fields); w.writeheader()
            for r in rows:
                w.writerow(r)
        print("\n[done] wrote %d district vectors -> %s" % (ok, out_csv))
    elif args.probe:
        print("\n[probe] access + dequant + pooling confirmed. Nothing written.")
    elif not args.apply:
        print("\n[dry-run] tiles resolved. Re-run with --apply to sample and write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
