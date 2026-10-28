"""Split one PCM narration at detected pauses, without dropping audio samples."""
from pathlib import Path
import re
import subprocess
import wave


def pause_boundaries(duration, pauses, target=3.0, minimum=1.5, maximum=4.5):
    cuts = [0.0]
    pauses = sorted(p for p in pauses if 0 < p < duration)
    while duration - cuts[-1] > maximum:
        start = cuts[-1]
        candidates = [p for p in pauses if minimum <= p - start <= maximum]
        if candidates:
            cut = min(candidates, key=lambda p: abs(p - start - target))
        else:
            # Keep an uninterrupted phrase intact rather than cutting a word.
            later = [p for p in pauses if maximum < p - start <= 18]
            if later:
                cut = later[0]
            elif duration - start <= 18:
                break
            else:
                raise ValueError('No natural pause within 18 seconds; add punctuation to the script')
        cuts.append(cut)
    return cuts + [duration]


def split_speech(source: Path, directory: Path):
    normalized = directory / 'narration-pcm.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(source), '-ac', '1',
                    '-ar', '16000', '-c:a', 'pcm_s16le', str(normalized)], check=True)
    detection = subprocess.run(['ffmpeg', '-hide_banner', '-i', str(normalized),
        '-af', 'silencedetect=noise=-35dB:d=0.12', '-f', 'null', '-'], capture_output=True, text=True, check=True)
    starts = []
    pauses = []
    for line in detection.stderr.splitlines():
        match = re.search(r'silence_start: ([\d.]+)', line)
        if match:
            starts.append(float(match[1]))
        match = re.search(r'silence_end: ([\d.]+)', line)
        if match and starts:
            pauses.append((starts.pop(0) + float(match[1])) / 2)
    with wave.open(str(normalized), 'rb') as wav:
        rate = wav.getframerate()
        frames = wav.getnframes()
        params = wav.getparams()
        data = wav.readframes(frames)
    cuts = pause_boundaries(frames / rate, pauses)
    samples = [0] + [round(c * rate) for c in cuts[1:-1]] + [frames]
    parts = []
    for index, (start, end) in enumerate(zip(samples, samples[1:])):
        path = directory / f'speech-{index:03d}.wav'
        with wave.open(str(path), 'wb') as wav:
            wav.setparams(params)
            wav.writeframes(data[start * 2:end * 2])
        parts.append((path, start / rate, end / rate))
    return parts
