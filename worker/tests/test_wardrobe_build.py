"""CPU-only FFmpeg composition proof; no network, storage writes or model loading."""
import base64
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock,patch

import numpy as np
import cv2
import build_wardrobe_show as builder

@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
class WardrobeBuildTests(unittest.TestCase):
    def test_three_look_cycle_has_separate_speech_and_motion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);assets=root/'assets';assets.mkdir();output=root/'output'
            source=root/'silent.mp4'
            subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=gray:s=576x1024:r=25','-t','0.08','-an','-c:v','libx264','-pix_fmt','yuv420p',str(source)],check=True)
            for name in [f'{look}/{name}.mp4' for look in ('white','burgundy','champagne') for name in ('idle','exit','enter')]+['empty-loop.mp4']:
                path=assets/name;path.parent.mkdir(exist_ok=True);shutil.copyfile(source,path)
            ok,jpeg=cv2.imencode('.jpg',np.zeros((1024,576,3),np.uint8));self.assertTrue(ok)
            class Renderer:
                def __init__(self,avatar):self.avatar=avatar;self.url="http://test"
                def warmup(self,base):pass
                def events(self,text,base):
                    yield {'type':'audio','audio':b'\0'*2560}
                    for _ in range(2):yield {'type':'frame','jpeg':base64.b64encode(jpeg).decode()}
                    yield {'type':'done'}
            storage=Mock();storage.download.side_effect=lambda key,dst:shutil.copyfile(source,dst)
            looks_file=root/'looks.json'
            looks={look:{'renderer_id':renderer,'idle_key':f'scene_videos/ruotang-wardrobe/{look}.mp4'}
                   for look,renderer in [('white',3),('burgundy',3002),('champagne',3003)]}
            looks_file.write_text(json.dumps({'looks':looks,'order':['white','burgundy','champagne']}))
            args=['build','--assets',str(assets),'--output',str(output),'--looks-file',str(looks_file)]
            with patch('sys.argv',args),patch.object(builder,'_load_local_env'),patch.object(builder,'MuseRenderer',Renderer),patch.object(builder,'S3Storage',return_value=storage),patch.object(builder.requests,'get') as ready:
                ready.return_value.json.return_value={'avatars':{str(looks[look]['renderer_id']):{'input_sha256':builder.sha(assets/look/'idle.mp4')} for look in ('burgundy','champagne')}}
                builder.main()
            data=json.loads((output/'wardrobe.json').read_text())
            self.assertFalse(data['enabled'])
            self.assertEqual(data['order'],['white','burgundy','champagne'])
            self.assertEqual(data['looks']['burgundy']['renderer_id'],3002)
            self.assertEqual(data['looks']['champagne']['renderer_id'],3003)
            for direction in ['white-to-burgundy','burgundy-to-champagne','champagne-to-white']:
                key=direction.replace('-to-',':')
                for phase in ('intro','motion','outro'):
                    path=output/(direction+'-'+phase+'.mp4')
                    self.assertGreater(builder.validate_transition(path,576,1024,25,speech=phase!='motion'),0)
                    self.assertEqual(data['transitions'][key][phase]['sha256'],builder.sha(path))
                    if phase=='motion':
                        # Mixed speech/movement files must be rejected by the contract.
                        with self.assertRaises(ValueError):builder.validate_transition(path,576,1024,25,speech=True)
                    else:
                        with self.assertRaises(ValueError):builder.validate_transition(path,576,1024,25,speech=False)
            storage.upload.assert_not_called()
