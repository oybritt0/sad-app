# ============================================================
# SAD: fix CORS preflight for ALL module routes (/extract toggles,
# /analyze_program, /analyze_nature). Adds a global OPTIONS 204 shim to
# make_app in sad_match_server.py. This is the real cause of the blocked
# toggles + stuck matching (preflight returned 500, browser blocked it).
# Dry-run by default. Paste this whole block into PowerShell.
# ============================================================
$ErrorActionPreference = "Stop"
$bat    = "C:\Program Files\QGIS 3.40.11\bin\python-qgis-ltr.bat"
$code   = "C:\Users\jmeyers\Desktop\Detroit_Test\code"
$script = Join-Path $code "patch_cors_preflight.py"

$b64 = @(
  'IyEvdXNyL2Jpbi9lbnYgcHl0aG9uMwojIC0qLSBjb2Rpbmc6IHV0Zi04IC0qLQpyIiIiCnBhdGNo',
  'X2NvcnNfcHJlZmxpZ2h0LnB5CgpBZGRzIGEgZ2xvYmFsIENPUlMgcHJlZmxpZ2h0IHNoaW0gdG8g',
  'bWFrZV9hcHAoKSBpbiBzYWRfbWF0Y2hfc2VydmVyLnB5IHNvIHRoYXQKRVZFUlkgcm91dGUgKGlu',
  'Y2x1ZGluZyBtb2R1bGFyIC9leHRyYWN0LCAvYW5hbHl6ZV9wcm9ncmFtLCAvYW5hbHl6ZV9uYXR1',
  'cmUpCmFuc3dlcnMgdGhlIGJyb3dzZXIgT1BUSU9OUyBwcmVmbGlnaHQgd2l0aCAyMDQgKyBDT1JT',
  'IGhlYWRlcnMgYmVmb3JlIHRoZSByb3V0ZQpoYW5kbGVyIHJ1bnMuIFdpdGhvdXQgdGhpcywgbW9k',
  'dWxlIHJvdXRlcyBleGVjdXRlIHRoZWlyIFBPU1QgYm9keSBvbiB0aGUKcHJlZmxpZ2h0LCB0aHJv',
  'dywgYW5kIHJldHVybiA1MDAsIHdoaWNoIHRoZSBicm93c2VyIGJsb2NrcyBhcwoicHJlZmxpZ2h0',
  'IC4uLiBkb2VzIG5vdCBoYXZlIEhUVFAgb2sgc3RhdHVzIi4KCkFuY2hvcnMgb24gdGhlIHNpbmds',
  'ZSAgYXBwID0gRmxhc2soX19uYW1lX18pICBsaW5lIGluc2lkZSBtYWtlX2FwcCBhbmQgaW5zZXJ0',
  'cwp0aGUgc2hpbSByaWdodCBhZnRlciBpdC4gUmVxdWlyZXMgZXhhY3RseSBvbmUgbWF0Y2guIElk',
  'ZW1wb3RlbnQgKG1hcmtlcgpfY29yc19wcmVmbGlnaHQpLiBDb21waWxlcyB0aGUgcGF0Y2hlZCBz',
  'b3VyY2UgYmVmb3JlIHdyaXRpbmcuCgpEZWZhdWx0IERSWS1SVU4uIFBhc3MgLS13cml0ZSB0byBi',
  'YWNrIHVwICh0aW1lc3RhbXBlZCAuYmFrKSB0aGVuIHdyaXRlIGluIHBsYWNlLgoKVXNhZ2UgKFBv',
  'd2VyU2hlbGwsIFFHSVMgYnVuZGxlZCBweXRob24pOgogICRiYXQgID0gIkM6XFByb2dyYW0gRmls',
  'ZXNcUUdJUyAzLjQwLjExXGJpblxweXRob24tcWdpcy1sdHIuYmF0IgogICRjb2RlID0gIkM6XFVz',
  'ZXJzXGptZXllcnNcRGVza3RvcFxEZXRyb2l0X1Rlc3RcY29kZSIKICAmICRiYXQgIiRjb2RlXHBh',
  'dGNoX2NvcnNfcHJlZmxpZ2h0LnB5IiAtLWNvZGUtZGlyICRjb2RlCiAgIyByZXZpZXcgdGhlIGRy',
  'eS1ydW4sIHRoZW4gcmUtcnVuIHdpdGggLS13cml0ZSBhcHBlbmRlZAoiIiIKaW1wb3J0IGFyZ3Bh',
  'cnNlCmltcG9ydCBkYXRldGltZQppbXBvcnQgaW8KaW1wb3J0IG9zCmltcG9ydCByZQppbXBvcnQg',
  'c3lzCgpNQVJLRVIgPSAiX2NvcnNfcHJlZmxpZ2h0IgpBTkNIT1IgPSByZS5jb21waWxlKHIiXig/',
  'UDxpbmRlbnQ+WyBcdF0qKWFwcFxzKj1ccypGbGFza1woX19uYW1lX19cKVteXG5dKiQiLCByZS5N',
  'KQoKU0hJTV9URU1QTEFURSA9ICgKICAgICJ7aW5kfSMgLS0tIGdsb2JhbCBDT1JTIHByZWZsaWdo',
  'dCBzaGltIChhbGwgcm91dGVzOyBhbnN3ZXJzIE9QVElPTlMgMjA0KSAtLS1cbiIKICAgICJ7aW5k',
  'fWZyb20gZmxhc2sgaW1wb3J0IG1ha2VfcmVzcG9uc2UgYXMgX21ha2VfcmVzcG9uc2VcbiIKICAg',
  'ICJ7aW5kfUBhcHAuYmVmb3JlX3JlcXVlc3RcbiIKICAgICJ7aW5kfWRlZiBfY29yc19wcmVmbGln',
  'aHQoKTpcbiIKICAgICJ7aW5kfSAgICBpZiByZXF1ZXN0Lm1ldGhvZCA9PSBcIk9QVElPTlNcIjpc',
  'biIKICAgICJ7aW5kfSAgICAgICAgX3IgPSBfbWFrZV9yZXNwb25zZShcIlwiLCAyMDQpXG4iCiAg',
  'ICAie2luZH0gICAgICAgIF9yLmhlYWRlcnNbXCJBY2Nlc3MtQ29udHJvbC1BbGxvdy1PcmlnaW5c',
  'Il0gPSBcIipcIlxuIgogICAgIntpbmR9ICAgICAgICBfci5oZWFkZXJzW1wiQWNjZXNzLUNvbnRy',
  'b2wtQWxsb3ctSGVhZGVyc1wiXSA9IFwiQ29udGVudC1UeXBlXCJcbiIKICAgICJ7aW5kfSAgICAg',
  'ICAgX3IuaGVhZGVyc1tcIkFjY2Vzcy1Db250cm9sLUFsbG93LU1ldGhvZHNcIl0gPSBcIlBPU1Qs',
  'IEdFVCwgT1BUSU9OU1wiXG4iCiAgICAie2luZH0gICAgICAgIHJldHVybiBfclxuIgogICAgIntp',
  'bmR9IyAtLS0gZW5kIENPUlMgcHJlZmxpZ2h0IHNoaW0gLS0tXG4iCikKCgpkZWYgbWFpbigpOgog',
  'ICAgYXAgPSBhcmdwYXJzZS5Bcmd1bWVudFBhcnNlcigpCiAgICBhcC5hZGRfYXJndW1lbnQoIi0t',
  'Y29kZS1kaXIiLCByZXF1aXJlZD1UcnVlLAogICAgICAgICAgICAgICAgICAgIGhlbHA9IkRpcmVj',
  'dG9yeSB0aGF0IGNvbnRhaW5zIHNhZF9tYXRjaF9zZXJ2ZXIucHkiKQogICAgYXAuYWRkX2FyZ3Vt',
  'ZW50KCItLWZpbGUiLCBkZWZhdWx0PSJzYWRfbWF0Y2hfc2VydmVyLnB5IiwKICAgICAgICAgICAg',
  'ICAgICAgICBoZWxwPSJUYXJnZXQgZmlsZW5hbWUgaW5zaWRlIC0tY29kZS1kaXIiKQogICAgYXAu',
  'YWRkX2FyZ3VtZW50KCItLXdyaXRlIiwgYWN0aW9uPSJzdG9yZV90cnVlIiwKICAgICAgICAgICAg',
  'ICAgICAgICBoZWxwPSJCYWNrIHVwIHRoZW4gd3JpdGUgaW4gcGxhY2UgKGRlZmF1bHQgaXMgZHJ5',
  'LXJ1bikiKQogICAgYXJncyA9IGFwLnBhcnNlX2FyZ3MoKQoKICAgIHRhcmdldCA9IG9zLnBhdGgu',
  'am9pbihhcmdzLmNvZGVfZGlyLCBhcmdzLmZpbGUpCiAgICBpZiBub3Qgb3MucGF0aC5leGlzdHMo',
  'dGFyZ2V0KToKICAgICAgICBwcmludCgiRVJST1I6IG5vdCBmb3VuZDogJXMiICUgdGFyZ2V0KQog',
  'ICAgICAgIHN5cy5leGl0KDIpCgogICAgd2l0aCBpby5vcGVuKHRhcmdldCwgInIiLCBlbmNvZGlu',
  'Zz0idXRmLTgiLCBuZXdsaW5lPSIiKSBhcyBmOgogICAgICAgIHMgPSBmLnJlYWQoKQoKICAgIGlm',
  'IE1BUktFUiBpbiBzOgogICAgICAgIHByaW50KCJBbHJlYWR5IHBhdGNoZWQ6ICVzIGFscmVhZHkg',
  'Y29udGFpbnMgdGhlIENPUlMgcHJlZmxpZ2h0IHNoaW0uIE5vIGNoYW5nZS4iCiAgICAgICAgICAg',
  'ICAgJSBhcmdzLmZpbGUpCiAgICAgICAgc3lzLmV4aXQoMCkKCiAgICBtYXRjaGVzID0gbGlzdChB',
  'TkNIT1IuZmluZGl0ZXIocykpCiAgICBpZiBsZW4obWF0Y2hlcykgIT0gMToKICAgICAgICBwcmlu',
  'dCgiRVJST1I6IGV4cGVjdGVkIGV4YWN0bHkgMSAnYXBwID0gRmxhc2soX19uYW1lX18pJyBsaW5l',
  'LCBmb3VuZCAlZC4iICUgbGVuKG1hdGNoZXMpKQogICAgICAgIGZvciBtIGluIG1hdGNoZXM6CiAg',
  'ICAgICAgICAgIGxuID0gcy5jb3VudCgiXG4iLCAwLCBtLnN0YXJ0KCkpICsgMQogICAgICAgICAg',
  'ICBwcmludCgiICAgbGluZSAlZDogJXMiICUgKGxuLCBtLmdyb3VwKDApLnN0cmlwKCkpKQogICAg',
  'ICAgIHByaW50KCIgICAgICAgQWJvcnRpbmcgcmF0aGVyIHRoYW4gZ3Vlc3NpbmcuIFBhc3RlIG1h',
  'a2VfYXBwIGlmIHRoaXMgaXMgd3JvbmcuIikKICAgICAgICBzeXMuZXhpdCgzKQoKICAgIG0gPSBt',
  'YXRjaGVzWzBdCiAgICBpbmRlbnQgPSBtLmdyb3VwKCJpbmRlbnQiKQogICAgYW5jaG9yX2xpbmUg',
  'PSBtLmdyb3VwKDApCiAgICBsbiA9IHMuY291bnQoIlxuIiwgMCwgbS5zdGFydCgpKSArIDEKCiAg',
  'ICAjIGluc2VydCByaWdodCBhZnRlciB0aGUgYW5jaG9yIGxpbmUgKGFmdGVyIGl0cyBuZXdsaW5l',
  'KQogICAgbGluZV9lbmQgPSBtLmVuZCgpCiAgICBpZiBsaW5lX2VuZCA8IGxlbihzKSBhbmQgc1ts',
  'aW5lX2VuZF0gPT0gIlxuIjoKICAgICAgICBsaW5lX2VuZCArPSAxCiAgICBzaGltID0gU0hJTV9U',
  'RU1QTEFURS5mb3JtYXQoaW5kPWluZGVudCkKICAgIG5ld19zID0gc1s6bGluZV9lbmRdICsgc2hp',
  'bSArIHNbbGluZV9lbmQ6XQoKICAgICMgY29tcGlsZSBndWFyZCBiZWZvcmUgd3JpdGluZwogICAg',
  'dHJ5OgogICAgICAgIGNvbXBpbGUobmV3X3MsIHRhcmdldCwgImV4ZWMiKQogICAgZXhjZXB0IFN5',
  'bnRheEVycm9yIGFzIGU6CiAgICAgICAgcHJpbnQoIkVSUk9SOiBwYXRjaGVkIHNvdXJjZSBmYWls',
  'ZWQgdG8gY29tcGlsZTogJXMiICUgZSkKICAgICAgICBzeXMuZXhpdCg0KQoKICAgIHByaW50KCJU',
  'YXJnZXQgICAgICA6ICVzIiAlIHRhcmdldCkKICAgIHByaW50KCJBbmNob3IgICAgICA6IGxpbmUg',
  'JWQgIC0+ICAlcyIgJSAobG4sIGFuY2hvcl9saW5lLnN0cmlwKCkpKQogICAgcHJpbnQoIkNvbXBp',
  'bGUgICAgIDogT0siKQogICAgcHJpbnQoIiIpCiAgICBwcmludCgiLS0tIGluc2VydGluZyBhZnRl',
  'ciB0aGUgYW5jaG9yIC0tLSIpCiAgICBwcmludCgiICAgICIgKyBhbmNob3JfbGluZS5zdHJpcCgp',
  'KQogICAgZm9yIGwgaW4gc2hpbS5yc3RyaXAoIlxuIikuc3BsaXRsaW5lcygpOgogICAgICAgIHBy',
  'aW50KCIgICsgIiArIGwpCiAgICBwcmludCgiIikKCiAgICBpZiBub3QgYXJncy53cml0ZToKICAg',
  'ICAgICBwcmludCgiRFJZLVJVTiBPTkxZLiBObyBmaWxlIHdyaXR0ZW4uIFJlLXJ1biB3aXRoIC0t',
  'd3JpdGUgdG8gYXBwbHksIHRoZW4iKQogICAgICAgIHByaW50KCJyZXN0YXJ0IHRoZSBtYXRjaCBz',
  'ZXJ2ZXIgKFdpbmRvdyAxKSBzbyBtYWtlX2FwcCByZWJ1aWxkcy4iKQogICAgICAgIHJldHVybgoK',
  'ICAgIHRzID0gZGF0ZXRpbWUuZGF0ZXRpbWUubm93KCkuc3RyZnRpbWUoIiVZJW0lZF8lSCVNJVMi',
  'KQogICAgYmFja3VwID0gIiVzLiVzLmJhayIgJSAodGFyZ2V0LCB0cykKICAgIHdpdGggaW8ub3Bl',
  'bihiYWNrdXAsICJ3IiwgZW5jb2Rpbmc9InV0Zi04IiwgbmV3bGluZT0iIikgYXMgZjoKICAgICAg',
  'ICBmLndyaXRlKHMpCiAgICB3aXRoIGlvLm9wZW4odGFyZ2V0LCAidyIsIGVuY29kaW5nPSJ1dGYt',
  'OCIsIG5ld2xpbmU9IiIpIGFzIGY6CiAgICAgICAgZi53cml0ZShuZXdfcykKICAgIHByaW50KCJC',
  'YWNrdXAgd3JpdHRlbjogJXMiICUgYmFja3VwKQogICAgcHJpbnQoIldyb3RlIGluIHBsYWNlOiAl',
  'cyIgJSB0YXJnZXQpCiAgICBwcmludCgiRG9uZS4gUkVTVEFSVCB0aGUgbWF0Y2ggc2VydmVyIChD',
  'dHJsK0MgaW4gV2luZG93IDEsIHJlLXJ1biBpdCksIHRoZW4gcmVsb2FkIHRoZSBwYWdlLiIpCgoK',
  'aWYgX19uYW1lX18gPT0gIl9fbWFpbl9fIjoKICAgIG1haW4oKQo='
) -join ''
[IO.File]::WriteAllBytes($script, [Convert]::FromBase64String($b64))
Write-Host "wrote $script"

# --- DRY RUN first (no changes) ---
Write-Host "`n=== DRY RUN ===" -ForegroundColor Cyan
& $bat $script --code-dir $code

Write-Host "`nIf it shows 'Compile : OK' and the shim landing right after" -ForegroundColor Yellow
Write-Host "app = Flask(__name__), re-run the SAME command with  --write  appended." -ForegroundColor Yellow
Write-Host "THEN: Ctrl+C the match server in Window 1 and re-run it (make_app must rebuild)." -ForegroundColor Yellow
