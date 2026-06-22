"""
module_21_transit_los.py

Transit LEVEL OF SERVICE for a SAD, from GTFS (schedules) and GBFS
(bike/scooter share). Where Module 13 answers "is there a transit stop
here?", this answers "can you actually get here, and how often?" — the
distinction the typology framework cares about (transit access is a named
success factor for Entertainment, Community, and Innovation districts).

WHAT IT MEASURES  (representative weekday, served stops = inside SAD or
within a quarter-mile buffer)
  - trips_per_day        distinct vehicle trips serving the district
  - routes_serving       distinct routes touching those stops
  - modes                bus / tram / subway / rail / ferry
  - best_stop_headway    midday headway at the busiest served stop (min)
  - span_hours           first-to-last departure across served stops
  - bikeshare_docks      GBFS docks within the catchment (optional)

DATA SOURCES  (no key required)
  GTFS:  agency feed .zip (pass --gtfs-url, repeatable for multi-agency
         metros), or auto-discover via the Mobility Database catalog
         (--discover) which filters feeds by bounding-box overlap.
  GBFS:  system gbfs.json (pass --gbfs-url).

USAGE
  # One or more agency feeds (Detroit: DDOT + SMART + QLINE + People Mover)
  python module_21_transit_los.py ^
      --derived ..\\data\\32_District-Detroit_Detroit-MI\\derived ^
      --source  ..\\data\\32_District-Detroit_Detroit-MI\\source ^
      --gtfs-url https://.../ddot.zip --gtfs-url https://.../qline.zip

  # Let the catalog find feeds whose bbox overlaps the SAD
  python module_21_transit_los.py --derived ... --source ... --discover

OUTPUTS
  source/<sad>/transit_los_stops.gpkg       Served stops + LOS attributes
  derived/<sad>/transit_los/transit_los_summary.json
  derived/<sad>/transit_los/transit_los.png  Stops sized by departures, by mode
"""
from __future__ import annotations
import argparse
import datetime as dt
import io
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

sys.path.insert(0, str(Path(__file__).parent))
from shared.schemas import Manifest
import canvas_render as cr


# ─── Config ──────────────────────────────────────────────────────────────────

DEFAULT_BUFFER_FT = 1320.0          # quarter mile catchment around the SAD
MIDDAY_WINDOW = (10 * 3600, 15 * 3600)   # for headway (a 5-hour midday window)
SERVICE_DAY_MAX_S = 28 * 3600       # count departures up to 04:00 next day

# GTFS route_type -> compact mode (base spec + common extended ranges)
def route_type_to_mode(rt) -> str:
    try:
        rt = int(rt)
    except (TypeError, ValueError):
        return 'other'
    if rt == 0 or (900 <= rt <= 999):   return 'tram'
    if rt == 1 or (400 <= rt <= 499):   return 'subway'
    if rt == 2 or (100 <= rt <= 199):   return 'rail'
    if rt == 3 or (700 <= rt <= 799) or rt == 11:  return 'bus'
    if rt == 4 or (1000 <= rt <= 1199): return 'ferry'
    if rt == 5:                         return 'cable'
    if rt == 12 or (1200 <= rt <= 1299): return 'monorail'
    return 'other'

MODE_COLOR = {
    'subway': '#e8743b', 'rail': '#e8743b', 'tram': '#cdbb4f',
    'bus': '#5b9bd5', 'ferry': '#4f9d69', 'cable': '#9aa0a8',
    'monorail': '#cdbb4f', 'other': '#9aa0a8',
}
DEFAULT_CATALOG_URL = "https://share.mobilitydata.org/catalogs-csv"


# ─── GTFS loading ────────────────────────────────────────────────────────────

def _open_zip(source: str, cache_dir: Path) -> zipfile.ZipFile:
    """Open a GTFS zip from a local path or URL (cached)."""
    if source.lower().startswith(('http://', 'https://')):
        import requests
        cache_dir.mkdir(parents=True, exist_ok=True)
        safe = source.split('/')[-1].split('?')[0] or 'feed.zip'
        if not safe.endswith('.zip'):
            safe += '.zip'
        cached = cache_dir / safe
        if not (cached.exists() and cached.stat().st_size > 0):
            print(f"    downloading GTFS {source}")
            resp = requests.get(source, timeout=180)
            resp.raise_for_status()
            cached.write_bytes(resp.content)
        return zipfile.ZipFile(cached)
    return zipfile.ZipFile(source)


