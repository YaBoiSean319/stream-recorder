'use strict';
(() => {
 const $=id=>document.getElementById(id), svg=$('viewer-chart');
 let rangeMs=3600000, metric='average', lastData=null, refreshTimer=null, startX=null, zoom=null, explicitRange=null, pointIndex=0;
 const presets={ '1h':3600000,'24h':86400000,'7d':604800000,'30d':2592000000 };
 const api=async(url)=>{const r=await recorderFetch(url);let d;try{d=await r.json();}catch(_){d={};}if(!r.ok)throw Error(d.error?.message||'Could not load viewer history');return d;};
 const fmtDate=(seconds,tz)=>new Intl.DateTimeFormat(undefined,{timeZone:tz,dateStyle:'short',timeStyle:'short'}).format(new Date(seconds*1000));
 const wallTime=(value,tz)=>{const [day,clock]=value.split('T'),[y,m,d]=day.split('-').map(Number),[h,minute]=clock.split(':').map(Number),wanted=Date.UTC(y,m-1,d,h,minute);let guess=wanted;for(let i=0;i<3;i++){const parts=new Intl.DateTimeFormat('en-US',{timeZone:tz,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(guess));const values=Object.fromEntries(parts.map(p=>[p.type,p.value]));const represented=Date.UTC(+values.year,+values.month-1,+values.day,+values.hour,+values.minute);guess+=wanted-represented;}return guess;};
 const setRange=(start,end)=>{rangeMs=end-start;zoom={start:start/1000,end:end/1000};explicitRange={start:start/1000,end:end/1000};loadViewerHistory();};
 async function loadViewerHistory(){
  if($('analytics-screen').hidden)return;
  clearTimeout(refreshTimer);
  const end=explicitRange?.end||Date.now()/1000,start=explicitRange?.start||end-rangeMs;
  const params=new URLSearchParams({from:String(start),to:String(end)});
  const configuration=$('configuration-filter').value;if(configuration)params.set('configuration_id',configuration);
  $('timeline-status').textContent='Loading history…';
  try{
   const data=await api('/api/v1/viewers/history?'+params);lastData=data;
   $('analytics-timezone').textContent=data.timezone;
   $('history-live').textContent=data.live.state==='available'?String(data.live.count):'Unavailable';
   $('history-live-time').textContent=data.live.as_of?`As of ${fmtDate(Date.parse(data.live.as_of)/1000,data.timezone)}`:'Collection disabled';
   $('history-peak').textContent=data.statistics.peak==null?'—':String(data.statistics.peak);
   $('history-average').textContent=data.statistics.average==null?'—':data.statistics.average.toFixed(2);
   $('history-hours').textContent=data.statistics.viewer_hours.toFixed(3);
   $('timeline-coverage').textContent=`${data.coverage_percent.toFixed(1)}% coverage · ${Math.round(data.coverage_seconds)} seconds known · ${Math.round(data.resolution_seconds)} second resolution. Unknown intervals are excluded.`;
   $('timeline-status').textContent=data.coverage_seconds?'':'No known collection samples in this range.';
   const select=$('configuration-filter'),selected=select.value;
   const options=['<option value="">Current player configuration</option>',...data.configurations.map((c,i)=>`<option value="${c.id}">Configuration ${data.configurations.length-i} · ${fmtDate(c.first_at,data.timezone)}</option>`)];
   const signature=options.join('');if(select.dataset.options!==signature){select.innerHTML=signature;select.dataset.options=signature;select.value=selected;}
   draw();
  }catch(e){$('timeline-status').textContent=e.message;svg.replaceChildren();}
  if(!$('analytics-screen').hidden)refreshTimer=setTimeout(loadViewerHistory,120000);
 }
 function draw(){
  if(!lastData)return;
  const W=900,H=280,L=48,R=16,T=18,B=34,innerW=W-L-R,innerH=H-T-B,points=lastData.points;
  const all=points.map(p=>Number(p[metric])).filter(Number.isFinite), max=Math.max(1,...all), minT=lastData.from,maxT=lastData.to;
  svg.replaceChildren();
  const ns='http://www.w3.org/2000/svg';
  function el(tag,attrs={}){const n=document.createElementNS(ns,tag);for(const [k,v]of Object.entries(attrs))n.setAttribute(k,String(v));svg.append(n);return n;}
  for(let i=0;i<=4;i++){const y=T+innerH*i/4;el('line',{x1:L,y1:y,x2:W-R,y2:y,class:'chart-grid'});const label=el('text',{x:L-8,y:y+4,'text-anchor':'end',class:'chart-axis'});label.textContent=String(Math.round(max*(4-i)/4));}
  const x=p=>L+(p.timestamp-minT)/(maxT-minT)*innerW, y=p=>T+innerH-(Number(p[metric])/max)*innerH;
  let segment=[];const segments=[];
  for(const p of points){if(p.covered_seconds>0&&p.complete)segment.push(p);else{if(segment.length){segments.push(segment);segment=[];}if(p.covered_seconds>0)segments.push([p]);}}
  if(segment.length)segments.push(segment);
  for(const group of segments){if(group.length>1){el('polyline',{points:group.map(p=>`${x(p)},${y(p)}`).join(' '),class:'chart-line'});}for(const p of group){const c=el('circle',{cx:x(p),cy:y(p),r:Math.max(2,Math.min(4,innerW/points.length)),class:'chart-point',tabindex:'0'});c.dataset.index=points.indexOf(p);const title=el('title');title.textContent=`${fmtDate(p.timestamp,lastData.timezone)} · ${p[metric]} viewers`;c.append(title);c.addEventListener('focus',()=>showPoint(Number(c.dataset.index)));}}
  for(const time of [minT,(minT+maxT)/2,maxT]){const tx=el('text',{x:L+(time-minT)/(maxT-minT)*innerW,y:H-8,'text-anchor':'middle',class:'chart-axis'});tx.textContent=fmtDate(time,lastData.timezone);}
  svg.onpointerdown=e=>{if(e.target.closest('circle'))return;const rect=svg.getBoundingClientRect();startX=Math.max(0,Math.min(1,(e.clientX-rect.left)/rect.width));svg.setPointerCapture?.(e.pointerId);};
  svg.onpointerup=e=>{if(startX===null)return;const rect=svg.getBoundingClientRect(),endX=Math.max(0,Math.min(1,(e.clientX-rect.left)/rect.width));if(Math.abs(startX-endX)>.04){const a=Math.min(startX,endX),b=Math.max(startX,endX);zoom={start:minT+(maxT-minT)*a,end:minT+(maxT-minT)*b};setRange(zoom.start*1000,zoom.end*1000);}startX=null;};
  svg.onpointermove=e=>{if(startX!==null)return;const rect=svg.getBoundingClientRect();const fraction=Math.max(0,Math.min(1,(e.clientX-rect.left)/rect.width));if(points.length)showPoint(points.reduce((best,p,i)=>Math.abs((p.timestamp-minT)/(maxT-minT)-fraction)<Math.abs((points[best].timestamp-minT)/(maxT-minT)-fraction)?i:best,0));};
  svg.onkeydown=e=>{if(e.key==='ArrowRight'||e.key==='ArrowLeft'){e.preventDefault();pointIndex=Math.max(0,Math.min(points.length-1,pointIndex+(e.key==='ArrowRight'?1:-1)));showPoint(pointIndex);}};
  pointIndex=Math.min(pointIndex,Math.max(0,points.length-1));
 }
 function showPoint(i){const p=lastData?.points[i];if(!p)return;pointIndex=i;$('timeline-hover').textContent=p.covered_seconds?`${fmtDate(p.timestamp,lastData.timezone)} · average ${p.average.toFixed(2)} · peak ${p.peak} · ${Math.round(p.covered_seconds)} seconds covered`:`${fmtDate(p.timestamp,lastData.timezone)} · no data` ;}
 document.querySelectorAll('[data-range]').forEach(button=>button.addEventListener('click',()=>{rangeMs=presets[button.dataset.range];zoom=null;explicitRange=null;loadViewerHistory();}));
 $('range-apply').addEventListener('click',()=>{const zone=lastData?.timezone||'UTC';let a,b;try{a=wallTime($('range-start').value,zone);b=wallTime($('range-end').value,zone);}catch(_){a=NaN;b=NaN;}if(!Number.isFinite(a)||!Number.isFinite(b)||a>=b){$('timeline-status').textContent='Choose a valid increasing custom range in the reporting timezone.';return;}setRange(a,b);});
 $('metric-average').addEventListener('click',()=>{metric='average';$('metric-average').setAttribute('aria-pressed','true');$('metric-peak').setAttribute('aria-pressed','false');draw();});
 $('metric-peak').addEventListener('click',()=>{metric='peak';$('metric-average').setAttribute('aria-pressed','false');$('metric-peak').setAttribute('aria-pressed','true');draw();});
 $('zoom-reset').addEventListener('click',()=>{zoom=null;explicitRange=null;loadViewerHistory();});
 $('configuration-filter').addEventListener('change',loadViewerHistory);
 window.loadViewerHistory=loadViewerHistory;
 window.addEventListener('hashchange',()=>{if(location.hash==='#analytics')loadViewerHistory();else clearTimeout(refreshTimer);});
 if(location.hash==='#analytics')loadViewerHistory();
})();
