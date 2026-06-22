"""
module_14_transit_routes.py

Transit ROUTE GEOMETRY for a SAD, from GTFS shapes. Where Module 13 shows
stops and Module 21 measures level-of-service, this draws the actual lines
transit follows so you can see how routes do (or don't) thread through the
district. Colored by mode (bus / tram / subway / rail / ferry).

Reuses Module 21's GTFS machinery verbatim (feed loading, multi-agency merge,
Mobility Database discovery, route_type->mode, mode colors) so the two stay
consistent and there's one GTFS code path.

GEOMETRY
    One representative line per route = its LONGEST shape (covers the full
    extent of the line; avoids drawing every branch/short-turn variant).
    Feeds without shapes.txt fall back to a line through each route's stop
    sequence (the longest trip's ordered stops).

OUTPUT
    derived/<sad>/transit/transit_routes.geojson
      per-route: route_id, agency, short_name, long_name, mode, color (hex),
                 served (does the line enter the SAD boundary?)
    derived/<sad>/transit/transit_routes_summary.json

USAGE  (same feed flags as Module 21; reuses the same _gtfs_cache)
    python module_14_transit_routes.py --derived <d> --source <s> --discover
    python module_14_transit_routes.py --derived <d> --source <s> \
        --gtfs-url https://.../feed.zip --gtfs-url https://.../feed2.zip
"""
from __future__ import annotations
import argparse
import json
import sys
import re
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).parent))
import module_21_transit_los as los   # reuse its GTFS machinery
import canvas_render as cr
from shared.schemas import Manifest


def _read_table(g_feed_zip, table):
    """Read one GTFS table from an already-namespaced feed dict is not possible;
    we re-open shapes from the raw zip since M21's load_gtfs doesn't read it."""
    raise NotImplementedError  # placeholder; see load_routes_geometry


def build_shapes(shapes_df) -> dict:
    """shape_id -> LineString from shapes.txt (ordered by shape_pt_sequence)."""
    if shapes_df is None or shapes_df.empty:
        return {}
    df = shapes_df.copy()
    for c in ('shape_pt_lat', 'shape_pt_lon', 'shape_pt_sequence'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['shape_pt_lat', 'shape_pt_lon', 'shape_pt_sequence'])
    out = {}
    for sid, grp in df.sort_values('shape_pt_sequence').groupby('shape_id'):
        pts = list(zip(grp['shape_pt_lon'], grp['shape_pt_lat']))
        if len(pts) >= 2:
            out[sid] = LineString(pts)
    return out


def stop_sequence_line(trips, stop_times, stops_xy, route_id):
    """Fallback geometry: longest trip's ordered stops -> LineString."""
    rtrips = trips[trips['route_id'] == route_id]['trip_id'].unique()
    best = None
    for tid in rtrips:
        st = stop_times[stop_times['trip_id'] == tid]
        if st.empty:
            continue
        st = st.sort_values('stop_sequence')
        pts = [stops_xy.get(sid) for sid in st['stop_id'] if sid in stops_xy]
        pts = [p for p in pts if p is not None]
        if len(pts) >= 2 and (best is None or len(pts) > len(best)):
            best = pts
    return LineString(best) if best else None


