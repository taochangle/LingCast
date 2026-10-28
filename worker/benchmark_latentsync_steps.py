"""Same audio/base/seed comparison of resident LatentSync inference steps."""
import json
from pathlib import Path
import statistics
import time

import requests
from worker import _load_local_env
from storage import S3Storage
from ai.resident_client import ResidentRenderer


def main():
    _load_local_env()
    storage = S3Storage()
    renderer = ResidentRenderer(storage)
    root = Path('../runtime/steps-benchmark').resolve()
    root.mkdir(parents=True, exist_ok=True)
    prefix = 'benchmarks/20261028-latentsync-steps'
    base = 'scene_videos/20261028_918bc8f6bc081975_base-h3.mp4'
    text = '大家好，欢迎来到直播间，今天我们一起聊聊有趣的开源项目。'
    for _ in range(150):
        try:
            if requests.get(renderer.latent_url + '/readyz', timeout=2).json()['ready']:
                break
        except requests.RequestException:
            pass
        time.sleep(2)
    else:
        raise RuntimeError('LatentSync did not become ready')
    audio = prefix + '/same-speech.wav'
    tts = renderer.job(renderer.tts_url, {'text': text, 'output_key': audio,
                                         'upload_url': renderer.signed(audio, True)})
    renderer.warmup(base)
    records = []
    # Full warm-up excluded. Reverse second round to limit ordering effects.
    for index, steps in enumerate([20, 20, 15, 10, 8, 8, 10, 15, 20]):
        key = f'{prefix}/{index:02d}-{steps}-steps.mp4'
        start = time.perf_counter()
        result = renderer.job(renderer.latent_url, {
            'video_url': renderer.signed(base), 'audio_url': renderer.signed(audio),
            'upload_url': renderer.signed(key, True), 'output_key': key, 'steps': steps,
        })
        metrics = result['metrics']
        row = dict(steps=steps, warmup=index == 0, wall_seconds=time.perf_counter() - start,
                   key=key, metrics=metrics)
        records.append(row)
        (root/'results.json').write_text(json.dumps(dict(text=text, audio_key=audio, base_key=base,
            seed=1247, guidance=1.5, tts_metrics=tts['metrics'], trials=records), indent=2, ensure_ascii=False))
        print(json.dumps(row), flush=True)
    client = storage.s3 if hasattr(storage, 's3') else storage.client
    for steps in [20, 15, 10, 8]:
        rows = [r for r in records if r['steps'] == steps and not r['warmup']]
        client.download_file(storage.bucket, rows[0]['key'], str(root/f'{steps}-steps.mp4'))
        print('SUMMARY', steps, 'median', round(statistics.median(r['wall_seconds'] for r in rows), 3), flush=True)
    client.download_file(storage.bucket, audio, str(root/'same-speech.wav'))


if __name__ == '__main__':
    main()
