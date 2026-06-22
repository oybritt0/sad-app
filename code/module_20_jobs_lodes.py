"""
module_20_jobs_lodes.py   (v2)

Workplace employment (daytime population) for a SAD, from the Census
LEHD LODES dataset. The free, high-signal answer to the Innovation/
Employment typology's core question: how much activity does the district
generate OUTSIDE event hours, what kind of work is it, and how has that
changed over time.

WHY LODES
  ACS (Module 4) tells you who LIVES in the surrounding block groups.
  LODES tells you who WORKS inside the district, block by block. The ratio
  of the two is a daytime-swing indicator: jobs >> residents => job center;
  jobs << residents => bedroom area. The sector mix separates an office/
  knowledge district (Innovation) from a consumer-serving one (Entertainment)
  on hard data, not OSM tag guesses.

ACCURACY NOTES  (per LEHD technical documentation)
  - Job type: JT00 = All Jobs (default; a 2-job worker counts twice).
    JT01 = Primary Jobs (one per worker) is the cleaner headcount for the
    jobs-per-resident ratio. Switch with --jobtype; recorded in the summary.
  - Geography vintage: LODES8 (2002-2022) is published on 2020 census
    blocks. We stay within LODES8 for ALL years so block boundaries are
    constant -> the time series is apples-to-apples. Blocks are fetched
    once and the SAD-overlap weights are reused across every year.
  - CNS10 = NAICS 52 (Finance & Insurance). The LEHD appendix mislabels
    CNS10 as "Information"; that is a doc typo. Our mapping is correct.
  - WAC also carries firm age (CFA), firm size (CFS), and education (CD);
    we surface young-firm share (an Innovation signal) and degree share.

USAGE
  # Single most-recent year (auto-falls back if the requested year 404s)
  python module_20_jobs_lodes.py ^
      --derived ..\\data\\32_District-Detroit_Detroit-MI\\derived ^
      --source  ..\\data\\32_District-Detroit_Detroit-MI\\source

  # Add a historical series (2010 -> latest available within LODES8)
  python module_20_jobs_lodes.py --derived ... --source ... --timeseries

  # Full LODES8 history, primary jobs only
  python module_20_jobs_lodes.py --derived ... --source ... ^
      --timeseries --start-year 2002 --jobtype JT01

OUTPUTS
  source/<sad>/lodes_blocks.gpkg          Blocks + WAC columns + zone tagging
  derived/<sad>/jobs/jobs_summary.json    Latest-year summary (M8 feed)
  derived/<sad>/jobs/jobs_density.png     Jobs-per-acre choropleth
  derived/<sad>/jobs/jobs_timeseries.json (only with --timeseries)
  derived/<sad>/jobs/jobs_timeseries.png  (only with --timeseries)
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import geopandas as gpd
import pandas as pd
from shapely.geometry import box as shp_box

sys.path.insert(0, str(Path(__file__).parent))
from shared.schemas import Manifest
import canvas_render as cr


# ─── LODES configuration ─────────────────────────────────────────────────────

LODES_BASE = "https://lehd.ces.census.gov/data/lodes/LODES8"   # 2020-block vintage

# LODES8 currently spans 2002-2022; some states lag a year. We probe down from
# the newest plausible year. Bump LODES_MAX_YEAR when a newer vintage lands.
LODES_MAX_YEAR = 2022
LODES_MIN_YEAR = 2002
DEFAULT_START_YEAR = 2010   # time-series default floor (brackets most SAD builds)

JOB_TYPES = {
    'JT00': 'All Jobs', 'JT01': 'Primary Jobs', 'JT02': 'All Private Jobs',
    'JT03': 'Private Primary Jobs', 'JT04': 'All Federal Jobs',
    'JT05': 'Federal Primary Jobs',
}
DEFAULT_JOBTYPE = 'JT00'

# NAICS supersector job-count columns, rolled up to a SAD-relevant scheme.
# NOTE: the LEHD appendix mislabels CNS10 as "Information"; CNS10 is NAICS 52
# (Finance & Insurance). The mapping below is correct per the NAICS standard.
CNS_LABELS = {
    'CNS01': 'agriculture', 'CNS02': 'mining', 'CNS03': 'utilities',
    'CNS04': 'construction', 'CNS05': 'manufacturing', 'CNS06': 'wholesale',
    'CNS07': 'retail', 'CNS08': 'transport_warehouse', 'CNS09': 'information',
    'CNS10': 'finance', 'CNS11': 'real_estate', 'CNS12': 'professional',
    'CNS13': 'management', 'CNS14': 'admin_support', 'CNS15': 'education',
    'CNS16': 'health', 'CNS17': 'arts_rec', 'CNS18': 'accommodation_food',
    'CNS19': 'other_services', 'CNS20': 'public_admin',
}
SECTOR_ROLLUP = {
    'office_knowledge':      ['CNS09', 'CNS10', 'CNS11', 'CNS12', 'CNS13', 'CNS14'],
    'retail':                ['CNS07'],
    'accommodation_food':    ['CNS18'],
    'arts_recreation':       ['CNS17'],
    'health':                ['CNS16'],
    'education':             ['CNS15'],
    'industrial_logistics':  ['CNS01', 'CNS02', 'CNS03', 'CNS04', 'CNS05',
                              'CNS06', 'CNS08'],
    'public_admin':          ['CNS20'],
    'other_services':        ['CNS19'],
}
CONSUMER_SERVING = ['retail', 'accommodation_food', 'arts_recreation']

CNS_COLS = list(CNS_LABELS.keys())
EARNINGS_COLS = ['CE01', 'CE02', 'CE03']            # <=$1250/mo, $1251-3333, >$3333
AGE_COLS = ['CA01', 'CA02', 'CA03']                 # <=29, 30-54, >=55
EDU_COLS = ['CD01', 'CD02', 'CD03', 'CD04']         # <HS, HS, some college, BA+
FIRMAGE_COLS = ['CFA01', 'CFA02', 'CFA03', 'CFA04', 'CFA05']   # 0-1,2-3,4-5,6-10,11+
FIRMSIZE_COLS = ['CFS01', 'CFS02', 'CFS03', 'CFS04', 'CFS05']  # 0-19 ... 500+
WAC_VALUE_COLS = (['C000'] + CNS_COLS + EARNINGS_COLS + AGE_COLS +
                  EDU_COLS + FIRMAGE_COLS + FIRMSIZE_COLS)


# ─── WAC download + auto year-fallback ───────────────────────────────────────

def _wac_url(state_abbr: str, year: int, jobtype: str) -> tuple[str, str]:
    st = state_abbr.lower()
    fname = f"{st}_wac_S000_{jobtype}_{year}.csv.gz"
    return f"{LODES_BASE}/{st}/wac/{fname}", fname


def download_wac(state_abbr: str, year: int, jobtype: str,
                 cache_dir: Path) -> pd.DataFrame | None:
    """Download (and cache) one state-year WAC file. Returns a DataFrame, or
    None if the file does not exist (404). Never exits — callers decide."""
    import gzip
    import requests

    url, fname = _wac_url(state_abbr, year, jobtype)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / fname

    if not (cached.exists() and cached.stat().st_size > 0):
        try:
            resp = requests.get(url, timeout=180)
        except requests.exceptions.RequestException as e:
            print(f"      WARN: request error for {fname}: {e}")
            return None
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        cached.write_bytes(resp.content)

    with gzip.open(cached, 'rt') as fh:
        df = pd.read_csv(fh, dtype={'w_geocode': str})
    df['w_geocode'] = df['w_geocode'].str.zfill(15)
    return df


def candidate_years(requested: int, floor: int = LODES_MIN_YEAR):
    """Yield years to try, newest first, from `requested` down to `floor`."""
    for y in range(min(requested, LODES_MAX_YEAR), floor - 1, -1):
        yield y


def resolve_latest_wac(states: list[str], requested_year: int, jobtype: str,
                       cache_dir: Path) -> tuple[int, pd.DataFrame]:
    """Find the newest year <= requested_year for which EVERY touched state has
    a WAC file, download those files, and return (year_used, concatenated_df).

    Stepping down one year at a time is the auto-fallback: a state that hasn't
    published the requested year transparently resolves to its newest year."""
    for y in candidate_years(requested_year):
        frames = []
        ok = True
        for st in states:
            df = download_wac(st, y, jobtype, cache_dir)
            if df is None:
                ok = False
                break
            frames.append(df)
        if ok and frames:
            if y != requested_year:
                print(f"  year fallback: {requested_year} unavailable; "
                      f"using newest available = {y}")
            else:
                print(f"  using LODES8 year {y}")
            return y, pd.concat(frames, ignore_index=True)
    sys.exit(f"No LODES8 WAC found for states={states} in "
             f"[{LODES_MIN_YEAR}..{requested_year}]. "
             f"(LODES is US-only; Canadian SADs are unsupported.)")


# ─── Block geometry retrieval ────────────────────────────────────────────────

def fetch_blocks_for_bbox(bbox_geo, tiger_year: int):
    """TIGER 2020 census blocks for the counties touched by bbox_geo, filtered
    to those intersecting the bbox. Returns (GeoDataFrame[EPSG:4326] with a
    normalized 15-char 'GEOID', sorted list of state USPS codes)."""
    try:
        import pygris
    except ImportError:
        sys.exit("Module 20 requires pygris. Install with: pip install pygris")

    minlon, minlat, maxlon, maxlat = bbox_geo
    bbox_poly = shp_box(minlon, minlat, maxlon, maxlat)

    counties = pygris.counties(cb=True, year=tiger_year).to_crs("EPSG:4326")
    touched = counties[counties.intersects(bbox_poly)]
    if touched.empty:
        raise ValueError(f"No counties intersect bbox {bbox_geo}")
    print(f"  bbox touches {len(touched)} counties: "
          f"{', '.join(touched['NAME'].tolist())}")

    frames, states = [], set()
    for _, c in touched.iterrows():
        st = c['STUSPS'] if 'STUSPS' in c else _fips_to_usps(c['STATEFP'])
        if st:
            states.add(st)
        frames.append(pygris.blocks(state=c['STATEFP'],
                                    county=c['COUNTYFP'], year=tiger_year))
    blocks = pd.concat(frames, ignore_index=True)
    blocks = gpd.GeoDataFrame(blocks, geometry='geometry',
                              crs=frames[0].crs).to_crs("EPSG:4326")
    geoid_col = 'GEOID20' if 'GEOID20' in blocks.columns else 'GEOID'
    blocks['GEOID'] = blocks[geoid_col].astype(str).str.zfill(15)
    blocks = blocks[blocks.intersects(bbox_poly)].copy()
    print(f"  -> {len(blocks)} intersecting blocks")
    return blocks, sorted(states)


def tag_blocks_against_sad(blocks: gpd.GeoDataFrame, source_dir: Path):
    """Add 'sad_overlap_ratio' (0..1) and interior/boundary/exterior 'zone'
    using the SAD polygon and Module 4's 70/10 rule. Constant across years."""
    metric_crs = blocks.estimate_utm_crs()
    blocks_m = blocks.to_crs(metric_crs)
    sad_path = source_dir / 'sad_boundary.geojson'
    if sad_path.exists():
        sad_b = gpd.read_file(sad_path).to_crs(metric_crs)
        try:
            sad_poly = sad_b.union_all()
        except AttributeError:
            sad_poly = sad_b.unary_union
        blocks['sad_overlap_ratio'] = (
            (blocks_m.geometry.intersection(sad_poly).area /
             blocks_m.geometry.area).clip(0.0, 1.0).values)
        blocks['zone'] = blocks['sad_overlap_ratio'].apply(
            lambda r: 'interior' if r >= 0.70 else 'boundary'
            if r >= 0.10 else 'exterior')
    else:
        print("  WARN: no sad_boundary.geojson - treating all blocks as inside")
        blocks['sad_overlap_ratio'] = 1.0
        blocks['zone'] = 'unknown'
    return blocks


