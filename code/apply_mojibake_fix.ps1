# apply_mojibake_fix.ps1
# Fix visible mojibake in the viewer UI. Matches the EXACT corrupted byte
# sequences so the ellipsis and em-dash placeholders get the right glyphs:
#   â€"  (ends U+201D)  -> —  em-dash    (anchor, typology, index fallback)
#   â€¦  (ends U+00A6)  -> …  ellipsis   (index loading state)
#   index.html scale-label double-encoded placeholder -> —
# Writes UTF-8 (no BOM) via .NET to avoid PowerShell re-mangling.

$ErrorActionPreference = 'Stop'
$code = "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\code"
$vj   = Join-Path $code "viewer\viewer.js"
$idx  = Join-Path $code "viewer\index.html"
foreach ($f in @($vj,$idx)) { if (-not (Test-Path $f)) { throw "Not found: $f" } }

$stamp = Get-Date -Format yyyyMMdd_HHmmss
Copy-Item $vj  "$vj.bak_mojibake_$stamp"
Copy-Item $idx "$idx.bak_mojibake_$stamp"
Write-Host "backups written ($stamp)"

$EMDASH = [char]0x2014
$ELLIP  = [char]0x2026
# exact mojibake sequences
$mojiEm = [char]0x00E2 + [char]0x20AC + [char]0x201D   # â€"
$mojiEl = [char]0x00E2 + [char]0x20AC + [char]0x00A6   # â€¦

$vtext = [System.IO.File]::ReadAllText($vj, [System.Text.Encoding]::UTF8)

# Replace exact mojibake em-dash sequence with em-dash, ellipsis with ellipsis.
# Scope to single-quoted placeholders to avoid touching comment text: a quoted
# token that is EXACTLY the mojibake sequence.
$vtext = $vtext.Replace("'" + $mojiEm + "'", "'" + $EMDASH + "'")
$vtext = $vtext.Replace("'" + $mojiEl + "'", "'" + $ELLIP + "'")

[System.IO.File]::WriteAllText($vj, $vtext, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "viewer.js placeholders fixed"

# index.html scale-label: replace whatever is between the tags with em-dash
$itext = [System.IO.File]::ReadAllText($idx, [System.Text.Encoding]::UTF8)
$itext = $itext -replace "(<span id=`"scale-label`">)[^<]*(</span>)", ("`${1}" + $EMDASH + "`${2}")
[System.IO.File]::WriteAllText($idx, $itext, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "index.html scale-label fixed"

# verify
$vchk = [System.IO.File]::ReadAllText($vj, [System.Text.Encoding]::UTF8)
$ichk = [System.IO.File]::ReadAllText($idx, [System.Text.Encoding]::UTF8)
$anchorOk = $vchk.Contains("anchor_venue || '" + $EMDASH + "'")
$typoOk    = $vchk.Contains("primary_typology || '" + $EMDASH + "'")
$ellipOk    = $vchk.Contains("'" + $ELLIP + "'")
$scaleOk     = $ichk.Contains("<span id=`"scale-label`">" + $EMDASH + "</span>")
$noEmMoji    = -not $vchk.Contains("'" + $mojiEm + "'")
$noElMoji     = -not $vchk.Contains("'" + $mojiEl + "'")
Write-Host ("anchor clean:    {0}" -f $anchorOk)
Write-Host ("typology clean:  {0}" -f $typoOk)
Write-Host ("ellipsis clean:  {0}" -f $ellipOk)
Write-Host ("scale clean:     {0}" -f $scaleOk)
Write-Host ("no moji em left: {0}" -f $noEmMoji)
Write-Host ("no moji el left: {0}" -f $noElMoji)
if (-not ($anchorOk -and $typoOk -and $ellipOk -and $scaleOk -and $noEmMoji -and $noElMoji)) {
    Write-Host "VERIFY incomplete -- restoring backups"
    Copy-Item "$vj.bak_mojibake_$stamp" $vj -Force
    Copy-Item "$idx.bak_mojibake_$stamp" $idx -Force
    throw "reverted"
}

Write-Host "redeploying viewer..."
Push-Location $code
python build_ui_manifest.py --data-dir "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\data" | Out-Null
Pop-Location
Write-Host "DONE. Ctrl+F5 (Network: Disable cache) to see clean glyphs."
