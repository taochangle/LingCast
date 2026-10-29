import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
// Unit fixtures for structured rows, not a claim about the platform's current DOM.
const source=fs.readFileSync(new URL('../dom-reader.js',import.meta.url),'utf8');
function parser(){
 const scope={};vm.runInNewContext(source.replace('  const inspect =','  globalThis.parseFixture = parse;\n  const inspect ='),scope);
 return scope.parseFixture;
}
function row(nickname,body,role='role-user',hasContent=false){
 const text={textContent:body,cloneNode(){return {textContent:body,querySelectorAll(){return []}}}};
 return {textContent:nickname+' '+body,
  matches(selector){return selector.split(',').some(s=>s.trim()===`.${role}` || s.trim()===`.msg-wrapper.${role}`)},
  closest(){return null},
  cloneNode(){return {textContent:nickname+' '+body,querySelectorAll(){return []}}},
  querySelector(selector){return selector==='.nickname'?{textContent:nickname}:selector==='.content'&&hasContent?text:null}};
}
test('recognizes screenshot-shaped like and gift notices without treating chat as gifts',()=>{
 const parse=parser();
 assert.equal(parse(row('Lynn','为主播点赞了')).kind,'like');
 assert.equal(parse(row('Lynn','为主播点赞了','role-notice')).kind,'like');
 const gift=parse(row('起个啥名字好','送出人气票 🐸 x1','role-gift'));
 assert.equal(gift.kind,'gift');assert.equal(gift.nickname,'起个啥名字好');
 assert.equal(parse(row('小雨','送出人气票 x1','role-user',true)).kind,'message');
 assert.equal(parse(row('主播','为主播点赞了','role-host')),null);
 assert.equal(parse(row('小雨','送出人气票 x0','role-gift')),null);
 assert.equal(parse(row('小雨','分享了直播间')),null);
});
