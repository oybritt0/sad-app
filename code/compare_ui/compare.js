// compare.js — SAD Synthesis Field (v2)
// A 3D orbit-able field of analyzed sports-anchored districts.
// Layout is a PCA-3D projection of the 27-D morphometric vector (for
// navigation only); similarity is always measured in the full feature space
// via the precomputed distance matrix, never from on-screen proximity.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';

// ── color systems ────────────────────────────────────────────────────────────
// Typology = the resting color of every point in the field.
const TYPO = {
  entertainment: 0xe2674f, community: 0x5fae7e, innovation: 0x5688c4,
  'sports park': 0xd2a23f, tourism: 0xc98a3a, mixed: 0x8f78c9, default: 0x9a9088,
};
function typoKey(t) { return (t == null ? '' : String(t)).trim().toLowerCase(); }
function typoColor(t) { return TYPO[typoKey(t)] ?? TYPO.default; }
// Selection slots = distinct per-district colors used across the panel (rose,
// chips, table, neighbor links) so overlaid districts are always tellable apart.
const SLOTS = [0xff5a45, 0x12b3a6, 0xeca42b, 0x8c6fe0];
const SLOT_NAMES = ['coral', 'teal', 'gold', 'violet'];

const MAX_SEL = 4;
const NEIGHBOR_K = 6;
const SUGGEST_K = 6;
// Canonical typology families (designer-confirmable, in the detail panel).
const CANON = ['Entertainment', 'Community', 'Innovation', 'Sports Park'];
const _UNSPEC = new Set(['', 'unspecified', 'unknown', 'unclassified', 'none', 'n/a', 'na', 'tbd']);
function _isUnspec(v) { return _UNSPEC.has(typoKey(v)); }
// Effective family: a confirmed primary typology, else the secondary label.
// (Corpus currently carries the family in secondary_typology; primary is wired
// up via the detail-panel picker.)
function effTypo(rec) {
  if (!rec) return null;
  if (!_isUnspec(rec.typology)) return String(rec.typology).trim();
  if (!_isUnspec(rec.secondary_typology)) return String(rec.secondary_typology).trim();
  return null;
}
function isConfirmed(rec) { return effTypo(rec) != null; }
function recColor(rec) { return typoColor(effTypo(rec) || 'default'); }

// ── comparison metrics (table) ───────────────────────────────────────────────
function svm(rec, key) {
  const rows = rec.census && rec.census.sad_vs_metro;
  if (!rows) return null;
  const r = rows.find(x => x.key === key);
  return r ? r.sad_percentile_in_metro : null;
}
const METRICS = [
  ['Typology',         r => r.typology || '—',                                   'text',   'typology'],
  ['Metro',            r => r.region || '—',                                     'text',   'metro'],
  ['Median income',    r => r.census?.sad?.median_household_income_pop_weighted, 'usd',    'income'],
  ['Income %ile metro',r => svm(r, 'median_household_income_pop_weighted'),      'pctile', 'income_pctile'],
  ['Median age',       r => r.census?.sad?.median_age_pop_weighted,              'num1',   'age'],
  ['Renter-occupied',  r => r.census?.sad?.pct_renter_occupied,                  'pct',    'renter'],
  ["Bachelor's+",      r => r.census?.sad?.pct_bachelors_or_higher,              'pct',    'educ'],
  ['Population',       r => r.census?.sad?.estimated_population,                 'int',    'pop'],
  ['POIs in SAD',      r => r.amenity?.total_points_in_sad ?? r.program?.total,  'int',    'pois'],
  ['Transit stations', r => r.transit?.total_stations,                          'int',    'transit'],
  ['10-min walkshed',  r => r.walkshed?.walkshed_10min_acres,                    'acres',  'walk'],
  ['Street nodes',     r => r.centrality?.network_size?.nodes,                   'int',    'streets'],
];
function fmt(v, kind) {
  if (v === null || v === undefined || v === '') return '—';
  if (kind === 'text') return v;
  const n = Number(v); if (!isFinite(n)) return String(v);
  switch (kind) {
    case 'usd':    return '$' + Math.round(n).toLocaleString();
    case 'pct':    return n.toFixed(0) + '%';
    case 'pctile': return ordinal(Math.round(n));
    case 'num1':   return n.toFixed(1);
    case 'int':    return Math.round(n).toLocaleString();
    case 'acres':  return n.toFixed(0) + ' ac';
    default:       return String(v);
  }
}
function ordinal(n) {
  const s = ['th', 'st', 'nd', 'rd'], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}
function hex(n) { return '#' + n.toString(16).padStart(6, '0'); }

// ── wind-rose axes (percentile within corpus) ─────────────────────────────────
const ROSE_AXES = [
  ['income',  'Income',  r => r.census?.sad?.median_household_income_pop_weighted],
  ['renter',  'Renter',  r => r.census?.sad?.pct_renter_occupied],
  ['educ',    'Educ',    r => r.census?.sad?.pct_bachelors_or_higher],
  ['pop',     'Pop',     r => r.census?.sad?.estimated_population],
  ['pois',    'POIs',    r => r.amenity?.total_points_in_sad ?? r.program?.total],
  ['transit', 'Transit', r => r.transit?.total_stations],
  ['walk',    'Walk',    r => r.walkshed?.walkshed_10min_acres],
  ['streets', 'Streets', r => r.centrality?.network_size?.nodes],
];

// ── metric explanations (hover = summary, click = full provenance) ───────────
const ACS = 'U.S. Census Bureau, American Community Survey 2019–2023 5-year estimates';
const INFO = {
  income: { title: 'Median household income', src: 'ACS 5-yr · table B19013 · block group · pop-weighted',
    body: `Typical household income across the census block groups that overlap the district. ${ACS}, aggregated and weighted by each block group's population. ACS figures are rolling five-year averages carrying margins of error — estimates, not point-in-time counts.` },
  renter: { title: 'Renter-occupied housing', src: 'ACS 5-yr · table B25003 · block group',
    body: `Share of occupied homes that are rented rather than owned, across the district's block groups. ${ACS}. High values mark rental-dominant — usually denser, more recently built — districts.` },
  educ: { title: "Bachelor's degree or higher", src: 'ACS 5-yr · table B15003 · adults 25+',
    body: `Share of adults 25 and older holding at least a bachelor's degree, across the district's block groups. ${ACS}.` },
  age: { title: 'Median age', src: 'ACS 5-yr · table B01002 · pop-weighted',
    body: `Median resident age across the district's block groups. ${ACS}, population-weighted.` },
  pop: { title: 'Estimated population', src: 'ACS 5-yr · table B01003 · block group',
    body: `Resident population summed across the block groups overlapping the district. ${ACS}. Block groups rarely align exactly with the district boundary, so this approximates residents in and immediately around the district — not a precise count inside the polygon.` },
  pois: { title: 'Points of interest', src: 'Rossetti program / OpenStreetMap · within SAD',
    body: `Count of mapped businesses, venues, and amenities inside the district boundary, classified into program categories. A proxy for commercial and programmatic intensity; depends on mapping completeness, which varies by city.` },
  transit: { title: 'Transit stations', src: 'OpenStreetMap · M13 · within analysis extent',
    body: `Rail, metro, tram, and bus stops in and around the district, from OpenStreetMap — spanning major stations down to individual bus stops. A coarse accessibility proxy; reflects OSM coverage.` },
  walk: { title: '10-minute walkshed', src: 'Network walkshed · M15 · acres',
    body: `Area reachable on foot within ten minutes from the district origin, walking the pedestrian street network at ~3 mph. Larger means better connectivity and fewer barriers. Clipped to the 2 km analysis canvas.` },
  streets: { title: 'Street-network size', src: 'OpenStreetMap graph · M06e · node count',
    body: `Number of intersections (nodes) in the local street graph within the analysis radius. A measure of network grain — more nodes means a finer, more connected grid. Topological only; not traffic or pedestrian volume.` },
  income_pctile: { title: 'Income percentile within metro', src: 'M4b · CBSA · pop-weighted',
    body: `The district's median household income expressed as a percentile of every block group across its metropolitan area (CBSA). "78th" means the district out-earns about 78% of the metro's residents. This reframes a raw dollar figure as relative standing in the region.` },
  metro: { title: 'Metropolitan area (CBSA)', src: 'Census Core-Based Statistical Area',
    body: `The Core-Based Statistical Area containing the district — the reference region for the income, age, and other percentiles. CBSAs are built from whole counties.` },
  typology: { title: 'District typology', src: 'Project classification',
    body: `The district's assigned character class — entertainment, community, innovation, tourism, or mixed — derived from its anchor and program profile.` },
  rose: { title: 'Reading the wind rose', src: 'Percentile within the analyzed corpus',
    body: `Each spoke is the district's percentile rank among all analyzed districts: the outer ring is the highest in the set, the center the lowest. A rose shows relative standing across the set — not absolute values. The exact numbers are in the comparison table below.` },
};

