"""Build a local extension ZIP with operator-supplied server, never credentials."""
import argparse
import html
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
import zipfile

root=Path(__file__).resolve().parent
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--env-file',type=Path,default=root.parent/'.env')
parser.add_argument('--output',type=Path,default=root.parent/'frontend/admin/public/downloads/lingcast-collector.zip')
args=parser.parse_args()
private={}
if args.env_file.exists():
 for line in args.env_file.read_text().splitlines():
  if line and not line.lstrip().startswith('#') and '=' in line:
   key,value=line.split('=',1);private[key.strip()]=value.strip().strip(chr(34)+chr(39))
configured=os.environ.get('LINGCAST_PUBLIC_ORIGIN',private.get('LINGCAST_PUBLIC_ORIGIN',''))
if not configured:raise SystemExit('Set LINGCAST_PUBLIC_ORIGIN in private .env before packaging')
u=urlsplit(configured)
if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.path not in ('','/') or u.query or u.fragment:
 raise SystemExit('Expected HTTP(S) origin without credentials, path or query')
origin=f'{u.scheme}://{u.netloc}'
args.output.parent.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(args.output,'w',zipfile.ZIP_DEFLATED) as archive:
 for path in sorted((root/'chrome').iterdir()):
  if not path.is_file() or path.suffix not in ('.js','.json','.html','.css','.md'):continue
  text=path.read_text()
  if path.name=='protocol.js':text='\n'.join('export const SERVER_ORIGIN = '+json.dumps(origin)+';' if line.startswith('export const SERVER_ORIGIN = ') else line for line in text.splitlines())+'\n'
  elif path.name=='manifest.json':
   data=json.loads(text);data['optional_host_permissions']=['http://*/*','https://*/*'];text=json.dumps(data,ensure_ascii=False,indent=2)+'\n'
  elif path.name=='popup.html':text=text.replace('id="server" type="url" required value=""',f'id="server" type="url" required value="{html.escape(origin,quote=True)}"')
  info=zipfile.ZipInfo('lingcast-collector/'+path.name,(2026,10,30,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;archive.writestr(info,text.encode())
print('Extension built without credentials')
