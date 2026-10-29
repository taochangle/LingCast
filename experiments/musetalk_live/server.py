"""Isolated LingCast streaming experiment. Loopback only; no live queues/control."""
import os,sys,json,time,threading,queue,uuid,base64,logging
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs
HERE=Path(__file__).resolve().parent
ROOT=Path('/data1/taoxin/lingcast-musetalk-bench')
for line in Path('/data1/taoxin/LingCast/worker/.env.local').read_text().splitlines():
 if line.strip() and not line.lstrip().startswith('#') and '=' in line:
  key,_,value=line.partition('=');os.environ.setdefault(key.strip(),value.strip().strip('\"\''))
for line in Path('/data1/taoxin/LingCast/.env').read_text().splitlines():
 if line.startswith(('V2_QWEN_API_KEY=','V2_QWEN_MODEL=')):
  key,_,value=line.partition('=');os.environ.setdefault(key,value.strip().strip('"\''))
os.environ['CUDA_VISIBLE_DEVICES']='2';os.environ['HF_HOME']=str(ROOT/'hf-cache');os.environ['TRANSFORMERS_OFFLINE']='1';os.environ['OMP_NUM_THREADS']='4'
sys.path.insert(0,'/data1/taoxin/LingCast/worker')
from storage import S3Storage
from ai.resident_client import ResidentRenderer
storage=S3Storage();tts=ResidentRenderer(storage)
ready=False;model=None;lock=threading.Lock();jobs={};jobs_lock=threading.Lock()
OUT=ROOT/'live-test';OUT.mkdir(exist_ok=True)

def load():
 global ready,model
 from model import StreamingMuse
 model=StreamingMuse();ready=True
threading.Thread(target=load,daemon=True).start()
def event(job,kind,**data):
 job['events'].put((kind,dict(elapsed_ms=(time.perf_counter()-job['start'])*1000,**data)),timeout=30)
def run(job):
 sentences=queue.Queue(maxsize=4);audio=queue.Queue(maxsize=2);stop=threading.Event()
 def put(target,item):
  while not stop.is_set():
   try:target.put(item,timeout=.5);return
   except queue.Full:pass
  raise RuntimeError('Test job stopped')
 def llm():
  try:
   if job['mode']=='direct':put(sentences,job['text'])
   else:
    from streaming_text import qwen_sentences
    def token(text):
     job['metrics'].setdefault('llm_first_token_ms',(time.perf_counter()-job['start'])*1000)
     event(job,'token',text=text)
    def done(text):
     job['metrics']['llm_done_ms']=(time.perf_counter()-job['start'])*1000
     (job['dir']/'reply.txt').write_text(text)
    for text in qwen_sentences(os.environ.get('QWEN_TEST_BASE','http://127.0.0.1:18003/v1'),os.environ.get('V2_QWEN_API_KEY',''),os.environ.get('V2_QWEN_MODEL','qwen35'),job['text'],token,done):
     job['metrics'].setdefault('llm_first_sentence_ms',(time.perf_counter()-job['start'])*1000)
     put(sentences,text)
   put(sentences,None)
  except Exception as error:put(sentences,error)
 def speech():
  try:
   index=0
   while True:
    text=sentences.get(timeout=65)
    if text is None:put(audio,None);return
    if isinstance(text,Exception):raise text
    folder=job['dir']/str(index);folder.mkdir()
    tts_started=(time.perf_counter()-job['start'])*1000
    result=tts.speech(text,folder);path=folder/'speech.wav';storage.download(result['s3_key'],path)
    row={'index':index,'text':text,'tts_started_ms':tts_started,'tts_ready_ms':(time.perf_counter()-job['start'])*1000,'tts_cache_hit':result['metrics'].get('cache_hit',False)}
    job['metrics']['segments'].append(row)
    job['metrics'].setdefault('tts_ready_ms',row['tts_ready_ms'])
    event(job,'audio',segment=index,url=f"/audio/{job['id']}?segment={index}")
    put(audio,(index,path));index+=1
  except Exception as error:put(audio,error)
 threading.Thread(target=llm,daemon=True).start();threading.Thread(target=speech,daemon=True).start()
 try:
  import cv2
  while True:
   item=audio.get(timeout=120)
   if item is None:break
   if isinstance(item,Exception):raise item
   segment,path=item
   def frame(image,index):
    job['metrics'].setdefault('server_first_frame_ms',(time.perf_counter()-job['start'])*1000)
    ok,jpeg=cv2.imencode('.jpg',image,[cv2.IMWRITE_JPEG_QUALITY,85])
    if not ok:raise RuntimeError('JPEG encoding failed')
    event(job,'frame',segment=segment,index=index,jpeg=base64.b64encode(jpeg).decode())
   model.render(job['avatar'],path,frame)
  job['metrics']['generation_finished_ms']=(time.perf_counter()-job['start'])*1000
  event(job,'done',metrics=job['metrics'],segments=len(job['metrics']['segments']))
 except Exception as error:
  logging.exception('isolated render failed');event(job,'error',message=str(error))
 finally:
  stop.set();(job['dir']/'metrics.json').write_text(json.dumps(job['metrics'],indent=2));lock.release()
