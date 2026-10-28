(() => {
  if(globalThis.LingCastContent) return;
  globalThis.LingCastContent=true;
  let timer, revision, waiting=false, lastSent=0, retry=[];
  function stop() { clearInterval(timer);timer=null;retry=[];globalThis.LingCastDOM.stop(); }
  async function flush() {
    if(waiting || !timer) return;
    retry=retry.filter(e=>Date.now()-e.observed_ms<25000);
    retry.push(...globalThis.LingCastDOM.drain()); retry=retry.slice(-100);
    if(!retry.length && Date.now()-lastSent<10000) return;
    waiting=true;
    const batch=retry.slice(0,20);
    try {
      const result=await chrome.runtime.sendMessage({type:'LC_EVENTS',revision,events:batch,dom:globalThis.LingCastDOM.status()});
      if(result?.ok) { retry.splice(0,batch.length);lastSent=Date.now(); }
      if(result?.stop) stop();
    } catch { stop(); } finally { waiting=false; }
  }
  chrome.runtime.onMessage.addListener((message,_sender,respond)=>{
    if(message.type==='LC_BEGIN') {
      stop();revision=message.revision;globalThis.LingCastDOM.start();
      lastSent=0;timer=setInterval(flush,1000);respond({ok:true});
    } else if(message.type==='LC_STOP') {stop();respond({ok:true});}
    else if(message.type==='LC_PING') respond({active:!!timer});
  });
})();
