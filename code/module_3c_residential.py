"""
module_3c_residential.py

Adds a residential signal that does NOT depend on the Overture *places* (POI)
layer. The places layer carries housing at ~0.1-1.5% of POIs in every SAD we
measured, so residential is structurally absent from the program donut. This
module recovers it from where residential actually lives in OSM:

  - landuse=residential polygons   (the broad residential fabric)
  - building=<residential value>   (specific labeled dwellings: apartments,
                                     house, dormitory, ...)

It writes one residential layer per SAD and tags each building footprint with
an `is_residential` flag (+ provenance), independent of whether any POI fell
inside it. Residential becomes an attribute of geometry.

OUTPUTS (per SAD)
  source/<sad>/residential_osm.geojson
      Pulled residential polygons. Columns: res_kind ('landuse' | 'building'),
      building (the OSM building=* value, or null for landuse), osm_id,
      osm_type, geometry.
  derived/<sad>/residential_buildings_summary.json   (only with --enrich)
      Counts and shares of residential buildings, broken down by provenance.

With --enrich, the module also reads derived/<sad>/buildings_enriched.gpkg
(Module 5 output), adds `is_residential` (0/1) and `residential_via`
(building_tag | osm_building | osm_landuse | none), and rewrites it. This lets
you add the signal to an already-built corpus without re-running Module 5. To
have Module 5 produce it directly instead, see patch_module_05_residential.md.

NETWORK
  Mirrors Module 13: a single Overpass endpoint (overpass-api.de — the only
  one reachable from the Rossetti network) with linear backoff. `out geom;`
  is used so way/relation geometry comes back in full, not just centroids.
  The live HTTP call can only be verified on a machine with that endpoint
  reachable; the geometry assembly and tagging logic are validated offline.

HONEST CAVEAT
  OSM building=* residential tagging varies city to city, so building-level
  hits are incomplete. landuse=residential is the more consistently mapped
  signal and carries the broad fabric; the two together move residential from
  "structurally ~0" to a defensible footprint, not to ground truth.

USAGE
  # one SAD: pull + write residential_osm.geojson
  python module_3c_residential.py --source ..\\data\\32_District-Detroit_Detroit-MI\\source

  # one SAD: pull + also tag buildings_enriched.gpkg in the sibling derived dir
  python module_3c_residential.py ^
      --source  ..\\data\\32_District-Detroit_Detroit-MI\\source ^
      --derived ..\\data\\32_District-Detroit_Detroit-MI\\derived --enrich

  # whole corpus (polite spacing between Overpass calls)
  python module_3c_residential.py --scan ..\\data --enrich --sleep 3

  # re-tag from an already-pulled layer, no network
  python module_3c_residential.py --source ..\\data\\<sad>\\source ^
      --derived ..\\data\\<sad>\\derived --enrich --from-cache
"""
from __future__ import annotations
import argparse
import json
import re
import sys
import time
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union


# ─── Overpass config (mirrors module_13_transit_stations.py) ─────────────────
OVERPASS_URL    = 'https://overpass-api.de/api/interpreter'
USER_AGENT      = 'SAD-Pipeline/1.0 (ROSSETTI architectural research)'
QUERY_TIMEOUT   = 90
HTTP_TIMEOUT    = 120
RETRY_ATTEMPTS  = 3
RETRY_BACKOFF_S = 8


# ─── What counts as residential ──────────────────────────────────────────────
# OSM building=* values that mean "people live here". Deliberately conservative:
# `building=yes` is NOT residential and is excluded; ambiguous values (farm,
# warehouse, etc.) are excluded. Tune this set per local tagging if needed.
RESIDENTIAL_BUILDING_VALUES = [
    'apartments', 'residential', 'house', 'detached',
    'semidetached_house', 'terrace', 'dormitory', 'bungalow',
    'cabin', 'houseboat', 'static_caravan', 'maisonette',
    'tower_block', 'house_boat', 'farmhouse',
]