// ── PCA → 3D via power iteration + deflation (validated vs numpy SVD) ─────────
function nrm(v) { let s = 0; for (const x of v) s += x * x; s = Math.sqrt(s) || 1e-12; return v.map(x => x / s); }
function mv(M, v) { const d = v.length, o = new Array(d).fill(0); for (let a = 0; a < d; a++) { let s = 0; for (let b = 0; b < d; b++) s += M[a][b] * v[b]; o[a] = s; } return o; }
function pca3(ids, vectors) {
  const n = ids.length, d = vectors[0].length;
  const mean = new Array(d).fill(0);
  for (const v of vectors) for (let j = 0; j < d; j++) mean[j] += v[j];
  for (let j = 0; j < d; j++) mean[j] /= n;
  const X = vectors.map(v => v.map((x, j) => x - mean[j]));
  const C = Array.from({ length: d }, () => new Array(d).fill(0));
  for (const row of X) for (let a = 0; a < d; a++) for (let b = 0; b < d; b++) C[a][b] += row[a] * row[b];
  const den = Math.max(1, n - 1);
  for (let a = 0; a < d; a++) for (let b = 0; b < d; b++) C[a][b] /= den;
  const trace = C.reduce((s, r, i) => s + r[i], 0);
  const comps = [], eigs = [], W = C.map(r => r.slice());
  let seed = 1234567; const rand = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff - 0.5; };
  for (let k = 0; k < 3; k++) {
    let v = nrm(new Array(d).fill(0).map(rand));
    for (let it = 0; it < 300; it++) { const Cv = mv(W, v); const vN = nrm(Cv); const dot = Math.abs(vN.reduce((s, x, i) => s + x * v[i], 0)); v = vN; if (it > 8 && dot > 1 - 1e-10) break; }
    const Cv = mv(W, v), lam = v.reduce((s, x, i) => s + x * Cv[i], 0);
    comps.push(v); eigs.push(lam);
    for (let a = 0; a < d; a++) for (let b = 0; b < d; b++) W[a][b] -= lam * v[a] * v[b];
  }
  const coords = {};
  X.forEach((row, i) => { coords[ids[i]] = comps.map(c => row.reduce((s, x, j) => s + x * c[j], 0)); });
  return { coords, varExplained: eigs.map(e => trace > 0 ? e / trace : 0) };
}

// ── state ──────────────────────────────────────────────────────────────────────
const S = {
  manifest: null, fieldSads: [], basePos: {}, idToRec: {}, distance: {},
  axisStats: [], liveAxes: [],
  scene: null, camera: null, renderer: null, labelRenderer: null, controls: null,
  raycaster: null, pointer: new THREE.Vector2(-2, -2),
  meshes: [], idToMesh: {}, labelOf: {}, rings: [], lines: [],
  typoOn: null, showCreated: true, _typoCount: 0,
  visible: null, typoOverride: {},
  selected: [], slotOf: {}, hovered: null,
  showAllLabels: false, showConnections: true,
  spread: 1.0, pointSize: 1.0,
  t0: performance.now(),
};

// ── boot ──────────────────────────────────────────────────────────────────────
async function boot() {
  const loading = document.getElementById('loading');
  try {
    S.manifest = await fetch('compare_manifest.json').then(r => {
      if (!r.ok) throw new Error('compare_manifest.json not found — run build_compare_manifest.py');
      return r.json();
    });
  } catch (e) { loading.textContent = e.message; return; }

  // designer-accepted typologies persist locally and survive reloads
  try { S.typoOverride = JSON.parse(localStorage.getItem('sad_typo_overrides') || '{}') || {}; }
  catch (e) { S.typoOverride = {}; }
  for (const rec of S.manifest.sads) { const ov = S.typoOverride[rec.sad_id]; if (ov) rec.typology = ov; }

  const emb = S.manifest.embedding || {};
  S.distance = emb.distance_matrix || {};
  const fnames = emb.feature_names || [];

  const ids = [], vectors = [];
  for (const rec of S.manifest.sads) {
    S.idToRec[rec.sad_id] = rec;
    const norm = rec.features && rec.features.normalized;
    if (norm && fnames.length) {
      const v = fnames.map(f => Number(norm[f]));
      if (v.every(x => isFinite(x))) { ids.push(rec.sad_id); vectors.push(v); }
    }
  }
  if (ids.length < 2) { loading.textContent = `Only ${ids.length} district(s) carry embedding vectors — run M8, then rebuild the manifest.`; return; }

  const { coords, varExplained } = pca3(ids, vectors);
  let maxAbs = 0; for (const id of ids) for (const c of coords[id]) maxAbs = Math.max(maxAbs, Math.abs(c));
  const k = maxAbs > 0 ? 40 / maxAbs : 1;
  ids.forEach(id => { S.basePos[id] = coords[id].map(c => c * k); });
  S.fieldSads = ids.map(id => ({ id }));
  S.visible = new Set(ids.filter(id => isConfirmed(S.idToRec[id])));
  if (S.visible.size === 0) S.visible = new Set(ids);   // never start empty

  S.axisStats = ROSE_AXES.map(([, , g]) => S.fieldSads.map(d => Number(g(S.idToRec[d.id]))).filter(x => isFinite(x)).sort((a, b) => a - b));
  S.liveAxes = ROSE_AXES.map((_, i) => i).filter(i => S.axisStats[i].length > 0);

  document.getElementById('pca-var').textContent =
    varExplained.slice(0, 3).map((v, i) => `PC${i + 1} ${(v * 100).toFixed(1)}%`).join('   ');
  document.getElementById('field-count').textContent = `${ids.length} districts`;
  loading.style.display = 'none';

  initThree(); buildPoints(); buildLegend(); buildSelector(); wireUI(); initInfoUI(); initMapUI(); initDrawUI();
  animate();
}

// ── three scene ────────────────────────────────────────────────────────────────
function makeGlowTexture() {
  const c = document.createElement('canvas'); c.width = c.height = 64;
  const ctx = c.getContext('2d');
  const g = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
  g.addColorStop(0, 'rgba(255,255,255,1)');
  g.addColorStop(0.22, 'rgba(255,255,255,0.65)');
  g.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = g; ctx.fillRect(0, 0, 64, 64);
  const tex = new THREE.CanvasTexture(c); tex.needsUpdate = true; return tex;
}

function initThree() {
  const wrap = document.getElementById('field');
  const w = wrap.clientWidth, h = wrap.clientHeight;
  S.scene = new THREE.Scene();
  S.scene.fog = new THREE.FogExp2(0x0b0a09, 0.0042);

  S.camera = new THREE.PerspectiveCamera(52, w / h, 0.1, 3000);
  S.camera.position.set(72, 46, 96);

  S.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  S.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  S.renderer.setSize(w, h);
  wrap.appendChild(S.renderer.domElement);

  S.labelRenderer = new CSS2DRenderer();
  S.labelRenderer.setSize(w, h);
  S.labelRenderer.domElement.className = 'label-layer';
  wrap.appendChild(S.labelRenderer.domElement);

  S.controls = new OrbitControls(S.camera, S.renderer.domElement);
  S.controls.enableDamping = true; S.controls.dampingFactor = 0.08;
  S.controls.autoRotate = true; S.controls.autoRotateSpeed = 0.5;
  S.controls.minDistance = 20; S.controls.maxDistance = 400;

  S.scene.add(new THREE.AmbientLight(0xffffff, 0.9));
  const key = new THREE.DirectionalLight(0xffffff, 0.5); key.position.set(1, 1.4, 0.8); S.scene.add(key);

  // glow texture for star-like points
  S.glowTex = makeGlowTexture();

  // starfield backdrop — faint, slowly parallax-rotating
  const starN = 1400, sg = new THREE.BufferGeometry(), sp = new Float32Array(starN * 3);
  for (let i = 0; i < starN; i++) {
    const r = 180 + Math.random() * 520, th = Math.random() * Math.PI * 2, ph = Math.acos(2 * Math.random() - 1);
    sp[i * 3] = r * Math.sin(ph) * Math.cos(th);
    sp[i * 3 + 1] = r * Math.cos(ph);
    sp[i * 3 + 2] = r * Math.sin(ph) * Math.sin(th);
  }
  sg.setAttribute('position', new THREE.BufferAttribute(sp, 3));
  S.stars = new THREE.Points(sg, new THREE.PointsMaterial({
    color: 0xbfc6d4, size: 1.1, sizeAttenuation: true, transparent: true, opacity: 0.5, depthWrite: false }));
  S.scene.add(S.stars);

  S.raycaster = new THREE.Raycaster();
  const dom = S.renderer.domElement;
  dom.addEventListener('pointermove', onPointerMove);
  dom.addEventListener('pointerdown', () => { S.controls.autoRotate = false; syncBtn('btn-rotate', false); });
  dom.addEventListener('click', onClick);
  window.addEventListener('resize', onResize);
}

