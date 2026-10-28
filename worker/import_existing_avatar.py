"""Import an existing portrait and idle video via LingCast's normal APIs.

cd worker && uv run python import_existing_avatar.py --portrait /path/portrait.png \
  --video /path/base-h3.mp4 --name 'IT 女主持人'
Reads admin credentials from ../.env (never prints them). Reuses avatar by name.
"""
import argparse
import json
import os
from pathlib import Path
import requests


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--portrait', type=Path, required=True)
    p.add_argument('--video', type=Path, required=True)
    p.add_argument('--name', default='IT 女主持人')
    p.add_argument('--api', default='http://127.0.0.1:8080')
    args = p.parse_args()
    env = {}
    config = Path(__file__).resolve().parent.parent/'.env'
    if config.exists():
        for line in config.read_text().splitlines():
            if line.strip() and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                env[key] = value
    with requests.Session() as s:
        def request(method, path, **kwargs):
            r = s.request(method, args.api.rstrip('/')+path, timeout=60, **kwargs)
            if not r.ok:
                raise RuntimeError(f'{method} {path}: HTTP {r.status_code}')
            return r.json()
        request('POST', '/api/admin/login', json={
            'username': os.environ.get('ADMIN_USERNAME', env.get('ADMIN_USERNAME', 'admin')),
            'password': os.environ.get('ADMIN_PASSWORD', env.get('ADMIN_PASSWORD', ''))})
        avatars = request('GET', '/api/avatars')['data']
        avatar = next((a for a in avatars if a['name'] == args.name), None)
        if not avatar:
            with args.portrait.open('rb') as image:
                avatar = request('POST', '/api/avatars',
                                 data={'name': args.name, 'category': '知识',
                                       'personality': '自然、友好、简洁，擅长解释技术问题'},
                                 files={'image': (args.portrait.name, image, 'image/png')})
        scenes = request('GET', f'/api/avatars/{avatar["id"]}/scenes')['data']
        scene = next(s for s in scenes if s['isDefault'])
        description = '原默认人物，自然眨眼与呼吸，供闲置轮播和同底片口型测试'
        if not any(v['description'] == description for v in scene['videos']):
            with args.video.open('rb') as video:
                request('POST', f'/api/scenes/{scene["id"]}/videos',
                        data={'description': description},
                        files={'file': (args.video.name, video, 'video/mp4')})
        avatar = request('GET', f'/api/avatars/{avatar["id"]}')
        result = {'id': avatar['id'], 'name': avatar['name'], 'status': avatar['status'],
                  'scene_id': scene['id'], 'portrait_source': str(args.portrait.resolve()),
                  'video_source': str(args.video.resolve())}
        output = Path(__file__).resolve().parent.parent/'tmp/lipsync-comparison/avatar-import.json'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
