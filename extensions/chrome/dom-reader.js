/* Runs in the extension's isolated world. No cookies, page scripts or inputs are read. */
(() => {
  if (globalThis.LingCastDOM) return;
  let observer, timer, baselineUntil, epoch, sequence, seen, pending, events;
  const selector = '.msg-wrapper, .enter-msgs .msg-content';
  const parse = node => {
    if (node.matches('.enter-msgs .msg-content') && node.closest('.msg-wrapper')) return null;
    if (node.matches('.role-host, .role-system') || node.closest('.role-host')) return null;
    const nickname = node.querySelector('.nickname')?.textContent?.trim();
    if (!nickname) return null;
    if (node.closest('.enter-msgs') && /来了\s*$/.test(node.textContent)) return {kind:'enter',nickname,text:''};
    const copyText = element => {
      const copy = element.cloneNode(true);
      copy.querySelectorAll('img[alt]').forEach(image => image.replaceWith(image.alt));
      return copy.textContent.trim();
    };
    // Only structured notification rows; ordinary user chat must stay chat.
    const body = copyText(node).replace(new RegExp('^'+nickname.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), '').trim();
    if (!node.querySelector('.content') || node.matches('.role-gift, .role-like')) {
      if (/^(?:为主播)?点赞了[！!。]?$/u.test(body.replace(/\s+/g,''))) return {kind:'like',nickname,text:''};
      const gift = body.match(/^送出\s*(.{1,40}?)\s*[x×]\s*([1-9]\d{0,3})$/u);
      if (gift) return {kind:'gift',nickname,text:`${gift[1].trim()} x${gift[2]}`};
    }
    if (node.matches('.role-notice') || !node.matches('.msg-wrapper.role-user')) return null;
    const content = node.querySelector('.content');
    if (!content) return null;
    const copy = content.cloneNode(true);
    copy.querySelectorAll('img[alt]').forEach(image => image.replaceWith(image.alt));
    const text = copy.textContent.trim();
    return text ? {kind:'message',nickname,text} : null;
  };
  const inspect = (node, baseline = false) => {
    const event = parse(node); if (!event) return;
    const signature = JSON.stringify(event);
    if (seen.get(node) === signature) return;
    seen.set(node, signature);
    if (!baseline && Date.now() >= baselineUntil) {
      events.push({...event,id:`${epoch}-${++sequence}`,observed_ms:Date.now()});
      if (events.length > 100) events.shift();
    }
  };
  function stop() { observer?.disconnect(); clearTimeout(timer); timer=null; events=[]; pending?.clear(); }
  function start() {
    stop(); epoch=crypto.randomUUID(); sequence=0; seen=new WeakMap(); pending=new Set(); events=[];
    baselineUntil=Date.now()+2000;
    document.querySelectorAll(selector).forEach(node=>inspect(node,true));
    observer=new MutationObserver(records=>{
      for (const record of records) {
        const target=record.target.nodeType===Node.ELEMENT_NODE ? record.target : record.target.parentElement;
        const wrapper=target?.closest('.msg-wrapper') || target?.closest('.enter-msgs .msg-content'); if(wrapper) pending.add(wrapper);
        record.addedNodes.forEach(node=>{
          if(node.nodeType!==Node.ELEMENT_NODE) return;
          if(node.matches(selector)) pending.add(node);
          node.querySelectorAll(selector).forEach(child=>pending.add(child));
        });
      }
      if (!timer) timer=setTimeout(()=>{
        timer=null;
        pending.forEach(node=>{if(node.isConnected) inspect(node);}); pending.clear();
      },150);
    });
    observer.observe(document.documentElement,{childList:true,subtree:true,characterData:true});
  }
  globalThis.LingCastDOM={start,stop,drain:()=>events.splice(0,20),
    status:()=>document.querySelector('#live-layout #input-area, .msg-content, .msg-wrapper') ? 'listening' : 'waiting_dom'};
})();
