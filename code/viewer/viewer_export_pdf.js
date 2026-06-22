/* viewer_export_pdf.js
 *
 * Adds an "Export PDF" button next to the existing "Export SVG" button in the
 * viewer. The PDF route bypasses Illustrator's recent SVG-rendering issues
 * (GPU regression, embedded-raster compositing, complex clipPath failures)
 * by emitting a vector PDF that Illustrator opens cleanly with all groups
 * intact.
 *
 * HOW IT WORKS
 *   The viewer's existing exportSvg() already builds the complete SVG export
 *   tree (chrome, clip, satellite, layers). We reuse that work — generate
 *   the same SVG payload in memory, but instead of serializing it to a .svg
 *   file, we convert it to PDF via svg2pdf.js + jsPDF and download that
 *   instead. Same content, smaller / friendlier container.
 *
 * INTEGRATION
 *   1) Put this file in code\viewer\
 *   2) Drop a copy in data\_ui\ (build_ui_manifest.py has a hardcoded
 *      allowlist; the manual copy is the simplest path)
 *   3) Add to code\viewer\index.html right before </body>:
 *        <script src="https://unpkg.com/jspdf@2.5.1/dist/jspdf.umd.min.js"></script>
 *        <script src="https://unpkg.com/svg2pdf.js@2.2.3/dist/svg2pdf.umd.min.js"></script>
 *        <script src="viewer_export_pdf.js"></script>
 *
 *   The script installs an "Export PDF" button at the top of the page (or
 *   next to #export-svg if it can find it). No other viewer code needs to
 *   change — we hook the same payload exportSvg already builds.
 *
 * DEPENDENCIES (CDN)
 *   - jspdf 2.5.1   — PDF generation
 *   - svg2pdf.js 2.2.3 — SVG-to-PDF renderer that handles paths, text,
 *                       clipPaths, gradients, embedded images
 *
 *   Both are tiny (~150 KB combined, gzipped). Once cached by the browser
 *   they don't re-download.
 */
