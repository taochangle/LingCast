import {SERVER_ORIGIN, DEFAULT_COLLECTOR_KEY, roomURL, normalizeEvents} from './protocol.js';
const init=Promise.all([
 chrome.storage.local.setAccessLevel({accessLevel:'TRUSTED_CONTEXTS'}),
 chrome.storage.session.setAccessLevel({accessLevel:'TRUSTED_CONTEXTS'})
]);
let busy=false;
async function state() {await init;return (await chrome.storage.session.get('collector')).collector;}
async function save(value) {await chrome.storage.session.set({collector:value});}
async function request(config,path,body) {
 const response=await fetch(SERVER_ORIGIN+'/api/collector/'+path,{method:'POST',credentials:'omit',redirect:'error',
   headers:{'Content-Type':'application/json',Authorization:'Bearer '+config.token,'X-LingCast-Avatar-ID':String(config.avatarId || 3)},
   body:JSON.stringify(body),signal:AbortSignal.timeout(12000)});
 const result=await response.json().catch(()=>({}));
 if(!response.ok) {const error=new Error(result.error || `服务器返回 ${response.status}`);error.status=response.status;throw error;}
 return result;
}
async function stop(config, reason='已停止') {
 if(!config) return;
 await save({...config,active:false,message:reason});
 chrome.tabs.sendMessage(config.tabId,{type:'LC_STOP'}).catch(()=>{});
 await request(config,'session',{action:'stop',revision:config.revision}).catch(()=>{});
}
async function handle(message,sender) {
 const current=await state();
 if(message.type==='LC_EVENTS') {
   if(!current?.active || sender.tab?.id!==current.tabId || sender.frameId!==0 || message.revision!==current.revision) return {ok:false,stop:true};
   let matchesRoom=false;
   try {matchesRoom=roomURL(sender.url)===current.room && roomURL(message.page_room)===current.room;} catch {}
   if(!matchesRoom) {await stop(current,'直播间已切换，请重新开始');return {ok:false,stop:true};}
   if(busy) return {ok:false};busy=true;
   try {
     const events=normalizeEvents(message.events,current.offset);
     const result=await request(current,'events',{url:current.room,revision:current.revision,events});
     // A stop/rebind during the HTTP request must never be undone by the result.
     const latest=await state();
     if(!latest?.active || latest.revision!==current.revision) return {ok:false,stop:true};
     await save({...latest,received:result.received,message:message.dom==='waiting_dom'?'未找到弹幕区域，请检查直播页面':'正在采集',lastUpload:Date.now()});
     return {ok:true};
   } catch(error) {
     if([401,409].includes(error.status)) {await stop(current,error.message);return {ok:false,stop:true};}
     const latest=await state();if(latest?.active && latest.revision===current.revision) await save({...latest,message:error.message});
     return {ok:false,error:error.message};
   } finally {busy=false;}
 }
 // A content script cannot change the target server or access the collector key.
 if(sender.tab || !sender.url?.startsWith(chrome.runtime.getURL(''))) throw new Error('无效的插件操作来源');
 if(message.type==='LC_STATUS') {
   const prefs=(await chrome.storage.local.get('prefs')).prefs || {};
   const safe=current ? {active:current.active,received:current.received,message:current.message,room:current.room,lastUpload:current.lastUpload} : null;
   return {ok:true,prefs,status:safe};
 }
 if(message.type==='LC_STOP') {await stop(current);return {ok:true};}
 if(message.type==='LC_START') {
   if(busy) throw new Error('正在上报，请稍后重试');
   const server=SERVER_ORIGIN;
   if(message.token!==DEFAULT_COLLECTOR_KEY && !/^lc_\d+_[a-f0-9]{64}$/.test(message.token)) throw new Error('请填写后台生成的采集密钥');
   const avatarId=Number(message.avatarId || 3);
   if(!Number.isSafeInteger(avatarId) || avatarId<1 || avatarId>4294967295) throw new Error('数字人 ID 无效');
   const tab=await chrome.tabs.get(message.tabId);const room=roomURL(tab.url);
   if(current?.active) await stop(current);
   const config={server,avatarId,token:message.token,tabId:tab.id,welcome:!!message.welcome,reply:!!message.reply,thanks:!!message.thanks};
   const startedAt=Date.now();const result=await request(config,'session',{action:'start',url:room,welcome:config.welcome,reply:config.reply,thanks:config.thanks});
   const active={...config,room,revision:result.revision,offset:result.server_ms-Math.round((startedAt+Date.now())/2),active:true,received:0,message:'正在连接直播页面'};
   await save(active);
   try {
     await chrome.scripting.executeScript({target:{tabId:tab.id},files:['dom-reader.js','content.js']});
     await chrome.tabs.sendMessage(tab.id,{type:'LC_BEGIN',revision:active.revision});
   } catch(error) {await stop(active,'无法读取当前页面，请刷新后重新开始');throw error;}
   await chrome.storage.local.set({prefs:{server,avatarId,token:config.token,welcome:config.welcome,reply:config.reply,thanks:config.thanks}});
   await chrome.alarms.create('collector-heartbeat',{periodInMinutes:1});
   return {ok:true};
 }
 throw new Error('未知操作');
}
chrome.runtime.onMessage.addListener((message,sender,respond)=>{
 if(sender.id!==chrome.runtime.id) return false;
 handle(message,sender).then(respond).catch(error=>respond({ok:false,error:error.message}));return true;
});
chrome.tabs.onRemoved.addListener(tabId=>{state().then(config=>{if(config?.active && config.tabId===tabId) return stop(config,'直播页面已关闭');});});
chrome.tabs.onUpdated.addListener((tabId,change)=>{if(change.status==='loading') state().then(config=>{if(config?.active && config.tabId===tabId) return stop(config,'页面已刷新，请重新开始采集');});});
chrome.alarms.onAlarm.addListener(async alarm=>{
 if(alarm.name!=='collector-heartbeat') return;
 const config=await state();if(!config?.active) return;
 try {const pong=await chrome.tabs.sendMessage(config.tabId,{type:'LC_PING'});if(!pong?.active) throw new Error();}
 catch {await stop(config,'页面连接已断开，请重新开始');return;}
 // The content script sends events/heartbeats. No historical events are replayed here.
});
