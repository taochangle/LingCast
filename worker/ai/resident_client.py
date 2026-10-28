"""IndexTTS + LatentSync resident services; media travels exclusively via S3."""
from dataclasses import dataclass
import json
import hashlib
import fcntl
from botocore.exceptions import ClientError
import logging
import os
from pathlib import Path
import time
import uuid
import tempfile
import threading
from collections import defaultdict

import boto3
from botocore.config import Config
import requests

logger = logging.getLogger(__name__)

# Warmup, manual narration and host preparation share each resident endpoint.
_service_locks = defaultdict(threading.Lock)


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
        with _service_locks[url]:
            return self._job(url, payload)

    def _job(self, url, payload):
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

    def identity(self, url):
        response = requests.get(url+'/readyz', timeout=(5, 10))
        response.raise_for_status()
        identity = response.json().get('cache_identity')
        if not identity:
            raise RuntimeError('Resident service missing cache identity; update service first')
        return identity

    def object_identity(self, key):
        head = self.storage.client.head_object(Bucket=self.storage.bucket, Key=key)
        return [key, head['ETag'], head['ContentLength'], head.get('VersionId')]

    def cached_job(self, url, inputs, payload, prefix, suffix, work_dir):
        digest = hashlib.sha256(json.dumps([1, inputs], sort_keys=True).encode()).hexdigest()
        namespace = hashlib.sha256((self.storage.endpoint+self.storage.bucket).encode()).hexdigest()
        directory = Path(tempfile.gettempdir())/'lingcast-media-cache-locks'/namespace
        directory.mkdir(parents=True, exist_ok=True)
        manifest_key = f'cache/resident/v1/{prefix}/{digest}.json'
        with (directory/(prefix+'-'+digest+'.lock')).open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                obj = self.storage.client.get_object(Bucket=self.storage.bucket, Key=manifest_key)
                with obj['Body'] as body:
                    result = json.loads(body.read())
                if result.get('schema') != 1:
                    raise ValueError('Unknown cache schema')
                if self.object_identity(result['s3_key']) != result['object_identity']:
                    raise ValueError('Cached media changed')
                result['metrics']['cache_hit'] = True
                logger.info('[MEDIA_CACHE] %s hit key=%s', prefix, digest)
                return result
            except ClientError as error:
                if error.response['Error']['Code'] not in ('NoSuchKey', '404', 'NotFound'):
                    raise
            except (ValueError, KeyError, TypeError):
                logger.warning('[MEDIA_CACHE] invalid %s entry, regenerating', prefix)
            logger.info('[MEDIA_CACHE] %s miss key=%s', prefix, digest)
            key = f'cache/resident/v1/{prefix}/{digest}/{uuid.uuid4().hex}.{suffix}'
            result = self.job(url, dict(payload, output_key=key, upload_url=self.signed(key, True)))
            if result['s3_key'] != key:
                raise RuntimeError('Resident output key mismatch')
            identity = self.object_identity(key)
            if identity[2] <= 0:
                raise RuntimeError('Empty resident output')
            result.update(schema=1, object_identity=identity)
            result['metrics']['cache_hit'] = False
            self.storage.client.put_object(Bucket=self.storage.bucket, Key=manifest_key,
                Body=json.dumps(result).encode(), ContentType='application/json')
            return result

    def speech(self, text, work_dir):
        text = text.strip()
        return self.cached_job(self.tts_url, [text, self.identity(self.tts_url)],
                               {'text': text}, 'tts', 'wav', work_dir)

    def warmup(self, base_key):
        return self.job(self.latent_url, {'warmup':True, 'video_url':self.signed(base_key)})

    def render(self, text, base_key, work_dir, tts_key=None):
        started = time.perf_counter()
        job_id = uuid.uuid4().hex
        metrics = {}
        if tts_key:
            audio_key = tts_key
        else:
            speech = self.speech(text, work_dir)
            audio_key = speech['s3_key']
            metrics['tts'] = speech['metrics']
        result = self.cached_job(self.latent_url,
            [self.object_identity(base_key), self.object_identity(audio_key)[1:3], self.identity(self.latent_url)],
            {'video_url': self.signed(base_key), 'audio_url': self.signed(audio_key)},
            'video', 'mp4', work_dir)
        video_key = result['s3_key']
        metrics['latent'] = result['metrics']
        metrics['cache_hit'] = result['metrics']['cache_hit']
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
        result = self.speech(text, work_dir)
        audio_key = result['s3_key']
        with tempfile.TemporaryDirectory(dir=work_dir, prefix='narration-') as temp:
            directory = Path(temp)
            source = directory / 'full.wav'
            self.storage.download(audio_key, source)
            parts = split_speech(source, directory)
            logger.info('[NARRATION] job=%s audio=%.2fs parts=%d tts=%.2fs', job_id,
                        result['metrics']['audio_seconds'], len(parts), time.perf_counter()-started)
            for index, (audio, start, end) in enumerate(parts):
                digest = hashlib.sha256(audio.read_bytes()).hexdigest()
                key = f'cache/resident/v1/segments/{digest}.wav'
                self.storage.upload(key, audio, 'audio/wav')
                clip = self.render(text, base_key, work_dir, tts_key=key)
                clip.metrics['narration'] = dict(job_id=job_id, index=index+1, count=len(parts),
                    start=start, end=end, submitted_ms=submitted_ms, trim_padding=True)
                logger.info('[NARRATION] job=%s part=%d/%d ready_after=%.2fs speech=%.2fs',
                            job_id, index+1, len(parts), time.perf_counter()-started, end-start)
                yield clip