function buildPoints() {
  const geo = new THREE.SphereGeometry(0.85, 24, 24);
  S.fieldSads.forEach((d, i) => {
    const rec = S.idToRec[d.id];
    const color = recColor(rec);
    const mat = new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.6, roughness: 0.4 });
    const m = new THREE.Mesh(geo, mat);
    const p = S.basePos[d.id]; m.position.set(p[0], p[1], p[2]);
    m.scale.setScalar(0.001);
    m.userData = { id: d.id, idx: i, phase: Math.random() * Math.PI * 2,
      typoKey: typoKey(effTypo(rec) || 'Unconfirmed'), created: !!rec.created };
    S.scene.add(m); S.meshes.push(m); S.idToMesh[d.id] = m;

    // glow halo (additive sprite) — the visible "star"
    const glow = new THREE.Sprite(new THREE.SpriteMaterial({
      map: S.glowTex, color, transparent: true, opacity: 0.85,
      blending: THREE.AdditiveBlending, depthWrite: false }));
    glow.scale.setScalar(6); m.userData.glow = glow; m.userData.glowBase = 6;
    m.add(glow);

    const el = document.createElement('div'); el.className = 'pt-label'; el.textContent = shortName(rec);
    const lbl = new CSS2DObject(el); lbl.position.set(0, 2.4, 0); lbl.visible = false;
    m.add(lbl); S.labelOf[d.id] = lbl;
  });
  buildConstellation();
}

// faint star-chart web: link each district to its 2 nearest (full-D distance)
function buildConstellation() {
  if (!S.distance) return;
  const segs = [];
  for (const d of S.fieldSads) {
    const a = S.basePos[d.id]; if (!a) continue;
    for (const nb of neighbors(d.id, 2)) {
      const b = S.basePos[nb.id]; if (!b) continue;
      segs.push(a[0], a[1], a[2], b[0], b[1], b[2]);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(segs), 3));
  S.constellation = new THREE.LineSegments(g, new THREE.LineBasicMaterial({
    color: 0x8aa0c0, transparent: true, opacity: 0.16,
    blending: THREE.AdditiveBlending, depthWrite: false }));
  S.scene.add(S.constellation);
}

function shortName(rec) {
  const parts = rec.sad_id.split('_');
  // Drawn districts carry their location in the 3rd id segment; the saved
  // sad_name is the generic "Drawn district", so derive a real name from the id.
  const drawn = /drawn/i.test(rec.sad_id) || /^drawn[\s-]?district$/i.test((rec.sad_name || '').trim());
  if (drawn) {
    const loc = (parts[2] || parts[1] || '').replace(/-/g, ' ').trim();
    if (loc) return loc;
  }
  if (rec.sad_name && rec.sad_name !== rec.sad_id) return rec.sad_name;
  return (parts[1] || rec.sad_id).replace(/-/g, ' ');
}
function cityOf(rec) {
  // for drawn districts the location is already the name, so don't repeat it
  if (/drawn/i.test(rec.sad_id)) return '';
  const p = rec.sad_id.split('_'); return (p[2] || '').replace(/-/g, ' ');
}

function buildLegend() {
  // unique typology labels actually present (preserve original casing for display)
  const seen = new Map();
  let created = 0;
  for (const d of S.fieldSads) {
    const rec = S.idToRec[d.id];
    const t = effTypo(rec) || 'Unconfirmed';
    if (!seen.has(typoKey(t))) seen.set(typoKey(t), t);
    if (rec.created) created++;
  }
  S._typoCount = seen.size;
  if (!S.typoOn) S.typoOn = new Set(seen.keys());
  const wrap = document.getElementById('legend'); wrap.innerHTML = '';
  for (const [key, label] of seen) {
    const on = S.typoOn.has(key);
    const row = document.createElement('div');
    row.className = 'legend-row clk' + (on ? '' : ' off');
    row.innerHTML = `<span class="dot" style="background:${hex(typoColor(label))}"></span>${label}`;
    row.title = 'Click to toggle · Shift-click to isolate';
    row.addEventListener('click', (e) => {
      if (e.shiftKey || e.metaKey) S.typoOn = new Set([key]);
      else {
        if (S.typoOn.has(key)) S.typoOn.delete(key); else S.typoOn.add(key);
        if (S.typoOn.size === 0) S.typoOn = new Set(seen.keys());
      }
      buildLegend();
    });
    wrap.appendChild(row);
  }
  const ctrl = document.createElement('div'); ctrl.className = 'legend-ctrl';
  ctrl.innerHTML = `<a id="typo-all">show all</a>` +
    (created ? ` · <a id="typo-created" class="${S.showCreated ? 'on' : ''}">created (${created})</a>` : '');
  wrap.appendChild(ctrl);
  const a = ctrl.querySelector('#typo-all');
  if (a) a.addEventListener('click', () => { S.typoOn = new Set(seen.keys()); buildLegend(); });
  const c = ctrl.querySelector('#typo-created');
  if (c) c.addEventListener('click', () => { S.showCreated = !S.showCreated; buildLegend(); });
  applyVisibility();
}

// visibility = in the district selector set AND not filtered out by the legend
function applyVisibility() {
  const filtering = S.typoOn && S.typoOn.size > 0 && S.typoOn.size < S._typoCount;
  for (const m of S.meshes) {
    const inVis = !S.visible || S.visible.has(m.userData.id);
    const okT = !filtering || S.typoOn.has(m.userData.typoKey);
    m.userData._dim = !(inVis && okT);
  }
  if (S.constellation) S.constellation.material.opacity = (filtering || (S.visible && S.visible.size < S.meshes.length)) ? 0.04 : 0.16;
  refreshVisuals();
}

// ── selection slot bookkeeping ───────────────────────────────────────────────
function assignSlot(id) {
  const used = new Set(Object.values(S.slotOf));
  for (let i = 0; i < SLOTS.length; i++) if (!used.has(i)) { S.slotOf[id] = i; return; }
}
function slotColor(id) { const s = S.slotOf[id]; return s == null ? 0xffffff : SLOTS[s]; }

// ── interaction ────────────────────────────────────────────────────────────────
function onPointerMove(e) {
  const r = S.renderer.domElement.getBoundingClientRect();
  S.pointer.x = ((e.clientX - r.left) / r.width) * 2 - 1;
  S.pointer.y = -((e.clientY - r.top) / r.height) * 2 + 1;
}
function pick() {
  S.raycaster.setFromCamera(S.pointer, S.camera);
  const hits = S.raycaster.intersectObjects(S.meshes.filter(m => !m.userData._dim), false);
  return hits.length ? hits[0].object.userData.id : null;
}
function onClick() { const id = pick(); if (id) toggleSelect(id); }

function toggleSelect(id) {
  const i = S.selected.indexOf(id);
  if (i >= 0) { S.selected.splice(i, 1); delete S.slotOf[id]; }
  else {
    if (S.selected.length >= MAX_SEL) { const drop = S.selected.pop(); delete S.slotOf[drop]; }
    S.selected.unshift(id); assignSlot(id);
  }
  S.controls.autoRotate = false; syncBtn('btn-rotate', false);
  refreshVisuals(); renderPanel();
}

function refreshVisuals() {
  for (const m of S.meshes) {
    const id = m.userData.id, sel = S.selected.includes(id), hov = S.hovered === id;
    const dim = m.userData._dim;
    const base = S.pointSize;
    m.scale.setScalar(base * (sel ? 1.7 : hov ? 1.35 : 1) * (m.userData._introK ?? 1));
    m.material.emissiveIntensity = dim ? 0.04 : (sel ? 0.85 : hov ? 0.55 : 0.3);
    const lbl = S.labelOf[id];
    if (lbl) { lbl.visible = !dim && (S.showAllLabels || sel || hov); lbl.element.classList.toggle('sel', sel); }
  }
  // selection rings in slot colors
  for (const r of S.rings) S.scene.remove(r);
  S.rings = [];
  for (const id of S.selected) {
    const m = S.idToMesh[id]; if (!m) continue;
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(1.9 * S.pointSize, 2.35 * S.pointSize, 32),
      new THREE.MeshBasicMaterial({ color: slotColor(id), side: THREE.DoubleSide, transparent: true, opacity: 0.95 }));
    ring.position.copy(m.position); ring.userData._billboard = true;
    S.scene.add(ring); S.rings.push(ring);
  }
  drawConnections();
}

