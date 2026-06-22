"""
module_22_environment.py

Environmental / ecological profile for a SAD: surface heat, greenness, and
impervious cover. The "health of the city" layer, and the most design-
actionable of the free set — it makes the sea-of-parking heat-island problem
around stadiums legible in one image.

WHAT IT MEASURES
  - mean_summer_lst_c        land-surface temperature inside the SAD (Landsat)
  - lst_vs_surroundings_c    SAD mean minus canvas mean  (the heat-island delta:
                             positive = the district runs hotter than its context)
  - lst_p90_c / lst_max_c    hot-spot intensity
  - mean_ndvi                vegetation greenness inside the SAD (-1..1)
  - impervious_pct           paved/built share inside the SAD (NLCD; US only)

DATA SOURCE  (free, no key, NOT Earth Engine)
  Microsoft Planetary Computer STAC:
    landsat-c2-l2   surface temperature (lwir11) + red/nir for NDVI
    nlcd            impervious surface (US only; skipped gracefully elsewhere)
  All raster data is public; PC signs asset URLs at read time.

DEPENDENCIES
  pip install pystac-client planetary-computer rioxarray rasterio

USAGE
  python module_22_environment.py ^
      --derived ..\\data\\32_District-Detroit_Detroit-MI\\derived ^
      --source  ..\\data\\32_District-Detroit_Detroit-MI\\source

OUTPUTS
  derived/<sad>/environment/environment_summary.json
  derived/<sad>/environment/heat_island.png
  source/<sad>/lst_celsius.tif         (clipped LST raster, for QGIS)
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import geopandas as gpd

sys.path.insert(0, str(Path(__file__).parent))
from shared.schemas import Manifest
import canvas_render as cr


# ─── Config ──────────────────────────────────────────────────────────────────

PEAK_SUMMER = (6, 7, 8)             # representative warm-season heat
WARM_FALLBACK = (5, 6, 7, 8, 9)     # used only if no clean peak-summer scene
LOOKBACK_YEARS = 3                  # search window for a clear summer scene
MAX_CLOUD = 20                      # percent
LANDSAT_COLLECTION = 'landsat-c2-l2'

# Landsat Collection-2 Level-2 scaling (USGS):
#   Surface temperature (Kelvin) = DN * 0.00341802 + 149.0
#   Surface reflectance          = DN * 0.0000275  - 0.2
ST_SCALE, ST_OFFSET = 0.00341802, 149.0
SR_SCALE, SR_OFFSET = 0.0000275, -0.2


# ─── Pure helpers (testable without network) ─────────────────────────────────

def landsat_st_to_celsius(dn: np.ndarray) -> np.ndarray:
    """Landsat C2L2 thermal DN -> degrees Celsius. Zero/fill -> NaN."""
    dn = dn.astype('float32')
    dn[dn <= 0] = np.nan
    return dn * ST_SCALE + ST_OFFSET - 273.15


def landsat_sr(dn: np.ndarray) -> np.ndarray:
    dn = dn.astype('float32')
    dn[dn <= 0] = np.nan
    return dn * SR_SCALE + SR_OFFSET


def compute_ndvi(red: np.ndarray, nir: np.ndarray) -> np.ndarray:
    red, nir = landsat_sr(red), landsat_sr(nir)
    denom = nir + red
    with np.errstate(invalid='ignore', divide='ignore'):
        ndvi = (nir - red) / denom
    ndvi[~np.isfinite(ndvi)] = np.nan
    return np.clip(ndvi, -1, 1)


def zonal_stats(data: np.ndarray, transform, raster_crs, polygon_metric,
                stats=('mean', 'median', 'p90', 'max')) -> dict:
    """Masked statistics of `data` within `polygon_metric` (already in the
    raster CRS). Uses a rasterized boolean mask."""
    from rasterio.features import geometry_mask
    mask = geometry_mask([polygon_metric], out_shape=data.shape,
                         transform=transform, invert=True)
    vals = data[mask & np.isfinite(data)]
    out = {'count': int(vals.size)}
    if vals.size == 0:
        return {**out, **{s: None for s in stats}}
    for s in stats:
        if s == 'mean':
            out['mean'] = float(np.mean(vals))
        elif s == 'median':
            out['median'] = float(np.median(vals))
        elif s == 'p90':
            out['p90'] = float(np.percentile(vals, 90))
        elif s == 'max':
            out['max'] = float(np.max(vals))
    return out


# ─── Planetary Computer fetch (network) ──────────────────────────────────────

def categorical_fraction(data: np.ndarray, transform, raster_crs, polygon_metric,
                         value: int) -> float | None:
    """Percent of valid pixels equal to `value` within `polygon_metric`."""
    from rasterio.features import geometry_mask
    mask = geometry_mask([polygon_metric], out_shape=data.shape,
                         transform=transform, invert=True)
    vals = data[mask & np.isfinite(data)]
    if vals.size == 0:
        return None
    return round(100 * float(np.mean(vals == value)), 1)


def _pc_client():
    try:
        import pystac_client
        import planetary_computer as pc
    except ImportError:
        sys.exit("Module 22 needs: pip install pystac-client planetary-computer "
                 "rioxarray rasterio")
    cat = pystac_client.Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=pc.sign_inplace)
    return cat


def _summer_datetime_range() -> str:
    import datetime as dt
    end = dt.date.today()
    start = dt.date(end.year - LOOKBACK_YEARS, 1, 1)
    return f"{start.isoformat()}/{end.isoformat()}"


def fetch_landsat(bbox_geo, metric_crs, canvas_bbox_m):
    """Return (lst_c, ndvi, transform) reprojected to metric_crs and clipped to
    the canvas bbox, from the least-cloudy summer Landsat scene over the SAD."""
    import rioxarray  # noqa: F401  (registers .rio accessor)
    import xarray as xr

    cat = _pc_client()
    search = cat.search(collections=[LANDSAT_COLLECTION],
                        bbox=list(bbox_geo),
                        datetime=_summer_datetime_range(),
                        query={'eo:cloud_cover': {'lt': MAX_CLOUD},
                               'platform': {'in': ['landsat-8', 'landsat-9']}})
    items = list(search.items())
    peak = [it for it in items if int(it.datetime.month) in PEAK_SUMMER]
    warm = [it for it in items if int(it.datetime.month) in WARM_FALLBACK]
    items = peak or warm or items
    if not items:
        raise RuntimeError("no suitable Landsat scene found over the SAD")
    item = min(items, key=lambda it: it.properties.get('eo:cloud_cover', 100))
    print(f"  Landsat scene {item.id} "
          f"({item.datetime.date()}, cloud {item.properties.get('eo:cloud_cover')}%)")

    def band(asset_key):
        import rioxarray
        da = rioxarray.open_rasterio(item.assets[asset_key].href,
                                     masked=True).squeeze()
        da = da.rio.reproject(metric_crs)
        return da.rio.clip_box(*canvas_bbox_m)

    st = band('lwir11')
    red, nir = band('red'), band('nir08')
    # align red/nir to st grid for NDVI
    red = red.rio.reproject_match(st)
    nir = nir.rio.reproject_match(st)

    lst_c = landsat_st_to_celsius(st.values.astype('float32'))
    ndvi = compute_ndvi(red.values.astype('float32'), nir.values.astype('float32'))
    transform = st.rio.transform()
    return lst_c, ndvi, transform, st


def fetch_worldcover_builtup(bbox_geo, metric_crs, sad_poly_metric):
    """Built-up surface fraction (%) within the SAD from ESA WorldCover (10 m,
    global; class 50 = built-up). A robust impervious proxy that also works
    outside the US (e.g. Canadian SADs). None on failure."""
    try:
        import rioxarray
        cat = _pc_client()
        items = list(cat.search(collections=['esa-worldcover'],
                                bbox=list(bbox_geo)).items())
        if not items:
            return None
        item = max(items, key=lambda it: it.datetime.year if it.datetime else 0)
        key = 'map' if 'map' in item.assets else next(iter(item.assets))
        da = rioxarray.open_rasterio(item.assets[key].href, masked=True).squeeze()
        da = da.rio.reproject(metric_crs)
        return categorical_fraction(da.values, da.rio.transform(), metric_crs,
                                    sad_poly_metric, value=50)
    except Exception as e:
        print(f"  WARN: WorldCover built-up unavailable ({e})")
        return None



# ─── Render ──────────────────────────────────────────────────────────────────

def export_heat_grid(lst_c, transform, metric_crs, canvas_geom_m, out_path,
                     downsample=3):
    """Vectorize the LST raster into a coarse polygon grid (one cell per
    downsample x downsample block) carrying mean lst_c, reprojected to
    EPSG:4326 and clipped to the canvas. Gives the viewer/map a recolorable
    heat field as polygons rather than a baked image."""
    from shapely.geometry import box as shp_box
    H, W = lst_c.shape
    polys, vals = [], []
    for i in range(0, H, downsample):
        for j in range(0, W, downsample):
            block = lst_c[i:i + downsample, j:j + downsample]
            finite = block[np.isfinite(block)]
            if finite.size == 0:
                continue
            x0, y0 = transform * (j, i)
            x1, y1 = transform * (j + downsample, i + downsample)
            polys.append(shp_box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
            vals.append(round(float(finite.mean()), 1))
    if not polys:
        return None
    g = gpd.GeoDataFrame({'lst_c': vals}, geometry=polys, crs=metric_crs)
    try:
        g = gpd.clip(g, canvas_geom_m)
        g = g[~g.geometry.is_empty & g.geometry.notna()]
    except Exception:
        pass
    g.to_crs('EPSG:4326').to_file(out_path, driver='GeoJSON')
    return out_path


def render_heat(lst_c, extent_xyxy, ctx, out_png, summary):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    minx, miny, maxx, maxy = ctx['canvas_bbox']
    fig, ax = plt.subplots(figsize=(11, 11), dpi=130)
    fig.patch.set_facecolor(cr.BG_COLOR); ax.set_facecolor(cr.BG_COLOR)

    finite = lst_c[np.isfinite(lst_c)]
    vmin = float(np.percentile(finite, 2)) if finite.size else 0
    vmax = float(np.percentile(finite, 98)) if finite.size else 1
    im = ax.imshow(lst_c, extent=extent_xyxy, origin='upper', cmap='inferno',
                   vmin=vmin, vmax=vmax, zorder=2, interpolation='nearest')

    for key, color, lw in (('streets_inside', cr.STREET_INSIDE, 0.5),):
        g = ctx.get(key)
        if g is not None and not g.empty:
            g.plot(ax=ax, color=color, linewidth=lw, alpha=0.4, zorder=4)
    gpd.GeoSeries([ctx['sad_geom_m']]).boundary.plot(
        ax=ax, color='#ffffff', linewidth=2.4, linestyle=(0, (10, 6)), zorder=8)

    cr.draw_scale_bar_mpl(ax, ctx['canvas_bbox'])
    cr.draw_north_arrow_mpl(ax, ctx['canvas_bbox'])
    ax.set_xlim(minx, maxx); ax.set_ylim(miny, maxy)
    ax.set_aspect('equal'); ax.set_xticks([]); ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_visible(False)

    chip = dict(boxstyle='square,pad=0.5', facecolor=cr.BG_COLOR, edgecolor='none')
    ax.text(0.015, 0.985, 'Surface Heat', transform=ax.transAxes, color='#ffffff',
            fontsize=15, fontweight='bold', va='top', ha='left',
            family='sans-serif', bbox=chip, zorder=12)
    sub = f"Summer land-surface temperature \u00b7 Landsat"
    if summary.get('lst_vs_surroundings_c') is not None:
        d = summary['lst_vs_surroundings_c']
        sub += (f"  \u00b7  district {'+' if d >= 0 else ''}{d}\u00b0C vs surroundings")
    ax.text(0.015, 0.925, sub, transform=ax.transAxes, color=cr.TEXT_DIM,
            fontsize=10, va='top', ha='left', family='sans-serif', bbox=chip, zorder=12)

    # Manual gradient legend (avoids colorbar/equal-aspect layout collapse)
    n = 24
    lx, ly, lw_, lh = 0.015, 0.86, 0.20, 0.014
    for i in range(n):
        ax.add_patch(plt.Rectangle((lx + i / n * lw_, ly), lw_ / n, lh,
                     transform=ax.transAxes, facecolor=plt.cm.inferno(i / (n - 1)),
                     edgecolor='none', zorder=12))
    ax.text(lx, ly + 0.024, '\u00b0C surface temp', transform=ax.transAxes,
            color=cr.TEXT_DIM, fontsize=9, va='bottom', ha='left',
            family='sans-serif', bbox=chip, zorder=12)
    ax.text(lx, ly - 0.012, f'{vmin:.0f}', transform=ax.transAxes, color=cr.TEXT_DIM,
            fontsize=8, va='top', ha='left', family='sans-serif', zorder=12)
    ax.text(lx + lw_, ly - 0.012, f'{vmax:.0f}', transform=ax.transAxes,
            color=cr.TEXT_DIM, fontsize=8, va='top', ha='right',
            family='sans-serif', zorder=12)

    fig.savefig(out_png, dpi=130, bbox_inches='tight',
                facecolor=cr.BG_COLOR, pad_inches=0.1)
    plt.close(fig)


# ─── Orchestration ───────────────────────────────────────────────────────────

def process_sad(derived_dir: Path, source_dir: Path) -> Path:
    manifest_path = derived_dir / 'manifest.json'
    if not manifest_path.exists():
        sys.exit(f"manifest.json not found at {manifest_path}. Run Module 1 first.")
    manifest = Manifest.model_validate_json(manifest_path.read_text())

    ctx = cr.load_canvas_context(source_dir)
    metric_crs = ctx['metric_crs']
    sad_poly_m = ctx['sad_geom_m']
    canvas_bbox_m = ctx['canvas_bbox']

    print(f"Environmental profile for {manifest.sad_id}...")
    lst_c, ndvi, transform, st_da = fetch_landsat(
        manifest.bbox_geo, metric_crs, canvas_bbox_m)

    sad_stats = zonal_stats(lst_c, transform, metric_crs, sad_poly_m)
    # Surroundings = canvas pixels OUTSIDE the SAD (excluding the district itself,
    # so the differential isn't diluted by the SAD's own pixels).
    from rasterio.features import geometry_mask
    sad_mask = geometry_mask([sad_poly_m], out_shape=lst_c.shape,
                             transform=transform, invert=True)
    outside = lst_c[(~sad_mask) & np.isfinite(lst_c)]
    surroundings_mean = float(np.mean(outside)) if outside.size else None
    ndvi_stats = zonal_stats(ndvi, transform, metric_crs, sad_poly_m, stats=('mean',))
    built_up = fetch_worldcover_builtup(manifest.bbox_geo, metric_crs, sad_poly_m)

    delta = (round(sad_stats['mean'] - surroundings_mean, 1)
             if sad_stats['mean'] is not None and surroundings_mean is not None
             else None)

    summary = {
        'sad_id': manifest.sad_id, 'sad_name': manifest.sad_name,
        'source': 'Landsat C2L2 + ESA WorldCover (Planetary Computer)',
        'mean_summer_lst_c': round(sad_stats['mean'], 1) if sad_stats['mean'] else None,
        'surroundings_lst_c': round(surroundings_mean, 1) if surroundings_mean else None,
        'lst_vs_surroundings_c': delta,
        'lst_p90_c': round(sad_stats['p90'], 1) if sad_stats['p90'] else None,
        'lst_max_c': round(sad_stats['max'], 1) if sad_stats['max'] else None,
        'mean_ndvi': round(ndvi_stats['mean'], 3) if ndvi_stats['mean'] else None,
        'built_up_pct': built_up,
        'pixels_in_sad': sad_stats['count'],
    }

    # Outputs
    env_dir = derived_dir / 'environment'
    env_dir.mkdir(parents=True, exist_ok=True)
    (env_dir / 'environment_summary.json').write_text(json.dumps(summary, indent=2))
    try:
        st_da.rio.to_raster(source_dir / 'lst_celsius.tif')
    except Exception as e:
        print(f"  WARN: could not write LST GeoTIFF ({e})")
    try:
        render_heat(lst_c, (canvas_bbox_m[0], canvas_bbox_m[2],
                            canvas_bbox_m[1], canvas_bbox_m[3]),
                    ctx, env_dir / 'heat_island.png', summary)
    except Exception as e:
        print(f"  WARN: render failed ({e}); data still written")
    try:
        hg = export_heat_grid(lst_c, transform, metric_crs,
                              ctx['canvas_geom_m'], env_dir / 'heat_grid.geojson')
        if hg:
            print(f"  wrote {hg}")
    except Exception as e:
        print(f"  WARN: heat grid export failed ({e})")

    print(f"\n[OK] {manifest.sad_id}")
    print(f"  mean summer LST: {summary['mean_summer_lst_c']}\u00b0C "
          f"(p90 {summary['lst_p90_c']}, max {summary['lst_max_c']})")
    if delta is not None:
        hotter = 'hotter' if delta >= 0 else 'cooler'
        print(f"  heat island: {abs(delta)}\u00b0C {hotter} than surroundings "
              f"(district {summary['mean_summer_lst_c']} vs "
              f"{summary['surroundings_lst_c']}\u00b0C around it)")
    print(f"  mean NDVI (greenness): {summary['mean_ndvi']}")
    if built_up is not None:
        print(f"  built-up surface (WorldCover): {built_up}%")
    else:
        print("  surface-cover layer unavailable for this location")
    print(f"\n  wrote {env_dir / 'environment_summary.json'}")
    return env_dir / 'environment_summary.json'


def main():
    p = argparse.ArgumentParser(description="Environmental profile for a SAD")
    p.add_argument('--derived', type=Path, required=True)
    p.add_argument('--source', type=Path, required=True)
    args = p.parse_args()
    process_sad(args.derived, args.source)


if __name__ == '__main__':
    main()
