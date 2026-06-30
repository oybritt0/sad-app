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
OCC_GROUPS = ['residential','hotel/institutional','retail','office','entertainment','civic/medical/parking','industrial/service','other']
import re as _re
def _nsi_group(occtype):
    if not occtype: return 'other'
    m = _re.match(r'^([A-Z]+)(\d+)', str(occtype))
    if not m: return 'other'
    fam, num = m.group(1), int(m.group(2))
    if fam=='RES':
        if num==4: return 'hotel/institutional'
        return 'residential'
    if fam=='COM':
        if num==1: return 'retail'
        if num==4: return 'office'
        if num in (8,9): return 'entertainment'
        if num in (2,3): return 'industrial/service'
        return 'civic/medical/parking'
    if fam=='IND': return 'industrial/service'
    if fam=='AGR': return 'industrial/service'
    if fam in ('REL','EDU','GOV'): return 'civic/medical/parking'
    return 'other'
def occ_code(props):
    return OCC_GROUPS.index(_nsi_group(props.get('occtype')))

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

def simplify_line(coords, tol):
    if len(coords) < 3: return coords
    def dp(chain):
        if len(chain) < 3: return chain
        x1,y1=chain[0]; x2,y2=chain[-1]
        dx,dy=x2-x1,y2-y1; norm=math.hypot(dx,dy) or 1e-9
        dmax=0; idx=0
        for i in range(1,len(chain)-1):
            px,py=chain[i]
            dd=abs(dx*(y1-py)-dy*(x1-px))/norm
            if dd>dmax: dmax=dd; idx=i
        if dmax>tol:
            return dp(chain[:idx+1])[:-1]+dp(chain[idx:])
        return [chain[0],chain[-1]]
    return dp(coords)

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
    base = path.split('/derived/')[0]
    def _norm_pt(x,y):
        return [int(round((x-minx)/spanx*1000)), int(round((1-(y-miny)/spany)*1000))]
    parks=[]
    pp = os.path.join(base,'source','parks.geojson')
    if os.path.exists(pp):
        try:
            pd = json.load(open(pp))
            for f in pd.get('features',[]):
                g=f.get('geometry') or {}; t=g.get('type'); cs=g.get('coordinates')
                if not cs: continue
                polys = cs if t=='MultiPolygon' else [cs] if t=='Polygon' else []
                for poly in polys:
                    if not poly: continue
                    ext=poly[0]
                    if len(ext)<4: continue
                    norm=[[ (pt[0]-minx)/spanx*1000, (1-(pt[1]-miny)/spany)*1000 ] for pt in ext]
                    simp=simplify_ring(norm, SIMPLIFY_TOL)
                    if len(simp)<3: continue
                    if ring_area(simp) < min_area: continue
                    flat=[]
                    for x,y in simp: flat.append(int(round(x))); flat.append(int(round(y)))
                    parks.append(flat)
            del pd
        except Exception: pass
    streets=[]; stw=[]
    sp = os.path.join(base,'derived','street_centrality.geojson')
    if os.path.exists(sp):
        try:
            sd = json.load(open(sp))
            cmax=1e-9
            for f in sd.get('features',[]):
                c=f.get('properties',{}).get('centrality_global')
                if isinstance(c,(int,float)) and c>cmax: cmax=c
            for f in sd.get('features',[]):
                g=f.get('geometry') or {}; t=g.get('type'); cs=g.get('coordinates')
                if not cs: continue
                lines = cs if t=='MultiLineString' else [cs] if t=='LineString' else []
                c=f.get('properties',{}).get('centrality_global') or 0
                w=round(min(1.0, (c/cmax))**0.5, 2) if cmax>0 else 0.3
                for ln in lines:
                    if len(ln)<2: continue
                    norm=[[ (pt[0]-minx)/spanx*1000, (1-(pt[1]-miny)/spany)*1000 ] for pt in ln]
                    simp=simplify_line(norm, SIMPLIFY_TOL)
                    if len(simp)<2: continue
                    flat=[]
                    for x,y in simp: flat.append(int(round(x))); flat.append(int(round(y)))
                    streets.append(flat); stw.append(w)
            del sd
        except Exception: pass
    bcenter=None
    bp = os.path.join(base,'source','sad_boundary.geojson')
    if os.path.exists(bp):
        try:
            bd = json.load(open(bp))
            xs=[]; ys=[]
            for f in bd.get('features',[]):
                g=f.get('geometry') or {}; t=g.get('type'); cs=g.get('coordinates')
                polys = cs if t=='MultiPolygon' else [cs] if t=='Polygon' else []
                for poly in polys:
                    for pt in poly[0]:
                        xs.append(pt[0]); ys.append(pt[1])
            if xs:
                bcenter=_norm_pt(sum(xs)/len(xs), sum(ys)/len(ys))
            # simplified boundary ring(s) for the toggle
            bpoly=[]
            for f in bd.get('features',[]):
                g=f.get('geometry') or {}; t=g.get('type'); cs=g.get('coordinates')
                bpolys = cs if t=='MultiPolygon' else [cs] if t=='Polygon' else []
                for poly in bpolys:
                    if not poly: continue
                    ext=poly[0]
                    if len(ext)<4: continue
                    norm=[[ (pt[0]-minx)/spanx*1000, (1-(pt[1]-miny)/spany)*1000 ] for pt in ext]
                    simp=simplify_ring(norm, SIMPLIFY_TOL)
                    if len(simp)<3: continue
                    flat=[]
                    for x,y in simp: flat.append(int(round(x))); flat.append(int(round(y)))
                    bpoly.append(flat)
            del bd
        except Exception: pass
    out['parks']=parks
    out['streets']=streets
    out['stw']=stw
    if bcenter: out['bcenter']=bcenter
    try:
        if bpoly: out['bpoly']=bpoly
    except NameError: pass
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
        print(f'  parks={len(r["parks"])} streets={len(r["streets"])} bcenter={r.get("bcenter")}')
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
