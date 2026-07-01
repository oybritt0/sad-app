
let ROWS=[], DIST={};
function _num(v){ const n=Number(v); return isFinite(n)?n:null; }
function manifestToStudio(m){
  const rows=(m.sads||[]).filter(s=>s.corpus!==false).map(s=>{
    const c=(s.census&&s.census.sad)||{}, dn=s.day_night||{};
    return {
      sad_id:s.sad_id,
      name:(s.sad_name||s.sad_id||'').split(',')[0].trim(),
      city:(s.region||'').split(',')[0].trim(),
      typology:s.typology||'unspecified',
      cen:{ pop:_num(c.estimated_population), income:_num(c.median_household_income_pop_weighted),
            age:_num(c.median_age_pop_weighted), renter:_num(c.pct_renter_occupied),
            bach:_num(c.pct_bachelors_or_higher) },
      pois:(_num(s.amenity&&s.amenity.total_points_in_sad) ?? _num(s.program&&s.program.total)),
      amenity:(s.amenity&&s.amenity.category_counts)||{},
      transit:_num(s.transit&&s.transit.total_stations),
      walk:_num(s.walkshed&&s.walkshed.walkshed_10min_acres),
      nodes:(_num(s.centrality&&s.centrality.network_size&&s.centrality.network_size.nodes) ?? _num(s.centrality&&s.centrality.feature_count)),
      edges:_num(s.centrality&&s.centrality.network_size&&s.centrality.network_size.edges),
      program_real:s.program_real||null,
      program_land:s.program_land||null,
      indices:s.indices||null,
      _jpr:_num((s.jobs||{}).jobs_per_resident), _know:_num((s.jobs||{}).office_knowledge_pct),
      _cov:_num(((s.features||{}).raw||{}).morph_coverage), _dens:_num(((s.features||{}).raw||{}).morph_density_per_km2), _comp:_num(((s.features||{}).raw||{}).morph_mean_compactness),
      _int:_num(((s.indices||{}).scores||{}).intensity), _lwp:_num(((s.indices||{}).scores||{}).live_work_play), _conn:_num(((s.indices||{}).scores||{}).connectivity),
      _pl:((s.program_land||{}).shares_sqft||null),
      _cost:_num((s.meta||{}).constructionCostUSD), _year:_num((s.meta||{}).yearOpened), _venue:((s.meta||{}).anchorVenue||null),
      _kind:((s.transit||{}).kind_counts||{}),
      day:_num((s.day_night||{}).day_pop), night:_num((s.day_night||{}).night_pop),
      daynight:_num((s.day_night||{}).swing_ratio), dn_profile:((s.day_night||{}).profile||null), dn_low:((s.day_night||{}).low_confidence||false),
    };
  });
  const dist=(m.embedding&&m.embedding.distance_matrix)||{};
  return { rows, dist };
}
let byId={};

const COLORS=['#ef3006','#2f8f80','#5688c4','#d2a23f','#8f78c9'];
const AMEN_C={retail_food_entertainment:'#e2674f',office:'#5688c4',sport:'#d2a23f',parking:'#8a8a8a',hotel:'#c98a3a',residential:'#5fae7e',open_space:'#7cb342',other:'#d6cebf'};
const METRICS=[['income','Median income','usd',r=>r.cen.income],['pop','Population','int',r=>r.cen.pop],['age','Median age','age',r=>r.cen.age],
  ['renter','Renter-occupied','pct',r=>r.cen.renter],['bach',"Bachelor's or higher",'pct',r=>r.cen.bach],['pois','POIs','int',r=>r.pois],
  ['transit','Transit stations','int',r=>r.transit],['walk','Walkshed acres','int',r=>r.walk],['nodes','Street nodes','int',r=>r.nodes]];
const MGET={}; METRICS.forEach(m=>MGET[m[0]]=m);
const esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const fmtV=(v,k)=>{ if(v==null||isNaN(+v))return '--'; const n=+v;
  if(k==='usd')return n>=1000?'$'+(n/1000).toFixed(0)+'k':'$'+Math.round(n);
  if(k==='pct')return Math.round(n)+'%'; if(k==='age')return n.toFixed(1);
  if(k==='int')return n>=1000?(n/1000).toFixed(n>=10000?0:1)+'k':String(Math.round(n)); return String(Math.round(n)); };
const AX=[['Income',r=>r.cen.income],['Renter',r=>r.cen.renter],['Educated',r=>r.cen.bach],['Pop',r=>r.cen.pop],
  ['POIs',r=>r.pois],['Transit',r=>r.transit],['Walk',r=>r.walk],['Streets',r=>r.nodes]];
let AXSTAT=AX.map(([,f])=>ROWS.map(r=>+f(r)).filter(isFinite).sort((a,b)=>a-b));
const pctRank=(v,arr)=>{if(v==null||!isFinite(v)||!arr.length)return null;let c=0;for(const x of arr)if(x<=v)c++;return c/arr.length;};
const axPct=(r,i)=>pctRank(+AX[i][1](r),AXSTAT[i]);

let FOCUS=null;
let COMPS=[];
let PROGMODE='area'; let SCAT={x:'pois',y:'income'}, RANKM='income';
function topSimilar(id,k){const row=DIST[id];if(!row)return [];return Object.entries(row).filter(([o])=>o!==id&&byId[o]).map(([o,d])=>({o,d:+d})).filter(x=>isFinite(x.d)).sort((a,b)=>a.d-b.d).slice(0,k).map(x=>x.o);}
function sel(){return [FOCUS,...COMPS].filter((v,i,a)=>byId[v]&&a.indexOf(v)===i);}
function colorOf(id){const i=sel().indexOf(id);return i<0?'#bbb':COLORS[i%COLORS.length];}

function svg(w,h,title,sub,inner){
  let leg='';const s=sel();let lx=20;
  s.forEach(id=>{const nm=byId[id].name;leg+='<circle cx="'+lx+'" cy="50" r="5" fill="'+colorOf(id)+'"/><text x="'+(lx+10)+'" y="54" class="leg">'+esc(nm)+'</text>';lx+=20+nm.length*6.6+16;});
  return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 '+w+' '+h+'" width="'+w+'" height="'+h+'">'
    +'<style>text{font-family:Arial,Helvetica,sans-serif} .ttl{font-size:16px;font-weight:700;fill:#000000}'
    +'.sub{font-family:"JetBrains Mono",monospace;font-size:11px;letter-spacing:.05em;fill:#8a8a8a} .leg{font-size:12px;fill:#5a5a5a}'
    +'.lab{font-size:12px;fill:#5a5a5a} .mono{font-family:"JetBrains Mono",monospace;font-size:11.5px;fill:#5a5a5a}'
    +'.axlab{font-family:"JetBrains Mono",monospace;font-size:10.5px;fill:#8a8a8a} .end{font-family:"JetBrains Mono",monospace;font-size:10.5px;fill:#8a8a8a}'
    +'.tick{stroke:#8a8a8a;stroke-width:1.3;opacity:.36} .median{stroke:#8a8a8a;stroke-width:1;stroke-dasharray:2 2;opacity:.65} .axis{stroke:#e0e0e0;stroke-width:1}'
    +'.ring{fill:none;stroke:#e0e0e0;stroke-width:1} .spoke{stroke:#e0e0e0;stroke-width:1}</style>'
    +'<rect width="'+w+'" height="'+h+'" fill="#ffffff"/>'
    +'<text x="20" y="24" class="ttl">'+esc(title)+'</text>'
    +(sub?'<text x="'+(w-20)+'" y="24" text-anchor="end" class="sub">'+esc(sub)+'</text>':'')
    +leg+inner+'</svg>';
}

