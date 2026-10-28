"""Offline benchmark helper run in the EXISTING cloud model environments.

Uses a dedicated work directory; never modifies model repositories or services.
The SSH fixture transfer is for benchmarking only, not the application media API.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['tts', 'latent'])
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--gpu', required=True)
    args = parser.parse_args()
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    os.environ['CUDA_VISIBLE_DEVICES'] = args.gpu
    lock = Path('/tmp/digital-human-latentsync.lock').open('a+b')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    started = time.perf_counter()
    if args.mode == 'tts':
        root = Path('/data1/taoxin/index-tts')
        os.chdir(root)
        sys.path.insert(0, str(root))
        from indextts.infer_v2_5 import IndexTTS2
        model = IndexTTS2(cfg_path=str(root/'checkpoints/config.yaml'),
                         model_dir=str(root/'checkpoints'), use_bf16=True)
        loaded = time.perf_counter()
        text = (work/'text.txt').read_text().strip()
        model.infer(spk_audio_prompt=str(root/'examples/voice_09.wav'), text=text,
                    lang='ZH', output_path=str(work/'speech.wav'), duration_factor=1.0, verbose=False)
        result = dict(engine='IndexTTS 2.5', gpu=args.gpu, text=text,
                      model_load_seconds=loaded-started,
                      synthesis_seconds=time.perf_counter()-loaded,
                      total_seconds=time.perf_counter()-started)
    else:
        root = Path('/data1/taoxin/avatar')
        for name in ['configs', 'checkpoints']:
            link = work/name
            if not link.exists():
                link.symlink_to(root/name)
        env = os.environ.copy()
        env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                   LATENTSYNC_VAE_PATH=str(root/'sd-vae-ft-mse'))
        with (work/'latent-inference.log').open('w') as log:
            subprocess.run([sys.executable, str(root/'integrations/v1_inference_runner.py'),
                            'lipsync', str(root), str(work)], cwd=root, env=env,
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
        result = dict(engine='LatentSync existing subprocess runner', gpu=args.gpu,
                      total_seconds=time.perf_counter()-started,
                      inference_steps=20, guidance_scale=1.5, seed=1247,
                      first_frame_seconds=None,
                      note='Complete-file output; no streamable first-frame callback.',
                      video_sha256=hashlib.sha256((work/'face-input.mp4').read_bytes()).hexdigest(),
                      audio_sha256=hashlib.sha256((work/'speech-input.wav').read_bytes()).hexdigest())
    (work/f'{args.mode}-metrics.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
