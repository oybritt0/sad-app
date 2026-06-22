# apply_convert_step.ps1
# Two changes so the gpkg->geojson conversion happens automatically and the
# draw-tool single-district flow "just works":
#   (1) convert_enriched_buildings.py: add --derived (one district) alongside
#       --data-dir (corpus). Either one; --derived wins if both given.
#   (2) batch_run_pipeline.py: add a per-SAD step "M5geo" right after M5 that
#       runs the converter for that district, so buildings_enriched.geojson is
#       always regenerated from the fresh .gpkg (no more stale viewer data).
#
# Safe pattern: timestamped backups, exact-string edits, verify, py-compile,
# auto-revert on any failure.

$ErrorActionPreference = 'Stop'
$code = "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\code"
$conv = Join-Path $code "convert_enriched_buildings.py"
$bpln = Join-Path $code "batch_run_pipeline.py"
foreach ($f in @($conv,$bpln)) { if (-not (Test-Path $f)) { throw "Not found: $f" } }

$stamp = Get-Date -Format yyyyMMdd_HHmmss
$cbak = "$conv.bak_convstep_$stamp"; Copy-Item $conv $cbak
$bbak = "$bpln.bak_convstep_$stamp"; Copy-Item $bpln $bbak
Write-Host "backups:`n  $cbak`n  $bbak"

# ---------- (1) converter: add --derived ----------
$csrc = Get-Content $conv -Raw

$cold = @'
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        print(f"ERROR: data dir not found: {data_dir}")
        sys.exit(1)

    gpkgs = sorted(data_dir.glob("*/derived/buildings_enriched.gpkg"))
'@

$cnew = @'
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", help="Convert every */derived/buildings_enriched.gpkg under this corpus dir.")
    ap.add_argument("--derived", help="Convert a single district's derived/buildings_enriched.gpkg (used by the pipeline + draw-tool flow).")
    args = ap.parse_args()

    # Single-district mode (preferred for the per-SAD pipeline and draw tool).
    if args.derived:
        derived = Path(args.derived)
        gpkg = derived / "buildings_enriched.gpkg"
        if not gpkg.exists():
            print(f"ERROR: no buildings_enriched.gpkg in {derived} (run M5 first).")
            sys.exit(1)
        try:
            msg = convert_one(gpkg)
            print(f"  + {derived.parts[-2]}: {msg}")
        except Exception as e:
            print(f"  ! {derived.parts[-2]}: FAILED - {e}")
            sys.exit(1)
        return

    if not args.data_dir:
        print("ERROR: pass --derived <district/derived> or --data-dir <corpus>.")
        sys.exit(1)

    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        print(f"ERROR: data dir not found: {data_dir}")
        sys.exit(1)

    gpkgs = sorted(data_dir.glob("*/derived/buildings_enriched.gpkg"))
'@

$n = ([regex]::Matches($csrc,[regex]::Escape($cold))).Count
if ($n -ne 1) { throw "converter: expected 1 match for main() head, found $n" }
$csrc = $csrc.Replace($cold,$cnew)
Set-Content -Path $conv -Value $csrc -Encoding UTF8
Write-Host "converter patched: --derived single-district mode added"

# ---------- (2) pipeline: insert M5geo step right after M5 ----------
$bsrc = Get-Content $bpln -Raw

# anchor: the end of the M5 dict block, just before Phase 5 comment.
$bold = @'
        'needs':  ['cv_metrics.json', 'program_summary.json'],
    },

    # ---- Phase 5: after M5 -------------------------------------------------
'@

$bnew = @'
        'needs':  ['cv_metrics.json', 'program_summary.json'],
    },

    # ---- Phase 4b: gpkg -> geojson for the viewer (auto post-M5) -----------
    # M5 writes buildings_enriched.gpkg; the web viewer can only read GeoJSON.
    # This converts the just-written gpkg to derived/buildings_enriched.geojson
    # so a building reprocess (or a freshly drawn district) never leaves the
    # viewer rendering stale geometry. Single-district mode via --derived.
    {
        'name':   'M5geo',
        'script': 'convert_enriched_buildings.py',
        'args':   ['--derived', '{derived}'],
        'marker': 'buildings_enriched.geojson',
        'needs':  ['buildings_enriched.gpkg'],
    },

    # ---- Phase 5: after M5 -------------------------------------------------
'@

$m = ([regex]::Matches($bsrc,[regex]::Escape($bold))).Count
if ($m -ne 1) { throw "pipeline: expected 1 match for M5/Phase5 anchor, found $m" }
$bsrc = $bsrc.Replace($bold,$bnew)
Set-Content -Path $bpln -Value $bsrc -Encoding UTF8
Write-Host "pipeline patched: M5geo conversion step inserted after M5"

# ---------- verify + compile ----------
$okc = (Get-Content $conv -Raw) -match 'args\.derived'
$okb = (Get-Content $bpln -Raw) -match "'name':   'M5geo'"
Write-Host ("converter --derived present: {0}" -f $okc)
Write-Host ("pipeline M5geo present:      {0}" -f $okb)
if (-not ($okc -and $okb)) { Copy-Item $cbak $conv -Force; Copy-Item $bbak $bpln -Force; throw "verify failed; reverted both" }

Push-Location $code
python -c "import py_compile; py_compile.compile(r'$conv', doraise=True); py_compile.compile(r'$bpln', doraise=True); print('py-compile OK (both)')"
if ($LASTEXITCODE -ne 0) { Pop-Location; Copy-Item $cbak $conv -Force; Copy-Item $bbak $bpln -Force; throw "py-compile failed; reverted both" }
Pop-Location

Write-Host ""
Write-Host "DONE. M5geo now runs automatically after M5 in the per-SAD phase."
Write-Host "Test on one district:"
Write-Host '  python batch_run_pipeline.py --data-dir <DATA> --stage per-sad --force --sads 32_District-Detroit_Detroit-MI --modules "M5,M5geo"'
