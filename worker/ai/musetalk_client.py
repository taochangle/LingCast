"""Consume paired audio/JPEG windows from the private resident renderer."""
import base64
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import time
import uuid
import requests

logger = logging.getLogger(__name__)

@dataclass
class StreamingSpeech:
    path: Path
    text: str
    base_key: str
    metrics: dict

class MuseRenderer:
    def __init__(self, avatar_id):
        self.avatar_id = int(avatar_id)
        self.url = os.environ['MUSETALK_RESIDENT_URL'].rstrip('/')
        self.token = os.environ['RESIDENT_API_TOKEN']

    def warmup(self, base_key):
        response = requests.get(self.url+'/readyz', timeout=(5, 10))
        response.raise_for_status()
        data = response.json()
        if not data.get('ready') or data.get('avatars', {}).get(str(self.avatar_id), {}).get('base_key') != base_key:
            raise RuntimeError('MuseTalk avatar/material is not prepared; refusing mismatched face')

    def events(self, text, base_key):
        response = None
        for attempt in range(60):
            response = requests.post(self.url+'/render', json={'avatar_id': self.avatar_id, 'text': text, 'base_key': base_key},
                headers={'Authorization': 'Bearer '+self.token}, stream=True, timeout=(5, 90))
            if response.status_code != 503:
                break
            response.close()
            time.sleep(1)
        with response:
            response.raise_for_status()
            remaining = 0
            seen = False
            ended = False
            expected_index = 0
            for line in response.iter_lines(chunk_size=None):
                if not line:
                    continue
                if len(line) > 4*1024*1024:
                    raise RuntimeError('Oversized MuseTalk packet')
                part = json.loads(line)
                part['cache_hit'] = response.headers.get('X-Media-Cache') == 'hit'
                kind = part.get('type')
                if kind == 'audio':
                    if remaining or part.get('index') != expected_index:
                        raise RuntimeError('Out of order MuseTalk audio')
                    expected_index += 1
                    remaining = int(part['frames'])
                    if not 1 <= remaining <= 250:
                        raise RuntimeError('Invalid MuseTalk frame count')
                    part['audio'] = base64.b64decode(part.pop('pcm'), validate=True)
                    if len(part['audio']) != remaining*1280:
                        raise RuntimeError('MuseTalk audio/sample clock mismatch')
                    seen = True
                elif kind == 'frame':
                    if not remaining or part.get('index') != expected_index-1:
                        raise RuntimeError('Out of order MuseTalk frame')
                    remaining -= 1
                elif kind == 'done':
                    if remaining or not seen:
                        raise RuntimeError('Incomplete MuseTalk stream')
                    ended = True
                elif kind == 'error':
                    raise RuntimeError('MuseTalk rendering failed: '+str(part.get('message')))
                else:
                    raise RuntimeError('Unknown MuseTalk packet')
                yield part
                if ended:
                    break
            if not ended:
                raise RuntimeError('MuseTalk stream disconnected before completion')

    def render(self, text, base_key, work_dir, **kwargs):
        """HostDirector prefetches the shared cache, without creating an MP4."""
        seconds = 0
        for event in self.events(text, base_key):
            if event['type'] == 'audio':
                seconds += event['frames']/25
        marker = Path(work_dir)/(uuid.uuid4().hex+'.speech.json')
        marker.write_text(json.dumps({'text': text, 'base_key': base_key}))
        return StreamingSpeech(marker, text, base_key, {'latent': {'audio_seconds': seconds}})
