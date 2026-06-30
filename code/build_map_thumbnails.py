#!/usr/bin/env python3
"""
build_map_thumbnails.py  (C6 data-prep)
Reads each district's buildings_enriched.geojson, produces a COMPACT thumbnail
geometry: simplified footprints normalized to a 0-1000 grid within the district
bbox, plus a few categorical layer attributes per building.

OOM-SAFE: processes ONE district at a time, frees memory between. Never holds all
districts' raw geojson at once.

Output: /data/_compare_ui/thumbnails.json  (all districts, one compact file)
  {
    "<sad_id>": {
      "bbox": [minx,miny,maxx,maxy],   # original lon/lat, for shared-scale option
      "w_m": <bbox width meters>, "h_m": <bbox height meters>,  # for shared scale
      "b": [ [x0,y0,x1,y1,...], ... ],  # each building: flat int coords 0-1000 (exterior ring only)
      "occ": [ <int code>, ... ],       # occupancy/program category per building
      "ht":  [ <int levels or null>, ... ],  # height (levels) per building
      "yr":  [ <int year or null>, ... ]     # year built per building
    }, ...
  }

Tunables: SIMPLIFY_TOL (in normalized 0-1000 units), MIN_AREA (drop tiny bldgs),
MAX_BLDGS (cap per district for the densest).
"""
import json, glob, os, math, gc, sys

SIMPLIFY_TOL = 4.0      # in 0-1000 normalized units (~0.4% of thumbnail) - aggressive
MIN_AREA_FRAC = 0.00003 # drop polygons smaller than this frac of bbox area
MAX_BLDGS = 1200        # cap per district (keep the largest by area)
OUT = '/data/_compare_ui/thumbnails.json'

# occupancy: FEMA occ class first (richly populated), then program, then building tag.
OCC_GROUPS = ['sport','retail/food/ent','office','residential','hotel','civic/edu','industrial','parking','unclassified']
def _occ_group_props(props):
    fc = str(props.get('fema_occ_cls') or '').strip().lower()
    fp = str(props.get('fema_prim_occ') or '').strip().lower()
    if fc and fc != 'unclassified':
        if 'residential' in fc: return 'residential'
        if 'commercial' in fc: return 'retail/food/ent'
        if 'education' in fc or 'government' in fc or 'religious' in fc: return 'civic/edu'
        if 'industrial' in fc: return 'industrial'
        if 'assembly' in fc:
            if any(k in fp for k in ['stadium','arena','sport']): return 'sport'
            return 'civic/edu'
    dp = str(props.get('dominant_program_inside') or '').strip().lower()
    if dp:
        if 'sport' in dp: return 'sport'
        if 'retail' in dp or 'food' in dp or 'entertainment' in dp: return 'retail/food/ent'
        if 'office' in dp: return 'office'
        if 'hotel' in dp: return 'hotel'
        if 'parking' in dp: return 'parking'
    b = str(props.get('building') or '').lower()
    if b and b not in ('yes','true','1'):
        if any(k in b for k in ['stadium','sport','arena']): return 'sport'
        if any(k in b for k in ['apartment','residential','house','dormitory']): return 'residential'
        if any(k in b for k in ['retail','commercial','shop']): return 'retail/food/ent'
        if 'office' in b: return 'office'
        if any(k in b for k in ['hotel','motel']): return 'hotel'
        if any(k in b for k in ['parking','garage']): return 'parking'
        if any(k in b for k in ['industrial','warehouse']): return 'industrial'
    return 'unclassified'
def occ_code(props):
    return OCC_GROUPS.index(_occ_group_props(props))

def height_levels(props):
    for k in ('building:levels','num_story','fema_height'):
        x = props.get(k)
        if x not in (None,''):
            try: return int(round(float(x)))
            except: pass
    # estimate from height_m_est at ~3.5m/level
    hm = props.get('height_m_est')
    if hm not in (None,''):
        try: return max(1, int(round(float(hm)/3.5)))
        except: pass
    return None

def year_built(props):
    for k in ('med_yr_blt','start_date'):
        x = props.get(k)
        if x not in (None,''):
            try:
                y = int(str(x)[:4])
                if 1700 < y < 2100: return y
            except: pass
    return None

def ring_area(coords):
    a = 0.0
    n = len(coords)
    for i in range(n):
        x1,y1 = coords[i]; x2,y2 = coords[(i+1)%n]
        a += x1*y2 - x2*y1
    return abs(a)/2.0