def join_wac(blocks: gpd.GeoDataFrame, wac: pd.DataFrame) -> gpd.GeoDataFrame:
    """Join WAC value columns onto blocks by GEOID; missing => 0 jobs."""
    keep = ['w_geocode'] + [c for c in WAC_VALUE_COLS if c in wac.columns]
    b = blocks.merge(wac[keep], left_on='GEOID', right_on='w_geocode', how='left')
    for c in keep:
        if c != 'w_geocode':
            b[c] = pd.to_numeric(b[c], errors='coerce').fillna(0)
    return b


# ─── Aggregation (pure, testable) ────────────────────────────────────────────

def rollup_sectors(weighted_cns: dict) -> dict:
    return {bucket: float(sum(weighted_cns.get(c, 0.0) for c in codes))
            for bucket, codes in SECTOR_ROLLUP.items()}


def _weighted_sums(blocks: gpd.GeoDataFrame, cols):
    w = blocks['sad_overlap_ratio'].fillna(0.0).clip(0, 1).to_numpy()
    out = {}
    for c in cols:
        out[c] = (float(np.nansum(blocks[c].to_numpy(dtype=float) * w))
                  if c in blocks.columns else 0.0)
    return out


def _pct(part, whole):
    return round(100 * part / whole, 1) if whole and whole > 0 else None