function drawConnections() {
  for (const l of S.lines) S.scene.remove(l);
  S.lines = [];
  if (!S.showConnections || !S.selected.length) return;
  const primary = S.selected[0], a = S.idToMesh[primary];
  if (!a) return;
  for (const nb of neighbors(primary, NEIGHBOR_K)) {
    const b = S.idToMesh[nb.id]; if (!b) continue;
    const g = new THREE.BufferGeometry().setFromPoints([a.position.clone(), b.position.clone()]);
    S.lines.push(addLine(g, slotColor(primary)));
  }
}
function addLine(g, color) {
  const l = new THREE.Line(g, new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.4 }));
  S.scene.add(l); return l;
}

function neighbors(id, k) {
  const row = S.distance[id]; if (!row) return [];
  return Object.entries(row).filter(([o]) => o !== id && S.idToRec[o])
    .map(([o, d]) => ({ id: o, dist: Number(d) })).filter(x => isFinite(x.dist))
    .sort((a, b) => a.dist - b.dist).slice(0, k);
}

// ── wind rose ────────────────────────────────────────────────────────────────
function axisPct(rec, i) {
  const v = Number(ROSE_AXES[i][2](rec)); if (!isFinite(v)) return null;
  const arr = S.axisStats[i]; if (!arr || !arr.length) return null;
  let c = 0; for (const x of arr) if (x <= v) c++; return c / arr.length;
}
function roseSVG(ids) {
  const live = S.liveAxes.length ? S.liveAxes : ROSE_AXES.map((_, i) => i);
  const N = live.length; if (N < 3) return '<div class="empty">Not enough comparable axes.</div>';
  const W = 320, H = 300, cx = 160, cy = 150, R = 88, LR = 1.18;
  const ang = p => (-90 + p * 360 / N) * Math.PI / 180;
  const pt = (p, v) => [cx + R * v * Math.cos(ang(p)), cy + R * v * Math.sin(ang(p))];
  let s = `<svg viewBox="0 0 ${W} ${H}" class="rose">`;
  for (const f of [0.25, 0.5, 0.75, 1]) {
    const ring = live.map((_, p) => pt(p, f).map(n => n.toFixed(1)).join(',')).join(' ');
    s += `<polygon class="ring" points="${ring}"/>`;
  }
  live.forEach((ax, p) => {
    const [x, y] = pt(p, 1), [lx, ly] = pt(p, LR);
    s += `<line class="spoke" x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}"/>`;
    s += `<text class="axlab" data-info="${ROSE_AXES[ax][0]}" x="${lx.toFixed(1)}" y="${ly.toFixed(1)}" text-anchor="middle" dominant-baseline="middle">${ROSE_AXES[ax][1]}</text>`;
  });
  // draw primary last so it reads on top
  [...ids].reverse().forEach(id => {
    const col = hex(slotColor(id));
    const pts = live.map((ax, p) => { const v = axisPct(S.idToRec[id], ax); return pt(p, v == null ? 0 : v); });
    const poly = pts.map(q => q.map(n => n.toFixed(1)).join(',')).join(' ');
    s += `<polygon class="dpoly" points="${poly}" style="stroke:${col};fill:${col}"/>`;
    pts.forEach(q => { s += `<circle class="dvert" cx="${q[0].toFixed(1)}" cy="${q[1].toFixed(1)}" r="2.2" style="fill:${col}"/>`; });
  });
  s += `</svg>`;
  return s;
}

// ── panel ──────────────────────────────────────────────────────────────────────
function renderPanel() {
  const panel = document.getElementById('panel');
  document.body.classList.toggle('has-selection', S.selected.length > 0);
  if (!S.selected.length) { panel.querySelector('.panel-body').innerHTML = ''; document.getElementById('panel-chips').innerHTML = '<span class="muted">Click districts in the field to compare</span>'; return; }

  const primary = S.selected[0], pRec = S.idToRec[primary];

  document.getElementById('panel-chips').innerHTML = S.selected.map(id => {
    const r = S.idToRec[id];
    return `<span class="chip"><span class="chip-key" style="background:${hex(slotColor(id))}"></span>
      <span class="chip-name">${shortName(r)}</span><span class="x" data-x="${id}">×</span></span>`;
  }).join('') +
    `<a class="to-viewer" href="../_ui/?sad=${encodeURIComponent(primary)}" title="Open ${shortName(pRec)} in the district viewer">Open in viewer ↗</a>`;

  const nb = neighbors(primary, NEIGHBOR_K);
  const maxD = nb.length ? Math.max(...nb.map(x => x.dist)) : 1;
  const nbRows = nb.map(x => {
    const r = S.idToRec[x.id], inSel = S.selected.includes(x.id);
    const w = (100 * (1 - x.dist / (maxD * 1.06))).toFixed(0);
    return `<div class="nb-row ${inSel ? 'in' : ''}" data-add="${x.id}">
      <div class="nb-name">${shortName(r)}<small>${cityOf(r)}</small></div>
      <div class="nb-bar"><span style="width:${w}%"></span></div>
      <div class="nb-d mono">${x.dist.toFixed(2)}</div></div>`;
  }).join('');

  const head = `<th class="rowlab"></th>` + S.selected.map(id =>
    `<th><span class="th-key" style="background:${hex(slotColor(id))}"></span>${shortName(S.idToRec[id])}</th>`).join('');
  const body = METRICS.map(([label, get, kind, info]) =>
    `<tr><th class="rowlab"${info ? ` data-info="${info}"` : ''}>${label}</th>` +
    S.selected.map(id => `<td>${fmt(get(S.idToRec[id]), kind)}</td>`).join('') + `</tr>`).join('');

  panel.querySelector('.panel-body').innerHTML = `
    ${typologySectionHTML(primary)}
    ${parcelSectionHTML(primary)}
    <section class="sec">
      <div class="sec-h">Profile <span data-info="rose" class="info-link">percentile within corpus ⓘ</span></div>
      ${roseSVG(S.selected)}
    </section>
    <section class="sec">
      <div class="sec-h">Nearest districts <span>full-D · ${shortName(pRec)}</span></div>
      <div class="nb-list">${nbRows || '<div class="empty">No distance data.</div>'}</div>
    </section>
    <section class="sec">
      <div class="sec-h">Comparison</div>
      <div class="cmp-scroll"><table class="cmp"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>
    </section>`;

  panel.querySelectorAll('[data-x]').forEach(el => el.addEventListener('click', e => { e.stopPropagation(); toggleSelect(el.dataset.x); }));
  panel.querySelectorAll('[data-add]').forEach(el => el.addEventListener('click', () => { if (!S.selected.includes(el.dataset.add)) toggleSelect(el.dataset.add); }));
  wireInfo(panel);
  wireTypology(panel, primary);
}

// ── UI wiring (toggles, sliders, splitter) ───────────────────────────────────
function syncBtn(id, on) { document.getElementById(id).classList.toggle('on', on); }
function wireUI() {
  const rot = document.getElementById('btn-rotate'); rot.classList.add('on');
  rot.addEventListener('click', () => { S.controls.autoRotate = !S.controls.autoRotate; syncBtn('btn-rotate', S.controls.autoRotate); });
  const lab = document.getElementById('btn-labels');
  lab.addEventListener('click', () => { S.showAllLabels = !S.showAllLabels; syncBtn('btn-labels', S.showAllLabels); refreshVisuals(); });
  const con = document.getElementById('btn-conn'); con.classList.add('on');
  con.addEventListener('click', () => { S.showConnections = !S.showConnections; syncBtn('btn-conn', S.showConnections); drawConnections(); });
  document.getElementById('btn-reset').addEventListener('click', () => {
    S.selected = []; S.slotOf = {}; refreshVisuals(); renderPanel();
    S.camera.position.set(72, 46, 96); S.controls.target.set(0, 0, 0);
  });

  const spread = document.getElementById('sl-spread'), size = document.getElementById('sl-size');
  spread.addEventListener('input', () => { S.spread = +spread.value; applySpread(); });
  size.addEventListener('input', () => { S.pointSize = +size.value; refreshVisuals(); });

  initSplitter();
}
function applySpread() {
  for (const m of S.meshes) { const p = S.basePos[m.userData.id]; m.position.set(p[0] * S.spread, p[1] * S.spread, p[2] * S.spread); }
  if (S.constellation) S.constellation.scale.setScalar(S.spread);
  refreshVisuals();
}
function initSplitter() {
  const sp = document.getElementById('splitter'), panel = document.getElementById('panel');
  let dragging = false;
  sp.addEventListener('pointerdown', e => { dragging = true; sp.setPointerCapture(e.pointerId); document.body.classList.add('resizing'); });
  sp.addEventListener('pointermove', e => {
    if (!dragging) return;
    const w = Math.min(Math.max(window.innerWidth - e.clientX, 320), window.innerWidth * 0.62);
    panel.style.width = w + 'px'; onResize();
  });
  sp.addEventListener('pointerup', e => { dragging = false; sp.releasePointerCapture(e.pointerId); document.body.classList.remove('resizing'); });
}

