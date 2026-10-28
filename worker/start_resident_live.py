"""Restore LingCast's local stack, private forwarding and native stream worker.

cd worker && uv run python start_resident_live.py
Requires deploy_resident.py to have configured the cloud services once.
"""
import os
from pathlib import Path
import subprocess
import time
import requests
from worker import _load_local_env


def main():
    root = Path(__file__).resolve().parent
    runtime = root.parent/'tmp/resident-live'
    runtime.mkdir(parents=True, exist_ok=True)
    _load_local_env()
    subprocess.run(['docker','compose','up','-d','mysql','redis','rustfs','srs',
                    'api-admin','api-live','api-scheduler','frontend-admin'], cwd=root.parent, check=True)
    subprocess.run(['docker','compose','run','--rm','--no-deps','rustfs-init'], cwd=root.parent, check=True)
    subprocess.run(['ssh','-o','BatchMode=yes','chengdu-taoxin',
                    'sh /data1/taoxin/lingcast-resident/start.sh'], check=True)
    def reachable():
        try:
            requests.get(os.environ['INDEXTTS_RESIDENT_URL']+'/readyz',timeout=2)
            requests.get(os.environ['LATENTSYNC_RESIDENT_URL']+'/readyz',timeout=2)
            return True
        except requests.RequestException:
            return False
    if not reachable():
        with (runtime/'forwarding.log').open('a') as log:
            process = subprocess.Popen(['ssh','-N','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes',
                '-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3',
                '-L','127.0.0.1:59411:127.0.0.1:59411',
                '-L','127.0.0.1:59412:127.0.0.1:59412',
                '-R','127.0.0.1:59490:127.0.0.1:9000','chengdu-taoxin'],
                stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
            (runtime/'forwarding.pid').write_text(str(process.pid))
    for _ in range(90):
        try:
            ready = all(requests.get(os.environ[key]+'/readyz',timeout=2).json()['ready']
                        for key in ['INDEXTTS_RESIDENT_URL','LATENTSYNC_RESIDENT_URL'])
            if ready:
                break
        except requests.RequestException:
            pass
        time.sleep(1)
    else:
        raise RuntimeError('Cloud models not ready; inspect forwarding and private cloud logs')
    existing = subprocess.run(['pgrep','-f',str(root/'.venv/bin/python')+'.*stream_worker.py'],
                              capture_output=True,text=True)
    if existing.returncode != 0:
        with (runtime/'stream-worker.log').open('a') as log:
            process = subprocess.Popen(['uv','run','--group','benchmark','python','-u','stream_worker.py'],
                cwd=root,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
            (runtime/'stream-worker.pid').write_text(str(process.pid))
    print('Models ready; existing live sessions restore automatically.')
    print('OBS / live preview: http://127.0.0.1:59480/obs.html?avatar=1')


if __name__ == '__main__':
    main()
