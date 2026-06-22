/* viewer_export_legends.js
 *
 * Adds three missing legends to the viewer's SVG/PDF export:
 *
 *   - LST (surface temperature, °C)         — when state.on.heat is true
 *   - LODES jobs (jobs / acre)               — when state.on.jobs is true
 *   - Census time-series choropleth          — when census time-series layer is shown
 *
 * The viewer's POI density heatmap already has a legend (in buildExportChrome
 * inside viewer.js). The three above are rendered by viewer_modules.js and
 * viewer_census_timeseries.js, but those modules write their legends into
 * sidebar placeholders (sf-legend-* divs) that the SVG export doesn't see.
 *
 * This addon intercepts exportSvg(), captures the renderer state needed for
 * each active legend, and appends them as <g id="legend_*"> groups inside
 * the export's #legend group BEFORE serialization. They render in the same
 * place + style as the POI heatmap legend, so the deliverable looks
 * consistent.
 *
 * INTEGRATION
 *   Same as viewer_export_pdf.js: drop in code\viewer\ and copy to data\_ui\,
 *   add a script tag in index.html. No changes to viewer.js required.
 *
 * READING THE RENDERERS (so the legend matches the map)
 *   LST:    d3.interpolateInferno, domain [lo, hi] from heat_grid.geojson
 *   Jobs:   d3.interpolateViridis, domain [0, maxPerAcre] fixed across years
 *   Census: 5-step blue PALETTE, 4 quintile breaks per metric, fmt by metric
 */