function cCensus(){
  const W=800,pad=20;let inner='',y=78;
  const strips=[['Median income','usd',r=>r.cen.income],['Population','int',r=>r.cen.pop],['Median age','age',r=>r.cen.age]];
  strips.forEach(m=>{const getter=m[2],kind=m[1],rw=W-2*pad,ay=y+34,x=t=>pad+t*rw;
    const corpus=ROWS.map(getter).filter(v=>v!=null&&isFinite(+v)).map(Number).sort((a,b)=>a-b);
    inner+='<text x="'+pad+'" y="'+(y+14)+'" class="lab" style="font-size:12px;fill:#000000">'+m[0]+'</text>';
    const vals=sel().map(id=>'<tspan fill="'+colorOf(id)+'">'+fmtV(getter(byId[id]),kind)+'</tspan>').join('<tspan fill="#8a8a8a">   </tspan>');
    inner+='<text x="'+(W-pad)+'" y="'+(y+14)+'" text-anchor="end" class="mono" style="font-size:11.5px">'+vals+'</text>';
    inner+='<line class="axis" x1="'+pad+'" y1="'+ay+'" x2="'+(W-pad)+'" y2="'+ay+'"/>';
    corpus.forEach((v,idx)=>{const t=(idx+0.5)/corpus.length;inner+='<line class="tick" x1="'+x(t).toFixed(1)+'" y1="'+(ay-6)+'" x2="'+x(t).toFixed(1)+'" y2="'+(ay+6)+'"/>';});
    inner+='<line class="median" x1="'+x(.5).toFixed(1)+'" y1="'+(ay-11)+'" x2="'+x(.5).toFixed(1)+'" y2="'+(ay+11)+'"/>';
    sel().forEach(id=>{const v=getter(byId[id]);if(v==null||!isFinite(+v))return;const t=pctRank(+v,corpus),foc=id===FOCUS;
      inner+='<circle cx="'+x(t).toFixed(1)+'" cy="'+ay+'" r="'+(foc?6:4.5)+'" fill="'+colorOf(id)+'" stroke="#ffffff" stroke-width="'+(foc?2:1.6)+'"/>';});
    inner+='<text x="'+pad+'" y="'+(ay+19)+'" class="end">'+fmtV(corpus[0],kind)+'</text><text x="'+(W-pad)+'" y="'+(ay+19)+'" text-anchor="end" class="end">'+fmtV(corpus[corpus.length-1],kind)+'</text>';
    y+=66;});
  y+=8;inner+='<line class="axis" x1="'+pad+'" y1="'+y+'" x2="'+(W-pad)+'" y2="'+y+'"/>';
  inner+='<text x="'+pad+'" y="'+(y+22)+'" class="sub" style="font-size:9.5px">SHARE OF HOUSEHOLDS</text>';y+=42;
  const arc=(cx,cy,r,a0,a1)=>{const A0=a0*Math.PI/180,A1=a1*Math.PI/180,x0=cx+r*Math.cos(A0),y0=cy+r*Math.sin(A0),x1=cx+r*Math.cos(A1),y1=cy+r*Math.sin(A1),lg=(a1-a0)>180?1:0;return 'M'+x0.toFixed(1)+' '+y0.toFixed(1)+' A'+r+' '+r+' 0 '+lg+' 1 '+x1.toFixed(1)+' '+y1.toFixed(1);};
  const donutRow=(label,getter)=>{const s=sel(),r=30,sw=8,slotW=(W-2*pad)/s.length;
    inner+='<text x="'+pad+'" y="'+y+'" class="lab" style="font-size:11px;fill:#000000">'+label+'</text>';
    s.forEach((id,i)=>{const cx=pad+slotW*i+slotW/2,cy=y+50,v=getter(byId[id]),has=v!=null&&isFinite(+v),pct=has?Math.max(0,Math.min(100,+v)):0,c=colorOf(id);
      inner+='<circle cx="'+cx+'" cy="'+cy+'" r="'+r+'" fill="none" stroke="#e8e8e8" stroke-width="'+sw+'"/>';
      if(has&&pct>0){if(pct>=99.9){inner+='<circle cx="'+cx+'" cy="'+cy+'" r="'+r+'" fill="none" stroke="'+c+'" stroke-width="'+sw+'"/>';}else{inner+='<path d="'+arc(cx,cy,r,-90,-90+pct/100*360)+'" fill="none" stroke="'+c+'" stroke-width="'+sw+'" stroke-linecap="round"/>';}}
      inner+='<text x="'+cx+'" y="'+(cy+5)+'" text-anchor="middle" class="mono" style="font-size:14px;fill:#000000">'+(has?Math.round(pct)+'%':'\u2014')+'</text>';
      inner+='<text x="'+cx+'" y="'+(cy+r+18)+'" text-anchor="middle" class="end">'+esc(byId[id].name)+'</text>';});
    y+=128;};
  donutRow('Renter-occupied',r=>r.cen.renter);
  donutRow("Bachelor's degree or higher",r=>r.cen.bach);
  return {title:'Census',svg:svg(W,y+4,'Census','corpus position + share of households',inner)};
}
function cRose(){
  const W=460,H=420,N=AX.length,cx=230,cy=225,R=128,LR=1.15;
  const ang=p=>(-90+p*360/N)*Math.PI/180,pt=(p,v)=>[cx+R*v*Math.cos(ang(p)),cy+R*v*Math.sin(ang(p))];
  let inner='';[.25,.5,.75,1].forEach(f=>{inner+='<polygon class="ring" points="'+AX.map((_,p)=>pt(p,f).map(n=>n.toFixed(1)).join(',')).join(' ')+'"/>';});
  AX.forEach(([label],p)=>{const[x,y]=pt(p,1),[lx,ly]=pt(p,LR);inner+='<line class="spoke" x1="'+cx+'" y1="'+cy+'" x2="'+x.toFixed(1)+'" y2="'+y.toFixed(1)+'"/><text class="axlab" x="'+lx.toFixed(1)+'" y="'+ly.toFixed(1)+'" text-anchor="middle">'+label+'</text>';});
  sel().forEach(id=>{const c=colorOf(id),r=byId[id];const pts=AX.map((_,i)=>{const t=axPct(r,i);return pt(i,t==null?0:t);});
    inner+='<polygon points="'+pts.map(q=>q.map(n=>n.toFixed(1)).join(',')).join(' ')+'" fill="'+c+'" fill-opacity="0.08" stroke="'+c+'" stroke-width="2" stroke-linejoin="round"/>';
    pts.forEach(q=>inner+='<circle cx="'+q[0].toFixed(1)+'" cy="'+q[1].toFixed(1)+'" r="2.2" fill="'+c+'"/>');});
  return {title:'Morphometric profile',svg:svg(W,H,'Morphometric profile','corpus percentile per axis',inner),center:true};
}
function cSim(){
  const row=DIST[FOCUS]||{};const ds=Object.values(row).map(Number).filter(isFinite);const maxD=Math.max(4,ds.length?Math.max.apply(null,ds):4);
  const items=COMPS.filter(id=>row[id]!=null).map(id=>({id,d:+row[id]})).sort((a,b)=>a.d-b.d);
  const W=800,top=74,rowH=40,H=top+Math.max(1,items.length)*rowH+10,lx=210,bw=W-lx-90;let inner='';
  if(!items.length)inner+='<text x="20" y="'+(top+18)+'" class="lab">Add districts to rank them against the focus.</text>';
  items.forEach((x,i)=>{const y=top+i*rowH,sim=Math.round(100*Math.max(0,1-x.d/maxD)),r=byId[x.id],c=colorOf(x.id);
    inner+='<circle cx="26" cy="'+(y+13)+'" r="5" fill="'+c+'"/><text x="40" y="'+(y+17)+'" class="lab" style="font-size:12.5px;fill:#000000">'+esc(r.name)+'</text>';
    inner+='<text x="40" y="'+(y+31)+'" class="end">'+esc(r.city)+'</text>';
    inner+='<rect x="'+lx+'" y="'+(y+9)+'" width="'+bw+'" height="9" rx="4.5" fill="#e8e8e8"/>';
    inner+='<rect x="'+lx+'" y="'+(y+9)+'" width="'+(bw*Math.max(.04,sim/100)).toFixed(1)+'" height="9" rx="4.5" fill="'+c+'"/>';
    inner+='<text x="'+(W-20)+'" y="'+(y+17)+'" text-anchor="end" class="mono" style="font-size:13px;fill:#000000">'+sim+'%</text>';});
  return {title:'Closeness to '+byId[FOCUS].name,svg:svg(W,H,'Closeness to '+byId[FOCUS].name,'morphology + demographics',inner)};
}
function cProgram(){
  const cats=Object.keys(AMEN_C),s=sel();const W=800,top=74,rowH=50,pad=20,bw=W-2*pad;const H=top+s.length*rowH+78;let inner='';
  const mode=PROGMODE;
  s.forEach((id,i)=>{const y=top+i*rowH,r=byId[id];
    let shares={},flag='',avail=true;
    if(mode==='area'){
      // prefer program_land (parking-complete); fall back to program_real (floor area)
      const pl=r.program_land&&r.program_land.shares_sqft, plOk=pl&&!r.program_land.low_confidence;
      const pr=r.program_real&&r.program_real.shares_sqft;
      if(plOk){ shares=pl; const ac=r.program_land.surface_parking_acres; flag=(ac!=null?ac+' ac surface parking':''); }
      else if(pr){ shares=pr; const cov=r.program_real.coverage&&r.program_real.coverage.occ_coverage;
        flag=(r.program_real.low_confidence?'low confidence':(cov!=null?Math.round(cov*100)+'% coverage':'')); }
      else { avail=false; flag='no occupancy data'; }
    } else {
      const am=r.amenity||{},tot=Object.values(am).reduce((a,b)=>a+(+b||0),0)||1;
      cats.forEach(c=>shares[c]=(+am[c]||0)/tot); flag=(r.pois||0)+' POIs';
    }
    inner+='<circle cx="'+(pad+5)+'" cy="'+(y+9)+'" r="5" fill="'+colorOf(id)+'"/><text x="'+(pad+16)+'" y="'+(y+13)+'" class="lab" style="font-size:12px;fill:#000000">'+esc(r.name)+'</text>';
    inner+='<text x="'+(W-pad)+'" y="'+(y+13)+'" text-anchor="end" class="mono">'+flag+'</text>';
    if(avail){let cx=pad;cats.forEach(c=>{const v=+shares[c]||0;if(v<=0)return;const w=v*bw;inner+='<rect x="'+cx.toFixed(1)+'" y="'+(y+20)+'" width="'+w.toFixed(1)+'" height="18" fill="'+AMEN_C[c]+'"><title>'+c.replace(/_/g,' ')+' '+Math.round(v*100)+'%</title></rect>';cx+=w;});}
    else { inner+='<rect x="'+pad+'" y="'+(y+20)+'" width="'+bw+'" height="18" fill="#f2f2f2"/>'; }
    inner+='<rect x="'+pad+'" y="'+(y+20)+'" width="'+bw+'" height="18" fill="none" stroke="#e0e0e0" rx="3"/>';});
  let ky=top+s.length*rowH+12,kx=pad;cats.forEach(c=>{inner+='<rect x="'+kx+'" y="'+ky+'" width="9" height="9" rx="2" fill="'+AMEN_C[c]+'"/><text x="'+(kx+13)+'" y="'+(ky+8)+'" class="axlab">'+c.replace(/_/g,' ')+'</text>';kx+=18+c.length*5.6+16;if(kx>W-120){kx=pad;ky+=18;}});
  const sub=mode==='area'?'occupancy incl. surface parking - NSI/HAZUS + OSM (sport-blind)':'POI counts - Overture/OSM (housing-blind)';
  return {title:'Program mix',svg:svg(W,Math.max(H,ky+20),'Program mix',sub,inner)};
}
function cScatter(){
  const W=800,H=500,m={l:64,r:28,t:78,b:52},gx=MGET[SCAT.x],gy=MGET[SCAT.y];
  const xs=ROWS.map(r=>+gx[3](r)).filter(isFinite),ys=ROWS.map(r=>+gy[3](r)).filter(isFinite);
  const xmin=Math.min.apply(null,xs),xmax=Math.max.apply(null,xs),ymin=Math.min.apply(null,ys),ymax=Math.max.apply(null,ys);
  const px=v=>m.l+((v-xmin)/((xmax-xmin)||1))*(W-m.l-m.r),py=v=>H-m.b-((v-ymin)/((ymax-ymin)||1))*(H-m.t-m.b);
  let inner='<line class="axis" x1="'+m.l+'" y1="'+(H-m.b)+'" x2="'+(W-m.r)+'" y2="'+(H-m.b)+'"/><line class="axis" x1="'+m.l+'" y1="'+m.t+'" x2="'+m.l+'" y2="'+(H-m.b)+'"/>';
  inner+='<text x="'+((W+m.l-m.r)/2)+'" y="'+(H-14)+'" text-anchor="middle" class="axlab">'+gx[1]+'</text>';
  inner+='<text transform="translate(18,'+((H-m.b+m.t)/2)+') rotate(-90)" text-anchor="middle" class="axlab">'+gy[1]+'</text>';
  const ss=sel();
  ROWS.forEach(r=>{const xv=+gx[3](r),yv=+gy[3](r);if(!isFinite(xv)||!isFinite(yv)||ss.includes(r.sad_id))return;inner+='<circle cx="'+px(xv).toFixed(1)+'" cy="'+py(yv).toFixed(1)+'" r="3.5" fill="#cdc4b6"/>';});
  ss.forEach(id=>{const r=byId[id],xv=+gx[3](r),yv=+gy[3](r);if(!isFinite(xv)||!isFinite(yv))return;const foc=id===FOCUS,c=colorOf(id);
    inner+='<circle cx="'+px(xv).toFixed(1)+'" cy="'+py(yv).toFixed(1)+'" r="'+(foc?7.5:6)+'" fill="'+c+'" stroke="#ffffff" stroke-width="2"/>';
    inner+='<text x="'+(px(xv)+10).toFixed(1)+'" y="'+(py(yv)+3.5).toFixed(1)+'" class="lab" style="font-size:10.5px;fill:#000000">'+esc(r.name)+'</text>';});
  return {title:gy[1]+' vs '+gx[1],svg:svg(W,H,gy[1]+' vs '+gx[1],'all 37 · selection highlighted',inner)};
}
function cRank(){
  const g=MGET[RANKM];const W=800,top=74,rowH=21;
  const rows=ROWS.map(r=>({r,v:+g[3](r)})).filter(x=>isFinite(x.v)).sort((a,b)=>b.v-a.v);
  const H=top+rows.length*rowH+14,pad=210,bw=W-pad-86,mx=Math.max.apply(null,rows.map(x=>x.v))||1;const ss=sel();let inner='';
  rows.forEach((x,i)=>{const y=top+i*rowH,inSel=ss.includes(x.r.sad_id),c=inSel?colorOf(x.r.sad_id):'#d6cebf';
    inner+='<text x="20" y="'+(y+11)+'" class="'+(inSel?'lab':'axlab')+'" style="'+(inSel?'fill:#000000;font-weight:600;font-size:11.5px':'')+'">'+esc(x.r.name)+'</text>';
    inner+='<rect x="'+pad+'" y="'+(y+3)+'" width="'+(bw*x.v/mx).toFixed(1)+'" height="11" rx="3" fill="'+c+'"/>';
    inner+='<text x="'+(W-20)+'" y="'+(y+12)+'" text-anchor="end" class="mono" style="'+(inSel?'fill:#000000':'')+'">'+fmtV(x.v,g[2])+'</text>';});
  return {title:'Ranked · '+g[1],svg:svg(W,H,'Ranked · '+g[1],'all 37 districts',inner)};
}
function cRadial(){
  const ss=sel(),W=800,H=470,cols=3,R=70,topY=92,torad=d=>d*Math.PI/180;
  const mets=[['Population',r=>r.cen.pop],['Median income',r=>r.cen.income],['POIs',r=>r.pois],['Transit',r=>r.transit],['Walkshed',r=>r.walk],['Street nodes',r=>r.nodes]];
  let inner='';
  mets.forEach((m,mi)=>{const cx=(W/cols)*(mi%cols)+(W/cols)/2,cy=topY+Math.floor(mi/cols)*194+R+6;
    const corpus=ROWS.map(m[1]).filter(v=>v!=null&&isFinite(+v)).map(Number),mx=corpus.length?Math.max.apply(null,corpus):1;
    [.25,.5,.75,1].forEach(f=>inner+='<circle class="ring" cx="'+cx+'" cy="'+cy+'" r="'+(R*f).toFixed(1)+'"/>');
    const N=ss.length;
    ss.forEach((id,k)=>{const v=m[1](byId[id]),frac=(v==null||!isFinite(+v))?0:Math.max(0,Math.min(1,+v/(mx||1))),r=Math.max(2,frac*R);
      if(N===1){inner+='<circle cx="'+cx+'" cy="'+cy+'" r="'+r.toFixed(1)+'" fill="'+colorOf(id)+'" fill-opacity="0.82"/>';return;}
      const a0=torad(-90+k*360/N),a1=torad(-90+(k+1)*360/N),x0=cx+r*Math.cos(a0),y0=cy+r*Math.sin(a0),x1=cx+r*Math.cos(a1),y1=cy+r*Math.sin(a1),lg=(360/N)>180?1:0;
      inner+='<path d="M'+cx+' '+cy+' L'+x0.toFixed(1)+' '+y0.toFixed(1)+' A'+r.toFixed(1)+' '+r.toFixed(1)+' 0 '+lg+' 1 '+x1.toFixed(1)+' '+y1.toFixed(1)+' Z" fill="'+colorOf(id)+'" fill-opacity="0.82" stroke="#ffffff" stroke-width="1"/>';});
    inner+='<text x="'+cx+'" y="'+(cy+R+22)+'" text-anchor="middle" class="lab" style="font-size:11px;fill:#000000">'+m[0]+'</text>';});
  return {title:'Radial comparison',svg:svg(W,H,'Radial comparison','wedge length = share of corpus maximum',inner)};
}
function cTable(){
  const ss=sel(),W=820,pad=16,top=88,rh=32,headH=30;
  const cols=[['District',176],['Typology',120],['Pop',72],['Income',82],['POIs',58],['Transit',64],['Walk',62],['Nodes',62],['Sim',58]];
  let xs=[pad];cols.forEach(c=>xs.push(xs[xs.length-1]+c[1]));
  const H=top+headH+ss.length*rh+14;
  const row=DIST[FOCUS]||{},dv=Object.values(row).map(Number).filter(isFinite),maxD=Math.max(4,dv.length?Math.max.apply(null,dv):4);
  let inner='<rect x="'+pad+'" y="'+top+'" width="'+(W-2*pad)+'" height="'+headH+'" fill="#000000"/>';
  cols.forEach((c,ci)=>{const tx=ci===0?xs[ci]+12:xs[ci+1]-8,anc=ci===0?'start':'end';
    inner+='<text x="'+tx+'" y="'+(top+headH/2+4)+'" text-anchor="'+anc+'" class="mono" style="font-size:9px;letter-spacing:.05em;fill:#ffffff">'+c[0].toUpperCase()+'</text>';});
  ss.forEach((id,ri)=>{const r=byId[id],y=top+headH+ri*rh,cy=y+rh/2+4;
    if(ri%2===1)inner+='<rect x="'+pad+'" y="'+y+'" width="'+(W-2*pad)+'" height="'+rh+'" fill="#f2f2f2"/>';
    inner+='<circle cx="'+(xs[0]+8)+'" cy="'+(y+rh/2)+'" r="4.5" fill="'+colorOf(id)+'"/>';
    inner+='<text x="'+(xs[0]+20)+'" y="'+cy+'" class="lab" style="font-size:11.5px;fill:#000000;font-weight:600">'+esc(r.name)+'</text>';
    inner+='<text x="'+(xs[1]+2)+'" y="'+cy+'" class="end" style="font-size:10px">'+esc(r.typology||'\u2014')+'</text>';
    const cells=[[r.cen.pop,'int'],[r.cen.income,'usd'],[r.pois,'int'],[r.transit,'int'],[r.walk,'int'],[r.nodes,'int']];
    cells.forEach((cc,i)=>{inner+='<text x="'+(xs[3+i]-8)+'" y="'+cy+'" text-anchor="end" class="mono" style="font-size:10.5px;fill:#000000">'+fmtV(cc[0],cc[1])+'</text>';});
    const sim=(id===FOCUS)?'\u2014':(row[id]!=null?Math.round(100*Math.max(0,1-(+row[id])/maxD))+'%':'\u2014');
    inner+='<text x="'+(xs[9]-8)+'" y="'+cy+'" text-anchor="end" class="mono" style="font-size:10.5px;fill:'+(id===FOCUS?'#8a8a8a':'#000000')+'">'+sim+'</text>';
    inner+='<line x1="'+pad+'" y1="'+(y+rh)+'" x2="'+(W-pad)+'" y2="'+(y+rh)+'" class="axis"/>';});
  return {title:'Comparison table',svg:svg(W,H,'Comparison table','focus + selected districts',inner)};
}
function cDayNight(){
  const s=sel();const W=800,top=78,rowH=58,pad=20,H=top+s.length*rowH+40;let inner='';
  let maxPop=1;s.forEach(id=>{const r=byId[id];maxPop=Math.max(maxPop,r.day||0,r.night||0);});
  const barMaxW=(W-pad-200);const DAY_C='#e8a13a',NIGHT_C='#3b5b8c';
  s.forEach((id,i)=>{const y=top+i*rowH,r=byId[id];
    const day=r.day,night=r.night,ratio=r.daynight,prof=r.dn_profile,low=r.dn_low;
    inner+='<circle cx="'+(pad+5)+'" cy="'+(y+8)+'" r="5" fill="'+colorOf(id)+'"/>';
    inner+='<text x="'+(pad+16)+'" y="'+(y+12)+'" class="lab" style="font-size:12px;fill:#000000">'+esc(r.name)+'</text>';
    if(low||day==null||night==null){inner+='<text x="'+(pad+16)+'" y="'+(y+34)+'" class="end">no population data</text>';return;}
    const dayW=(day/maxPop)*barMaxW,nightW=(night/maxPop)*barMaxW,bx=pad+150;
    inner+='<text x="'+(bx-8)+'" y="'+(y+8)+'" text-anchor="end" class="axlab">day</text>';
    inner+='<rect x="'+bx+'" y="'+(y)+'" width="'+dayW.toFixed(1)+'" height="13" rx="2" fill="'+DAY_C+'"/>';
    inner+='<text x="'+(bx+dayW+6)+'" y="'+(y+10)+'" class="mono" style="font-size:10px">'+(day>=1000?(day/1000).toFixed(1)+'k':day)+'</text>';
    inner+='<text x="'+(bx-8)+'" y="'+(y+27)+'" text-anchor="end" class="axlab">night</text>';
    inner+='<rect x="'+bx+'" y="'+(y+19)+'" width="'+nightW.toFixed(1)+'" height="13" rx="2" fill="'+NIGHT_C+'"/>';
    inner+='<text x="'+(bx+nightW+6)+'" y="'+(y+29)+'" class="mono" style="font-size:10px">'+(night>=1000?(night/1000).toFixed(1)+'k':night)+'</text>';
    const ratioTxt=(ratio==null)?'--':(ratio.toFixed(2)+'x');
    inner+='<text x="'+(W-pad)+'" y="'+(y+8)+'" text-anchor="end" class="mono" style="font-size:14px;fill:#000000;font-weight:700">'+ratioTxt+'</text>';
    inner+='<text x="'+(W-pad)+'" y="'+(y+26)+'" text-anchor="end" class="end">'+(prof||'')+'</text>';
  });
  let ly=top+s.length*rowH+14;
  inner+='<rect x="'+pad+'" y="'+ly+'" width="9" height="9" rx="2" fill="'+DAY_C+'"/><text x="'+(pad+13)+'" y="'+(ly+8)+'" class="axlab">day (2PM / working)</text>';
  inner+='<rect x="'+(pad+170)+'" y="'+ly+'" width="9" height="9" rx="2" fill="'+NIGHT_C+'"/><text x="'+(pad+183)+'" y="'+(ly+8)+'" class="axlab">night (2AM / residential)</text>';
  inner+='<text x="'+(W-pad)+'" y="'+(ly+8)+'" text-anchor="end" class="axlab">ratio = day / night</text>';
  return {title:'Day / night population',svg:svg(W,H,'Day / night population','NSI AM vs PM population - who is here when',inner)};
}
function cIndices(){
  const s=sel();const W=560,H=470,cx=280,cy=250,R=190,LR=1.24;
  const AXES=[['Live / Work / Play','live_work_play'],['Intensity','intensity'],['Connectivity','connectivity']];
  const N=AXES.length;const ang=p=>(-90+p*360/N)*Math.PI/180;
  const pt=(p,v)=>[cx+R*v*Math.cos(ang(p)),cy+R*v*Math.sin(ang(p))];let inner='';
  [.25,.5,.75,1].forEach(f=>{inner+='<polygon class="ring" points="'+AXES.map((_,p)=>pt(p,f).map(n=>n.toFixed(1)).join(',')).join(' ')+'"/>';});
  AXES.forEach(([label],p)=>{const a=pt(p,1),b=pt(p,LR);
    inner+='<line class="spoke" x1="'+cx+'" y1="'+cy+'" x2="'+a[0].toFixed(1)+'" y2="'+a[1].toFixed(1)+'"/>';
    inner+='<text class="axlab" x="'+b[0].toFixed(1)+'" y="'+b[1].toFixed(1)+'" text-anchor="middle">'+label+'</text>';});
  [.25,.5,.75,1].forEach(f=>{inner+='<text x="'+(cx+4)+'" y="'+(cy-R*f+3).toFixed(1)+'" class="end" style="font-size:8px">'+Math.round(f*100)+'</text>';});
  s.forEach(id=>{const c=colorOf(id),r=byId[id];const idx=r.indices&&r.indices.scores;
    if(!idx)return;if(AXES.every(([,key])=>idx[key]==null))return;
    const pts=AXES.map(([,key],i)=>{const v=idx[key];return pt(i,(v==null?0:v/100));});
    inner+='<polygon points="'+pts.map(q=>q.map(n=>n.toFixed(1)).join(',')).join(' ')+'" fill="'+c+'" fill-opacity="0.10" stroke="'+c+'" stroke-width="2" stroke-linejoin="round"/>';
    pts.forEach((q,i)=>{const v=idx[AXES[i][1]];if(v==null)return;inner+='<circle cx="'+q[0].toFixed(1)+'" cy="'+q[1].toFixed(1)+'" r="2.6" fill="'+c+'"/>';});
  });
  let ry=cy+R+30;
  s.forEach((id,i)=>{const r=byId[id],idx=(r.indices&&r.indices.scores)||{};
    const fmt=v=>v==null?'--':Math.round(v);const low=(r.indices&&r.indices.low_confidence)?' *':'';
    inner+='<circle cx="20" cy="'+(ry+i*18-3)+'" r="4" fill="'+colorOf(id)+'"/>';
    inner+='<text x="30" y="'+(ry+i*18)+'" class="mono" style="font-size:10px;fill:#000000">'+esc(r.name)+low+'</text>';
    inner+='<text x="'+(W-20)+'" y="'+(ry+i*18)+'" text-anchor="end" class="mono" style="font-size:10px">LWP '+fmt(idx.live_work_play)+'  INT '+fmt(idx.intensity)+'  CON '+fmt(idx.connectivity)+'</text>';
  });
  const Hd=Math.max(H,ry+s.length*18+10);
  return {title:'Planning indices (3 composite)',svg:svg(W,Hd,'Planning indices - 3 composite scores','percentile vs corpus  (* partial)',inner),center:true};
}
function cDelta(){
  const s=sel();const W=800,pad=20,labelW=150,trackX=pad+labelW,trackW=W-trackX-90;
  const top=70,rowH=46,H=top+METRICS.length*rowH+20;let inner='';
  const quantile=(arr,q)=>{if(!arr.length)return null;const p=(arr.length-1)*q,b=Math.floor(p),r=p-b;return arr[b+1]!==undefined?arr[b]+r*(arr[b+1]-arr[b]):arr[b];};
  inner+='<text x="'+trackX+'" y="'+(top-14)+'" class="axlab">corpus min</text>';
  inner+='<text x="'+(trackX+trackW)+'" y="'+(top-14)+'" text-anchor="end" class="axlab">corpus max  (band = IQR, dash = median)</text>';
  METRICS.forEach(([id,label,fmt,acc],mi)=>{
    const y=top+mi*rowH;
    const vals=ROWS.map(r=>+acc(r)).filter(isFinite).sort((a,b)=>a-b);
    if(!vals.length)return;
    const lo=vals[0],hi=vals[vals.length-1],span=(hi-lo)||1;
    const q1=quantile(vals,.25),q2=quantile(vals,.5),q3=quantile(vals,.75);
    const X=v=>trackX+((v-lo)/span)*trackW;
    inner+='<text x="'+pad+'" y="'+(y+4)+'" class="lab" style="font-size:12px;fill:#000000">'+label+'</text>';
    inner+='<line x1="'+trackX+'" y1="'+(y+18)+'" x2="'+(trackX+trackW)+'" y2="'+(y+18)+'" stroke="#e0e0e0" stroke-width="1"/>';
    inner+='<rect x="'+X(q1).toFixed(1)+'" y="'+(y+12)+'" width="'+(X(q3)-X(q1)).toFixed(1)+'" height="12" rx="2" fill="#ece6db"/>';
    inner+='<line x1="'+X(q2).toFixed(1)+'" y1="'+(y+9)+'" x2="'+X(q2).toFixed(1)+'" y2="'+(y+27)+'" stroke="#8a8a8a" stroke-width="1.4" stroke-dasharray="2 2"/>';
    s.forEach(id2=>{const r=byId[id2],v=+acc(r);if(!isFinite(v))return;
      inner+='<circle cx="'+X(v).toFixed(1)+'" cy="'+(y+18)+'" r="5" fill="'+colorOf(id2)+'" stroke="#ffffff" stroke-width="1.5"/>';});
    const f=s[0],fv=+acc(byId[f]),fp=pctRank(fv,vals);
    inner+='<text x="'+(W-pad)+'" y="'+(y+12)+'" text-anchor="end" class="mono" style="font-size:11px;fill:#000000">'+fmtV(fv,fmt)+'</text>';
    inner+='<text x="'+(W-pad)+'" y="'+(y+26)+'" text-anchor="end" class="end">'+(fp==null?'--':'p'+Math.round(fp*100))+'</text>';
  });
  return {title:'How it measures up',svg:svg(W,H,'How it measures up','focus vs corpus distribution per metric  (p## = focus percentile)',inner)};
}
const _Lg=v=>(v!=null&&isFinite(v)&&v>0)?Math.log(v):null;
const _Nz=v=>{const x=+v;return isFinite(x)?x:null;};
function _bboxKm2(r){const b=r.bbox;if(!b||b.length<4)return null;const ml=(b[1]+b[3])/2*Math.PI/180;const a=Math.abs(b[2]-b[0])*111.32*Math.cos(ml)*Math.abs(b[3]-b[1])*110.574;return a>0?a:null;}
function _walkRatio(r){const ac=_Nz(r.walk),a=_bboxKm2(r);if(ac==null||a==null)return null;return Math.min(1,ac/(a*247.105));}
function _transitW(r){const k=r._kind||{};return (_Nz(k.rail_station)||0)*3+(_Nz(k.tram_stop)||0)*2+(_Nz(k.bus_stop)||0)+(_Nz(k.public_transport_station)||0);}
const LENS_DEFS={
  economic:{title:'Economy',fn:r=>[['income',_Nz(r.cen.income)],['age',_Nz(r.cen.age)],['renter',_Nz(r.cen.renter)],['bach',_Nz(r.cen.bach)],['jpr',_Nz(r._jpr)],['know',_Nz(r._know)]]},
  form:{title:'Urban form',fn:r=>[['cov',_Nz(r._cov)],['dens',_Nz(r._dens)],['comp',_Nz(r._comp)],['int',_Nz(r._int)]]},
  program:{title:'Program mix',fn:r=>{const s=r._pl||{};return [['resid',_Nz(s.residential)],['office',_Nz(s.office)],['retail',_Nz(s.retail_food_entertainment)],['parking',_Nz(s.parking)],['lwp',_Nz(r._lwp)]];}},
  temporal:{title:'Day/night life',fn:r=>[['swing',_Lg(r.daynight)],['daypop',_Lg(r.day)]]},
  access:{title:'Connectivity',fn:r=>[['walk',_walkRatio(r)],['transit',_transitW(r)],['conn',_Nz(r._conn)]]},
  anchor:{title:'Anchor / investment',fn:r=>{let c=_Nz(r._cost);c=(c&&c>0)?c:null;return [['parking',_Nz((r._pl||{}).parking)],['cost',_Lg(c)],['year',_Nz(r._year)]];}}
};
const LENS_KEYS=['economic','form','program','temporal','access','anchor'];
let SIM=null;
function buildSIM(){SIM={};for(const lk of LENS_KEYS){const fn=LENS_DEFS[lk].fn,feats=fn(ROWS[0]).map(f=>f[0]);const cols={};feats.forEach(f=>cols[f]=[]);ROWS.forEach(r=>fn(r).forEach(([n,v])=>{if(v!=null)cols[n].push(v);}));const stats={};feats.forEach(f=>{const xs=cols[f];if(xs.length){const mn=xs.reduce((a,b)=>a+b,0)/xs.length;const vr=xs.reduce((a,b)=>a+(b-mn)*(b-mn),0)/xs.length;stats[f]=[mn,vr>0?Math.sqrt(vr):1];}else stats[f]=[0,1];});const zv={};ROWS.forEach(r=>{const o={};fn(r).forEach(([n,v])=>{o[n]=v==null?null:(v-stats[n][0])/stats[n][1];});zv[r.sad_id]=o;});SIM[lk]={feats,zv};}}
function _lensDist(va,vb,feats){let sq=0,used=0;const drivers=[];feats.forEach(f=>{const a=va[f],b=vb[f];if(a!=null&&b!=null){const d=(a-b)*(a-b);sq+=d;used++;drivers.push([f,d]);}});const need=Math.max(2,Math.ceil(feats.length*0.6));if(used<need)return null;drivers.sort((x,y)=>x[1]-y[1]);return {dist:Math.sqrt(sq/used),close:drivers.slice(0,2).map(d=>d[0])};}
function nearestOn(focusId,lk,k){const o=SIM[lk];const fv=o.zv[focusId];const fn0=(byId[focusId]||{}).name;const ds=[];ROWS.forEach(r=>{if(r.sad_id===focusId||r.name===fn0)return;const res=_lensDist(fv,o.zv[r.sad_id],o.feats);if(res)ds.push([r,res.dist,res.close]);});ds.sort((a,b)=>a[1]-b[1]);return ds.slice(0,k);}
let ANMODE='focus';
function rankOf(focusId,lk,targetId){const full=nearestOn(focusId,lk,999);const i=full.findIndex(n=>n[0].sad_id===targetId);return i<0?null:i+1;}
function cAnalogues(){
  if(!ROWS||!ROWS.length)return {title:'Analogues',svg:svg(800,90,'Analogues','no data','')};
  buildSIM();const focusId=FOCUS,fr=byId[focusId];const ids=sel();const comps=ids.filter(id=>id!==focusId);
  const W=800,pad=18;let inner='';
  if(ANMODE==='each' && comps.length>=1){
    const x0=150,colW=(W-x0-10)/6,rowH=30,y0=78;
    LENS_KEYS.forEach((lk,ci)=>{inner+='<text x="'+(x0+ci*colW)+'" y="'+(y0-14)+'" style="font:600 8px Arial;fill:#8a8a8a;letter-spacing:0.04em">'+LENS_DEFS[lk].title.toUpperCase().slice(0,11)+'</text>';});
    ids.forEach((id,ri)=>{const yy=y0+ri*rowH;
      inner+='<circle cx="22" cy="'+(yy-3)+'" r="4" fill="'+colorOf(id)+'"/>';
      inner+='<text x="32" y="'+yy+'" style="font:700 10px Arial;fill:#000000">'+esc(byId[id].name).slice(0,17)+'</text>';
      LENS_KEYS.forEach((lk,ci)=>{const top=nearestOn(id,lk,1)[0];const nm=top?esc(top[0].name).slice(0,13):'--';
        inner+='<text x="'+(x0+ci*colW)+'" y="'+yy+'" style="font:9px Arial;fill:#5a5a5a">'+nm+'</text>';});
    });
    const H=y0+ids.length*rowH+16;
    return {title:'Closest analogue by lens',svg:svg(W,H,'Each district\u2019s closest analogue by lens','one row per selected district',inner)};
  }
  const colW=W/3,cellH=230;const H=86+Math.ceil(LENS_KEYS.length/3)*cellH+10;
  LENS_KEYS.forEach((lk,li)=>{const col=li%3,row=Math.floor(li/3);const x0=col*colW+pad,y0=72+row*cellH;
    const near=nearestOn(focusId,lk,5);const maxD=near.length?Math.max.apply(null,near.map(n=>n[1])):1;
    inner+='<text x="'+x0+'" y="'+y0+'" class="lab" style="font-size:12.5px;font-weight:700;fill:#000000">'+LENS_DEFS[lk].title+'</text>';
    if(!near.length){inner+='<text x="'+x0+'" y="'+(y0+20)+'" class="end">insufficient data</text>';return;}
    near.forEach((n,ri)=>{const r=n[0],d=n[1],close=n[2],yy=y0+18+ri*34;const sim=Math.max(0,1-d/(maxD*1.15));const barW=(colW-2*pad-20)*sim;const isC=comps.includes(r.sad_id);
      if(isC)inner+='<circle cx="'+(x0+4)+'" cy="'+(yy-3)+'" r="5.5" fill="none" stroke="'+colorOf(r.sad_id)+'" stroke-width="1.5"/>';
      inner+='<circle cx="'+(x0+4)+'" cy="'+(yy-3)+'" r="3" fill="'+colorOf(r.sad_id)+'"/>';
      inner+='<text x="'+(x0+12)+'" y="'+yy+'" class="mono" style="font-size:10.5px;fill:#000000;font-weight:'+(isC?'700':'400')+'">'+esc(r.name).slice(0,22)+(isC?' \u2190 yours':'')+'</text>';
      inner+='<rect x="'+(x0+12)+'" y="'+(yy+3)+'" width="'+barW.toFixed(1)+'" height="4" rx="2" fill="'+colorOf(r.sad_id)+'" opacity="0.5"/>';
      inner+='<text x="'+(x0+12)+'" y="'+(yy+13)+'" class="end" style="font-size:8.5px;fill:#8a8a8a">'+close.join(' / ')+'</text>';
    });
    let ey=y0+18+5*34+6;comps.forEach(cid=>{if(!near.find(n=>n[0].sad_id===cid)){const rk=rankOf(focusId,lk,cid);if(rk){inner+='<text x="'+(x0+12)+'" y="'+ey+'" class="end" style="font-size:9px;fill:'+colorOf(cid)+'">'+esc(byId[cid].name).slice(0,18)+'  #'+rk+'</text>';ey+=15;}}});
  });
  return {title:'Closest analogues to '+esc(fr.name),svg:svg(W,H,'Closest analogues to '+esc(fr.name),'nearest match differs by lens',inner)};
}
function _fpNum(v){if(v==null||v==='')return null;const x=+v;return isFinite(x)?x:null;}
function _fpCost(v){const x=_fpNum(v);return (x&&x>0)?x:null;}
const FP_LENSES=[
  ['ECONOMY',  r=>_fpNum(r.cen&&r.cen.income)],
  ['FORM',     r=>_fpNum(r._int)],
  ['PROGRAM',  r=>_fpNum(r._lwp)],
  ['DAY/NIGHT',r=>_fpNum(r.day)],
  ['ACCESS',   r=>_fpNum(r._conn)],
  ['ANCHOR',   r=>_fpCost(r._cost)]
];
let FP_STATS=null;
function _fpPct(v,xs){if(v==null||!xs.length)return null;let c=0;xs.forEach(x=>{if(x<v)c++;});return Math.round(100*c/xs.length);}
function buildFPStats(){FP_STATS=FP_LENSES.map(d=>{const fn=d[1];const xs=ROWS.map(fn).filter(v=>v!=null).sort((a,b)=>a-b);return {fn,xs};});}
function fingerprintOf(r){return FP_STATS.map(s=>{const v=s.fn(r);return v==null?null:_fpPct(v,s.xs);});}
function radarGlyph(cx,cy,R,fp,color,showLabels){
  const n=fp.length,ang=i=>(-Math.PI/2)+i*2*Math.PI/n;let g='';
  [0.5,1].forEach(t=>{let p='';for(let i=0;i<n;i++){const a=ang(i),x=cx+Math.cos(a)*R*t,y=cy+Math.sin(a)*R*t;p+=(i?'L':'M')+x.toFixed(1)+' '+y.toFixed(1);}g+='<path d="'+p+'Z" fill="none" stroke="#e0e0e0" stroke-width="0.75"/>';});
  for(let i=0;i<n;i++){const a=ang(i);g+='<line x1="'+cx+'" y1="'+cy+'" x2="'+(cx+Math.cos(a)*R).toFixed(1)+'" y2="'+(cy+Math.sin(a)*R).toFixed(1)+'" stroke="#e0e0e0" stroke-width="0.5"/>';}
  let dp='',pts=[];
  for(let i=0;i<n;i++){const a=ang(i),t=(fp[i]==null?0:fp[i]/100),x=cx+Math.cos(a)*R*t,y=cy+Math.sin(a)*R*t;pts.push([x,y,fp[i]]);dp+=(i?'L':'M')+x.toFixed(1)+' '+y.toFixed(1);}
  g+='<path d="'+dp+'Z" fill="'+color+'" fill-opacity="0.13" stroke="'+color+'" stroke-width="1.5"/>';
  pts.forEach(p=>{g+='<circle cx="'+p[0].toFixed(1)+'" cy="'+p[1].toFixed(1)+'" r="2" fill="'+(p[2]==null?'#ffffff':color)+'" stroke="'+color+'" stroke-width="1"/>';});
  if(showLabels){for(let i=0;i<n;i++){const a=ang(i),lr=R+13,x=cx+Math.cos(a)*lr,y=cy+Math.sin(a)*lr;const anc=Math.abs(Math.cos(a))<0.3?'middle':(Math.cos(a)>0?'start':'end');g+='<text x="'+x.toFixed(1)+'" y="'+(y+3).toFixed(1)+'" text-anchor="'+anc+'" style="font:600 7.5px Arial;fill:#8a8a8a;letter-spacing:0.04em">'+FP_LENSES[i][0]+'</text>';}}
  return g;
}
let FPMODE='grid';
function radarFrame(cx,cy,R){const n=6,ang=i=>(-Math.PI/2)+i*2*Math.PI/n;let g='';
  [0.5,1].forEach(t=>{let p='';for(let i=0;i<n;i++){const a=ang(i),x=cx+Math.cos(a)*R*t,y=cy+Math.sin(a)*R*t;p+=(i?'L':'M')+x.toFixed(1)+' '+y.toFixed(1);}g+='<path d="'+p+'Z" fill="none" stroke="#e0e0e0" stroke-width="0.75"/>';});
  for(let i=0;i<n;i++){const a=ang(i);g+='<line x1="'+cx+'" y1="'+cy+'" x2="'+(cx+Math.cos(a)*R).toFixed(1)+'" y2="'+(cy+Math.sin(a)*R).toFixed(1)+'" stroke="#e0e0e0" stroke-width="0.5"/>';}
  for(let i=0;i<n;i++){const a=ang(i),lr=R+14,x=cx+Math.cos(a)*lr,y=cy+Math.sin(a)*lr;const anc=Math.abs(Math.cos(a))<0.3?'middle':(Math.cos(a)>0?'start':'end');g+='<text x="'+x.toFixed(1)+'" y="'+(y+3).toFixed(1)+'" text-anchor="'+anc+'" style="font:600 8px Arial;fill:#8a8a8a;letter-spacing:0.04em">'+FP_LENSES[i][0]+'</text>';}
  return g;}
