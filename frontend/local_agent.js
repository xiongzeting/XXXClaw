'use strict';
const $ = id => document.getElementById(id);
const chat = $('chat'), input = $('input'), send = $('send');
const welcome = chat.innerHTML;
let busy = false, following = true, activeRun = null;
const statusLine = document.createElement('div');
statusLine.className = 'run-status'; statusLine.setAttribute('role', 'status');
$('form').prepend(statusLine);
const stop = document.createElement('button');
stop.type = 'button'; stop.className = 'stop'; stop.textContent = '■ 停止'; stop.hidden = true;
send.before(stop); send.setAttribute('aria-label', '发送消息');
const resume = document.createElement('button');
resume.type = 'button'; resume.className = 'resume'; resume.textContent = '↻ 继续任务'; resume.hidden = true;
send.before(resume);
input.setAttribute('aria-label', '消息');
const latest = document.createElement('button');
latest.type = 'button'; latest.className = 'latest'; latest.textContent = '↓ 回到最新消息'; latest.hidden = true;
chat.after(latest);
chat.setAttribute('aria-label', '对话消息'); chat.tabIndex = 0;
function follow(){if(following){chat.scrollTop = chat.scrollHeight;latest.hidden = true}else latest.hidden = false}
chat.addEventListener('scroll', () => {following = chat.scrollHeight-chat.scrollTop-chat.clientHeight < 70;latest.hidden = following});
latest.onclick = () => {following = true;follow()};
function setStatus(text, error=false){statusLine.textContent=text;statusLine.dataset.error=String(error)}
function statusLabel(status){return {success:'已完成',paused:'任务已暂停',cancelled:'任务已取消',error:'任务失败'}[status]||'任务状态未知'}
function terminalStatusText(run){
  if(run.error)return `请求失败：${run.error}`;
  if(run.status==='paused'){
    const todo=[...run.pending||[],...run.remaining||[]].filter(Boolean);
    return `任务已暂停${todo.length?`：${todo.join('；')}`:run.nextAction?`：${run.nextAction}`:'，可继续恢复'}`;
  }
  return statusLabel(run.status||(run.stopRequested?'cancelled':'error'));
}
function setBusy(value){busy=value;send.disabled=value;stop.hidden=!value;stop.disabled=false;$('newBtn').disabled=value;renderList()}
function textNode(tag, text, className){const el=document.createElement(tag);el.textContent=text;if(className)el.className=className;return el}
function addMessage(kind, text, phase=''){
  const el=document.createElement('div');el.className='message '+kind+(phase?' '+phase:'');
  el.append(textNode('div',kind==='user'?'你':'M','avatar'));
  const content=document.createElement('div');content.className='message-content';
  content.append(textNode('div',kind==='user'?'你':phase==='progress'?'处理过程':'MiniClaw','message-label'));
  const bubble=textNode('div',text,'bubble');content.append(bubble);el.append(content);chat.append(el);if(phase==='progress')collapseProgress(bubble);follow();return bubble;
}
function collapseProgress(bubble){
  const content=bubble.parentElement;
  if(content.tagName==='DETAILS')return;
  content.querySelector('.message-label')?.remove();
  const details=document.createElement('details');details.className='progress-details';
  details.append(textNode('summary','处理过程'));content.append(details);details.append(bubble);
}
function renderTool(row){
  const el=document.createElement('details');el.className='tool-event';
  const summary=document.createElement('summary');el.append(summary);
  el.append(textNode('div','调用参数','label'));const args=textNode('pre','','');el.append(args);
  el.append(textNode('div','执行结果','label'));const result=textNode('pre','','');el.append(result);
  chat.append(el);
  const update=()=>{el.dataset.state=row.state;summary.textContent=`${row.state==='running'?'◌':row.state==='done'?'✓':'!'} ${row.name} · ${row.state==='running'?'执行中':row.state==='error'?'执行失败':row.state==='interrupted'?'已中断':row.state==='done'?'已完成':'状态未记录'}`;args.textContent=typeof row.arguments==='string'?row.arguments:JSON.stringify(row.arguments??{},null,2);result.textContent=row.result??'等待工具返回…';follow()};
  update();return update;
}
let conversations=[];
try{const saved=JSON.parse(localStorage.getItem('miniclaw_conversations')||'[]');if(Array.isArray(saved))conversations=saved.filter(c=>c&&typeof c.id==='string'&&Array.isArray(c.messages))}catch{}
let current=localStorage.getItem('miniclaw_current')||crypto.randomUUID();
if(!conversations.some(c=>c.id===current))conversations.unshift({id:current,title:'新对话',messages:[],createdAt:Date.now()});
function currentConv(){return conversations.find(c=>c.id===current)}
function save(){try{localStorage.setItem('miniclaw_conversations',JSON.stringify(conversations));localStorage.setItem('miniclaw_current',current)}catch{setStatus('本地存储空间不足，本次记录暂未保存。',true)}renderList()}
function renderList(){
  const box=$('conversationList');box.replaceChildren();
  for(const c of conversations){
    const b=document.createElement('button');b.type='button';b.className='session'+(c.id===current?' active':'');b.setAttribute('aria-current',c.id===current?'true':'false');b.disabled=busy;
    b.title=c.title||'新对话';b.append(textNode('span',b.title,'session-title'));
    const date=c.createdAt?new Date(c.createdAt).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):c.id.slice(0,6);
    b.append(textNode('span',`${c.id===current?'当前对话 · ':''}${date} · ${c.messages.filter(m=>m.kind==='user').length} 条提问`,'session-meta'));
    b.onclick=()=>loadConv(c.id);box.append(b);
  }
  document.querySelector('.topbar .title').textContent=currentConv()?.title||'新对话';
}
function displayConv(id){current=id;following=true;resume.hidden=true;chat.replaceChildren();const c=currentConv();if(!c.messages.length)chat.innerHTML=welcome;for(const m of c.messages){if(m.kind==='tool')renderTool(m);else addMessage(m.kind,m.text,m.phase)}setStatus('');save();follow()}
async function selectSession(id){
  const r=await fetch('/api/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:id})});
  const result=await r.json();if(!r.ok)throw Error(result.error||'切换会话失败');return result;
}
async function loadConv(id){
  if(busy)return;const target=conversations.find(c=>c.id===id);if(!target)return;
  if(!target.serverId){displayConv(id);setStatus('这是旧版本的本地历史，无法确认对应模型会话。请新建对话后继续。',true);return}
  setBusy(true);try{await selectSession(target.serverId);displayConv(id);await info()}catch(e){setStatus(e.message,true)}finally{setBusy(false)}
}
function appendRecord(conv,row){conv.messages.push(row);save()}
function markProgress(run){if(run.bubble){run.bubble.closest('.message').classList.add('progress');collapseProgress(run.bubble);run.row.phase='progress';run.bubble=null;run.row=null}}
function consume(run,kind,e){
  if(kind==='status'){if(!run.error)setStatus(e.text+(e.elapsed?` · 已等待 ${e.elapsed} 秒`:''));return}
  if(kind==='error'){run.error=e.error||'请求失败';run.status='error';setStatus(run.error,true);return}
  if(kind==='done'){
    run.done=true;run.status=e.status||'error';run.stopReason=e.stop_reason;
    run.resumable=Boolean(e.resumable);run.nextAction=e.next_action||'';run.pending=e.pending||[];run.remaining=e.remaining||[];
    $('goal').textContent=e.goal||'暂无';
    if(run.status==='paused'){
      const todo=[...run.pending,...run.remaining].filter(Boolean);
      setStatus(`任务已暂停${todo.length?`：${todo.join('；')}`:'，可继续恢复'}`,true);
    } else setStatus(statusLabel(run.status),run.status==='error');
    return;
  }
  if(kind!=='agent')return;
  if(e.type==='text_delta'){
    if(!run.bubble){run.row={kind:'assistant',text:''};appendRecord(run.conv,run.row);run.bubble=addMessage('assistant','')}
    run.row.text+=e.text_delta||e.text||'';run.bubble.textContent=run.row.text;setStatus('正在生成回复');follow();
  }else if(e.type==='turn_started'){markProgress(run);setStatus('等待模型响应')}
  else if(e.type==='tool_started'||e.type==='tool_finished'){
    markProgress(run);const id=e.tool_call_id||e.tool_name;let tool=run.tools.get(id);
    if(!tool){const row={kind:'tool',id,name:e.tool_name||'工具',arguments:e.arguments,state:'running'};appendRecord(run.conv,row);tool={row,update:renderTool(row)};run.tools.set(id,tool)}
    if(e.type==='tool_finished'){tool.row.state=e.cancelled?'interrupted':e.is_error?'error':'done';tool.row.result=typeof e.tool_result==='string'?e.tool_result:JSON.stringify(e.tool_result??'');tool.update()}
    setStatus(e.type==='tool_started'?`正在执行：${tool.row.name}`:'工具已返回，等待模型响应');
  }else if(e.type==='error'){run.error=e.text||'模型执行失败';setStatus(run.error,true)}
}
async function readEvents(body, onEvent){
  const reader=body.getReader(),decoder=new TextDecoder();let buffer='';
  const drain=()=>{let match;while((match=/\r?\n\r?\n/.exec(buffer))){const block=buffer.slice(0,match.index);buffer=buffer.slice(match.index+match[0].length);let kind='message';const data=[];for(const line of block.split(/\r?\n/)){if(line.startsWith('event:'))kind=line.slice(6).trim();if(line.startsWith('data:'))data.push(line.slice(5).trimStart())}if(data.length)onEvent(kind,JSON.parse(data.join('\n')))}};
  try{while(true){const part=await reader.read();if(part.done){buffer+=decoder.decode();drain();break}buffer+=decoder.decode(part.value,{stream:true});drain()}}finally{reader.releaseLock()}
}
async function sendMessage(text){
  text=text.trim();if(!text||busy)return;
  if(!currentConv()?.serverId){setStatus('此历史记录没有对应的模型会话，请新建对话。',true);return}
  const conv=currentConv();document.querySelector('.welcome')?.remove();following=true;setBusy(true);
  if(conv.title==='新对话')conv.title=text.slice(0,24);appendRecord(conv,{kind:'user',text});addMessage('user',text);input.value='';
  const run={conv,tools:new Map(),bubble:null,row:null,done:false,error:null,status:null,stopRequested:false};activeRun=run;resume.hidden=true;setStatus('正在连接模型…');
  try{
    const r=await fetch('/api/chat/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text,session_id:conv.serverId})});
    if(!r.ok){const e=await r.json().catch(()=>({}));throw Error(e.error||`请求失败（${r.status}）`)}
    await readEvents(r.body,(kind,e)=>consume(run,kind,e));
    if(!run.done&&!run.error)throw Error('连接已中断，未收到完成确认。');
  }catch(e){run.error=e.message}
  finally{
    for(const tool of run.tools.values())if(tool.row.state==='running'){tool.row.state='interrupted';tool.row.result='执行未完成，连接或任务已中断。';tool.update()}
    const outcome=terminalStatusText(run);
    if(run.error||run.stopRequested){appendRecord(conv,{kind:'assistant',text:outcome});addMessage('assistant',outcome)}
    resume.hidden=!(run.status==='paused' && run.resumable);
    activeRun=null;setBusy(false);setStatus(outcome,!!run.error);save();input.focus();follow();
  }
}
async function resumeTask(){
  if(busy||!currentConv()?.serverId)return;
  const conv=currentConv(),run={conv,tools:new Map(),bubble:null,row:null,done:false,error:null,status:null,stopRequested:false};
  activeRun=run;resume.hidden=true;setBusy(true);setStatus('正在恢复上次暂停的任务…');
  try{
    const r=await fetch('/api/resume',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:conv.serverId})});
    if(!r.ok){const e=await r.json().catch(()=>({}));throw Error(e.error||`恢复失败（${r.status}）`)}
    await readEvents(r.body,(kind,e)=>consume(run,kind,e));
    if(!run.done&&!run.error)throw Error('连接已中断，未收到完成确认。');
  }catch(e){run.error=e.message}
  finally{
    for(const tool of run.tools.values())if(tool.row.state==='running'){tool.row.state='interrupted';tool.row.result='执行未完成，连接或任务已中断。';tool.update()}
    const outcome=terminalStatusText(run);
    if(run.error){appendRecord(conv,{kind:'assistant',text:outcome});addMessage('assistant',outcome)}
    resume.hidden=!(run.status==='paused'&&run.resumable);activeRun=null;setBusy(false);setStatus(outcome,!!run.error);save();input.focus();follow();
  }
}
resume.onclick=()=>void resumeTask();
stop.onclick=async()=>{const run=activeRun;if(!run)return;stop.disabled=true;try{const r=await fetch('/api/cancel',{method:'POST'});if(!r.ok)throw Error('停止请求失败');if(activeRun===run){run.stopRequested=true;setStatus('正在停止，等待当前任务结束…')}}catch(e){if(activeRun===run){setStatus(e.message,true);stop.disabled=false}}};
$('form').addEventListener('submit',e=>{e.preventDefault();void sendMessage(input.value)});
input.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();void sendMessage(input.value)}});
chat.addEventListener('click',e=>{const b=e.target.closest('[data-prompt]');if(b)void sendMessage(b.dataset.prompt)});
$('newBtn').onclick=async()=>{if(busy)return;setBusy(true);try{const r=await fetch('/api/new',{method:'POST'});const x=await r.json();if(!r.ok)throw Error(x.error||'新建对话失败，请稍后重试');current=x.session_id;conversations.unshift({id:current,serverId:current,title:'新对话',messages:[],createdAt:Date.now()});displayConv(current);input.focus()}catch(e){setStatus(e.message,true)}finally{setBusy(false)}};
async function info(){try{const r=await fetch('/api/info');if(!r.ok)throw Error();const x=await r.json();$('workspace').textContent=x.workspace;$('workspace2').textContent=x.workspace;$('model').textContent=`${x.provider} / ${x.model}`;$('runtime').textContent=`${x.sandbox} · ${x.workspace_mode}`;$('approval').textContent=x.approval;$('goal').textContent=x.goal||'暂无';$('tools').replaceChildren(...(x.tools||[]).map(t=>textNode('span',t,'chip')));const last=x.last_result;if(last?.status==='paused'&&last.resumable){resume.hidden=false;setStatus(`任务已暂停${last.next_action?`：${last.next_action}`:''}`,true)}document.querySelector('.status span').textContent='已连接'}catch{document.querySelector('.status span').textContent='未连接';$('workspace').textContent='连接失败';setStatus('无法连接本地服务，请检查服务是否运行。',true)}}
async function initialize(){
  displayConv(current);setBusy(true);
  try{
    const r=await fetch('/api/info');if(!r.ok)throw Error('无法连接服务');const state=await r.json();let target=currentConv();
    if(target.serverId){await selectSession(target.serverId)}
    else if(target.id===state.session_id||target.messages.length===0){target.serverId=state.session_id}
    else if(/^[a-f0-9]{32}$/.test(target.id)){await selectSession(target.id);target.serverId=target.id}
    else{
      target=conversations.find(c=>c.serverId===state.session_id||c.id===state.session_id);
      if(!target){const response=await fetch('/api/history');if(!response.ok)throw Error('无法恢复会话历史');const history=await response.json();target={id:state.session_id,serverId:state.session_id,title:history.messages.find(m=>m.kind==='user')?.text.slice(0,24)||'新对话',messages:history.messages,createdAt:Date.now()};conversations.unshift(target)}
      target.serverId=state.session_id;
    }
    if(target.messages.length===0){const response=await fetch('/api/history');if(response.ok){const history=await response.json();if(history.session_id===target.serverId){target.messages=history.messages;target.title=history.messages.find(m=>m.kind==='user')?.text.slice(0,24)||target.title}}}
    displayConv(target.id);await info();
  }catch(e){setStatus(e.message,true)}finally{setBusy(false)}
}
void initialize();
