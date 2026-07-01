/* SAD match-server host shim. The Compare tab must send match-server calls to
   the match server on :8000, not the static page server on :5500 (which returns
   501 for POST). This rewrites ONLY match-server endpoints; static asset fetches
   are left untouched. Additive and reversible. */
(function(){
  if (window.__sadMatchHostShim) return;
  window.__sadMatchHostShim = true;
  var MATCH = 'http://localhost:8000';
  var EP = /^\/(analyze(_[a-z]+)?|extract|health)(\/|\?|$)/;
  var _fetch = window.fetch.bind(window);
  function reroute(u){
    if (typeof u !== 'string') return null;
    if (EP.test(u)) return MATCH + u;
    if (u.indexOf(location.origin) === 0){
      var p = u.slice(location.origin.length);
      if (EP.test(p)) return MATCH + p;
    }
    return null;
  }
  window.fetch = function(input, init){
    try {
      var url = (typeof input === 'string') ? input : (input && input.url) || '';
      var abs = reroute(url);
      if (abs){
        input = (typeof input === 'string') ? abs : new Request(abs, input);
      }
    } catch (e) { /* fall through to original fetch */ }
    return _fetch(input, init);
  };
})();

let ROWS=[],DIST={};
let byId={};
const COLORS=['#ff5a45','#2f8f80','#5688c4','#d2a23f','#8f78c9'];
const AMEN_C={retail_food_entertainment:'#e2674f',office:'#5688c4',sport:'#d2a23f',parking:'#968d80',hotel:'#c98a3a',residential:'#5fae7e',open_space:'#7cb342',institutional:'#7d7299',other:'#d6cebf'};
const METRICS=[['income','Median income','usd',r=>r.cen.income],['pop','Population','int',r=>r.cen.pop],['age','Median age','age',r=>r.cen.age],
  ['renter','Renter-occupied','pct',r=>r.cen.renter],['bach',"Bachelor's or higher",'pct',r=>r.cen.bach],['pois','POIs','int',r=>r.pois],
  ['transit','Transit stations','int',r=>r.transit]];
const MGET={}; METRICS.forEach(m=>MGET[m[0]]=m);
const esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const fmtV=(v,k)=>{ if(v==null||isNaN(+v))return '—'; const n=+v;
  if(k==='usd')return n>=1000?'$'+(n/1000).toFixed(0)+'k':'$'+Math.round(n);
  if(k==='pct')return Math.round(n)+'%'; if(k==='age')return n.toFixed(1);
  if(k==='int')return n>=1000?(n/1000).toFixed(n>=10000?0:1)+'k':String(Math.round(n)); return String(Math.round(n)); };
const AX=[['Income',r=>r.cen.income],['Renter',r=>r.cen.renter],['Educated',r=>r.cen.bach],['Pop',r=>r.cen.pop],
  ['POIs',r=>r.pois],['Transit',r=>r.transit]];
let AXSTAT=[];
const pctRank=(v,arr)=>{if(v==null||!isFinite(v)||!arr.length)return null;let c=0;for(const x of arr)if(x<=v)c++;return c/arr.length;};
const axPct=(r,i)=>pctRank(+AX[i][1](r),AXSTAT[i]);

let FOCUS=null;
let COMPS=[];
let SCAT={x:'pois',y:'income'}, RANKM='income';
let NATMODE='table';
function topSimilar(id,k){const row=DIST[id];if(!row)return [];return Object.entries(row).filter(([o])=>o!==id&&byId[o]&&!/rawn/i.test(o)).map(([o,d])=>({o,d:+d})).filter(x=>isFinite(x.d)).sort((a,b)=>a.d-b.d).slice(0,k).map(x=>x.o);}

function vecOf(r){const am=r.amenity||{};const cats=Object.keys(AMEN_C);let tot=0;for(const c of cats)tot+=(+am[c]||0);if(!tot)return null;return cats.map(c=>(+am[c]||0)/tot);}
function nearestByFeatures(id,k){const fr=byId[id];if(!fr)return [];const fv=vecOf(fr);if(!fv)return [];return ROWS.map(r=>{if(r.sad_id===id||/rawn/i.test(r.sad_id))return null;const v=vecOf(r);if(!v)return null;let d=0;for(let i=0;i<fv.length;i++){const x=fv[i]-v[i];d+=x*x;}return {o:r.sad_id,d:Math.sqrt(d)};}).filter(Boolean).sort((a,b)=>a.d-b.d).slice(0,k).map(x=>x.o);}
function autoComps(id,k){const t=topSimilar(id,k);return t.length?t:nearestByFeatures(id,k);}

function sel(){return [FOCUS,...COMPS].filter((v,i,a)=>byId[v]&&a.indexOf(v)===i);}
function vis(){const _s=sel();return ROWS.filter(r=>!/rawn/i.test(r.sad_id)||_s.includes(r.sad_id));}
function colorOf(id){const i=sel().indexOf(id);return i<0?'#bbb':COLORS[i%COLORS.length];}

function svg(w,h,title,sub,inner){
  let leg='';const s=sel();let lx=20,ly=50;
  s.forEach(id=>{const nm=byId[id].name;if(lx>w-130){lx=20;ly+=18;}leg+='<circle cx="'+lx+'" cy="'+ly+'" r="4.5" fill="'+colorOf(id)+'"/><text x="'+(lx+9)+'" y="'+(ly+4)+'" class="leg">'+esc(nm)+'</text>';lx+=16+nm.length*6.0+14;});
  return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 '+w+' '+h+'" width="'+w+'" height="'+h+'">'
    +'<style>text{font-family:Arial,Helvetica,sans-serif} .ttl{font-size:16px;font-weight:700;fill:#1b1813}'
    +'.sub{font-family:"JetBrains Mono",monospace;font-size:11.5px;letter-spacing:.03em;fill:#968d80} .leg{font-size:11px;fill:#5a534a}'
    +'.lab{font-size:11px;fill:#5a534a} .mono{font-family:"JetBrains Mono",monospace;font-size:10.5px;fill:#5a534a}'
    +'.axlab{font-family:"JetBrains Mono",monospace;font-size:9px;fill:#968d80} .end{font-family:"JetBrains Mono",monospace;font-size:9px;fill:#968d80}'
    +'.tick{stroke:#968d80;stroke-width:1.3;opacity:.36} .dot{fill:#c8bfb1} .qband{fill:#1b1813;opacity:.05} .grid{stroke:#e8e2d8;stroke-width:1} .median{stroke:#968d80;stroke-width:1;stroke-dasharray:2 2;opacity:.65} .axis{stroke:#e6e0d6;stroke-width:1}'
    +'.ring{fill:none;stroke:#e6e0d6;stroke-width:1} .spoke{stroke:#e6e0d6;stroke-width:1}</style>'
    +'<rect width="'+w+'" height="'+h+'" fill="#faf8f4"/>'
    +'<text x="20" y="24" class="ttl">'+esc(title)+'</text>'
    +(sub?'<text x="'+(w-20)+'" y="24" text-anchor="end" class="sub">'+esc(sub)+'</text>':'')
    +leg+inner+'</svg>';
}