(function () {
  'use strict';

  // Same color palette census uses (mirrored so we don't need to reach into
  // the viewer_census_timeseries IIFE's closure).
  const CENSUS_PALETTE = ['#eaf3fb', '#c6dbef', '#9ecae1', '#4292c6', '#08519c'];

  // Wait for exportSvg to be defined, then wrap it.
  let installed = false;
  let tries = 0;
  const iv = setInterval(() => {
    if (installed) return clearInterval(iv);
    if (typeof window.exportSvg === 'function') {
      wrapExportSvg();
      rebindSvgButton();    // route the button through window.exportSvg
      installed = true;
      clearInterval(iv);
    } else if (++tries > 60) {
      clearInterval(iv);
    }
  }, 100);

  // The Export SVG button uses addEventListener to bind a CLOSURE reference
  // to the original exportSvg — patching window.exportSvg doesn't reach it.
  // Replacing the button with a clone strips the old listener; we then
  // attach a new one that calls window.exportSvg by name, so my wrap fires.
  function rebindSvgButton() {
    const btn = document.getElementById('export-svg');
    if (!btn) {
      console.warn('[export-legends] #export-svg not found at install time');
      return;
    }
    const fresh = btn.cloneNode(true);
    btn.parentNode.replaceChild(fresh, btn);
    fresh.addEventListener('click', () => {
      console.log('[export-legends] SVG button clicked, calling window.exportSvg');
      window.exportSvg();
    });
    console.log('[export-legends] Export SVG button rebound to window.exportSvg');
  }

  function wrapExportSvg() {
    const orig = window.exportSvg;
    window.exportSvg = async function () {
      // Pre-grab the data we'll need; the export may strip/move it later.
      const ctx = snapshotLegendContext();
      // Patch XMLSerializer just for the duration of this call so we can
      // add legends right before the SVG is serialized to a blob.
      const origSerialize = XMLSerializer.prototype.serializeToString;
      XMLSerializer.prototype.serializeToString = function (node) {
        try {
          if (isSvgExport(node)) {
            appendLegends(node, ctx);
          }
        } catch (e) {
          console.warn('[export-legends] could not append:', e);
        }
        return origSerialize.call(this, node);
      };
      try {
        return await orig.apply(this, arguments);
      } finally {
        XMLSerializer.prototype.serializeToString = origSerialize;
      }
    };
  }

  function isSvgExport(node) {
    if (!node || node.nodeType !== 1) return false;
    if (node.tagName && node.tagName.toLowerCase() !== 'svg') return false;
    return !!node.querySelector('[id="legend"]');
  }

  // ── Snapshot what each renderer was showing at export time ──────────────
  // SELECTORS reflect the ACTUAL sidebar DOM (from viewer_modules.js and
  // viewer_census_timeseries.js); previous version guessed wrong.
  function snapshotLegendContext() {
    const ctx = { lst: null, jobs: null, census: null };

    // LST — written by viewer_modules.js `legendHeat(lo, hi)`:
    //   #sf-legend-heat contains:
    //     .sf-leg-title  "surface temp °C"
    //     .sf-grad.sf-grad-heat
    //     .sf-grad-ax > <span>lo</span><span>hi</span>
    try {
      const heatEl = document.getElementById('sf-legend-heat');
      if (heatEl && heatEl.innerHTML.trim()) {
        const axisSpans = heatEl.querySelectorAll('.sf-grad-ax span');
        if (axisSpans.length >= 2) {
          const lo = parseFloat(axisSpans[0].textContent);
          const hi = parseFloat(axisSpans[axisSpans.length - 1].textContent);
          if (isFinite(lo) && isFinite(hi)) {
            ctx.lst = { lo, hi };
            console.log('[export-legends] LST captured:', lo, '..', hi, '°C');
          }
        }
      }
    } catch (e) {
      console.warn('[export-legends] LST capture failed:', e);
    }

    // Jobs — written by viewer_modules.js `legendJobs(maxPerAcre, year)`:
    //   #sf-legend-jobs contains:
    //     .sf-leg-title  "jobs / acre · YEAR"
    //     .sf-grad with viridis gradient as inline style
    //     .sf-grad-ax > <span>0</span><span>maxPerAcre</span>
    try {
      const jobsEl = document.getElementById('sf-legend-jobs');
      if (jobsEl && jobsEl.innerHTML.trim()) {
        const titleEl = jobsEl.querySelector('.sf-leg-title');
        const axisSpans = jobsEl.querySelectorAll('.sf-grad-ax span');
        const title = titleEl ? titleEl.textContent.trim() : 'jobs / acre';
        let lo = 0, hi = null;
        if (axisSpans.length >= 2) {
          lo = parseFloat(axisSpans[0].textContent) || 0;
          hi = parseFloat(axisSpans[axisSpans.length - 1].textContent);
        }
        if (isFinite(hi)) {
          ctx.jobs = { title, lo, hi };
          console.log('[export-legends] Jobs captured:', title, lo, '..', hi);
        }
      }
    } catch (e) {
      console.warn('[export-legends] Jobs capture failed:', e);
    }

    // Census — written by viewer_census_timeseries.js:
    //   .cts-legend contains 5 .cts-leg-step elements, each with
    //     <i style="background:COLOR"></i>LABEL
    //   The metric label comes from <select id="cts-metric">'s selected option.
    //   The active year is the value of <input id="cts-year">, or .cts-yr.
    try {
      const steps = document.querySelectorAll('.cts-legend .cts-leg-step');
      if (steps.length >= 5) {
        const tierLabels = [];
        steps.forEach(s => {
          // textContent strips the <i>; what's left is the label
          const txt = (s.textContent || '').trim();
          tierLabels.push(txt);
        });
        const sel = document.getElementById('cts-metric');
        const metricLabel = (sel && sel.options[sel.selectedIndex])
          ? sel.options[sel.selectedIndex].text
          : 'Census';
        const yearInput = document.getElementById('cts-year');
        const yearDisp = document.querySelector('.cts-yr');
        const year = (yearDisp && yearDisp.textContent.trim()) ||
                     (yearInput && yearInput.value) || null;
        ctx.census = { metricLabel, year, tierLabels };
        console.log('[export-legends] Census captured:', metricLabel, year, tierLabels);
      }
    } catch (e) {
      console.warn('[export-legends] Census capture failed:', e);
    }

    if (!ctx.lst && !ctx.jobs && !ctx.census) {
      console.log('[export-legends] no active layer legends found in sidebar');
    }
    return ctx;
  }

  // ── Append each legend block into the existing #legend group ────────────
  function appendLegends(svgRoot, ctx) {
    const legend = svgRoot.querySelector('[id="legend"]');
    if (!legend) return;
    const geom = legendGeometry(svgRoot, legend);
    const GUTTER = 14;

    // Hard-clip the analysis overlay layers (heat/jobs) to the map canvas.
    // They live inside #cropped_content which carries clip-path, but some
    // renderers (and some Illustrator builds) don't honor a parent clip on
    // these dynamically-added subtrees. Physically dropping elements whose
    // bbox is fully outside the crop rect guarantees the result.
    hardClipOverlays(svgRoot);

    let y = geom.bottomY;
    function tryAppend(fn) {
      const nextY = fn(legend, geom, y);
      if (nextY > geom.ceilingY) {
        console.warn('[export-legends] insufficient room — block skipped at y=' +
                     y.toFixed(0) + ' (ceiling=' + geom.ceilingY.toFixed(0) + ')');
        return;
      }
      y = nextY + GUTTER;
    }

    if (ctx.lst) {
      tryAppend((leg, g, yy) => appendLstLegend(leg, g, yy, ctx.lst));
    }
    if (ctx.jobs) {
      tryAppend((leg, g, yy) => appendJobsLegend(leg, g, yy, ctx.jobs));
    }
    if (ctx.census) {
      tryAppend((leg, g, yy) => appendCensusLegend(leg, g, yy, ctx.census));
    }

    if (ctx.lst || ctx.jobs || ctx.census) {
      anchorChromeBelow(svgRoot, y, geom);
    }
  }

  // Physically clip the heat/jobs/census overlay layers to the crop rect.
  // Reads the crop rect from clipPath#crop-inside, then removes any element
  // inside m_layers / m_heat / m_jobs / cts_layer whose bbox is fully outside.
  function hardClipOverlays(svgRoot) {
    const cp = svgRoot.querySelector('clipPath[id="crop-inside"] path, [id="crop-inside"] path');
    if (!cp) return;
    const d = cp.getAttribute('d') || '';
    const nums = (d.match(/-?[\d.]+/g) || []).map(parseFloat);
    if (nums.length < 4) return;
    const xs = nums.filter((_, i) => i % 2 === 0);
    const ys = nums.filter((_, i) => i % 2 === 1);
    const clipX0 = Math.min(...xs), clipX1 = Math.max(...xs);
    const clipY0 = Math.min(...ys), clipY1 = Math.max(...ys);

    const NUM = /-?\d+(?:\.\d+)?/g;
    function bboxOutside(elementD) {
      if (!elementD) return false;
      const n = elementD.match(NUM);
      if (!n || n.length < 2) return false;
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (let i = 0; i + 1 < n.length; i += 2) {
        const x = parseFloat(n[i]), yv = parseFloat(n[i + 1]);
        if (x < minX) minX = x; if (x > maxX) maxX = x;
        if (yv < minY) minY = yv; if (yv > maxY) maxY = yv;
      }
      // fully outside on any side?
      return (maxX < clipX0 || minX > clipX1 || maxY < clipY0 || minY > clipY1);
    }

    let removed = 0;
    ['m_heat', 'm_jobs', 'cts_layer'].forEach(layerId => {
      const layer = svgRoot.querySelector('[id="' + layerId + '"]');
      if (!layer) return;
      Array.from(layer.querySelectorAll('path, rect, circle, polygon')).forEach(elx => {
        let outside = false;
        const dd = elx.getAttribute('d');
        if (dd) {
          outside = bboxOutside(dd);
        } else {
          // rect/circle: check x/y/cx/cy against clip
          const x = parseFloat(elx.getAttribute('x'));
          const yv = parseFloat(elx.getAttribute('y'));
          const w = parseFloat(elx.getAttribute('width')) || 0;
          const h = parseFloat(elx.getAttribute('height')) || 0;
          const cxv = parseFloat(elx.getAttribute('cx'));
          const cyv = parseFloat(elx.getAttribute('cy'));
          if (isFinite(x) && isFinite(yv)) {
            outside = (x + w < clipX0 || x > clipX1 || yv + h < clipY0 || yv > clipY1);
          } else if (isFinite(cxv) && isFinite(cyv)) {
            outside = (cxv < clipX0 || cxv > clipX1 || cyv < clipY0 || cyv > clipY1);
          }
        }
        if (outside) { elx.parentNode && elx.parentNode.removeChild(elx); removed++; }
      });
    });
    if (removed) console.log('[export-legends] hard-clipped ' + removed +
                             ' overlay elements outside the canvas');
  }

  // Move scale_bar and north_arrow so they sit below `startY`, keeping their
  // relative spacing. We compute each group's current top and translate it by
  // (targetTop - currentTop). north_arrow follows scale_bar with a gutter.
  function anchorChromeBelow(svgRoot, startY, geom) {
    const GAP = 26;
    let cursor = startY + 10;

    ['scale_bar', 'north_arrow'].forEach(id => {
      const g = svgRoot.querySelector('[id="' + id + '"]');
      if (!g) return;
      // Current top of this group (min y across its children)
      let top = Infinity, bottom = -Infinity;
      g.querySelectorAll('*').forEach(child => {
        ['y', 'y1', 'y2', 'cy'].forEach(a => {
          const v = parseFloat(child.getAttribute(a));
          if (isFinite(v)) { if (v < top) top = v; if (v > bottom) bottom = v; }
        });
        const pts = child.getAttribute('points');
        if (pts) {
          const nums = (pts.match(/-?[\d.]+/g) || []).map(parseFloat);
          for (let i = 1; i < nums.length; i += 2) {
            if (nums[i] < top) top = nums[i];
            if (nums[i] > bottom) bottom = nums[i];
          }
        }
      });
      if (!isFinite(top)) return;
      const dy = cursor - top;
      // Compose with any existing transform (there is none in current exports,
      // but be safe).
      const prev = g.getAttribute('transform') || '';
      g.setAttribute('transform', (prev + ' translate(0,' + dy.toFixed(1) + ')').trim());
      cursor = bottom + dy + GAP;   // next chrome group sits below this one
    });
  }

  // Read the legend column's geometry from the SVG we're about to serialize.
  // We can't call buildExportChrome's local constants from here, but every
  // existing legend element has x/y attributes we can inspect.
  function legendGeometry(svgRoot, legendG) {
    // Column left edge + width from the legend group's own children.
    let cx = Infinity, maxRight = 0, legendBottom = 0;
    legendG.querySelectorAll('*').forEach(node => {
      const tag = node.tagName.toLowerCase();
      const x = parseFloat(node.getAttribute('x'));
      const y = parseFloat(node.getAttribute('y'));
      const w = parseFloat(node.getAttribute('width')) || 0;
      const h = parseFloat(node.getAttribute('height')) || 0;
      if (isFinite(x) && x < cx) cx = x;
      if (isFinite(x)) {
        let rightEdge = x + w;
        if (tag === 'text') {
          const fs = parseFloat(node.getAttribute('font-size')) || 11;
          rightEdge = x + (node.textContent || '').length * fs * 0.56;
        }
        if (rightEdge > maxRight) maxRight = rightEdge;
      }
      if (isFinite(y) && y + h > legendBottom) legendBottom = y + h;
      const y2 = parseFloat(node.getAttribute('y2'));
      if (isFinite(y2) && y2 > legendBottom) legendBottom = y2;
    });
    if (!isFinite(cx)) cx = 24;
    const columnW = Math.max(220, maxRight - cx);

    // CRITICAL FIX: scale_bar and north_arrow are SIBLINGS of the legend
    // group, not children — and they sit at FIXED y positions regardless of
    // how long the legend is. On a short legend (few layers, e.g. Philly),
    // they sit BELOW the legend's own bottom. We must find the max y across
    // the legend AND those chrome groups, then start new legends below all
    // of them. (On Detroit the legend is long and runs past the chrome; on
    // Philly the chrome is the lowest thing — this handles both.)
    let lowestChrome = legendBottom;
    ['scale_bar', 'north_arrow'].forEach(id => {
      const node = svgRoot.querySelector('[id="' + id + '"]');
      if (!node) return;
      node.querySelectorAll('*').forEach(child => {
        // gather y, y2, and polygon point-ys
        const ys = [];
        ['y', 'y2', 'cy'].forEach(a => {
          const v = parseFloat(child.getAttribute(a));
          if (isFinite(v)) ys.push(v);
          const h = parseFloat(child.getAttribute('height'));
          if (a === 'y' && isFinite(v) && isFinite(h)) ys.push(v + h);
        });
        const pts = child.getAttribute('points');
        if (pts) {
          const nums = (pts.match(/-?[\d.]+/g) || []).map(parseFloat);
          for (let i = 1; i < nums.length; i += 2) ys.push(nums[i]);
        }
        ys.forEach(y => { if (isFinite(y) && y > lowestChrome) lowestChrome = y; });
      });
    });

    // Attribution top = ceiling (don't run legends into the source line).
    let attrTop = Infinity;
    const attr = svgRoot.querySelector('[id="attribution"]');
    if (attr) {
      attr.querySelectorAll('*').forEach(n => {
        const y = parseFloat(n.getAttribute('y'));
        if (isFinite(y) && y < attrTop) attrTop = y;
      });
    }
    if (!isFinite(attrTop)) {
      const vb = (svgRoot.getAttribute('viewBox') || '').trim().split(/\s+/);
      const pageH = (vb.length === 4 && parseFloat(vb[3])) ||
                    parseFloat(svgRoot.getAttribute('height')) || 800;
      attrTop = pageH - 40;
    }

    return {
      cx,
      columnRight: cx + columnW,
      columnW,
      bottomY: lowestChrome + 22,   // start below the LOWEST of legend/scale/north
      ceilingY: attrTop - 18,
      SW: 16,
      ROW: 23,
      labelX: cx + 16 + 12,
    };
  }

  // ── SVG element helpers ─────────────────────────────────────────────────
  const NS = 'http://www.w3.org/2000/svg';
  function el(tag, attrs, parent, textContent) {
    const n = document.createElementNS(NS, tag);
    if (attrs) {
      for (const k in attrs) {
        if (attrs[k] != null) n.setAttribute(k, attrs[k]);
      }
    }
    if (textContent != null) n.textContent = textContent;
    if (parent) parent.appendChild(n);
    return n;
  }
  const SANS = 'system-ui,-apple-system,"Segoe UI",Helvetica,Arial,sans-serif';
  const MONO = '"SF Mono","JetBrains Mono","Consolas",monospace';
  const INK = '#0a0a0a';
  const MUTE = '#888';

  function sectionHeader(legend, geom, y, text) {
    el('text', {
      x: geom.cx, y: y,
      'font-family': MONO, 'font-size': 8.5, 'font-weight': 600,
      'letter-spacing': '0.12em', fill: MUTE,
    }, legend, text);
    return y + geom.ROW - 3;
  }

  function gradientBar(legend, geom, y, stops, axisLabels, options) {
    const w = geom.columnW - 8;
    const h = options && options.h || 12;
    const gradId = 'grad_' + Math.random().toString(36).slice(2, 9);

    // Find the SVG root by walking UP from the legend node — this gets the
    // CLONE the serializer is processing, not the live #map in the document.
    // (`document.querySelector('svg')` returns the live one.)
    let svg = legend.parentNode;
    while (svg && svg.tagName && svg.tagName.toLowerCase() !== 'svg') {
      svg = svg.parentNode;
    }
    if (!svg) {
      console.warn('[export-legends] could not find clone SVG root from legend node');
      // Fall back to a fully-inline approach: render the gradient as discrete
      // rect cells, no <linearGradient> dependency. Looks essentially identical
      // at legend scale and works in any SVG without defs plumbing.
      renderInlineGradient(legend, geom, y, stops, w, h);
    } else {
      let defs = svg.querySelector(':scope > defs') || svg.querySelector('defs');
      if (!defs) {
        defs = legend.ownerDocument.createElementNS(NS, 'defs');
        svg.insertBefore(defs, svg.firstChild);
      }
      const grad = el('linearGradient',
        { id: gradId, x1: '0', y1: '0', x2: '1', y2: '0' }, defs);
      stops.forEach((color, i) => {
        el('stop', {
          offset: (stops.length === 1 ? 0 : i / (stops.length - 1)) * 100 + '%',
          'stop-color': color,
        }, grad);
      });
      // Dark backing so opacity ramps read as designed
      el('rect', {
        x: geom.cx, y: y, width: w, height: h, fill: '#1a1a1a',
      }, legend);
      el('rect', {
        x: geom.cx, y: y, width: w, height: h, fill: 'url(#' + gradId + ')',
      }, legend);
    }
    y += h + 12;
    // Axis labels at ends
    if (axisLabels && axisLabels.length >= 2) {
      el('text', {
        x: geom.cx, y: y, 'font-family': MONO, 'font-size': 9, fill: INK,
      }, legend, axisLabels[0]);
      el('text', {
        x: geom.cx + w, y: y, 'text-anchor': 'end',
        'font-family': MONO, 'font-size': 9, fill: INK,
      }, legend, axisLabels[axisLabels.length - 1]);
      y += 4;
    }
    return y + geom.ROW - 4;
  }

  // Inline fallback: 24 discrete rect cells, each filled with an interpolated
  // color between adjacent stops. No gradient defs required; reads identically.
  function renderInlineGradient(legend, geom, y, stops, w, h) {
    const N = 32;
    const cellW = w / N;
    for (let i = 0; i < N; i++) {
      const t = i / (N - 1);
      const seg = t * (stops.length - 1);
      const lo = Math.floor(seg), hi = Math.min(stops.length - 1, lo + 1);
      const ft = seg - lo;
      const color = lerpHex(stops[lo], stops[hi], ft);
      el('rect', {
        x: geom.cx + i * cellW, y: y, width: cellW + 0.5, height: h,
        fill: color, stroke: 'none',
      }, legend);
    }
  }

  function lerpHex(a, b, t) {
    const pa = parseHex(a), pb = parseHex(b);
    const r = Math.round(pa[0] + (pb[0] - pa[0]) * t);
    const g = Math.round(pa[1] + (pb[1] - pa[1]) * t);
    const bl = Math.round(pa[2] + (pb[2] - pa[2]) * t);
    return '#' + [r, g, bl].map(n => n.toString(16).padStart(2, '0')).join('');
  }
  function parseHex(h) {
    h = h.replace('#', '');
    if (h.length === 3) h = h.split('').map(c => c + c).join('');
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
  }

  // ── Individual legends ──────────────────────────────────────────────────

  // LST: inferno gradient, °C low/high
  function appendLstLegend(legend, geom, y, info) {
    const sub = el('g', {
      id: 'legend_surface_temp',
      'inkscape:label': 'legend_surface_temp',
      'inkscape:groupmode': 'layer',
    }, legend);
    y = sectionHeader(sub, geom, y, 'SURFACE TEMPERATURE');
    const stops = ['#000004', '#420a68', '#932667', '#dd513a', '#fca50a', '#fcffa4'];
    y = gradientBar(sub, geom, y, stops,
      [info.lo.toFixed(1) + '°C', info.hi.toFixed(1) + '°C']);
    el('text', {
      x: geom.cx, y: y, 'font-family': SANS, 'font-size': 9, fill: MUTE,
    }, sub, 'Landsat 8/9, peak summer, °C');
    return y + geom.ROW;
  }

  // Jobs: viridis gradient, jobs/acre 0..max (fixed across years)
  function appendJobsLegend(legend, geom, y, info) {
    const sub = el('g', {
      id: 'legend_jobs',
      'inkscape:label': 'legend_jobs',
      'inkscape:groupmode': 'layer',
    }, legend);
    y = sectionHeader(sub, geom, y, info.title.toUpperCase());
    const stops = ['#440154', '#414487', '#2a788e', '#22a884', '#7ad151', '#fde725'];
    const lo = '0';
    const hi = isFinite(info.hi) ? Number(info.hi).toFixed(1) : '';
    y = gradientBar(sub, geom, y, stops, [lo, hi]);
    el('text', {
      x: geom.cx, y: y, 'font-family': SANS, 'font-size': 9, fill: MUTE,
    }, sub, 'LEHD LODES · scale fixed across years');
    return y + geom.ROW;
  }

  // Census: 5-swatch quintile choropleth with active metric + year
  function appendCensusLegend(legend, geom, y, info) {
    const sub = el('g', {
      id: 'legend_census',
      'inkscape:label': 'legend_census',
      'inkscape:groupmode': 'layer',
    }, legend);
    y = sectionHeader(sub, geom, y,
      'CENSUS · ' + (info.metricLabel || '').toUpperCase());
    const labels = info.tierLabels || ['Q1', 'Q2', 'Q3', 'Q4', 'Q5'];
    const swH = 14, gap = 4;
    CENSUS_PALETTE.forEach((color, i) => {
      el('rect', {
        x: geom.cx, y: y, width: geom.SW, height: swH, fill: color,
        stroke: '#222', 'stroke-width': 0.5,
      }, sub);
      el('text', {
        x: geom.cx + geom.SW + 8, y: y + swH * 0.78,
        'font-family': SANS, 'font-size': 10.5, fill: INK,
      }, sub, labels[i] || '');
      y += swH + gap;
    });
    if (info.year) {
      y += 4;
      el('text', {
        x: geom.cx, y: y, 'font-family': MONO, 'font-size': 9, fill: MUTE,
      }, sub, 'YEAR ' + info.year);
    }
    return y + geom.ROW;
  }
})();
