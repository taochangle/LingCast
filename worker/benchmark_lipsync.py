"""Reproducible Wav2Lip benchmark using an already synthesized shared WAV.

cd worker && uv run --group benchmark python benchmark_lipsync.py \
  --video ../tmp/lipsync-comparison/face-input.mp4 \
  --audio ../tmp/lipsync-comparison/speech-input.wav --out ../tmp/lipsync-comparison
No face restoration: matches the live worker. Reports cold and warm runs.
"""
import argparse
import hashlib
import json
import logging
from pathlib import Path
import platform
import subprocess
import time


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--video', type=Path, required=True)
    p.add_argument('--audio', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--runs', type=int, default=2)
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO)
    started = time.perf_counter()
    import cv2
    from ai.lipsync_onnx import Wav2LipOnnxLipSync
    engine = Wav2LipOnnxLipSync(enhancer=None)
    frames, fps = engine._read_frames(args.video)
    media_prepare = time.perf_counter() - started
    records = []
    for run in range(args.runs):
        start = time.perf_counter()
        first = None
        count = 0
        stages = {}
        def timing(stage, ms):
            stages[stage] = stages.get(stage, 0) + ms
        raw = args.out / f'wav2lip-{run + 1}-silent.mp4'
        writer = cv2.VideoWriter(str(raw), cv2.VideoWriter_fourcc(*'mp4v'), fps,
                                 (frames[0].shape[1], frames[0].shape[0]))
        if not writer.isOpened():
            raise RuntimeError('VideoWriter could not open')
        try:
            for frame in engine.iter_frames(args.audio, frames, fps, timing=timing):
                if first is None:
                    first = time.perf_counter() - start
                writer.write(frame)
                count += 1
        finally:
            writer.release()
        inference_seconds = time.perf_counter() - start
        output = args.out / f'wav2lip-{run + 1}.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(raw), '-i', str(args.audio),
                        '-map', '0:v', '-map', '1:a', '-c:v', 'libx264', '-crf', '18',
                        '-preset', 'fast', '-c:a', 'aac', '-shortest', str(output)], check=True)
        raw.unlink()
        records.append(dict(run=run+1, cold=run==0, first_frame_seconds=first,
                            inference_and_write_seconds=inference_seconds,
                            total_seconds=time.perf_counter()-start, frames=count,
                            output_duration=count/fps, stage_ms=stages, output=str(output),
                            providers=engine._session.get_providers()))
        print(json.dumps(records[-1], ensure_ascii=False), flush=True)
    result = dict(engine='LingCast Wav2Lip ONNX', host=platform.platform(),
                  video_sha256=sha(args.video), audio_sha256=sha(args.audio),
                  media_prepare_seconds=media_prepare, enhancer=False, runs=records)
    (args.out/'wav2lip-metrics.json').write_text(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
