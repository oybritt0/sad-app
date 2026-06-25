"""
module_3d_building_attrs.py

Enrich the SAD's building footprints with attribute data from two complementary
public sources:

  - USACE NSI (National Structure Inventory) - POINT-based national database
    of structure attributes (HAZUS occupancy class, sqft, num_story, year built,
    population estimates, structure valuation). REST API, no auth.
    https://nsi.sec.usace.army.mil/nsiapi/structures

  - FEMA USA Structures - POLYGON footprints with occupancy class and height,
    125M+ structures. ArcGIS Feature Server. Optional - the NSI bulk handles
    the bulk of useful attribution; FEMA adds HEIGHT specifically.

USAGE
    python module_3d_building_attrs.py --source <dir> --derived <dir>
    python module_3d_building_attrs.py --source <dir> --derived <dir> --no-fema
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import mapping


NSI_URL = "https://nsi.sec.usace.army.mil/nsiapi/structures"
NSI_KEEP_COLS = [
    'fd_id', 'occtype', 'st_damcat', 'sqft', 'num_story', 'med_yr_blt',
    'val_struct', 'val_cont', 'pop2amu65', 'pop2pmu65', 'found_type',
    'firmzone', 'bldgtype', 'found_ht',
]

# FEMA USA Structures Feature Server. The correct endpoint is the "View" layer
# on services2.arcgis.com (not services.arcgis.com - that was the wrong host).
# Item id 0ec8512ad21e4bb987d7e848d14e7e24, hosted on FiaPA4ga0iQKduv3.
FEMA_FEATURE_URL = (
    "https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services/"
    "USA_Structures_View/FeatureServer/0/query"
)
# FEMA USA Structures fields. Confirmed from the live layer schema at
# services2.arcgis.com/FiaPA4ga0iQKduv3/.../USA_Structures_View/FeatureServer/0
# Notes on field names:
#  - BLDG_AREA does not exist; the dataset uses SQMETERS / SQFEET
#  - IMG_DATE does not exist; the dataset uses IMAGE_DATE
#  - POP_MEDIAN is the median building population estimate
#  - PROP_ADDR/CITY/ST/ZIP give actual addresses
FEMA_KEEP_COLS = [
    'OCC_CLS', 'PRIM_OCC', 'SEC_OCC',
    'HEIGHT', 'SQMETERS', 'SQFEET',
    'POP_MEDIAN',
    'PROP_ADDR', 'PROP_CITY', 'PROP_ST', 'PROP_ZIP',
    'IMAGE_DATE', 'PROD_DATE',
    'B_CODE', 'VAL_METHOD',
    'H_ADJ_ELEV', 'L_ADJ_ELEV',
]


def fetch_nsi(extent_poly_4326) -> gpd.GeoDataFrame:
    geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {},
            "geometry": mapping(extent_poly_4326),
        }]
    }
    try:
        r = requests.post(f"{NSI_URL}?fmt=fc", json=geojson, timeout=120)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"  WARN NSI fetch failed: {e}")
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    fc = r.json()
    feats = fc.get('features', [])
    if not feats:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    gdf = gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326")
    keep = ['geometry'] + [c for c in NSI_KEEP_COLS if c in gdf.columns]
    return gdf[keep].copy()


def fetch_fema(extent_poly_4326, page_size=2000) -> gpd.GeoDataFrame:
    minx, miny, maxx, maxy = extent_poly_4326.bounds
    out_rows = []
    offset = 0
    while True:
        params = {
            'where': '1=1',
            'geometry': f'{minx},{miny},{maxx},{maxy}',
            'geometryType': 'esriGeometryEnvelope',
            'inSR': 4326,
            'spatialRel': 'esriSpatialRelIntersects',
            'outFields': ','.join(FEMA_KEEP_COLS),
            'outSR': 4326,
            'f': 'geojson',
            'resultOffset': offset,
            'resultRecordCount': page_size,
        }
        try:
            r = requests.get(FEMA_FEATURE_URL, params=params, timeout=120)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"  WARN FEMA fetch failed at offset {offset}: {e}")
            break
        fc = r.json()
        feats = fc.get('features', [])
        if not feats:
            break
        out_rows.extend(feats)
        if len(feats) < page_size:
            break
        offset += page_size
        time.sleep(0.2)
    if not out_rows:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    gdf = gpd.GeoDataFrame.from_features(out_rows, crs="EPSG:4326")
    gdf = gdf[gdf.intersects(extent_poly_4326)].copy()
    return gdf


def _utm_epsg(lon: float, lat: float) -> str:
    zone = int((lon + 180) // 6) + 1
    return f"EPSG:326{zone:02d}"


def join_nsi_to_buildings(buildings, nsi_pts, max_dist_m=25.0):
    if nsi_pts.empty:
        return buildings.copy()
    centroid = buildings.geometry.unary_union.centroid
    utm = _utm_epsg(centroid.x, centroid.y)
    bldg_m = buildings.to_crs(utm).reset_index(drop=True)
    nsi_m = nsi_pts.to_crs(utm).reset_index(drop=True)
    bldg_m['_bldg_idx'] = bldg_m.index
    joined = gpd.sjoin_nearest(
        nsi_m, bldg_m[['_bldg_idx', 'geometry']],
        how='left', max_distance=max_dist_m, distance_col='_join_dist_m'
    )
    agg = {}
    for c in NSI_KEEP_COLS:
        if c not in joined.columns:
            continue
        if c in {'sqft', 'num_story', 'val_struct', 'val_cont',
                 'pop2amu65', 'pop2pmu65', 'med_yr_blt', 'found_ht'}:
            agg[c] = 'mean'
        else:
            agg[c] = lambda s: s.mode().iloc[0] if not s.mode().empty else None
    if not agg:
        return buildings.copy()
    by_bldg = joined.dropna(subset=['_bldg_idx']).groupby('_bldg_idx').agg(agg)
    out = buildings.reset_index(drop=True).copy()
    for c in by_bldg.columns:
        out[c] = out.index.map(by_bldg[c]).astype(object)
    return out


def join_fema_to_buildings(buildings, fema_polys):
    if fema_polys.empty:
        return buildings.copy()
    centroid = buildings.geometry.unary_union.centroid
    utm = _utm_epsg(centroid.x, centroid.y)
    bldg_m = buildings.to_crs(utm).reset_index(drop=True)
    fema_m = fema_polys.to_crs(utm).reset_index(drop=True)
    bldg_m['_bldg_idx'] = bldg_m.index
    inter = gpd.overlay(fema_m, bldg_m[['_bldg_idx', 'geometry']],
                        how='intersection', keep_geom_type=False)
    if inter.empty:
        return buildings.copy()
    inter['_area'] = inter.geometry.area
    best = inter.sort_values('_area', ascending=False).drop_duplicates('_bldg_idx')
    cols = ['_bldg_idx'] + [c for c in FEMA_KEEP_COLS if c in best.columns]
    out = buildings.reset_index(drop=True).copy()
    bm = best.set_index('_bldg_idx')[cols[1:]]
    for c in bm.columns:
        out[f'fema_{c.lower()}'] = out.index.map(bm[c]).astype(object)
    return out


def process_sad(source_dir, derived_dir, use_fema=True, use_nsi=True,
                nsi_max_dist_m=25.0):
    bldgs_path = source_dir / 'buildings.geojson'
    extent_path = source_dir / 'image_extent.geojson'
    if not bldgs_path.exists():
        sys.exit(f"buildings.geojson not found at {bldgs_path}")
    if not extent_path.exists():
        sys.exit(f"image_extent.geojson not found at {extent_path}")
    buildings = gpd.read_file(bldgs_path).to_crs("EPSG:4326")
    extent = gpd.read_file(extent_path).to_crs("EPSG:4326")
    extent_poly = extent.iloc[0].geometry
    print(f"  loaded {len(buildings)} buildings, extent area {extent_poly.area:.6f} deg^2")
    out = buildings.copy()
    if use_nsi:
        print(f"  fetching NSI points for extent polygon...")
        nsi = fetch_nsi(extent_poly)
        print(f"  NSI: {len(nsi)} points returned")
        if len(nsi):
            out = join_nsi_to_buildings(out, nsi, max_dist_m=nsi_max_dist_m)
            joined_n = sum(1 for v in out.get('occtype', []) if pd.notna(v))
            print(f"  NSI joined: {joined_n}/{len(out)} buildings have HAZUS attributes")
    if use_fema:
        print(f"  fetching FEMA USA Structures polygons (paginated)...")
        fema = fetch_fema(extent_poly)
        print(f"  FEMA: {len(fema)} polygons returned")
        if len(fema):
            out = join_fema_to_buildings(out, fema)
            joined_n = sum(1 for v in out.get('fema_height', []) if pd.notna(v))
            print(f"  FEMA joined: {joined_n}/{len(out)} buildings have height/occupancy")
    # Coerce numeric columns (joins via .map() leave them as object dtype).
    NUM_COLS = ['sqft', 'num_story', 'med_yr_blt', 'val_struct', 'val_cont',
                'pop2amu65', 'pop2pmu65', 'found_ht']
    for c in NUM_COLS:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors='coerce')

    # Derived: building height estimate from num_story (3.5m per floor is the
    # commonly used average for mixed residential/commercial stock). NSI does
    # not give us a measured height, but num_story is well-populated.
    if 'num_story' in out.columns:
        out['height_m_est'] = out['num_story'] * 3.5

    derived_dir.mkdir(parents=True, exist_ok=True)
    out_path = derived_dir / 'buildings_attrs.geojson'
    out.to_file(out_path, driver='GeoJSON')
    print(f"  wrote {out_path}")

    # Also merge the new attribute columns into the viewer's canonical
    # buildings_enriched.geojson so the viewer (which reads that path via
    # build_ui_manifest.py) gains the attributes without any plumbing change.
    enriched_path = derived_dir / 'buildings_enriched.geojson'
    if enriched_path.exists():
        try:
            enriched = gpd.read_file(enriched_path).to_crs('EPSG:4326')
            # Spatial-join by building polygon overlap (out preserves source
            # order; enriched may have different ordering / different OBJECTID).
            # Use centroid-within-polygon to assign attributes.
            centroid = out.geometry.unary_union.centroid
            utm = _utm_epsg(centroid.x, centroid.y)
            out_m = out.to_crs(utm).reset_index(drop=True).copy()
            enr_m = enriched.to_crs(utm).reset_index(drop=True).copy()
            enr_m['_enr_idx'] = enr_m.index
            # Centroid of M3d output point-in-polygon of enriched
            out_m['_centroid'] = out_m.geometry.centroid
            cpts = gpd.GeoDataFrame(
                out_m.drop(columns='geometry').rename(columns={'_centroid': 'geometry'}),
                geometry='geometry', crs=utm,
            )
            joined = gpd.sjoin(cpts, enr_m[['_enr_idx', 'geometry']],
                               how='left', predicate='within')
            # Attach the M3d columns onto the enriched gdf
            # Include both NSI columns AND FEMA columns (prefixed fema_*).
            # join_fema_to_buildings adds fema_ prefixed columns to out_m;
            # include them so they flow into buildings_enriched.geojson too.
            fema_cols_lower = ['fema_' + c.lower() for c in FEMA_KEEP_COLS]
            ATTR_COLS = [c for c in NSI_KEEP_COLS + ['height_m_est'] + fema_cols_lower
                         if c in out_m.columns]
            # Deduplicate by _enr_idx (multiple M3d centroids can fall inside
            # the same enriched polygon; keep the first non-null per group).
            dedup = (joined.dropna(subset=['_enr_idx'])
                           .drop_duplicates(subset='_enr_idx', keep='first')
                           .set_index('_enr_idx'))
            for c in ATTR_COLS:
                if c in dedup.columns:
                    enriched[c] = enriched.index.map(dedup[c]).astype(object)
            # Re-coerce numerics on the merged file too. Include FEMA numeric
            # columns (they arrive from the ArcGIS layer as strings via the JSON
            # transport even when the schema says Single/Integer).
            FEMA_NUMERIC = ['fema_height', 'fema_sqmeters', 'fema_sqfeet',
                            'fema_pop_median', 'fema_h_adj_elev', 'fema_l_adj_elev']
            for c in NUM_COLS + ['height_m_est'] + FEMA_NUMERIC:
                if c in enriched.columns:
                    enriched[c] = pd.to_numeric(enriched[c], errors='coerce')
            enriched.to_file(enriched_path, driver='GeoJSON')
            nsi_attached = sum(1 for v in enriched.get('occtype', []) if pd.notna(v))
            fema_attached = sum(1 for v in enriched.get('fema_height', []) if pd.notna(v)) \
                if 'fema_height' in enriched.columns else 0
            print(f"  merged into {enriched_path.name}: "
                  f"{nsi_attached}/{len(enriched)} have NSI occtype, "
                  f"{fema_attached}/{len(enriched)} have FEMA height")
        except Exception as e:
            print(f"  WARN merging NSI attrs into buildings_enriched failed: {e}")
    summary = {
        'buildings_total': int(len(out)),
        'nsi_joined': int(sum(1 for v in out.get('occtype', []) if pd.notna(v))) if 'occtype' in out.columns else 0,
        'fema_joined': int(sum(1 for v in out.get('fema_height', []) if pd.notna(v))) if 'fema_height' in out.columns else 0,
        'nsi_max_dist_m': nsi_max_dist_m,
        'use_nsi': use_nsi,
        'use_fema': use_fema,
    }
    (derived_dir / 'buildings_attrs_summary.json').write_text(json.dumps(summary, indent=2))
    return out_path


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--derived', type=Path, required=True)
    p.add_argument('--no-nsi', dest='use_nsi', action='store_false')
    p.add_argument('--no-fema', dest='use_fema', action='store_false')
    p.add_argument('--nsi-max-dist-m', type=float, default=25.0)
    args = p.parse_args()
    process_sad(args.source, args.derived, use_fema=args.use_fema,
                use_nsi=args.use_nsi, nsi_max_dist_m=args.nsi_max_dist_m)


if __name__ == '__main__':
    main()