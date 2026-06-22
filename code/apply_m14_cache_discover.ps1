# apply_m14_cache_discover.ps1
# Make module_14's --discover path reuse the LOCAL gtfs cache instead of
# re-downloading. discover_gtfs_feeds() returns catalog URLs whose tail doesn't
# match the descriptively-named cached zips, so _open_zip re-downloads every feed
# (~30 min/district). This adds a cache-first resolver: index data/_gtfs_cache by
# MDB id, fetch the catalog for id+bbox, pick cached zips overlapping the SAD bbox
# (skip nationwide feeds via a span cap), use local paths. Falls back to URL
# download only for feeds not in cache. Makes drawn districts fast via the
# auto-pipeline (sad_match_server -> batch_run_pipeline -> M14).
#
# Safe pattern: backup, exact-string edit, verify, py-compile, auto-revert.

$ErrorActionPreference = 'Stop'
$code = "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\code"
$m14  = Join-Path $code "module_14_transit_routes.py"
if (-not (Test-Path $m14)) { throw "Not found: $m14" }

$bak = "$m14.bak_cachediscover_$(Get-Date -Format yyyyMMdd_HHmmss)"
Copy-Item $m14 $bak
Write-Host "backup: $bak"

$src = Get-Content $m14 -Raw

# 1) Add a cache-resolver helper just before def process_sad.
$anchor = "def process_sad(derived_dir, source_dir, gtfs_sources, discover, catalog_url,"
if (([regex]::Matches($src,[regex]::Escape($anchor))).Count -ne 1) { throw "process_sad signature not found uniquely" }

$helper = @'
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


'@
$src = $src.Replace($anchor, $helper + $anchor)

# 2) In process_sad, use the cache resolver before falling back to URL discovery.
$old = @'
    feed_names = [Path(s).stem for s in gtfs_sources]
    if discover and not gtfs_sources:
        gtfs_sources = los.discover_gtfs_feeds(manifest.bbox_geo, catalog_url)
        feed_names = [Path(s).stem for s in gtfs_sources]
'@
$new = @'
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
'@
if (([regex]::Matches($src,[regex]::Escape($old))).Count -ne 1) { throw "discover block not found uniquely" }
$src = $src.Replace($old,$new)

# 3) ensure 're' is imported (module uses regex now)
if ($src -notmatch "(?m)^import re\b") {
    # add after the first 'import sys' or at top of imports
    if ($src -match "(?m)^import sys\b") {
        $src = $src -replace "(?m)^(import sys\b.*)$", "`$1`r`nimport re"
    } else {
        $src = "import re`r`n" + $src
    }
}

Set-Content -Path $m14 -Value $src -Encoding UTF8
Write-Host "patched: cache-first discover resolution added"

# verify + compile
$chk = Get-Content $m14 -Raw
$okHelper = $chk -match "def resolve_cached_feeds\("
$okUse    = $chk -match "resolved \{len\(cached\)\} feed"
$okRe     = $chk -match "(?m)^import re\b"
Write-Host ("resolver present: {0}" -f $okHelper)
Write-Host ("wired into discover: {0}" -f $okUse)
Write-Host ("re imported: {0}" -f $okRe)
if (-not ($okHelper -and $okUse -and $okRe)) { Copy-Item $bak $m14 -Force; throw "verify failed; reverted" }
Push-Location $code
python -c "import py_compile; py_compile.compile(r'$m14', doraise=True); print('py-compile OK')"
if ($LASTEXITCODE -ne 0) { Pop-Location; Copy-Item $bak $m14 -Force; throw "py-compile failed; reverted" }
Pop-Location
Write-Host ""
Write-Host "DONE. Test --discover now uses cache (should be ~1-2 min, not 37):"
Write-Host '  python module_14_transit_routes.py --derived <D>\32_..\derived --source <D>\32_..\source --discover'
