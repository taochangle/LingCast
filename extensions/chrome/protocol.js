// Filled from private .env by package_chrome.py; never commit a real origin.
export const SERVER_ORIGIN = '';
export function serverOrigin(raw) {
  let url;
  try {url=new URL(raw);} catch {throw new Error('请填写有效的 LingCast 服务器地址');}
  if(!['http:','https:'].includes(url.protocol) || url.username || url.password || !['','/'].includes(url.pathname) || url.search || url.hash) throw new Error('服务器地址只填写 HTTP(S) 主机和端口，不带路径或密钥');
  return url.origin;
}
export function roomURL(raw) {
  const url = new URL(raw);
  const room = url.pathname.match(/^\/livestream\/(?:dynpath[A-Za-z0-9_-]+\/)?(\d+)\/?$/);
  if (url.protocol !== 'https:' || !['xiaohongshu.com', 'www.xiaohongshu.com'].includes(url.hostname) || url.port || url.username || url.password || !room) throw new Error('请先切到小红书直播间页面，再点击开始');
  return 'https://www.xiaohongshu.com/livestream/'+room[1];
}
export function normalizeEvents(events, offset, now = Date.now()) {
  if (!Array.isArray(events) || events.length > 20) throw new Error('消息批次无效');
  return events.filter(event => event && ['message', 'enter', 'like', 'gift'].includes(event.kind) &&
    typeof event.id === 'string' && event.id.length <= 100 &&
    typeof event.nickname === 'string' && event.nickname.trim() &&
    typeof event.text === 'string' && (event.kind !== 'gift' || /^.{1,60} x[1-9]\d{0,3}$/u.test(event.text)) && Number.isFinite(event.observed_ms) &&
    now-event.observed_ms >= -5000 && now-event.observed_ms <= 30000)
    .map(event => ({id:event.id,kind:event.kind,nickname:[...event.nickname.trim()].slice(0,40).join(''),
      text:[...event.text.trim()].slice(0,300).join(''),observed_ms:Math.round(event.observed_ms+offset)}));
}
