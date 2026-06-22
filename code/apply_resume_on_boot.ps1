# apply_resume_on_boot.ps1
# Add boot-time reclaim to make_app: when SAD_RESUME_ON_BOOT is set (Render),
# on startup scan for districts whose integration status is 'queued' or
# 'running' (i.e. interrupted by a restart) and resume them via _integrate_async.
# This gives the single-service deploy the robustness a separate worker would,
# without needing a shared disk. No effect locally (env var unset).
# Safe: backup, exact edit, verify, py-compile, auto-revert.

$ErrorActionPreference = 'Stop'
$code = "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\code"
$ms   = Join-Path $code "sad_match_server.py"
if (-not (Test-Path $ms)) { throw "Not found: $ms" }

$bak = "$ms.bak_resumeboot_$(Get-Date -Format yyyyMMdd_HHmmss)"
Copy-Item $ms $bak
Write-Host "backup: $bak"

$src = Get-Content $ms -Raw

# 1) Add a resume helper just before make_app.
$anchor = "def make_app(data_dir: Path, api_key: str, year: int) -> `"Flask`":"
if (([regex]::Matches($src,[regex]::Escape($anchor))).Count -ne 1) { throw "make_app signature not found uniquely" }

$helper = @'
def _resume_interrupted(data_dir: Path):
    """On startup (Render), resume any district whose integration was cut off by
    a restart -- status still 'queued' or 'running'. Re-runs the chain in a
    thread so a redeploy mid-draw doesn't strand a district. Gated on
    SAD_RESUME_ON_BOOT so it never fires during local use."""
    import os as _os
    if not _os.environ.get('SAD_RESUME_ON_BOOT'):
        return
    try:
        for sd in sorted(data_dir.iterdir()):
            if not sd.is_dir():
                continue
            p = _integration_status_path(data_dir, sd.name)
            if not p.exists():
                continue
            try:
                st = json.loads(p.read_text())
            except Exception:
                continue
            if st.get('state') in ('queued', 'running'):
                print(f"  [resume] {sd.name} was '{st.get('state')}' -> re-integrating")
                _write_status(data_dir, sd.name, state='queued', step='queued (resumed)')
                _integrate_async(data_dir, sd.name)
    except Exception as e:
        print(f"  [resume] scan failed: {e}")


'@
$src = $src.Replace($anchor, $helper + $anchor)

# 2) Call it inside make_app, right after corpus is loaded.
$old = @'
    app = Flask(__name__)
    corpus = Corpus(data_dir)
'@
$new = @'
    app = Flask(__name__)
    corpus = Corpus(data_dir)
    _resume_interrupted(data_dir)
'@
if (([regex]::Matches($src,[regex]::Escape($old))).Count -ne 1) { throw "make_app body anchor not found uniquely" }
$src = $src.Replace($old,$new)

Set-Content -Path $ms -Value $src -Encoding UTF8
Write-Host "patched: boot-time resume added"

$chk = Get-Content $ms -Raw
$okHelper = $chk -match "def _resume_interrupted\("
$okCall   = $chk -match "_resume_interrupted\(data_dir\)"
Write-Host ("resume helper present: {0}" -f $okHelper)
Write-Host ("called in make_app: {0}" -f $okCall)
if (-not ($okHelper -and $okCall)) { Copy-Item $bak $ms -Force; throw "verify failed; reverted" }
Push-Location $code
python -c "import py_compile; py_compile.compile(r'$ms', doraise=True); print('py-compile OK')"
if ($LASTEXITCODE -ne 0) { Pop-Location; Copy-Item $bak $ms -Force; throw "py-compile failed; reverted" }
Pop-Location
Write-Host ""
Write-Host "DONE. Local app unchanged (env var unset). On Render, SAD_RESUME_ON_BOOT=1 enables resume."
