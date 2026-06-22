# apply_route_dash_fix.ps1
# The route tooltip shows mojibake (â€") where an em-dash was meant. Replace the
# whole return-expression with a plain ASCII hyphen so nothing can corrupt.
# Anchors on ASCII-only fragments (the parts before/after the bad char) using a
# regex so we don't have to byte-match the mojibake itself.

$ErrorActionPreference = 'Stop'
$code = "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\code"
$vj   = Join-Path $code "viewer\viewer.js"
if (-not (Test-Path $vj)) { throw "Not found: $vj" }

$bak = "$vj.bak_dashfix_$(Get-Date -Format yyyyMMdd_HHmmss)"
Copy-Item $vj $bak
Write-Host "backup: $bak"

$src = Get-Content $vj -Raw

# Regex: match  return nm + ' (' + md + ')' + (p.served ? '<ANYTHING> enters SAD' : '');
# and rewrite with a clean ASCII hyphen. [^']* swallows the mojibake bytes.
$pattern = "return nm \+ ' \(' \+ md \+ '\)' \+ \(p\.served \? '[^']*enters SAD' : ''\);"
$replacement = "return nm + ' (' + md + ')' + (p.served ? ' - enters SAD' : '');"

$m = [regex]::Matches($src, $pattern)
if ($m.Count -ne 1) { throw "expected 1 match for the title return line, found $($m.Count)" }
$src = [regex]::Replace($src, $pattern, $replacement)
Set-Content -Path $vj -Value $src -Encoding UTF8
Write-Host "patched: tooltip now uses a plain ASCII hyphen (no mojibake)"

# verify
$chk = Get-Content $vj -Raw
$ok = $chk -match "p\.served \? ' - enters SAD' : ''"
$open  = ([regex]::Matches($chk,'\{')).Count
$close = ([regex]::Matches($chk,'\}')).Count
Write-Host ("clean hyphen present: {0}" -f $ok)
Write-Host ("brace balance: {0} / {1}" -f $open,$close)
if (-not $ok -or ($open -ne $close)) { Copy-Item $bak $vj -Force; throw "verify failed; reverted" }

Write-Host "redeploying viewer..."
Push-Location $code
python build_ui_manifest.py --data-dir "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\data" | Out-Null
Pop-Location
Write-Host "DONE. Network tab -> Disable cache -> reload; hover a route (tooltip clean now)."
