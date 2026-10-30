"""Offline prep of Ruotang's blue appearance on an explicitly selected spare GPU.

Never touches a running process or replaces an existing prepared profile. Does
not install the descriptor, enable the feature, or upload assets automatically.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from ai.wardrobe_profiles import LOOK_PROFILES


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--look',choices=list(LOOK_PROFILES),default='blue')
    parser.add_argument('--gpu',required=True,help='Explicit spare GPU index; no automatic GPU selection')
    args=parser.parse_args()
    if not args.gpu.isdecimal():raise ValueError('GPU index required')
    root=Path(os.environ.get('MUSETALK_ROOT','/data1/taoxin/lingcast-musetalk-bench'))
    renderer_id,name=LOOK_PROFILES[args.look]
    profile=root/'source/results/v15/avatars'/('bench_'+name)
    if profile.exists():raise FileExistsError('Prepared profile already exists; refusing overwrite or interactive recreation')
    source=args.source.resolve()
    meta=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(source)]))
    video=next(s for s in meta['streams'] if s['codec_type']=='video')
    n,d=map(int,video['avg_frame_rate'].split('/'))
    if (video['width'],video['height'])!=(576,1024) or not d or abs(n/d-25)>0.01 or not 1<=float(meta['format']['duration'])<=10:
        raise ValueError('Reviewed 576x1024/25fps short idle required')
    free=int(subprocess.check_output(['nvidia-smi','--id='+args.gpu,'--query-gpu=memory.free','--format=csv,noheader,nounits'],text=True).strip())
    if free<12000:raise RuntimeError('Chosen GPU has less than 12GB free; refusing prep')
    checksum=hashlib.sha256(source.read_bytes()).hexdigest()
    destination=root/'inputs'/(name+'.mp4')
    if destination.exists():raise FileExistsError('Blue input exists; refusing overwrite')
    destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,destination)
    os.environ['MUSETALK_GPU']=args.gpu
    # Imports weights only in this dedicated offline process, never in a service.
    from ai.musetalk_runtime import m
    avatar=m.Avatar('bench_'+name,str(destination),0,8,True)
    if not getattr(avatar,'input_latent_list_cycle',None):raise RuntimeError('No prepared face latents')
    (profile/'lingcast-source.sha256').write_text(checksum+'\n')
    descriptor={str(renderer_id):{'profile':name,'base_key':f'scene_videos/ruotang-wardrobe/{args.look}-{checksum}.mp4','input_sha256':checksum}}
    out=root/f'prepared-look-{renderer_id}.candidate.json';out.write_text(json.dumps(descriptor,indent=2))
    print('Offline profile prepared; candidate descriptor saved. Live services unchanged.')

if __name__=='__main__':main()