function radarPoly(cx,cy,R,fp,color){const n=fp.length,ang=i=>(-Math.PI/2)+i*2*Math.PI/n;let dp='',pts=[];
  for(let i=0;i<n;i++){const a=ang(i),t=(fp[i]==null?0:fp[i]/100),x=cx+Math.cos(a)*R*t,y=cy+Math.sin(a)*R*t;pts.push([x,y,fp[i]]);dp+=(i?'L':'M')+x.toFixed(1)+' '+y.toFixed(1);}
  let g='<path d="'+dp+'Z" fill="'+color+'" fill-opacity="0.08" stroke="'+color+'" stroke-width="1.5"/>';
  pts.forEach(p=>{g+='<circle cx="'+p[0].toFixed(1)+'" cy="'+p[1].toFixed(1)+'" r="2" fill="'+(p[2]==null?'#ffffff':color)+'" stroke="'+color+'" stroke-width="1"/>';});
  return g;}
function cFingerprint(){
  if(!ROWS||!ROWS.length)return {title:'Fingerprint',svg:svg(800,90,'Fingerprint','no data','')};
  buildFPStats();const ids=sel();const W=800;let inner='';var H;
  if(ids.length<=1){const cx=W/2,cy=130,R=95;
    inner+=radarGlyph(cx,cy,R,fingerprintOf(byId[ids[0]]),colorOf(ids[0]),true);
    inner+='<text x="'+cx+'" y="252" text-anchor="middle" style="font:700 12px Arial;fill:#000000;letter-spacing:0.03em">'+esc(byId[ids[0]].name).toUpperCase()+'</text>';
    H=282;
  } else if(FPMODE==='overlay'){const cx=W/2,cy=152,R=112;
    inner+=radarFrame(cx,cy,R);
    ids.forEach(id=>{inner+=radarPoly(cx,cy,R,fingerprintOf(byId[id]),colorOf(id));});
    let lx=cx-Math.min(ids.length,5)*70;ids.slice(0,5).forEach(id=>{inner+='<circle cx="'+lx+'" cy="300" r="4" fill="'+colorOf(id)+'"/><text x="'+(lx+9)+'" y="304" style="font:700 9px Arial;fill:#000000;letter-spacing:0.02em">'+esc(byId[id].name).slice(0,15).toUpperCase()+'</text>';lx+=140;});
    H=322;
  } else {const per=Math.min(ids.length,5),cellW=W/per,R=Math.min(54,cellW/2-28),cy=104;
    ids.slice(0,5).forEach((id,i)=>{const cx=cellW*i+cellW/2;
      inner+=radarGlyph(cx,cy,R,fingerprintOf(byId[id]),colorOf(id),true);
      inner+='<text x="'+cx+'" y="'+(cy+R+28)+'" text-anchor="middle" style="font:700 8.5px Arial;fill:#000000;letter-spacing:0.02em">'+esc(byId[id].name).slice(0,18).toUpperCase()+'</text>';});
    H=200;
  }
  return {title:'Six-lens fingerprint',svg:svg(W,H,'Six-lens fingerprint (1 metric per lens)','each spoke = corpus percentile on that lens',inner)};
}
let WEIGHTS={economic:1,form:1,program:1,temporal:1,access:1,anchor:1};
const LENS_TIP={economic:'Economic profile: household income, education, jobs per resident, and knowledge/creative job share.',form:'Urban form: built density, coverage, building-size mix, and the intensity index - how compact and built-up it is.',program:'Program mix: the balance of residential, office, retail and other uses - the live/work/play composition.',temporal:'Day/night rhythm: daytime vs nighttime population and the swing between them.',access:'Connectivity: street and intersection density, transit access, and the connectivity index.',anchor:'Anchor and investment: venue type, construction cost, year opened, and parking footprint of the anchor.'};
function _weq(){return LENS_KEYS.every(lk=>WEIGHTS[lk]===WEIGHTS.economic);}
function _heat(sim){sim=Math.max(0,Math.min(1,sim));var r,g,b;function L(a,z,t){return Math.round(a+(z-a)*t);}if(sim<0.5){var t=sim/0.5;r=L(255,239,t);g=L(240,48,t);b=L(235,6,t);}else{var t=(sim-0.5)/0.5;r=L(239,120,t);g=L(48,15,t);b=L(6,0,t);}return 'rgb('+r+','+g+','+b+')';}
function cMatrix(){
  if(!ROWS||!ROWS.length)return {title:'Matrix',svg:svg(800,90,'Matrix','no data','')};
  buildSIM();const focusId=FOCUS;
  const rowsRaw=ROWS.filter(r=>r.sad_id!==focusId).map(r=>{
    const dists=LENS_KEYS.map(lk=>{const o=SIM[lk];const res=_lensDist(o.zv[focusId],o.zv[r.sad_id],o.feats);return res?res.dist:null;});
    return {id:r.sad_id,dists};
  });
  const colMax=LENS_KEYS.map((lk,ci)=>{const ds=rowsRaw.map(r=>r.dists[ci]).filter(d=>d!=null);return ds.length?Math.max.apply(null,ds):1;});
  const rowsData=rowsRaw.map(r=>{let wsum=0,acc=0;LENS_KEYS.forEach((lk,ci)=>{const d=r.dists[ci];if(d==null)return;const sim=1-Math.min(1,d/(colMax[ci]||1));const w=WEIGHTS[lk];acc+=w*sim;wsum+=w;});return {id:r.id,dists:r.dists,overall:(wsum>0?acc/wsum:0)};});
  rowsData.sort((a,b)=>b.overall-a.overall);
  const comps=sel().filter(id=>id!==focusId);
  let shown=rowsData.slice(0,15);
  comps.forEach(cid=>{if(!shown.find(r=>r.id===cid)){const r=rowsData.find(x=>x.id===cid);if(r)shown.push(r);}});
  const W=800,labW=148,ovW=46,colW=(W-labW-ovW-8)/6,rowH=15,y0=92;let inner='';
  LENS_KEYS.forEach((lk,ci)=>{inner+='<text x="'+(labW+ci*colW+colW/2)+'" y="'+(y0-8)+'" text-anchor="middle" style="font:600 7.5px Arial;fill:#8a8a8a;letter-spacing:0.03em">'+({economic:'ECONOMY',form:'FORM',program:'PROGRAM',temporal:'DAY/NIGHT',access:'ACCESS',anchor:'ANCHOR'}[lk]||lk.toUpperCase())+'</text>';});
  inner+='<text x="'+(labW+6*colW+ovW/2)+'" y="'+(y0-8)+'" text-anchor="middle" style="font:700 7.5px Arial;fill:#000000">'+(_weq()?'OVERALL':'OVERALL*')+'</text>';
  shown.forEach((r,ri)=>{const yy=y0+ri*rowH;const isC=comps.includes(r.id);
    inner+='<text x="'+(labW-6)+'" y="'+(yy+11)+'" text-anchor="end" style="font:'+(isC?'700':'400')+' 9px Arial;fill:'+(isC?colorOf(r.id):'#000000')+'">'+esc(byId[r.id].name).slice(0,24)+'</text>';
    r.dists.forEach((d,ci)=>{const x=labW+ci*colW;
      if(d==null){inner+='<rect x="'+x+'" y="'+yy+'" width="'+(colW-2)+'" height="'+(rowH-2)+'" fill="#f2f2f2" stroke="#e0e0e0" stroke-width="0.5"/>';}
      else{const sim=1-Math.min(1,d/(colMax[ci]||1));inner+='<rect x="'+x+'" y="'+yy+'" width="'+(colW-2)+'" height="'+(rowH-2)+'" fill="'+_heat(sim)+'"/>';}
    });
    const ov=Math.max(0,Math.min(1,r.overall));
    inner+='<rect x="'+(labW+6*colW)+'" y="'+yy+'" width="'+(ovW-2)+'" height="'+(rowH-2)+'" fill="'+_heat(ov)+'" stroke="#e0e0e0" stroke-width="0.5"/>';
    if(isC)inner+='<rect x="'+(labW)+'" y="'+yy+'" width="'+(6*colW+ovW-2)+'" height="'+(rowH-2)+'" fill="none" stroke="'+colorOf(r.id)+'" stroke-width="1"/>';
  });
  const gy=y0+shown.length*rowH+22,gx=labW;
  inner+='<text x="'+gx+'" y="'+(gy-4)+'" style="font:600 8px Arial;fill:#8a8a8a">LESS SIMILAR</text>';
  for(let i=0;i<24;i++){inner+='<rect x="'+(gx+78+i*7)+'" y="'+(gy-12)+'" width="7" height="9" fill="'+_heat(i/23)+'"/>';}
  inner+='<text x="'+(gx+78+24*7+6)+'" y="'+(gy-4)+'" style="font:600 8px Arial;fill:#000000">MORE SIMILAR</text>';
  const H=gy+10;
  return {title:'Cross-dimension matrix',svg:svg(W,H,'Cross-dimension matrix','similarity to focus per lens - darker = closer (each column self-scaled)',inner)};
}
// full cMaps panel for injection. Async-loads THUMBS once, draws grid of thumbnails.
let THUMBS=null, THUMBS_LOADING=false;
let MAPSAT=false, MAPSAT_OP=0.7, MAPSTREETS=false, MAPPARKS=false;
let MAPSIZE=0;  // 0 = auto cell size; >0 = continuous cell px (size slider)
let MAPBOUNDARY=false;
let MAPSHOW={'residential':1,'hotel/institutional':1,'retail':1,'office':1,'entertainment':1,'civic/medical/parking':1,'industrial/service':1,'other':1};
const SHARED_SPAN_M=2500;
function occGroup(raw){const v=String(raw||'').toLowerCase();
  if(v==='residential')return 'residential';
  if(v==='hotel/institutional')return 'hotel/institutional';
  if(v==='retail')return 'retail';
  if(v==='office')return 'office';
  if(v==='entertainment')return 'entertainment';
  if(v==='civic/medical/parking')return 'civic/medical/parking';
  if(v==='industrial/service')return 'industrial/service';
  return 'other';}
