import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
// Source is copied into a data: module so the extension does not need package.json.
const source=fs.readFileSync(new URL('../protocol.js',import.meta.url),'utf8');
const {SERVER_ORIGIN,roomURL,normalizeEvents}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
test('fixed production server and real live-room URLs',()=>{
 assert.equal(SERVER_ORIGIN,'http://127.0.0.1:59480');
 assert.equal(roomURL('https://xiaohongshu.com/livestream/123?xsec_token=private'),'https://www.xiaohongshu.com/livestream/123');
 for(const url of ['https://xhslink.com/o/123','https://www.xiaohongshu.com.evil.test/livestream/123','https://www.xiaohongshu.com/explore/123','http://www.xiaohongshu.com/livestream/123']) assert.throws(()=>roomURL(url));
});
test('bounded normalized messages, clock offset, stale messages discarded',()=>{
 const event={id:'new-1',kind:'message',nickname:' 小雨 ',text:'你好😀',observed_ms:10000};
 assert.deepEqual(normalizeEvents([event],500,11000),[{...event,nickname:'小雨',observed_ms:10500}]);
 assert.deepEqual(normalizeEvents([event],0,41000),[]);
 assert.deepEqual(normalizeEvents([{...event,kind:'gift'}],0,11000),[]);
 assert.throws(()=>normalizeEvents(new Array(21).fill(event),0,11000));
 assert.equal([...normalizeEvents([{...event,nickname:'雨'.repeat(60),text:'😀'.repeat(400)}],0,11000)[0].text].length,300);
});
test('manifest limits reading to user action, no cookie/network interception permission',()=>{
 const manifest=JSON.parse(fs.readFileSync(new URL('../manifest.json',import.meta.url)));
 assert.deepEqual(manifest.permissions,['activeTab','scripting','storage','alarms']);
 assert.equal(manifest.content_scripts,undefined);
 assert.equal(manifest.host_permissions,undefined);
 assert.equal(manifest.externally_connectable,undefined);
 assert.deepEqual(manifest.optional_host_permissions,['http://127.0.0.1/*']);
 const popup=fs.readFileSync(new URL('../popup.html',import.meta.url),'utf8');
 assert.match(popup, /id="server"[^>]*readonly[^>]*value="http:\/\/112\.19\.174\.43:59480"/);
});
