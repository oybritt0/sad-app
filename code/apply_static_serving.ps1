# apply_static_serving.ps1
# The Flask match server is API-only; locally a separate ui_server.py (:8765)
# serves the viewer + data files. On Render's SINGLE service the Flask app must
# serve both. This adds static routes that serve files from data_dir (the
# persistent disk), so the viewer loads at /_ui/ and its fetches
# (manifest.json, ../<sad>/..., ../_compare_ui/...) resolve on the same service.
# No-cache headers match ui_server. Local app is unaffected (you still use :8765),
# but these routes also work locally if you hit :8000.
# Safe: backup, exact edit, verify, py-compile, auto-revert.

$ErrorActionPreference = 'Stop'
$code = "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\code"
$ms   = Join-Path $code "sad_match_server.py"
if (-not (Test-Path $ms)) { throw "Not found: $ms" }

$bak = "$ms.bak_static_$(Get-Date -Format yyyyMMdd_HHmmss)"
Copy-Item $ms $bak
Write-Host "backup: $bak"

$src = Get-Content $ms -Raw

# Ensure send_from_directory + redirect are imported from flask.
$src = $src -replace "from flask import Flask, request, jsonify",
                     "from flask import Flask, request, jsonify, send_from_directory, redirect, abort"

# Insert static routes inside make_app, right after the /health route.
# Anchor on the health route's return line (unique).
$anchor = @'
    @app.route("/health")
    def health():
        return jsonify({"ok": True, "sads": len(corpus.records), "dims": corpus.feat_present})
'@
if (([regex]::Matches($src,[regex]::Escape($anchor))).Count -ne 1) { throw "health route anchor not found uniquely" }

$routes = @'
    @app.route("/health")
    def health():
        return jsonify({"ok": True, "sads": len(corpus.records), "dims": corpus.feat_present})

    # ---- Static serving (single-service deploy) ----------------------------
    # Serve the viewer + data files straight from data_dir, the same tree
    # ui_server.py serves locally. One service then covers viewer + API.
    _NOCACHE = {"Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache", "Expires": "0"}

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

    @app.route("/")
    def _root():
        return redirect("/_ui/index.html")

    @app.route("/_ui/")
    def _ui_index():
        return _serve("_ui/index.html")

    # everything else: serve as a file under data_dir (viewer assets, manifests,
    # per-district geojsons, _compare_ui, _shared, place cards, etc.)
    @app.route("/<path:relpath>")
    def _static_any(relpath):
        return _serve(relpath)
'@
$src = $src.Replace($anchor, $routes)

Set-Content -Path $ms -Value $src -Encoding UTF8
Write-Host "patched: static serving added"

$chk = Get-Content $ms -Raw
$okImp   = $chk -match "send_from_directory"
$okServe = $chk -match "def _serve\("
$okRoot  = $chk -match 'redirect\("/_ui/index.html"\)'
$okAny   = $chk -match "def _static_any\("
Write-Host ("import ok:        {0}" -f $okImp)
Write-Host ("_serve helper:    {0}" -f $okServe)
Write-Host ("root redirect:    {0}" -f $okRoot)
Write-Host ("catch-all route:  {0}" -f $okAny)
if (-not ($okImp -and $okServe -and $okRoot -and $okAny)) { Copy-Item $bak $ms -Force; throw "verify failed; reverted" }
Push-Location $code
python -c "import py_compile; py_compile.compile(r'$ms', doraise=True); print('py-compile OK')"
if ($LASTEXITCODE -ne 0) { Pop-Location; Copy-Item $bak $ms -Force; throw "py-compile failed; reverted" }
Pop-Location
Write-Host ""
Write-Host "DONE. The Flask app now serves viewer+data. Test locally: start ONLY the match"
Write-Host "server, open http://localhost:8000/_ui/  -- it should show the viewer."