# Keywords used to read a residential class out of a building/parcel attribute
# already present in the user's buildings.geojson (municipal exports often carry
# a use/class field). Matched case-insensitively as substrings/tokens, with the
# same look-alike guards as the diagnostic (warehouse/courthouse/etc).
ATTR_RESIDENTIAL_TOKENS = {
    'apartment', 'apartments', 'condominium', 'condo', 'dormitory',
    'residential', 'dwelling', 'multifamily', 'multi_family',
    'single_family', 'townhouse', 'townhome', 'duplex', 'housing',
}
ATTR_SUBSTR_EXCLUDE = (
    'warehous', 'courthouse', 'clubhouse', 'guesthouse', 'household',
    'real_estate', 'funeral', 'non_residential', 'nonresidential',
)
# Candidate column names that might hold a building use/class in an export.
ATTR_CANDIDATE_COLS = [
    'building', 'use', 'usecode', 'use_code', 'class', 'subtype',
    'bldg_use', 'bldguse', 'landuse', 'land_use', 'lu', 'descriptor',
    'occupancy', 'zoning', 'zone',
]


# ─── Overpass query + retry ──────────────────────────────────────────────────

def _build_query(bbox_wgs) -> str:
    bbox = ','.join(f'{v:.6f}' for v in bbox_wgs)   # (south, west, north, east)
    bval = '|'.join(re.escape(v) for v in RESIDENTIAL_BUILDING_VALUES)
    return f"""
[out:json][timeout:{QUERY_TIMEOUT}];
(
  way["landuse"="residential"]({bbox});
  relation["landuse"="residential"]({bbox});
  way["building"~"^({bval})$"]({bbox});
  relation["building"~"^({bval})$"]({bbox});
);
out geom;
"""


def query_overpass(bbox_wgs):
    """POST the residential query; return the raw elements list. Retries with
    linear backoff on the single reachable endpoint, exactly like Module 13."""
    import requests  # local import so offline tests don't need the package
    query = _build_query(bbox_wgs)
    last_err = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = requests.post(OVERPASS_URL, data={'data': query},
                                 headers={'User-Agent': USER_AGENT},
                                 timeout=HTTP_TIMEOUT)
            if resp.status_code == 200:
                return resp.json().get('elements', [])
            last_err = RuntimeError(f'HTTP {resp.status_code}: {resp.text[:200]}')
        except Exception as e:  # noqa: BLE001 — requests/json errors both retry
            last_err = e
        if attempt < RETRY_ATTEMPTS:
            sleep = RETRY_BACKOFF_S * attempt
            print(f"  Overpass attempt {attempt} failed ({last_err}); "
                  f"retrying in {sleep}s...")
            time.sleep(sleep)
    raise RuntimeError(f'Overpass residential query failed after '
                       f'{RETRY_ATTEMPTS} attempts: {last_err}')


# ─── Geometry assembly from `out geom` ───────────────────────────────────────

def _ring(coords):
    """Build a closed Polygon from a list of {lat,lon}. Returns None if the
    ring is degenerate."""
    pts = [(c['lon'], c['lat']) for c in coords
           if c.get('lon') is not None and c.get('lat') is not None]
    if len(pts) < 3:
        return None
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    try:
        poly = Polygon(pts)
        if not poly.is_valid:
            poly = poly.buffer(0)
        return poly if (not poly.is_empty and poly.is_valid) else None
    except Exception:
        return None


def _relation_polygon(el):
    """Assemble a relation (multipolygon) from member outer/inner rings.
    Best-effort: outer rings unioned, inner rings subtracted where present."""
    outers, inners = [], []
    for m in el.get('members', []) or []:
        geom = m.get('geometry')
        if not geom:
            continue
        poly = _ring(geom)
        if poly is None:
            continue
        if m.get('role') == 'inner':
            inners.append(poly)
        else:
            outers.append(poly)
    if not outers:
        return None
    try:
        shell = unary_union(outers)
        if inners:
            shell = shell.difference(unary_union(inners))
        if shell.is_empty or not shell.is_valid:
            return None
        return shell
    except Exception:
        return None


