"""IndexTTS + LatentSync resident services; media travels exclusively via S3."""
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import time
import uuid
import tempfile

import boto3
from botocore.config import Config
import requests

logger = logging.getLogger(__name__)


@dataclass
class CompletedClip:
    path: Path
    s3_key: str
    metrics: dict


class ResidentRenderer:
    def __init__(self, storage):
        self.storage = storage
        self.tts_url = os.environ['INDEXTTS_RESIDENT_URL'].rstrip('/')
        self.latent_url = os.environ['LATENTSYNC_RESIDENT_URL'].rstrip('/')
        self.token = os.environ['RESIDENT_API_TOKEN']
        self.signer = boto3.client('s3', endpoint_url=os.environ['RESIDENT_S3_ENDPOINT'],
            aws_access_key_id=storage.access_key, aws_secret_access_key=storage.secret_key,
            region_name='us-east-1', config=Config(signature_version='s3v4', s3={'addressing_style':'path'}))

    def signed(self, key, upload=False):
        return self.signer.generate_presigned_url('put_object' if upload else 'get_object',
                    Params={'Bucket':self.storage.bucket, 'Key':key}, ExpiresIn=900)

    def job(self, url, payload):
        for attempt in range(8):
            response = requests.post(url+'/v1/jobs', json=payload,
                headers={'Authorization':'Bearer '+self.token}, timeout=(10,240))
            if response.status_code == 503 and attempt < 7:
                time.sleep(min(2+attempt, 8))
                continue
            if response.status_code != 200:
                raise RuntimeError(f'Resident service HTTP {response.status_code}')
            return response.json()
        raise RuntimeError('Resident service unavailable')

    def warmup(self, base_key):
        return self.job(self.latent_url, {'warmup':True, 'video_url':self.signed(base_key)})

    def render(self, text, base_key, work_dir, tts_key=None):
        started = time.perf_counter()
        job_id = uuid.uuid4().hex
        audio_key = tts_key or f'tts/resident/{job_id}.wav'
        metrics = {}
        if not tts_key:
            result = self.job(self.tts_url, {'text':text, 'output_key':audio_key,
                                           'upload_url':self.signed(audio_key, True)})
            if result['s3_key'] != audio_key:
                raise RuntimeError('TTS output key mismatch')
            metrics['tts'] = result['metrics']
        video_key = f'live/resident/{job_id}.mp4'
        result = self.job(self.latent_url, {'video_url':self.signed(base_key),
            'audio_url':self.signed(audio_key), 'output_key':video_key,
            'upload_url':self.signed(video_key, True)})
        if result['s3_key'] != video_key:
            raise RuntimeError('LatentSync output key mismatch')
        metrics['latent'] = result['metrics']
        path = Path(work_dir)/(job_id+'.mp4')
        self.storage.download(video_key, path)
        metrics['total_seconds'] = time.perf_counter()-started
        (Path(work_dir)/(job_id+'-metrics.json')).write_text(json.dumps(metrics, indent=2))
        logger.info('resident clip ready key=%s total=%.2fs alignment_cache_hit=%s',
                    video_key, metrics['total_seconds'], metrics['latent'].get('alignment_cache_hit'))
        return CompletedClip(path, video_key, metrics)

    def render_narration(self, text, base_key, work_dir, job_id, submitted_ms=0):
        """Synthesize once; publish each pause-aligned clip as soon as it exists."""
        from .speech_segments import split_speech
        started = time.perf_counter()
        audio_key = f'tts/narration/{job_id}/full.wav'
        result = self.job(self.tts_url, {'text': text, 'output_key': audio_key,
                                        'upload_url': self.signed(audio_key, True)})
        if result['s3_key'] != audio_key:
            raise RuntimeError('Narration TTS output key mismatch')
        with tempfile.TemporaryDirectory(dir=work_dir, prefix='narration-') as temp:
            directory = Path(temp)
            source = directory / 'full.wav'
            self.storage.download(audio_key, source)
            parts = split_speech(source, directory)
            logger.info('[NARRATION] job=%s audio=%.2fs parts=%d tts=%.2fs', job_id,
                        result['metrics']['audio_seconds'], len(parts), time.perf_counter()-started)
            for index, (audio, start, end) in enumerate(parts):
                key = f'tts/narration/{job_id}/{index:03d}.wav'
                self.storage.upload(key, audio, 'audio/wav')
                clip = self.render(text, base_key, work_dir, tts_key=key)
                clip.metrics['narration'] = dict(job_id=job_id, index=index+1, count=len(parts),
                    start=start, end=end, submitted_ms=submitted_ms, trim_padding=True)
                logger.info('[NARRATION] job=%s part=%d/%d ready_after=%.2fs speech=%.2fs',
                            job_id, index+1, len(parts), time.perf_counter()-started, end-start)
                yield clip
