"""
module_overture_places.py

Queries Overture Maps **Places** for an arbitrary polygon, using the same engine
as the ROD Search Tool: DuckDB reading Overture's open parquet directly from S3
(spatial + httpfs extensions, anonymous us-west-2 access). No GUI, no API key —
this is the data mechanism behind the search tool, reused server-side so any
drawn boundary can get its POIs.

Returns a GeoJSON FeatureCollection of points, each carrying the Overture
primary category (the "typology" of the POI) plus name and confidence — the raw
material for POI mix / density / clustering comparisons and the on-demand POI
layer on drawn areas.

USAGE (standalone test)
    python module_overture_places.py --release 2025-01-22.0 --bbox -83.07 42.32 -83.03 42.35 --out pois.geojson
    python module_overture_places.py --geojson drawn.geojson --out pois.geojson

PROGRAMMATIC
    from module_overture_places import places_in_polygon
    fc = places_in_polygon(geometry_geojson, release=None, categories=None)

NETWORK (same as the ROD tool)
    stac.overturemaps.org           - latest release lookup
    s3://overturemaps-us-west-2/...  - the Places parquet (anonymous, open data)
    extensions.duckdb.org            - first-run DuckDB extension install
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

OVERTURE_S3 = "s3://overturemaps-us-west-2/release/{release}/theme=places/type=place/*"
STAC_CATALOG = "https://stac.overturemaps.org/catalog.json"
ROD_CONFIG = Path.home() / ".rod_search_tool" / "config.json"

# ── Rossetti program classification ──────────────────────────────────────────
# Mirrors module_03_rod_program_extractor.rollup_category EXACTLY (subcategory
# overrides + top-level fallback) so drawn-area POIs land in the same 8 buckets
# the viewer uses. Inlined rather than imported to avoid pulling in the pipeline
# package (module_03 imports shared.schemas). Keep in sync with module_03.
_PARKING_SUBCATS = {'parking', 'parking_garage', 'parking_lot'}
_RESIDENTIAL_SUBCATS = {'condominium', 'apartment', 'housing_authority',
                        'residential_building', 'service_apartment'}
_HOTEL_SUBCATS = {'hotel', 'motel', 'inn', 'lodge', 'resort', 'lodging', 'bed_and_breakfast'}
_SPORT_SUBCATS = {
    'stadium_arena', 'baseball_stadium', 'hockey_arena', 'basketball_stadium',
    'football_stadium', 'sports_complex', 'baseball_field', 'soccer_field',
    'race_track', 'golf_course', 'tennis_court', 'swimming_pool', 'gym',
    'fitness_center', 'sport_or_fitness_facility', 'sport_or_recreation_club',
    'yoga_studio', 'fitness_trainer', 'martial_arts_club', 'dance_studio',
    'boot_camp', 'bowling_alley', 'ice_skating_rink', 'skate_park',
    'rock_climbing_spot', 'mountain_bike_trail', 'professional_sport_team',
    'amateur_sport_league', 'sports_clubs_and_leagues'}
_OPEN_SPACE_SUBCATS = {'park', 'dog_park', 'public_plaza', 'public_fountain',
                       'community_center', 'public_space'}
_TOP_LEVEL_TO_ROSSETTI = {
    'food_and_drink': 'retail_food_entertainment', 'shopping': 'retail_food_entertainment',
    'arts_and_entertainment': 'retail_food_entertainment', 'lodging': 'hotel',
    'services_and_business': 'office', 'health_care': 'office',
    'sports_and_recreation': 'sport', 'community_and_government': 'other',
    'travel_and_transportation': 'other', 'cultural_and_historic': 'other',
    'education': 'other', 'lifestyle_services': 'other', 'geographic_entities': 'other'}


def _infer_top_level(pc: str) -> str | None:
    """Overture leaf -> top-level family, by keyword/suffix. The ROD tool reads
    this from Overture's taxonomy_hierarchy; querying the parquet we only get the
    leaf, so we infer. sport/parking/hotel/residential/open_space do NOT rely on
    this (matched on the leaf exactly in classify); this resolves the rest.
    Order matters — more specific families are tested before the broad
    services_and_business catch-all."""
    pc = (pc or '').lower()
    def has(*ks): return any(k in pc for k in ks)
    # food & drink
    if has('restaurant', 'cafe', 'coffee', 'bar', 'pub', 'brewery', 'bakery', 'food',
           'diner', 'eatery', 'pizz', 'steakhouse', 'winery', 'ice_cream', 'deli',
           'smoothie', 'juice_bar', 'grill', 'bistro', 'brunch', 'tea_', 'donut', 'dessert'):
        return 'food_and_drink'
    # health care (before shopping so 'pharmacy'/'medical_spa' land here)
    if has('hospital', 'clinic', 'doctor', 'dentist', 'dental', 'medical', 'urgent_care',
           'physician', 'veterinar', 'optometr', 'chiropract', 'pharmacy', 'health_'):
        return 'health_care'
    # shopping / retail
    if has('store', 'shop', 'market', 'retail', 'boutique', 'mall', 'grocery',
           'supermarket', 'dealer', 'dealership', 'supply', 'outlet', 'pawn'):
        return 'shopping'
    # arts & entertainment
    if has('theater', 'theatre', 'cinema', 'museum', 'gallery', 'nightclub', 'casino',
           'arcade', 'entertainment', 'music_venue', 'concert', 'amusement', 'art_'):
        return 'arts_and_entertainment'
    # education
    if has('school', 'university', 'college', 'education', 'library', 'academy',
           'kindergarten', 'tutoring', 'vocational'):
        return 'education'
    # sports & recreation ('sport' guarded against 'passport')
    if has('gym', 'fitness', 'sports', 'stadium', 'arena', 'recreation', 'athletic',
           'martial', 'climbing') or ('sport' in pc and 'passport' not in pc):
        return 'sports_and_recreation'
    # travel & transportation
    if has('station', 'transit', 'transport', 'airport', 'terminal', 'taxi',
           'car_rental', 'ferry', 'subway', 'railway'):
        return 'travel_and_transportation'
    # services & business (broad catch — many '*_services', trades, professional)
    if has('service', 'agency', 'agencies', 'law', 'attorney', 'legal', 'consult',
           'accounting', 'insurance', 'bank', 'financial', 'finance', 'real_estate',
           'realtor', 'office', 'coworking', 'telecommunication', 'roofing', 'gutter',
           'plumb', 'electric', 'contractor', 'repair', 'automotive', 'auto_', 'marketing',
           'advertis', 'notary', 'passport', 'staffing', 'employment', 'printing',
           'logistics', 'salon', 'barber', 'spa', 'tech', 'software', 'it_'):
        return 'services_and_business'
    return None


def classify(primary_category: str) -> str:
    """Overture leaf category -> Rossetti 8-bucket program (matches the viewer)."""
    pc = (primary_category or '').lower().strip()
    if pc in _PARKING_SUBCATS: return 'parking'
    if pc in _RESIDENTIAL_SUBCATS: return 'residential'
    if pc in _HOTEL_SUBCATS: return 'hotel'
    if pc in _SPORT_SUBCATS: return 'sport'
    if pc in _OPEN_SPACE_SUBCATS: return 'open_space'
    return _TOP_LEVEL_TO_ROSSETTI.get(_infer_top_level(pc) or '', 'other')


# ── release resolution ────────────────────────────────────────────────────────
def _release_from_rod_config() -> str | None:
    """Reuse the ROD Search Tool's optional release override if the user set one."""
    try:
        cfg = json.loads(ROD_CONFIG.read_text(encoding="utf-8"))
        rel = cfg.get("overture_release") or cfg.get("release_override") or cfg.get("release")
        return rel or None
    except Exception:
        return None