function onResize() {
  const wrap = document.getElementById('field'); const w = wrap.clientWidth, h = wrap.clientHeight;
  if (!w || !h) return;
  S.camera.aspect = w / h; S.camera.updateProjectionMatrix();
  S.renderer.setSize(w, h); S.labelRenderer.setSize(w, h);
}

// ── loop ──────────────────────────────────────────────────────────────────────
function animate() {
  requestAnimationFrame(animate);
  // staggered intro scale-in
  const t = performance.now() - S.t0;
  let introDone = true;
  for (const m of S.meshes) {
    const delay = m.userData.idx * 18, dur = 520;
    const k = Math.max(0, Math.min(1, (t - delay) / dur));
    const e = 1 - Math.pow(1 - k, 3);
    m.userData._introK = e;
    if (k < 1) introDone = false;
  }
  if (!introDone) refreshIntroScale();

  const id = pick();
  if (id !== S.hovered) { S.hovered = id; refreshVisuals(); S.renderer.domElement.style.cursor = id ? 'pointer' : 'grab'; }

  // billboard the selection rings toward camera
  for (const r of S.rings) r.quaternion.copy(S.camera.quaternion);

  // twinkle the star glows + slow parallax on the starfield
  const tt = performance.now() * 0.001;
  for (const m of S.meshes) {
    const glow = m.userData.glow; if (!glow) continue;
    if (m.userData._dim) { glow.material.opacity = 0.05; glow.scale.setScalar((m.userData.glowBase || 6) * 0.6); continue; }
    const sel = S.selected.includes(m.userData.id), hov = S.hovered === m.userData.id;
    const tw = 0.82 + 0.18 * Math.sin(tt * 1.5 + m.userData.phase);
    const base = (m.userData.glowBase || 6) * (sel ? 1.8 : hov ? 1.4 : 1) * (m.userData._introK ?? 1);
    glow.scale.setScalar(base * tw);
    glow.material.opacity = (sel ? 1 : hov ? 0.95 : 0.78) * tw;
  }
  if (S.stars) S.stars.rotation.y += 0.0002;

  S.controls.update();
  S.renderer.render(S.scene, S.camera);
  S.labelRenderer.render(S.scene, S.camera);
}
function refreshIntroScale() {
  for (const m of S.meshes) {
    const id = m.userData.id, sel = S.selected.includes(id), hov = S.hovered === id;
    m.scale.setScalar(S.pointSize * (sel ? 1.7 : hov ? 1.35 : 1) * (m.userData._introK ?? 1));
  }
}

// ── metric info: hover tooltip + click card ─────────────────────────────────
function initInfoUI() {
  const back = document.createElement('div'); back.id = 'm-backdrop'; back.className = 'm-backdrop';
  const card = document.createElement('div'); card.id = 'm-card'; card.className = 'm-card';
  const tip = document.createElement('div'); tip.id = 'm-tip'; tip.className = 'm-tip';
  document.body.append(back, card, tip);
  back.addEventListener('click', closeInfo);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') { closeInfo(); closeMap(); } });
}
function wireInfo(root) {
  root.querySelectorAll('[data-info]').forEach(el => {
    el.classList.add('has-info');
    el.addEventListener('mouseenter', () => showTip(el, el.getAttribute('data-info')));
    el.addEventListener('mousemove', moveTip);
    el.addEventListener('mouseleave', hideTip);
    el.addEventListener('click', e => { e.stopPropagation(); openInfo(el.getAttribute('data-info')); });
  });
}
function showTip(el, id) {
  const info = INFO[id]; if (!info) return;
  const tip = document.getElementById('m-tip');
  tip.innerHTML = `<strong>${info.title}</strong><span>${info.body.split('.')[0]}. Click for source.</span>`;
  tip.classList.add('show'); positionTip(el);
}
function positionTip(el) {
  const tip = document.getElementById('m-tip'), r = el.getBoundingClientRect();
  let x = r.left + r.width / 2, y = r.top - 10;
  tip.style.left = Math.min(Math.max(x, 130), window.innerWidth - 130) + 'px';
  tip.style.top = y + 'px';
}
let _tipEl = null;
function moveTip(e) { _tipEl = e.currentTarget; }
function hideTip() { document.getElementById('m-tip').classList.remove('show'); }
function openInfo(id) {
  const info = INFO[id]; if (!info) return;
  hideTip();
  const primary = S.selected[0], rec = primary ? S.idToRec[primary] : null;
  const col = CENSUS_COL[id];
  const canMap = col && rec && rec.artifacts && rec.artifacts.census_geojson;
  const mapBtn = canMap ? `<button class="m-mapbtn" data-map="${id}">View ${shortName(rec)} block groups on the census map →</button>` : '';
  const card = document.getElementById('m-card');
  card.innerHTML = `<button class="m-x" aria-label="Close">×</button>
    <div class="m-title">${info.title}</div>
    <div class="m-src mono">${info.src}</div>
    <p class="m-body">${info.body}</p>${mapBtn}`;
  card.querySelector('.m-x').addEventListener('click', closeInfo);
  const mb = card.querySelector('.m-mapbtn');
  if (mb) mb.addEventListener('click', () => { closeInfo(); openMap(primary, id); });
  card.classList.add('show'); document.getElementById('m-backdrop').classList.add('show');
}
function closeInfo() {
  document.getElementById('m-card')?.classList.remove('show');
  document.getElementById('m-backdrop')?.classList.remove('show');
}

// ── census-area map drill-down (Leaflet) ─────────────────────────────────────
const CENSUS_COL = { income:'median_household_income', age:'median_age', pop:'total_pop', renter:'pct_renter', educ:'pct_bachelors', income_pctile:'median_household_income' };
const MAP_METRICS = [
  ['median_household_income','Median household income','usd'],
  ['median_age','Median age','num1'],
  ['total_pop','Population','int'],
  ['median_home_value','Median home value','usd'],
  ['median_gross_rent','Median gross rent','usd'],
  ['pct_renter','Renter-occupied %','pct'],
  ['pct_bachelors',"Bachelor's+ %",'pct'],
  ['unemployment_rate','Unemployment %','pct'],
  ['pct_white','White %','pct'],
  ['pct_black','Black %','pct'],
  ['pct_asian','Asian %','pct'],
  ['pct_hispanic','Hispanic %','pct'],
];
const MAP = { map:null, bgLayer:null, boundaryLayer:null, cache:{}, geo:null, district:null, col:null, overlays:[], layerControl:null };

function initMapUI() {
  const m = document.createElement('div'); m.id = 'cmap-modal'; m.className = 'cmap-modal';
  m.innerHTML = `
    <div class="cmap-head">
      <div class="cmap-title"><span id="cmap-name"></span><small>census block groups · ACS 2019–2023 5-yr</small></div>
      <div class="cmap-tools">
        <label class="slider">Shade by <select id="cmap-metric"></select></label>
        <button id="cmap-close" class="btn ghost">Close</button>
      </div>
    </div>
    <div id="cmap"></div>
    <div id="cmap-legend" class="cmap-legend"></div>`;
  document.body.appendChild(m);
  document.getElementById('cmap-close').addEventListener('click', closeMap);
  const sel = document.getElementById('cmap-metric');
  MAP_METRICS.forEach(([c, l]) => { const o = document.createElement('option'); o.value = c; o.textContent = l; sel.appendChild(o); });
  sel.addEventListener('change', () => shadeBy(sel.value));
}

async function loadGeo(url) {
  if (MAP.cache[url]) return MAP.cache[url];
  const g = await fetch(url).then(r => { if (!r.ok) throw new Error('not found: ' + url); return r.json(); });
  MAP.cache[url] = g; return g;
}

async function openMap(districtId, metricId) {
  const rec = S.idToRec[districtId];
  if (!rec || !rec.artifacts || !rec.artifacts.census_geojson) return;
  MAP.district = districtId;
  document.getElementById('cmap-modal').classList.add('show');
  document.getElementById('cmap-name').textContent = shortName(rec);
  const L = window.L;
  if (!MAP.map) {
    MAP.map = L.map('cmap', { zoomControl: true });
    L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
      { maxZoom: 19, attribution: '© OpenStreetMap · © CARTO' }).addTo(MAP.map);
  }
  setTimeout(() => MAP.map.invalidateSize(), 60);
  try {
    const geo = await loadGeo('../' + rec.artifacts.census_geojson);
    MAP.geo = geo;
    if (MAP.bgLayer) MAP.map.removeLayer(MAP.bgLayer);
    MAP.bgLayer = L.geoJSON(geo, { style: () => ({ color: '#fff', weight: 0.6, fillOpacity: 0.78 }), onEachFeature: bindFeature }).addTo(MAP.map);
    if (MAP.boundaryLayer) { MAP.map.removeLayer(MAP.boundaryLayer); MAP.boundaryLayer = null; }
    if (rec.artifacts.sad_boundary) {
      try {
        const b = await loadGeo('../' + rec.artifacts.sad_boundary);
        MAP.boundaryLayer = L.geoJSON(b, { style: { color: '#1b1813', weight: 2.5, fill: false, dashArray: '5 3' } }).addTo(MAP.map);
      } catch (e) { /* boundary optional */ }
    }
    await addContextLayers(districtId);
    MAP.map.fitBounds(MAP.bgLayer.getBounds(), { padding: [26, 26] });
    const col = CENSUS_COL[metricId] || 'median_household_income';
    document.getElementById('cmap-metric').value = col;
    shadeBy(col);
  } catch (e) {
    document.getElementById('cmap-legend').textContent = 'Could not load census geometry — ' + e.message;
  }
}

