import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
test('late acknowledgement from an old session cannot stop a restarted reader',async()=>{
 let listener,flush,resolveUpload;
 const active=new Set();let nextTimer=0;
 const context={location:{origin:'https://www.xiaohongshu.com',pathname:'/livestream/123'},Date,
  setInterval:fn=>{flush=fn;active.add(++nextTimer);return nextTimer;},clearInterval:id=>active.delete(id),
  LingCastDOM:{start(){},stop(){},drain:()=>[],status:()=> 'listening'},
  chrome:{runtime:{onMessage:{addListener:fn=>listener=fn},sendMessage:()=>new Promise(resolve=>resolveUpload=resolve)}}};
 vm.runInNewContext(fs.readFileSync(new URL('../content.js',import.meta.url),'utf8'),context);
 listener({type:'LC_BEGIN',revision:1},{},()=>{});
 const pending=flush();
 listener({type:'LC_BEGIN',revision:2},{},()=>{});
 resolveUpload({ok:false,stop:true});await pending;
 let ping;listener({type:'LC_PING'},{},value=>ping=value);
 assert.equal(ping.active,true);assert.equal(active.size,1);
});
