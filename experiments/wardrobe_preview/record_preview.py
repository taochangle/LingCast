"""Record the real Watchdog wardrobe queue locally; no Redis/RTMP/model calls."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import queue
import os,hashlib,shutil
from unittest.mock import patch

REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO/'worker'))
import cv2
import numpy as np
from stream_worker import LiveAvatarSession
from ai.wardrobe import validate_transition


def frames(path):
    cap=cv2.VideoCapture(str(path));out=[]
    try:
        while True:
            ok,frame=cap.read()
            if not ok:break
            out.append(frame)
    finally:cap.release()
    if not out:raise ValueError('No frames: '+str(path))
    return out


def decode(path,speech):
    validate_transition(path,576,1024,25,speech=speech)
    images=frames(path);length=len(images)*1280
    pcm=(subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-vn','-f','s16le','-ar','16000','-ac','1','pipe:1'])
         if speech else b'')
    return images,pcm[:length].ljust(length,b'\0')


def record(source,target,assets,speech,output,with_hint=False,through_session=False):
    listing=output/'motion-parts.txt'
    listing.write_text(''.join(f"file '{p}'\n" for p in [assets/source/'exit.mp4',assets/'empty-loop.mp4',assets/target/'enter.mp4']))
    motion=output/f'{source}-to-{target}-motion.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','concat','-safe','0','-i',str(listing),'-an','-vf','fps=25','-c:v','libx264','-crf','18','-pix_fmt','yuv420p',str(motion)],check=True)
    decoded=None if through_session else {'intro':decode(speech/f'{source}-intro.mp4',True),'motion':decode(motion,False),'outro':decode(speech/f'{target}-outro.mp4',True)}
    old=frames(assets/source/'idle.mp4');new=[] if through_session else frames(assets/target/'idle.mp4')
    prefix=f'{source}-to-{target}'+('-with-host-hint' if with_hint else '')
    video=output/(prefix+'-raw.mp4');audio=output/(prefix+'.pcm')
    writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'mp4v'),25,(576,1024))
    if not writer.isOpened():raise RuntimeError('Video writer failed')
    pcm=audio.open('wb');count=[0];events=[];errors=[]
    s=LiveAvatarSession.__new__(LiveAvatarSession)
    s.avatar_id=3;s.stream_id='local-'+prefix;s.fps=25;s._running=True;s._dead=False
    s._talk_queue=queue.Queue(maxsize=75);s._idle_lock=threading.Lock()
    s._last_watchdog_warn=0;s._subtitle_text='';s._wardrobe_visual_hold=None;s._talk_is_action=True
    s._wardrobe_current=source;s.resident=source+'-renderer'
    s.idle_clips=[old];s._idle_frame_idx=0;s._idle_clip_idx=0;s._idle_clip_keys=[f'assets/{source}/idle.mp4']
    class Pipe:
        def write_audio(self,data):pcm.write(data)
        def stop(self):pass
    s.pipe=Pipe()
    def idle():
        with s._idle_lock:
            sequence=s.idle_clips[0]
            frame=sequence[s._idle_frame_idx%len(sequence)]
            s._idle_frame_idx+=1
            return frame
    def state(field,value,**kwargs):
        if field in ('wardrobe','wardrobe_phase'):
            events.append({'frame':count[0],'time':round(count[0]/25,3),'field':field,'value':value})
    def write(frame):writer.write(frame);count[0]+=1
    s._idle_frame=idle;s._state=state;s._write_frame=write
    commit={'frames':new,'key':target+'/idle.mp4','renderer':target+'-renderer','look':target}
    thread=threading.Thread(target=s._writer_loop);thread.start()
    try:
        time.sleep(1)
        def produce():
            if with_hint:s._queue_wardrobe_clip(*decode(speech/'host-hint-selected.mp4',True),'host_hint')
            s._queue_wardrobe_show(decoded,commit)
        if through_session:
            from ai.musetalk_client import MuseRenderer as OriginalRenderer
            from ai.wardrobe_profiles import LOOK_RENDERERS
            import stream_worker
            sha=lambda path:hashlib.sha256(Path(path).read_bytes()).hexdigest()
            roles={look:{'renderer_id':LOOK_RENDERERS[look],'idle_key':f'assets/{look}/idle.mp4',
                         'idle_sha256':sha(assets/look/'idle.mp4')} for look in (source,target)}
            transition={}
            for phase,look in [('intro',source),('motion',None),('outro',target)]:
                path=motion if phase=='motion' else speech/f'{look}-{phase}.mp4'
                item={'media_key':str(path.relative_to(output)),'sha256':sha(path)}
                if look:item.update(renderer_id=LOOK_RENDERERS[look],base_key=roles[look]['idle_key'])
                transition[phase]=item
            manifest={'enabled':True,'avatar_id':3,'initial':source,'order':[source,target],'looks':roles,
                      'transitions':{source+':'+target:transition}}
            full=output/'local-test-manifest.json'
            if full.exists():
                candidate=json.loads(full.read_text())
                cycle=candidate['order'];i=cycle.index(source)
                if cycle[(i+1)%len(cycle)]==target:manifest=candidate;roles=candidate['looks']
            manifest_path=output/(prefix+'-session-test.json');manifest_path.write_text(json.dumps(manifest))
            class LocalRenderer(OriginalRenderer):
                def __init__(self,avatar):
                    self.avatar_id=avatar
                    self.prepared_avatars={str(v['renderer_id']):{'base_key':v['idle_key'],'input_sha256':v['idle_sha256']} for v in roles.values()}
                def warmup(self,key):
                    assert self.prepared_avatars[str(self.avatar_id)]['base_key']==key
            class LocalStorage:
                def download(self,key,destination):
                    path=(output/key).resolve()
                    if not path.is_relative_to(output):raise ValueError('Path outside local preview')
                    shutil.copyfile(path,destination)
            playback={'playback':'idle'}
            class Redis:
                def get(self,key):return json.dumps({'enabled':True,'reply':True,'revision':1}) if key=='live_xhs_config:3' else None
                def hgetall(self,key):return playback
            class Host:
                def tick(self,*args,**kwargs):return None
                def close(self):pass
            previous_state=s._state
            def local_state(field,value,**kwargs):
                if field=='playback':playback['playback']=value
                previous_state(field,value,**kwargs)
            s._state=local_state;s.resident=LocalRenderer(LOOK_RENDERERS[source]);s.storage=LocalStorage()
            s.work_dir=output;s.base_frames=[old[0]];s._redis=Redis();s.queue_key='unused-local-queue'
            s._muse_play_lock=threading.Lock();s._pending=queue.Queue();s._tts_results=queue.Queue()
            s._produce_thread=None;s._tts_thread=None;s._last_health_log=time.monotonic()
            s._wardrobe_active=False;s._wardrobe_pending={'action':'wardrobe','xhs_revision':1,'ts':time.time()*1000}
            s._host_director=Host()
            if with_hint:
                s._tts_thread=threading.Thread(target=s._queue_wardrobe_clip,args=(*decode(speech/'host-hint-selected.mp4',True),'host_hint'))
                s._tts_thread.start()
            with patch.object(stream_worker,'MuseRenderer',LocalRenderer),patch.dict(os.environ,{'WARDROBE_MANIFEST':str(manifest_path)}):
                deadline=time.monotonic()+65
                while s._running and (s._wardrobe_pending is not None or s._wardrobe_active):
                    s.tick(s._redis)
                    if time.monotonic()>deadline:raise RuntimeError('Local session playback timeout')
            if s._wardrobe_current!=target:raise RuntimeError('Session did not commit new outfit')
        else:
            producer=threading.Thread(target=produce);producer.start()
            producer.join(55)
            if producer.is_alive():raise RuntimeError('Playback barrier timeout')
        time.sleep(1)
    finally:
        s._running=False;thread.join(3);writer.release();pcm.close()
    if s._dead or thread.is_alive():raise RuntimeError('Watchdog recording failed')
    result=output/(prefix+'.mp4')
    subprocess.run(['ffmpeg','-v','error','-y','-i',str(video),'-f','s16le','-ar','16000','-ac','1','-i',str(audio),'-map','0:v:0','-map','1:a:0','-c:v','libx264','-crf','18','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart',str(result)],check=True)
    start=next(e['frame'] for e in events if e['field']=='wardrobe_phase' and e['value']=='motion')
    commit_frame=next(e['frame'] for e in events if e['field']=='wardrobe')
    outro=next(e['frame'] for e in events if e['field']=='wardrobe_phase' and e['value']=='outro')
    assert outro>commit_frame,'No new idle frame before outro'
    raw=audio.read_bytes()
    assert not any(raw[start*1280:commit_frame*1280]),'Audio during movement'
    outro_frames=(len(decoded['outro'][0]) if decoded is not None else int(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=nb_frames','-of','csv=p=0',str(speech/f'{target}-outro.mp4')],text=True).strip()))
    def duration(path):
        return float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',str(path)],text=True).strip())
    report={'through_session':through_session,'file_storage_and_resident_adapter':through_session,'host_hint':with_hint,'simulated_viewer_request':with_hint,'outro_end':round((outro+outro_frames)/25,3),'exit_end':round(start/25+duration(assets/source/'exit.mp4'),3),'empty_end':round(start/25+duration(assets/source/'exit.mp4')+duration(assets/'empty-loop.mp4'),3),'file':result.name,'source':source,'target':target,'frames':count[0],'duration':round(count[0]/25,3),
            'events':events,'motion_zero_pcm':True,'idle_before_outro':True,'actual_watchdog':True}
    video.unlink();audio.unlink();(output/(prefix+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--assets',type=Path,required=True);parser.add_argument('--speech',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--order',default='white,blue');parser.add_argument('--hint-demo',action='store_true');parser.add_argument('--through-session',action='store_true')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    order=args.order.split(',')
    if len(order)<2 or len(set(order))!=len(order) or set(order)-{'white','blue','burgundy','champagne'}:raise ValueError('Invalid preview order')
    if args.hint_demo:
        record(order[0],order[1],args.assets.resolve(),args.speech.resolve(),args.output.resolve(),with_hint=True,through_session=args.through_session)
        return
    reports=[record(a,b,args.assets.resolve(),args.speech.resolve(),args.output.resolve(),through_session=args.through_session) for a,b in zip(order,order[1:]+order[:1])]
    (args.output/'recordings.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2));print('RECORDED both directions with actual Watchdog',flush=True)

if __name__=='__main__':main()