def elements_to_gdf(elements) -> gpd.GeoDataFrame:
    """Turn Overpass elements into a residential-polygon GeoDataFrame (WGS84).
    res_kind is 'landuse' for landuse=residential, else 'building'."""
    rows = []
    for el in elements:
        tags = el.get('tags', {}) or {}
        etype = el.get('type')
        if etype == 'way' and el.get('geometry'):
            geom = _ring(el['geometry'])
        elif etype == 'relation':
            geom = _relation_polygon(el)
        else:
            geom = None
        if geom is None or geom.is_empty:
            continue
        is_landuse = tags.get('landuse') == 'residential'
        rows.append({
            'res_kind': 'landuse' if is_landuse else 'building',
            'building': tags.get('building') if not is_landuse else None,
            'name': tags.get('name'),
            'osm_id': el.get('id'),
            'osm_type': etype,
            'geometry': geom,
        })
    if not rows:
        return gpd.GeoDataFrame(
            columns=['res_kind', 'building', 'name', 'osm_id', 'osm_type',
                     'geometry'],
            geometry='geometry', crs='EPSG:4326')
    return gpd.GeoDataFrame(rows, geometry='geometry', crs='EPSG:4326')


# ─── Attribute-based residential read (from the user's own buildings file) ───

def _attr_is_residential(value) -> bool:
    v = str(value or '').strip().lower()
    if not v:
        return False
    if any(x in v for x in ATTR_SUBSTR_EXCLUDE):
        return False
    toks = set(re.split(r'[^a-z0-9]+', v))
    if toks & ATTR_RESIDENTIAL_TOKENS:
        return True
    return ('residential' in v) or ('housing' in v) or ('dwelling' in v)


def _find_attr_col(buildings: gpd.GeoDataFrame):
    """Return the first column whose name looks like a use/class field AND whose
    values include at least one residential-looking entry. None if no usable
    attribute exists (the common case for plain footprint exports)."""
    lower = {c.lower(): c for c in buildings.columns}
    for cand in ATTR_CANDIDATE_COLS:
        if cand in lower:
            col = lower[cand]
            try:
                if buildings[col].map(_attr_is_residential).any():
                    return col
            except Exception:
                continue
    return None


# ─── Core tagger (importable by Module 5) ────────────────────────────────────

def tag_buildings_residential(buildings: gpd.GeoDataFrame,
                              residential_layer: gpd.GeoDataFrame,
                              metric_crs) -> gpd.GeoDataFrame:
    """Add is_residential (0/1) and residential_via to a buildings GeoDataFrame.

    Priority of evidence (first match wins):
      1. building_tag  — the building's OWN use/class attribute, if the export
                         carries one and it reads residential
      2. osm_building  — footprint's interior point falls in an OSM residential
                         building polygon
      3. osm_landuse   — footprint's interior point falls in an OSM
                         landuse=residential polygon
    Returns a copy; never mutates the input.
    """
    out = buildings.copy()
    n = len(out)
    out['is_residential'] = 0
    out['residential_via'] = 'none'
    if n == 0:
        return out

    # (1) building's own attribute
    attr_col = _find_attr_col(out)
    if attr_col is not None:
        mask = out[attr_col].map(_attr_is_residential).fillna(False).values
        out.loc[mask, 'is_residential'] = 1
        out.loc[mask, 'residential_via'] = 'building_tag'

    # (2) + (3) spatial: interior point in OSM residential polygons
    if residential_layer is not None and len(residential_layer) > 0:
        res_m = residential_layer.to_crs(metric_crs)
        bld_m = out.to_crs(metric_crs)
        # Guaranteed-interior representative point for a robust point-in-poly.
        pts = bld_m.copy()
        pts['geometry'] = bld_m.geometry.representative_point()

        buildings_poly = res_m[res_m['res_kind'] == 'building']
        landuse_poly = res_m[res_m['res_kind'] == 'landuse']

        def _hits(points, polys):
            if len(polys) == 0:
                return set()
            j = gpd.sjoin(points[['geometry']], polys[['geometry']],
                          how='inner', predicate='within')
            return set(j.index)

        in_building = _hits(pts, buildings_poly)
        in_landuse = _hits(pts, landuse_poly)

        # Only fill rows still unresolved, respecting priority.
        for idx in in_building:
            if out.at[idx, 'is_residential'] == 0:
                out.at[idx, 'is_residential'] = 1
                out.at[idx, 'residential_via'] = 'osm_building'
        for idx in in_landuse:
            if out.at[idx, 'is_residential'] == 0:
                out.at[idx, 'is_residential'] = 1
                out.at[idx, 'residential_via'] = 'osm_landuse'

    return out


