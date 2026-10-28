import {serverOrigin} from './protocol.js';
const $=id=>document.getElementById(id);
const send=async message=>{const result=await chrome.runtime.sendMessage(message);if(!result?.ok) throw new Error(result?.error || '插件连接失败');return result;};
let initialized=false;
async function refresh() {
 try {
  const {prefs,status}=await send({type:'LC_STATUS'});
  if(!initialized) {for(const id of ['server','token']) $(id).value=prefs[id] || '';for(const id of ['welcome','reply']) $(id).checked=!!prefs[id];initialized=true;}
  $('status').textContent=status ? `${status.message || (status.active?'正在采集':'已停止')} · 已接收 ${status.received || 0} 条${status.room?'\n'+status.room:''}` : '尚未开始';
  $('stop').disabled=!status?.active;
 } catch(error) {$('error').textContent=error.message;}
}
$('form').addEventListener('submit',async event=>{
 event.preventDefault();$('error').textContent='';$('start').disabled=true;
 try {
  const server=serverOrigin($('server').value);
  const origin=new URL(server);
  // Permission is requested only for the explicitly configured LingCast host.
  const granted=await chrome.permissions.request({origins:[`${origin.protocol}//${origin.hostname}/*`]});
  if(!granted) throw new Error('未授权连接这个 LingCast 服务器');
  const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
  await send({type:'LC_START',server,token:$('token').value.trim(),tabId:tab.id,welcome:$('welcome').checked,reply:$('reply').checked});
  await refresh();
 } catch(error) {$('error').textContent=error.message;} finally {$('start').disabled=false;}
});
$('stop').addEventListener('click',async()=>{try{await send({type:'LC_STOP'});await refresh();}catch(error){$('error').textContent=error.message;}});
void refresh();setInterval(refresh,2000);
