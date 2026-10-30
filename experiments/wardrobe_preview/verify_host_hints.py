"""Local HostDirector integration test with file-cache adapter and simulated time.
Normal paragraph files are timing fixtures, never shown. The selected reminder
is a real CosyVoice/MuseTalk clip. Does not contact services or use live Redis.
"""
from concurrent.futures import Future
import hashlib,json
from pathlib import Path
import shutil,sys,tempfile
from types import SimpleNamespace
from unittest.mock import patch

REPO=Path(__file__).resolve().parents[2];sys.path.insert(0,str(REPO/'worker'))
from ai.host_director import HostDirector
from ai.resident_client import CompletedClip
from ai.wardrobe import host_wardrobe_ready
from ai.wardrobe_profiles import LOOK_RENDERERS

HINT='想看我换套穿搭，可以在评论里发“换衣服”。我会说完这一段，再去换好回来。'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
class Executor:
    def __init__(self,**kwargs):pass
    def submit(self,fn):
        future=Future()
        try:future.set_result(fn())
        except Exception as e:future.set_exception(e)
        return future
    def shutdown(self,**kwargs):pass


def verify(root,order=None):
    assets=root/'assets';speech=root/'speech';order=order or ['white','burgundy','champagne']
    data={'enabled':True,'avatar_id':3,'initial':'white','order':order,'looks':{},'transitions':{}}
    snapshots={}
    for look in order:
        path=assets/look/'idle.mp4';checksum=sha(path);key=f'assets/{look}/idle.mp4'
        data['looks'][look]={'renderer_id':LOOK_RENDERERS[look],'idle_key':key,'idle_sha256':checksum}
        snapshots[str(LOOK_RENDERERS[look])]={'base_key':key,'input_sha256':checksum}
    for a,b in zip(order,order[1:]+order[:1]):
        phases={}
        for phase,look in [('intro',a),('motion',None),('outro',b)]:
            path=root/f'{a}-to-{b}-motion.mp4' if phase=='motion' else speech/f'{look}-{phase}.mp4'
            entry={'media_key':str(path.relative_to(root)),'sha256':sha(path)}
            if look:entry.update(renderer_id=LOOK_RENDERERS[look],base_key=data['looks'][look]['idle_key'])
            phases[phase]=entry
        data['transitions'][a+':'+b]=phases
    manifest=root/'local-test-manifest.json';manifest.write_text(json.dumps(data,indent=2))
    prepared=SimpleNamespace(prepared_avatars=snapshots)
    collector={'enabled':True,'reply':True}
    assert host_wardrobe_ready(manifest,3,prepared,collector)
    broken=json.loads(json.dumps(data));broken['transitions'][order[-1]+':'+order[0]]['motion']['sha256']=''
    manifest.write_text(json.dumps(broken))
    assert not host_wardrobe_ready(manifest,3,prepared,collector)
    manifest.write_text(json.dumps(data,indent=2))
    assert not host_wardrobe_ready(manifest,3,prepared,{'enabled':True,'reply':False})
    missing=SimpleNamespace(prepared_avatars={k:v for k,v in snapshots.items() if k!=str(LOOK_RENDERERS[order[-1]])})
    assert not host_wardrobe_ready(manifest,3,missing,collector)
    objects={};clock=[0.0];selected=[]
    class Redis:
        def get(self,key):return objects.get(key)
        def set(self,key,value):objects[key]=value
    r=Redis()
    scripts=['普通主持计时样本一','普通主持计时样本二','普通主持计时样本三',HINT]
    config={'kind':'library','run_id':'local-hint-test','mode':'running','command':'1','scripts':scripts,
            'min_pause':0,'max_pause':0,'welcome_count':1,'wardrobe_enabled':True,'wardrobe_hint_indices':[3]}
    objects['live_host:3']=json.dumps(config)
    with tempfile.TemporaryDirectory() as tmp:
        class Renderer:
            def render(self,text,base,work):
                path=Path(work)/(hashlib.sha256(text.encode()).hexdigest()+'.mp4')
                if text==HINT:shutil.copyfile(speech/'white-hint.mp4',path)
                else:path.write_bytes(b'timing fixture; not played or shown')
                return CompletedClip(path,'local-cache',{'latent':{'audio_seconds':0}})
        with patch('ai.host_director.ThreadPoolExecutor',Executor),patch('ai.host_director.time.monotonic',side_effect=lambda:clock[0]):
            director=HostDirector(3)
            try:
                def tick(available=True,manual=False):
                    item=director.tick(r,Renderer(),'assets/white/idle.mp4',tmp,True,manual,wardrobe_available=available)
                    if item:selected.append(item[0])
                    return item
                for _ in range(4):assert tick() is None
                for _ in range(4):assert tick()[0]!=HINT
                clock[0]=239;assert tick()[0]!=HINT
                clock[0]=240;assert tick(available=False)[0]!=HINT
                assert tick(manual=True) is None
                item=tick();assert item[0]==HINT
                selected_path=speech/'host-hint-selected.mp4';shutil.copyfile(item[1].path,selected_path)
                item[1].path.unlink(missing_ok=True)
                for _ in range(4):assert tick()[0]!=HINT
                clock[0]=479;assert tick()[0]!=HINT
                clock[0]=480;assert tick()[0]==HINT
                config.update(mode='paused',command='pause');objects['live_host:3']=json.dumps(config)
                assert tick() is None
            finally:director.close()
    report={'tested_order':order,'passed':True,'normal_paragraphs_are_unplayed_fixtures':True,'clock_simulated':True,'minimum_interval_seconds':240,
            'missing_clip_descriptor_suppresses_hint':True,'missing_profile_suppresses_hint':True,'reply_disabled_suppresses_hint':True,'manual_priority':True,'pause_suppresses_hint':True,
            'real_selected_hint':HINT,'selected_hint_sha256':sha(selected_path),'not_auto_triggering_wardrobe':True}
    (root/'host-hint-test.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print('HOST_HINT_CHECKS_PASS; actual reminder clip selected; 4-minute clock simulated')

if __name__=='__main__':verify(Path(sys.argv[1]).resolve(),sys.argv[2].split(',') if len(sys.argv)>2 else None)
