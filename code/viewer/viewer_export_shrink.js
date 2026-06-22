/* viewer_export_shrink.js
 *
 * SVG-export size reducer for the SAD Viewer's exportSvg() in viewer.js.
 *
 * WHY
 *   The current export serializes the live DOM verbatim. Every D3-generated
 *   coordinate carries 10+ decimal digits, every building/street outline is
 *   written at sub-pixel precision (invisible at any reasonable zoom), and an
 *   on-screen satellite tile becomes a base64 PNG inside the SVG that can be
 *   1-2 MB on its own. Illustrator chokes on files in the 30-80 MB range; this
 *   shrinks a typical export 5-10x with no visible loss.
 *
 * HOW (in size-impact order)
 *   1. ROUND PATH COORDINATES to N decimal places. D3 writes ~3.5 chars of
 *      slop per number; rounding 412.348293847912 -> 412.3 saves ~10 chars per
 *      coordinate. Buildings + streets + walkshed alone go down 50-70%.
 *   2. STRIP EMPTY ATTRIBUTES (fill="", stroke-width="", style="").
 *   3. STRIP HIDDEN / DISPLAY:NONE NODES that wouldn't render anyway.
 *   4. CONSOLIDATE INKSCAPE LABELS â€” only on id'd group nodes (not every leaf).
 *   5. SATELLITE: optionally drop, downsample, or compress the embedded raster.
 *      JPEG at 0.82 quality is typically 1/8 the size of PNG with no visible
 *      loss at viewer scale.
 *   6. HEATMAP / MANY-TINY-POLYS: optional decimation pass that drops polys
 *      below a pixel-area threshold (visually invisible).
 *
 * INTEGRATION
 *   1) Put this file next to viewer.js (same folder build_ui_manifest copies
 *      to data/_ui/).
 *   2) Add to viewer's HTML right before </body>:
 *        <script src="viewer_export_shrink.js"></script>
 *   3) That's it â€” this monkey-patches exportSvg() once on DOMContentLoaded.
 *      Original exportSvg is preserved; the patch wraps it.
 *
 * CONFIG
 *   Tunable via window.ExportShrink (set BEFORE the script runs, or live in
 *   the console). All values are conservative defaults that preserve visible
 *   fidelity:
 *     coordPrecision    1     (decimal places to keep; 1 = 0.1px, invisible)
 *     stripEmpty        true  (remove fill="" etc.)
 *     stripHidden       true  (remove display:none / visibility:hidden nodes)
 *     satelliteMode     'jpeg' ('jpeg' = re-encode, 'drop' = remove, 'keep' = unchanged)
 *     satelliteQuality  0.82  (jpeg quality 0-1; 0.82 is the sweet spot)
 *     decimateMinPxArea 0     (drop polys smaller than this in pixel area; 0 = off)
 */
