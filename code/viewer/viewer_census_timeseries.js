/* viewer_census_timeseries.js
 *
 * Census-over-time module for the SAD viewer. Adds:
 *   1. A "Census over time" sidebar section with metric dropdown,
 *      year slider with play, big-number readout, mini time-series chart.
 *   2. A spatial choropleth layer drawn into the d3 viewer canvas (matches
 *      the jobs/transit/heat pattern from viewer_modules.js). Toggleable.
 *      Block-group polygons colored by the selected metric, recomputed
 *      across all years so colors are comparable year-to-year.
 *
 * Reads from
 *   ../<sad_id>/derived/census_timeseries.json
 *   ../<sad_id>/source/census_blockgroups_2010.geojson  (2013-2019)
 *   ../<sad_id>/source/census_blockgroups_2020.geojson  (2020+)
 *
 * The GeoJSON files are produced by running
 *   python module_4b_export_bg_geojson.py <data_dir>
 * after M4b.
 */
(function () {
  'use strict';

  var d3 = window.d3;

  var state = {
    metric: 'income',
    year: null,
    showLayer: false
  };
  var cache = {};
  var lastSad = null;
  var playTimer = null;
  var milestones = null;   // loaded lazily from ../_shared/sad_milestones.json
  var milestoneCache = {}; // sadId -> matched milestone or null

  // ─── metric catalog ──────────────────────────────────────────────────────
  var METRICS = [
    { group: 'Economic',         key: 'income',   label: 'Median household income',
      sumField: 'median_household_income_pop_weighted', bgField: 'median_household_income', fmt: 'currency' },
    { group: 'Economic',         key: 'home',     label: 'Median home value',
      sumField: 'median_home_value_pop_weighted', bgField: 'median_home_value', fmt: 'currency' },
    { group: 'Economic',         key: 'rent',     label: 'Median gross rent',
      sumField: 'median_gross_rent_pop_weighted', bgField: 'median_gross_rent', fmt: 'currency' },
    { group: 'Economic',         key: 'unemp',    label: 'Unemployment rate',
      sumField: 'unemployment_rate',
      bgDerive: function (bg) { return ratio(bg.unemployed_count, bg.labor_force) * 100; }, fmt: 'percent' },
    { group: 'Economic',         key: 'renter',   label: 'Renter share',
      sumField: 'pct_renter_occupied',
      bgDerive: function (bg) { return ratio(bg.renter_occupied_units, bg.total_housing_units) * 100; }, fmt: 'percent' },
    { group: 'Economic',         key: 'owner',    label: 'Owner share',
      sumField: 'pct_owner_occupied',
      bgDerive: function (bg) { return ratio(bg.owner_occupied_units, bg.total_housing_units) * 100; }, fmt: 'percent' },
    { group: 'Demographic',      key: 'pop',      label: 'Population',
      sumField: 'estimated_population', bgField: 'total_population', fmt: 'count' },
    { group: 'Demographic',      key: 'age',      label: 'Median age',
      sumField: 'median_age_pop_weighted', bgField: 'median_age', fmt: 'decimal' },
    { group: 'Demographic',      key: 'educ',     label: "Bachelor's or higher",
      sumField: 'pct_bachelors_or_higher',
      bgDerive: function (bg) {
        var n = (+bg.bachelors_25plus || 0) + (+bg.masters_25plus || 0) +
                (+bg.prof_degree_25plus || 0) + (+bg.doctorate_25plus || 0);
        return ratio(n, bg.pop_25plus) * 100;
      }, fmt: 'percent' },
    { group: 'Race / Ethnicity', key: 'white',    label: '% White',
      sumField: 'pct_white',
      bgDerive: function (bg) { return ratio(bg.white_alone, bg.total_pop_race) * 100; }, fmt: 'percent' },
    { group: 'Race / Ethnicity', key: 'black',    label: '% Black',
      sumField: 'pct_black',
      bgDerive: function (bg) { return ratio(bg.black_alone, bg.total_pop_race) * 100; }, fmt: 'percent' },
    { group: 'Race / Ethnicity', key: 'asian',    label: '% Asian',
      sumField: 'pct_asian',
      bgDerive: function (bg) { return ratio(bg.asian_alone, bg.total_pop_race) * 100; }, fmt: 'percent' },
    { group: 'Race / Ethnicity', key: 'hispanic', label: '% Hispanic',
      sumField: 'pct_hispanic',
      bgDerive: function (bg) { return ratio(bg.hispanic_or_latino, bg.total_pop_race) * 100; }, fmt: 'percent' }
  ];

  function ratio(a, b) {
    a = +a; b = +b;
    if (!isFinite(a) || !isFinite(b) || b <= 0) return null;
    return a / b;
  }
  function metricByKey(k) {
    for (var i = 0; i < METRICS.length; i++) if (METRICS[i].key === k) return METRICS[i];
    return METRICS[0];
  }
  function fmtValue(v, kind) {
    if (v == null || !isFinite(+v)) return '\u2014';
    var n = +v;
    if (kind === 'currency') return '$' + Math.round(n).toLocaleString();
    if (kind === 'percent') return n.toFixed(1) + '%';
    if (kind === 'count') return Math.round(n).toLocaleString();
    if (kind === 'decimal') return n.toFixed(1);
    return String(n);
  }
  function bgValue(bg, metric) {
    if (!bg) return null;
    if (metric.bgField && bg[metric.bgField] != null) return +bg[metric.bgField];
    if (metric.bgDerive) { try { return metric.bgDerive(bg); } catch (e) { return null; } }
    return null;
  }

  // ─── color scale (5-step blue, quintile breaks across all years) ─────────
  var PALETTE = ['#eaf3fb', '#c6dbef', '#9ecae1', '#4292c6', '#08519c'];

  function computeBreaks(c, metric) {
    var key = metric.key;
    if (c.breaks[key]) return c.breaks[key];
    var values = [];
    if (!c.ts) return null;
    Object.keys(c.ts.block_groups || {}).forEach(function (year) {
      (c.ts.block_groups[year] || []).forEach(function (bg) {
        var v = bgValue(bg, metric);
        if (v != null && isFinite(v)) values.push(v);
      });
    });
    if (values.length < 5) return null;
    values.sort(function (a, b) { return a - b; });
    var q = function (p) {
      var i = Math.min(values.length - 1, Math.floor(p * values.length));
      return values[i];
    };
    c.breaks[key] = [q(0.2), q(0.4), q(0.6), q(0.8)];
    return c.breaks[key];
  }
  function colorFor(value, breaks) {
    if (value == null || !isFinite(value) || !breaks) return null;
    var i = 0;
    while (i < breaks.length && value > breaks[i]) i++;
    return PALETTE[i];
  }

  // ─── data ────────────────────────────────────────────────────────────────
  function url(sadId, rel) { return '../' + sadId + '/' + rel; }
  function getJSON(u) {
    return fetch(u).then(function (r) { return r.ok ? r.json() : null; })
                   .catch(function () { return null; });
  }
  function currentSad() { var s = document.getElementById('sad-select'); return s && s.value; }

  // ─── milestone matching ──────────────────────────────────────────────────
  function norm(s) {
    return String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').replace(/\s+/g, ' ').trim();
  }
  function currentSadName() {
    var sel = document.getElementById('sad-select');
    if (!sel) return '';
    var opt = sel.options && sel.options[sel.selectedIndex];
    return (opt && opt.textContent) || sel.value || '';
  }
  function matchMilestone(sadId, sadName) {
    if (milestoneCache[sadId] !== undefined) return milestoneCache[sadId];
    if (!milestones || !milestones.length) { milestoneCache[sadId] = null; return null; }
    var sn = norm(sadName), sid = norm(sadId);
    var best = null;
    for (var i = 0; i < milestones.length; i++) {
      var m = milestones[i];
      if (m.sad_id && norm(m.sad_id) === norm(sadId)) { best = m; break; }
      var dn = norm(m.districtName);
      if (!dn) continue;
      if (sn.indexOf(dn) >= 0 || sid.indexOf(dn) >= 0 || dn.indexOf(sn) >= 0) {
        var ct = norm(m.city);
        if (!best || (ct && (sn.indexOf(ct) >= 0 || sid.indexOf(ct) >= 0))) best = m;
      }
    }
    milestoneCache[sadId] = best;
    return best;
  }
  function loadMilestones() {
    if (milestones !== null) return Promise.resolve(milestones);
    return getJSON('../_shared/sad_milestones.json').then(function (m) {
      milestones = m || [];
      return milestones;
    });
  }

  function loadSad(sadId) {
    if (cache[sadId] !== undefined) { buildPanel(sadId); redraw(); return; }
    Promise.all([
      getJSON(url(sadId, 'derived/census_timeseries.json')),
      getJSON(url(sadId, 'source/census_blockgroups_2010.geojson')),
      getJSON(url(sadId, 'source/census_blockgroups_2020.geojson'))
    ]).then(function (r) {
      var c = { ts: r[0], geo10: r[1], geo20: r[2], byGEOID: {}, breaks: {} };
      if (c.ts && c.ts.block_groups) {
        Object.keys(c.ts.block_groups).forEach(function (year) {
          c.byGEOID[year] = {};
          (c.ts.block_groups[year] || []).forEach(function (bg) {
            if (bg && bg.GEOID) c.byGEOID[year][bg.GEOID] = bg;
          });
        });
      }
      cache[sadId] = c;
      buildPanel(sadId);
      redraw();
    });
  }
  function redraw() { if (window.sadViewerRender) window.sadViewerRender(); }

  // ─── sidebar panel ───────────────────────────────────────────────────────
  function buildDropdownHtml() {
    var groups = {}, order = [];
    METRICS.forEach(function (m) {
      if (!groups[m.group]) { groups[m.group] = []; order.push(m.group); }
      groups[m.group].push(m);
    });
    var html = '<select id="cts-metric">';
    order.forEach(function (g) {
      html += '<optgroup label="' + g + '">';
      groups[g].forEach(function (m) {
        html += '<option value="' + m.key + '"' +
          (m.key === state.metric ? ' selected' : '') + '>' + m.label + '</option>';
      });
      html += '</optgroup>';
    });
    return html + '</select>';
  }

  function buildLegendHtml(breaks, fmt) {
    if (!breaks) return '';
    var lbl = function (v) {
      if (fmt === 'currency') return '$' + Math.round(v / 1000) + 'k';
      if (fmt === 'percent') return v.toFixed(0) + '%';
      if (fmt === 'count') return Math.round(v).toLocaleString();
      return v.toFixed(0);
    };
    var rows = '';
    for (var i = 0; i < PALETTE.length; i++) {
      var label;
      if (i === 0) label = '< ' + lbl(breaks[0]);
      else if (i === PALETTE.length - 1) label = '\u2265 ' + lbl(breaks[breaks.length - 1]);
      else label = lbl(breaks[i - 1]) + '\u2013' + lbl(breaks[i]);
      rows += '<span class="cts-leg-step"><i style="background:' + PALETTE[i] +
              '"></i>' + label + '</span>';
    }
    return '<div class="cts-legend">' + rows + '</div>';
  }

  function buildPanel(sadId) {
    var sb = document.querySelector('.sidebar'); if (!sb) return;
    var sec = document.getElementById('cts-section');
    if (!sec) {
      sec = document.createElement('section');
      sec.id = 'cts-section';
      sb.appendChild(sec);
    }
    var c = cache[sadId];
    var ts = c && c.ts;
    if (!ts || !ts.years_pulled || !ts.years_pulled.length) {
      sec.innerHTML = '<h2>Census over time</h2>' +
        '<div class="cts-empty">No time-series data for this district. ' +
        'Run module_4b_census_timeseries.py to populate.</div>';
      return;
    }
    var years = ts.years_pulled;
    if (state.year == null || years.indexOf(state.year) < 0) {
      state.year = years[years.length - 1];
    }
    var m = metricByKey(state.metric);
    var summary = ts.summaries[String(state.year)] || {};
    var currentVal = summary[m.sumField];
    var breaks = computeBreaks(c, m);
    var hasGeo = !!(c.geo10 || c.geo20);

    var mile = matchMilestone(sadId, currentSadName());
    var openYr = mile && (mile.yearOpened || mile.yearCompleted);
    var sliderTick = '';
    var preWindowNote = '';
    if (openYr) {
      if (openYr >= years[0] && openYr <= years[years.length - 1]) {
        var pct = (openYr - years[0]) / (years[years.length - 1] - years[0]) * 100;
        var vTitle = ((mile && mile.anchorVenue) || 'Venue') + ' opened ' + openYr;
        sliderTick = '<div class="cts-mile-tick" style="left:' + pct.toFixed(2) +
          '%" title="' + vTitle.replace(/"/g, '&quot;') + '"></div>' +
          '<div class="cts-mile-lbl" style="left:' + pct.toFixed(2) +
          '%">' + openYr + '</div>';
      } else if (openYr < years[0]) {
        preWindowNote = '<div class="cts-pre-window">Venue opened ' + openYr +
          ' \u2014 predates ACS window</div>';
      }
    }

    var html =
      '<h2>Census over time</h2>' +
      '<div class="cts-metric-row">' + buildDropdownHtml() + '</div>' +
      '<div class="cts-readout">' +
        '<span class="cts-val">' + fmtValue(currentVal, m.fmt) + '</span>' +
        '<span class="cts-yr">' + state.year + '</span>' +
      '</div>' +
      '<div class="cts-slider-row">' +
        '<button id="cts-play" title="Play through years">\u25b6</button>' +
        '<div class="cts-slider-stack">' +
          '<input type="range" id="cts-year" min="' + years[0] + '" max="' +
            years[years.length - 1] + '" step="1" value="' + state.year + '">' +
          sliderTick +
        '</div>' +
      '</div>' +
      preWindowNote +
      '<div class="cts-chart" id="cts-chart"></div>' +
      '<div class="cts-show-row">' +
        '<label class="cts-show-lbl">' +
          '<input type="checkbox" id="cts-show"' +
            (state.showLayer ? ' checked' : '') +
            (hasGeo ? '' : ' disabled') + '> ' +
          'Show choropleth' +
          (hasGeo ? '' : ' <span class="cts-faint">(no geometry available)</span>') +
        '</label>' +
      '</div>' +
      (state.showLayer ? buildLegendHtml(breaks, m.fmt) : '') +
      '<div class="cts-note">Population-weighted across block groups. ' +
        (mile && (mile.anchorVenue || mile.yearOpened)
          ? (mile.anchorVenue || 'Venue') + (openYr ? ' opened ' + openYr : '') + '. '
          : '') +
        'ACS 5-year estimates.</div>';
    sec.innerHTML = html;
    drawChart(sadId);
    wire(sadId);
  }

  function drawChart(sadId) {
    var c = cache[sadId]; if (!c || !c.ts || !d3) return;
    var host = document.getElementById('cts-chart'); if (!host) return;
    host.innerHTML = '';
    var m = metricByKey(state.metric);
    var data = c.ts.years_pulled.map(function (y) {
      var v = (c.ts.summaries[String(y)] || {})[m.sumField];
      return { year: y, value: (v == null || !isFinite(+v)) ? null : +v };
    });
    var valid = data.filter(function (d) { return d.value != null; });
    if (valid.length < 2) {
      host.innerHTML = '<div class="cts-empty" style="padding:4px 0">Not enough data points.</div>';
      return;
    }
    var W = host.clientWidth || 280, H = 90;
    var pad = { l: 4, r: 4, t: 6, b: 14 };
    var xExtent = d3.extent(valid, function (d) { return d.year; });
    var yMin = d3.min(valid, function (d) { return d.value; });
    var yMax = d3.max(valid, function (d) { return d.value; });
    if (yMin === yMax) { yMin -= 1; yMax += 1; }
    var yPad = (yMax - yMin) * 0.08;
    var x = d3.scaleLinear().domain(xExtent).range([pad.l, W - pad.r]);
    var y = d3.scaleLinear().domain([yMin - yPad, yMax + yPad]).range([H - pad.b, pad.t]);
    var svg = d3.select(host).append('svg').attr('width', W).attr('height', H);
    var line = d3.line()
      .defined(function (d) { return d.value != null; })
      .x(function (d) { return x(d.year); })
      .y(function (d) { return y(d.value); });
    svg.append('path').attr('d', line(data))
       .attr('fill', 'none').attr('stroke', '#1a1a1a').attr('stroke-width', 1.2);
    svg.selectAll('circle.cts-dot').data(valid).enter().append('circle')
       .attr('class', 'cts-dot')
       .attr('cx', function (d) { return x(d.year); })
       .attr('cy', function (d) { return y(d.value); })
       .attr('r', 2).attr('fill', '#1a1a1a');
    var current = valid.filter(function (d) { return d.year === state.year; })[0];
    if (current) {
      svg.append('line')
         .attr('x1', x(current.year)).attr('x2', x(current.year))
         .attr('y1', pad.t).attr('y2', H - pad.b)
         .attr('stroke', '#1a1a1a').attr('stroke-width', 0.5).attr('opacity', 0.35);
      svg.append('circle')
         .attr('cx', x(current.year)).attr('cy', y(current.value))
         .attr('r', 4).attr('fill', '#1a1a1a')
         .attr('stroke', '#ffffff').attr('stroke-width', 1.5);
    }
    svg.append('text').attr('x', pad.l).attr('y', H - 2)
       .attr('font-size', 9).attr('fill', '#666').attr('font-family', 'Consolas, monospace').text(xExtent[0]);
    svg.append('text').attr('x', W - pad.r).attr('y', H - 2).attr('text-anchor', 'end')
       .attr('font-size', 9).attr('fill', '#666').attr('font-family', 'Consolas, monospace').text(xExtent[1]);

    // Milestone marker. If opening year is within the data window, draw a
    // vertical line at it. If it falls before the window, anchor a label at
    // the left edge so users know the venue predates the data we have.
    var mile = matchMilestone(sadId, currentSadName());
    var openYr = mile && (mile.yearOpened || mile.yearCompleted);
    if (openYr) {
      if (openYr >= xExtent[0] && openYr <= xExtent[1]) {
        var mx = x(openYr);
        svg.append('line')
           .attr('x1', mx).attr('x2', mx).attr('y1', pad.t).attr('y2', H - pad.b)
           .attr('stroke', '#d2a23f').attr('stroke-width', 1.2)
           .attr('stroke-dasharray', '3,2');
        svg.append('text').attr('x', mx).attr('y', pad.t + 7)
           .attr('text-anchor', mx > W / 2 ? 'end' : 'start')
           .attr('dx', mx > W / 2 ? -3 : 3)
           .attr('font-size', 9).attr('fill', '#a07a1f').attr('font-family', 'Consolas, monospace')
           .text('opened ' + openYr);
      } else if (openYr < xExtent[0]) {
        svg.append('text').attr('x', pad.l + 1).attr('y', pad.t + 7)
           .attr('font-size', 9).attr('fill', '#a07a1f').attr('font-family', 'Consolas, monospace')
           .text('opened ' + openYr + ' \u2190');
      }
    }
  }

  // ─── spatial choropleth ──────────────────────────────────────────────────
  function geoForYear(c, year) {
    if (year >= 2020 && c.geo20) return c.geo20;
    if (year < 2020 && c.geo10) return c.geo10;
    return c.geo10 || c.geo20 || null;
  }
  function drawChoropleth(g, c, pathGen) {
    if (!state.showLayer) return;
    var year = state.year;
    var fc = geoForYear(c, year);
    if (!fc || !fc.features) return;
    var m = metricByKey(state.metric);
    var breaks = computeBreaks(c, m);
    if (!breaks) return;
    var bgs = c.byGEOID[String(year)] || {};
    var gg = g.append('g').attr('id', 'cts_layer').attr('pointer-events', 'all');
    fc.features.forEach(function (f) {
      if (!f.geometry) return;
      var d = pathGen(f); if (!d) return;
      var p = f.properties || {};
      var geoid = p.GEOID || p.GEOID20 || p.GEOID10;
      var bg = geoid && bgs[geoid];
      var val = bgValue(bg, m);
      var fill = colorFor(val, breaks);
      // Render BGs with missing data as light gray so they remain visible.
      // Distinguishes "no data this year" from "geometry not in corpus."
      var isMissing = !fill;
      if (isMissing) fill = '#e8e8e8';
      var path = gg.append('path').attr('d', d)
         .attr('fill', fill)
         .attr('fill-opacity', isMissing ? 0.35 : 0.62)
         .attr('stroke', '#ffffff').attr('stroke-width', 0.4).attr('stroke-opacity', 0.7);
      path.on('mousemove', function (ev) {
        if (isMissing) {
          showTip(geoid, null, m, year, ev, true);
        } else {
          showTip(geoid, val, m, year, ev);
        }
      }).on('mouseleave', hideTip);
    });
  }

  // ─── tooltip ─────────────────────────────────────────────────────────────
  var tip = null;
  function ensureTip() {
    if (tip) return tip;
    tip = document.createElement('div'); tip.id = 'cts-tip';
    document.body.appendChild(tip);
    return tip;
  }
  function showTip(geoid, val, metric, year, ev, missing) {
    var t = ensureTip();
    if (missing) {
      t.innerHTML = '<b>no data</b><br>' +
        '<span class="cts-tip-dim">' + metric.label + ' \u00b7 ' + year + '</span><br>' +
        '<span class="cts-tip-dim">BG ' + (geoid || '') + '</span>';
    } else {
      t.innerHTML = '<b>' + fmtValue(val, metric.fmt) + '</b><br>' +
        '<span class="cts-tip-dim">' + metric.label + ' \u00b7 ' + year + '</span><br>' +
        '<span class="cts-tip-dim">BG ' + (geoid || '') + '</span>';
    }
    t.style.left = (ev.clientX + 12) + 'px';
    t.style.top = (ev.clientY + 12) + 'px';
    t.style.display = 'block';
  }
  function hideTip() { if (tip) tip.style.display = 'none'; }

  // ─── wiring ──────────────────────────────────────────────────────────────
  function wire(sadId) {
    var sel = document.getElementById('cts-metric');
    if (sel) sel.addEventListener('change', function () {
      state.metric = sel.value; buildPanel(sadId); redraw();
    });
    var sl = document.getElementById('cts-year');
    if (sl) sl.addEventListener('input', function () {
      state.year = +sl.value; buildPanel(sadId); redraw();
    });
    var play = document.getElementById('cts-play');
    if (play) play.addEventListener('click', function () {
      if (playTimer) stopPlay(); else startPlay(sadId);
    });
    var show = document.getElementById('cts-show');
    if (show) show.addEventListener('change', function () {
      state.showLayer = show.checked; buildPanel(sadId); redraw();
    });
  }
  function startPlay(sadId) {
    var c = cache[sadId]; if (!c || !c.ts || !c.ts.years_pulled.length) return;
    var years = c.ts.years_pulled;
    var play = document.getElementById('cts-play');
    if (play) play.textContent = '\u275a\u275a';
    state.year = years[0];
    buildPanel(sadId); redraw();
    playTimer = setInterval(function () {
      var idx = years.indexOf(state.year);
      if (idx >= years.length - 1) { stopPlay(); return; }
      state.year = years[idx + 1];
      buildPanel(sadId); redraw();
    }, 650);
  }
  function stopPlay() {
    if (playTimer) { clearInterval(playTimer); playTimer = null; }
    var play = document.getElementById('cts-play');
    if (play) play.textContent = '\u25b6';
  }

  // ─── styles ──────────────────────────────────────────────────────────────
  function injectStyles() {
    if (document.getElementById('cts-styles')) return;
    var s = document.createElement('style'); s.id = 'cts-styles';
    s.textContent =
      '#cts-section{padding:10px 14px;border-top:1px solid var(--line, #ddd);}' +
      '#cts-section h2{font:600 11px/1.2 var(--sans,Arial);' +
      'text-transform:uppercase;letter-spacing:.08em;color:var(--ink,#1a1a1a);' +
      'margin:0 0 8px;padding:0;}' +
      '.cts-metric-row{margin-bottom:8px;}' +
      '#cts-metric{width:100%;padding:4px 6px;font:11px var(--sans,Arial);' +
      'border:1px solid var(--line,#ddd);background:var(--bg,#fff);' +
      'color:var(--ink,#1a1a1a);border-radius:0;}' +
      '#cts-metric:focus{outline:none;border-color:var(--ink,#1a1a1a);}' +
      '.cts-readout{display:flex;align-items:baseline;gap:8px;margin-bottom:6px;}' +
      '.cts-val{font:600 22px/1.1 var(--mono,Consolas);color:var(--ink,#1a1a1a);}' +
      '.cts-yr{font:11px var(--mono,Consolas);color:var(--ink-faint,#888);letter-spacing:.04em;}' +
      '.cts-slider-row{display:flex;align-items:center;gap:6px;margin-bottom:8px;}' +
      '.cts-slider-stack{position:relative;flex:1;padding-bottom:11px;}' +
      '.cts-slider-stack input[type=range]{width:100%;display:block;margin:0;}' +
      '.cts-mile-tick{position:absolute;top:-3px;bottom:10px;width:1px;background:#d2a23f;' +
      'pointer-events:auto;cursor:help;}' +
      '.cts-mile-lbl{position:absolute;bottom:-2px;transform:translateX(-50%);' +
      'font:9px var(--mono,Consolas);color:#a07a1f;pointer-events:none;white-space:nowrap;}' +
      '#cts-play{font-size:10px;line-height:1;padding:3px 7px;' +
      'border:1px solid var(--line,#ddd);background:var(--bg,#fff);' +
      'color:var(--ink,#1a1a1a);cursor:pointer;}' +
      '#cts-play:hover{border-color:var(--ink,#1a1a1a);}' +
      '.cts-chart{width:100%;min-height:90px;margin:2px 0 6px;}' +
      '.cts-chart svg{display:block;}' +
      '.cts-show-row{margin:6px 0 4px;}' +
      '.cts-show-lbl{font:11px var(--sans,Arial);color:var(--ink,#1a1a1a);' +
      'display:flex;align-items:center;gap:5px;cursor:pointer;}' +
      '.cts-show-lbl input[disabled]{cursor:not-allowed;}' +
      '.cts-faint{color:var(--ink-faint,#888);}' +
      '.cts-legend{display:flex;flex-wrap:wrap;gap:5px 8px;font:10px var(--mono,Consolas);' +
      'color:var(--ink-soft,#444);margin:4px 0 6px;}' +
      '.cts-leg-step{display:inline-flex;align-items:center;gap:3px;}' +
      '.cts-leg-step i{display:inline-block;width:11px;height:11px;border:1px solid rgba(0,0,0,.18);}' +
      '.cts-pre-window{font:10px var(--mono,Consolas);color:#a07a1f;' +
      'margin:-4px 0 8px;padding:3px 6px;background:rgba(210,162,63,0.08);' +
      'border-left:2px solid #d2a23f;}' +
      '.cts-note{font:10px/1.4 var(--sans,Arial);color:var(--ink-faint,#888);}' +
      '.cts-empty{font:11px/1.5 var(--sans,Arial);color:var(--ink-faint,#888);}' +
      '#cts-tip{position:fixed;z-index:9999;display:none;pointer-events:none;' +
      'background:#0a0a0a;color:#fff;font:11px/1.45 var(--sans,Arial);padding:6px 9px;' +
      'border:1px solid #333;max-width:220px;}' +
      '#cts-tip b{font-weight:600;}#cts-tip .cts-tip-dim{color:#9a9a9a;}';
    document.head.appendChild(s);
  }

  // ─── hook into viewer's after-render ─────────────────────────────────────
  var prevAfterRender = window.sadAfterRender;
  window.sadAfterRender = function (vstate, _d3, root) {
    if (prevAfterRender) { try { prevAfterRender(vstate, _d3, root); } catch (e) {} }
    var sad = currentSad();
    var c = cache[sad];
    if (!c || !vstate.pathGen) return;
    if (!state.showLayer) return;
    var g = root.append('g').attr('class', 'cts-overlay');
    drawChoropleth(g, c, vstate.pathGen);
  };

  // ─── bootstrap ───────────────────────────────────────────────────────────
  function tick() {
    var sad = currentSad();
    if (sad && sad !== lastSad) { lastSad = sad; loadSad(sad); }
  }
  function init() {
    injectStyles();
    loadMilestones();  // start loading immediately; matcher tolerates not-yet-loaded
    var sel = document.getElementById('sad-select');
    if (sel) sel.addEventListener('change', function () { setTimeout(tick, 30); });
    setInterval(tick, 400);
    tick();
  }
  if (document.readyState !== 'loading') init();
  else document.addEventListener('DOMContentLoaded', init);
})();