const OCC_COLOR={'residential':'#0072B2','hotel/institutional':'#56B4E9','retail':'#E69F00','office':'#D55E00','entertainment':'#CC79A7','civic/medical/parking':'#009E73','industrial/service':'#F0E442','other':'#999999'};
const OCC_ORDER=['residential','hotel/institutional','retail','office','entertainment','civic/medical/parking','industrial/service','other'];
let OCC_LUT=null;
function _thumbFill(data,i,layer){
  if(layer==='occupancy'){return OCC_COLOR[OCC_LUT[data.occ[i]]||'mixed/other'];}
  if(layer==='height'){const h=data.ht[i];if(h==null)return '#eeeeee';const t=Math.min(1,h/20),v=Math.round(235-t*205);return 'rgb('+v+','+v+','+Math.round(240-t*120)+')';}
  if(layer==='age'){const y=data.yr[i];if(y==null)return '#eeeeee';const t=Math.max(0,Math.min(1,(y-1950)/75));return 'rgb('+Math.round(70+t*165)+','+Math.round(60+t*95)+','+Math.round(55+t*45)+')';}
  return '#cccccc';
}
// ===== C6 cMaps v2: fill-crop only, parks+streets+satellite layers, thin-black outlines =====
// Globals expected: THUMBS, THUMBS_LOADING, SHARED_SPAN_M, OCC_LUT, OCC_COLOR, OCC_ORDER, MAPSHOW
// New globals: MAPSAT (bool), MAPSAT_OP (0-1 opacity), MAPSTREETS (bool), MAPPARKS (bool)