def load_routes_geometry(gtfs_sources, cache_dir, feed_names):
    """For each feed: load via M21, additionally read shapes.txt from the same
    zip, and produce one representative LineString per route."""
    records = []
    for i, src in enumerate(gtfs_sources):
        try:
            g = los.load_gtfs(src, cache_dir, i)
        except Exception as e:
            print(f"  WARN skipping feed {src}: {e}")
            continue
        # M21 doesn't read shapes/agency; pull them from the same zip directly.
        z = los._open_zip(src, cache_dir)
        def _read(table):
            hits = [n for n in z.namelist() if n.lower().endswith(table)]
            if not hits:
                return None
            import io
            with z.open(hits[0]) as fh:
                return pd.read_csv(fh, dtype=str, low_memory=False)
        shapes_raw = _read('shapes.txt')
        agency_raw = _read('agency.txt')
        agency_name = None
        if agency_raw is not None and 'agency_name' in agency_raw.columns and len(agency_raw):
            agency_name = str(agency_raw['agency_name'].iloc[0])
        else:
            agency_name = feed_names[i] if i < len(feed_names) else f'feed{i}'

        pfx = f"{i}:"
        shapes = build_shapes(shapes_raw)
        # namespace shape ids to match M21's namespaced trips
        shapes = {pfx + k: v for k, v in shapes.items()}

        routes = g['routes']; trips = g['trips']
        # stop coords for fallback
        stops = g['stops'].copy()
        stops['stop_lat'] = pd.to_numeric(stops['stop_lat'], errors='coerce')
        stops['stop_lon'] = pd.to_numeric(stops['stop_lon'], errors='coerce')
        stops_xy = {r.stop_id: (r.stop_lon, r.stop_lat)
                    for r in stops.itertuples() if pd.notna(r.stop_lat) and pd.notna(r.stop_lon)}

        has_shape_col = 'shape_id' in trips.columns
        for _, rt in routes.iterrows():
            rid = rt['route_id']
            mode = los.route_type_to_mode(rt.get('route_type'))
            color = rt.get('route_color')
            color = ('#' + str(color)) if (isinstance(color, str) and color.strip()) else los.MODE_COLOR.get(mode, '#9aa0a8')
            geom = None
            if has_shape_col and shapes:
                rtrips = trips[trips['route_id'] == rid]
                sids = [pfx + str(s) for s in rtrips['shape_id'].dropna().unique()]
                cand = [shapes[s] for s in sids if s in shapes]
                if cand:
                    geom = max(cand, key=lambda ln: ln.length)  # longest = representative
            if geom is None:
                geom = stop_sequence_line(trips, g['stop_times'], stops_xy, rid)
            if geom is None:
                continue
            records.append({
                'route_id': rid,
                'agency': agency_name,
                'short_name': rt.get('route_short_name') or '',
                'long_name': rt.get('route_long_name') or '',
                'mode': mode,
                'color': color,
                'geometry': geom,
            })
    if not records:
        return gpd.GeoDataFrame(
            columns=['route_id', 'agency', 'short_name', 'long_name',
                     'mode', 'color', 'geometry'],
            geometry='geometry', crs='EPSG:4326')
    return gpd.GeoDataFrame(records, geometry='geometry', crs='EPSG:4326')


_GTFS_ID_RE = re.compile(r'-gtfs-(\d+)\.zip$', re.I)


def resolve_cached_feeds(bbox_geo, catalog_url, cache_dir, max_span=3.0):
    """Cache-first feed resolution for --discover. Returns local cached zip paths
    whose catalog bbox overlaps the SAD bbox, skipping nationwide/intercity feeds
    (bbox span > max_span deg). Avoids re-downloading the local _gtfs_cache.
    Returns [] if the catalog can't be fetched (caller falls back to URLs)."""
    import requests, io as _io
    try:
        cache_idx = {}
        for z in Path(cache_dir).glob('*.zip'):
            m = _GTFS_ID_RE.search(z.name)
            if m:
                cache_idx[m.group(1)] = z
        if not cache_idx:
            return []
        csv = requests.get(catalog_url, timeout=120, allow_redirects=True).content
        cat = pd.read_csv(_io.BytesIO(csv), low_memory=False)
        cols = {c.lower(): c for c in cat.columns}
        def col(*cands):
            for c in cands:
                if c in cols:
                    return cols[c]
            return None
        dt = col('data_type')
        if dt is not None:
            cat = cat[cat[dt].astype(str).str.lower().str.contains('gtfs', na=False)]
        idc = col('mdb_source_id', 'id')
        la1, la2 = col('location.bounding_box.minimum_latitude'), col('location.bounding_box.maximum_latitude')
        lo1, lo2 = col('location.bounding_box.minimum_longitude'), col('location.bounding_box.maximum_longitude')
        if not all([idc, la1, la2, lo1, lo2]):
            return []
        for c in (la1, la2, lo1, lo2):
            cat[c] = pd.to_numeric(cat[c], errors='coerce')
        cat = cat.dropna(subset=[la1, la2, lo1, lo2])
        cat[idc] = cat[idc].astype(str).str.replace(r'\.0$', '', regex=True)
        minlon, minlat, maxlon, maxlat = bbox_geo
        hit = cat[(cat[la1] <= maxlat) & (cat[la2] >= minlat) &
                  (cat[lo1] <= maxlon) & (cat[lo2] >= minlon)]
        if max_span and max_span > 0:
            hit = hit[((hit[lo2] - hit[lo1]) <= max_span) &
                      ((hit[la2] - hit[la1]) <= max_span)]
        paths = []
        for fid in hit[idc]:
            if fid in cache_idx:
                paths.append(str(cache_idx[fid]))
        return paths
    except Exception as e:
        print(f"  cache resolve failed ({e}); falling back to discover URLs")
        return []

