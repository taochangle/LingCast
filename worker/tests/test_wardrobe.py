import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from ai.wardrobe import plan_change,verify_file

class WardrobeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'wardrobe.json'
        self.data={'enabled':True,'avatar_id':3,'initial':'white','looks':{
            name:{'renderer_id':3 if name=='white' else 3001,'idle_key':name+'.mp4','idle_sha256':'a'*64}
            for name in ('white','blue')},'transitions':{
                direction:{phase:{'media_key':direction+'-'+phase+'.mp4','sha256':'b'*64,**({'renderer_id':3 if look=='white' else 3001,'base_key':look+'.mp4'} if phase!='motion' else {})} for phase,look in [('intro',direction.split(':')[0]),('motion',None),('outro',direction.split(':')[1])]} for direction in ('white:blue','blue:white')}}
        self.save()
    def save(self):self.path.write_text(json.dumps(self.data))
    def test_toggle_preserves_matching_idle_and_renderer(self):
        blue=plan_change(self.path,3)
        self.assertEqual((blue.target,blue.renderer_id,blue.idle_key),('blue',3001,'blue.mp4'))
        white=plan_change(self.path,3,blue.target)
        self.assertEqual((white.target,white.renderer_id,white.idle_key),('white',3,'white.mp4'))
    def test_disabled_missing_assets_wrong_avatar_and_unknown_look_fail_closed(self):
        with self.assertRaises(ValueError):plan_change(self.path,2)
        with self.assertRaises(ValueError):plan_change(self.path,3,'invented')
        self.data['looks']['blue']['idle_sha256']='';self.save()
        with self.assertRaises(ValueError):plan_change(self.path,3)
        self.data['enabled']=False;self.save()
        with self.assertRaises(ValueError):plan_change(self.path,3)
    def test_rejects_combined_movie_and_speech_with_wrong_appearance(self):
        original=json.loads(self.path.read_text())
        self.data['transitions']['white:blue']={'media_key':'old-combined.mp4','sha256':'b'*64}
        self.save()
        with self.assertRaises(ValueError):plan_change(self.path,3)
        self.data=original
        self.data['transitions']['white:blue']['outro']['renderer_id']=3
        self.save()
        with self.assertRaises(ValueError):plan_change(self.path,3)

    def test_both_new_styles_are_in_one_rotation_without_overwriting_blue(self):
        from ai.wardrobe_profiles import LOOK_RENDERERS
        order=['white','burgundy','champagne']
        self.data['order']=order
        self.data['looks']={name:{'renderer_id':LOOK_RENDERERS[name],'idle_key':name+'.mp4','idle_sha256':'a'*64} for name in order}
        self.data['transitions']={}
        for old,new in zip(order,order[1:]+order[:1]):
            self.data['transitions'][old+':'+new]={phase:{'media_key':old+'-'+new+'-'+phase+'.mp4','sha256':'b'*64,
                **({'renderer_id':LOOK_RENDERERS[look],'base_key':look+'.mp4'} if phase!='motion' else {})}
                for phase,look in [('intro',old),('motion',None),('outro',new)]}
        self.save()
        for old,new in zip(order,order[1:]+order[:1]):
            plan=plan_change(self.path,3,old)
            self.assertEqual(plan.target,new)
            self.assertEqual(plan.renderer_id,LOOK_RENDERERS[new])
        self.assertEqual(LOOK_RENDERERS['blue'],3001)

    def test_checksum_before_playback(self):
        path=Path(self.temp.name)/'video';path.write_bytes(b'prepared')
        verify_file(path,hashlib.sha256(b'prepared').hexdigest())
        with self.assertRaises(ValueError):verify_file(path,'0'*64)

class PreparedLookTests(unittest.TestCase):
    def test_only_operator_prepared_ruotang_variant(self):
        from ai.prepared_looks import prepared_looks
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'looks.json'
            self.assertEqual(prepared_looks(path),{})
            path.write_text(json.dumps({'3001':{'profile':'ruotang_blue','base_key':'scene_videos/blue.mp4','input_sha256':'a'*64}}))
            self.assertEqual(prepared_looks(path)['3001']['profile'],'ruotang_blue')
            data={str(index):{'profile':name,'base_key':'scene_videos/'+name+'.mp4','input_sha256':'a'*64} for index,name in [(3002,'ruotang_burgundy'),(3003,'ruotang_champagne')]}
            path.write_text(json.dumps(data))
            self.assertEqual(set(prepared_looks(path)),{'3002','3003'})
            path.write_text(json.dumps({'3':{'profile':'different-person'}}))
            with self.assertRaises(ValueError):prepared_looks(path)

