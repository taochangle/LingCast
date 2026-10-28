/* Runs in the extension's isolated world. No cookies, page scripts or inputs are read. */
(() => {
  if (globalThis.LingCastDOM) return;
  let observer, timer, baselineUntil, epoch, sequence, seen, pending, events;
  const selector = '.msg-wrapper, .enter-msgs .msg-content';
  const parse = node => {
    if (node.matches('.enter-msgs .msg-content') && node.closest('.msg-wrapper')) return null;
    if (node.matches('.role-host, .role-system, .role-notice, .role-gift') || node.closest('.role-host')) return null;
    const nickname = node.querySelector('.nickname')?.textContent?.trim();
    if (!nickname) return null;
    if (node.closest('.enter-msgs') && /来了\s*$/.test(node.textContent)) return {kind:'enter',nickname,text:''};
    if (!node.matches('.msg-wrapper.role-user')) return null;
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
