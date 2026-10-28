"""Wait for models and the API before restoring live sessions at server boot."""
import os
import time

import requests
from worker import _load_local_env


def main():
    _load_local_env()
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        try:
            ready = all(requests.get(os.environ[name] + '/readyz', timeout=3).json().get('ready')
                        for name in ('INDEXTTS_RESIDENT_URL', 'LATENTSYNC_RESIDENT_URL'))
            if ready and requests.get(os.environ['API_BASE_URL'] + '/api/live', timeout=3).status_code == 200:
                print('Models and API ready', flush=True)
                return
        except (requests.RequestException, ValueError):
            pass
        time.sleep(2)
    raise RuntimeError('Models/API did not become ready within 300 seconds')


if __name__ == '__main__':
    main()