def load_gtfs(source: str, cache_dir: Path, feed_idx: int) -> dict:
    """Read a GTFS feed into DataFrames with ids namespaced per feed so
    multiple agencies can be merged without id collisions."""
    z = _open_zip(source, cache_dir)

    def read(table):
        # exact basename match - SEPTA ships route_stops.txt next to
        # stops.txt; endswith('stops.txt') wrongly grabbed the former.
        hits = [n for n in z.namelist() if Path(n).name.lower() == table]
        if not hits:
            return None
        with z.open(hits[0]) as fh:
            df = pd.read_csv(fh, dtype=str, low_memory=False, encoding="utf-8-sig")
        df.columns = [c.strip().lstrip("﻿") for c in df.columns]
        return df

    g = {t: read(t + '.txt') for t in
         ('stops', 'routes', 'trips', 'stop_times', 'calendar', 'calendar_dates')}
    for req in ('stops', 'routes', 'trips', 'stop_times'):
        if g[req] is None:
            raise ValueError(f"GTFS feed '{source}' missing required {req}.txt")

    pfx = f"{feed_idx}:"
    g['stops']['stop_id'] = pfx + g['stops']['stop_id']
    g['routes']['route_id'] = pfx + g['routes']['route_id']
    g['trips']['route_id'] = pfx + g['trips']['route_id']
    g['trips']['trip_id'] = pfx + g['trips']['trip_id']
    g['trips']['service_id'] = pfx + g['trips']['service_id']
    g['stop_times']['trip_id'] = pfx + g['stop_times']['trip_id']
    g['stop_times']['stop_id'] = pfx + g['stop_times']['stop_id']
    if g['calendar'] is not None:
        g['calendar']['service_id'] = pfx + g['calendar']['service_id']
    if g['calendar_dates'] is not None:
        g['calendar_dates']['service_id'] = pfx + g['calendar_dates']['service_id']
    return g


def merge_feeds(feeds: list[dict]) -> dict:
    """Concatenate the per-feed tables into one combined GTFS dict."""
    out = {}
    for t in ('stops', 'routes', 'trips', 'stop_times', 'calendar', 'calendar_dates'):
        frames = [f[t] for f in feeds if f.get(t) is not None]
        out[t] = pd.concat(frames, ignore_index=True) if frames else None
    return out


# ─── Representative weekday + active services ────────────────────────────────

_WD = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']

def active_service_ids(cal, caldates, date: dt.date) -> set:
    ds = date.strftime('%Y%m%d')
    active = set()
    if cal is not None and len(cal):
        wd = _WD[date.weekday()]
        for _, r in cal.iterrows():
            if (str(r.get(wd, '0')) == '1'
                    and str(r['start_date']) <= ds <= str(r['end_date'])):
                active.add(r['service_id'])
    if caldates is not None and len(caldates):
        for _, r in caldates[caldates['date'] == ds].iterrows():
            if str(r['exception_type']) == '1':
                active.add(r['service_id'])
            elif str(r['exception_type']) == '2':
                active.discard(r['service_id'])
    return active