def simplify_ring(coords, tol):
    # Douglas-Peucker for CLOSED rings: split at farthest point from start so the
    # ring doesn't collapse to the degenerate start==end segment.
    if len(coords) < 5: return coords
    closed = coords[0]==coords[-1]
    pts = coords[:-1] if closed else coords[:]
    n=len(pts)
    if n<4: return coords
    x0,y0=pts[0]; far=0; fd=-1
    for i in range(1,n):
        d=math.hypot(pts[i][0]-x0,pts[i][1]-y0)
        if d>fd: fd=d; far=i
    def dp(chain):
        if len(chain)<3: return chain
        x1,y1=chain[0]; x2,y2=chain[-1]
        dx,dy=x2-x1,y2-y1; norm=math.hypot(dx,dy) or 1e-9
        dmax=0; idx=0
        for i in range(1,len(chain)-1):
            px,py=chain[i]
            d=abs(dx*(y1-py)-dy*(x1-px))/norm
            if d>dmax: dmax=d; idx=i
        if dmax>tol:
            return dp(chain[:idx+1])[:-1]+dp(chain[idx:])
        return [chain[0],chain[-1]]
    chainA=pts[:far+1]; chainB=pts[far:]+[pts[0]]
    simp=dp(chainA)[:-1]+dp(chainB)[:-1]
    if len(simp)>=3: simp=simp+[simp[0]]
    return simp if len(simp)>=4 else coords

def process_district(path):
    d = json.load(open(path))
    feats = d.get('features',[])
    # collect all exterior rings in lon/lat + their attrs, compute bbox
    raw = []
    minx=miny=1e18; maxx=maxy=-1e18
    for f in feats:
        g = f.get('geometry') or {}
        t = g.get('type'); cs = g.get('coordinates')
        if not cs: continue
        polys = cs if t=='MultiPolygon' else [cs] if t=='Polygon' else []
        for poly in polys:
            if not poly: continue
            ext = poly[0]  # exterior ring
            if len(ext)<4: continue
            raw.append((ext, f.get('properties',{})))
            for x,y,*z in ext:
                if x<minx:minx=x
                if x>maxx:maxx=x
                if y<miny:miny=y
                if y>maxy:maxy=y
    if not raw: return None
    spanx = (maxx-minx) or 1e-9; spany=(maxy-miny) or 1e-9
    # meters (approx) for shared-scale: lat/lon -> meters
    midlat = (miny+maxy)/2
    w_m = spanx * 111320 * math.cos(math.radians(midlat))
    h_m = spany * 110540
    # normalize each ring to 0-1000, simplify, drop tiny, keep largest MAX_BLDGS
    bbox_area = 1000*1000
    min_area = bbox_area * MIN_AREA_FRAC
    items=[]
    for ext, props in raw:
        norm = [[ (x-minx)/spanx*1000, (1-(y-miny)/spany)*1000 ] for x,y,*z in ext]  # flip y for screen
        simp = simplify_ring(norm, SIMPLIFY_TOL)
        if len(simp)<3: continue
        a = ring_area(simp)
        if a < min_area: continue
        flat=[]
        for x,y in simp: flat.append(int(round(x))); flat.append(int(round(y)))
        items.append((a, flat, occ_code(props), height_levels(props), year_built(props)))
    # keep largest MAX_BLDGS
    items.sort(key=lambda t:-t[0])
    items = items[:MAX_BLDGS]
    out = {
        'bbox':[round(minx,6),round(miny,6),round(maxx,6),round(maxy,6)],
        'w_m':round(w_m), 'h_m':round(h_m),
        'b':[it[1] for it in items],
        'occ':[it[2] for it in items],
        'ht':[it[3] for it in items],
        'yr':[it[4] for it in items],
    }
    del d, feats, raw, items
    gc.collect()
    return out

def main():
    files = sorted(glob.glob('/data/*/derived/buildings_enriched.geojson'))
    print(f'{len(files)} districts with buildings')
    if len(sys.argv)>1 and sys.argv[1]=='--one':
        # test one district: process, report size, do not write combined
        path=files[0]
        sad_id=path.split('/')[2]
        r=process_district(path)
        import json as _j
        s=_j.dumps({sad_id:r},separators=(',',':'))
        print(f'ONE district {sad_id}: bldgs={len(r["b"])} occ_groups={len(OCC_GROUPS)}')
        print(f'  json size for this district: {len(s)/1000:.1f} KB')
        print(f'  projected for 63 districts: ~{len(s)*63/1e6:.1f} MB')
        print(f'  sample bldg coords (first): {r["b"][0][:12]}...')
        print(f'  occ groups: {OCC_GROUPS}')
        return
    result={}
    for i,path in enumerate(files):
        sad_id = path.split('/')[2]  # /data/<sad_id>/derived/...
        try:
            r = process_district(path)
            if r:
                result[sad_id]=r
                print(f'[{i+1}/{len(files)}] {sad_id}: {len(r["b"])} bldgs')
        except Exception as e:
            print(f'[{i+1}/{len(files)}] {sad_id}: ERROR {e}')
        gc.collect()
    # attach the occupancy legend (code->label)
    result['_occ_legend'] = {i:g for i,g in enumerate(OCC_GROUPS)}
    os.makedirs('/data/_compare_ui', exist_ok=True)
    json.dump(result, open(OUT,'w'), separators=(',',':'))
    sz = os.path.getsize(OUT)
    print(f'WROTE {OUT}  size={sz/1e6:.2f} MB  districts={len(result)-1}  occ_groups={len(OCC_GROUPS)}')

if __name__=='__main__':
    main()