function _fillProj(nx,ny,wm,hm,ppm,ox,oy,S,cx,cy){
  // project normalized 0-1000 coord to pixels, fill-crop centered on (cx,cy) normalized center
  const mx=nx/1000*wm, my=ny/1000*hm;
  const ccx=cx/1000*wm, ccy=cy/1000*hm;   // center in meters (from bcenter or 500,500)
  return [ox+S/2+(mx-ccx)*ppm, oy+S/2+(my-ccy)*ppm];
}

// lon/lat bbox of the 2500m window centered on the district's boundary centroid (for satellite)
function _toMerc(lon,lat){const R=6378137.0;return [R*lon*Math.PI/180, R*Math.log(Math.tan(Math.PI/4+lat*Math.PI/360))];}
function _satURL(data,S){
  const bb=data.bbox; if(!bb) return null;
  const c=data.bcenter||[500,500];
  const clon=bb[0]+(c[0]/1000)*(bb[2]-bb[0]);
  const clat=bb[3]-(c[1]/1000)*(bb[3]-bb[1]);
  const m=_toMerc(clon,clat);
  // Web Mercator scale factor: 1 ground metre = 1/cos(lat) merc-metres. Correct the
  // half-window so the image shows the true SHARED_SPAN_M ground metres (matches footprints).
  const half=(SHARED_SPAN_M/2)/Math.cos(clat*Math.PI/180);
  const box=[m[0]-half,m[1]-half,m[0]+half,m[1]+half];
  const px=Math.round(S*2);
  return 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export'
    +'?bbox='+box.join(',')+'&bboxSR=3857&imageSR=3857&size='+px+','+px+'&format=jpg&f=image';
}
function _drawThumb(ox,oy,S,data,cid){
  const wm=data.w_m||SHARED_SPAN_M, hm=data.h_m||SHARED_SPAN_M;
  const ppm=S/SHARED_SPAN_M;
  const c=data.bcenter||[500,500];
  const P=(nx,ny)=>_fillProj(nx,ny,wm,hm,ppm,ox,oy,S,c[0],c[1]);
  const clip='clip'+cid;
  let g='<clipPath id="'+clip+'"><rect x="'+ox+'" y="'+oy+'" width="'+S+'" height="'+S+'"/></clipPath>';
  g+='<rect x="'+ox+'" y="'+oy+'" width="'+S+'" height="'+S+'" fill="#ffffff"/>';
  g+='<g clip-path="url(#'+clip+')">';
  // 1. satellite (under everything)
  if(MAPSAT){const u=_satURL(data,S);if(u)g+='<image x="'+ox+'" y="'+oy+'" width="'+S+'" height="'+S+'" href="'+u+'" preserveAspectRatio="xMidYMid slice" opacity="'+MAPSAT_OP+'"/>';}
  // 2. parks (green fill)
  if(MAPPARKS && data.parks){data.parks.forEach(flat=>{let d='';for(let k=0;k<flat.length;k+=2){const p=P(flat[k],flat[k+1]);d+=(k?'L':'M')+p[0].toFixed(1)+' '+p[1].toFixed(1);}d+='Z';g+='<path d="'+d+'" fill="#A8D5BA" fill-opacity="0.55" stroke="none"/>';});}
  // 3. streets (gray lines, width by centrality weight stw)
  if(MAPSTREETS && data.streets){data.streets.forEach((flat,i)=>{const w=(data.stw&&data.stw[i])||0.3;let d='';for(let k=0;k<flat.length;k+=2){const p=P(flat[k],flat[k+1]);d+=(k?'L':'M')+p[0].toFixed(1)+' '+p[1].toFixed(1);}g+='<path d="'+d+'" fill="none" stroke="#9a9a9a" stroke-width="'+(0.4+w*1.1).toFixed(2)+'" stroke-opacity="0.8"/>';});}
  // 4. buildings (occupancy), respecting per-class visibility
  data.b.forEach((flat,i)=>{
    const grp=OCC_LUT[data.occ[i]]||'unclassified';
    if(!MAPSHOW[grp])return;
    let d='';for(let k=0;k<flat.length;k+=2){const p=P(flat[k],flat[k+1]);d+=(k?'L':'M')+p[0].toFixed(1)+' '+p[1].toFixed(1);}
    d+='Z';g+='<path d="'+d+'" fill="'+(OCC_COLOR[grp]||'#8a8a8a')+'" stroke="none"/>';
  });
  if(MAPBOUNDARY && data.bpoly){data.bpoly.forEach(flat=>{let d='';for(let k=0;k<flat.length;k+=2){const p=P(flat[k],flat[k+1]);d+=(k?'L':'M')+p[0].toFixed(1)+' '+p[1].toFixed(1);}d+='Z';g+='<path d="'+d+'" fill="none" stroke="#333333" stroke-width="1.2" stroke-dasharray="5 3" stroke-opacity="0.9"/>';});}
  g+='</g>';
  return g;
}

