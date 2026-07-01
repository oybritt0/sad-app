# ============================================================
# SAD: fix /extract NameError (_AREA not defined). The ported _area() helper
# reads two module-level cache globals that were not carried across. This lifts
# _AREA / _AREA_ERR from district_server.py into sad_match_server.py.
# Dry-run first; add --write to apply. Paste this whole block into PowerShell.
# ============================================================
$ErrorActionPreference = "Stop"
$bat    = "C:\Program Files\QGIS 3.40.11\bin\python-qgis-ltr.bat"
$code   = "C:\Users\jmeyers\Desktop\Detroit_Test\code"
$script = Join-Path $code "patch_fix_area_globals.py"

$b64 = @(
  'IyEvdXNyL2Jpbi9lbnYgcHl0aG9uMwojIC0qLSBjb2Rpbmc6IHV0Zi04IC0qLQpyIiIiCnBhdGNo',
  'X2ZpeF9hcmVhX2dsb2JhbHMucHkKCkZpeC11cCBmb3IgdGhlIHBvcnRlZCAvZXh0cmFjdCByb3V0',
  'ZTogdGhlIGxpZnRlZCBfYXJlYSgpIGhlbHBlciByZWFkcyB0d28KbW9kdWxlLWxldmVsIGNhY2hl',
  'IGdsb2JhbHMgKF9BUkVBLCBfQVJFQV9FUlIpIHRoYXQgd2VyZSBub3QgY2FycmllZCBhY3Jvc3Mg',
  'ZnJvbQpkaXN0cmljdF9zZXJ2ZXIucHksIGNhdXNpbmcgTmFtZUVycm9yIGF0IHJlcXVlc3QgdGlt',
  'ZS4gVGhpcyBsaWZ0cyB0aG9zZSBleGFjdAp0b3AtbGV2ZWwgYXNzaWdubWVudHMgZnJvbSBkaXN0',
  'cmljdF9zZXJ2ZXIucHkgaW50byBzYWRfbWF0Y2hfc2VydmVyLnB5LCBwbGFjZWQKanVzdCBiZWZv',
  'cmUgX2FyZWEncyBkZWZpbml0aW9uIChvciBiZWZvcmUgbWFrZV9hcHAgaWYgX2FyZWEgaXMgbm90',
  'IGZvdW5kKS4KCklkZW1wb3RlbnQgKHNraXBzIG5hbWVzIGFscmVhZHkgZGVmaW5lZCBhdCBtb2R1',
  'bGUgbGV2ZWwgaW4gdGhlIHRhcmdldCkuCkNvbXBpbGVzIHRoZSBwYXRjaGVkIHRhcmdldCBiZWZv',
  'cmUgd3JpdGluZy4gRHJ5LXJ1biBieSBkZWZhdWx0OyAtLXdyaXRlIGFwcGxpZXMuCgpVc2FnZSAo',
  'UG93ZXJTaGVsbCwgUUdJUyBidW5kbGVkIHB5dGhvbik6CiAgJGJhdCAgPSAiQzpcUHJvZ3JhbSBG',
  'aWxlc1xRR0lTIDMuNDAuMTFcYmluXHB5dGhvbi1xZ2lzLWx0ci5iYXQiCiAgJGNvZGUgPSAiQzpc',
  'VXNlcnNcam1leWVyc1xEZXNrdG9wXERldHJvaXRfVGVzdFxjb2RlIgogICYgJGJhdCAiJGNvZGVc',
  'cGF0Y2hfZml4X2FyZWFfZ2xvYmFscy5weSIgLS1jb2RlLWRpciAkY29kZQogICMgcmV2aWV3IGRy',
  'eS1ydW4sIHRoZW4gcmUtcnVuIHdpdGggLS13cml0ZSwgdGhlbiBSRVNUQVJUIHRoZSBtYXRjaCBz',
  'ZXJ2ZXIKIiIiCmltcG9ydCBhcmdwYXJzZQppbXBvcnQgZGF0ZXRpbWUKaW1wb3J0IGlvCmltcG9y',
  'dCBvcwppbXBvcnQgcmUKaW1wb3J0IHN5cwoKIyB0aGUgY2FjaGUgZ2xvYmFscyBfYXJlYSgpIGRl',
  'cGVuZHMgb24KV0FOVEVEID0gWyJfQVJFQSIsICJfQVJFQV9FUlIiXQoKCmRlZiB0b3BsZXZlbF9h',
  'c3NpZ24obGluZXMsIG5hbWUpOgogICAgIiIiUmV0dXJuIChzdGFydF9pZHgsIGJsb2NrX2xpbmVz',
  'KSBmb3IgYSB0b3AtbGV2ZWwgYG5hbWUgPSAuLi5gIGFzc2lnbm1lbnQsCiAgICBpbmNsdWRpbmcg',
  'YW55IGNvbnRpbnVhdGlvbiB2aWEgYnJhY2tldHMvcGFyZW5zIG9yIHRyYWlsaW5nIGJhY2tzbGFz',
  'aC4KICAgIE5vbmUgaWYgbm90IGZvdW5kLiIiIgogICAgcGF0ID0gcmUuY29tcGlsZShyJ14nICsg',
  'cmUuZXNjYXBlKG5hbWUpICsgcidccyooPzo6W149XSspPz0nKQogICAgZm9yIGksIGxuIGluIGVu',
  'dW1lcmF0ZShsaW5lcyk6CiAgICAgICAgaWYgcGF0Lm1hdGNoKGxuKToKICAgICAgICAgICAgYmxv',
  'Y2sgPSBbbG5dCiAgICAgICAgICAgICMgc2ltcGxlIHNpbmdsZS1saW5lIGFzc2lnbm1lbnQgaXMg',
  'dGhlIGV4cGVjdGVkIGNhc2UgKD0gTm9uZSkKICAgICAgICAgICAgIyBleHRlbmQgZm9yIG9wZW4g',
  'YnJhY2tldHMgb3IgbGluZSBjb250aW51YXRpb25zLCBqdXN0IGluIGNhc2UKICAgICAgICAgICAg',
  'ZGVwdGggPSBsbi5jb3VudCgiKCIpICsgbG4uY291bnQoIlsiKSArIGxuLmNvdW50KCJ7IikgXAog',
  'ICAgICAgICAgICAgICAgLSBsbi5jb3VudCgiKSIpIC0gbG4uY291bnQoIl0iKSAtIGxuLmNvdW50',
  'KCJ9IikKICAgICAgICAgICAgY29udCA9IGxuLnJzdHJpcCgiXG4iKS5lbmRzd2l0aCgiXFwiKQog',
  'ICAgICAgICAgICBqID0gaSArIDEKICAgICAgICAgICAgd2hpbGUgKGRlcHRoID4gMCBvciBjb250',
  'KSBhbmQgaiA8IGxlbihsaW5lcyk6CiAgICAgICAgICAgICAgICBubCA9IGxpbmVzW2pdCiAgICAg',
  'ICAgICAgICAgICBibG9jay5hcHBlbmQobmwpCiAgICAgICAgICAgICAgICBkZXB0aCArPSBubC5j',
  'b3VudCgiKCIpICsgbmwuY291bnQoIlsiKSArIG5sLmNvdW50KCJ7IikgXAogICAgICAgICAgICAg',
  'ICAgICAgIC0gbmwuY291bnQoIikiKSAtIG5sLmNvdW50KCJdIikgLSBubC5jb3VudCgifSIpCiAg',
  'ICAgICAgICAgICAgICBjb250ID0gbmwucnN0cmlwKCJcbiIpLmVuZHN3aXRoKCJcXCIpCiAgICAg',
  'ICAgICAgICAgICBqICs9IDEKICAgICAgICAgICAgcmV0dXJuIGksIGJsb2NrCiAgICByZXR1cm4g',
  'Tm9uZQoKCmRlZiBtYWluKCk6CiAgICBhcCA9IGFyZ3BhcnNlLkFyZ3VtZW50UGFyc2VyKCkKICAg',
  'IGFwLmFkZF9hcmd1bWVudCgiLS1jb2RlLWRpciIsIHJlcXVpcmVkPVRydWUpCiAgICBhcC5hZGRf',
  'YXJndW1lbnQoIi0tc291cmNlIiwgZGVmYXVsdD0iZGlzdHJpY3Rfc2VydmVyLnB5IikKICAgIGFw',
  'LmFkZF9hcmd1bWVudCgiLS10YXJnZXQiLCBkZWZhdWx0PSJzYWRfbWF0Y2hfc2VydmVyLnB5IikK',
  'ICAgIGFwLmFkZF9hcmd1bWVudCgiLS13cml0ZSIsIGFjdGlvbj0ic3RvcmVfdHJ1ZSIpCiAgICBh',
  'cmdzID0gYXAucGFyc2VfYXJncygpCgogICAgc3JjX3BhdGggPSBvcy5wYXRoLmpvaW4oYXJncy5j',
  'b2RlX2RpciwgYXJncy5zb3VyY2UpCiAgICB0Z3RfcGF0aCA9IG9zLnBhdGguam9pbihhcmdzLmNv',
  'ZGVfZGlyLCBhcmdzLnRhcmdldCkKICAgIGZvciBwIGluIChzcmNfcGF0aCwgdGd0X3BhdGgpOgog',
  'ICAgICAgIGlmIG5vdCBvcy5wYXRoLmV4aXN0cyhwKToKICAgICAgICAgICAgcHJpbnQoIkVSUk9S',
  'OiBub3QgZm91bmQ6ICVzIiAlIHApOyBzeXMuZXhpdCgyKQoKICAgIHdpdGggaW8ub3BlbihzcmNf',
  'cGF0aCwgInIiLCBlbmNvZGluZz0idXRmLTgiLCBuZXdsaW5lPSIiKSBhcyBmOgogICAgICAgIHNy',
  'YyA9IGYucmVhZCgpCiAgICB3aXRoIGlvLm9wZW4odGd0X3BhdGgsICJyIiwgZW5jb2Rpbmc9InV0',
  'Zi04IiwgbmV3bGluZT0iIikgYXMgZjoKICAgICAgICB0Z3QgPSBmLnJlYWQoKQoKICAgIHNyY19s',
  'aW5lcyA9IHNyYy5zcGxpdGxpbmVzKGtlZXBlbmRzPVRydWUpCgogICAgIyB3aGljaCB3YW50ZWQg',
  'Z2xvYmFscyBhcmUgYWxyZWFkeSBkZWZpbmVkIGF0IHRvcCBsZXZlbCBpbiB0aGUgdGFyZ2V0Pwog',
  'ICAgZGVmIGRlZmluZWRfdG9wbGV2ZWwodGV4dCwgbmFtZSk6CiAgICAgICAgcmV0dXJuIHJlLnNl',
  'YXJjaChyJ14nICsgcmUuZXNjYXBlKG5hbWUpICsgcidccyooPzo6W149XSspPz0nLCB0ZXh0LCBy',
  'ZS5NKSBpcyBub3QgTm9uZQoKICAgIG1pc3NpbmcgPSBbbiBmb3IgbiBpbiBXQU5URUQgaWYgbm90',
  'IGRlZmluZWRfdG9wbGV2ZWwodGd0LCBuKV0KICAgIGlmIG5vdCBtaXNzaW5nOgogICAgICAgIHBy',
  'aW50KCJOb3RoaW5nIHRvIGRvOiAlcyBhbHJlYWR5IGRlZmluZXMgJXMuIE5vIGNoYW5nZS4iCiAg',
  'ICAgICAgICAgICAgJSAoYXJncy50YXJnZXQsICIsICIuam9pbihXQU5URUQpKSkKICAgICAgICBz',
  'eXMuZXhpdCgwKQoKICAgIGJsb2NrcyA9IFtdCiAgICBmb3IgbmFtZSBpbiBtaXNzaW5nOgogICAg',
  'ICAgIGZvdW5kID0gdG9wbGV2ZWxfYXNzaWduKHNyY19saW5lcywgbmFtZSkKICAgICAgICBpZiBu',
  'b3QgZm91bmQ6CiAgICAgICAgICAgIHByaW50KCJFUlJPUjogY291bGQgbm90IGZpbmQgYSB0b3At',
  'bGV2ZWwgJyVzID0gLi4uJyBpbiAlcy4iICUgKG5hbWUsIGFyZ3Muc291cmNlKSkKICAgICAgICAg',
  'ICAgcHJpbnQoIiAgICAgICBBYm9ydGluZzsgbm90aGluZyBjaGFuZ2VkLiIpCiAgICAgICAgICAg',
  'IHN5cy5leGl0KDMpCiAgICAgICAgYmxvY2tzLmFwcGVuZCgobmFtZSwgIiIuam9pbihmb3VuZFsx',
  'XSkpKQoKICAgIGluc2VydF90ZXh0ID0gIiMgLS0tIHBvcnRlZCBjYWNoZSBnbG9iYWxzIGZvciBf',
  'YXJlYSgpIC0tLVxuIiBcCiAgICAgICAgKyAiIi5qb2luKGIgZm9yIF8sIGIgaW4gYmxvY2tzKQog',
  'ICAgaWYgbm90IGluc2VydF90ZXh0LmVuZHN3aXRoKCJcbiIpOgogICAgICAgIGluc2VydF90ZXh0',
  'ICs9ICJcbiIKICAgIGluc2VydF90ZXh0ICs9ICJcbiIKCiAgICAjIGluc2VydCBqdXN0IGJlZm9y',
  'ZSBkZWYgX2FyZWEsIGVsc2UgYmVmb3JlIGRlZiBtYWtlX2FwcAogICAgbSA9IHJlLnNlYXJjaChy',
  'J15kZWYgX2FyZWFccypcKCcsIHRndCwgcmUuTSkKICAgIGlmIG5vdCBtOgogICAgICAgIG0gPSBy',
  'ZS5zZWFyY2gocideZGVmIG1ha2VfYXBwXHMqXCgnLCB0Z3QsIHJlLk0pCiAgICBpZiBub3QgbToK',
  'ICAgICAgICBwcmludCgiRVJST1I6IGNvdWxkIG5vdCBmaW5kICdkZWYgX2FyZWEoJyBvciAnZGVm',
  'IG1ha2VfYXBwKCcgaW4gdGFyZ2V0LiIpCiAgICAgICAgc3lzLmV4aXQoNCkKICAgIHBvcyA9IG0u',
  'c3RhcnQoKQogICAgbmV3X3RndCA9IHRndFs6cG9zXSArIGluc2VydF90ZXh0ICsgdGd0W3Bvczpd',
  'CgogICAgdHJ5OgogICAgICAgIGNvbXBpbGUobmV3X3RndCwgdGd0X3BhdGgsICJleGVjIikKICAg',
  'IGV4Y2VwdCBTeW50YXhFcnJvciBhcyBlOgogICAgICAgIHByaW50KCJFUlJPUjogcGF0Y2hlZCB0',
  'YXJnZXQgZmFpbGVkIHRvIGNvbXBpbGU6ICVzIiAlIGUpCiAgICAgICAgc3lzLmV4aXQoNSkKCiAg',
  'ICBwcmludCgiVGFyZ2V0ICAgICAgICA6ICVzIiAlIHRndF9wYXRoKQogICAgcHJpbnQoIk1pc3Np',
  'bmcgZ2xvYmFsczogJXMiICUgIiwgIi5qb2luKG1pc3NpbmcpKQogICAgcHJpbnQoIkNvbXBpbGUg',
  'ICAgICAgOiBPSyIpCiAgICBwcmludCgiIikKICAgIHByaW50KCItLS0gaW5qZWN0aW5nIGJlZm9y',
  'ZSAlcyAtLS0iICUgKCJkZWYgX2FyZWEiIGlmICJkZWYgX2FyZWEiIGluIHRndCBlbHNlICJkZWYg',
  'bWFrZV9hcHAiKSkKICAgIGZvciBsbiBpbiBpbnNlcnRfdGV4dC5yc3RyaXAoIlxuIikuc3BsaXRs',
  'aW5lcygpOgogICAgICAgIHByaW50KCIgICsgIiArIGxuKQoKICAgIGlmIG5vdCBhcmdzLndyaXRl',
  'OgogICAgICAgIHByaW50KCIiKQogICAgICAgIHByaW50KCJEUlktUlVOIE9OTFkuIE5vIGZpbGUg',
  'd3JpdHRlbi4gUmUtcnVuIHdpdGggLS13cml0ZSwgdGhlbiBSRVNUQVJUIHRoZSIpCiAgICAgICAg',
  'cHJpbnQoIm1hdGNoIHNlcnZlciAoQ3RybCtDIFdpbmRvdyAxLCByZS1ydW4pIGFuZCBoYXJkLXJl',
  'bG9hZC4iKQogICAgICAgIHJldHVybgoKICAgIHRzID0gZGF0ZXRpbWUuZGF0ZXRpbWUubm93KCku',
  'c3RyZnRpbWUoIiVZJW0lZF8lSCVNJVMiKQogICAgYmFja3VwID0gIiVzLiVzLmJhayIgJSAodGd0',
  'X3BhdGgsIHRzKQogICAgd2l0aCBpby5vcGVuKGJhY2t1cCwgInciLCBlbmNvZGluZz0idXRmLTgi',
  'LCBuZXdsaW5lPSIiKSBhcyBmOgogICAgICAgIGYud3JpdGUodGd0KQogICAgd2l0aCBpby5vcGVu',
  'KHRndF9wYXRoLCAidyIsIGVuY29kaW5nPSJ1dGYtOCIsIG5ld2xpbmU9IiIpIGFzIGY6CiAgICAg',
  'ICAgZi53cml0ZShuZXdfdGd0KQogICAgcHJpbnQoIiIpCiAgICBwcmludCgiQmFja3VwIHdyaXR0',
  'ZW46ICVzIiAlIGJhY2t1cCkKICAgIHByaW50KCJXcm90ZSBpbiBwbGFjZTogJXMiICUgdGd0X3Bh',
  'dGgpCiAgICBwcmludCgiRG9uZS4gUkVTVEFSVCB0aGUgbWF0Y2ggc2VydmVyIChDdHJsK0MgV2lu',
  'ZG93IDEsIHJlLXJ1biBpdCksIHRoZW4gaGFyZC1yZWxvYWQuIikKCgppZiBfX25hbWVfXyA9PSAi',
  'X19tYWluX18iOgogICAgbWFpbigpCg=='
) -join ''
[IO.File]::WriteAllBytes($script, [Convert]::FromBase64String($b64))
Write-Host "wrote $script"

Write-Host "`n=== DRY RUN ===" -ForegroundColor Cyan
& $bat $script --code-dir $code

Write-Host "`nIf it shows 'Compile : OK', re-run with --write, e.g.:" -ForegroundColor Yellow
Write-Host '  & $bat "$code\patch_fix_area_globals.py" --code-dir $code --write' -ForegroundColor Yellow
Write-Host "THEN restart the match server (Ctrl+C Window 1, re-run) and hard-reload." -ForegroundColor Yellow
