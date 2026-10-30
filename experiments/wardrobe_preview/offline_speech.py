"""Isolated GPU job for local preview; no live queues/config/service lifecycle."""
import base64
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import wave

ROOT=Path('/data1/taoxin/lingcast-wardrobe-local-test/20261030')
REFERENCE=Path('/data1/taoxin/lingcast-musetalk-bench')
SERVICES=['lingcast-musetalk','lingcast-cosyvoice','lingcast-stream']
def pids():
 return {s:subprocess.check_output(['systemctl','--user','show',s,'-p','MainPID','--value'],text=True).strip() for s in SERVICES}
report={'state':'starting','pids_before':pids(),'outputs':[]}
def save():
 (ROOT/'status.json').write_text(json.dumps(report,indent=2))
save()
try:
 free=int(subprocess.check_output(['nvidia-smi','--id=2','--query-gpu=memory.free','--format=csv,noheader,nounits'],text=True).strip())
 if free<16000:raise RuntimeError('GPU2 too busy for isolated job')
 target=ROOT/'source'
 if not target.exists():
  shutil.copytree(REFERENCE/'source',target,ignore=lambda directory,names:[name for name in names if name in {'.git','__pycache__','.venv'} or (Path(directory)==REFERENCE/'source' and name in {'models','results'})])
  (target/'models').symlink_to(REFERENCE/'source/models',target_is_directory=True)
 (ROOT/'inputs').mkdir(exist_ok=True);(ROOT/'output').mkdir(exist_ok=True)
 os.environ.update(MUSETALK_ROOT=str(ROOT),MUSETALK_GPU='2',HF_HOME=str(REFERENCE/'hf-cache'),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',OMP_NUM_THREADS='4')
 sys.path.insert(0,str(ROOT/'code'))
 import numpy as np
 import requests
 import soundfile as sf
 from scipy.signal import resample_poly
 from ai.audio_windows import AudioWindows
 speeches={}
 for phase,text in [('intro','好呀，我去换件衣服，等我一下。'),('outro','我回来啦，这套怎么样？'),('hint','想看我换套穿搭，可以在评论里发“换衣服”。我会说完这一段，再去换好回来。')]:
  path=ROOT/'inputs'/f'{phase}.wav'
  if not path.exists():
   parts=[];rate=None;done=False
   for retry in range(60):
    response=requests.post('http://127.0.0.1:59495/speech',json={'text':text},stream=True,timeout=(5,90))
    if response.status_code not in (409,503):break
    response.close();time.sleep(1)
   with response:
    response.raise_for_status()
    for line in response.iter_lines():
     if not line:continue
     item=json.loads(line)
     if item.get('error'):raise RuntimeError(item['error'])
     if item.get('done'):done=True;break
     current=int(item['sample_rate'])
     if rate is not None and rate!=current:raise RuntimeError('Sample rate changed')
     rate=current;parts.append(np.frombuffer(base64.b64decode(item['pcm']),dtype='<f4').copy())
   if not done or not parts:raise RuntimeError('Incomplete voice synthesis')
   sf.write(path,np.concatenate(parts),rate,subtype='FLOAT')
  speeches[phase]=sf.read(path,dtype='float32')
  print('VOICE_READY',phase,flush=True)
 report['state']='loading_isolated_model';save()
 from ai.musetalk_runtime import m,StreamingMuse
 import cv2
 runtime=StreamingMuse.__new__(StreamingMuse);runtime.avatars={}
 for look in os.environ.get('WARDROBE_PREVIEW_LOOKS','white,blue').split(','):
  if look not in ('white','blue','burgundy','champagne'):raise ValueError('Unknown preview look')
  name='wardrobe_preview_'+look
  source=ROOT/'assets'/look/'idle.mp4'
  report['state']='preparing_'+look;save()
  print('PREPARE',look,flush=True)
  profile=ROOT/'source/results/v15/avatars'/('bench_'+name)
  runtime.avatars[name]=m.Avatar('bench_'+name,str(source),0,8,not profile.exists())
  for phase,(pcm,rate) in speeches.items():
   existing=ROOT/'output'/f'{look}-{phase}.mp4'
   if existing.exists():
    report['outputs'].append({'look':look,'phase':phase,'file':existing.name,'reused':True});save();continue
   report['state']='rendering_'+look+'_'+phase;save()
   temp=ROOT/'output'/f'{look}-{phase}-silent.mp4'
   writer=cv2.VideoWriter(str(temp),cv2.VideoWriter_fourcc(*'mp4v'),25,(576,1024))
   if not writer.isOpened():raise RuntimeError('Video writer failed')
   assembler=AudioWindows(rate);audio=[];offset=0
   try:
    for core,window,left in assembler.feed(pcm,final=True):
     count=math.ceil(len(core)/rate*25);emitted=[0]
     wav=ROOT/'inputs/window.wav';sf.write(wav,window,rate,subtype='PCM_16')
     def frame(image,number):
      if left<=number<left+count:
       if image.shape[:2]!=(1024,576):raise RuntimeError('Frame shape changed')
       writer.write(image);emitted[0]+=1
     runtime.render(name,wav,frame,frame_offset=offset-left)
     if emitted[0]!=count:raise RuntimeError('Incomplete face window')
     divisor=math.gcd(rate,16000)
     samples=resample_poly(core,16000//divisor,rate//divisor)
     samples=np.pad(samples,(0,max(0,count*640-len(samples))))[:count*640]
     audio.append(samples);offset+=count
   finally:writer.release()
   audiofile=ROOT/'output'/f'{look}-{phase}.wav';sf.write(audiofile,np.concatenate(audio),16000,subtype='PCM_16')
   out=ROOT/'output'/f'{look}-{phase}.mp4'
   subprocess.run(['ffmpeg','-v','error','-y','-i',str(temp),'-i',str(audiofile),'-map','0:v:0','-map','1:a:0','-c:v','libx264','-crf','18','-pix_fmt','yuv420p','-c:a','aac','-ar','16000','-ac','1',str(out)],check=True)
   report['outputs'].append({'look':look,'phase':phase,'file':out.name,'frames':offset,'duration':offset/25});save()
   print('SPEECH_READY',look,phase,offset,flush=True)
 report['pids_after']=pids()
 assert report['pids_after']==report['pids_before'],'Existing process changed'
 report['state']='complete';save()
 print('COMPLETE isolated job; existing model/worker PIDs unchanged',flush=True)
except BaseException as error:
 report['state']='failed';report['error_type']=type(error).__name__;save()
 raise
