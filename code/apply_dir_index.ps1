# apply_dir_index.ps1
# Field mode targets a DIRECTORY ('../_compare_ui/'), but _serve only handles
# files, so a trailing-slash directory request 404s. This makes _serve append
# 'index.html' when the resolved path is a directory (and redirect a dir path
# without a trailing slash), mirroring ui_server.py's directory indexing.
# Safe: backup, exact edit, verify, py-compile, auto-revert.

$ErrorActionPreference = 'Stop'
$code = "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\code"
$ms   = Join-Path $code "sad_match_server.py"
if (-not (Test-Path $ms)) { throw "Not found: $ms" }

$bak = "$ms.bak_dirindex_$(Get-Date -Format yyyyMMdd_HHmmss)"
Copy-Item $ms $bak
Write-Host "backup: $bak"

$src = Get-Content $ms -Raw

$old = @'
    def _serve(relpath):
        # prevent path traversal; resolve under data_dir only
        base = data_dir.resolve()
        target = (base / relpath).resolve()
        if not str(target).startswith(str(base)):
            abort(403)
        if not target.is_file():
            abort(404)
        resp = send_from_directory(str(target.parent), target.name)
        for k, v in _NOCACHE.items():
            resp.headers[k] = v
        return resp
'@
$new = @'
    def _serve(relpath):
        # prevent path traversal; resolve under data_dir only
        base = data_dir.resolve()
        target = (base / relpath).resolve()
        if not str(target).startswith(str(base)):
            abort(403)
        # directory -> serve its index.html (mirrors ui_server.py indexing)
        if target.is_dir():
            idx = target / 'index.html'
            if idx.is_file():
                target = idx
            else:
                abort(404)
        if not target.is_file():
            abort(404)
        resp = send_from_directory(str(target.parent), target.name)
        for k, v in _NOCACHE.items():
            resp.headers[k] = v
        return resp
'@

$c = ([regex]::Matches($src,[regex]::Escape($old))).Count
if ($c -ne 1) { throw "expected 1 match for _serve body, found $c" }
$src = $src.Replace($old,$new)
Set-Content -Path $ms -Value $src -Encoding UTF8
Write-Host "patched: _serve now indexes directories to index.html"

$chk = Get-Content $ms -Raw
$ok = $chk -match "directory -> serve its index.html"
Write-Host ("dir-index present: {0}" -f $ok)
if (-not $ok) { Copy-Item $bak $ms -Force; throw "verify failed; reverted" }
Push-Location $code
python -c "import py_compile; py_compile.compile(r'$ms', doraise=True); print('py-compile OK')"
if ($LASTEXITCODE -ne 0) { Pop-Location; Copy-Item $bak $ms -Force; throw "py-compile failed; reverted" }
Pop-Location
Write-Host ""
Write-Host "DONE. Restart the match server, reload, click Field mode."