def pick_representative_date(cal, caldates) -> tuple[dt.date, set]:
    """Choose a typical weekday (prefer Wednesday) that is actually in service,
    anchored near TODAY and clamped to the feed's validity window. Robust to
    sentinel far-future end_dates and to calendar_dates-only feeds. Returns
    (date, active_service_ids)."""
    today = dt.date.today()

    def _rng(series):
        d = pd.to_datetime(series, format='%Y%m%d', errors='coerce').dropna()
        return (d.min(), d.max()) if len(d) else (None, None)

    lo = hi = None
    if cal is not None and len(cal):
        s_lo, _ = _rng(cal['start_date'])
        _, s_hi = _rng(cal['end_date'])
        lo, hi = s_lo, s_hi
    if caldates is not None and len(caldates):
        c_lo, c_hi = _rng(caldates['date'])
        if c_lo is not None:
            lo = c_lo if lo is None or pd.isna(lo) else min(lo, c_lo)
            hi = c_hi if hi is None or pd.isna(hi) else max(hi, c_hi)
    if lo is None or pd.isna(lo):
        return today, active_service_ids(cal, caldates, today)
    lo, hi = lo.date(), hi.date()

    # Anchor near today (clamped into the window), then take the nearest Wed.
    anchor = min(max(today, lo), hi)
    base = anchor - dt.timedelta(days=(anchor.weekday() - 2))

    candidates = set()
    for wk in range(-26, 27):                       # +/- ~6 months of Wednesdays
        d = base + dt.timedelta(days=7 * wk)
        if lo <= d <= hi:
            candidates.add(d)
    if caldates is not None and len(caldates):       # calendar_dates-only feeds
        adds = caldates[caldates['exception_type'].astype(str) == '1']
        for ds in adds['date'].dropna().unique():
            d = pd.to_datetime(ds, format='%Y%m%d', errors='coerce')
            if pd.notna(d) and lo <= d.date() <= hi:
                candidates.add(d.date())
    if not candidates:
        candidates = {anchor}

    # Most active services wins; tie-break toward the date closest to today.
    best = max(candidates, key=lambda d: (
        len(active_service_ids(cal, caldates, d)), -abs((d - today).days)))
    return best, active_service_ids(cal, caldates, best)


def resolve_active_services(feeds: list[dict]):
    """Resolve each feed's OWN typical-weekday services, then union. Service ids
    are feed-namespaced, so the union filters each feed's trips correctly even
    when feeds have different (or sentinel) calendar windows."""
    union, info = set(), []
    for i, f in enumerate(feeds):
        d, svc = pick_representative_date(f.get('calendar'), f.get('calendar_dates'))
        union |= svc
        info.append({'feed': i, 'date': d.isoformat(), 'n_services': len(svc)})
    return union, info


# ─── Stop selection + LOS ────────────────────────────────────────────────────

def _secs(t) -> float:
    """GTFS time 'HH:MM:SS' -> seconds since midnight (HH may exceed 24)."""
    try:
        h, m, s = str(t).split(':')
        return int(h) * 3600 + int(m) * 60 + int(s)
    except Exception:
        return np.nan


def select_served_stops(g: dict, sad_poly_m, buffer_m: float, metric_crs):
    """Stops inside the SAD or within `buffer_m`. Returns a metric-CRS gdf
    tagged with 'catchment' in {inside, buffer}."""
    s = g['stops'].dropna(subset=['stop_lat', 'stop_lon']).copy()
    s['stop_lat'] = pd.to_numeric(s['stop_lat'], errors='coerce')
    s['stop_lon'] = pd.to_numeric(s['stop_lon'], errors='coerce')
    s = s.dropna(subset=['stop_lat', 'stop_lon'])
    gdf = gpd.GeoDataFrame(
        s, geometry=gpd.points_from_xy(s['stop_lon'], s['stop_lat']),
        crs='EPSG:4326').to_crs(metric_crs)
    buffered = sad_poly_m.buffer(buffer_m)
    gdf = gdf[gdf.geometry.within(buffered)].copy()
    gdf['catchment'] = np.where(gdf.geometry.within(sad_poly_m), 'inside', 'buffer')
    return gdf


