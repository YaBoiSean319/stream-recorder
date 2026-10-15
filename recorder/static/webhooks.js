'use strict';
(async function webhookSettings() {
 const access=await accessReady;
 if(access.role!=='admin')return;
 const byId=id=>document.getElementById(id);
 byId('webhook-settings').hidden=false;
 let editing=null;
 function reset(){editing=null;byId('webhook-form').reset();byId('webhook-secret').placeholder='Enter a signing secret (16–256 characters)';}
 function edit(item){editing=item.id;byId('webhook-url').value=item.url;byId('webhook-secret').value='';byId('webhook-secret').placeholder='Saved secret; leave blank to retain';byId('webhook-enabled').checked=item.enabled;document.querySelectorAll('[name="webhook-event"]').forEach(input=>input.checked=item.events.includes(input.value));}
 async function load(){const [destinations,deliveries]=await Promise.all([api('/api/v1/webhooks'),api('/api/v1/webhooks/deliveries')]);const box=byId('webhook-destinations');box.replaceChildren();for(const item of destinations.items){const row=document.createElement('p');const label=document.createElement('span');label.textContent=`${item.url} · ${item.events.join(', ')} · ${item.enabled?'Enabled':'Disabled'} `;const modify=document.createElement('button');modify.type='button';modify.textContent='Edit';modify.onclick=()=>edit(item);const remove=document.createElement('button');remove.type='button';remove.textContent='Delete';remove.className='danger';remove.onclick=async()=>{if(!confirm('Delete this receiver and cancel pending deliveries?'))return;try{await api(`/api/v1/webhooks/${encodeURIComponent(item.id)}`,{method:'DELETE'});if(editing===item.id)reset();await load();}catch(e){byId('webhook-error').textContent=e.message;}};row.append(label,modify,remove);box.append(row);}if(!destinations.items.length)box.textContent='No receivers configured.';const out=byId('webhook-deliveries');out.replaceChildren();for(const delivery of deliveries.items){const row=document.createElement('p');row.textContent=`${delivery.event} · ${delivery.event_id.slice(0,12)} · ${delivery.status} · ${delivery.attempts} attempt(s)${delivery.last_error?' · '+delivery.last_error:''}`;out.append(row);}if(!deliveries.items.length)out.textContent='No deliveries yet.';}
 byId('webhook-new').onclick=reset;
 byId('webhook-form').onsubmit=async e=>{e.preventDefault();const body={url:byId('webhook-url').value,events:[...document.querySelectorAll('[name="webhook-event"]:checked')].map(input=>input.value),enabled:byId('webhook-enabled').checked};const secret=byId('webhook-secret').value;if(secret)body.secret=secret;try{await api(editing?`/api/v1/webhooks/${encodeURIComponent(editing)}`:'/api/v1/webhooks',{method:editing?'PUT':'POST',body:JSON.stringify(body)});byId('webhook-error').textContent='';reset();await load();}catch(error){byId('webhook-error').textContent=error.message;}};
 try{await load();}catch(e){byId('webhook-error').textContent=e.message;}
 setInterval(()=>{if(location.hash==='#settings')load().catch(e=>byId('webhook-error').textContent=e.message);},5000);
})();