function cCensus(){
  const W=800,pad=20;let inner='',y=78;
  const strips=[['Median income','usd',r=>r.cen.income],['Population','int',r=>r.cen.pop],['Median age','age',r=>r.cen.age]];
  strips.forEach(m=>{const getter=m[2],kind=m[1],rw=W-2*pad,ay=y+34,x=t=>pad+t*rw;
    const corpus=vis().map(getter).filter(v=>v!=null&&isFinite(+v)).map(Number).sort((a,b)=>a-b);
    inner+='<text x="'+pad+'" y="'+(y+14)+'" class="lab" style="font-size:12px;fill:#1b1813">'+m[0]+'</text>';
    
    inner+='<text x="'+(W-pad)+'" y="'+(y+14)+'" text-anchor="end" class="mono" style="font-size:12px;fill:'+colorOf(FOCUS)+'">'+fmtV(getter(byId[FOCUS]),kind)+'</text>';
    inner+='<line class="axis" x1="'+pad+'" y1="'+ay+'" x2="'+(W-pad)+'" y2="'+ay+'"/>';
    corpus.forEach((v,idx)=>{const t=(idx+0.5)/corpus.length;inner+='<circle class="dot" cx="'+x(t).toFixed(1)+'" cy="'+ay+'" r="2"/>';});
    inner+='<line class="median" x1="'+x(.5).toFixed(1)+'" y1="'+(ay-11)+'" x2="'+x(.5).toFixed(1)+'" y2="'+(ay+11)+'"/>';
    sel().forEach(id=>{const v=getter(byId[id]);if(v==null||!isFinite(+v))return;const t=pctRank(+v,corpus),foc=id===FOCUS;
      inner+='<circle cx="'+x(t).toFixed(1)+'" cy="'+ay+'" r="'+(foc?6:4.5)+'" fill="'+colorOf(id)+'" stroke="#faf8f4" stroke-width="'+(foc?2:1.6)+'"/>';});
    inner+='<text x="'+pad+'" y="'+(ay+19)+'" class="end">'+fmtV(corpus[0],kind)+'</text><text x="'+(W-pad)+'" y="'+(ay+19)+'" text-anchor="end" class="end">'+fmtV(corpus[corpus.length-1],kind)+'</text>';
    y+=66;});
  y+=8;inner+='<line class="axis" x1="'+pad+'" y1="'+y+'" x2="'+(W-pad)+'" y2="'+y+'"/>';
  inner+='<text x="'+pad+'" y="'+(y+22)+'" class="sub" style="font-size:9.5px">SHARE OF HOUSEHOLDS</text>';y+=42;
  const arc=(cx,cy,r,a0,a1)=>{const A0=a0*Math.PI/180,A1=a1*Math.PI/180,x0=cx+r*Math.cos(A0),y0=cy+r*Math.sin(A0),x1=cx+r*Math.cos(A1),y1=cy+r*Math.sin(A1),lg=(a1-a0)>180?1:0;return 'M'+x0.toFixed(1)+' '+y0.toFixed(1)+' A'+r+' '+r+' 0 '+lg+' 1 '+x1.toFixed(1)+' '+y1.toFixed(1);};
  const donutRow=(label,getter)=>{const s=sel(),r=30,sw=8,slotW=(W-2*pad)/s.length;
    inner+='<text x="'+pad+'" y="'+y+'" class="lab" style="font-size:11px;fill:#1b1813">'+label+'</text>';
    s.forEach((id,i)=>{const cx=pad+slotW*i+slotW/2,cy=y+50,v=getter(byId[id]),has=v!=null&&isFinite(+v),pct=has?Math.max(0,Math.min(100,+v)):0,c=colorOf(id);
      inner+='<circle cx="'+cx+'" cy="'+cy+'" r="'+r+'" fill="none" stroke="#ece7de" stroke-width="'+sw+'"/>';
      if(has&&pct>0){if(pct>=99.9){inner+='<circle cx="'+cx+'" cy="'+cy+'" r="'+r+'" fill="none" stroke="'+c+'" stroke-width="'+sw+'"/>';}else{inner+='<path d="'+arc(cx,cy,r,-90,-90+pct/100*360)+'" fill="none" stroke="'+c+'" stroke-width="'+sw+'" stroke-linecap="round"/>';}}
      inner+='<text x="'+cx+'" y="'+(cy+5)+'" text-anchor="middle" class="mono" style="font-size:14px;fill:#1b1813">'+(has?Math.round(pct)+'%':'\u2014')+'</text>';
      inner+='<text x="'+cx+'" y="'+(cy+r+18)+'" text-anchor="middle" class="end">'+esc(byId[id].name)+'</text>';});
    y+=128;};
  donutRow('Renter-occupied',r=>r.cen.renter);
  donutRow("Bachelor's degree or higher",r=>r.cen.bach);
  return {title:'Who lives here',svg:svg(W,y+4,'Who lives here','the people who live here, ranked against all 37',inner)};
}
function cRose(){
  const W=460,H=420,N=AX.length,cx=230,cy=225,R=128,LR=1.15;
  const ang=p=>(-90+p*360/N)*Math.PI/180,pt=(p,v)=>[cx+R*v*Math.cos(ang(p)),cy+R*v*Math.sin(ang(p))];
  let inner='';[.25,.5,.75,1].forEach(f=>{inner+='<polygon class="ring" points="'+AX.map((_,p)=>pt(p,f).map(n=>n.toFixed(1)).join(',')).join(' ')+'"/>';});
  AX.forEach(([label],p)=>{const[x,y]=pt(p,1),[lx,ly]=pt(p,LR);inner+='<line class="spoke" x1="'+cx+'" y1="'+cy+'" x2="'+x.toFixed(1)+'" y2="'+y.toFixed(1)+'"/><text class="axlab" x="'+lx.toFixed(1)+'" y="'+ly.toFixed(1)+'" text-anchor="middle">'+label+'</text>';});
  sel().forEach(id=>{const c=colorOf(id),r=byId[id];const pts=AX.map((_,i)=>{const t=axPct(r,i);return pt(i,t==null?0:t);});
    inner+='<polygon points="'+pts.map(q=>q.map(n=>n.toFixed(1)).join(',')).join(' ')+'" fill="'+c+'" fill-opacity="0.08" stroke="'+c+'" stroke-width="2" stroke-linejoin="round"/>';
    pts.forEach(q=>inner+='<circle cx="'+q[0].toFixed(1)+'" cy="'+q[1].toFixed(1)+'" r="2.2" fill="'+c+'"/>');});
  return {title:'Street and block character',svg:svg(W,H,'Street and block character','how built-up and connected this place is, vs all 37',inner),center:true};
}
function cSim(){
  const row=DIST[FOCUS]||{};const ds=Object.values(row).map(Number).filter(isFinite);
  let basis='morphology + demographics';
  let items=COMPS.filter(id=>row[id]!=null).map(id=>({id,d:+row[id]})).sort((a,b)=>a.d-b.d);
  let maxD=Math.max(4,ds.length?Math.max.apply(null,ds):4);
  if(!items.length){const fv=vecOf(byId[FOCUS]||{});if(fv){basis='program (form not available for a drawn district)';items=COMPS.map(id=>{const r2=byId[id];if(!r2)return null;const v=vecOf(r2);if(!v)return null;let d=0;for(let i=0;i<fv.length;i++){const x=fv[i]-v[i];d+=x*x;}return {id,d:Math.sqrt(d)};}).filter(Boolean).sort((a,b)=>a.d-b.d);maxD=items.length?Math.max(0.001,items[items.length-1].d)*1.12:4;}}
  const _dmin=items.length?Math.max(1e-9,items[0].d):1;const W=800,top=74,rowH=40,H=top+Math.max(1,items.length)*rowH+10,lx=210,bw=W-lx-90;let inner='';
  if(!items.length)inner+='<text x="20" y="'+(top+18)+'" class="lab">Add districts to compare against the drawn area.</text>';
  items.forEach((x,i)=>{const y=top+i*rowH,sim=Math.round(100*_dmin/Math.max(_dmin,x.d)),r=byId[x.id],c=colorOf(x.id);
    inner+='<circle cx="26" cy="'+(y+13)+'" r="5" fill="'+c+'"/><text x="40" y="'+(y+17)+'" class="lab" style="font-size:12.5px;fill:#1b1813">'+esc(r.name)+'</text>';
    inner+='<text x="40" y="'+(y+31)+'" class="end">'+esc(r.city)+'</text>';
    inner+='<rect x="'+lx+'" y="'+(y+9)+'" width="'+bw+'" height="9" rx="4.5" fill="#ece7de"/>';
    inner+='<rect x="'+lx+'" y="'+(y+9)+'" width="'+(bw*Math.max(.04,sim/100)).toFixed(1)+'" height="9" rx="4.5" fill="'+c+'"/>';
    inner+='<text x="'+(W-20)+'" y="'+(y+17)+'" text-anchor="end" class="mono" style="font-size:13px;fill:#1b1813">'+sim+'%</text>';});
  return {title:'Closeness to '+byId[FOCUS].name,svg:svg(W,H,'Closeness to '+byId[FOCUS].name,'100% = the closest match · '+basis,inner)};
}
function cProgram(){
  const cats=Object.keys(AMEN_C),s=sel();const W=800,pad=20,R=52,sw=20,top=98;const anyReal=s.some(id=>{const x=byId[id]&&byId[id].program_real;return x&&Object.keys(x).length;});
  const slotW=(W-2*pad)/s.length;
  const seg=(cx,cy,a0,a1,color)=>{const A0=a0*Math.PI/180,A1=a1*Math.PI/180,x0=cx+R*Math.cos(A0),y0=cy+R*Math.sin(A0),x1=cx+R*Math.cos(A1),y1=cy+R*Math.sin(A1),lg=(a1-a0)>180?1:0;return '<path d="M'+x0.toFixed(1)+' '+y0.toFixed(1)+' A'+R+' '+R+' 0 '+lg+' 1 '+x1.toFixed(1)+' '+y1.toFixed(1)+'" fill="none" stroke="'+color+'" stroke-width="'+sw+'"/>';};
  let inner='';
  s.forEach((id,i)=>{const cx=pad+slotW*i+slotW/2,cy=top+R+4,r=byId[id],_real=r.program_real,_isReal=!!(_real&&Object.keys(_real).length),am=_isReal?_real:(r.amenity||{}),tot=Object.values(am).reduce((a,b)=>a+(+b||0),0)||1;
    inner+='<circle cx="'+cx+'" cy="'+cy+'" r="'+R+'" fill="none" stroke="#ece7de" stroke-width="'+sw+'"/>';
    let a0=-90;cats.forEach(c=>{const v=+am[c]||0;if(v<=0)return;const frac=v/tot,a1=a0+frac*360;
      if(frac>=0.999)inner+='<circle cx="'+cx+'" cy="'+cy+'" r="'+R+'" fill="none" stroke="'+AMEN_C[c]+'" stroke-width="'+sw+'"/>';
      else inner+=seg(cx,cy,a0,a1,AMEN_C[c]);a0=a1;});
    inner+='<text x="'+cx+'" y="'+(cy-1)+'" text-anchor="middle" class="lab" style="font-size:17px;fill:#1b1813;font-weight:700">'+(_isReal?'':(r.pois!=null?r.pois:tot))+'</text>';
    inner+='<text x="'+cx+'" y="'+(cy+13)+'" text-anchor="middle" class="axlab">'+(_isReal?'BY AREA':'POINTS')+'</text>';
    inner+='<text x="'+cx+'" y="'+(cy+R+24)+'" text-anchor="middle" class="lab" style="font-size:11.5px;fill:#1b1813;font-weight:600">'+esc(r.name)+'</text>';});
  let ky=top+2*R+46,kx=pad;cats.forEach(c=>{const lbl=c.replace(/_/g,' ');inner+='<rect x="'+kx+'" y="'+ky+'" width="10" height="10" rx="2" fill="'+AMEN_C[c]+'"/><text x="'+(kx+15)+'" y="'+(ky+9)+'" class="axlab">'+lbl+'</text>';kx+=22+lbl.length*5.6+18;if(kx>W-130){kx=pad;ky+=20;}});
  return {title:'Mix of uses',svg:svg(W,ky+24,'Mix of uses',(anyReal?'building floor area where available \u00b7 NSI structure occupancy':'business counts \u00b7 blind to housing, inflates retail'),inner)};
}
function cScatter(){
  const W=800,H=500,m={l:64,r:28,t:78,b:52},gx=MGET[SCAT.x],gy=MGET[SCAT.y];
  const xs=vis().map(r=>+gx[3](r)).filter(isFinite),ys=vis().map(r=>+gy[3](r)).filter(isFinite);
  const xmin=Math.min.apply(null,xs),xmax=Math.max.apply(null,xs),ymin=Math.min.apply(null,ys),ymax=Math.max.apply(null,ys);
  const px=v=>m.l+((v-xmin)/((xmax-xmin)||1))*(W-m.l-m.r),py=v=>H-m.b-((v-ymin)/((ymax-ymin)||1))*(H-m.t-m.b);
  let inner='<line class="axis" x1="'+m.l+'" y1="'+(H-m.b)+'" x2="'+(W-m.r)+'" y2="'+(H-m.b)+'"/><line class="axis" x1="'+m.l+'" y1="'+m.t+'" x2="'+m.l+'" y2="'+(H-m.b)+'"/>';
  inner+='<text x="'+((W+m.l-m.r)/2)+'" y="'+(H-14)+'" text-anchor="middle" class="axlab">'+gx[1]+'</text>';
  inner+='<text transform="translate(18,'+((H-m.b+m.t)/2)+') rotate(-90)" text-anchor="middle" class="axlab">'+gy[1]+'</text>';
  {const _xt=[],_yt=[];for(let i=0;i<=4;i++){_xt.push(xmin+(xmax-xmin)*i/4);_yt.push(ymin+(ymax-ymin)*i/4);}_yt.forEach(t=>{const Y=py(t).toFixed(1);inner+='<line class="grid" x1="'+m.l+'" y1="'+Y+'" x2="'+(W-m.r)+'" y2="'+Y+'"/><text x="'+(m.l-7)+'" y="'+(+Y+3)+'" text-anchor="end" class="mono" style="font-size:9px;fill:#968d80">'+fmtV(t,gy[2])+'</text>';});_xt.forEach(t=>{const X=px(t).toFixed(1);inner+='<line class="grid" x1="'+X+'" y1="'+m.t+'" x2="'+X+'" y2="'+(H-m.b)+'"/><text x="'+X+'" y="'+(H-m.b+17)+'" text-anchor="middle" class="mono" style="font-size:9px;fill:#968d80">'+fmtV(t,gx[2])+'</text>';});}
  const ss=sel();
  vis().forEach(r=>{const xv=+gx[3](r),yv=+gy[3](r);if(!isFinite(xv)||!isFinite(yv)||ss.includes(r.sad_id))return;inner+='<circle cx="'+px(xv).toFixed(1)+'" cy="'+py(yv).toFixed(1)+'" r="3.5" fill="#cdc4b6"/>';});
  ss.forEach(id=>{const r=byId[id],xv=+gx[3](r),yv=+gy[3](r);if(!isFinite(xv)||!isFinite(yv))return;const foc=id===FOCUS,c=colorOf(id);
    inner+='<circle cx="'+px(xv).toFixed(1)+'" cy="'+py(yv).toFixed(1)+'" r="'+(foc?7.5:6)+'" fill="'+c+'" stroke="#faf8f4" stroke-width="2"/>';
    inner+='<text x="'+(px(xv)+10).toFixed(1)+'" y="'+(py(yv)+3.5).toFixed(1)+'" class="lab" style="font-size:10.5px;fill:#1b1813">'+esc(r.name)+'</text>';});
  return {title:gy[1]+' vs '+gx[1],svg:svg(W,H,gy[1]+' vs '+gx[1],'all 37 districts · the ones you picked are highlighted',inner)};
}
function cRank(){
  const g=MGET[RANKM];const W=800,top=74,rowH=21;
  const rows=vis().map(r=>({r,v:+g[3](r)})).filter(x=>isFinite(x.v)).sort((a,b)=>b.v-a.v);
  const H=top+rows.length*rowH+14,pad=210,bw=W-pad-86,mx=Math.max.apply(null,rows.map(x=>x.v))||1;const ss=sel();let inner='';
  rows.forEach((x,i)=>{const y=top+i*rowH,inSel=ss.includes(x.r.sad_id),c=inSel?colorOf(x.r.sad_id):'#d6cebf';
    inner+='<text x="20" y="'+(y+11)+'" class="'+(inSel?'lab':'axlab')+'" style="'+(inSel?'fill:#1b1813;font-weight:600;font-size:11.5px':'')+'">'+esc(x.r.name)+'</text>';
    const lx=pad+bw*x.v/mx;
    inner+='<line x1="'+pad+'" y1="'+(y+8)+'" x2="'+lx.toFixed(1)+'" y2="'+(y+8)+'" stroke="'+c+'" stroke-width="'+(inSel?2.6:1)+'" opacity="'+(inSel?1:.5)+'"/><circle cx="'+lx.toFixed(1)+'" cy="'+(y+8)+'" r="'+(inSel?5:2.8)+'" fill="'+c+'"/>';
    inner+='<text x="'+(W-20)+'" y="'+(y+12)+'" text-anchor="end" class="mono" style="'+(inSel?'fill:#1b1813':'')+'">'+fmtV(x.v,g[2])+'</text>';});
  return {title:'Ranked · '+g[1],svg:svg(W,H,'Ranked · '+g[1],'every district, ranked by this measure',inner)};
}
function cBench(){
  const ss=sel(),W=800,pad=20,top=86,rowH=60,labW=118;
  const mets=[['Population','int',r=>r.cen.pop],['Median income','usd',r=>r.cen.income],['POIs','int',r=>r.pois],
    ['Transit','int',r=>r.transit]];
  const x0=pad+labW,x1=W-pad-18,tw=x1-x0;
  const quart=(a,p)=>{const i=(a.length-1)*p,lo=Math.floor(i),hi=Math.ceil(i);return a[lo]+(a[hi]-a[lo])*(i-lo);};
  let inner='',y=top;
  mets.forEach(m=>{const getter=m[2],kind=m[1];
    const corpus=vis().map(getter).filter(v=>v!=null&&isFinite(+v)).map(Number).sort((a,b)=>a-b);
    const lo=corpus[0],hi=corpus[corpus.length-1],rng=(hi-lo)||1,px=v=>x0+((+v-lo)/rng)*tw;
    const q1=quart(corpus,.25),q3=quart(corpus,.75),md=quart(corpus,.5);
    inner+='<text x="'+pad+'" y="'+(y+4)+'" class="lab" style="font-size:11.5px;fill:#1b1813">'+m[0]+'</text>';
    inner+='<rect x="'+px(q1).toFixed(1)+'" y="'+(y-7)+'" width="'+Math.max(0,px(q3)-px(q1)).toFixed(1)+'" height="14" class="qband"/>';
    inner+='<line class="axis" x1="'+x0+'" y1="'+y+'" x2="'+x1+'" y2="'+y+'"/>';
    corpus.forEach(v=>inner+='<circle class="dot" cx="'+px(v).toFixed(1)+'" cy="'+y+'" r="2"/>');
    inner+='<line class="median" x1="'+px(md).toFixed(1)+'" y1="'+(y-10)+'" x2="'+px(md).toFixed(1)+'" y2="'+(y+10)+'"/>';
    inner+='<text x="'+x0+'" y="'+(y+21)+'" class="end">'+fmtV(lo,kind)+'</text><text x="'+x1+'" y="'+(y+21)+'" text-anchor="end" class="end">'+fmtV(hi,kind)+'</text>';
    ss.forEach(id=>{const v=getter(byId[id]);if(v==null||!isFinite(+v))return;const foc=id===FOCUS,c=colorOf(id),cxp=px(v);
      inner+='<circle cx="'+cxp.toFixed(1)+'" cy="'+y+'" r="'+(foc?6:4.8)+'" fill="'+c+'" stroke="#faf8f4" stroke-width="'+(foc?2:1.6)+'"/>';});
    y+=rowH;});
  return {title:'How it measures up',svg:svg(W,y+4,'How it measures up','each measure against the full range of all 37 · shaded = middle half · dashed = typical',inner)};
}
function cTable(){
  const ss=sel(),W=820,pad=16,top=88,rh=32,headH=30;
  const cols=[['District',176],['Typology',120],['Pop',72],['Income',82],['POIs',58],['Transit',64],['Walk',62],['Nodes',62],['Sim',58]];
  let xs=[pad];cols.forEach(c=>xs.push(xs[xs.length-1]+c[1]));
  const H=top+headH+ss.length*rh+14;
  const row=DIST[FOCUS]||{},dv=Object.values(row).map(Number).filter(isFinite),maxD=Math.max(4,dv.length?Math.max.apply(null,dv):4);
  let inner='<rect x="'+pad+'" y="'+top+'" width="'+(W-2*pad)+'" height="'+headH+'" fill="#e3ddd1"/>';
  cols.forEach((c,ci)=>{const tx=ci===0?xs[ci]+12:xs[ci+1]-8,anc=ci===0?'start':'end';
    inner+='<text x="'+tx+'" y="'+(top+headH/2+4)+'" text-anchor="'+anc+'" class="mono" style="font-size:9px;letter-spacing:.05em;fill:#1b1813">'+c[0].toUpperCase()+'</text>';});
  ss.forEach((id,ri)=>{const r=byId[id],y=top+headH+ri*rh,cy=y+rh/2+4;
    if(ri%2===1)inner+='<rect x="'+pad+'" y="'+y+'" width="'+(W-2*pad)+'" height="'+rh+'" fill="#f3efe8"/>';
    inner+='<circle cx="'+(xs[0]+8)+'" cy="'+(y+rh/2)+'" r="4.5" fill="'+colorOf(id)+'"/>';
    inner+='<text x="'+(xs[0]+20)+'" y="'+cy+'" class="lab" style="font-size:11.5px;fill:#1b1813;font-weight:600">'+esc(r.name)+'</text>';
    inner+='<text x="'+(xs[1]+2)+'" y="'+cy+'" class="end" style="font-size:10px">'+esc(r.typology||'\u2014')+'</text>';
    const cells=[[r.cen.pop,'int'],[r.cen.income,'usd'],[r.pois,'int'],[r.transit,'int'],[r.walk,'int'],[r.nodes,'int']];
    cells.forEach((cc,i)=>{inner+='<text x="'+(xs[3+i]-8)+'" y="'+cy+'" text-anchor="end" class="mono" style="font-size:10.5px;fill:#1b1813">'+fmtV(cc[0],cc[1])+'</text>';});
    const sim=(id===FOCUS)?'\u2014':(row[id]!=null?Math.round(100*Math.max(0,1-(+row[id])/maxD))+'%':'\u2014');
    inner+='<text x="'+(xs[9]-8)+'" y="'+cy+'" text-anchor="end" class="mono" style="font-size:10.5px;fill:'+(id===FOCUS?'#968d80':'#1b1813')+'">'+sim+'</text>';
    inner+='<line x1="'+pad+'" y1="'+(y+rh)+'" x2="'+(W-pad)+'" y2="'+(y+rh)+'" class="axis"/>';});
  return {title:'Side by side',svg:svg(W,H,'Side by side','your district and the ones you picked, measure by measure',inner)};
}
const TYPO_FIT_COLORS={'Entertainment':'#D85A30','Innovation':'#534AB7','Sports Park':'#1D9E75','Community':'#888780'};
const TYPO_ORDER=['Entertainment','Innovation','Sports Park','Community'];
let FITS={};
async function ensureFit(id){
  if(!id||FITS[id])return;
  const r=byId[id];
  if(!r||!r.boundary){FITS[id]={state:'error',msg:'no boundary on file'};return;}
  FITS[id]={state:'loading'};
  try{
    const b=await fetch('../'+r.boundary).then(x=>{if(!x.ok)throw 0;return x.json();});
    const geom=(b&&b.type==='FeatureCollection')?((b.features&&b.features[0]||{}).geometry):((b&&b.geometry)||b);
    const resp=await fetch(location.origin+'/analyze_program',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({geometry:geom})}).then(x=>x.json());
    FITS[id]=(resp&&resp.ok&&resp.typology_fit)?{state:'ok',fit:resp.typology_fit}:{state:'error',msg:'program fit unavailable'};
  }catch(e){FITS[id]={state:'error',msg:'match server not reachable'};}
  render();
}
const TYPO_DEF={'Entertainment':'Entertainment Destinations: event-spending driven, commercial mixed-use, serves visitors.','Community':'Community-Centered Districts: civic-value driven, year-round local use, serves residents.','Innovation':'Innovation / Employment Districts: jobs and knowledge driven, office and research, serves workers.','Sports Park':'Sports Tourism Districts: amateur tournament travel, fields and recreation (framework name: Sports Tourism).'};
const TYPO_GLOSS={'Entertainment':'Built for events and visitors: venues, bars, game-day crowds.','Innovation':'Jobs and research: offices, labs, daytime workers.','Sports Park':'Fields and tournaments: amateur sport, travel teams.','Community':'Everyday local life: homes, services, residents.'};
function cTypology(){
  const s=sel(),W=800;s.forEach(id=>ensureFit(id));
  const pad=20,top=84,rowH=48,labelW=156,barX=pad+labelW,barW=W-barX-pad-150,bh=24;
  let inner='',y=top;
  s.forEach(id=>{const r=byId[id],st=FITS[id];
    inner+='<text x="'+pad+'" y="'+(y+bh/2)+'" class="lab" style="font-size:12.5px;fill:#1b1813;font-weight:600">'+esc(r.name)+'</text>';
    inner+='<text x="'+pad+'" y="'+(y+bh/2+14)+'" class="axlab"'+(id===FOCUS?' style="fill:#b14a2e;font-weight:700;font-size:10px"':'')+'>'+esc(id===FOCUS?'YOUR DISTRICT':((r.city||'')+'  \u00b7 nearest match'))+'</text>';
    if(!st||st.state==='loading'){inner+='<rect x="'+barX+'" y="'+y+'" width="'+barW+'" height="'+bh+'" rx="6" fill="#f0ebe2"/><text x="'+(barX+11)+'" y="'+(y+bh-8)+'" class="axlab">computing\u2026</text>';}
    else if(st.state==='error'){inner+='<rect x="'+barX+'" y="'+y+'" width="'+barW+'" height="'+bh+'" rx="6" fill="#f7ede9"/><text x="'+(barX+11)+'" y="'+(y+bh-8)+'" class="axlab">'+esc(st.msg)+'</text>';}
    else{const pct=st.fit.percent_by_typology||{};let x=barX;
      TYPO_ORDER.forEach(t=>{const v=Math.max(0,Math.min(100,+pct[t]||0)),w=barW*v/100;if(w<=0)return;
        inner+='<rect x="'+x.toFixed(1)+'" y="'+y+'" width="'+w.toFixed(1)+'" height="'+bh+'" fill="'+TYPO_FIT_COLORS[t]+'"><title>'+esc(TYPO_DEF[t]||t)+'</title></rect>';
        if(w>30)inner+='<text x="'+(x+w/2).toFixed(1)+'" y="'+(y+bh-8)+'" text-anchor="middle" class="mono" style="fill:#fff;font-size:10.5px">'+Math.round(v)+'%</text>';
        x+=w;});
      inner+='<rect x="'+barX+'" y="'+y+'" width="'+barW+'" height="'+bh+'" rx="6" fill="none" stroke="#e6e0d6"/>';const _t=TYPO_ORDER.map(t=>[t,+pct[t]||0]).sort((a,b)=>b[1]-a[1])[0];inner+='<text x="'+(barX+barW+12)+'" y="'+(y+bh/2+4)+'" class="lab" style="font-size:12px;font-weight:600;fill:'+TYPO_FIT_COLORS[_t[0]]+'">'+esc(_t[0])+' '+Math.round(_t[1])+'%</text>';}
    y+=rowH;});
  let ky=y+12;
  inner+='<text x="'+pad+'" y="'+(ky+10)+'" class="lab" style="font-size:11px;font-weight:700;fill:#1b1813;letter-spacing:.04em">THE FOUR TYPES</text>';
  ky+=20;
  TYPO_ORDER.forEach((t,i)=>{const ry=ky+i*22;
    inner+='<rect x="'+pad+'" y="'+ry+'" width="12" height="12" rx="2" fill="'+TYPO_FIT_COLORS[t]+'"><title>'+esc(TYPO_DEF[t]||t)+'</title></rect>';
    inner+='<text x="'+(pad+20)+'" y="'+(ry+10)+'" class="lab" style="font-size:12px;font-weight:600;fill:#1b1813">'+esc(t)+'</text>';
    inner+='<text x="'+(pad+150)+'" y="'+(ry+10)+'" class="axlab" style="fill:#6b6358">'+esc(TYPO_GLOSS[t]||'')+'</text>';});
  const keyBottom=ky+TYPO_ORDER.length*22+10;
  return {title:'Typology fit',svg:svg(W,keyBottom,'What kind of place is each one','your district, then its 3 nearest \u00b7 each bar shows that place\u2019s character',inner)};
}
function cFacts(){
  const s=sel(),W=800,pad=20,labW=150,top=96,rowH=28;
  const F=[
    ['Anchor league',r=>r.report&&r.report.anchor_league],
    ['Sport',r=>r.report&&r.report.anchor_sport],
    ['Size',r=>r.report&&r.report.size_acres!=null?r.report.size_acres+' ac':null],
    ['Location',r=>r.report&&r.report.location_type],
    ['Opened',r=>r.report&&r.report.year_opened],
    ['Status',r=>r.report&&r.report.status],
    ['Owner type',r=>r.report&&r.report.owner_type],
    ['Cost / acre',r=>r.report&&r.report.cost_per_acre_raw],
    ['Total GSF',r=>r.report&&r.report.total_gsf_raw]
  ];
  const slotW=(W-pad-labW)/Math.max(1,s.length);let inner='';
  s.forEach((id,i)=>{const cx=pad+labW+slotW*i+slotW/2,r=byId[id];
    inner+='<text x=\"'+cx.toFixed(1)+'\" y=\"'+(top-4)+'\" text-anchor=\"middle\" class=\"lab\" style=\"font-size:11.5px;font-weight:600;fill:'+colorOf(id)+'\">'+esc(r.name)+'</text>';});
  inner+='<line class=\"axis\" x1=\"'+pad+'\" y1=\"'+(top+4)+'\" x2=\"'+(W-pad)+'\" y2=\"'+(top+4)+'\"/>';
  let y=top+4+rowH;
  F.forEach(f=>{inner+='<text x=\"'+pad+'\" y=\"'+(y-9)+'\" class=\"axlab\" style=\"font-size:10.5px\">'+f[0]+'</text>';
    s.forEach((id,i)=>{const cx=pad+labW+slotW*i+slotW/2,r=byId[id];let v=f[1](r);v=(v==null||v==='')?'\u2014':String(v);
      inner+='<text x=\"'+cx.toFixed(1)+'\" y=\"'+(y-9)+'\" text-anchor=\"middle\" class=\"mono\" style=\"font-size:10.5px;fill:#1b1813\">'+esc(v)+'</text>';});
    inner+='<line class=\"grid\" x1=\"'+pad+'\" y1=\"'+y+'\" x2=\"'+(W-pad)+'\" y2=\"'+y+'\"/>';y+=rowH;});
  const missing=s.filter(id=>!(byId[id]&&byId[id].report)).map(id=>byId[id].name);
  const sub=missing.length?('no report data: '+missing.join(', ')):'from the 2025 SAD Report';
  return {title:'Site facts',svg:svg(W,y+8,'Site facts',sub,inner)};
}
/* nat-modes */
function natFmt(v,k){ if(v==null||isNaN(+v))return '\u2014'; var n=+v;
  if(k==='pctf')return Math.round(n*100)+'%'; if(k==='m')return n.toFixed(1)+' m';
  if(k==='dist')return n<=0?'adj':(n<1000?Math.round(n)+' m':(n/1000).toFixed(1)+' km');
  if(k==='pm')return n.toFixed(1); return String(Math.round(n)); }