def compute_los(g: dict, served_stop_ids: set, services: set,
                served_stops_gdf: gpd.GeoDataFrame) -> tuple[dict, gpd.GeoDataFrame]:
    """Level-of-service metrics for the served stops on the representative day."""
    st = g['stop_times'][g['stop_times']['stop_id'].isin(served_stop_ids)].copy()
    trips = g['trips']
    if services:
        active_trip_ids = set(trips[trips['service_id'].isin(services)]['trip_id'])
        st = st[st['trip_id'].isin(active_trip_ids)]
        approx = False
    else:
        # No resolvable calendar -> fall back to all trips (flagged approximate)
        approx = True

    st['dep_s'] = st['departure_time'].map(_secs)
    st = st.dropna(subset=['dep_s'])
    st = st[(st['dep_s'] >= 0) & (st['dep_s'] < SERVICE_DAY_MAX_S)]

    # route per trip + mode
    trip_route = trips.set_index('trip_id')['route_id'].to_dict()
    st['route_id'] = st['trip_id'].map(trip_route)
    rtype = g['routes'].set_index('route_id')['route_type'].to_dict()
    st['mode'] = st['route_id'].map(lambda r: route_type_to_mode(rtype.get(r)))

    trips_serving = st['trip_id'].nunique()
    routes_serving = st['route_id'].nunique()
    modes = sorted(set(st['mode'].dropna()))

    # per-stop departures (for the render + best-stop headway)
    per_stop = st.groupby('stop_id').agg(
        departures=('trip_id', 'nunique'),
        modes=('mode', lambda x: ','.join(sorted(set(x))))).reset_index()
    midday = st[(st['dep_s'] >= MIDDAY_WINDOW[0]) & (st['dep_s'] < MIDDAY_WINDOW[1])]
    md_by_stop = midday.groupby('stop_id')['trip_id'].nunique()
    best_stop_dep = int(per_stop['departures'].max()) if len(per_stop) else 0
    window_min = (MIDDAY_WINDOW[1] - MIDDAY_WINDOW[0]) / 60
    best_md = int(md_by_stop.max()) if len(md_by_stop) else 0
    # Combined frequency at the busiest node (ALL routes) -- a node-importance
    # signal, NOT the wait for any single line.
    busiest_node_headway = round(window_min / best_md, 1) if best_md > 0 else None
    # Typical per-line wait: median over routes of (window / that route's
    # midday departures at its busiest served stop). This is what a rider feels.
    route_headways = []
    if len(midday):
        for _, grp in midday.groupby('route_id'):
            best = grp.groupby('stop_id')['trip_id'].nunique().max()
            if best and best > 0:
                route_headways.append(window_min / best)
    median_route_headway = (round(float(np.median(route_headways)), 1)
                            if route_headways else None)

    span_hours = (round((st['dep_s'].max() - st['dep_s'].min()) / 3600, 1)
                  if len(st) else None)

    out = {
        'trips_per_day': int(trips_serving),
        'routes_serving': int(routes_serving),
        'modes': modes,
        'n_modes': len(modes),
        'median_route_headway_min': median_route_headway,
        'busiest_stop_departures_per_day': best_stop_dep,
        'busiest_node_combined_headway_min': busiest_node_headway,
        'span_hours': span_hours,
        'service_basis': 'all_trips_approx' if approx else 'representative_weekday',
    }
    # attach per-stop departures back to the geometry for rendering
    served = served_stops_gdf.merge(per_stop, on='stop_id', how='left')
    served['departures'] = served['departures'].fillna(0).astype(int)
    served['mode'] = served['modes'].fillna('other').map(
        lambda s: s.split(',')[0] if isinstance(s, str) and s else 'other')
    return out, served


# ─── GBFS (optional) ─────────────────────────────────────────────────────────

def fetch_gbfs_docks(gbfs_url: str, sad_poly_m, buffer_m: float, metric_crs):
    """Count GBFS docks/stations within the SAD catchment. Returns a dict."""
    import requests
    try:
        root = requests.get(gbfs_url, timeout=60).json()
        feeds = root['data']
        # gbfs.json nests feeds by language; take the first language block
        lang = next(iter(feeds.values())) if isinstance(feeds, dict) else feeds
        flist = lang['feeds'] if isinstance(lang, dict) else lang
        si_url = next(f['url'] for f in flist if f['name'] == 'station_information')
        stations = requests.get(si_url, timeout=60).json()['data']['stations']
    except Exception as e:
        print(f"    WARN: GBFS fetch failed ({e}); skipping bikeshare")
        return None
    df = pd.DataFrame(stations)
    if df.empty or 'lat' not in df:
        return None
    gdf = gpd.GeoDataFrame(
        df, geometry=gpd.points_from_xy(df['lon'], df['lat']),
        crs='EPSG:4326').to_crs(metric_crs)
    catch = gdf[gdf.geometry.within(sad_poly_m.buffer(buffer_m))]
    cap = (pd.to_numeric(catch.get('capacity'), errors='coerce').fillna(0).sum()
           if 'capacity' in catch else 0)
    return {'bikeshare_stations': int(len(catch)), 'bikeshare_docks': int(cap)}


# ─── Mobility Database discovery (best effort) ───────────────────────────────