def compute_jobs_summary(blocks, sad_id, sad_name, year, jobtype,
                         resident_population):
    """Overlap-weighted aggregation of jobs inside the SAD + mix metrics."""
    ws = _weighted_sums(blocks, WAC_VALUE_COLS)
    jobs = ws.get('C000', 0.0)

    sectors = rollup_sectors({c: ws[c] for c in CNS_COLS})
    office = sectors.get('office_knowledge', 0.0)
    consumer = sum(sectors.get(k, 0.0) for k in CONSUMER_SERVING)
    young_firms = ws.get('CFA01', 0) + ws.get('CFA02', 0)   # firms <= 3 yrs

    sector_profile = sorted(
        ({'sector': k, 'jobs': round(v), 'pct': _pct(v, jobs)}
         for k, v in sectors.items() if v > 0),
        key=lambda d: -d['jobs'])

    # Firm age/size (CFA/CFS) are not populated in every WAC file. Only emit
    # these fields when the columns are present AND carry nonzero totals, so we
    # never report a misleading "0.0%" for data that simply isn't there.
    firm_total = sum(ws.get(c, 0.0) for c in (FIRMAGE_COLS + FIRMSIZE_COLS))
    firm_avail = (any(c in blocks.columns for c in FIRMAGE_COLS + FIRMSIZE_COLS)
                  and firm_total > 0)

    return {
        'sad_id': sad_id, 'sad_name': sad_name,
        'lodes_version': 'LODES8', 'lodes_year': year,
        'job_type': jobtype, 'job_type_label': JOB_TYPES.get(jobtype, jobtype),
        'lodes_dataset': f'LODES8 WAC S000 {jobtype} {year}',
        'blocks_total': int(len(blocks)),
        'blocks_interior': int((blocks.get('zone') == 'interior').sum())
                           if 'zone' in blocks.columns else None,

        'jobs_inside': round(jobs),
        'resident_population': round(resident_population)
                               if resident_population else None,
        'jobs_per_resident': (round(jobs / resident_population, 2)
                              if resident_population and resident_population > 0
                              else None),

        'office_knowledge_jobs': round(office),
        'office_knowledge_pct': _pct(office, jobs),
        'consumer_serving_jobs': round(consumer),
        'consumer_serving_pct': _pct(consumer, jobs),

        'pct_jobs_high_earning': _pct(ws.get('CE03', 0), jobs),
        'pct_jobs_low_earning': _pct(ws.get('CE01', 0), jobs),
        'pct_jobs_under_30': _pct(ws.get('CA01', 0), jobs),
        # Education + firm dynamics (Innovation-typology signals)
        'pct_jobs_bachelors_plus': _pct(ws.get('CD04', 0), jobs),
        'firm_characteristics_available': bool(firm_avail),
        'pct_jobs_young_firms': _pct(young_firms, jobs) if firm_avail else None,
        'pct_jobs_small_firms': (_pct(ws.get('CFS01', 0), jobs)
                                 if firm_avail else None),
        'pct_jobs_large_firms': (_pct(ws.get('CFS05', 0), jobs)
                                 if firm_avail else None),

        'sector_profile': sector_profile,
        'top_sector': sector_profile[0]['sector'] if sector_profile else None,
    }


