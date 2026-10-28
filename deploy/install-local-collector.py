"""Install login-time visible collector + SSH-only Redis tunnel on macOS."""
import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import sys


def main():
    if sys.platform != 'darwin':raise SystemExit('This installer requires macOS')
    parser=argparse.ArgumentParser()
    parser.add_argument('--ssh-host',default='chengdu-taoxin')
    parser.add_argument('--api',default='http://127.0.0.1:59480')
    parser.add_argument('--local-redis-port',type=int,default=59489)
    args=parser.parse_args()
    root=Path(__file__).resolve().parent.parent
    python=root/'worker/.venv/bin/python'
    if not python.exists():raise SystemExit('Run uv sync --group resident-runtime in worker first')
    agents=Path.home()/'Library/LaunchAgents';agents.mkdir(exist_ok=True)
    logs=root/'runtime/local-collector';logs.mkdir(parents=True,exist_ok=True,mode=0o700)
    profiles=root/'runtime/xhs-browser';profiles.mkdir(parents=True,exist_ok=True,mode=0o700)
    domain=f'gui/{os.getuid()}'
    definitions={
        'com.lingcast.collector-tunnel':{
            'ProgramArguments':['/usr/bin/ssh','-N','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes',
                '-o','ServerAliveInterval=20','-o','ServerAliveCountMax=3',
                '-L',f'127.0.0.1:{args.local_redis_port}:127.0.0.1:59479',args.ssh_host],
        },
        'com.lingcast.xhs-collector':{
            'ProgramArguments':[str(python),'-u',str(root/'worker/xhs_collector.py')],
            'WorkingDirectory':str(root/'worker'),
            'EnvironmentVariables':{'REDIS_URL':f'127.0.0.1:{args.local_redis_port}',
                'API_BASE_URL':args.api,'XHS_BROWSER_HEADLESS':'0','XHS_BROWSER_PROFILE_DIR':str(profiles)},
        },
    }
    # Stop the collector before its tunnel, start in the opposite order.
    for label in reversed(definitions):
        subprocess.run(['launchctl','bootout',f'{domain}/{label}'],capture_output=True)
    for label,definition in definitions.items():
        config=dict(Label=label,RunAtLoad=True,KeepAlive=True,ThrottleInterval=15,
                    StandardOutPath=str(logs/f'{label}.out.log'),StandardErrorPath=str(logs/f'{label}.err.log'),**definition)
        path=agents/f'{label}.plist'
        path.write_bytes(plistlib.dumps(config));path.chmod(0o600)
        subprocess.run(['launchctl','bootstrap',domain,str(path)],check=True)
        print(f'Installed {label}')
    print('Collector opens an independent visible browser when a room is enabled in Live Studio.')


if __name__=='__main__':main()