def summarize(buildings: gpd.GeoDataFrame) -> dict:
    n = len(buildings)
    n_res = int((buildings['is_residential'] == 1).sum()) if n else 0
    via = (buildings.loc[buildings['is_residential'] == 1, 'residential_via']
           .value_counts().to_dict()) if n else {}
    return {
        'n_buildings': n,
        'n_residential': n_res,
        'residential_share': round(n_res / n, 4) if n else 0.0,
        'by_provenance': {str(k): int(v) for k, v in via.items()},
    }


# ─── IO helpers ──────────────────────────────────────────────────────────────

def load_boundary(source_dir: Path) -> gpd.GeoDataFrame:
    p = source_dir / 'sad_boundary.geojson'
    if not p.exists():
        raise FileNotFoundError(f'sad_boundary.geojson not found in {source_dir}')
    gdf = gpd.read_file(p)
    if gdf.crs is None:
        gdf = gdf.set_crs('EPSG:4326')
    return gdf.to_crs('EPSG:4326')


def boundary_bbox_overpass(boundary: gpd.GeoDataFrame):
    minx, miny, maxx, maxy = boundary.total_bounds
    return (miny, minx, maxy, maxx)   # Overpass wants (south, west, north, east)


def resolve_metric_crs(boundary, derived_dir):
    """Prefer manifest.crs_metric (matches the rest of the pipeline); else
    estimate UTM from the boundary."""
    if derived_dir is not None:
        man = derived_dir / 'manifest.json'
        if man.exists():
            try:
                m = json.loads(man.read_text())
                crs = m.get('crs_metric')
                if crs:
                    return crs
            except Exception:
                pass
    return boundary.estimate_utm_crs()


# ─── Per-SAD driver ──────────────────────────────────────────────────────────

def process_one(source_dir: Path, derived_dir: Path | None,
                enrich: bool, from_cache: bool) -> dict:
    boundary = load_boundary(source_dir)
    res_path = source_dir / 'residential_osm.geojson'

    if from_cache:
        if not res_path.exists():
            raise FileNotFoundError(
                f'--from-cache set but {res_path} does not exist; run without '
                f'--from-cache once to pull it.')
        residential = gpd.read_file(res_path)
        print(f"  using cached residential layer: {len(residential)} polygons")
    else:
        bbox = boundary_bbox_overpass(boundary)
        print(f"  querying Overpass for residential landuse + buildings...")
        elements = query_overpass(bbox)
        residential = elements_to_gdf(elements)
        # Clip to the boundary so we don't carry the full bbox rectangle.
        if len(residential) > 0:
            try:
                residential = gpd.clip(residential, boundary.union_all())
            except AttributeError:
                residential = gpd.clip(residential, boundary.unary_union)
        residential.to_file(res_path, driver='GeoJSON')
        n_lu = int((residential['res_kind'] == 'landuse').sum()) if len(residential) else 0
        n_b = int((residential['res_kind'] == 'building').sum()) if len(residential) else 0
        print(f"  wrote {res_path.name}: {n_lu} landuse, {n_b} residential buildings")

    result = {'sad_source': str(source_dir), 'residential_polys': len(residential)}

    if enrich:
        if derived_dir is None:
            raise ValueError('--enrich requires --derived (or batch mode, which '
                             'sets it automatically)')
        enr_path = derived_dir / 'buildings_enriched.gpkg'
        if not enr_path.exists():
            print(f"  SKIP enrich: {enr_path} not found (run Module 5 first)")
            result['enriched'] = False
            return result
        metric_crs = resolve_metric_crs(boundary, derived_dir)
        enriched = gpd.read_file(enr_path, layer='buildings')
        tagged = tag_buildings_residential(enriched, residential, metric_crs)
        # cast to int for clean GPKG storage
        tagged['is_residential'] = tagged['is_residential'].astype(int)
        tagged.to_file(enr_path, driver='GPKG', layer='buildings')
        summary = summarize(tagged)
        (derived_dir / 'residential_buildings_summary.json').write_text(
            json.dumps(summary, indent=2))
        print(f"  tagged buildings_enriched.gpkg: "
              f"{summary['n_residential']}/{summary['n_buildings']} residential "
              f"({100*summary['residential_share']:.1f}%) "
              f"via {summary['by_provenance']}")
        result['enriched'] = True
        result.update(summary)

    return result