class Handler(BaseHTTPRequestHandler):
 def log_message(self,fmt,*args):pass
 def json(self,data,status=200):
  payload=json.dumps(data).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
 def do_GET(self):
  path=urlparse(self.path).path
  if path=='/readyz':return self.json({'ready':ready,'gpu':2})
  if path=='/':
   body=(HERE/'index.html').read_bytes();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body);return
  parts=path.strip('/').split('/')
  if len(parts)!=2 or parts[1] not in jobs:return self.json({'error':'not found'},404)
  job=jobs[parts[1]]
  if parts[0]=='audio':
   segment=parse_qs(urlparse(self.path).query).get('segment',['0'])[0]
   if not segment.isdecimal() or len(segment)>3:return self.json({'error':'invalid segment'},400)
   file=job['dir']/segment/'speech.wav'
   if not file.exists():return self.json({'error':'audio not ready'},409)
   data=file.read_bytes();self.send_response(200);self.send_header('Content-Type','audio/wav');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
  if parts[0]=='events':
   if job.get('connected'):return self.json({'error':'already connected'},409)
   job['connected']=True
   self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Cache-Control','no-store');self.end_headers()
   try:
    while True:
     kind,data=job['events'].get(timeout=120)
     self.wfile.write(('event: '+kind+'\ndata: '+json.dumps(data)+'\n\n').encode());self.wfile.flush()
     if kind in ['done','error']:break
   except (BrokenPipeError,ConnectionResetError,queue.Empty):pass
   return
  return self.json({'error':'not found'},404)
 def do_POST(self):
  if self.headers.get('Origin','') not in ['http://127.0.0.1:59494','http://localhost:59494','']:
   return self.json({'error':'origin rejected'},403)
  if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.json({'error':'JSON required'},400)
  size=int(self.headers.get('Content-Length','0'))
  if not 0<size<=4096:return self.json({'error':'invalid size'},400)
  try:body=json.loads(self.rfile.read(size))
  except Exception:return self.json({'error':'invalid JSON'},400)
  path=urlparse(self.path).path
  if path=='/submit':
   text=str(body.get('text','')).strip();avatar=body.get('avatar');mode=body.get('mode','direct')
   if mode not in ['direct','llm']:return self.json({'error':'invalid mode'},400)
   if not 1<=len(text)<=100 or avatar not in ['zhixia','ruotang']:return self.json({'error':'请输入1至100字，选择人物'},400)
   if not ready:return self.json({'error':'模型尚未预热完成'},503)
   if not lock.acquire(False):return self.json({'error':'测试任务正在处理'},409)
   with jobs_lock:
    if len(jobs)>=30:lock.release();return self.json({'error':'本轮实验最多30个任务，请重启测试进程'},409)
    job_id=uuid.uuid4().hex;directory=OUT/job_id;directory.mkdir()
    job={'id':job_id,'mode':mode,'text':text,'avatar':avatar,'dir':directory,'start':time.perf_counter(),'events':queue.Queue(maxsize=150),'metrics':{'avatar':avatar,'characters':len(text),'mode':mode,'segments':[]}};jobs[job_id]=job
   threading.Thread(target=run,args=(job,),daemon=True).start();return self.json({'id':job_id})
  if path=='/report' and body.get('id') in jobs:
   job=jobs[body['id']];metrics=body.get('metrics',{})
   if not isinstance(metrics,dict):return self.json({'error':'invalid metrics'},400)
   safe={k:v for k,v in metrics.items() if k in ['browser_first_frame_ms','browser_audio_start_ms','frames','browser_first_speech_ms'] and isinstance(v,(int,float))}
   (job['dir']/'browser.json').write_text(json.dumps(safe,indent=2));return self.json({'saved':True})
  return self.json({'error':'not found'},404)
ThreadingHTTPServer(('127.0.0.1',59494),Handler).serve_forever()