def latest_release(timeout: int = 20) -> str:
    """Resolve the newest Overture release id (e.g. '2025-01-22.0').

    Prefers the ROD tool's override, else parses the STAC catalog, picking the
    lexicographically greatest release-looking id (they sort chronologically).
    """
    import re
    import requests
    override = _release_from_rod_config()
    if override:
        return override
    rel_pat = re.compile(r"\d{4}-\d{2}-\d{2}(?:\.\d+)?")
    found = set()
    try:
        cat = requests.get(STAC_CATALOG, timeout=timeout).json()
        # walk links / ids for release-shaped strings
        blobs = [json.dumps(cat)]
        for link in cat.get("links", []):
            blobs.append(json.dumps(link))
        for b in blobs:
            found.update(rel_pat.findall(b))
    except Exception as e:
        raise RuntimeError(
            f"Couldn't resolve the latest Overture release ({e}). "
            f"Pass --release explicitly (the ROD Search Tool shows the current release)."
        )
    if not found:
        raise RuntimeError("No release id found in the Overture STAC catalog; pass --release.")
    return sorted(found)[-1]


# ── DuckDB connection ──────────────────────────────────────────────────────────
def _connect(source: str):
    import duckdb
    con = duckdb.connect()
    # httpfs is only needed to read from S3; local parquet needs no extension.
    if str(source).startswith("s3://"):
        con.execute("INSTALL httpfs; LOAD httpfs;")
        con.execute("SET s3_region='us-west-2';")
    return con


