/* viewer_modules.js
 * Spatial integration of Module 20 (jobs), 21 (transit LOS) and 22 (environment)
 * into the SAD viewer. Each is drawn as a projected layer in the viewer's own
 * d3 space via the window.sadAfterRender hook, so the layers pan/zoom/crop and
 * redraw with everything else.
 *
 * Controls per layer: on/off toggle, a LEGEND (jobs quantile bins in jobs/acre,
 * the surface-heat C gradient, the transit mode key), an OPACITY slider, and
 * HOVER TOOLTIPS that read out the underlying statistics for each block / heat
 * cell / stop. Jobs also gets a YEAR SLIDER (+ play) over its LODES history.
 *
 * Non-invasive: reads GeoJSON by convention from ../<sad_id>/derived/... and
 * relies only on hooks exposed by viewer.js (sadViewerState / sadViewerRender /
 * sadAfterRender). SADs missing a layer simply disable that toggle.
 */
(function () {
  'use strict';

  var d3 = window.d3;
  var state = {
    on: { jobs: false, transit: false, heat: false },
    opacity: { jobs: 0.62, transit: 0.85, heat: 0.55 },
    year: null
  };
  var cache = {};
  var lastSad = null;
  var playTimer = null;
  var tip = null;

  // Jobs density uses a continuous, perceptually-ordered scale (viridis: dark
  // = few jobs/acre, bright = many) on a domain held FIXED across all years,
  // so colors are comparable year-to-year and the slider shows real change.
  var JOBS_INTERP = d3.interpolateViridis || d3.interpolateInferno;
  function jobsColorScale(maxPerAcre) {
    return d3.scaleSequentialSqrt(JOBS_INTERP).domain([0, maxPerAcre || 1]);
  }
  function jobsGradientCss() {
    var stops = [0, 0.25, 0.5, 0.75, 1].map(function (t) { return JOBS_INTERP(t); });
    return 'linear-gradient(90deg,' + stops.join(',') + ')';
  }
  var MODE_COLOR = {
    bus: '#5b9bd5', tram: '#cdbb4f', subway: '#e8743b', rail: '#e8743b',
    ferry: '#4f9d69', cable: '#9aa0a8', monorail: '#cdbb4f', other: '#9aa0a8'
  };

  // ─── data ────────────────────────────────────────────────────────────────
  function url(sadId, rel) { return '../' + sadId + '/' + rel; }
  function getJSON(u) {
    return fetch(u).then(function (r) { return r.ok ? r.json() : null; })
                   .catch(function () { return null; });
  }
  function currentSad() { var s = document.getElementById('sad-select'); return s && s.value; }
  function redraw() { if (window.sadViewerRender) window.sadViewerRender(); }

  function loadSad(sadId) {
    if (cache[sadId]) { afterLoad(sadId); return; }
    Promise.all([
      getJSON(url(sadId, 'derived/jobs/jobs_blocks.geojson')),
      getJSON(url(sadId, 'derived/jobs/jobs_timeseries.json')),
      getJSON(url(sadId, 'derived/transit_los/transit_los_stops.geojson')),
      getJSON(url(sadId, 'derived/environment/heat_grid.geojson')),
      getJSON(url(sadId, 'derived/jobs/jobs_summary.json')),
      getJSON(url(sadId, 'derived/transit_los/transit_los_summary.json')),
      getJSON(url(sadId, 'derived/environment/environment_summary.json'))
    ]).then(function (r) {
      cache[sadId] = {
        jobsGeo: r[0], ts: r[1], transitGeo: r[2], heatGeo: r[3],
        jobsSum: r[4], transitSum: r[5], envSum: r[6], years: jobsYears(r[0]),
        jobsMaxPerAcre: jobsDomain(r[0])
      };
      afterLoad(sadId);
    });
  }

  function jobsDomain(geo) {
    // 98th-percentile jobs/acre across ALL years -> a stable color ceiling.
    if (!geo || !geo.features) return 1;
    var ys = jobsYears(geo), vals = [];
    geo.features.forEach(function (f) {
      var p = f.properties, ac = +p.acres || 0;
      if (ac <= 0) return;
      if (ys.length) ys.forEach(function (y) {
        var v = (+p['jobs_' + y] || 0) / ac; if (v > 0) vals.push(v);
      });
      else { var v0 = (+p.jobs || 0) / ac; if (v0 > 0) vals.push(v0); }
    });
    if (!vals.length) return 1;
    vals.sort(function (a, b) { return a - b; });
    return vals[Math.floor(0.98 * (vals.length - 1))] || vals[vals.length - 1];
  }
  function jobsYears(geo) {
    if (!geo || !geo.features || !geo.features.length) return [];
    var p = geo.features[0].properties || {};
    return Object.keys(p).filter(function (k) { return /^jobs_\d{4}$/.test(k); })
            .map(function (k) { return +k.slice(5); }).sort(function (a, b) { return a - b; });
  }
  function afterLoad(sadId) {
    var c = cache[sadId];
    state.year = c.years.length ? c.years[c.years.length - 1] : null;
    stopPlay();
    buildPanel(sadId);
    redraw();
  }

  // ─── tooltip ───────────────────────────────────────────────────────────────
  function ensureTip() {
    if (tip) return tip;
    tip = document.createElement('div');
    tip.id = 'sf-tip';
    document.body.appendChild(tip);
    return tip;
  }
  function showTip(html, ev) {
    var t = ensureTip();
    t.innerHTML = html;
    t.style.display = 'block';
    t.style.left = (ev.clientX + 14) + 'px';
    t.style.top = (ev.clientY + 14) + 'px';
  }
  function hideTip() { if (tip) tip.style.display = 'none'; }
  function bindHover(sel, htmlFn) {
    sel.on('mousemove', function (ev) { showTip(htmlFn(), ev); })
       .on('mouseleave', hideTip);
  }

  // ─── formatting ──────────────────────────────────────────────────────────
  function fmt(n) { return (n == null) ? '\u2014' : Number(n).toLocaleString(); }
  function r1(n) { return Math.round(n * 10) / 10; }

  // ─── drawing ───────────────────────────────────────────────────────────────
  function jobValue(p, year) {
    if (year != null && p['jobs_' + year] != null) return +p['jobs_' + year];
    return +(p.jobs || 0);
  }
  function drawJobs(g, c, pathGen) {
    var year = state.year, op = state.opacity.jobs;
    var scale = jobsColorScale(c.jobsMaxPerAcre);
    var gg = g.append('g').attr('id', 'm_jobs');
    c.jobsGeo.features.forEach(function (f) {
      if (!f.geometry) return;
      var d = pathGen(f); if (!d) return;
      var p = f.properties, ac = +p.acres || 0;
      var jv = jobValue(p, year), pa = ac > 0 ? jv / ac : 0;
      if (pa <= 0) return;                       // no jobs that year -> not drawn
      var ext = (p.zone === 'exterior');
      var s = gg.append('path').attr('d', d).attr('fill', scale(pa))
        .attr('fill-opacity', ext ? op * 0.4 : op)
        .attr('stroke', '#0a0a0a').attr('stroke-opacity', 0.18).attr('stroke-width', 0.3);
      bindHover(s, function () {
        return '<b>' + fmt(Math.round(jv)) + ' jobs</b> \u00b7 ' + (year || '') +
          '<br>' + r1(pa) + ' jobs/acre<br><span class="sf-tip-dim">' +
          p.zone + ' \u00b7 ' + r1(ac) + ' acres</span>';
      });
    });
    legendJobs(c.jobsMaxPerAcre, year);
  }

  function drawHeat(g, c, pathGen) {
    var op = state.opacity.heat;
    var feats = c.heatGeo.features.filter(function (f) { return f.geometry; });
    var vals = feats.map(function (f) { return +f.properties.lst_c; }).filter(isFinite);
    if (!vals.length) return;
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    var scale = d3.scaleSequential(d3.interpolateInferno).domain([lo, hi || lo + 1]);
    var gg = g.append('g').attr('id', 'm_heat');
    feats.forEach(function (f) {
      var d = pathGen(f); if (!d) return;
      var v = +f.properties.lst_c;
      var s = gg.append('path').attr('d', d).attr('fill', scale(v))
        .attr('fill-opacity', op).attr('stroke', 'none');
      bindHover(s, function () {
        return '<b>' + r1(v) + '\u00b0C</b><br><span class="sf-tip-dim">surface temp</span>';
      });
    });
    legendHeat(lo, hi);
  }

  function drawTransit(g, c, proj) {
    var op = state.opacity.transit;
    var feats = c.transitGeo.features.filter(function (f) {
      return f.geometry && f.geometry.type === 'Point';
    });
    var maxDep = 1, modes = {};
    feats.forEach(function (f) {
      maxDep = Math.max(maxDep, +f.properties.departures || 0);
      modes[f.properties.mode || 'other'] = true;
    });
    var gg = g.append('g').attr('id', 'm_transit');
    feats.forEach(function (f) {
      var xy = proj(f.geometry.coordinates); if (!xy) return;
      var p = f.properties, dep = +p.departures || 0;
      var rad = 2.5 + 9 * Math.sqrt(dep / maxDep);
      var s = gg.append('circle').attr('cx', xy[0]).attr('cy', xy[1]).attr('r', rad)
        .attr('fill', MODE_COLOR[p.mode] || MODE_COLOR.other)
        .attr('fill-opacity', op).attr('stroke', '#0a0a0a').attr('stroke-width', 0.5);
      bindHover(s, function () {
        return '<b>' + (p.stop_name || p.stop_id) + '</b><br>' +
          fmt(dep) + ' departures/day<br><span class="sf-tip-dim">' +
          (p.mode || 'other') + ' \u00b7 ' + (p.catchment || '') + '</span>';
      });
    });
    legendTransit(Object.keys(modes));
  }

  window.sadAfterRender = function (vstate, _d3, root) {
    var c = cache[vstate.currentSadId];
    if (!c || !vstate.pathGen) return;
    root.select('#m_layers').remove();
    var g = root.append('g').attr('id', 'm_layers');
    if (state.on.heat && c.heatGeo) drawHeat(g, c, vstate.pathGen);
    if (state.on.jobs && c.jobsGeo) drawJobs(g, c, vstate.pathGen);
    if (state.on.transit && c.transitGeo) drawTransit(g, c, vstate.projection);
  };

  // ─── legends (write into sidebar placeholders) ─────────────────────────────
  function setLegend(key, html) {
    var el = document.getElementById('sf-legend-' + key);
    if (el) el.innerHTML = html;
  }
  function legendJobs(maxPerAcre, year) {
    setLegend('jobs', '<div class="sf-leg-title">jobs / acre' +
      (year ? ' \u00b7 ' + year : '') + '</div>' +
      '<div class="sf-grad" style="background:' + jobsGradientCss() + '"></div>' +
      '<div class="sf-grad-ax"><span>0</span><span>' + r1(maxPerAcre) + '</span></div>' +
      '<div class="sf-leg-note">scale fixed across years</div>');
  }
  function legendHeat(lo, hi) {
    setLegend('heat',
      '<div class="sf-leg-title">surface temp \u00b0C</div>' +
      '<div class="sf-grad sf-grad-heat"></div>' +
      '<div class="sf-grad-ax"><span>' + r1(lo) + '</span><span>' + r1(hi) + '</span></div>');
  }
  function legendTransit(modes) {
    var key = modes.map(function (m) {
      return '<span class="sf-leg-inline"><span class="sf-chip" style="background:' +
        (MODE_COLOR[m] || MODE_COLOR.other) + '"></span>' + m + '</span>';
    }).join('');
    setLegend('transit', '<div class="sf-leg-title">mode</div>' + key +
      '<div class="sf-leg-note">circle size = departures/day</div>');
  }

  // ─── sidebar panel ─────────────────────────────────────────────────────────
  function metricLine(label, value) {
    return '<div class="sf-metric"><span>' + label + '</span><b>' + value + '</b></div>';
  }
  function jobsLabel(c) {
    if (!c || !c.ts || !c.ts.series || state.year == null) return '';
    var row = c.ts.series.filter(function (s) { return s.year === state.year; })[0];
    return state.year + (row && row.jobs_inside != null ?
      ' \u00b7 ' + fmt(row.jobs_inside) + ' jobs' : '');
  }

  function layerCtl(key, label, swatch, available) {
    var checked = state.on[key] ? ' checked' : '';
    var shown = (state.on[key] && available) ? '' : ' style="display:none"';
    return '<li class="sf-tog' + (available ? '' : ' off') + '">' +
        '<input type="checkbox" id="sf-tog-' + key + '"' + checked +
        (available ? '' : ' disabled') + '>' + swatch +
        '<label for="sf-tog-' + key + '">' + label + '</label></li>' +
      '<div class="sf-ctl" id="sf-ctl-' + key + '"' + shown + '>' +
        '<div class="sf-legend" id="sf-legend-' + key + '"></div>' +
        '<div class="sf-op"><span>opacity</span>' +
        '<input type="range" id="sf-op-' + key + '" min="0.1" max="1" step="0.05" value="' +
        state.opacity[key] + '"></div>' +
      '</div>';
  }

  function buildPanel(sadId) {
    var c = cache[sadId] || {};
    var sb = document.querySelector('.sidebar'); if (!sb) return;
    var sec = document.getElementById('sf-data-section');
    if (!sec) {
      sec = document.createElement('section'); sec.id = 'sf-data-section';
      sb.insertBefore(sec, sb.firstElementChild);
    }
    var hasJobs = !!(c.jobsGeo && c.jobsGeo.features && c.jobsGeo.features.length);
    var hasTransit = !!(c.transitGeo && c.transitGeo.features && c.transitGeo.features.length);
    var hasHeat = !!(c.heatGeo && c.heatGeo.features && c.heatGeo.features.length);

    var jobsSw = '<span class="sf-sw" style="background:' + jobsGradientCss() + '"></span>';
    var heatSw = '<span class="sf-sw sf-sw-heat"></span>';
    var transitSw = '<span class="sf-sw" style="background:' + MODE_COLOR.bus + '"></span>';

    var html = '<h2>District data</h2>';
    if (!hasJobs && !hasTransit && !hasHeat) {
      html += '<div class="sf-empty">No spatial layers for this district yet. ' +
              'Run modules 20\u201322 (use --timeseries on M20 for the year slider).</div>';
      sec.innerHTML = html; return;
    }
    html += '<ul class="layer-list sf-layers">' +
      layerCtl('jobs', 'Jobs density', jobsSw, hasJobs) +
      layerCtl('transit', 'Transit service', transitSw, hasTransit) +
      layerCtl('heat', 'Surface heat', heatSw, hasHeat) + '</ul>';

    if (hasJobs && c.years && c.years.length > 1) {
      html += '<div class="sf-slider' + (state.on.jobs ? '' : ' dim') + '">' +
        '<div class="sf-slider-head"><span>Jobs year</span>' +
        '<b id="sf-year-label">' + jobsLabel(c) + '</b></div>' +
        '<div class="sf-slider-row"><button id="sf-play" title="Play through the years">\u25b6</button>' +
        '<input type="range" id="sf-year" min="' + c.years[0] + '" max="' +
        c.years[c.years.length - 1] + '" step="1" value="' +
        (state.year || c.years[c.years.length - 1]) + '"></div></div>';
    }

    var m = '';
    if (c.jobsSum) m += metricLine('Jobs (latest)', fmt(c.jobsSum.jobs_inside) +
      (c.jobsSum.jobs_per_resident != null ? ' \u00b7 ' + c.jobsSum.jobs_per_resident + '/res' : ''));
    if (c.transitSum) m += metricLine('Transit', fmt(c.transitSum.trips_per_day) +
      ' trips/day \u00b7 ' + (c.transitSum.routes_serving || 0) + ' routes');
    if (c.envSum) m += metricLine('Surface heat', (c.envSum.mean_summer_lst_c != null ?
      c.envSum.mean_summer_lst_c + '\u00b0C' : '\u2014') +
      (c.envSum.built_up_pct != null ? ' \u00b7 ' + c.envSum.built_up_pct + '% built' : ''));
    if (m) html += '<div class="sf-metrics">' + m + '</div>';

    sec.innerHTML = html;
    wirePanel(sadId);
  }

  function wirePanel(sadId) {
    ['jobs', 'transit', 'heat'].forEach(function (k) {
      var cb = document.getElementById('sf-tog-' + k);
      if (cb) cb.addEventListener('change', function () {
        state.on[k] = cb.checked;
        var ctl = document.getElementById('sf-ctl-' + k);
        if (ctl) ctl.style.display = cb.checked ? '' : 'none';
        if (k === 'jobs') {
          var sl = document.querySelector('.sf-slider');
          if (sl) sl.classList.toggle('dim', !cb.checked);
        }
        if (!cb.checked) hideTip();
        redraw();
      });
      var op = document.getElementById('sf-op-' + k);
      if (op) op.addEventListener('input', function () {
        state.opacity[k] = +op.value; redraw();
      });
    });
    var yr = document.getElementById('sf-year');
    if (yr) yr.addEventListener('input', function () {
      state.year = +yr.value;
      var lab = document.getElementById('sf-year-label');
      if (lab) lab.textContent = jobsLabel(cache[sadId]);
      redraw();
    });
    var play = document.getElementById('sf-play');
    if (play) play.addEventListener('click', function () {
      if (playTimer) stopPlay(); else startPlay(sadId);
    });
  }

  function startPlay(sadId) {
    var c = cache[sadId]; if (!c || !c.years.length) return;
    if (!state.on.jobs) {
      state.on.jobs = true;
      var cb = document.getElementById('sf-tog-jobs'); if (cb) cb.checked = true;
      var ctl = document.getElementById('sf-ctl-jobs'); if (ctl) ctl.style.display = '';
      var sl = document.querySelector('.sf-slider'); if (sl) sl.classList.remove('dim');
    }
    var play = document.getElementById('sf-play'); if (play) play.textContent = '\u275a\u275a';
    playTimer = setInterval(function () {
      var ys = c.years, i = ys.indexOf(state.year);
      state.year = ys[(i + 1) % ys.length];
      var yr = document.getElementById('sf-year'); if (yr) yr.value = state.year;
      var lab = document.getElementById('sf-year-label'); if (lab) lab.textContent = jobsLabel(c);
      redraw();
    }, 800);
  }
  function stopPlay() {
    if (playTimer) { clearInterval(playTimer); playTimer = null; }
    var play = document.getElementById('sf-play'); if (play) play.textContent = '\u25b6';
  }

  // ─── styles ──────────────────────────────────────────────────────────────
  function injectStyles() {
    if (document.getElementById('sf-modules-style')) return;
    var s = document.createElement('style'); s.id = 'sf-modules-style';
    s.textContent =
      '.sf-layers{margin:0 0 4px;}' +
      '.sf-tog.off{opacity:.4;}' +
      '.sf-sw{display:inline-block;width:22px;height:10px;border:1px solid var(--line);' +
      'vertical-align:middle;margin:0 6px 0 0;}' +
      '.sf-sw-heat{background:linear-gradient(90deg,#1b0c41,#b5367a,#fb9b06,#f7e03c);}' +
      '.sf-ctl{margin:2px 0 8px 28px;padding-left:8px;border-left:1px solid var(--line);}' +
      '.sf-legend{margin:2px 0 6px;}' +
      '.sf-leg-title{font-size:9px;text-transform:uppercase;letter-spacing:.07em;' +
      'color:var(--ink-faint);margin-bottom:3px;}' +
      '.sf-leg-row{display:flex;align-items:center;font-size:10px;color:var(--ink-soft);' +
      'font-family:var(--mono);line-height:1.6;}' +
      '.sf-leg-inline{display:inline-flex;align-items:center;margin-right:10px;font-size:10px;' +
      'color:var(--ink-soft);}' +
      '.sf-chip{display:inline-block;width:11px;height:11px;margin-right:5px;border:1px solid var(--line);}' +
      '.sf-leg-note{font-size:9px;color:var(--ink-faint);margin-top:3px;}' +
      '.sf-grad{height:9px;border:1px solid var(--line);}' +
      '.sf-grad-heat{height:9px;background:linear-gradient(90deg,#1b0c41,#4a0c6b,#781c6d,' +
      '#a52c60,#cf4446,#ed6925,#fb9b06,#f7d03c);border:1px solid var(--line);}' +
      '.sf-grad-ax{display:flex;justify-content:space-between;font-size:9px;' +
      'font-family:var(--mono);color:var(--ink-faint);margin-top:2px;}' +
      '.sf-op{display:flex;align-items:center;gap:6px;font-size:9px;text-transform:uppercase;' +
      'letter-spacing:.06em;color:var(--ink-faint);}' +
      '.sf-op input[type=range]{flex:1;}' +
      '.sf-slider{margin:8px 0 4px;}.sf-slider.dim{opacity:.45;}' +
      '.sf-slider-head{display:flex;justify-content:space-between;font-size:10px;' +
      'text-transform:uppercase;letter-spacing:.08em;color:var(--ink-faint);margin-bottom:3px;}' +
      '.sf-slider-head b{font-family:var(--mono);text-transform:none;letter-spacing:0;color:var(--ink);}' +
      '.sf-slider-row{display:flex;align-items:center;gap:6px;}' +
      '.sf-slider-row input[type=range]{flex:1;}' +
      '#sf-play{font-size:10px;line-height:1;padding:3px 7px;border:1px solid var(--line);' +
      'background:var(--bg);color:var(--ink);cursor:pointer;}#sf-play:hover{border-color:var(--ink);}' +
      '.sf-metrics{margin-top:8px;border-top:1px solid var(--line-faint);padding-top:6px;}' +
      '.sf-metric{display:flex;justify-content:space-between;gap:8px;font-size:11px;padding:2px 0;}' +
      '.sf-metric span{color:var(--ink-soft);}' +
      '.sf-metric b{font-family:var(--mono);font-weight:600;color:var(--ink);text-align:right;}' +
      '.sf-empty{font-size:11px;color:var(--ink-faint);line-height:1.5;}' +
      '#sf-tip{position:fixed;z-index:9999;display:none;pointer-events:none;' +
      'background:#0a0a0a;color:#fff;font:11px/1.45 var(--sans);padding:6px 9px;' +
      'border:1px solid #333;max-width:220px;}' +
      '#sf-tip b{font-weight:600;}#sf-tip .sf-tip-dim{color:#9a9a9a;}';
    document.head.appendChild(s);
  }

  function tick() {
    var s = currentSad();
    if (s && s !== lastSad) { lastSad = s; loadSad(s); }
  }
  function init() {
    injectStyles();
    var sel = document.getElementById('sad-select');
    if (sel) sel.addEventListener('change', function () { setTimeout(tick, 30); });
    setInterval(tick, 400);
    tick();
  }
  if (document.readyState !== 'loading') init();
  else document.addEventListener('DOMContentLoaded', init);
})();