const NAT_COLS=[['Green cover','green','pctf'],['Canopy','canopy','m'],['Trees','trees','int'],
  ['Water','water','dist'],['Air PM2.5','pm25','pm']];
function natFocusLine(){
  const fr=byId[FOCUS],fn=fr&&fr.nat||{}; if(!fr)return '';
  const gc=fn.green, corpus=vis().map(x=>x.nat&&x.nat.green).filter(v=>v!=null&&isFinite(+v)).map(Number).sort((a,b)=>a-b);
  let pct=null; if(gc!=null&&corpus.length){let cnt=0;corpus.forEach(v=>{if(v<=gc)cnt++;});pct=Math.round(100*cnt/corpus.length);}
  const canTxt=(fn.canopy==null)?'thin canopy sample':'canopy '+fn.canopy.toFixed(1)+' m';
  if(gc==null)return esc(fr.name)+': no nature data yet (draw-pull pending).';
  return esc(fr.name)+': greener than '+(pct==null?'\u2014':pct+'%')+' of districts, '+canTxt+'.';
}
function cNatureTable(){
  const ss=sel(),W=800,pad=20,nameW=176,colW=(W-2*pad-nameW)/NAT_COLS.length,top=92,rh=30,headH=26;
  let inner='';
  NAT_COLS.forEach((c,ci)=>{const cx=pad+nameW+ci*colW+colW-10;
    inner+='<text x="'+cx.toFixed(0)+'" y="'+top+'" text-anchor="end" class="lab" style="font-size:11.5px;fill:#5f5e5a">'+c[0]+'</text>';});
  inner+='<line class="axis" x1="'+pad+'" y1="'+(top+8)+'" x2="'+(W-pad)+'" y2="'+(top+8)+'"/>';
  ss.forEach((id,ri)=>{const r=byId[id],nat=r.nat||{},y=top+headH+ri*rh,cy=y+rh/2,c=colorOf(id),foc=id===FOCUS;
    inner+='<circle cx="'+(pad+6)+'" cy="'+cy.toFixed(0)+'" r="4.5" fill="'+c+'"/>';
    inner+='<text x="'+(pad+18)+'" y="'+(cy+4).toFixed(0)+'" class="lab" style="font-size:12px;font-weight:'+(foc?500:400)+';fill:#1b1813">'+esc(r.name)+'</text>';
    NAT_COLS.forEach((col,ci)=>{const v=nat[col[1]],tx=pad+nameW+ci*colW+colW-10;
      inner+='<text x="'+tx.toFixed(0)+'" y="'+(cy+4).toFixed(0)+'" text-anchor="end" class="mono" style="font-size:11.5px;fill:#1b1813">'+natFmt(v,col[2])+'</text>';});
    if(ri<ss.length-1)inner+='<line class="grid" x1="'+pad+'" y1="'+(y+rh)+'" x2="'+(W-pad)+'" y2="'+(y+rh)+'"/>';});
  const yEnd=top+headH+ss.length*rh+8;
  inner+='<text x="'+pad+'" y="'+(yEnd+18)+'" class="end" style="font-size:11.5px;fill:#5f5e5a">'+natFocusLine()+'</text>';
  return {y:yEnd+30,inner:inner};
}
function cNatureBars(){
  const ss=sel(),W=800,pad=20,labW=120,top=84,groupH=22,barH=11,barGap=4,groupGap=16;
  const x0=pad+labW,x1=W-pad-44,tw=x1-x0;
  let inner='',y=top;
  NAT_COLS.forEach(col=>{const getter=r=>r.nat&&r.nat[col[1]];
    const corpus=vis().map(getter).filter(v=>v!=null&&isFinite(+v)).map(Number);
    const mx=corpus.length?Math.max.apply(null,corpus):0;
    const inv=(col[1]==='water'||col[1]==='pm25');
    inner+='<text x="'+pad+'" y="'+(y+9)+'" class="lab" style="font-size:12px;fill:#5f5e5a">'+col[0]+'</text>';
    y+=groupH;
    ss.forEach(id=>{const r=byId[id],v=getter(r),c=colorOf(id),has=v!=null&&isFinite(+v);
      let frac=0; if(has&&mx>0){frac=inv?(1-Math.min(1,(+v)/mx)):((+v)/mx); frac=Math.max(0,Math.min(1,frac));}
      inner+='<text x="'+(pad+8)+'" y="'+(y+barH-1)+'" class="lab" style="font-size:10.5px;fill:#888780">'+esc(r.name).slice(0,16)+'</text>';
      inner+='<rect x="'+x0+'" y="'+y+'" width="'+tw+'" height="'+barH+'" rx="3" fill="#f1efe8"/>';
      if(has)inner+='<rect x="'+x0+'" y="'+y+'" width="'+(tw*frac).toFixed(1)+'" height="'+barH+'" rx="3" fill="'+c+'"/>';
      inner+='<text x="'+(x1+6)+'" y="'+(y+barH-1)+'" class="mono" style="font-size:10.5px;fill:#1b1813">'+natFmt(v,col[2])+'</text>';
      y+=barH+barGap;});
    y+=groupGap;});
  inner+='<text x="'+pad+'" y="'+(y+4)+'" class="end" style="font-size:11.5px;fill:#5f5e5a">'+natFocusLine()+'</text>';
  return {y:y+20,inner:inner};
}
function cNatureDots(){
  const ss=sel(),W=800,pad=20,top=86,rowH=58,labW=118;
  const x0=pad+labW,x1=W-pad-18,tw=x1-x0;
  let inner='',y=top;
  NAT_COLS.forEach(col=>{const getter=r=>r.nat&&r.nat[col[1]];
    const corpus=vis().map(getter).filter(v=>v!=null&&isFinite(+v)).map(Number).sort((a,b)=>a-b);
    inner+='<text x="'+pad+'" y="'+(y+4)+'" class="lab" style="font-size:11.5px;fill:#1b1813">'+col[0]+'</text>';
    if(!corpus.length){inner+='<text x="'+x0+'" y="'+(y+4)+'" class="end">no data</text>';y+=rowH;return;}
    const lo=corpus[0],hi=corpus[corpus.length-1],rng=(hi-lo)||1,px=v=>x0+((+v-lo)/rng)*tw;
    inner+='<line class="axis" x1="'+x0+'" y1="'+y+'" x2="'+x1+'" y2="'+y+'"/>';
    corpus.forEach(v=>inner+='<circle class="dot" cx="'+px(v).toFixed(1)+'" cy="'+y+'" r="2"/>');
    inner+='<text x="'+x0+'" y="'+(y+21)+'" class="end">'+natFmt(lo,col[2])+'</text><text x="'+x1+'" y="'+(y+21)+'" text-anchor="end" class="end">'+natFmt(hi,col[2])+'</text>';
    ss.forEach(id=>{const v=getter(byId[id]);if(v==null||!isFinite(+v))return;const foc=id===FOCUS,c=colorOf(id);
      inner+='<circle cx="'+px(v).toFixed(1)+'" cy="'+y+'" r="'+(foc?6:4.8)+'" fill="'+c+'" stroke="#faf8f4" stroke-width="'+(foc?2:1.6)+'"/>';});
    y+=rowH;});
  inner+='<text x="'+pad+'" y="'+(y+2)+'" class="end" style="font-size:11.5px;fill:#5f5e5a">'+natFocusLine()+'</text>';
  return {y:y+18,inner:inner};
}
function cNature(){
  const W=800;
  const out=NATMODE==='bars'?cNatureBars():NATMODE==='dots'?cNatureDots():cNatureTable();
  const sub=NATMODE==='bars'?'green, canopy, trees, water and air \u00b7 longer = more (water and air inverted: cleaner is longer)'
    :NATMODE==='dots'?'each district vs the 37-district range \u00b7 dots = corpus \u00b7 canopy gated to reliable samples'
    :'green, canopy, trees, water and air for each district \u00b7 canopy gated to reliable samples';
  return {title:'Nature',svg:svg(W,out.y,'Nature',sub,out.inner)};
}