(function () {
  'use strict';

  // ── install the button beside Export SVG ────────────────────────────────
  function installButton() {
    if (document.getElementById('export-pdf')) return true;        // idempotent
    const svgBtn = document.getElementById('export-svg');
    if (!svgBtn) return false;                                       // wait
    const btn = document.createElement('button');
    btn.id = 'export-pdf';
    btn.textContent = 'Export PDF';
    btn.title = 'Export as PDF (use this if Illustrator struggles with the SVG)';
    // Match Export SVG's styling so they read as a pair
    for (const attr of ['className', 'style']) {
      try { btn[attr] = svgBtn[attr]; } catch (e) {}
    }
    // Copy computed class list explicitly (className copy is browser-flaky for SVG buttons)
    if (svgBtn.className && typeof svgBtn.className === 'string') {
      btn.className = svgBtn.className;
    }
    svgBtn.parentNode.insertBefore(btn, svgBtn.nextSibling);
    btn.addEventListener('click', onExportPdf);
    return true;
  }

  // exportSvg may be wired later; poll briefly
  let tries = 0;
  const iv = setInterval(() => {
    if (installButton() || ++tries > 60) clearInterval(iv);
  }, 100);

  // ── handler ─────────────────────────────────────────────────────────────
  async function onExportPdf() {
    // Sanity check dependencies up front so the failure mode is a useful
    // message rather than a silent no-op
    const jsPDF = (window.jspdf && window.jspdf.jsPDF) || window.jsPDF;
    if (!jsPDF) {
      alert('Export PDF: jsPDF not loaded.\n\nAdd these to index.html before viewer_export_pdf.js:\n' +
            '<script src="https://unpkg.com/jspdf@2.5.1/dist/jspdf.umd.min.js"></script>\n' +
            '<script src="https://unpkg.com/svg2pdf.js@2.2.3/dist/svg2pdf.umd.min.js"></script>');
      return;
    }
    if (typeof window.svg2pdf !== 'function' &&
        !(window.svg2pdf && typeof window.svg2pdf.svg2pdf === 'function')) {
      alert('Export PDF: svg2pdf.js not loaded — see console.');
      return;
    }
    const svg2pdfFn = (typeof window.svg2pdf === 'function')
      ? window.svg2pdf
      : window.svg2pdf.svg2pdf;

    // Hijack the URL.createObjectURL / a.click pattern at the end of exportSvg
    // so we capture the assembled SVG blob without it auto-downloading as .svg.
    // exportSvg builds the full export tree (clip + chrome + clones), so this
    // hook gives us the same payload the SVG button would have produced.
    const capturedBlob = await captureExportSvg();
    if (!capturedBlob) {
      alert('Export PDF: could not capture SVG payload from exportSvg().');
      return;
    }

    const svgText = await capturedBlob.text();
    const parser = new DOMParser();
    const doc = parser.parseFromString(svgText, 'image/svg+xml');
    const svgEl = doc.querySelector('svg');
    if (!svgEl) {
      alert('Export PDF: parsed SVG has no <svg> root.'); return;
    }

    // Read dimensions from the SVG so PDF page matches exactly
    const vb = (svgEl.getAttribute('viewBox') || '').trim().split(/\s+/).map(parseFloat);
    const w = vb[2] || parseFloat(svgEl.getAttribute('width')) || 1100;
    const h = vb[3] || parseFloat(svgEl.getAttribute('height')) || 800;

    // Build PDF page in landscape if wider than tall, portrait otherwise
    const orient = w >= h ? 'landscape' : 'portrait';
    const pdf = new jsPDF({
      orientation: orient,
      unit: 'pt',
      format: [w, h],
      compress: true,
    });

    // The svg2pdf renderer needs the SVG node attached to the live document
    // (it reads computed styles). Mount it offscreen.
    document.body.appendChild(svgEl);
    svgEl.style.position = 'absolute';
    svgEl.style.left = '-99999px';
    svgEl.style.top = '0';

    try {
      await svg2pdfFn(svgEl, pdf, { x: 0, y: 0, width: w, height: h });
      const sadId = currentSadId() || 'export';
      pdf.save(sadId + '_viewer_export.pdf');
    } catch (e) {
      console.error('[export-pdf] svg2pdf failed:', e);
      alert('Export PDF failed during rendering. See console for details.');
    } finally {
      svgEl.parentNode && svgEl.parentNode.removeChild(svgEl);
    }
  }

  // ── capture the SVG that exportSvg() produces, without saving the .svg ──
  // We monkey-patch URL.createObjectURL for ONE call: the next blob the page
  // creates is the export payload. We grab the blob, then restore the
  // original function and suppress the .svg download.
  function captureExportSvg() {
    return new Promise(async (resolve, reject) => {
      if (typeof window.exportSvg !== 'function') {
        return reject(new Error('exportSvg() not defined'));
      }
      let captured = null;
      const origCreate = URL.createObjectURL;
      const origRevoke = URL.revokeObjectURL;
      const origAppend = HTMLAnchorElement.prototype.click;

      URL.createObjectURL = function (blob) {
        if (!captured && blob && blob.type && blob.type.indexOf('svg') >= 0) {
          captured = blob;
          // Return a harmless URL — won't be downloaded because we override
          // the next .click() on the anchor below.
          return 'data:text/plain;base64,';
        }
        return origCreate.call(URL, blob);
      };
      HTMLAnchorElement.prototype.click = function () {
        if (captured && this.download && /\.svg$/i.test(this.download)) {
          // suppress the .svg download — we've got the blob, we'll make PDF
          return;
        }
        return origAppend.apply(this, arguments);
      };

      try {
        await window.exportSvg();
      } catch (e) {
        // restore before bailing
        URL.createObjectURL = origCreate;
        URL.revokeObjectURL = origRevoke;
        HTMLAnchorElement.prototype.click = origAppend;
        return reject(e);
      } finally {
        // restore (revoke after a short delay to let exportSvg's own setTimeout run)
        setTimeout(() => {
          URL.createObjectURL = origCreate;
          URL.revokeObjectURL = origRevoke;
          HTMLAnchorElement.prototype.click = origAppend;
        }, 1500);
      }
      resolve(captured);
    });
  }

  function currentSadId() {
    try {
      // viewer's state object is exposed at window.state in some builds;
      // failing that, read the sad-select dropdown
      if (window.state && window.state.currentSadId) return window.state.currentSadId;
      const sel = document.getElementById('sad-select');
      if (sel && sel.value) return sel.value;
    } catch (e) {}
    return null;
  }
})();