class PlaybackTests(unittest.TestCase):
    def test_writer_commit_switches_visuals_and_renderer_together(self):
        import threading
        from unittest.mock import Mock
        from stream_worker import LiveAvatarSession
        s=LiveAvatarSession.__new__(LiveAvatarSession)
        s._idle_lock=threading.Lock();s._state=Mock()
        s.resident='old';s.idle_clips=[['old']]
        renderer=object()
        commit={'frames':['blue-frame'],'key':'blue.mp4','renderer':renderer,'look':'blue'}
        s._commit_wardrobe(commit)
        self.assertEqual(s.idle_clips,[['blue-frame']])
        self.assertEqual(s._idle_clip_keys,['blue.mp4'])
        self.assertIs(s.resident,renderer)
        self.assertEqual(s._wardrobe_current,'blue')
        self.assertEqual(s._idle_frame_idx,0)
    def test_unprepared_renderer_fails_before_download_or_playback(self):
        import threading
        from unittest.mock import Mock,patch
        import stream_worker
        with tempfile.TemporaryDirectory() as tmp:
            s=stream_worker.LiveAvatarSession.__new__(stream_worker.LiveAvatarSession)
            s.avatar_id=3;s.work_dir=Path(tmp);s._wardrobe_active=True;s._wardrobe_current=None
            s._redis=Mock();s._redis.get.return_value=json.dumps({'enabled':True,'revision':1})
            s._state=Mock();s.storage=Mock()
            s.resident=Mock(spec=stream_worker.MuseRenderer)
            with patch.object(stream_worker.MuseRenderer,'warmup',side_effect=RuntimeError('not prepared')),patch.dict('os.environ',{'MUSETALK_RESIDENT_URL':'http://127.0.0.1:1','RESIDENT_API_TOKEN':'test'}),patch('ai.wardrobe.plan_change') as plan:
                plan.return_value=type('Plan',(),{'renderer_id':3001,'idle_key':'blue.mp4'})()
                import time
                s._play_wardrobe({'xhs_revision':1,'ts':time.time()*1000})
            s.storage.download.assert_not_called()
            self.assertFalse(s._wardrobe_active)
            s._state.assert_called_with('wardrobe','failed')

    def test_prepared_show_commits_only_after_playback_queue_boundary(self):
        import threading,time
        from unittest.mock import Mock,patch
        import numpy as np
        import stream_worker
        from ai.wardrobe import OutfitChange
        with tempfile.TemporaryDirectory() as tmp:
            s=stream_worker.LiveAvatarSession.__new__(stream_worker.LiveAvatarSession)
            s.avatar_id=3;s.work_dir=Path(tmp);s._running=True;s._wardrobe_active=True;s._wardrobe_current=None
            s._idle_lock=threading.Lock();s._muse_play_lock=threading.Lock();s.base_frames=[np.zeros((8,8,3),np.uint8)]
            s.fps=25;s._state=Mock();s.storage=Mock();s._redis=Mock()
            s._redis.get.return_value=json.dumps({'enabled':True,'revision':1})
            old=Mock(spec=stream_worker.MuseRenderer);s.resident=old
            media=b'prepared';checksum=hashlib.sha256(media).hexdigest()
            from ai.wardrobe import WardrobeClip
            plan=OutfitChange('blue',3001,'blue.mp4',checksum,
                tuple(WardrobeClip(phase,phase+'.mp4',checksum) for phase in ('intro','motion','outro')))
            s.storage.download.side_effect=lambda key,dst:dst.write_bytes(media)
            def queue_show(decoded,commit):
                self.assertIs(s.resident,old)
                self.assertEqual(set(decoded),{'intro','motion','outro'})
                self.assertEqual(set(decoded['motion'][1]),{0})
                self.assertNotEqual(set(decoded['outro'][1]),{0})
                self.assertEqual(s.storage.download.call_count,4)
                s._commit_wardrobe(commit)
            s._queue_wardrobe_show=Mock(side_effect=queue_show)
            with patch.object(stream_worker.MuseRenderer,'warmup'),patch.dict('os.environ',{'MUSETALK_RESIDENT_URL':'http://127.0.0.1:1','RESIDENT_API_TOKEN':'test'}),patch('ai.wardrobe.plan_change',return_value=plan),patch('ai.wardrobe.validate_transition',return_value=0.1),patch('stream_worker.subprocess.run'),patch('stream_worker.subprocess.check_output',return_value=b'1'*2560),patch('stream_worker._read_video_frames',return_value=(s.base_frames,25)):
                s._play_wardrobe({'xhs_revision':1,'ts':time.time()*1000})
            self.assertEqual(s._wardrobe_current,'blue')
            self.assertEqual(s.resident.avatar_id,3001)
            self.assertFalse(s._wardrobe_active)

    def test_real_writer_finishes_motion_then_displays_new_idle_before_outro(self):
        import threading,queue
        from unittest.mock import Mock
        import numpy as np
        from stream_worker import LiveAvatarSession
        s=LiveAvatarSession.__new__(LiveAvatarSession)
        s.avatar_id=3;s.stream_id='wardrobe-test';s.fps=25;s._running=True;s._dead=False
        s._idle_lock=threading.Lock();s._talk_queue=queue.Queue(maxsize=2)
        s._last_watchdog_warn=0;s._subtitle_text='';s._state=Mock()
        s.resident='white-renderer';s._wardrobe_current='white';s._wardrobe_visual_hold=None
        frame=lambda n:np.full((2,2,3),n,np.uint8)
        s.idle_clips=[[frame(0)]];s._idle_frame=lambda:s.idle_clips[0][0]
        trace=[];audio=[b''];s.pipe=Mock()
        s.pipe.write_audio.side_effect=lambda value:audio.__setitem__(0,value)
        s._write_frame=lambda value:trace.append((int(value[0,0,0]),s.resident,audio[0]))
        decoded={'intro':([frame(11)],b'1'*1280),
                 'motion':([frame(21),frame(22),frame(23)],b'\0'*3840),
                 'outro':([frame(41)],b'2'*1280)}
        commit={'frames':[frame(30)],'key':'blue.mp4','renderer':'blue-renderer','look':'blue'}
        writer=threading.Thread(target=s._writer_loop);writer.start()
        producer=threading.Thread(target=s._queue_wardrobe_show,args=(decoded,commit));producer.start()
        producer.join(3)
        finished=not producer.is_alive()
        s._running=False;producer.join(1);writer.join(1)
        self.assertTrue(finished,'outro playback barrier was not reached')
        values=[value for value,_,_ in trace]
        for value in [11,21,22,23,30,41]:self.assertIn(value,values)
        self.assertLess(values.index(11),values.index(21))
        self.assertLess(values.index(23),values.index(30))
        self.assertLess(values.index(30),values.index(41))
        self.assertNotIn(0,values[values.index(21):])
        for value,renderer,audio in trace:
            if value in (21,22,23):
                self.assertEqual(renderer,'white-renderer')
                self.assertEqual(set(audio),{0},'audio during walking/entrance')
            if value in (30,41):self.assertEqual(renderer,'blue-renderer')
        self.assertEqual(s._wardrobe_current,'blue')

    def test_pending_show_waits_until_existing_speech_is_fully_drained(self):
        import queue,time
        from unittest.mock import Mock,patch
        import stream_worker
        s=stream_worker.LiveAvatarSession.__new__(stream_worker.LiveAvatarSession)
        s.avatar_id=3;s.stream_id='test';s._last_health_log=time.monotonic()
        s._wardrobe_active=False;s._wardrobe_pending={'action':'wardrobe'}
        s._tts_thread=Mock();s._tts_thread.is_alive.return_value=True
        s._produce_thread=None;s._pending=queue.Queue();s._tts_results=queue.Queue();s._talk_queue=queue.Queue()
        s.resident=None;r=Mock();r.hgetall.return_value={'playback':'playing'}
        with patch.object(stream_worker.threading,'Thread') as thread,patch.object(stream_worker.time,'sleep'):
            s.tick(r);thread.assert_not_called()
            s._tts_thread=None
            s.tick(r);thread.assert_not_called()
            r.hgetall.return_value={'playback':'idle'}
            s._talk_queue.put(('frame','remaining-frame'))
            s.tick(r);thread.assert_not_called()
            s._talk_queue.get()
            s.tick(r)
            thread.assert_called_once()
            thread.return_value.start.assert_called_once()
        self.assertTrue(s._wardrobe_active)
        self.assertIsNone(s._wardrobe_pending)

class HostHintReadinessTests(unittest.TestCase):
    def test_requires_enabled_collector_reply_all_profiles_and_full_manifest(self):
        from ai.wardrobe import host_wardrobe_ready
        from types import SimpleNamespace
        fixture=WardrobeTests();fixture.setUp()
        try:
            ready={'3':{'base_key':'white.mp4'},'3001':{'base_key':'blue.mp4','input_sha256':'a'*64}}
            renderer=SimpleNamespace(prepared_avatars=ready)
            collector={'enabled':True,'reply':True}
            self.assertTrue(host_wardrobe_ready(fixture.path,3,renderer,collector))
            self.assertFalse(host_wardrobe_ready(fixture.path,2,renderer,collector))
            self.assertFalse(host_wardrobe_ready(fixture.path,3,renderer,{'enabled':True,'reply':False}))
            ready['3001']['input_sha256']='b'*64
            self.assertFalse(host_wardrobe_ready(fixture.path,3,renderer,collector))
        finally:fixture.doCleanups()