const CHARTS=[['typology',cTypology],['facts',cFacts],['census',cCensus],['table',cTable],['benchmark',cBench],['rose',cRose],['similarity',cSim],['program',cProgram],['scatter',cScatter],['ranking',cRank],['nature',cNature]];

function svgToPng(svgStr,name){
  const vb=svgStr.match(/viewBox="0 0 (\d+(?:\.\d+)?) (\d+(?:\.\d+)?)"/);const sc=2,w=(+vb[1])*sc,h=(+vb[2])*sc;
  const blob=new Blob([svgStr],{type:'image/svg+xml;charset=utf-8'}),url=URL.createObjectURL(blob),img=new Image();
  img.onload=function(){const c=document.createElement('canvas');c.width=w;c.height=h;const ctx=c.getContext('2d');
    ctx.fillStyle='#faf8f4';ctx.fillRect(0,0,w,h);ctx.drawImage(img,0,0,w,h);URL.revokeObjectURL(url);
    c.toBlob(function(b){const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=name+'.png';document.body.appendChild(a);a.click();a.remove();});};
  img.src=url;
}
function svgDownload(svgStr,name){var s=svgStr;if(!/xmlns=/.test(s))s=s.replace('<svg','<svg xmlns="http://www.w3.org/2000/svg"');var blob=new Blob([s],{type:'image/svg+xml'});var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name+'.svg';document.body.appendChild(a);a.click();a.remove();}
let LAST={};
function metricSelect(id,val){return '<select data-axis="'+id+'">'+METRICS.map(m=>'<option value="'+m[0]+'"'+(m[0]===val?' selected':'')+'>'+m[1]+'</option>').join('')+'</select>';}
function render(){
  const sheet=document.getElementById('sheet');sheet.innerHTML='';LAST={};
  CHARTS.forEach(([key,fn])=>{const out=fn();LAST[key]=out.svg;
    let ax='';
    if(key==='scatter')ax='<div class="axsel"><span class="k">Y</span>'+metricSelect('y',SCAT.y)+'<span class="k">X</span>'+metricSelect('x',SCAT.x)+'</div>';
    else if(key==='ranking')ax='<div class="axsel"><span class="k">Metric</span>'+metricSelect('rank',RANKM)+'</div>';
    else if(key==='nature')ax='<div class="axsel"><span class="k">View</span><select data-natmode="1">'+['table','bars','dots'].map(function(m){return '<option value="'+m+'"'+(m===NATMODE?' selected':'')+'>'+m.charAt(0).toUpperCase()+m.slice(1)+'</option>';}).join('')+'</select></div>';
    else ax='<span></span>';
    const b=document.createElement('div');b.className='block'+(out.center?' center':'');
    b.innerHTML='<div class="chart">'+out.svg+'</div><div class="blockbar">'+ax
      +'<div><button class="ghost" data-big="'+key+'">Enlarge</button><button class="ghost" data-svg="'+key+'">SVG</button><button class="ghost" data-png="'+key+'">PNG</button></div></div>';
    sheet.appendChild(b);
  });
  sheet.querySelectorAll('[data-png]').forEach(b=>b.addEventListener('click',()=>svgToPng(LAST[b.dataset.png],'SAD_'+b.dataset.png+'_'+byId[FOCUS].name.replace(/\s+/g,'-'))));
  sheet.querySelectorAll('[data-big]').forEach(b=>b.addEventListener('click',()=>openBig(b.dataset.big)));
  sheet.querySelectorAll('[data-svg]').forEach(b=>b.addEventListener('click',()=>svgDownload(LAST[b.dataset.svg],'SAD_'+b.dataset.svg+'_'+byId[FOCUS].name.replace(/\s+/g,'-'))));
  sheet.querySelectorAll('[data-axis]').forEach(s=>s.addEventListener('change',e=>{const a=e.target.dataset.axis,v=e.target.value;if(a==='rank')RANKM=v;else SCAT[a]=v;render();}));
  sheet.querySelectorAll('[data-natmode]').forEach(s=>s.addEventListener('change',e=>{NATMODE=e.target.value;render();}));
  renderChips();renderAdd();
  document.getElementById('foot').textContent='Focus: '+byId[FOCUS].name+' · '+COMPS.length+' comparison'+(COMPS.length===1?'':'s')+' · '+ROWS.length+' districts · enlarge or download any diagram as a slide-ready PNG.';
}
function openBig(key){const o=document.getElementById('ovl');document.getElementById('ovl-body').innerHTML=LAST[key];
  document.getElementById('ovl-png').onclick=()=>svgToPng(LAST[key],'SAD_'+key+'_'+byId[FOCUS].name.replace(/\s+/g,'-'));o.classList.add('on');}