function bindFeature(f, layer) {
  layer.on('mouseover', () => layer.setStyle({ weight: 1.8, color: '#1b1813' }));
  layer.on('mouseout', () => layer.setStyle({ weight: 0.6, color: '#fff' }));
}
function lerp(a, b, t) { return Math.round(a + (b - a) * t); }
function ramp(t) { const A = [244, 238, 229], B = [255, 90, 69]; return `rgb(${lerp(A[0], B[0], t)},${lerp(A[1], B[1], t)},${lerp(A[2], B[2], t)})`; }
function shadeBy(col) {
  if (!MAP.bgLayer || !MAP.geo) return;
  MAP.col = col;
  const meta = MAP_METRICS.find(m => m[0] === col) || [col, col, 'num1'];
  const vals = MAP.geo.features.map(f => f.properties[col]).filter(v => v != null && isFinite(v));
  const legend = document.getElementById('cmap-legend');
  if (!vals.length) { legend.innerHTML = `<span class="lg-label">${meta[1]}</span><span class="muted">no data for these block groups</span>`; return; }
  const min = Math.min(...vals), max = Math.max(...vals), span = (max - min) || 1;
  MAP.bgLayer.eachLayer(layer => {
    const v = layer.feature.properties[col];
    const t = (v == null || !isFinite(v)) ? null : (v - min) / span;
    layer.setStyle({ fillColor: t == null ? '#e9e4db' : ramp(t), fillOpacity: t == null ? 0.35 : 0.82, weight: 0.6, color: '#fff' });
    const g = String(layer.feature.properties.GEOID || '');
    layer.bindTooltip(`<b>${meta[1]}</b><br>${fmt(v, meta[2])}<br><span class="t-sub">${layer.feature.properties.zone} · BG …${g.slice(-7)}</span>`, { sticky: true });
  });
  legend.innerHTML = `<span class="lg-label">${meta[1]}</span><span class="lg-min">${fmt(min, meta[2])}</span><span class="lg-bar"></span><span class="lg-max">${fmt(max, meta[2])}</span>`;
}
function closeMap() { document.getElementById('cmap-modal')?.classList.remove('show'); }

// pull the district's physical layers straight from the viewer's manifest —
// one shared source of truth — and overlay them legibly with a toggle control.
async function addContextLayers(districtId) {
  const L = window.L;
  (MAP.overlays || []).forEach(o => MAP.map.removeLayer(o.layer));
  if (MAP.layerControl) { MAP.map.removeControl(MAP.layerControl); MAP.layerControl = null; }
  MAP.overlays = [];
  let layers = null;
  try {
    const vm = await loadGeo('../_ui/manifest.json');
    const entry = (vm.sads || []).find(s => s.sad_id === districtId);
    layers = entry && entry.layers;
  } catch (e) { return; }
  if (!layers) return;

  const STYLE = {
    parks:    { kind: 'poly', def: { color: '#4f9e6e', weight: 0.6, fillColor: '#5fae7e', fillOpacity: 0.34 } },
    parking:  { kind: 'poly', def: { color: '#9a9088', weight: 0.5, fillColor: '#b4a99c', fillOpacity: 0.30 } },
    buildings:{ kind: 'poly', def: { color: '#2a2520', weight: 0.5, fillColor: '#3a342d', fillOpacity: 0.42 } },
    roads:    { kind: 'line', def: { color: '#6a635a', weight: 1.0, opacity: 0.75 } },
    pois:     { kind: 'point', def: { radius: 2.8, color: '#ff5a45', weight: 0, fillOpacity: 0.9 } },
  };
  const LABEL = { parks: 'Parks', parking: 'Parking', buildings: 'Buildings', roads: 'Streets', pois: 'POIs' };
  const overlays = {};
  // draw order: parks/parking/buildings (fills) under streets/POIs
  for (const key of ['parks', 'parking', 'buildings', 'roads', 'pois']) {
    const rec = layers[key]; if (!rec || !rec.path) continue;
    try {
      const gj = await loadGeo('../' + rec.path);
      const st = STYLE[key];
      const layer = st.kind === 'point'
        ? L.geoJSON(gj, { pointToLayer: (f, ll) => L.circleMarker(ll, st.def) })
        : L.geoJSON(gj, { style: () => st.def });
      overlays[LABEL[key]] = layer;
      if (key === 'buildings' || key === 'parks') layer.addTo(MAP.map);  // sensible defaults
      MAP.overlays.push({ key, layer });
    } catch (e) { /* layer optional */ }
  }
  if (Object.keys(overlays).length) {
    MAP.layerControl = L.control.layers(null, overlays, { collapsed: false, position: 'topright' }).addTo(MAP.map);
  }
}

// ── draw a district → analyze → match to existing SADs (census-first v1) ─────
const MATCH_API = 'http://localhost:8000';   // local sad_match_server.py
const DRAW = { map:null, group:null, bgLayer:null, ready:false };

function initDrawUI() {
  const m = document.createElement('div'); m.id = 'draw-modal'; m.className = 'cmap-modal draw-modal';
  m.innerHTML = `
    <div class="cmap-head">
      <div class="cmap-title"><span>Draw a district</span><small>sketch a boundary · analyze ACS · match your corpus</small></div>
      <div class="cmap-tools"><button id="draw-close" class="btn ghost">Close</button></div>
    </div>
    <div class="draw-body">
      <div id="dmap"></div>
      <aside class="draw-side" id="draw-side">
        <div class="draw-hint">
          <p>Use the polygon or rectangle tool (top-left of the map) to sketch a boundary over any U.S. area.</p>
          <p class="note faint">On finish, the area's census block groups are pulled and weighted into a demographic profile, then ranked against your existing districts.</p>
        </div>
      </aside>
    </div>`;
  document.body.appendChild(m);
  document.getElementById('draw-close').addEventListener('click', () => m.classList.remove('show'));
  document.getElementById('btn-draw').addEventListener('click', openDraw);
}

function openDraw() {
  const modal = document.getElementById('draw-modal'); modal.classList.add('show');
  const L = window.L;
  if (!DRAW.map) {
    DRAW.map = L.map('dmap', { zoomControl: true }).setView([42.331, -83.05], 12);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
      { maxZoom: 19, attribution: '© OpenStreetMap · © CARTO' }).addTo(DRAW.map);
    DRAW.group = new L.FeatureGroup().addTo(DRAW.map);
    const ctl = new L.Control.Draw({
      position: 'topleft',
      draw: { polygon: { shapeOptions: { color: '#ff5a45', weight: 2 } },
              rectangle: { shapeOptions: { color: '#ff5a45', weight: 2 } },
              marker: false, polyline: false, circle: false, circlemarker: false },
      edit: { featureGroup: DRAW.group, edit: false }
    });
    DRAW.map.addControl(ctl);
    DRAW.map.on(L.Draw.Event.CREATED, e => {
      DRAW.group.clearLayers(); DRAW.group.addLayer(e.layer);
      analyzeDrawn(e.layer.toGeoJSON().geometry);
    });
  }
  setTimeout(() => DRAW.map.invalidateSize(), 60);
}

async function analyzeDrawn(geometry) {
  const side = document.getElementById('draw-side');
  side.innerHTML = `<div class="draw-busy"><div class="spinner"></div><p>Pulling ACS block groups and matching…</p><p class="note faint">First call can take a moment while Census data downloads.</p></div>`;
  let data;
  try {
    const resp = await fetch(MATCH_API + '/analyze', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ geometry, name: 'Drawn district' })
    });
    data = await resp.json();
  } catch (e) {
    side.innerHTML = `<div class="draw-hint"><p class="draw-err">Couldn't reach the match server.</p>
      <p class="note">Start it from your <code>code</code> folder:</p>
      <pre class="note mono">python sad_match_server.py --data-dir ..\\data</pre>
      <p class="note faint">It runs on ${MATCH_API}. Then draw again.</p></div>`;
    return;
  }
  if (!data.ok) { side.innerHTML = `<div class="draw-hint"><p class="draw-err">${data.error || 'Analysis failed.'}</p></div>`; return; }
  renderDrawResults(data);
}