function cMaps(){
  if(!THUMBS){
    if(!THUMBS_LOADING){THUMBS_LOADING=true;
      fetch('/_compare_ui/thumbnails.json').then(r=>r.json()).then(j=>{THUMBS=j;OCC_LUT={};const lg=j._occ_legend||{};Object.keys(lg).forEach(c=>OCC_LUT[c]=occGroup(lg[c]));render();}).catch(e=>{THUMBS={};});
    }
    return {title:'District footprints',svg:svg(800,120,'District footprints','loading building geometry...','')};
  }
  const ids=sel().filter(id=>THUMBS[id]);
  if(!ids.length)return {title:'District footprints',svg:svg(800,120,'District footprints','no footprint data for selection','')};
  const W=800,gap=12;
  const autoCols=Math.min(ids.length,4);
  const autoS=(W-40)/autoCols-12;
  const S=Math.max(120,Math.min(760, MAPSIZE||autoS));
  const cols=Math.max(1,Math.min(ids.length, Math.floor((W-20)/(S+gap))));
  const rows=Math.ceil(ids.length/cols);
  let inner='';let y0=64;
  ids.forEach((id,idx)=>{const col=idx%cols,row=Math.floor(idx/cols);
    const ox=20+col*(S+gap),oy=y0+row*(S+34);
    const data=THUMBS[id];
    const isF=(id===FOCUS);
    inner+='<a href="../_ui/?sad='+encodeURIComponent(id)+'" target="_top" style="cursor:pointer"><title>Open '+esc(byId[id].name)+' in viewer</title>';
    inner+=_drawThumb(ox,oy,S,data,idx);
    // thin BLACK outline for all; focus thicker (double weight)
    inner+='<rect x="'+ox+'" y="'+oy+'" width="'+S+'" height="'+S+'" fill="none" stroke="#000000" stroke-width="'+(isF?2:0.75)+'"/>';
    inner+='<text x="'+ox+'" y="'+(oy+S+16)+'" style="font:700 9px Arial;fill:#000000;letter-spacing:0.02em">'+esc(byId[id].name).slice(0,24).toUpperCase()+(isF?'  \u25c0':'')+'</text>';
    inner+='</a>';
  });
  let H=y0+rows*(S+34)+14;
  return {title:'District footprints',svg:svg(W,H,'District footprints','simplified building footprints, shared 2.5km scale - click a district to open it in the viewer',inner)};
}
const CHARTS=[['census',cCensus],['table',cTable],['rose',cRose],['indices',cIndices],['delta',cDelta],['analogues',cAnalogues],['fingerprint',cFingerprint],['matrix',cMatrix],['maps',cMaps],['similarity',cSim],['program',cProgram],['daynight',cDayNight],['scatter',cScatter],['ranking',cRank]];