# ── core query ─────────────────────────────────────────────────────────────────
def places_in_polygon(geometry: dict, release: str | None = None,
                      categories: list[str] | None = None,
                      source: str | None = None, min_confidence: float = 0.0) -> dict:
    """Return a GeoJSON FeatureCollection of Overture places inside `geometry`.

    `geometry` is a GeoJSON Polygon/MultiPolygon dict. `source` overrides the S3
    glob (used by tests with a local parquet). `categories` optionally filters to
    Overture primary categories.
    """
    from shapely.geometry import shape
    poly = shape(geometry)
    if poly.is_empty:
        return {"type": "FeatureCollection", "features": []}
    minx, miny, maxx, maxy = poly.bounds
    poly_wkt = poly.wkt

    if source is None:
        release = release or latest_release()
        source = OVERTURE_S3.format(release=release)

    from shapely.wkb import loads as wkb_loads
    con = _connect(source)
    cat_filter = ""
    params: list = []
    if categories:
        placeholders = ",".join(["?"] * len(categories))
        cat_filter = f"AND categories.primary IN ({placeholders})"   # real column, not the SELECT alias
        params = list(categories)

    # bbox prefilter in SQL (struct-field comparison, no spatial extension);
    # precise polygon containment done in Python via shapely.
    sql = f"""
      SELECT id,
             names.primary      AS name,
             categories.primary  AS category,
             categories.alternate AS alt,
             confidence,
             geometry
      FROM read_parquet('{source}', hive_partitioning=1)
      WHERE bbox.xmin <= {maxx} AND bbox.xmax >= {minx}
        AND bbox.ymin <= {maxy} AND bbox.ymax >= {miny}
        AND confidence >= {float(min_confidence)}
        {cat_filter}
    """
    rows = con.execute(sql, params).fetchall()
    cols = [d[0] for d in con.description]
    feats = []
    for r in rows:
        rec = dict(zip(cols, r))
        wkb = rec.get("geometry")
        if wkb is None:
            continue
        try:
            geom = wkb_loads(bytes(wkb))
        except Exception:
            continue
        pt = geom if geom.geom_type == "Point" else geom.representative_point()
        if not poly.covers(pt):
            continue
        prog = classify(rec.get("category"))
        if prog == 'other':                       # try alternate categories
            alts = rec.get("alt") or []
            try:
                for a in alts:
                    p2 = classify(a)
                    if p2 != 'other':
                        prog = p2; break
            except TypeError:
                pass
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [pt.x, pt.y]},
            "properties": {"id": rec["id"], "name": rec["name"],
                           "category": rec["category"],
                           "rossetti_category": prog,
                           "confidence": round(rec["confidence"], 3) if rec["confidence"] is not None else None},
        })
    con.close()
    # diagnostic: what came back (shows in the server terminal)
    from collections import Counter
    prog_counts = Counter(f["properties"]["rossetti_category"] for f in feats)
    raw_sample = list({f["properties"]["category"] for f in feats if f["properties"]["category"]})[:12]
    print(f"  POIs: {len(feats)} | programs: {dict(prog_counts)}")
    print(f"  sample raw categories: {raw_sample}")
    return {"type": "FeatureCollection", "features": feats}


def category_summary(fc: dict) -> dict:
    """Aggregate POIs into category counts — the basis for POI-mix comparisons."""
    counts: dict[str, int] = {}
    for f in fc.get("features", []):
        c = f["properties"].get("category") or "uncategorized"
        counts[c] = counts.get(c, 0) + 1
    return {"total": sum(counts.values()),
            "category_counts": dict(sorted(counts.items(), key=lambda kv: -kv[1]))}


# ── CLI ─────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Query Overture Places for a polygon (ROD-tool engine)")
    ap.add_argument("--release", default=None, help="Overture release id (else auto-resolved)")
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("MINLON", "MINLAT", "MAXLON", "MAXLAT"))
    ap.add_argument("--geojson", type=Path, help="GeoJSON file with a polygon geometry")
    ap.add_argument("--categories", nargs="*", default=None)
    ap.add_argument("--out", type=Path, default=Path("overture_places.geojson"))
    args = ap.parse_args()

    if args.geojson:
        gj = json.loads(args.geojson.read_text())
        geom = gj["features"][0]["geometry"] if gj.get("type") == "FeatureCollection" else gj.get("geometry", gj)
    elif args.bbox:
        minlon, minlat, maxlon, maxlat = args.bbox
        geom = {"type": "Polygon", "coordinates": [[
            [minlon, minlat], [maxlon, minlat], [maxlon, maxlat], [minlon, maxlat], [minlon, minlat]]]}
    else:
        sys.exit("Provide --bbox or --geojson.")

    fc = places_in_polygon(geom, release=args.release, categories=args.categories)
    args.out.write_text(json.dumps(fc))
    summ = category_summary(fc)
    print(f"  {summ['total']} places -> {args.out}")
    for cat, n in list(summ["category_counts"].items())[:12]:
        print(f"    {n:4d}  {cat}")


if __name__ == "__main__":
    main()
