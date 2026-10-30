import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
const id='a'.repeat(32),origin='chrome-extension://'+id+'/';
const session={},local={},calls=[];let listener,updated,removed,revision=100;
const store=object=>({setAccessLevel:async()=>{},get:async key=>({[key]:object[key]}),set:async value=>Object.assign(object,value)});
globalThis.chrome={storage:{session:store(session),local:store(local)},
 runtime:{id,getURL:path=>origin+path,onMessage:{addListener:fn=>listener=fn}},
 tabs:{get:async tabId=>({id:tabId,url:'https://www.xiaohongshu.com/livestream/123?xsec_token=private'}),
 sendMessage:async()=>({ok:true,active:true}),onUpdated:{addListener:fn=>updated=fn},onRemoved:{addListener:fn=>removed=fn}},
 scripting:{executeScript:async()=>{}},alarms:{create:async()=>{},onAlarm:{addListener:()=>{}}}};
globalThis.fetch=async(url,options)=>{
 calls.push({url,options,body:JSON.parse(options.body)});
 return {ok:true,json:async()=>url.endsWith('/session')?{revision:++revision,server_ms:Date.now()}:{received:1}};
};
const protocol='data:text/javascript;base64,'+Buffer.from(fs.readFileSync(new URL('../protocol.js',import.meta.url))).toString('base64');
const script=fs.readFileSync(new URL('../background.js',import.meta.url),'utf8').replace("'./protocol.js'",JSON.stringify(protocol));
await import('data:text/javascript;base64,'+Buffer.from(script).toString('base64'));
const popup={id,url:origin+'popup.html'};
const content={id,url:'https://www.xiaohongshu.com/livestream/123',tab:{id:1},frameId:0};
const send=(message,sender=popup)=>new Promise(resolve=>{if(listener(message,sender,resolve)===false) resolve(null);});
const start=()=>send({type:'LC_START',server:'https://collector.example.invalid',token:'lc_2_'+'f'.repeat(64),tabId:1,welcome:false,reply:false});
test('scoped transport survives worker idle state and rejects content reconfiguration',async()=>{
 assert.equal((await start()).ok,true);
 assert.equal(calls[0].options.credentials,'omit');
 assert.equal(calls[0].body.url,'https://www.xiaohongshu.com/livestream/123');
 const blocked=await send({type:'LC_START',server:'http://evil.test',token:'stolen'},content);
 assert.equal(blocked.ok,false);
 const result=await send({type:'LC_EVENTS',revision:session.collector.revision,page_room:content.url,events:[{id:'event-1',kind:'message',nickname:'小雨',text:'你好',observed_ms:Date.now()}],server:'http://evil.test',dom:'listening'},content);
 assert.equal(result.ok,true);
 assert.equal(calls.at(-1).url,'https://collector.example.invalid/api/collector/events');
 assert.equal((await send({type:'LC_EVENTS',revision:session.collector.revision,page_room:content.url,events:[]},{...content,tab:{id:9}})).stop,true);
 assert.equal((await send({type:'LC_EVENTS',revision:session.collector.revision-1,events:[]},content)).stop,true);
 const status=await send({type:'LC_STATUS'});assert.equal(status.status.token,undefined);
 assert.equal((await send({type:'LC_STATUS'},content)).ok,false);
});
test('reload and close stop capture rather than silently replaying history',async()=>{
 updated(1,{status:'loading'});
 await new Promise(resolve=>setTimeout(resolve,10));
 assert.equal(session.collector.active,false);
 assert.equal((await start()).ok,true);
 removed(1);await new Promise(resolve=>setTimeout(resolve,10));
 assert.equal(session.collector.active,false);
});

test('same-tab SPA room changes fail closed before upload',async()=>{
 assert.equal((await start()).ok,true);
 const result=await send({type:'LC_EVENTS',revision:session.collector.revision,page_room:'https://www.xiaohongshu.com/livestream/456',events:[]},content);
 assert.equal(result.stop,true);assert.equal(session.collector.active,false);
});
test('public default passwords are rejected',async()=>{
 assert.equal((await send({type:'LC_START',token:'invalid-shared-key',avatarId:2,tabId:1})).ok,false);
});

test('edited server persists locally and only selected server receives events',async()=>{
 const saved=await send({type:'LC_SAVE_SERVER',server:'https://new-collector.example.invalid:8443/'});
 assert.equal(saved.ok,true);assert.equal(saved.changed,true);
 const status=await send({type:'LC_STATUS'});
 assert.equal(status.prefs.server,'https://new-collector.example.invalid:8443');
 assert.equal(status.prefs.token,'');
 const token='lc_2_'+'e'.repeat(64);
 assert.equal((await send({type:'LC_START',server:status.prefs.server,token,avatarId:2,tabId:1})).ok,true);
 assert.equal(calls.at(-1).url,'https://new-collector.example.invalid:8443/api/collector/session');
 assert.equal((await send({type:'LC_SAVE_SERVER',server:'https://evil.test'},content)).ok,false);
 assert.equal((await send({type:'LC_SAVE_SERVER',server:'https://new-collector.example.invalid/path'})).ok,false);
 assert.equal((await send({type:'LC_STATUS'})).prefs.server,'https://new-collector.example.invalid:8443');
});