def process_sad(derived_dir, source_dir, gtfs_sources, discover, catalog_url,
                cache_dir, clip_to_canvas=True):
    manifest_path = derived_dir / 'manifest.json'
    if not manifest_path.exists():
        sys.exit(f"manifest.json not found at {manifest_path}. Run Module 1 first.")
    manifest = Manifest.model_validate_json(manifest_path.read_text())
    if cache_dir is None:
        cache_dir = source_dir.parent.parent / '_gtfs_cache'

    feed_names = [Path(s).stem for s in gtfs_sources]
    if discover and not gtfs_sources:
        # Cache-first: reuse local _gtfs_cache by MDB id (fast, no re-download).
        cached = resolve_cached_feeds(manifest.bbox_geo, catalog_url, cache_dir)
        if cached:
            gtfs_sources = cached
            print(f"  resolved {len(cached)} feed(s) from local cache")
        else:
            gtfs_sources = los.discover_gtfs_feeds(manifest.bbox_geo, catalog_url)
        feed_names = [Path(s).stem for s in gtfs_sources]
    if not gtfs_sources:
        sys.exit("No GTFS sources. Pass --gtfs-url/--gtfs-zip (repeatable) or --discover.")

    print(f"Transit routes for {manifest.sad_id} from {len(gtfs_sources)} feed(s)...")
    routes = load_routes_geometry(gtfs_sources, cache_dir, feed_names)
    print(f"  {len(routes)} routes with geometry")
    if routes.empty:
        sys.exit("No route geometry produced. Check feeds cover this metro.")

    # SAD boundary + canvas
    sad_b = gpd.read_file(source_dir / 'sad_boundary.geojson').to_crs('EPSG:4326')
    sad_poly = unary_union(list(sad_b.geometry))
    # 'served' = the route line enters the SAD boundary
    routes['served'] = routes.geometry.apply(lambda g: g.intersects(sad_poly))

    # clip to canvas extent for the map (keep full line in a separate field? no:
    # the viewer wants canvas-clipped geometry like every other layer)
    ext_path = source_dir / 'image_extent.geojson'
    if clip_to_canvas and ext_path.exists():
        canvas = unary_union(list(gpd.read_file(ext_path).to_crs('EPSG:4326').geometry))
        routes['geometry'] = routes.geometry.intersection(canvas)
        routes = routes[~routes.geometry.is_empty & routes.geometry.notna()].copy()
    print(f"  {len(routes)} routes intersect the canvas")
    print(f"  {int(routes['served'].sum())} routes enter the SAD boundary")

    out_dir = derived_dir / 'transit'
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / 'transit_routes.geojson'
    if len(routes):
        routes.to_file(dst, driver='GeoJSON')
    else:
        dst.write_text('{"type":"FeatureCollection","features":[]}')
    print(f"  wrote {dst}")

    by_mode = routes.groupby('mode').size().to_dict() if len(routes) else {}
    summary = {
        'sad_id': manifest.sad_id,
        'n_feeds': len(gtfs_sources),
        'total_routes': int(len(routes)),
        'routes_serving_sad': int(routes['served'].sum()) if len(routes) else 0,
        'routes_by_mode': {k: int(v) for k, v in by_mode.items()},
        'source': 'GTFS shapes via Module 21 feed cache',
    }
    (out_dir / 'transit_routes_summary.json').write_text(json.dumps(summary, indent=2))
    print(f"  wrote transit_routes_summary.json")


def main():
    p = argparse.ArgumentParser(description="Transit route geometry for a SAD")
    p.add_argument('--derived', type=Path, required=True)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--gtfs-url', action='append', default=[])
    p.add_argument('--gtfs-zip', action='append', default=[])
    p.add_argument('--discover', action='store_true')
    p.add_argument('--catalog-url', default=los.DEFAULT_CATALOG_URL)
    p.add_argument('--cache-dir', type=Path, default=None)
    p.add_argument('--no-clip', action='store_true',
                   help='Keep full route lines (do not clip to canvas extent).')
    args = p.parse_args()
    process_sad(args.derived, args.source,
                list(args.gtfs_url) + list(args.gtfs_zip),
                args.discover, args.catalog_url, args.cache_dir,
                clip_to_canvas=not args.no_clip)


if __name__ == '__main__':
    main()

