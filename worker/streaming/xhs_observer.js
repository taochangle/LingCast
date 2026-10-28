/* Only observes rendered messages. The initial DOM is deliberately a baseline. */
() => {
  if (window.__lingcastXHS) return;
  const state = {events: [], sequence: 0, epoch: crypto.randomUUID(), seen: new WeakMap()};
  window.__lingcastXHS = state;
  const parse = (node) => {
    if (!node.matches('.msg-wrapper.role-user') || node.matches('.role-host')) return null;
    const nickname = node.querySelector('.nickname')?.textContent?.trim();
    if (!nickname) return null;
    const content = node.querySelector('.content');
    if (content) {
      const copy = content.cloneNode(true);
      copy.querySelectorAll('img[alt]').forEach(image => image.replaceWith(image.alt));
      const text = copy.textContent.trim();
      if (!text) return null;
      return {kind:'message', nickname, text};
    }
    if (node.closest('.enter-msgs') && /来了\s*$/.test(node.textContent)) {
      return {kind:'enter', nickname, text:''};
    }
    return null; // likes, gifts, shares and notifications are not questions
  };
  const inspect = (node, baseline=false) => {
    const event = parse(node);
    if (!event) return;
    const signature = JSON.stringify(event);
    if (state.seen.get(node) === signature) return;
    state.seen.set(node, signature);
    if (!baseline) {
      state.events.push({...event, id: `${state.epoch}-${++state.sequence}`, observed_ms:Date.now()});
      if (state.events.length > 200) state.events.shift();
    }
  };
  document.querySelectorAll('.msg-wrapper').forEach(node => inspect(node, true));
  const pending = new Set();
  let timer;
  new MutationObserver(records => {
    for (const record of records) {
      const target = record.target.nodeType === Node.ELEMENT_NODE ? record.target : record.target.parentElement;
      const wrapper = target?.closest('.msg-wrapper');
      if (wrapper) pending.add(wrapper);
      record.addedNodes.forEach(node => {
        if (node.nodeType !== Node.ELEMENT_NODE) return;
        if (node.matches('.msg-wrapper')) pending.add(node);
        node.querySelectorAll('.msg-wrapper').forEach(child => pending.add(child));
      });
    }
    clearTimeout(timer);
    timer = setTimeout(() => { pending.forEach(node => { if (node.isConnected) inspect(node); }); pending.clear(); }, 150);
  }).observe(document.documentElement, {childList:true, subtree:true, characterData:true});
}
