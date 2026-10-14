'use strict';
let uploadRecordId = null;
window.renderUploadActions = function(record, info, actions) {
 const jobs=(window.uploadJobs||[]).filter(job=>job.recording_id===record.id);
 if(record.state==='ready') {
  const button=element('button','Upload to YouTube');button.type='button';
  button.onclick=async()=>{
   uploadRecordId=record.id;
   $('upload-title').value=(record.title||record.filename.replace(/\.mp4$/,'' )).slice(0,100);
   $('upload-description').value='';$('upload-privacy').value='unlisted';$('upload-tags').value='';$('upload-category').value='22';$('upload-audience').value='';$('upload-notify').checked=false;
   try {const channel=await api('/api/v1/uploads/channel');$('upload-channel').textContent=channel.channel?`Channel: ${channel.channel.name} (${channel.channel.id})`:'Connect a YouTube channel in Settings first.';}
   catch(e){$('upload-channel').textContent=e.message;}
   $('upload-dialog').showModal();
  };actions.append(button);
 }
 for(const job of jobs) {
  const line=element('div',undefined,'record-meta');line.append(element('span',`YouTube: ${job.state} · ${Math.round(100*(job.acknowledged_bytes||0)/(job.size||1))}% · attempt ${job.attempts}${job.error?' · '+job.error:''}`));
  if(job.url){const link=element('a',job.url);link.href=job.url;link.target='_blank';link.rel='noopener';line.append(document.createTextNode(' · '),link);const copy=element('button','Copy link');copy.type='button';copy.onclick=()=>navigator.clipboard.writeText(job.url).then(()=>notice('YouTube link copied.'),()=>notice('Select and copy the link above.'));line.append(copy);}
  if(['failed','paused'].includes(job.state)){const retry=element('button','Retry');retry.onclick=()=>act(()=>api(`/api/v1/uploads/${job.id}/retry`,{method:'POST',body:'{}'}));line.append(retry);}
  if(['queued','uploading','paused','failed','uncertain'].includes(job.state)){const cancel=element('button','Cancel upload');cancel.onclick=()=>act(()=>api(`/api/v1/uploads/${job.id}/cancel`,{method:'POST',body:'{}'}));line.append(cancel);}
  if(['failed','uncertain','paused'].includes(job.state)){const release=element('button','Release protection');release.title='Allow storage cleanup or deletion of this local recording';release.onclick=()=>{if(confirm('Release upload protection for this recording?'))act(()=>api(`/api/v1/uploads/${job.id}/release`,{method:'POST',body:'{}'}));};line.append(release);}
  if(['completed','canceled','released','failed','uncertain','paused'].includes(job.state)){const requeue=element('button','Upload again');requeue.onclick=()=>{if(!confirm('Start a separate video upload? Check the channel first, especially if the previous result is uncertain.'))return;act(()=>api(`/api/v1/uploads/${job.id}/requeue`,{method:'POST',body:JSON.stringify({reconciled:true})}));};line.append(requeue);}
  info.append(line);
 }
};
$('upload-cancel').onclick=()=>$('upload-dialog').close();
$('upload-form').onsubmit=event=>{
 event.preventDefault();if(!uploadRecordId)return;
 const id=uploadRecordId;
 const body={title:$('upload-title').value.trim(),description:$('upload-description').value.trim(),privacy_status:$('upload-privacy').value,made_for_kids:$('upload-audience').value==='true',notify_subscribers:$('upload-notify').checked,tags:$('upload-tags').value.split(',').map(x=>x.trim()).filter(Boolean),category_id:$('upload-category').value.trim()};
 $('upload-dialog').close();
 act(()=>api(`/api/v1/recordings/${encodeURIComponent(id)}/uploads`,{method:'POST',body:JSON.stringify(body),headers:{'Idempotency-Key':crypto.randomUUID()}}));
};