function roseFromValues(entries) {       // entries: [[label, t01], ...]
  const N = entries.length; if (N < 3) return '';
  const W = 280, H = 250, cx = 140, cy = 125, R = 76, LR = 1.2;
  const ang = p => (-90 + p * 360 / N) * Math.PI / 180;
  const pt = (p, v) => [cx + R * v * Math.cos(ang(p)), cy + R * v * Math.sin(ang(p))];
  let s = `<svg viewBox="0 0 ${W} ${H}" class="rose">`;
  for (const f of [0.25, 0.5, 0.75, 1]) s += `<polygon class="ring" points="${entries.map((_, p) => pt(p, f).map(n => n.toFixed(1)).join(',')).join(' ')}"/>`;
  entries.forEach(([label], p) => {
    const [x, y] = pt(p, 1), [lx, ly] = pt(p, LR);
    s += `<line class="spoke" x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}"/>`;
    s += `<text class="axlab" x="${lx.toFixed(1)}" y="${ly.toFixed(1)}" text-anchor="middle" dominant-baseline="middle">${label}</text>`;
  });
  const pts = entries.map(([, t], p) => pt(p, t == null ? 0 : t));
  s += `<polygon class="dpoly" points="${pts.map(q => q.map(n => n.toFixed(1)).join(',')).join(' ')}" style="stroke:#ff5a45;fill:#ff5a45"/>`;
  pts.forEach(q => s += `<circle class="dvert" cx="${q[0].toFixed(1)}" cy="${q[1].toFixed(1)}" r="2.1" style="fill:#ff5a45"/>`);
  return s + `</svg>`;
}

function renderDrawResults(data) {
  const L = window.L, p = data.profile || {};
  // shade analyzed block groups on the map
  if (DRAW.bgLayer) DRAW.map.removeLayer(DRAW.bgLayer);
  const geo = data.blockgroups_geojson;
  if (geo && geo.features && geo.features.length) {
    const vals = geo.features.map(f => f.properties.median_household_income).filter(v => v != null && isFinite(v));
    const min = Math.min(...vals), max = Math.max(...vals), span = (max - min) || 1;
    DRAW.bgLayer = L.geoJSON(geo, {
      style: f => { const v = f.properties.median_household_income; const t = (v == null) ? null : (v - min) / span;
        return { color: '#fff', weight: 0.5, fillColor: t == null ? '#e9e4db' : ramp(t), fillOpacity: 0.7 }; }
    }).addTo(DRAW.map);
    DRAW.bgLayer.bringToBack();
    try { DRAW.map.fitBounds(DRAW.group.getBounds(), { padding: [40, 40] }); } catch (e) {}
  }
  const bgUsed = (p.block_groups_fully_inside || 0) + (p.block_groups_partial || 0);
  const stat = (label, v, kind) => `<div class="ds-row"><span>${label}</span><b>${fmt(v, kind)}</b></div>`;
  const roseEntries = Object.entries(data.percentiles || {}).map(([k, v]) => [k, v]);
  const matches = (data.matches || []).map(m => {
    const c = hex(typoColor(m.typology));
    const known = !!S.idToRec[m.sad_id];
    return `<div class="dm-row">
      <span class="dot" style="background:${c}"></span>
      <div class="dm-name">${m.sad_name || m.sad_id}<small>${m.region || ''} · dist ${m.distance}</small></div>
      <div class="dm-act">
        ${known ? `<button class="mini" data-sel="${m.sad_id}">Field</button>` : ''}
        <a class="mini" href="../_ui/?sad=${encodeURIComponent(m.sad_id)}" title="Open in viewer">Viewer ↗</a>
      </div></div>`;
  }).join('');

  document.getElementById('draw-side').innerHTML = `
    <div class="ds-sec">
      <div class="sec-h">Drawn profile <span>${bgUsed} block groups · ACS 5-yr</span></div>
      ${roseFromValues(roseEntries)}
      <div class="ds-stats">
        ${stat('Population', p.estimated_population, 'int')}
        ${stat('Median income', p.median_household_income_pop_weighted, 'usd')}
        ${stat('Median age', p.median_age_pop_weighted, 'num1')}
        ${stat('Renter-occupied', p.pct_renter_occupied, 'pct')}
        ${stat("Bachelor's+", p.pct_bachelors_or_higher, 'pct')}
        ${stat('Unemployment', p.unemployment_rate, 'pct')}
      </div>
    </div>
    <div class="ds-sec">
      <div class="sec-h">Nearest districts <span>demographic similarity</span></div>
      <div class="dm-list">${matches || '<div class="empty">No matches.</div>'}</div>
      <p class="note faint" style="margin-top:12px">${data.note || ''}</p>
    </div>`;

  document.getElementById('draw-side').querySelectorAll('[data-sel]').forEach(b =>
    b.addEventListener('click', () => {
      const id = b.dataset.sel;
      document.getElementById('draw-modal').classList.remove('show');
      if (!S.selected.includes(id)) toggleSelect(id); else renderPanel();
    }));
}

// ── district selector (left rail) ───────────────────────────────────────────────
function buildSelector() {
  const box = document.getElementById('district-select'); if (!box) return;
  const recs = S.fieldSads.map(d => S.idToRec[d.id]);
  const fam = r => effTypo(r);
  const conf = recs.filter(isConfirmed);
  const unconf = recs.filter(r => !isConfirmed(r));
  conf.sort((a, b) => (fam(a) || '').localeCompare(fam(b) || '') || shortName(a).localeCompare(shortName(b)));
  unconf.sort((a, b) => shortName(a).localeCompare(shortName(b)));
  const total = recs.length;
  const setCount = () => { const fc = document.getElementById('field-count'); if (fc) fc.textContent = `${S.visible.size}/${total} shown`; };
  const rowHTML = r => {
    const id = r.sad_id, on = !S.visible || S.visible.has(id), f = fam(r);
    return `<label class="ds-pick${on ? '' : ' off'}" data-name="${shortName(r).toLowerCase()}">
      <input type="checkbox" data-id="${id}"${on ? ' checked' : ''}>
      <span class="dot" style="background:${hex(typoColor(f || 'default'))}"></span>
      <span class="nm">${shortName(r)}</span>
      <span class="fam">${f || '\u2014'}</span></label>`;
  };
  box.innerHTML = `
    <div class="ds-head">
      <input type="text" id="ds-filter" class="ds-filter" placeholder="filter\u2026">
      <div class="ds-quick"><a data-q="all">all</a> \u00b7 <a data-q="conf">confirmed</a> \u00b7 <a data-q="none">none</a></div>
    </div>
    <div class="ds-list">
      ${conf.map(rowHTML).join('')}
      ${unconf.length ? `<div class="ds-div">Unconfirmed (${unconf.length})</div>` + unconf.map(rowHTML).join('') : ''}
    </div>`;
  setCount();
  box.querySelectorAll('input[type=checkbox]').forEach(cb => cb.addEventListener('change', () => {
    const id = cb.dataset.id;
    if (cb.checked) S.visible.add(id); else S.visible.delete(id);
    cb.closest('.ds-pick').classList.toggle('off', !cb.checked);
    setCount(); applyVisibility();
  }));
  const filt = box.querySelector('#ds-filter');
  if (filt) filt.addEventListener('input', () => {
    const q = filt.value.trim().toLowerCase();
    box.querySelectorAll('.ds-pick').forEach(el => { el.style.display = (!q || el.dataset.name.includes(q)) ? '' : 'none'; });
  });
  box.querySelectorAll('.ds-quick a').forEach(a => a.addEventListener('click', () => {
    const q = a.dataset.q;
    if (q === 'all') S.visible = new Set(recs.map(r => r.sad_id));
    else if (q === 'none') S.visible = new Set();
    else S.visible = new Set(conf.map(r => r.sad_id));
    buildSelector(); applyVisibility();
  }));
}