def discover_gtfs_feeds(bbox_geo, catalog_url: str) -> list[str]:
    """Return GTFS feed URLs whose bounding box overlaps the SAD bbox, using
    the Mobility Database catalog CSV. Best-effort; empty on failure."""
    import requests
    minlon, minlat, maxlon, maxlat = bbox_geo
    try:
        csv = requests.get(catalog_url, timeout=120,
                           allow_redirects=True).content
        cat = pd.read_csv(io.BytesIO(csv), low_memory=False)
    except Exception as e:
        print(f"  WARN: catalog fetch failed ({e}). Pass --gtfs-url instead.")
        return []
    cols = {c.lower(): c for c in cat.columns}
    def col(*cands):
        for c in cands:
            if c in cols:
                return cols[c]
        return None
    dt_col = col('data_type')
    if dt_col is not None:
        cat = cat[cat[dt_col].astype(str).str.lower().str.contains('gtfs', na=False)]
    la1, la2 = col('location.bounding_box.minimum_latitude'), col('location.bounding_box.maximum_latitude')
    lo1, lo2 = col('location.bounding_box.minimum_longitude'), col('location.bounding_box.maximum_longitude')
    url_col = col('urls.latest', 'urls.direct_download', 'urls.direct_download_url')
    if not all([la1, la2, lo1, lo2, url_col]):
        print("  WARN: catalog schema unexpected; pass --gtfs-url instead.")
        return []
    for c in (la1, la2, lo1, lo2):
        cat[c] = pd.to_numeric(cat[c], errors='coerce')
    hit = cat[(cat[la1] <= maxlat) & (cat[la2] >= minlat) &
              (cat[lo1] <= maxlon) & (cat[lo2] >= minlon)]
    urls = [u for u in hit[url_col].dropna().tolist() if str(u).startswith('http')]
    print(f"  catalog: {len(urls)} GTFS feed(s) overlap the SAD bbox")
    return urls


# ─── Render ──────────────────────────────────────────────────────────────────

def render_transit_los(served: gpd.GeoDataFrame, source_dir: Path, out_png: Path,
                       summary: dict, buffer_m: float):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    ctx = cr.load_canvas_context(source_dir)
    minx, miny, maxx, maxy = ctx['canvas_bbox']
    fig, ax = plt.subplots(figsize=(11, 11), dpi=130)
    fig.patch.set_facecolor(cr.BG_COLOR); ax.set_facecolor(cr.BG_COLOR)

    for key, color, lw in (('streets_outside', cr.STREET_OUTSIDE, cr.STREET_OUTSIDE_WIDTH),
                           ('streets_inside', cr.STREET_INSIDE, cr.STREET_INSIDE_WIDTH)):
        g = ctx.get(key)
        if g is not None and not g.empty:
            g.plot(ax=ax, color=color, linewidth=lw, zorder=2)
    for key, color in (('buildings_outside', cr.BUILDING_OUTSIDE),
                       ('buildings_inside', cr.BUILDING_INSIDE)):
        g = ctx.get(key)
        if g is not None and not g.empty:
            g.plot(ax=ax, color=color, edgecolor='none', zorder=3)

    # quarter-mile catchment ring
    gpd.GeoSeries([ctx['sad_geom_m'].buffer(buffer_m)]).boundary.plot(
        ax=ax, color=cr.TEXT_DIM, linewidth=0.8, linestyle=(0, (3, 3)), zorder=6)
    gpd.GeoSeries([ctx['sad_geom_m']]).boundary.plot(
        ax=ax, color=cr.BOUNDARY_COLOR, linewidth=cr.BOUNDARY_WIDTH,
        linestyle=(0, (10, 6)), zorder=7)

    s = served.to_crs(ctx['metric_crs'])
    dep = s['departures'].clip(lower=0)
    sizes = 30 + (dep / max(dep.max(), 1)) * 520
    for mode in sorted(s['mode'].unique()):
        sub = s[s['mode'] == mode]
        ax.scatter(sub.geometry.x, sub.geometry.y, s=sizes[sub.index],
                   c=MODE_COLOR.get(mode, '#9aa0a8'), alpha=0.85,
                   edgecolor=cr.BG_COLOR, linewidth=0.6, zorder=9, label=mode)

    cr.draw_scale_bar_mpl(ax, ctx['canvas_bbox'])
    cr.draw_north_arrow_mpl(ax, ctx['canvas_bbox'])
    ax.set_xlim(minx, maxx); ax.set_ylim(miny, maxy)
    ax.set_aspect('equal'); ax.set_xticks([]); ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_visible(False)

    chip = dict(boxstyle='square,pad=0.5', facecolor=cr.BG_COLOR, edgecolor='none')
    ax.text(0.015, 0.985, 'Transit Level of Service', transform=ax.transAxes,
            color='#ffffff', fontsize=15, fontweight='bold', va='top', ha='left',
            family='sans-serif', bbox=chip, zorder=12)
    sub = (f"{summary['trips_per_day']} trips/day \u00b7 {summary['routes_serving']} routes "
           f"\u00b7 {', '.join(summary['modes']) or 'n/a'}")
    if summary.get('median_route_headway_min'):
        sub += f" \u00b7 typical line ~{summary['median_route_headway_min']} min"
    ax.text(0.015, 0.925, sub, transform=ax.transAxes, color=cr.TEXT_DIM,
            fontsize=10, va='top', ha='left', family='sans-serif', bbox=chip, zorder=12)
    leg = ax.legend(loc='upper right', frameon=False, labelcolor=cr.TEXT_COLOR,
                    fontsize=9, title='mode')
    if leg and leg.get_title():
        leg.get_title().set_color(cr.TEXT_DIM)

    fig.savefig(out_png, dpi=130, bbox_inches='tight',
                facecolor=cr.BG_COLOR, pad_inches=0.1)
    plt.close(fig)