function svgToPng(svgStr,name){
  const vb=svgStr.match(/viewBox="0 0 (\d+(?:\.\d+)?) (\d+(?:\.\d+)?)"/);const sc=2,w=(+vb[1])*sc,h=(+vb[2])*sc;
  const blob=new Blob([svgStr],{type:'image/svg+xml;charset=utf-8'}),url=URL.createObjectURL(blob),img=new Image();
  img.onload=function(){const c=document.createElement('canvas');c.width=w;c.height=h;const ctx=c.getContext('2d');
    ctx.fillStyle='#ffffff';ctx.fillRect(0,0,w,h);ctx.drawImage(img,0,0,w,h);URL.revokeObjectURL(url);
    c.toBlob(function(b){const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=name+'.png';document.body.appendChild(a);a.click();a.remove();});};
  img.src=url;
}
let LAST={};
function metricSelect(id,val){return '<select data-axis="'+id+'">'+METRICS.map(m=>'<option value="'+m[0]+'"'+(m[0]===val?' selected':'')+'>'+m[1]+'</option>').join('')+'</select>';}
const METHOD={
  census:{title:'Demographics',notes:[
    {label:'Median income',calc:'Pop-weighted median household income across intersecting block groups.',src:'ACS 5-year (pop-weighted)',vin:'2019-2023'},
    {label:'Population',calc:'Estimated population across intersecting block groups, apportioned to district.',src:'ACS 5-year',vin:'2019-2023'},
    {label:'Renter / Educated / Age',calc:'Pop-weighted shares (renter-occupied, bachelors-plus, median age) across block groups.',src:'ACS 5-year',vin:'2019-2023'},
    {label:'Coverage',calc:'US-only; 4 Canadian districts lack ACS and show no demographic data.',src:'-',vin:'-'}]},
  rose:{title:'Morphometric profile',notes:[
    {label:'Coverage / density / grain',calc:'Building-footprint morphometrics: coverage=footprint/area, density=bldgs per km2, plus grain, compactness, orientation. Shown as corpus percentile per axis.',src:'NSI/M3d building geometry',vin:'release on file'}]},
  indices:{title:'Planning index profile',notes:[
    {label:'Method (all 3)',calc:'Each input to corpus percentile (0-100); index = equal-weight mean of available input percentiles. v1, weights tunable.',src:'-',vin:'-'},
    {label:'Live/Work/Play',calc:'Normalized entropy of live(residential)/work(office, jobs-suppressed)/play(retail+hotel+sport) x coverage x dominance penalty (no single use over 50pct). Encodes 3+ complementary uses, none overpowering.',src:'program_land (NSI+OSM) + LODES8 jobs',vin:'2021-2026'},
    {label:'Intensity',calc:'Mean percentile of building coverage, building density, population density.',src:'morphometrics + ACS',vin:'2019-2023'},
    {label:'Connectivity',calc:'Mean percentile of walkshed-area-ratio + transit tier-weighted score (rail x3 + tram x2 + bus x1). Excludes broken clipped-centrality.',src:'OSM walkshed + GTFS/OSM transit',vin:'June 2026'},
    {label:'Note',calc:'LWP rewards balance, not vitality/magnitude - flagged for planner review.',src:'-',vin:'-'}]},
  delta:{title:'How it measures up',notes:[
    {label:'Strips',calc:'Per metric: corpus IQR band (25-75 pctile) + median tick; focus and comparison markers placed by value; p## = focus percentile rank.',src:'per-metric (see each)',vin:'mixed'}]},
  program:{title:'Program mix',notes:[
    {label:'By occupancy',calc:'Building program by floor area (NSI occupancy x stories) + surface parking by ground area (OSM lots). 8 buckets, sqft-weighted. SPORT-BLIND: NSI does not label stadia.',src:'FEMA NSI + OSM',vin:'NSI on file / OSM June 2026'},
    {label:'By POIs',calc:'Share of points-of-interest by category (count-based). HOUSING-BLIND: under-represents residential.',src:'Overture/OSM POIs',vin:'June 2026'}]},
  daynight:{title:'Day / night population',notes:[
    {label:'Day/night + swing',calc:'Sum of NSI per-building day (pop2pmu65) and night (pop2amu65) population. Swing = day/night. Coverage-gated; US-only.',src:'FEMA NSI',vin:'release on file'}]},
  maps:{title:'District footprints',notes:[{label:'Geometry',calc:'Simplified building footprints per district (Douglas-Peucker, tiny buildings dropped, capped at 1200 largest). Normalized to each district bbox. OCCUPANCY colors group OSM/program tags into 9 classes (sport, retail/food/ent, office, residential, hotel, civic/edu, industrial, parking, mixed). HEIGHT by levels; AGE by year built. Own scale fits each cell; Shared scale draws all at the same real-world meters-per-pixel to compare physical size.',src:'buildings_enriched.geojson per district',vin:'OSM + FEMA/parcel enrichment'}]},
  matrix:{title:'Cross-dimension matrix',notes:[{label:'Cells',calc:'Each cell = similarity of that district to the focus on one lens (inverse mean-squared z-distance, same engine as the analogue lists). Heat is COLUMN-NORMALIZED: each lens self-scaled so the closest match in that column is darkest. Compare within a column, or read a row for a district full profile. Gray = insufficient data. Rows = nearest 15 by overall mean similarity, plus any selected comparisons.',src:'six-lens engine over manifest layers',vin:'see other panels'}]},
  fingerprint:{title:'Six-lens fingerprint',notes:[{label:'Spokes',calc:'Each spoke = this district corpus percentile on one representative metric per lens: economy=median income, form=intensity index, program=live/work/play index, day/night=daytime population, access=connectivity index, anchor=construction cost. Hollow vertex = no data. NOTE: this is a one-metric-per-lens SUMMARY for legibility; analogue matching uses the full multi-feature vectors.',src:'manifest (census/indices/day_night/meta)',vin:'see other panels'}]},
  analogues:{title:'Closest analogues by dimension',notes:[{label:'Per-lens similarity',calc:'Six independent lenses (economy, form, program, day/night, connectivity, anchor). Each feature z-scored across the corpus; similarity = inverse mean-squared z-distance within that lens. Requires 60pct feature overlap. Closest match per lens differs - that divergence is the signal.',src:'all committed manifest layers',vin:'see other panels'}]},
  scatter:{title:'Scatter',notes:[{label:'Axes',calc:'Any two metrics plotted; see each metric note.',src:'-',vin:'-'}]},
  ranking:{title:'Ranking',notes:[{label:'Rank',calc:'Districts ranked on the chosen metric across the corpus.',src:'-',vin:'-'}]},
  table:{title:'Table',notes:[{label:'Values',calc:'Raw metric values per selected district; see each metric note.',src:'-',vin:'-'}]},
  similarity:{title:'Similarity',notes:[{label:'Closeness',calc:'Distance in the precomputed feature embedding. NOTE: current basis includes POI-derived features; occupancy-based recompute is planned.',src:'embedding.distance_matrix',vin:'-'}]}
};
function methodHTML(key){
  const m=METHOD[key]; if(!m)return '<div style="padding:8px">No methodology note for this panel.</div>';
  let h='<div style="font:700 16px Arial;color:#000000;margin-bottom:10px">'+m.title+' - methodology</div>';
  h+='<table style="border-collapse:collapse;width:100%;font-family:Arial;font-size:12px">';
  h+='<tr style="text-align:left;color:#8a8a8a;font-size:10.5px"><th style="padding:4px 8px">Metric</th><th style="padding:4px 8px">Calculation</th><th style="padding:4px 8px">Source</th><th style="padding:4px 8px">Vintage</th></tr>';
  m.notes.forEach(n=>{h+='<tr style="border-top:1px solid #e0e0e0;vertical-align:top">'
    +'<td style="padding:6px 8px;font-weight:600;color:#000000;white-space:nowrap">'+n.label+'</td>'
    +'<td style="padding:6px 8px;color:#5a5a5a">'+n.calc+'</td>'
    +'<td style="padding:6px 8px;color:#5a5a5a;white-space:nowrap">'+n.src+'</td>'
    +'<td style="padding:6px 8px;color:#5a5a5a;white-space:nowrap">'+n.vin+'</td></tr>';});
  h+='</table>';
  h+='<div style="margin-top:12px;font-family:monospace;font-size:10px;color:#8a8a8a">Corpus = 41 studied districts. Percentiles vs corpus. US-only data absent for 4 Canadian districts.</div>';
  return h;
}
function updateMapsChart(srcEl){
  // Redraw ONLY the maps chart SVG so the control bar / dragged slider survive.
  let out; try{ out=cMaps(); }catch(e){ return; }
  LAST['maps']=out.svg;
  const block = srcEl && srcEl.closest ? srcEl.closest('.block') : null;
  if(block){ const c=block.querySelector('.chart'); if(c) c.innerHTML=out.svg; }
}
function render(){
  const sheet=document.getElementById('sheet');sheet.innerHTML='';LAST={};
  CHARTS.forEach(([key,fn])=>{let out;try{out=fn();}catch(err){out={title:key,svg:'<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 800 90\"><rect width=\"800\" height=\"90\" fill=\"#ffffff\"/><text x=\"20\" y=\"34\" style=\"font:700 14px Arial;fill:#d64545\">'+key+' panel error</text><text x=\"20\" y=\"58\" style=\"font:11px monospace;fill:#5a5a5a\">'+String(err&&err.message||err).slice(0,90)+'</text></svg>'};}LAST[key]=out.svg;
    let ax='';
    if(key==='scatter')ax='<div class="axsel"><span class="k">Y</span>'+metricSelect('y',SCAT.y)+'<span class="k">X</span>'+metricSelect('x',SCAT.x)+'</div>';
    else if(key==='ranking')ax='<div class="axsel"><span class="k">Metric</span>'+metricSelect('rank',RANKM)+'</div>';
    else if(key==='program')ax='<div class="axsel"><span class="k">Program</span><select data-prog="1"><option value="area"'+(PROGMODE==='area'?' selected':'')+'>By occupancy (incl. surface parking)</option><option value="poi"'+(PROGMODE==='poi'?' selected':'')+'>By POIs</option></select></div>';
    else if(key==='analogues')ax='<div class="axsel"><span class="k">View</span><select data-anmode="1"><option value="focus"'+(ANMODE==='focus'?' selected':'')+'>Closest to focus</option><option value="each"'+(ANMODE==='each'?' selected':'')+'>Each district</option></select></div>';
      else if(key==='fingerprint')ax='<div class="axsel"><span class="k">View</span><select data-fpmode="1"><option value="grid"'+(FPMODE==='grid'?' selected':'')+'>Side by side</option><option value="overlay"'+(FPMODE==='overlay'?' selected':'')+'>Overlay</option></select></div>';
      else if(key==='maps')ax='<div class="axsel">'+'<label class="occchk"><input type="checkbox" data-sat="1"'+(MAPSAT?' checked':'')+'>SATELLITE</label>'+'<input type="range" data-satop="1" min="0" max="1" step="0.05" value="'+MAPSAT_OP+'" style="width:70px;vertical-align:middle" title="satellite opacity">'+'<label class="occchk" style="margin-left:14px"><input type="checkbox" data-streets="1"'+(MAPSTREETS?' checked':'')+'>STREETS</label>'+'<label class="occchk"><input type="checkbox" data-parks="1"'+(MAPPARKS?' checked':'')+'>PARKS</label>'+'<label class="occchk"><input type="checkbox" data-boundary="1"'+(MAPBOUNDARY?' checked':'')+'>SAD BOUNDARY</label>'
      +'<span style="margin-left:14px;font:600 9px monospace;color:#666">SIZE</span>'+'<input type="range" data-mapsize="1" min="120" max="760" step="2" value="'+(MAPSIZE||175)+'" style="width:90px;vertical-align:middle" title="thumbnail size">'+'<div class="occfilt">'+OCC_ORDER.map(grp=>'<label class="occchk" style="--sw:'+OCC_COLOR[grp]+'"><input type="checkbox" data-occ="'+grp+'"'+(MAPSHOW[grp]?' checked':'')+'><span class="occsw"></span>'+grp.toUpperCase()+'</label>').join('')+'</div>'+'</div>';
      else if(key==='matrix'){ax='<div class="wwrap"><div class="wdesc">Weight the dimensions to re-rank the overall match by what matters to your project. Individual lens columns stay unweighted.</div><div class="axsel wsel">'+LENS_KEYS.map(lk=>'<label class="wlab" title="'+(LENS_TIP[lk]||'')+'">'+({economic:'Econ',form:'Form',program:'Prog',temporal:'Day',access:'Access',anchor:'Anchor'}[lk])+'<input type="range" min="0" max="3" step="0.1" value="'+WEIGHTS[lk]+'" data-wt="'+lk+'" title="'+(LENS_TIP[lk]||'')+'"><span class="wval" data-wval="'+lk+'">'+(+WEIGHTS[lk]).toFixed(1)+'</span></label>').join('')+'<button class="ghost" data-wreset="1">reset</button></div></div>';}
    else ax='<span></span>';
    const b=document.createElement('div');b.className='block'+(out.center?' center':'');
    b.innerHTML='<div class="chart">'+out.svg+'</div><div class="blockbar">'+ax
      +'<div><button class="ghost" data-method="'+key+'">Method</button><button class="ghost" data-big="'+key+'">Enlarge</button><button class="ghost" data-png="'+key+'">Download PNG</button></div></div>';
    sheet.appendChild(b);
  });
  sheet.querySelectorAll('[data-png]').forEach(b=>b.addEventListener('click',()=>svgToPng(LAST[b.dataset.png],'SAD_'+b.dataset.png+'_'+byId[FOCUS].name.replace(/\s+/g,'-'))));
  sheet.querySelectorAll('[data-big]').forEach(b=>b.addEventListener('click',()=>openBig(b.dataset.big)));
  sheet.querySelectorAll('[data-method]').forEach(b=>b.addEventListener('click',()=>{const o=document.getElementById('ovl');document.getElementById('ovl-body').innerHTML=methodHTML(b.dataset.method);o.classList.add('on');}));
  sheet.querySelectorAll('[data-axis]').forEach(s=>s.addEventListener('change',e=>{const a=e.target.dataset.axis,v=e.target.value;if(a==='rank')RANKM=v;else SCAT[a]=v;render();}));
  sheet.querySelectorAll('[data-prog]').forEach(s=>s.addEventListener('change',e=>{PROGMODE=e.target.value;render();}));
  sheet.querySelectorAll('[data-anmode]').forEach(s=>s.addEventListener('change',e=>{ANMODE=e.target.value;render();}));
  sheet.querySelectorAll('[data-fpmode]').forEach(s=>s.addEventListener('change',e=>{FPMODE=e.target.value;render();}));
  sheet.querySelectorAll('[data-wt]').forEach(s=>s.addEventListener('input',e=>{WEIGHTS[e.target.dataset.wt]=+e.target.value;render();}));
  sheet.querySelectorAll('[data-sat]').forEach(s=>s.addEventListener('change',e=>{MAPSAT=e.target.checked;updateMapsChart(e.target);}));
  sheet.querySelectorAll('[data-satop]').forEach(s=>s.addEventListener('input',e=>{MAPSAT_OP=+e.target.value;updateMapsChart(e.target);}));
  sheet.querySelectorAll('[data-streets]').forEach(s=>s.addEventListener('change',e=>{MAPSTREETS=e.target.checked;updateMapsChart(e.target);}));
  sheet.querySelectorAll('[data-parks]').forEach(s=>s.addEventListener('change',e=>{MAPPARKS=e.target.checked;updateMapsChart(e.target);}));
  sheet.querySelectorAll('[data-boundary]').forEach(s=>s.addEventListener('change',e=>{MAPBOUNDARY=e.target.checked;updateMapsChart(e.target);}));
  sheet.querySelectorAll('[data-mapsize]').forEach(s=>s.addEventListener('input',e=>{MAPSIZE=+e.target.value;updateMapsChart(e.target);}));
  sheet.querySelectorAll('[data-occ]').forEach(c=>c.addEventListener('change',e=>{MAPSHOW[e.target.dataset.occ]=e.target.checked?1:0;updateMapsChart(e.target);}));

  sheet.querySelectorAll('[data-wreset]').forEach(b=>b.addEventListener('click',()=>{LENS_KEYS.forEach(lk=>WEIGHTS[lk]=1);render();}));
  renderChips();renderAdd();renderAnaEntry();
  document.getElementById('foot').textContent='Focus: '+byId[FOCUS].name+' · '+COMPS.length+' comparison'+(COMPS.length===1?'':'s')+' · '+ROWS.length+' in corpus · enlarge or download any diagram as a slide-ready PNG.';
}
function openBig(key){const o=document.getElementById('ovl');document.getElementById('ovl-body').innerHTML=LAST[key];
  document.getElementById('ovl-png').onclick=()=>svgToPng(LAST[key],'SAD_'+key+'_'+byId[FOCUS].name.replace(/\s+/g,'-'));o.classList.add('on');}