// ── typology: client-side k-NN suggestion + designer confirmation ────────────────
function suggestTypology(id) {
  const nb = neighbors(id, 40).filter(x => isConfirmed(S.idToRec[x.id]));
  if (!nb.length) return null;
  const top = nb.slice(0, SUGGEST_K);
  const vote = {};
  for (const x of top) { const f = effTypo(S.idToRec[x.id]); const w = 1 / (x.dist + 0.05); vote[f] = (vote[f] || 0) + w; }
  const tot = Object.values(vote).reduce((a, b) => a + b, 0) || 1;
  const ranked = Object.entries(vote).map(([f, w]) => ({ family: f, conf: w / tot })).sort((a, b) => b.conf - a.conf);
  return { best: ranked[0], ranked,
    neighbors: top.map(x => ({ name: shortName(S.idToRec[x.id]), family: effTypo(S.idToRec[x.id]), dist: x.dist })) };
}
function saveOverrides() { try { localStorage.setItem('sad_typo_overrides', JSON.stringify(S.typoOverride)); } catch (e) {} }
function setTypology(id, family) {
  const rec = S.idToRec[id]; if (!rec) return;
  family = (family || '').trim();
  if (family) { rec.typology = family; S.typoOverride[id] = family; }
  else { rec.typology = 'unspecified'; delete S.typoOverride[id]; }
  saveOverrides();
  const m = S.idToMesh[id];
  if (m) {
    const c = recColor(rec);
    m.material.color.setHex(c); m.material.emissive.setHex(c);
    m.userData.typoKey = typoKey(effTypo(rec) || 'Unconfirmed');
    if (m.userData.glow) m.userData.glow.material.color.setHex(c);
  }
  if (isConfirmed(rec) && S.visible) S.visible.add(id);
  // best-effort durable write (no-op if the match server isn't running)
  fetch(MATCH_API + '/set_typology', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sad_id: id, primary_typology: family || null })
  }).catch(() => {});
  S.typoOn = null;              // recompute legend so a new family appears + is on
  buildLegend(); buildSelector(); applyVisibility(); renderPanel();
}
function typologySectionHTML(id) {
  const rec = S.idToRec[id];
  const cur = effTypo(rec);
  const prim = _isUnspec(rec.typology) ? '' : String(rec.typology).trim();
  const fromSec = cur && !prim;
  const sug = suggestTypology(id);
  const opts = ['<option value="">\u2014 unconfirmed \u2014</option>']
    .concat(CANON.map(f => `<option value="${f}"${typoKey(f) === typoKey(prim) ? ' selected' : ''}>${f}</option>`)).join('');
  const curChip = cur
    ? `<span class="t-chip"><span class="dot" style="background:${hex(typoColor(cur))}"></span>${cur}${fromSec ? ' <small class="faint">(inferred)</small>' : ''}</span>`
    : `<span class="t-chip none">Unconfirmed</span>`;
  let sugHTML;
  if (sug && sug.best) {
    const b = sug.best;
    const ev = sug.neighbors.slice(0, 3).map(n => `${n.name} (${n.family}, ${n.dist.toFixed(2)})`).join(' \u00b7 ');
    sugHTML = `<div class="t-suggest">
      <div class="t-sug-row"><span class="t-sug-lab">Suggested</span>
        <b style="color:${hex(typoColor(b.family))}">${b.family}</b>
        <span class="t-conf mono">${(b.conf * 100).toFixed(0)}%</span>
        <button class="mini" id="typo-use">Use</button></div>
      <div class="t-ev note faint">nearest confirmed: ${ev}</div></div>`;
  } else {
    sugHTML = `<div class="t-ev note faint">No confirmed neighbours yet to suggest from.</div>`;
  }
  return `<section class="sec typo-sec">
    <div class="sec-h">Typology <span data-info="typology" class="info-link">\u24d8</span></div>
    <div class="t-cur">${curChip}</div>
    <div class="t-pick">
      <select id="typo-pick" class="t-select">${opts}</select>
      <button class="mini accent" id="typo-apply">Apply</button>
    </div>
    ${sugHTML}
  </section>`;
}
function wireTypology(panel, id) {
  const sel = panel.querySelector('#typo-pick');
  const apply = panel.querySelector('#typo-apply');
  const use = panel.querySelector('#typo-use');
  if (apply && sel) apply.addEventListener('click', e => { e.stopPropagation(); setTypology(id, sel.value); });
  if (use) use.addEventListener('click', e => { e.stopPropagation(); const sg = suggestTypology(id); if (sg && sg.best) setTypology(id, sg.best.family); });
}

// ── parcels: planning-relevant signal, parallel to the morphology embedding ────
const PARCEL_FKEYS = ['ownership_gini', 'zoning_diversity', 'year_built_median', 'vacant_share', 'mean_bldg_density_per_acre'];
const PARCEL_LABELS = {
  ownership_gini: 'ownership Gini',
  zoning_diversity: 'zoning entropy',
  year_built_median: 'median year built',
  vacant_share: 'vacant share',
  mean_bldg_density_per_acre: 'bldg sqft / acre',
};
function _parcelRecsWithFeatures() {
  return Object.values(S.idToRec).filter(r => r.parcels && r.parcels.features
    && PARCEL_FKEYS.some(k => Number.isFinite(r.parcels.features[k])));
}
// Z-score each feature across the parcel-having subset, then Euclidean distance.
function parcelNearest(id, k = 3) {
  const recs = _parcelRecsWithFeatures();
  if (recs.length < 2) return [];
  const myIdx = recs.findIndex(r => r.sad_id === id);
  if (myIdx < 0) return [];
  const cols = PARCEL_FKEYS.map((_, j) => {
    const vs = recs.map(r => r.parcels.features[PARCEL_FKEYS[j]]).filter(Number.isFinite);
    if (!vs.length) return { m: 0, s: 1, present: false };
    const m = vs.reduce((a, b) => a + b, 0) / vs.length;
    const v = vs.reduce((a, x) => a + (x - m) * (x - m), 0) / vs.length;
    return { m, s: Math.sqrt(v) || 1, present: true };
  });
  const zRow = r => PARCEL_FKEYS.map((k2, j) => {
    const v = r.parcels.features[k2];
    return (cols[j].present && Number.isFinite(v)) ? (v - cols[j].m) / cols[j].s : 0;
  });
  const myZ = zRow(recs[myIdx]);
  return recs
    .map((r, i) => i === myIdx ? null : ({ id: r.sad_id, name: shortName(r), d: Math.sqrt(zRow(r).reduce((s, x, j) => s + (x - myZ[j]) * (x - myZ[j]), 0)) }))
    .filter(x => x)
    .sort((a, b) => a.d - b.d)
    .slice(0, k);
}
function parcelSectionHTML(id) {
  const rec = S.idToRec[id];
  const p = rec && rec.parcels;
  if (!p || !p.parcel_count) return '';
  const stats = [
    `${p.parcel_count.toLocaleString()} parcels`,
    p.total_acres != null ? `${Math.round(p.total_acres).toLocaleString()} ac` : null,
    p.ownership_gini != null ? `ownership Gini ${p.ownership_gini.toFixed(2)}` : null,
    p.zoning_diversity_shannon != null ? `zoning entropy ${p.zoning_diversity_shannon.toFixed(2)}` : null,
    p.year_built_median != null ? `median yr ${p.year_built_median}` : null,
    p.vacant_share != null ? `${Math.round(p.vacant_share * 100)}% vacant` : null,
  ].filter(Boolean).join(' \u00b7 ');
  const zoning = (p.zoning_standardized_top || p.zoning_raw_top || []).slice(0, 5);
  const zHTML = zoning.length
    ? `<div class="p-zoning"><div class="sub-h">Zoning mix</div>${zoning.map(([k, v]) => `<div class="p-row"><span class="nm">${k}</span><span class="mono">${v}</span></div>`).join('')}</div>`
    : '';
  const _entry = (e) => Array.isArray(e)
    ? [e[0], e[1]]
    : (e && typeof e === 'object'
        ? [e.name || e.owner || e.label || '', (e.share != null ? e.share : (e.pct != null ? e.pct : e.value))]
        : [String(e), '']);
  const _pct = (v) => (v == null || v === '') ? '' : (v <= 1 ? `${Math.round(v * 100)}%` : `${Math.round(v)}%`);
  const owners = (p.top_owners || []).slice(0, 5).map(_entry);
  const _owntop = p.top5_owner_share != null ? ` <span class="faint">(top 5 = ${_pct(p.top5_owner_share)})</span>` : '';
  const ownHTML = owners.length
    ? `<div class="p-owners"><div class="sub-h">Ownership${_owntop}</div>${owners.map(([k, v]) => `<div class="p-row"><span class="nm">${k}</span><span class="mono">${_pct(v)}</span></div>`).join('')}</div>`
    : '';
  const landuse = (p.use_desc_top || p.use_code_top || []).slice(0, 5).map(_entry);
  const luHTML = landuse.length
    ? `<div class="p-landuse"><div class="sub-h">Land use mix</div>${landuse.map(([k, v]) => `<div class="p-row"><span class="nm">${k}</span><span class="mono">${v}</span></div>`).join('')}</div>`
    : '';
  const near = parcelNearest(id, 3);
  const nearHTML = near.length
    ? `<div class="p-near"><div class="sub-h">Nearest by parcel signal</div>${near.map(t => `<div class="p-row"><span class="nm">${t.name}</span><span class="mono">${t.d.toFixed(2)}</span></div>`).join('')}</div>`
    : `<div class="p-near note faint">Need at least 2 districts with parcels for the parcel-signal similarity.</div>`;
  return `<section class="sec parcel-sec">
    <div class="sec-h">Parcels <span class="info-link" data-info="parcels">\u24d8</span></div>
    <div class="p-stats">${stats}</div>
    ${ownHTML}
    ${luHTML}
    ${zHTML}
    ${nearHTML}
  </section>`;
}


boot();