def iter_sad_dirs(root: Path):
    """Yield (source_dir, derived_dir) for every SAD under root that has a
    sad_boundary.geojson."""
    for boundary in sorted(root.rglob('sad_boundary.geojson')):
        source_dir = boundary.parent
        derived_dir = source_dir.parent / 'derived'
        yield source_dir, (derived_dir if derived_dir.exists() else None)


def main():
    ap = argparse.ArgumentParser(
        description='Pull OSM residential (landuse + buildings) and tag '
                    'building footprints with is_residential.')
    ap.add_argument('--source', type=Path,
                    help='a SAD source dir containing sad_boundary.geojson')
    ap.add_argument('--derived', type=Path, default=None,
                    help='sibling derived dir (for manifest crs + enrich)')
    ap.add_argument('--scan', type=Path, default=None,
                    help='data root to batch over every SAD')
    ap.add_argument('--enrich', action='store_true',
                    help='also tag buildings_enriched.gpkg in the derived dir')
    ap.add_argument('--from-cache', action='store_true',
                    help='reuse an existing residential_osm.geojson (no network)')
    ap.add_argument('--sleep', type=float, default=3.0,
                    help='seconds to wait between SADs in --scan mode '
                         '(politeness to the single Overpass endpoint)')
    args = ap.parse_args()

    if not args.source and not args.scan:
        ap.error('provide --source DIR or --scan DATA_ROOT')

    results = []
    if args.scan:
        if not args.scan.exists():
            sys.exit(f'--scan path not found: {args.scan}')
        sads = list(iter_sad_dirs(args.scan))
        if not sads:
            sys.exit(f'no sad_boundary.geojson found under {args.scan}')
        for i, (src, der) in enumerate(sads):
            print(f"[{i+1}/{len(sads)}] {src.parent.name}")
            try:
                results.append(process_one(src, der, args.enrich, args.from_cache))
            except Exception as e:  # noqa: BLE001 — one bad SAD shouldn't stop the run
                print(f"  ERROR: {e}")
                results.append({'sad_source': str(src), 'error': str(e)})
            if not args.from_cache and i < len(sads) - 1:
                time.sleep(args.sleep)
    else:
        results.append(process_one(args.source, args.derived,
                                   args.enrich, args.from_cache))

    # Brief cross-SAD recap when enriching multiple
    enriched = [r for r in results if r.get('enriched')]
    if len(enriched) > 1:
        print("\nRESIDENTIAL BUILDING SHARE BY SAD")
        for r in enriched:
            name = Path(r['sad_source']).parent.name
            print(f"  {name[:40]:40s} {r['n_residential']:5d}/{r['n_buildings']:5d} "
                  f"({100*r['residential_share']:4.1f}%)")


if __name__ == '__main__':
    main()
