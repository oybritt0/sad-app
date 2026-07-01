# ============================================================
# SAD: fix Compare tab "match server not reachable".
# The Compare tab was POSTing to :5500 (static server -> 501). This prepends a
# scoped shim to compare_dash.js that reroutes match-server calls to :8000.
# Static asset fetches are untouched. Dry-run by default.
# Paste this whole block into PowerShell.
# ============================================================
$ErrorActionPreference = "Stop"
$bat    = "C:\Program Files\QGIS 3.40.11\bin\python-qgis-ltr.bat"
$ui     = "C:\Users\jmeyers\Desktop\Detroit_Test\data\_compare_ui"
$script = Join-Path $ui "patch_compare_matchhost.py"

$b64 = @(
  'IyEvdXNyL2Jpbi9lbnYgcHl0aG9uMwojIC0qLSBjb2Rpbmc6IHV0Zi04IC0qLQpyIiIiCnBhdGNo',
  'X2NvbXBhcmVfbWF0Y2hob3N0LnB5CgpQcmVwZW5kcyBhIHNtYWxsIGhvc3Qgc2hpbSB0byBjb21w',
  'YXJlX2Rhc2guanMgc28gdGhlIENvbXBhcmUgdGFiIHNlbmRzIGl0cwptYXRjaC1zZXJ2ZXIgY2Fs',
  'bHMgKC9hbmFseXplX3Byb2dyYW0sIC9leHRyYWN0LCAvaGVhbHRoLCBldGMuKSB0byB0aGUgbWF0',
  'Y2gKc2VydmVyIG9uIGh0dHA6Ly9sb2NhbGhvc3Q6ODAwMCBpbnN0ZWFkIG9mIHRoZSBzdGF0aWMg',
  'cGFnZSBzZXJ2ZXIgb24gOjU1MDAsCndoaWNoIHJldHVybnMgNTAxIGZvciBQT1NULgoKVGhlIHNo',
  'aW0gcmV3cml0ZXMgT05MWSBtYXRjaC1zZXJ2ZXIgZW5kcG9pbnRzOyBzdGF0aWMgYXNzZXQgZmV0',
  'Y2hlcyBhcmUgbGVmdAphbG9uZS4gSXQgaXMgc2NvcGVkIGluIGFuIElJRkUgYW5kIGlkZW1wb3Rl',
  'bnQgKG1hcmtlciBfX3NhZE1hdGNoSG9zdFNoaW0pLiBJZgp0aGUgZmlsZSBiZWdpbnMgd2l0aCBh',
  'ICJ1c2Ugc3RyaWN0IiBkaXJlY3RpdmUsIHRoZSBzaGltIGlzIGluc2VydGVkIHJpZ2h0IGFmdGVy',
  'Cml0IHNvIHRoZSBkaXJlY3RpdmUgc3RheXMgZmlyc3QuCgpEZWZhdWx0IERSWS1SVU4uIFBhc3Mg',
  'LS13cml0ZSB0byBiYWNrIHVwICh0aW1lc3RhbXBlZCAuYmFrKSB0aGVuIHdyaXRlIGluIHBsYWNl',
  'LgoKVXNhZ2UgKFBvd2VyU2hlbGwsIFFHSVMgYnVuZGxlZCBweXRob24pOgogICRiYXQgPSAiQzpc',
  'UHJvZ3JhbSBGaWxlc1xRR0lTIDMuNDAuMTFcYmluXHB5dGhvbi1xZ2lzLWx0ci5iYXQiCiAgJHVp',
  'ICA9ICJDOlxVc2Vyc1xqbWV5ZXJzXERlc2t0b3BcRGV0cm9pdF9UZXN0XGRhdGFcX2NvbXBhcmVf',
  'dWkiCiAgJiAkYmF0ICIkdWlccGF0Y2hfY29tcGFyZV9tYXRjaGhvc3QucHkiIC0tdWktZGlyICR1',
  'aQogICMgcmV2aWV3IHRoZSBkcnktcnVuLCB0aGVuIHJlLXJ1biB3aXRoIC0td3JpdGUgYXBwZW5k',
  'ZWQKIiIiCmltcG9ydCBhcmdwYXJzZQppbXBvcnQgZGF0ZXRpbWUKaW1wb3J0IGlvCmltcG9ydCBv',
  'cwppbXBvcnQgcmUKaW1wb3J0IHN5cwoKTUFSS0VSID0gIl9fc2FkTWF0Y2hIb3N0U2hpbSIKClNI',
  'SU0gPSByIiIiLyogU0FEIG1hdGNoLXNlcnZlciBob3N0IHNoaW0uIFRoZSBDb21wYXJlIHRhYiBt',
  'dXN0IHNlbmQgbWF0Y2gtc2VydmVyIGNhbGxzIHRvCiAgIHRoZSBtYXRjaCBzZXJ2ZXIgb24gOjgw',
  'MDAsIG5vdCB0aGUgc3RhdGljIHBhZ2Ugc2VydmVyIG9uIDo1NTAwICh3aGljaCByZXR1cm5zCiAg',
  'IDUwMSBmb3IgUE9TVCkuIFRoaXMgcmV3cml0ZXMgT05MWSBtYXRjaC1zZXJ2ZXIgZW5kcG9pbnRz',
  'OyBzdGF0aWMgYXNzZXQgZmV0Y2hlcwogICBhcmUgbGVmdCB1bnRvdWNoZWQuIEFkZGl0aXZlIGFu',
  'ZCByZXZlcnNpYmxlLiAqLwooZnVuY3Rpb24oKXsKICBpZiAod2luZG93Ll9fc2FkTWF0Y2hIb3N0',
  'U2hpbSkgcmV0dXJuOwogIHdpbmRvdy5fX3NhZE1hdGNoSG9zdFNoaW0gPSB0cnVlOwogIHZhciBN',
  'QVRDSCA9ICdodHRwOi8vbG9jYWxob3N0OjgwMDAnOwogIHZhciBFUCA9IC9eXC8oYW5hbHl6ZShf',
  'W2Etel0rKT98ZXh0cmFjdHxoZWFsdGgpKFwvfFw/fCQpLzsKICB2YXIgX2ZldGNoID0gd2luZG93',
  'LmZldGNoLmJpbmQod2luZG93KTsKICBmdW5jdGlvbiByZXJvdXRlKHUpewogICAgaWYgKHR5cGVv',
  'ZiB1ICE9PSAnc3RyaW5nJykgcmV0dXJuIG51bGw7CiAgICBpZiAoRVAudGVzdCh1KSkgcmV0dXJu',
  'IE1BVENIICsgdTsKICAgIGlmICh1LmluZGV4T2YobG9jYXRpb24ub3JpZ2luKSA9PT0gMCl7CiAg',
  'ICAgIHZhciBwID0gdS5zbGljZShsb2NhdGlvbi5vcmlnaW4ubGVuZ3RoKTsKICAgICAgaWYgKEVQ',
  'LnRlc3QocCkpIHJldHVybiBNQVRDSCArIHA7CiAgICB9CiAgICByZXR1cm4gbnVsbDsKICB9CiAg',
  'd2luZG93LmZldGNoID0gZnVuY3Rpb24oaW5wdXQsIGluaXQpewogICAgdHJ5IHsKICAgICAgdmFy',
  'IHVybCA9ICh0eXBlb2YgaW5wdXQgPT09ICdzdHJpbmcnKSA/IGlucHV0IDogKGlucHV0ICYmIGlu',
  'cHV0LnVybCkgfHwgJyc7CiAgICAgIHZhciBhYnMgPSByZXJvdXRlKHVybCk7CiAgICAgIGlmIChh',
  'YnMpewogICAgICAgIGlucHV0ID0gKHR5cGVvZiBpbnB1dCA9PT0gJ3N0cmluZycpID8gYWJzIDog',
  'bmV3IFJlcXVlc3QoYWJzLCBpbnB1dCk7CiAgICAgIH0KICAgIH0gY2F0Y2ggKGUpIHsgLyogZmFs',
  'bCB0aHJvdWdoIHRvIG9yaWdpbmFsIGZldGNoICovIH0KICAgIHJldHVybiBfZmV0Y2goaW5wdXQs',
  'IGluaXQpOwogIH07Cn0pKCk7CiIiIgoKCmRlZiBtYWluKCk6CiAgICBhcCA9IGFyZ3BhcnNlLkFy',
  'Z3VtZW50UGFyc2VyKCkKICAgIGFwLmFkZF9hcmd1bWVudCgiLS11aS1kaXIiLCByZXF1aXJlZD1U',
  'cnVlLAogICAgICAgICAgICAgICAgICAgIGhlbHA9IkRpcmVjdG9yeSB0aGF0IGNvbnRhaW5zIGNv',
  'bXBhcmVfZGFzaC5qcyIpCiAgICBhcC5hZGRfYXJndW1lbnQoIi0tZmlsZSIsIGRlZmF1bHQ9ImNv',
  'bXBhcmVfZGFzaC5qcyIsCiAgICAgICAgICAgICAgICAgICAgaGVscD0iVGFyZ2V0IGZpbGVuYW1l',
  'IGluc2lkZSAtLXVpLWRpciIpCiAgICBhcC5hZGRfYXJndW1lbnQoIi0td3JpdGUiLCBhY3Rpb249',
  'InN0b3JlX3RydWUiLAogICAgICAgICAgICAgICAgICAgIGhlbHA9IkJhY2sgdXAgdGhlbiB3cml0',
  'ZSBpbiBwbGFjZSAoZGVmYXVsdCBpcyBkcnktcnVuKSIpCiAgICBhcmdzID0gYXAucGFyc2VfYXJn',
  'cygpCgogICAgdGFyZ2V0ID0gb3MucGF0aC5qb2luKGFyZ3MudWlfZGlyLCBhcmdzLmZpbGUpCiAg',
  'ICBpZiBub3Qgb3MucGF0aC5leGlzdHModGFyZ2V0KToKICAgICAgICBwcmludCgiRVJST1I6IG5v',
  'dCBmb3VuZDogJXMiICUgdGFyZ2V0KQogICAgICAgIHN5cy5leGl0KDIpCgogICAgd2l0aCBpby5v',
  'cGVuKHRhcmdldCwgInIiLCBlbmNvZGluZz0idXRmLTgiLCBuZXdsaW5lPSIiKSBhcyBmOgogICAg',
  'ICAgIHMgPSBmLnJlYWQoKQoKICAgIGlmIE1BUktFUiBpbiBzOgogICAgICAgIHByaW50KCJBbHJl',
  'YWR5IHBhdGNoZWQ6ICVzIGFscmVhZHkgaGFzIHRoZSBtYXRjaC1ob3N0IHNoaW0uIE5vIGNoYW5n',
  'ZS4iICUgYXJncy5maWxlKQogICAgICAgIHN5cy5leGl0KDApCgogICAgIyBrZWVwIGEgbGVhZGlu',
  'ZyAidXNlIHN0cmljdCIgZGlyZWN0aXZlIGZpcnN0LCBpZiBwcmVzZW50CiAgICBtID0gcmUubWF0',
  'Y2gociIiIlxzKihbJyJdKXVzZSBzdHJpY3RcMVxzKjs/WyBcdF0qXHI/XG4iIiIsIHMpCiAgICBp',
  'ZiBtOgogICAgICAgIGN1dCA9IG0uZW5kKCkKICAgICAgICBuZXdfcyA9IHNbOmN1dF0gKyBTSElN',
  'ICsgc1tjdXQ6XQogICAgICAgIHdoZXJlID0gImFmdGVyIHRoZSBsZWFkaW5nICd1c2Ugc3RyaWN0',
  'JyBkaXJlY3RpdmUiCiAgICBlbHNlOgogICAgICAgIG5ld19zID0gU0hJTSArIHMKICAgICAgICB3',
  'aGVyZSA9ICJhdCB0aGUgdG9wIG9mIHRoZSBmaWxlIgoKICAgIHByaW50KCJUYXJnZXQgOiAlcyIg',
  'JSB0YXJnZXQpCiAgICBwcmludCgiSW5zZXJ0IDogJXMiICUgd2hlcmUpCiAgICBwcmludCgiU2hp',
  'bSAgIDogcmV3cml0ZXMgL2FuYWx5emUqLCAvZXh0cmFjdCwgL2hlYWx0aCB0byBodHRwOi8vbG9j',
  'YWxob3N0OjgwMDAiKQogICAgcHJpbnQoIiIpCiAgICBwcmludCgiLS0tIGZpcnN0IGxpbmVzIGFm',
  'dGVyIHBhdGNoIC0tLSIpCiAgICBmb3IgbCBpbiBuZXdfcy5zcGxpdGxpbmVzKClbOjZdOgogICAg',
  'ICAgIHByaW50KCIgICAgIiArIGwpCgogICAgaWYgbm90IGFyZ3Mud3JpdGU6CiAgICAgICAgcHJp',
  'bnQoIiIpCiAgICAgICAgcHJpbnQoIkRSWS1SVU4gT05MWS4gTm8gZmlsZSB3cml0dGVuLiBSZS1y',
  'dW4gd2l0aCAtLXdyaXRlIHRvIGFwcGx5LCB0aGVuIikKICAgICAgICBwcmludCgiaGFyZC1yZWxv',
  'YWQgdGhlIENvbXBhcmUgdGFiIChDdHJsK0Y1KS4gTm8gc2VydmVyIHJlc3RhcnQgbmVlZGVkLiIp',
  'CiAgICAgICAgcmV0dXJuCgogICAgdHMgPSBkYXRldGltZS5kYXRldGltZS5ub3coKS5zdHJmdGlt',
  'ZSgiJVklbSVkXyVIJU0lUyIpCiAgICBiYWNrdXAgPSAiJXMuJXMuYmFrIiAlICh0YXJnZXQsIHRz',
  'KQogICAgd2l0aCBpby5vcGVuKGJhY2t1cCwgInciLCBlbmNvZGluZz0idXRmLTgiLCBuZXdsaW5l',
  'PSIiKSBhcyBmOgogICAgICAgIGYud3JpdGUocykKICAgIHdpdGggaW8ub3Blbih0YXJnZXQsICJ3',
  'IiwgZW5jb2Rpbmc9InV0Zi04IiwgbmV3bGluZT0iIikgYXMgZjoKICAgICAgICBmLndyaXRlKG5l',
  'd19zKQogICAgcHJpbnQoIiIpCiAgICBwcmludCgiQmFja3VwIHdyaXR0ZW46ICVzIiAlIGJhY2t1',
  'cCkKICAgIHByaW50KCJXcm90ZSBpbiBwbGFjZTogJXMiICUgdGFyZ2V0KQogICAgcHJpbnQoIkRv',
  'bmUuIEhhcmQtcmVsb2FkIHRoZSBDb21wYXJlIHRhYiAoQ3RybCtGNSkuIikKCgppZiBfX25hbWVf',
  'XyA9PSAiX19tYWluX18iOgogICAgbWFpbigpCg=='
) -join ''
[IO.File]::WriteAllBytes($script, [Convert]::FromBase64String($b64))
Write-Host "wrote $script"

# --- DRY RUN first (no changes) ---
Write-Host "`n=== DRY RUN ===" -ForegroundColor Cyan
& $bat $script --ui-dir $ui

Write-Host "`nIf the first lines look right, re-run the SAME command with  --write" -ForegroundColor Yellow
Write-Host "appended, then hard-reload the Compare tab (Ctrl+F5). No server restart." -ForegroundColor Yellow
