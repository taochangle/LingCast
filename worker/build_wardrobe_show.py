"""Build local, reviewable outfit mini-shows from delivered silent assets.

Requires prepared renderer 3001 to be available; never starts/restarts a model,
changes live configuration, or publishes an enabled manifest. Upload is explicit.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import requests

from ai.musetalk_client import MuseRenderer
from ai.wardrobe import validate_transition
from ai.wardrobe_profiles import LOOK_RENDERERS
from storage import S3Storage
from worker import _load_local_env


def run(*args):
    subprocess.run(['ffmpeg','-v','error','-y',*map(str,args)],check=True)


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def render_speech(renderer, text, base, destination, work):
    frames=work/'frames';frames.mkdir()
    audio=work/'audio.pcm';count=0
    with audio.open('wb') as pcm:
        for event in renderer.events(text,base):
            if event['type']=='audio':pcm.write(event['audio'])
            elif event['type']=='frame':
                (frames/f'{count:06d}.jpg').write_bytes(base64.b64decode(event['jpeg'],validate=True));count+=1
    if count==0:raise ValueError('No speech frames')
    run('-framerate',25,'-i',frames/'%06d.jpg','-f','s16le','-ar',16000,'-ac',1,'-i',audio,
        '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-ar',16000,'-ac',1,destination)


def normalize_silent(source,destination):
    data=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(source)]))
    video=next(s for s in data['streams'] if s['codec_type']=='video')
    if any(s['codec_type']=='audio' for s in data['streams']):
        raise ValueError('Exit/empty/entrance assets must have no audio')
    if (video['width'],video['height'])!=(576,1024):raise ValueError('Assets must be 576x1024')
    run('-i',source,'-an','-vf','fps=25','-c:v','libx264','-pix_fmt','yuv420p',destination)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--looks-file',type=Path,required=True,help='Prepared looks and explicit rotation order')
    parser.add_argument('--upload',action='store_true',help='Upload reviewed staging objects only; does not enable live feature')
    args=parser.parse_args();_load_local_env()
    args.output.mkdir(parents=True,exist_ok=True)
    config=json.loads(args.looks_file.read_text())
    looks=config['looks'];order=config['order']
    if (len(order)<2 or len(set(order))!=len(order) or set(order)!=set(looks)
            or set(order)-set(LOOK_RENDERERS)):
        raise ValueError('Invalid Ruotang appearance order')
    for look,identity in looks.items():
        if identity['renderer_id']!=LOOK_RENDERERS[look]:raise ValueError('Wrong appearance renderer')
        if look!='white' and not identity['idle_key'].startswith('scene_videos/ruotang-wardrobe'):
            raise ValueError('New look must use isolated wardrobe staging objects')
    renderers={look:MuseRenderer(identity['renderer_id']) for look,identity in looks.items()}
    for look,renderer in renderers.items():renderer.warmup(looks[look]['idle_key'])
    storage=S3Storage()
    for look in order:
        if look=='white':
            path=args.output/'white-current-idle.mp4';storage.download(looks[look]['idle_key'],path)
        else:path=args.assets/look/'idle.mp4'
        looks[look]['idle_sha256']=sha(path)
        if look!='white':
            ready=requests.get(renderers[look].url+'/readyz',timeout=(5,10));ready.raise_for_status()
            if ready.json().get('avatars',{}).get(str(looks[look]['renderer_id']),{}).get('input_sha256')!=looks[look]['idle_sha256']:
                raise ValueError('Idle differs from prepared mouth profile: '+look)
    manifest={'enabled':False,'avatar_id':3,'initial':order[0],'order':order,'looks':looks,'transitions':{}}
    directions=list(zip(order,order[1:]+order[:1]))
    for source,target in directions:
        transition={}
        for phase,look,text in [('intro',source,'好呀，我去换件衣服，等我一下。'),
                                ('outro',target,'我回来啦，这套怎么样？')]:
            output=args.output/f'{source}-to-{target}-{phase}.mp4'
            with tempfile.TemporaryDirectory(dir=args.output) as tmp:
                render_speech(renderers[look],text,looks[look]['idle_key'],output,Path(tmp))
            validate_transition(output,576,1024,25)
            transition[phase]={'media_key':f'scene_videos/ruotang-wardrobe-v1/{sha(output)}.mp4',
                               'sha256':sha(output),'renderer_id':looks[look]['renderer_id'],
                               'base_key':looks[look]['idle_key']}
        with tempfile.TemporaryDirectory(dir=args.output) as tmp:
            work=Path(tmp);parts=[]
            for i,path in enumerate([args.assets/source/'exit.mp4',args.assets/'empty-loop.mp4',args.assets/target/'enter.mp4']):
                clip=work/f'move{i}.mp4';normalize_silent(path,clip);parts.append(clip)
            listing=work/'parts.txt';listing.write_text(''.join(f"file '{p.name}'\n" for p in parts))
            output=args.output/f'{source}-to-{target}-motion.mp4'
            run('-f','concat','-safe',0,'-i',listing,'-an','-vf','fps=25',
                '-c:v','libx264','-pix_fmt','yuv420p',output)
            validate_transition(output,576,1024,25,speech=False)
            transition['motion']={'media_key':f'scene_videos/ruotang-wardrobe-v1/{sha(output)}.mp4','sha256':sha(output)}
        seconds=sum(validate_transition(args.output/f'{source}-to-{target}-{phase}.mp4',576,1024,25,speech=phase!='motion')
                    for phase in ('intro','motion','outro'))
        if seconds>20:raise ValueError('Whole wardrobe show must be at most 20 seconds')
        manifest['transitions'][source+':'+target]=transition
    (args.output/'wardrobe.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    if args.upload:
        for look in order:
            if look!='white':storage.upload(looks[look]['idle_key'],args.assets/look/'idle.mp4','video/mp4')
        for source,target in directions:
            for phase in ('intro','motion','outro'):
                storage.upload(manifest['transitions'][source+':'+target][phase]['media_key'],
                               args.output/f'{source}-to-{target}-{phase}.mp4','video/mp4')
    print('Prepared separate speech/silent motion clips and disabled manifest; live settings unchanged.')

if __name__=='__main__':main()