# ─── Orchestration ───────────────────────────────────────────────────────────

def process_sad(derived_dir: Path, source_dir: Path, gtfs_sources: list[str],
                discover: bool, catalog_url: str, gbfs_url: str | None,
                buffer_ft: float, cache_dir: Path | None) -> Path:
    manifest_path = derived_dir / 'manifest.json'
    if not manifest_path.exists():
        sys.exit(f"manifest.json not found at {manifest_path}. Run Module 1 first.")
    manifest = Manifest.model_validate_json(manifest_path.read_text())
    if cache_dir is None:
        cache_dir = source_dir.parent.parent / '_gtfs_cache'

    if discover and not gtfs_sources:
        gtfs_sources = discover_gtfs_feeds(manifest.bbox_geo, catalog_url)
    if not gtfs_sources:
        sys.exit("No GTFS sources. Pass --gtfs-url (repeatable) or --discover.")

    print(f"Transit LOS for {manifest.sad_id} from {len(gtfs_sources)} feed(s)...")
    feeds = []
    for i, src in enumerate(gtfs_sources):
        try:
            feeds.append(load_gtfs(src, cache_dir, i))
        except Exception as e:
            print(f"  WARN: skipping feed {src}: {e}")
    if not feeds:
        sys.exit("No usable GTFS feeds loaded.")
    g = merge_feeds(feeds)

    services, feed_days = resolve_active_services(feeds)
    rep_dates = sorted({fd['date'] for fd in feed_days if fd['n_services']})
    print(f"  active services: {len(services)} across {len(feeds)} feed(s) "
          f"(per-feed typical weekday)")

    # SAD polygon in metric CRS
    sad_b = gpd.read_file(source_dir / 'sad_boundary.geojson')
    metric_crs = sad_b.estimate_utm_crs().to_string()
    sad_m = sad_b.to_crs(metric_crs)
    try:
        sad_poly = sad_m.union_all()
    except AttributeError:
        sad_poly = sad_m.unary_union
    buffer_m = buffer_ft * cr.M_PER_FT

    served_stops = select_served_stops(g, sad_poly, buffer_m, metric_crs)
    print(f"  served stops: {len(served_stops)} "
          f"({int((served_stops['catchment'] == 'inside').sum())} inside, "
          f"{int((served_stops['catchment'] == 'buffer').sum())} in buffer)")
    if served_stops.empty:
        sys.exit("No transit stops within the SAD or its buffer. "
                 "Check that the feed covers this metro.")

    summary_los, served = compute_los(
        g, set(served_stops['stop_id']), services, served_stops)

    summary = {
        'sad_id': manifest.sad_id, 'sad_name': manifest.sad_name,
        'representative_dates': rep_dates,
        'buffer_ft': buffer_ft,
        'n_feeds': len(feeds),
        'stops_inside': int((served_stops['catchment'] == 'inside').sum()),
        'stops_in_buffer': int((served_stops['catchment'] == 'buffer').sum()),
        **summary_los,
    }
    if gbfs_url:
        gbfs = fetch_gbfs_docks(gbfs_url, sad_poly, buffer_m, metric_crs)
        if gbfs:
            summary.update(gbfs)

    # Outputs
    source_dir.mkdir(parents=True, exist_ok=True)
    served_4326 = served.to_crs('EPSG:4326')
    cols = ['stop_id', 'stop_name', 'catchment', 'departures', 'mode', 'geometry']
    out_gpkg = source_dir / 'transit_los_stops.gpkg'
    served_4326[cols].to_file(out_gpkg, driver='GPKG', layer='served_stops')

    los_dir = derived_dir / 'transit_los'
    los_dir.mkdir(parents=True, exist_ok=True)
    # GeoJSON for the viewer/map (points sized by departures, colored by mode)
    try:
        served_4326[cols].to_file(los_dir / 'transit_los_stops.geojson',
                                  driver='GeoJSON')
        print(f"  wrote {los_dir / 'transit_los_stops.geojson'}")
    except Exception as e:
        print(f"  WARN: transit GeoJSON export failed ({e})")
    (los_dir / 'transit_los_summary.json').write_text(json.dumps(summary, indent=2))
    try:
        render_transit_los(served, source_dir, los_dir / 'transit_los.png',
                           summary, buffer_m)
    except Exception as e:
        print(f"  WARN: render failed ({e}); data still written")

    print(f"\n[OK] {manifest.sad_id}")
    print(f"  trips/day: {summary['trips_per_day']:,}  |  routes: {summary['routes_serving']}"
          f"  |  modes: {', '.join(summary['modes']) or 'n/a'}")
    if summary.get('median_route_headway_min'):
        print(f"  typical line midday headway: ~{summary['median_route_headway_min']} min "
              f"(median across routes)")
    if summary.get('busiest_node_combined_headway_min'):
        print(f"  busiest node: {summary['busiest_stop_departures_per_day']} departures/day "
              f"(~{summary['busiest_node_combined_headway_min']} min combined across all routes)")
    if summary.get('span_hours'):
        print(f"  span of service: {summary['span_hours']} hours")
    if summary.get('bikeshare_docks') is not None:
        print(f"  bikeshare: {summary['bikeshare_stations']} stations / "
              f"{summary['bikeshare_docks']} docks in catchment")
    if summary['service_basis'] == 'all_trips_approx':
        print("  note: calendar unresolved; LOS based on ALL trips (approximate)")
    print(f"\n  wrote {out_gpkg}")
    print(f"  wrote {los_dir / 'transit_los_summary.json'}")
    return out_gpkg