def summarize_timeseries(series: list[dict]) -> dict:
    """Trend metrics over a list of per-year {year, jobs_inside, ...} dicts."""
    s = sorted([r for r in series if r.get('jobs_inside') is not None],
               key=lambda r: r['year'])
    if not s:
        return {}
    first, last = s[0], s[-1]
    j0, j1 = first['jobs_inside'], last['jobs_inside']
    span = last['year'] - first['year']
    cagr = (round(100 * ((j1 / j0) ** (1 / span) - 1), 1)
            if span > 0 and j0 > 0 else None)
    peak = max(s, key=lambda r: r['jobs_inside'])
    return {
        'first_year': first['year'], 'last_year': last['year'],
        'jobs_first': j0, 'jobs_last': j1,
        'jobs_change': j1 - j0,
        'pct_change': _pct(j1 - j0, j0),
        'cagr_pct': cagr,
        'peak_year': peak['year'], 'peak_jobs': peak['jobs_inside'],
    }


# ─── Renders ─────────────────────────────────────────────────────────────────

def render_jobs_density(blocks, source_dir, out_png, sad_id, year):
    """KPF-style dark choropleth: jobs per acre by block, quantile-binned."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    ctx = cr.load_canvas_context(source_dir)
    metric_crs = ctx['metric_crs']
    minx, miny, maxx, maxy = ctx['canvas_bbox']

    blk = blocks.to_crs(metric_crs).copy()
    blk = gpd.clip(blk, ctx['canvas_geom_m'])
    blk = blk[~blk.geometry.is_empty & blk.geometry.notna()]
    acres = (blk.geometry.area * cr.FT_PER_M ** 2) / 43560.0
    blk['jobs_per_acre'] = np.where(acres > 0,
                                    blk['C000'].astype(float) / acres, 0.0)

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

    ramp = ['#243447', '#2f6b6b', '#4f9d69', '#cdbb4f', '#e8743b']
    positive = blk[blk['jobs_per_acre'] > 0]
    qs = (positive['jobs_per_acre'].quantile([.2, .4, .6, .8]).to_list()
          if len(positive) >= 5 else None)

    def value_color(v):
        if v <= 0:
            return None
        if qs is None:
            return ramp[-1]
        for i, q in enumerate(qs):
            if v <= q:
                return ramp[i]
        return ramp[-1]

    blk['_fill'] = blk['jobs_per_acre'].apply(value_color)
    # Dim the exterior-zone blocks so the SAD reads as the subject; keep
    # interior/boundary blocks at full saturation.
    if 'zone' in blk.columns:
        ext = blk[blk['zone'] == 'exterior']
        inb = blk[blk['zone'] != 'exterior']
    else:
        ext, inb = blk.iloc[0:0], blk
    for grp, alpha, z in ((ext, 0.30, 4), (inb, 0.96, 5)):
        for color in ramp:
            sub = grp[grp['_fill'] == color]
            if not sub.empty:
                sub.plot(ax=ax, color=color, edgecolor=cr.BG_COLOR,
                         linewidth=0.3, zorder=z, alpha=alpha)

    gpd.GeoSeries([ctx['sad_geom_m']]).boundary.plot(
        ax=ax, color=cr.BOUNDARY_COLOR, linewidth=cr.BOUNDARY_WIDTH,
        linestyle=(0, (10, 6)), zorder=8)
    cr.draw_scale_bar_mpl(ax, ctx['canvas_bbox'])
    cr.draw_north_arrow_mpl(ax, ctx['canvas_bbox'])

    ax.set_xlim(minx, maxx); ax.set_ylim(miny, maxy)
    ax.set_aspect('equal'); ax.set_xticks([]); ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_visible(False)
    chip = dict(boxstyle='square,pad=0.5', facecolor=cr.BG_COLOR, edgecolor='none')
    ax.text(0.015, 0.985, 'Workplace Employment', transform=ax.transAxes,
            color='#ffffff', fontsize=15, fontweight='bold', va='top', ha='left',
            family='sans-serif', bbox=chip, zorder=12)
    ax.text(0.015, 0.925, f'Jobs per acre by census block \u00b7 LODES8 {year}',
            transform=ax.transAxes, color=cr.TEXT_DIM, fontsize=10, va='top',
            ha='left', family='sans-serif', bbox=chip, zorder=12)
    # Compact quantile legend (low -> high jobs/acre)
    if qs is not None:
        lx, ly, sw = 0.015, 0.19, 0.022
        ax.text(lx, ly + 0.05, 'Jobs / acre', transform=ax.transAxes,
                color=cr.TEXT_COLOR, fontsize=9, va='bottom', ha='left',
                family='sans-serif', bbox=chip, zorder=12)
        for i, color in enumerate(ramp):
            ax.add_patch(plt.Rectangle((lx + i * sw, ly), sw, 0.022,
                         transform=ax.transAxes, facecolor=color,
                         edgecolor='none', zorder=12))
        ax.text(lx, ly - 0.012, 'low', transform=ax.transAxes, color=cr.TEXT_DIM,
                fontsize=8, va='top', ha='left', family='sans-serif', zorder=12)
        ax.text(lx + len(ramp) * sw, ly - 0.012, 'high', transform=ax.transAxes,
                color=cr.TEXT_DIM, fontsize=8, va='top', ha='right',
                family='sans-serif', zorder=12)
    fig.savefig(out_png, dpi=130, bbox_inches='tight',
                facecolor=cr.BG_COLOR, pad_inches=0.1)
    plt.close(fig)


def render_timeseries(series: list[dict], trend: dict, out_png: Path,
                      sad_name: str, jobtype: str):
    """Flat dark line chart: jobs inside the SAD over time."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    s = sorted([r for r in series if r.get('jobs_inside') is not None],
               key=lambda r: r['year'])
    years = [r['year'] for r in s]
    jobs = [r['jobs_inside'] for r in s]

    fig, ax = plt.subplots(figsize=(11, 6), dpi=130)
    fig.patch.set_facecolor(cr.BG_COLOR); ax.set_facecolor(cr.BG_COLOR)

    ax.plot(years, jobs, color='#e8743b', linewidth=2.4, zorder=5,
            solid_capstyle='round')
    ax.scatter(years, jobs, color='#e8743b', s=22, zorder=6)
    for r in (s[0], s[-1]):
        ax.annotate(f"{r['jobs_inside']:,}", (r['year'], r['jobs_inside']),
                    textcoords='offset points', xytext=(0, 10),
                    color=cr.TEXT_COLOR, fontsize=10, ha='center',
                    family='sans-serif')

    ax.set_ylim(0, max(jobs) * 1.18)
    ax.set_xticks(years)
    ax.tick_params(colors=cr.TEXT_DIM, labelsize=9)
    for sp in ax.spines.values():
        sp.set_color(cr.TEXT_DIM)
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    ax.grid(axis='y', color='#222222', linewidth=0.6, zorder=0)

    title = 'Workplace Employment Over Time'
    sub = f"{sad_name} \u00b7 jobs inside SAD \u00b7 LODES8 {JOB_TYPES.get(jobtype, jobtype)}"
    if trend.get('pct_change') is not None:
        arrow = '\u25b2' if trend['jobs_change'] >= 0 else '\u25bc'
        sub += (f"  \u00b7  {arrow} {trend['pct_change']:+}% "
                f"{trend['first_year']}\u2013{trend['last_year']}")
    ax.set_title(title, color=cr.TEXT_COLOR, fontsize=15, fontweight='bold',
                 loc='left', family='sans-serif', pad=18)
    ax.text(0.0, 1.02, sub, transform=ax.transAxes, color=cr.TEXT_DIM,
            fontsize=10, ha='left', va='bottom', family='sans-serif')

    fig.savefig(out_png, dpi=130, bbox_inches='tight',
                facecolor=cr.BG_COLOR, pad_inches=0.15)
    plt.close(fig)


