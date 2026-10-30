"""Prepared, all-or-nothing outfit transitions. No model imports or viewer identity."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from ai.wardrobe_profiles import LOOK_RENDERERS

@dataclass(frozen=True)
class WardrobeClip:
    phase: str
    media_key: str
    sha256: str

@dataclass(frozen=True)
class OutfitChange:
    target: str
    renderer_id: int
    idle_key: str
    idle_sha256: str
    clips: tuple[WardrobeClip, ...]


def plan_change(path, avatar_id, current=None):
    data = json.loads(Path(path).read_text())
    if data.get('enabled') is not True or data.get('avatar_id') != avatar_id:
        raise ValueError('Wardrobe is not enabled for this avatar')
    order=data.get('order',list(data['looks']))
    if (not 2 <= len(order) <= len(LOOK_RENDERERS) or len(set(order))!=len(order)
            or set(order)!=set(data['looks']) or set(order)-set(LOOK_RENDERERS)):
        raise ValueError('A complete, unique order of prepared Ruotang looks is required')
    for name,appearance in data['looks'].items():
        if appearance.get('renderer_id')!=LOOK_RENDERERS[name]:
            raise ValueError('Outfit must use its reserved Ruotang renderer')
    current = current or data['initial']
    if current not in data['looks']:
        raise ValueError('Unknown current outfit')
    target=order[(order.index(current)+1)%len(order)]
    look = data['looks'][target]
    transition = data['transitions'][current+':'+target]
    if set(transition) != {'intro','motion','outro'}:
        raise ValueError('Separate intro, silent motion and outro are required')
    for phase, expected in [('intro',data['looks'][current]),('outro',look)]:
        if (transition[phase].get('renderer_id')!=expected['renderer_id']
                or transition[phase].get('base_key')!=expected['idle_key']):
            raise ValueError('Speech appearance does not match its phase')
    clips=tuple(WardrobeClip(phase,transition[phase]['media_key'],transition[phase]['sha256'])
                for phase in ('intro','motion','outro'))
    for key in (look['idle_key'], *(clip.media_key for clip in clips)):
        if not isinstance(key, str) or not key.strip() or key.startswith('/') or '://' in key or '..' in key.split('/'):
            raise ValueError('Prepared S3 keys required')
    for checksum in (look['idle_sha256'], *(clip.sha256 for clip in clips)):
        if not re.fullmatch('[a-f0-9]{64}', checksum):
            raise ValueError('Prepared SHA256 required')
    if type(look['renderer_id']) is not int or look['renderer_id'] < 1:
        raise ValueError('Prepared renderer ID required')
    return OutfitChange(target,look['renderer_id'],look['idle_key'],look['idle_sha256'],clips)


def verify_file(path, expected):
    digest=hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):
            digest.update(chunk)
    if digest.hexdigest()!=expected:
        raise ValueError('Wardrobe media checksum mismatch')


def validate_transition(path, width, height, fps, speech=True):
    """Bound predecoded memory/time and require a finished audio+video mini-show."""
    import subprocess
    data=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)]))
    videos=[s for s in data['streams'] if s.get('codec_type')=='video']
    audios=[s for s in data['streams'] if s.get('codec_type')=='audio']
    if len(videos)!=1 or len(audios)!=(1 if speech else 0):
        raise ValueError('Speech needs one audio stream; motion must have no audio stream')
    video=videos[0]
    num,den=map(int,video['avg_frame_rate'].split('/'))
    duration=float(data['format']['duration'])
    if (video['width']!=width or video['height']!=height or den==0 or abs(num/den-fps)>0.01
            or not 0<duration<=20):
        raise ValueError('Transition must match live dimensions/fps and be at most 20 seconds')
    return duration


def host_wardrobe_ready(manifest, avatar_id, renderer, collector):
    """Fast local readiness check; uses the resident snapshot from its last warmup."""
    if avatar_id!=3 or not collector.get('enabled') or not collector.get('reply'):
        return False
    try:
        data=json.loads(Path(manifest).read_text())
        prepared=getattr(renderer,'prepared_avatars',{})
        for current in data['looks']:
            plan=plan_change(manifest,avatar_id,current)
            identity=prepared.get(str(plan.renderer_id),{})
            if identity.get('base_key')!=plan.idle_key:
                return False
            if plan.target!='white' and identity.get('input_sha256')!=plan.idle_sha256:
                return False
        return bool(data['looks'])
    except (OSError,ValueError,KeyError,TypeError,AttributeError):
        return False