(function () {
  'use strict';

  // â”€â”€ public knobs â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  const CFG = Object.assign({
    coordPrecision: 1,
    stripEmpty: true,
    stripHidden: true,
    satelliteMode: 'jpeg',
    satelliteQuality: 0.82,
    decimateMinPxArea: 0,
    clipToViewport: true,       // drop paths fully outside the export's viewBox
    clipViewportPad: 4,         // extra px of slack around the viewBox (a path
                                // whose bbox sits within `pad` of the edge is
                                // kept, so we never clip a line that hairlines
                                // into view)
    dropChrome: false,          // strip legend, attribution, scale bar, north
                                // arrow, title â€” for when Illustrator can't
                                // composite chrome + map together. Most teams
                                // rebuild chrome in Illustrator anyway.
    dropEmbeddedImages: false,  // drop ALL <image> elements outside #00_satellite
                                // (logos, badges, watermarks). Even tiny PNGs
                                // can trigger Illustrator's outline-mode fallback
                                // because the preview pipeline color-manages
                                // every embedded raster.
    verbose: true,
  }, window.ExportShrink || {});

  function log(...a) { if (CFG.verbose) console.log('[shrink]', ...a); }

  // â”€â”€ round every numeric token inside a d="..." attribute â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  // SVG path data is a stream of commands + space/comma-separated numbers.
  // A regex pass that touches only number tokens preserves command semantics.
  const NUM_RE = /-?\d+\.\d+(?:e[+-]?\d+)?/gi;
  function roundDAttr(d, decimals) {
    if (!d) return d;
    const pow = Math.pow(10, decimals);
    return d.replace(NUM_RE, m => {
      const n = parseFloat(m);
      const r = Math.round(n * pow) / pow;
      // Keep '0' rather than '0.0' / '0.00' for the leanest output
      return Number.isInteger(r) ? String(r) : String(r);
    });
  }

  // â”€â”€ round transform="translate(x,y) scale(s)" coordinate tokens too â”€â”€â”€â”€â”€
  function roundNumberishAttr(val, decimals) {
    if (!val) return val;
    const pow = Math.pow(10, decimals);
    return val.replace(NUM_RE, m => {
      const r = Math.round(parseFloat(m) * pow) / pow;
      return Number.isInteger(r) ? String(r) : String(r);
    });
  }

  // â”€â”€ empty / default-only attributes â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  const REMOVE_IF_EMPTY = new Set([
    'style', 'fill', 'stroke', 'stroke-width', 'stroke-dasharray',
    'stroke-linecap', 'stroke-linejoin', 'class', 'transform',
    'pointer-events', 'opacity', 'fill-opacity', 'stroke-opacity',
  ]);

  function processNode(el, stats) {
    if (el.nodeType !== 1) return;

    // skip+drop hidden
    if (CFG.stripHidden) {
      const dispAttr = el.getAttribute && el.getAttribute('display');
      const visAttr = el.getAttribute && el.getAttribute('visibility');
      const styleAttr = el.getAttribute && el.getAttribute('style');
      if (dispAttr === 'none' || visAttr === 'hidden' ||
          (styleAttr && /display\s*:\s*none|visibility\s*:\s*hidden/i.test(styleAttr))) {
        el.parentNode && el.parentNode.removeChild(el);
        stats.dropped++;
        return;
      }
    }

    // round geometry-bearing attributes
    if (CFG.coordPrecision >= 0) {
      const d = el.getAttribute && el.getAttribute('d');
      if (d) {
        const r = roundDAttr(d, CFG.coordPrecision);
        if (r !== d) { el.setAttribute('d', r); stats.savedD += d.length - r.length; }
      }
      const tr = el.getAttribute && el.getAttribute('transform');
      if (tr) {
        const r = roundNumberishAttr(tr, CFG.coordPrecision + 2);  // a bit safer
        if (r !== tr) el.setAttribute('transform', r);
      }
      // points="..." on <polygon>/<polyline>
      const pts = el.getAttribute && el.getAttribute('points');
      if (pts) {
        const r = roundNumberishAttr(pts, CFG.coordPrecision);
        if (r !== pts) el.setAttribute('points', r);
      }
      // x, y, cx, cy, r, x1, x2, y1, y2 on simple shapes
      for (const attr of ['x', 'y', 'cx', 'cy', 'r',
                          'x1', 'x2', 'y1', 'y2', 'width', 'height']) {
        const v = el.getAttribute && el.getAttribute(attr);
        if (v && NUM_RE.test(v)) {
          NUM_RE.lastIndex = 0;
          const r = roundNumberishAttr(v, CFG.coordPrecision);
          if (r !== v) el.setAttribute(attr, r);
        }
      }
    }

    // empty attributes
    if (CFG.stripEmpty && el.attributes) {
      for (let i = el.attributes.length - 1; i >= 0; i--) {
        const a = el.attributes[i];
        if (REMOVE_IF_EMPTY.has(a.name) && (!a.value || a.value.trim() === '')) {
          el.removeAttribute(a.name);
          stats.attrsRemoved++;
        }
      }
    }

    // recurse â€” copy to array first since we may remove children
    const kids = Array.from(el.children);
    for (const k of kids) processNode(k, stats);
  }

  // â”€â”€ satellite: synchronous drop in post-process, async re-encode in hook â”€â”€
  // 'drop' runs here (sync, always reliable). 'jpeg' is handled by the async
  // hook below â€” we can't await inside serializeToString so re-encoding must
  // happen before the serializer is called. Either path puts a fix in front
  // of Illustrator's preview, but 'drop' is the most reliable.
  function dropSatelliteSync(svgRoot, stats) {
    if (CFG.satelliteMode !== 'drop') return;
    const sat = svgRoot.querySelector('[id="00_satellite"]');
    if (sat) {
      sat.parentNode && sat.parentNode.removeChild(sat);
      stats.satelliteDropped = true;
    }
  }

  // â”€â”€ drop every other embedded raster (logos, badges) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  // Tiny PNGs in the legend or chrome can trigger Illustrator's outline-mode
  // fallback even when they're under 50 KB, because the preview pipeline
  // color-manages every embedded raster. This pass removes any <image> not
  // inside #00_satellite (satellite is handled by its own pass above).
  function dropEmbeddedImagesPass(svgRoot, stats) {
    if (!CFG.dropEmbeddedImages) return;
    let removed = 0;
    svgRoot.querySelectorAll('image').forEach(img => {
      // skip if inside the satellite group (already handled by dropSatelliteSync)
      let n = img;
      while (n && n.nodeType === 1) {
        if (n.id === '00_satellite') return;
        n = n.parentNode;
      }
      img.parentNode && img.parentNode.removeChild(img);
      removed++;
    });
    stats.imagesDropped = removed;
  }

  // â”€â”€ sanity-clean the export clipPath if it has a wild secondary subpath â”€â”€
  // Some viewer exports leave a stale second subpath in clipPath#crop-inside
  // with coordinates in the millions (projected-but-not-canvas-scaled). The
  // intended clip is the FIRST subpath only; the wild second subpath forces
  // Illustrator to compute clipping against an effectively infinite region
  // and falls back to outline mode. Replace the path d-attr with just the
  // first subpath (everything up to the first 'M' after the opening one).
  function fixWildClipPath(svgRoot, stats) {
    const cp = svgRoot.querySelector('clipPath[id="crop-inside"] path, ' +
                                      '[id="crop-inside"] path');
    if (!cp) return;
    const d = cp.getAttribute('d') || '';
    // Split into subpaths at each moveto. D3 emits an absolute 'M' for the
    // first subpath but often a RELATIVE 'm' for later ones -- the old code
    // searched only for 'M' and so missed (and kept) the wild rectangle.
    const parts = d.match(/[Mm][^Mm]*/g);
    if (!parts || parts.length < 2) return;
    // Artboard size from the viewBox; fall back to width/height.
    const svg = svgRoot.tagName && svgRoot.tagName.toLowerCase() === 'svg'
                ? svgRoot : svgRoot.querySelector('svg') || svgRoot.ownerDocument.documentElement;
    let W = 0, H = 0;
    const vb = (svg.getAttribute && svg.getAttribute('viewBox')) || '';
    const vbn = vb.split(/[\s,]+/).map(parseFloat).filter(isFinite);
    if (vbn.length === 4) { W = vbn[2]; H = vbn[3]; }
    if (!W || !H) {
      W = parseFloat(svg.getAttribute && svg.getAttribute('width')) || 2000;
      H = parseFloat(svg.getAttribute && svg.getAttribute('height')) || 2000;
    }
    const pad = 0.10;
    const limit = Math.max((1 + pad) * W, (1 + pad) * H) * 3;
    // Keep only subpaths whose coordinates fit within (a padded) artboard;
    // drop any subpath with runaway coordinates (the dim-overlay rectangle).
    const kept = parts.filter(sp => {
      const nums = (sp.match(/-?\d+(?:\.\d+)?/g) || []).map(parseFloat);
      if (!nums.length) return false;
      const mx = Math.max.apply(null, nums.map(Math.abs));
      return mx <= limit;
    });
    if (kept.length === parts.length) return;   // nothing wild
    let out = kept.join(' ').trim();
    if (out && !/[Zz]\s*$/.test(out)) out += 'Z';
    if (out) {
      cp.setAttribute('d', out);
      stats.clipPathFixed = true;
    }
  }

  async function reencodeSatellite(svgRoot, stats) {
    if (CFG.satelliteMode === 'keep') return;
    const sat = svgRoot.querySelector('[id="00_satellite"] image');
    if (!sat) return;
    const src = sat.getAttribute('href') || sat.getAttribute('xlink:href');
    if (!src) return;

    if (CFG.satelliteMode === 'drop') {
      sat.parentNode && sat.parentNode.removeChild(sat);
      stats.satelliteDropped = true;
      log('satellite raster removed');
      return;
    }

    // 'jpeg' mode: re-encode to JPEG with controlled quality. Works whether
    // src is a data URI (already embedded) or a remote URL (we draw and convert).
    try {
      const im = await loadImage(src);
      const c = document.createElement('canvas');
      c.width = im.naturalWidth || im.width;
      c.height = im.naturalHeight || im.height;
      const ctx = c.getContext('2d');
      // White fill so transparent edges look right under JPEG (no alpha channel)
      ctx.fillStyle = '#fff';
      ctx.fillRect(0, 0, c.width, c.height);
      ctx.drawImage(im, 0, 0);
      const jpeg = c.toDataURL('image/jpeg', CFG.satelliteQuality);
      if (jpeg && jpeg.length > 100) {
        sat.setAttribute('xlink:href', jpeg);
        sat.removeAttribute('href');
        stats.satelliteReencoded = { from: src.length, to: jpeg.length };
        log(`satellite re-encoded: ${(src.length / 1024).toFixed(0)} KB -> ` +
            `${(jpeg.length / 1024).toFixed(0)} KB`);
      }
    } catch (e) {
      console.warn('[shrink] satellite re-encode failed:', e);
    }
  }

  function loadImage(src) {
    return new Promise((resolve, reject) => {
      const im = new Image();
      im.crossOrigin = 'anonymous';
      im.onload = () => resolve(im);
      im.onerror = reject;
      im.src = src;
    });
  }

  // â”€â”€ decimate tiny polys (off by default; useful for dense heatmaps) â”€â”€â”€â”€â”€
  function decimateTinyPolys(svgRoot, stats) {
    if (!CFG.decimateMinPxArea) return;
    const min = CFG.decimateMinPxArea;
    // <polygon>/<polyline>: parse points, compute shoelace area
    svgRoot.querySelectorAll('polygon, polyline').forEach(p => {
      const pts = (p.getAttribute('points') || '').trim().split(/[\s,]+/).map(parseFloat);
      if (pts.length < 6) return;
      let a = 0;
      for (let i = 0; i < pts.length; i += 2) {
        const j = (i + 2) % pts.length;
        a += pts[i] * pts[j + 1] - pts[j] * pts[i + 1];
      }
      if (Math.abs(a) / 2 < min) {
        p.parentNode && p.parentNode.removeChild(p);
        stats.polysDropped++;
      }
    });
  }

  // â”€â”€ clip paths whose bbox falls entirely outside the export viewport â”€â”€â”€â”€
  // This is the bigger win for any export where the data extent exceeds the
  // visible canvas (e.g. Philly, where building/road datasets cover the metro
  // but only the 2km district shows). Illustrator stays in preview mode when
  // total path count drops below its tipping point, even if file size hasn't
  // changed dramatically.
  function clipToViewport(svgRoot, stats) {
    if (!CFG.clipToViewport) return;
    const vb = (svgRoot.getAttribute('viewBox') || '').trim().split(/\s+/).map(parseFloat);
    if (vb.length !== 4 || vb.some(n => !isFinite(n))) return;
    const pad = CFG.clipViewportPad || 0;
    const vx1 = vb[0] - pad, vy1 = vb[1] - pad;
    const vx2 = vb[0] + vb[2] + pad, vy2 = vb[1] + vb[3] + pad;

    // For every <path>, <polygon>, <polyline>, compute the bbox of its
    // coordinate stream and drop the element if that bbox is fully outside
    // the viewport. Using cheap regex math, not the DOM's getBBox (which is
    // slow and we don't need geometry precision here â€” only inside/outside).
    const NUM = /-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/gi;
    function bboxOfNumbers(str) {
      if (!str) return null;
      NUM.lastIndex = 0;
      const nums = str.match(NUM);
      if (!nums || nums.length < 4) return null;
      // Treat consecutive numbers as x,y pairs (works for both `d` paths
      // and `points` lists â€” close enough for an outside/inside test)
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (let i = 0; i + 1 < nums.length; i += 2) {
        const x = parseFloat(nums[i]);
        const y = parseFloat(nums[i + 1]);
        if (!isFinite(x) || !isFinite(y)) continue;
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      }
      if (!isFinite(minX)) return null;
      return [minX, minY, maxX, maxY];
    }

    // Skip protected layers â€” chrome we WANT to keep even if it sits "outside"
    // the geographic viewport. Legend, attribution, scale, north arrow live
    // beside the map and are part of the export deliverable.
    const PROTECTED_ANCESTORS = ['attribution', 'legend', 'chrome', 'scale',
                                 'north', 'page_background', 'title'];
    function isProtected(node) {
      let n = node;
      while (n && n.nodeType === 1) {
        const id = n.id || '';
        if (id && PROTECTED_ANCESTORS.some(p => id.toLowerCase().includes(p))) {
          return true;
        }
        n = n.parentNode;
      }
      return false;
    }

    let dropped = 0;
    svgRoot.querySelectorAll('path, polygon, polyline').forEach(el => {
      if (isProtected(el)) return;
      const attr = el.getAttribute('d') || el.getAttribute('points');
      const bb = bboxOfNumbers(attr);
      if (!bb) return;
      // Fully outside the viewport on any axis?
      if (bb[2] < vx1 || bb[0] > vx2 || bb[3] < vy1 || bb[1] > vy2) {
        el.parentNode && el.parentNode.removeChild(el);
        dropped++;
      }
    });
    stats.clipped = dropped;
  }

  // â”€â”€ drop chrome (legend, attribution, scale, north, title) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  // Used when Illustrator can preview the map alone, or the chrome alone,
  // but not both at once. Most teams rebuild legends in Illustrator with
  // consistent typography anyway, so the editable deliverable wants the
  // geometry and not the chrome.
  function dropChrome(svgRoot, stats) {
    if (!CFG.dropChrome) return;
    const CHROME_IDS = ['attribution', 'legend', 'chrome', 'scale',
                        'north', 'title', 'page_background',
                        'export_chrome', 'export-chrome'];
    let removed = 0;
    CHROME_IDS.forEach(id => {
      // Match by exact id AND by id-contains, so 'legend_panel' /
      // 'north_arrow' / 'scale_bar' all get hit.
      svgRoot.querySelectorAll(`[id="${id}"], [id*="${id}"]`).forEach(el => {
        el.parentNode && el.parentNode.removeChild(el);
        removed++;
      });
    });
    stats.chromeDropped = removed;
  }

  // â”€â”€ patch exportSvg by wrapping serializeToString â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  // The original exportSvg uses `new XMLSerializer().serializeToString(clone)`.
  // We patch the prototype to run our post-processor on the clone first.
  // String-level coordinate clamp, applied to the SERIALIZED svg text (after all
  // DOM passes). Clamps any d=/cx/cy coordinate wildly outside the artboard into
  // it -- e.g. the sad_boundary dim-overlay rectangle that forces Illustrator
  // into outline preview. Operates only on attribute values, never on data: URIs
  // (so the satellite raster is untouched). Fallback-safe: returns the original
  // string on any error, so it can never break the export download.
  function clampSvgString(svgText) {
    try {
      if (typeof svgText !== 'string' || svgText.lastIndexOf('<svg', 200) === -1) return svgText;
      const vbm = svgText.match(/viewBox="\s*([\-\d.]+)\s+([\-\d.]+)\s+([\-\d.]+)\s+([\-\d.]+)\s*"/);
      let W = 0, H = 0;
      if (vbm) { W = parseFloat(vbm[3]); H = parseFloat(vbm[4]); }
      if (!W || !H) {
        const wm = svgText.match(/\bwidth="([\d.]+)"/), hm = svgText.match(/\bheight="([\d.]+)"/);
        W = wm ? parseFloat(wm[1]) : 2000; H = hm ? parseFloat(hm[1]) : 2000;
      }
      const pad = 0.10;
      const xlo = -pad*W, xhi = (1+pad)*W, ylo = -pad*H, yhi = (1+pad)*H;
      const trigger = Math.max(xhi, yhi) * 3;
      const clampv = (v, lo, hi) => (v < lo ? lo : (v > hi ? hi : v));
      svgText = svgText.replace(/(\sd=")([^"]*)(")/g, (full, p1, d, p3) => {
        const nums = d.match(/-?\d+(?:\.\d+)?/g);
        if (!nums) return full;
        let mx = 0; for (const n of nums) { const a = Math.abs(+n); if (a > mx) mx = a; }
        if (mx <= trigger) return full;
        let idx = 0;
        const nd = d.replace(/-?\d+(?:\.\d+)?/g, m => {
          let v = parseFloat(m);
          v = (idx++ % 2 === 0) ? clampv(v, xlo, xhi) : clampv(v, ylo, yhi);
          return String(Math.round(v*10)/10);
        });
        return p1 + nd + p3;
      });
      svgText = svgText.replace(/\bcx="(-?\d+(?:\.\d+)?)"/g, (full, n) => {
        const v = parseFloat(n); return (v < -trigger || v > trigger) ? ('cx="' + clampv(v,xlo,xhi) + '"') : full;
      });
      svgText = svgText.replace(/\bcy="(-?\d+(?:\.\d+)?)"/g, (full, n) => {
        const v = parseFloat(n); return (v < -trigger || v > trigger) ? ('cy="' + clampv(v,ylo,yhi) + '"') : full;
      });
      return svgText;
    } catch (e) {
      return svgText;
    }
  }
  const Orig = XMLSerializer.prototype.serializeToString;
  XMLSerializer.prototype.serializeToString = function (node) {
    try {
      // Only intervene for SVG roots â€” leave everything else alone (Leaflet
      // panes, internal toolkits using XMLSerializer for other things)
      const isSvgExport = node && node.nodeType === 1 &&
                          node.tagName && node.tagName.toLowerCase() === 'svg' &&
                          node.querySelector('[id^="0"], #map_root, [id="page_background"]');
      if (isSvgExport) {
        const stats = {
          dropped: 0, savedD: 0, attrsRemoved: 0, polysDropped: 0,
          clipped: 0, chromeDropped: 0, imagesDropped: 0,
          satelliteDropped: false, clipPathFixed: false,
        };
        dropSatelliteSync(node, stats);   // sync drop runs every time satelliteMode='drop'
        dropEmbeddedImagesPass(node, stats); // drop non-satellite rasters (legend logo etc.)
        fixWildClipPath(node, stats);     // sanitize clipPath wild secondary subpath
        dropChrome(node, stats);          // drop chrome first if requested
        clipToViewport(node, stats);     // clip BEFORE processing â€” every clipped
                                          // path is one less node to round
        processNode(node, stats);
        decimateTinyPolys(node, stats);
        log('post-process:',
            stats.satelliteDropped ? 'satellite DROPPED, ' : '',
            stats.imagesDropped, 'images,',
            stats.clipPathFixed ? 'wild clipPath FIXED, ' : '',
            stats.chromeDropped, 'chrome nodes,',
            stats.clipped, 'off-canvas paths,',
            stats.dropped, 'hidden nodes,',
            (stats.savedD / 1024).toFixed(1), 'KB saved on path data,',
            stats.attrsRemoved, 'empty attrs,',
            stats.polysDropped, 'tiny polys');
      }
    } catch (e) {
      console.warn('[shrink] processing skipped:', e);
    }
    {
      const __out = Orig.call(this, node);
      try {
        const __isSvg = node && node.nodeType === 1 && node.tagName &&
                        node.tagName.toLowerCase() === 'svg' &&
                        node.querySelector('[id^="0"], #map_root, [id="page_background"]');
        return __isSvg ? clampSvgString(__out) : __out;
      } catch (e) { return __out; }
    }
  };

  // Patch exportSvg to give us an async hook for the satellite re-encode.
  function installSatelliteHook() {
    if (typeof window.exportSvg !== 'function') return false;
    const origExportSvg = window.exportSvg;
    window.exportSvg = async function () {
      // We can't intercept the clone inside the original function, so we
      // pre-process the live #map satellite tile BEFORE the original runs.
      // The shrink post-process inside XMLSerializer covers everything else.
      try {
        if (CFG.satelliteMode !== 'keep') {
          const svg = document.getElementById('map');
          if (svg) {
            const sat = svg.querySelector('[id="00_satellite"] image');
            if (sat && CFG.satelliteMode === 'jpeg') {
              await reencodeSatellite(svg, { });
            }
          }
        }
      } catch (e) { console.warn('[shrink] pre-export hook failed:', e); }
      return origExportSvg.apply(this, arguments);
    };
    return true;
  }

  // exportSvg may be defined later; poll briefly
  let tries = 0;
  const iv = setInterval(() => {
    if (installSatelliteHook() || ++tries > 40) clearInterval(iv);
  }, 100);

  // expose runtime knobs
  window.ExportShrink = CFG;
  log('loaded â€” defaults:', CFG);
})();