# ─── Orchestration ───────────────────────────────────────────────────────────

def export_jobs_geojson(blocks, year_used, year_cols, out_path):
    """Write block polygons (EPSG:4326) with jobs, per-acre density, zone, and a
    jobs_<year> column per historical year so the viewer can recolor by year
    client-side without refetching."""
    g = blocks.copy()
    gm = g.to_crs(g.estimate_utm_crs())
    g['acres'] = (gm.geometry.area * 0.000247105).round(3)   # m^2 -> acres
    g['jobs'] = g['C000'].round().astype(int)
    g['jobs_per_acre'] = np.where(g['acres'] > 0,
                                  (g['jobs'] / g['acres']).round(2), 0.0)
    g['lodes_year'] = int(year_used)
    for y in sorted(year_cols):
        g[f'jobs_{y}'] = g['GEOID'].map(year_cols[y]).fillna(0).round().astype(int)
    keep = (['GEOID', 'zone', 'sad_overlap_ratio', 'jobs', 'acres',
             'jobs_per_acre', 'lodes_year']
            + [f'jobs_{y}' for y in sorted(year_cols)] + ['geometry'])
    keep = [c for c in keep if c in g.columns]
    g[keep].to_file(out_path, driver='GeoJSON')
    return out_path


def process_sad(derived_dir: Path, source_dir: Path, requested_year: int,
                jobtype: str, cache_dir: Path | None, do_timeseries: bool,
                start_year: int, tiger_year: int) -> Path:
    manifest_path = derived_dir / 'manifest.json'
    if not manifest_path.exists():
        sys.exit(f"manifest.json not found at {manifest_path}. Run Module 1 first.")
    manifest = Manifest.model_validate_json(manifest_path.read_text())
    if cache_dir is None:
        cache_dir = source_dir.parent.parent / '_lodes_cache'

    print(f"Fetching LODES workplace jobs for {manifest.sad_id} "
          f"(job type {jobtype} = {JOB_TYPES.get(jobtype, jobtype)})...")
    blocks, states = fetch_blocks_for_bbox(manifest.bbox_geo, tiger_year)
    blocks = tag_blocks_against_sad(blocks, source_dir)

    resident_pop = None
    census_summary = derived_dir / 'census_summary.json'
    if census_summary.exists():
        try:
            resident_pop = json.loads(census_summary.read_text()).get(
                'estimated_population')
        except Exception:
            pass

    # ── Latest-year summary (auto year-fallback) ─────────────────────────────
    year_used, wac = resolve_latest_wac(states, requested_year, jobtype, cache_dir)
    blocks = join_wac(blocks, wac)

    source_dir.mkdir(parents=True, exist_ok=True)
    out_gpkg = source_dir / 'lodes_blocks.gpkg'
    blocks.to_file(out_gpkg, driver='GPKG', layer='lodes_blocks')

    jobs_dir = derived_dir / 'jobs'
    jobs_dir.mkdir(parents=True, exist_ok=True)
    summary = compute_jobs_summary(blocks, manifest.sad_id, manifest.sad_name,
                                   year_used, jobtype, resident_pop)
    (jobs_dir / 'jobs_summary.json').write_text(json.dumps(summary, indent=2))
    try:
        render_jobs_density(blocks, source_dir, jobs_dir / 'jobs_density.png',
                            manifest.sad_id, year_used)
    except Exception as e:
        print(f"  WARN: density render failed ({e}); data still written")

    print(f"\n[OK] {manifest.sad_id}")
    print(f"  dataset: {summary['lodes_dataset']}")
    print(f"  jobs inside SAD (overlap-weighted): {summary['jobs_inside']:,}")
    if summary['jobs_per_resident'] is not None:
        print(f"  jobs per resident: {summary['jobs_per_resident']} "
              f"({'job center' if summary['jobs_per_resident'] >= 1 else 'residential'})")
    if summary['office_knowledge_pct'] is not None:
        print(f"  office/knowledge: {summary['office_knowledge_pct']}%  |  "
              f"consumer-serving: {summary['consumer_serving_pct']}%")
    if summary['pct_jobs_bachelors_plus'] is not None:
        line = f"  bachelor's+: {summary['pct_jobs_bachelors_plus']}%"
        if summary['pct_jobs_young_firms'] is not None:
            line += (f"  |  jobs at young firms (<=3 yrs): "
                     f"{summary['pct_jobs_young_firms']}%")
        print(line)
    if not summary.get('firm_characteristics_available', False):
        print("  note: firm age/size (CFA/CFS) not populated in this WAC file; "
              "those fields omitted")
    print(f"  wrote {jobs_dir / 'jobs_summary.json'}")

    # ── Optional historical series within LODES8 (constant geography) ────────
    year_cols = {}   # year -> {GEOID: jobs}, for the per-block per-year GeoJSON
    if do_timeseries:
        print(f"\n  building time series {start_year}\u2013{year_used} "
              f"(LODES8, 2020 blocks, weights reused)...")
        base = blocks[['GEOID', 'geometry', 'sad_overlap_ratio', 'zone']].copy()
        series = []
        for y in range(start_year, year_used + 1):
            frames, ok = [], True
            for st in states:
                df = download_wac(st, y, jobtype, cache_dir)
                if df is None:
                    ok = False
                    break
                frames.append(df)
            if not ok:
                continue
            by = join_wac(base.copy(), pd.concat(frames, ignore_index=True))
            year_cols[y] = dict(zip(by['GEOID'], by['C000']))
            sy = compute_jobs_summary(by, manifest.sad_id, manifest.sad_name,
                                      y, jobtype, resident_pop)
            series.append({
                'year': y, 'jobs_inside': sy['jobs_inside'],
                'office_knowledge_pct': sy['office_knowledge_pct'],
                'consumer_serving_pct': sy['consumer_serving_pct'],
                'pct_jobs_high_earning': sy['pct_jobs_high_earning'],
                'pct_jobs_young_firms': sy['pct_jobs_young_firms'],
            })
            print(f"    {y}: {sy['jobs_inside']:,} jobs")
        trend = summarize_timeseries(series)
        out = {'sad_id': manifest.sad_id, 'sad_name': manifest.sad_name,
               'lodes_version': 'LODES8', 'job_type': jobtype,
               'years': [r['year'] for r in series],
               'trend': trend, 'series': series}
        (jobs_dir / 'jobs_timeseries.json').write_text(json.dumps(out, indent=2))
        try:
            render_timeseries(series, trend, jobs_dir / 'jobs_timeseries.png',
                              manifest.sad_name, jobtype)
        except Exception as e:
            print(f"  WARN: timeseries render failed ({e}); data still written")
        if trend:
            print(f"  trend: {trend['jobs_first']:,} ({trend['first_year']}) "
                  f"\u2192 {trend['jobs_last']:,} ({trend['last_year']})  "
                  f"= {trend['pct_change']:+}%  (CAGR {trend['cagr_pct']}%)")
        print(f"  wrote {jobs_dir / 'jobs_timeseries.json'}")

    # ── Spatial export for the viewer/map (block choropleth, per-year aware) ──
    try:
        gj = export_jobs_geojson(blocks, year_used, year_cols,
                                 jobs_dir / 'jobs_blocks.geojson')
        print(f"  wrote {gj}"
              + (f"  ({len(year_cols)} years for time slider)" if year_cols else ""))
    except Exception as e:
        print(f"  WARN: jobs GeoJSON export failed ({e})")

    print(f"\n  wrote {out_gpkg}")
    return out_gpkg