document.getElementById('ovl-close').addEventListener('click',()=>document.getElementById('ovl').classList.remove('on'));
document.getElementById('ovl').addEventListener('click',e=>{if(e.target.id==='ovl')e.currentTarget.classList.remove('on');});
function renderChips(){document.getElementById('chips').innerHTML=sel().map(id=>{const r=byId[id],foc=id===FOCUS;
  return '<span class="chip"><span class="chip-key" style="background:'+colorOf(id)+'"></span><span class="chip-name">'+r.name+'</span>'+(foc?'':'<span class="x" data-rm="'+id+'">×</span>')+'</span>';}).join('');
  document.querySelectorAll('#chips .x').forEach(x=>x.addEventListener('click',()=>{COMPS=COMPS.filter(c=>c!==x.dataset.rm);render();}));}
function renderAdd(){const s=sel();document.getElementById('add').innerHTML='<option value="">+ add district…</option>'+ROWS.filter(r=>!s.includes(r.sad_id)).sort((a,b)=>a.name.localeCompare(b.name)).map(r=>'<option value="'+r.sad_id+'">'+r.name+' · '+r.city+'</option>').join('');}

function extract(m){
  const g=(d,...p)=>{for(const k of p){if(!d||typeof d!=='object')return null;d=d[k];}return d;};
  const rows=[];
  for(const r of (m.sads||[])){
    const hasData=(r.census&&r.census.sad)||((r.amenity&&r.amenity.total_points_in_sad)!=null)||((r.program&&r.program.total)!=null);
    if(/drawn/i.test(r.sad_id)&&!hasData)continue;
    const parts=r.sad_id.split('_'), cs=(r.census&&r.census.sad)||{};
    rows.push({sad_id:r.sad_id,name:(parts[1]||r.sad_id).replace(/-/g,' '),city:(parts[2]||'').replace(/-/g,' '),typology:r.typology,
      cen:{pop:cs.estimated_population,income:cs.median_household_income_pop_weighted,age:cs.median_age_pop_weighted,renter:cs.pct_renter_occupied,bach:cs.pct_bachelors_or_higher},
      pois:(g(r,'amenity','total_points_in_sad')!=null?g(r,'amenity','total_points_in_sad'):g(r,'program','total')),
      amenity:g(r,'amenity','category_counts')||{},transit:g(r,'transit','total_stations'),
      walk:g(r,'walkshed','walkshed_10min_acres'),nodes:g(r,'centrality','network_size','nodes'),boundary:g(r,'artifacts','sad_boundary'),program_real:(r.program_real||null),report:(r.report||null),nat:(function(){var n=r.nature||{};var c=n.canopy||{},gb=n.green_blue||{},ai=n.air||{};var green=(gb.green_area_share!=null?gb.green_area_share:null),carea=(c.canopy_area_share!=null?c.canopy_area_share:null);var gcov=(green!=null||carea!=null)?Math.max(green||0,carea||0):null;return {green:gcov,canopy:(c.height_reliable?c.mean_canopy_m:null),trees:(gb.tree_points!=null?gb.tree_points:null),water:(gb.nearest_water_m!=null?gb.nearest_water_m:null),pm25:(ai.pm25!=null?ai.pm25:null)};})()});
  }
  const ids=new Set(rows.map(x=>x.sad_id)), dm=g(m,'embedding','distance_matrix')||{}, dist={};
  for(const a in dm){if(!ids.has(a))continue;dist[a]={};for(const b in dm[a])if(ids.has(b))dist[a][b]=dm[a][b];}
  return {rows,dist};
}
async function init(){
  try{const m=await (await fetch('compare_manifest_geo.json?t='+Date.now())).json();const ex=extract(m);ROWS=ex.rows;DIST=ex.dist;}
  catch(e){document.getElementById('sheet').innerHTML='<div class="block">Could not load compare_manifest_geo.json.</div>';return;}
  byId={};ROWS.forEach(r=>byId[r.sad_id]=r);
  AXSTAT=AX.map(([,f])=>ROWS.filter(r=>!/rawn/i.test(r.sad_id)).map(r=>+f(r)).filter(isFinite).sort((a,b)=>a-b));
  const _drawn=ROWS.filter(r=>/rawn/i.test(r.sad_id)&&r.amenity&&Object.keys(r.amenity).length).sort((a,b)=>(parseInt(b.sad_id)||0)-(parseInt(a.sad_id)||0));FOCUS=(_drawn[0]||ROWS.find(r=>/Detroit/i.test(r.sad_id))||ROWS[0]).sad_id;COMPS=autoComps(FOCUS,3);
const fsel=document.getElementById('focus');
ROWS.slice().sort((a,b)=>a.name.localeCompare(b.name)).forEach(r=>{const o=document.createElement('option');o.value=r.sad_id;o.textContent=r.name+' · '+r.city;fsel.appendChild(o);});
fsel.value=FOCUS;fsel.addEventListener('change',()=>{FOCUS=fsel.value;COMPS=autoComps(FOCUS,3);render();});
document.getElementById('add').addEventListener('change',e=>{if(e.target.value&&COMPS.length<4){COMPS.push(e.target.value);render();}});
render();
}
init();
