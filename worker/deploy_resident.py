"""Deploy only LingCast's dedicated resident services; preserve existing services.

cd worker && uv run python deploy_resident.py --gpu 4
Existing Python environments and model weights are reused without installation.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--gpu', required=True)
    p.add_argument('--host', default='chengdu-taoxin')
    a = p.parse_args()
    if not a.gpu.isdigit():
        raise ValueError('GPU must be a numeric device index')
    root = Path(__file__).resolve().parent
    env_file = root/'.env.local'
    values = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=',1)
                values[key] = value
    values.setdefault('RESIDENT_API_TOKEN', secrets.token_urlsafe(32))
    values.update(INDEXTTS_RESIDENT_URL='http://127.0.0.1:59411',
                  LATENTSYNC_RESIDENT_URL='http://127.0.0.1:59412',
                  RESIDENT_S3_ENDPOINT='http://127.0.0.1:59490',
                  LIPSYNC_ENGINE='latentsync', TTS_ENGINE='indextts',
                  REDIS_ADDR='127.0.0.1:6379', S3_ENDPOINT='http://127.0.0.1:9000',
                  S3_ACCESS_KEY='rustfsadmin', S3_SECRET_KEY='rustfsadmin',
                  S3_BUCKET='talking-avatar', API_BASE_URL='http://127.0.0.1:59480',
                  S3_PUBLIC_BASE_URL='http://127.0.0.1:59480/media',
                  LIVEPORTRAIT_OUTPUT_FPS='24', FACE_ENHANCER='off')
    env_file.write_text(''.join(f'{key}={value}\n' for key,value in values.items()))
    os.chmod(env_file,0o600)
    remote = '/data1/taoxin/lingcast-resident'
    subprocess.run(['ssh',a.host,'mkdir -p '+remote+'/work'],check=True)
    with tempfile.TemporaryDirectory() as directory:
        config = Path(directory)/'resident.env'
        config.write_text('RESIDENT_API_TOKEN='+values['RESIDENT_API_TOKEN']+'\nRESIDENT_S3_HOST=127.0.0.1:59490\nCUDA_VISIBLE_DEVICES='+a.gpu+'\nHF_HUB_OFFLINE=1\nTRANSFORMERS_OFFLINE=1\n')
        os.chmod(config,0o600)
        subprocess.run(['scp',str(root/'resident_service.py'),str(config),a.host+':'+remote+'/'],check=True)
    script = '''#!/bin/sh
set -eu
cd /data1/taoxin/lingcast-resident
chmod 600 resident.env
set -a
. ./resident.env
set +a
for mode in tts latent; do
  if [ -f "$mode.pid" ] && kill -0 "$(cat "$mode.pid")" 2>/dev/null; then
    echo "$mode resident already running"
    continue
  fi
  if [ "$mode" = tts ]; then
    python=/data1/taoxin/index-tts/.venv/bin/python
    root=/data1/taoxin/index-tts
    port=59411
  else
    python=/data1/taoxin/avatar/.venv-latentsync/bin/python
    root=/data1/taoxin/avatar
    port=59412
  fi
  nohup "$python" -u resident_service.py "$mode" --root "$root" --port "$port" --work /data1/taoxin/lingcast-resident/work > "$mode.log" 2>&1 < /dev/null &
  echo $! > "$mode.pid"
  echo "$mode resident launched"
done
'''
    subprocess.run(['ssh',a.host,'cat > '+remote+'/start.sh'],input=script.encode(),check=True)
    subprocess.run(['ssh',a.host,'sh '+remote+'/start.sh'],check=True)
    print('Private resident configuration saved; start SSH forwarding as documented.')


if __name__ == '__main__':
    main()
