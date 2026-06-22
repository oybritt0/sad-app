/* viewer_integration.js
 * Bridges the district viewer (/_ui/) into the Synthesis navigation: a shared
 * Map / Field / Viewer switcher, and ?sad= deep-link handling so links from the
 * map land on the right district. Non-invasive — touches only the public DOM.
 *
 * AUTO-INSTALL: place this file in code/viewer/ and re-run build_ui_manifest;
 * it copies the file into data/_ui/ and injects the <script> tag for you.
 */
(function () {
  'use strict';
  var MAP = '../_compare_ui/map.html';
  var FIELD = '../_compare_ui/';

  // Always fetch a fresh manifest.json — saving a district from the map
  // rebuilds it, and we don't want a stale browser-cached copy on viewer open.
  var _fetch = window.fetch;
  window.fetch = function (u, o) {
    try {
      if (typeof u === 'string' && /(^|\/)manifest\.json($|\?)/.test(u)) {
        u += (u.indexOf('?') >= 0 ? '&' : '?') + 't=' + Date.now();
      }
    } catch (e) {}
    return _fetch.call(this, u, o);
  };

  function injectStyles() {
    if (document.getElementById('sf-nav-style')) return;
    var s = document.createElement('style');
    s.id = 'sf-nav-style';
    s.textContent =
      '.sf-nav{display:inline-flex;gap:2px;border:1px solid rgba(0,0,0,0.12);' +
      'border-radius:100px;padding:3px;background:rgba(255,255,255,0.55);}' +
      '.sf-nav a{font:500 11px/1 ui-sans-serif,system-ui,sans-serif;letter-spacing:.05em;' +
      'text-transform:uppercase;padding:7px 15px;border-radius:100px;color:#5a534a;' +
      'text-decoration:none;transition:background .18s,color .18s;}' +
      '.sf-nav a:hover{color:#1b1813;}' +
      '.sf-nav a.on{background:#1b1813;color:#fff;}' +
      '.sf-act{display:inline-flex;gap:6px;margin-left:8px;}' +
      '.sf-act button{font:500 11px/1 ui-sans-serif,system-ui,sans-serif;letter-spacing:.04em;' +
      'text-transform:uppercase;padding:7px 11px;border-radius:100px;cursor:pointer;' +
      'border:1px solid rgba(0,0,0,0.12);background:rgba(255,255,255,0.55);color:#5a534a;transition:all .18s;}' +
      '.sf-act button:hover{color:#1b1813;border-color:rgba(0,0,0,0.28);}' +
      '.sf-act button.danger:hover{color:#c0392b;border-color:#c0392b;}';
    document.head.appendChild(s);
  }

  function addNav() {
    var host = document.querySelector('.topbar-actions') ||
               document.querySelector('.topbar') ||
               document.querySelector('header');
    if (!host || document.getElementById('sf-nav')) return;
    injectStyles();
    var nav = document.createElement('nav');
    nav.id = 'sf-nav';
    nav.className = 'sf-nav';
    nav.innerHTML =
      '<a href="' + MAP + '">Map</a>' +
      '<a href="' + FIELD + '">Field</a>' +
      '<a class="on" href="#" onclick="return false">Viewer</a>';
    host.insertBefore(nav, host.firstChild);
    addActions(host, nav);
  }

  var MATCH_API = 'http://localhost:8000';
  function addActions(host, nav) {
    if (document.getElementById('sf-act')) return;
    var act = document.createElement('span');
    act.id = 'sf-act'; act.className = 'sf-act';
    act.innerHTML = '<button id="sf-refresh" title="Reload the district list">\u21bb Refresh</button>' +
      '<button id="sf-delete" class="danger" title="Delete the selected map-created district">\u2715 Delete</button>';
    nav.parentNode.insertBefore(act, nav.nextSibling);
    document.getElementById('sf-refresh').addEventListener('click', function () { location.reload(); });
    document.getElementById('sf-delete').addEventListener('click', function () {
      var sel = document.getElementById('sad-select');
      var id = sel && sel.value;
      if (!id) return;
      if (!window.confirm('Delete "' + id + '"? This removes its saved data folder and cannot be undone. (Only map-created districts can be deleted.)')) return;
      fetch(MATCH_API + '/delete_district', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sad_id: id })
      }).then(function (r) { return r.json(); }).then(function (d) {
        if (d && d.ok) { location.reload(); return; }
        // Partial delete: boundary removed + manifest rebuilt, but some files
        // were locked. Warn, then still reload so the list reflects the removal.
        if (d && d.viewer_rebuilt) { window.alert((d && d.error) || 'Partially deleted.'); location.reload(); return; }
        window.alert('Could not delete: ' + ((d && d.error) || 'unknown error'));
      }).catch(function () { window.alert('Could not reach the match server to delete.'); });
    });
  }

  function applyDeepLink() {
    var sad = new URLSearchParams(location.search).get('sad');
    if (!sad) return;
    var tries = 0;
    var iv = setInterval(function () {
      tries++;
      var sel = document.getElementById('sad-select');
      if (sel && sel.options.length) {
        var match = null;
        for (var i = 0; i < sel.options.length; i++) {
          if (sel.options[i].value === sad) { match = sel.options[i]; break; }
        }
        if (!match) {
          for (var j = 0; j < sel.options.length; j++) {
            var o = sel.options[j];
            if ((o.value && (sad.indexOf(o.value) >= 0 || o.value.indexOf(sad) >= 0)) ||
                (o.text && o.text.indexOf(sad) >= 0)) { match = o; break; }
          }
        }
        if (match) {
          sel.value = match.value;
          sel.dispatchEvent(new Event('change', { bubbles: true }));
        }
        clearInterval(iv);
      }
      if (tries > 60) clearInterval(iv);
    }, 150);
  }

  function init() { addNav(); applyDeepLink(); }
  if (document.readyState !== 'loading') init();
  else document.addEventListener('DOMContentLoaded', init);
})();