_FIPS_USPS = {
    '01':'AL','02':'AK','04':'AZ','05':'AR','06':'CA','08':'CO','09':'CT',
    '10':'DE','11':'DC','12':'FL','13':'GA','15':'HI','16':'ID','17':'IL',
    '18':'IN','19':'IA','20':'KS','21':'KY','22':'LA','23':'ME','24':'MD',
    '25':'MA','26':'MI','27':'MN','28':'MS','29':'MO','30':'MT','31':'NE',
    '32':'NV','33':'NH','34':'NJ','35':'NM','36':'NY','37':'NC','38':'ND',
    '39':'OH','40':'OK','41':'OR','42':'PA','44':'RI','45':'SC','46':'SD',
    '47':'TN','48':'TX','49':'UT','50':'VT','51':'VA','53':'WA','54':'WV',
    '55':'WI','56':'WY',
}
def _fips_to_usps(fips):
    return _FIPS_USPS.get(str(fips).zfill(2))


def main():
    p = argparse.ArgumentParser(description="LODES workplace employment for a SAD")
    p.add_argument('--derived', type=Path, required=True)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--year', type=int, default=LODES_MAX_YEAR,
                   help=f'Requested LODES year; auto-falls back to the newest '
                        f'available <= this (default {LODES_MAX_YEAR})')
    p.add_argument('--jobtype', choices=list(JOB_TYPES), default=DEFAULT_JOBTYPE,
                   help='JT00=All Jobs (default), JT01=Primary Jobs (one per worker)')
    p.add_argument('--timeseries', action='store_true',
                   help='Also build a historical jobs series within LODES8')
    p.add_argument('--start-year', type=int, default=DEFAULT_START_YEAR,
                   help=f'First year of the time series (default {DEFAULT_START_YEAR})')
    p.add_argument('--tiger-year', type=int, default=2021,
                   help='TIGER block vintage to fetch (2020-block geography)')
    p.add_argument('--cache-dir', type=Path, default=None,
                   help='LODES download cache (default <data>/_lodes_cache)')
    args = p.parse_args()
    process_sad(args.derived, args.source, args.year, args.jobtype,
                args.cache_dir, args.timeseries, args.start_year, args.tiger_year)


if __name__ == '__main__':
    main()