def main():
    p = argparse.ArgumentParser(description="Transit level-of-service for a SAD")
    p.add_argument('--derived', type=Path, required=True)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--gtfs-url', action='append', default=[],
                   help='GTFS feed .zip URL (repeatable for multi-agency metros)')
    p.add_argument('--gtfs-zip', action='append', default=[],
                   help='Local GTFS .zip path (repeatable)')
    p.add_argument('--discover', action='store_true',
                   help='Auto-discover feeds via the Mobility Database catalog')
    p.add_argument('--catalog-url', default=DEFAULT_CATALOG_URL,
                   help='Mobility Database catalog CSV URL')
    p.add_argument('--gbfs-url', default=None,
                   help='GBFS gbfs.json URL for bikeshare docks (optional)')
    p.add_argument('--buffer-ft', type=float, default=DEFAULT_BUFFER_FT,
                   help=f'Catchment buffer in feet (default {DEFAULT_BUFFER_FT:.0f})')
    p.add_argument('--cache-dir', type=Path, default=None,
                   help='GTFS download cache (default <data>/_gtfs_cache)')
    args = p.parse_args()
    process_sad(args.derived, args.source, list(args.gtfs_url) + list(args.gtfs_zip),
                args.discover, args.catalog_url, args.gbfs_url,
                args.buffer_ft, args.cache_dir)


if __name__ == '__main__':
    main()