document.getElementById('ovl-close').addEventListener('click',()=>document.getElementById('ovl').classList.remove('on'));
document.getElementById('ovl').addEventListener('click',e=>{if(e.target.id==='ovl')e.currentTarget.classList.remove('on');});
function renderChips(){document.getElementById('chips').innerHTML=sel().map(id=>{const r=byId[id],foc=id===FOCUS;
  return '<span class="chip"><span class="chip-key" style="background:'+colorOf(id)+'"></span><span class="chip-name">'+r.name+'</span>'+(foc?'':'<span class="x" data-rm="'+id+'">×</span>')+'</span>';}).join('');
  document.querySelectorAll('#chips .x').forEach(x=>x.addEventListener('click',()=>{COMPS=COMPS.filter(c=>c!==x.dataset.rm);render();}));}
function renderAdd(){const s=sel();document.getElementById('add').innerHTML='<option value="">+ add district…</option>'+ROWS.filter(r=>!s.includes(r.sad_id)).sort((a,b)=>a.name.localeCompare(b.name)).map(r=>'<option value="'+r.sad_id+'">'+r.name+' · '+r.city+'</option>').join('');}
function initControls(){
  const fsel=document.getElementById('focus');
  fsel.innerHTML='';
  ROWS.slice().sort((a,b)=>a.name.localeCompare(b.name)).forEach(r=>{const o=document.createElement('option');o.value=r.sad_id;o.textContent=r.name+' / '+r.city;fsel.appendChild(o);});
  fsel.value=FOCUS;
  fsel.onchange=()=>{FOCUS=fsel.value;COMPS=[];render();};
  document.getElementById('add').onchange=e=>{if(e.target.value&&COMPS.length<4){COMPS.push(e.target.value);render();}};
}


// ─── Analogue entry: embedding-based composite lenses (current / trajectory / target) ───
let AE_FN=[], AE_VEC={}, AE_METRIC='cosine';
const AE_LENSES=[
  { key:'current', title:'Current condition', q:'Places like the site now',
    match:f=> f.indexOf('conn_')===0 || f.indexOf('morph_')===0 ||
      ['demo_pop_density','demo_median_age','demo_pct_owner_occupied',
       'econ_median_income','econ_median_home_value','econ_median_gross_rent'].indexOf(f)>=0,
    typFilter:false,
    note:'24 measures: connectivity, built form, demographics & economics (present levels). Z-scored embedding; no typology filter.' },
  { key:'trajectory', title:'Trajectory', q:'Similar change over time',
    match:f=> f.slice(-6)==='_trend', typFilter:false,
    note:'4 measures: income, home-value, population & ownership trend (2013–2023). Narrow lens — high % is easier here; means similar rate/direction of change.' },
  { key:'target', title:'Target character', q:'References for the intended character',
    match:f=> f.indexOf('morph_')===0 || f.indexOf('progocc_')===0 || f.indexOf('anchor_')===0,
    typFilter:true,
    note:'21 measures: built form, NSI occupancy program, anchor. Filtered to the study district’s typology. Anchor is zero for many districts.' },
];
const AE_METHOD={
  current:{title:'Current condition — the site as it exists',
    src:'Module 8 embedding · 24 z-scored features',
    body:'Ranks districts by similarity to the study district across connectivity, built form, and present-day demographics and economics. Each feature is standardised across the corpus so none dominates by scale. No typology filter — it finds places like the site as it is today, whatever their classification.',
    cav:'Drawn/synthetic and data-incomplete districts are excluded. Scores span all 24 measures, so they are lower and more spread than a narrow lens.'},
  trajectory:{title:'Trajectory — the journey, not the snapshot',
    src:'Module 8 · 4 trend features · 11-year ACS (2013–2023)',
    body:'Ranks districts by similarity in how they changed over 2013–2023: the slope of median income, home value, population, and ownership. Profile similarity means a similar pattern of change; magnitude similarity means a similar amount of change.',
    cav:'Only 4 measures — a narrow, specific signal; high percentages should not be read as broader confidence. US districts only.'},
  target:{title:'Target character — the form you are aiming for',
    src:'Module 8 · 21 features (form + occupancy program + anchor)',
    body:'Filters the corpus to the study district’s typology, then ranks those by similarity in built form, NSI occupancy program, and anchor geometry — the formal and programmatic references for the intended character.',
    cav:'Anchor geometry is zero for many districts, so it contributes little. Typology labels come from the corpus classification.'},
};
function _aeSub(vec,cols){ return cols.map(i=>vec[i]); }
function _aeCos(a,b){ let d=0,na=0,nb=0; for(let i=0;i<a.length;i++){d+=a[i]*b[i];na+=a[i]*a[i];nb+=b[i]*b[i];}
  return (na===0||nb===0)?0:d/Math.sqrt(na*nb); }
function _aeEuc(a,b){ let s=0; for(let i=0;i<a.length;i++){const d=a[i]-b[i];s+=d*d;} return Math.sqrt(s); }
function _aeDrawn(id){ return /drawn/i.test(id); }
function _aeReal(id,cols){ const raw=(byId[id]&&byId[id]._normraw)||null; return true; } // vec presence checked at load
function aeAnalogues(lens,k){
  const cols=lens._cols, q=AE_VEC[FOCUS]; if(!q) return [];
  const qv=_aeSub(q,cols); const ftyp=(byId[FOCUS]||{}).typology; const out=[];
  for(const id in AE_VEC){
    if(id===FOCUS || _aeDrawn(id)) continue;
    if(lens.typFilter && ftyp && (byId[id]||{}).typology!==ftyp) continue;
    const sv=_aeSub(AE_VEC[id],cols);
    const val = AE_METRIC==='cosine' ? _aeCos(qv,sv) : _aeEuc(qv,sv);
    out.push({id,val});
  }
  out.sort((a,b)=> AE_METRIC==='cosine' ? b.val-a.val : a.val-b.val);
  return out.slice(0,k);
}
function renderAnaEntry(){
  const host=document.getElementById('ae-cols'); if(!host) return;
  if(!AE_FN.length){ host.innerHTML='<div class="ae-note">Embedding not available in this manifest.</div>'; return; }
  host.innerHTML='';
  for(const lens of AE_LENSES){
    const res=aeAnalogues(lens,6);
    const col=document.createElement('div'); col.className='ae-lens';
    const tl = lens.typFilter && byId[FOCUS] ? ' · '+((byId[FOCUS].typology)||'') : '';
    let rows='';
    if(!res.length){ rows='<div class="ae-note">No analogues for this lens.</div>'; }
    else rows='<ul class="ae-list">'+res.map((r,i)=>{
      const picked = COMPS.indexOf(r.id)>=0;
      let sim = AE_METRIC==='cosine' ? Math.round(Math.max(0,r.val)*100)+'%' : r.val.toFixed(2);
      const nm=((byId[r.id]||{}).name||r.id).replace(/^\d+_/,'').replace(/_/g,' ');
      const tp=(byId[r.id]||{}).typology||'';
      return '<li class="'+(picked?'picked':'')+'" data-add="'+r.id+'">'+
        '<span class="ae-rk">'+(i+1)+'</span><span class="ae-sim">'+sim+'</span>'+
        '<span class="ae-nm">'+nm+'</span><span class="ae-tp">'+tp+'</span>'+
        '<span class="ae-plus">'+(picked?'\u2212':'+')+'</span></li>';
    }).join('')+'</ul>';
    col.innerHTML='<div class="ae-lh"><div><div class="ae-lt">'+lens.title+tl+'</div>'+
      '<div class="ae-lq">'+lens.q+'</div></div>'+
      '<span class="ae-lm" data-aem="'+lens.key+'">methods</span></div>'+
      rows+'<div class="ae-note">'+lens.note+'</div>';
    host.appendChild(col);
  }
  host.querySelectorAll('[data-add]').forEach(li=>li.addEventListener('click',()=>aeToggle(li.getAttribute('data-add'))));
  host.querySelectorAll('[data-aem]').forEach(el=>el.addEventListener('click',()=>aeMethod(el.getAttribute('data-aem'))));
}
function aeToggle(id){
  if(id===FOCUS) return;
  const i=COMPS.indexOf(id);
  if(i>=0){ COMPS.splice(i,1); }
  else { if(COMPS.length>=4) COMPS.shift(); COMPS.push(id); }
  render();
}
function aeMethod(key){
  const m=AE_METHOD[key]; if(!m) return;
  const c=document.getElementById('ae-mcard');
  c.innerHTML='<button class="x">×</button><h3>'+m.title+'</h3><div class="src">'+m.src+'</div>'+
    '<p>'+m.body+'</p><p class="cav">'+m.cav+'</p>';
  c.querySelector('.x').addEventListener('click',aeCloseMethod);
  c.classList.add('show'); document.getElementById('ae-mback').classList.add('show');
}
function aeCloseMethod(){ document.getElementById('ae-mcard').classList.remove('show');
  document.getElementById('ae-mback').classList.remove('show'); }
function aeInit(m){
  AE_FN=(m.embedding&&m.embedding.feature_names)||[];
  AE_VEC={};
  (m.sads||[]).forEach(s=>{ const nz=s.features&&s.features.normalized;
    if(nz && !/drawn/i.test(s.sad_id)) AE_VEC[s.sad_id]=AE_FN.map(n=>(nz[n]!=null?nz[n]:0)); });
  AE_LENSES.forEach(L=>{ L._cols=AE_FN.map((f,i)=>[f,i]).filter(x=>L.match(x[0])).map(x=>x[1]); });
  const mb=document.getElementById('ae-mback'); if(mb) mb.addEventListener('click',aeCloseMethod);
  document.querySelectorAll('#ae-metric button').forEach(b=>b.addEventListener('click',()=>{
    AE_METRIC=b.getAttribute('data-m');
    document.querySelectorAll('#ae-metric button').forEach(x=>x.classList.toggle('on',x===b));
    renderAnaEntry(); }));
}

async function boot(){
  try{
    const m=await fetch('compare_manifest.json').then(r=>{if(!r.ok)throw new Error('compare_manifest.json '+r.status);return r.json();});
    const t=manifestToStudio(m);
    ROWS=t.rows; DIST=t.dist; byId={}; ROWS.forEach(r=>byId[r.sad_id]=r);
  aeInit(m);
    FOCUS=(ROWS.find(r=>/Detroit/i.test(r.sad_id))||ROWS[0]).sad_id;
    COMPS=[];
    AXSTAT=AX.map(([,f])=>ROWS.map(r=>+f(r)).filter(isFinite).sort((a,b)=>a-b));
    initControls();
    render();
  }catch(e){
    const sheet=document.getElementById('sheet');
    if(sheet)sheet.innerHTML='<div style="padding:40px;color:#8a8a8a">Could not load compare_manifest.json - '+e.message+'</div>';
  }
}
boot();
