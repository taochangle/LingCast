import os,sys,time,json,types,subprocess,copy,queue
from pathlib import Path
os.environ['CUDA_VISIBLE_DEVICES']=os.environ.get('MUSETALK_GPU','2')
os.environ['OMP_NUM_THREADS']='4'
root=Path(os.environ.get('MUSETALK_ROOT','/data1/taoxin/lingcast-musetalk-bench'));os.chdir(root/'source');sys.path.insert(0,str(root/'source'))
started=time.perf_counter()
import torch,cv2
torch.cuda.set_per_process_memory_fraction(.20)
import scripts.realtime_inference as m
m.args=types.SimpleNamespace(version='v15',extra_margin=10,parsing_mode='jaw',skip_save_images=True,audio_padding_length_left=2,audio_padding_length_right=2)
m.device=torch.device('cuda:0')
m.vae,m.unet,m.pe=m.load_all_model(unet_model_path='models/musetalkV15/unet.pth',vae_type='sd-vae',unet_config='models/musetalkV15/musetalk.json',device=m.device)
m.timesteps=torch.tensor([0],device=m.device)
m.pe=m.pe.half().to(m.device);m.vae.vae=m.vae.vae.half().to(m.device);m.unet.model=m.unet.model.half().to(m.device)
m.audio_processor=m.AudioProcessor(feature_extractor_path='models/whisper');m.weight_dtype=m.unet.model.dtype
m.whisper=m.WhisperModel.from_pretrained('models/whisper').to(device=m.device,dtype=m.weight_dtype).eval();m.whisper.requires_grad_(False)
m.fp=m.FaceParsing(left_cheek_width=90,right_cheek_width=90)

class StreamingMuse:
 def __init__(self):
  self.avatars={name:m.Avatar('bench_'+name,str(root/'inputs'/f'{name}.mp4'),0,8,False) for name in ['zhixia','ruotang']}
  for name in self.avatars:
   self.render(name,root/'inputs/speech.wav',lambda frame,index:None)
  # CosyVoice uses 24kHz windows; warm its resampling/short-input path too.
  warm=root/'live-test'/'warm-24k.wav';warm.parent.mkdir(exist_ok=True)
  subprocess.run(['ffmpeg','-v','error','-y','-i',str(root/'inputs/speech.wav'),'-t','1.16','-ar','24000',str(warm)],check=True)
  for name in self.avatars:
   self.render(name,warm,lambda frame,index:None)
 def render(self,name,audio,callback,frame_offset=0):
  avatar=copy.copy(self.avatars[name]);errors=[]
  # Rotate appearance and matching latents together; preserve pose across audio windows.
  for key in ['frame_list_cycle','coord_list_cycle','input_latent_list_cycle','mask_list_cycle','mask_coords_list_cycle']:
   values=getattr(avatar,key);offset=frame_offset%len(values);setattr(avatar,key,values[offset:]+values[:offset])
  def consume(self,q,n,skip):
   try:
    for idx in range(n):
     face=q.get(timeout=60);bbox=self.coord_list_cycle[idx%len(self.coord_list_cycle)]
     frame=copy.deepcopy(self.frame_list_cycle[idx%len(self.frame_list_cycle)])
     x1,y1,x2,y2=bbox;face=cv2.resize(face.astype('uint8'),(x2-x1,y2-y1))
     frame=m.get_image_blending(frame,face,bbox,self.mask_list_cycle[idx%len(self.mask_list_cycle)],self.mask_coords_list_cycle[idx%len(self.mask_coords_list_cycle)])
     callback(frame,idx);self.idx=idx+1
   except Exception as error:errors.append(str(error))
  avatar.process_frames=types.MethodType(consume,avatar)
  avatar.inference(str(audio),None,25,True)
  if errors:raise RuntimeError(errors)
