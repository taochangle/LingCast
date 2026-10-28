import copy
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from botocore.exceptions import ClientError
from ai.resident_client import ResidentRenderer


class MemoryS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        return {'Body': io.BytesIO(self.objects[Key])}

    def head_object(self, Bucket, Key):
        data = self.get_object(Bucket, Key)['Body'].read()
        return {'ETag': hashlib.md5(data).hexdigest(), 'ContentLength': len(data)}


class ResidentCacheTests(unittest.TestCase):
    def setUp(self):
        self.client = MemoryS3()
        self.renderer = ResidentRenderer.__new__(ResidentRenderer)
        self.renderer.storage = Mock(client=self.client, bucket='test', endpoint='memory')
        self.renderer.storage.download.side_effect = lambda key, path: Path(path).write_bytes(self.client.objects[key])
        self.renderer.signed = lambda key, upload=False: key
        self.renderer.tts_url, self.renderer.latent_url = 'tts', 'latent'
        self.identities = {'tts': 'voice1', 'latent': 'steps10'}
        self.renderer.identity = self.identities.__getitem__
        def generate(url, payload):
            key = payload['output_key']
            self.client.objects[key] = (url + str(payload)).encode()
            return {'s3_key': key, 'metrics': {'audio_seconds': 3, 'steps': 10}}
        self.renderer.job = Mock(side_effect=generate)
        self.client.objects['base'] = b'base-v1'

    def test_repeat_and_invalidation(self):
        with tempfile.TemporaryDirectory() as directory:
            first = self.renderer.render('你好', 'base', directory)
            second = self.renderer.render('你好', 'base', directory)
            self.assertEqual(self.renderer.job.call_count, 2)
            self.assertTrue(second.metrics['cache_hit'])
            self.assertTrue(second.metrics['tts']['cache_hit'])
            self.assertNotEqual(first.path, second.path)
            self.identities['latent'] = 'steps8'
            self.renderer.render('你好', 'base', directory)
            self.assertEqual(self.renderer.job.call_count, 3)
            self.client.objects['base'] = b'base-v2'
            self.renderer.render('你好', 'base', directory)
            self.assertEqual(self.renderer.job.call_count, 4)
            self.identities['tts'] = 'voice2'
            self.renderer.render('你好', 'base', directory)
            self.assertEqual(self.renderer.job.call_count, 6)
            self.renderer.render('再见', 'base', directory)
            self.assertEqual(self.renderer.job.call_count, 8)

    def test_missing_media_and_failure_do_not_poison_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            first = self.renderer.render('你好', 'base', directory)
            del self.client.objects[first.s3_key]
            self.renderer.render('你好', 'base', directory)
            self.assertEqual(self.renderer.job.call_count, 3)
            generate = self.renderer.job.side_effect
            self.renderer.job.side_effect = RuntimeError('inference failed')
            with self.assertRaises(RuntimeError):
                self.renderer.render('新句子', 'base', directory)
            self.renderer.job.side_effect = generate
            result = self.renderer.render('新句子', 'base', directory)
            self.assertFalse(result.metrics['cache_hit'])


if __name__ == '__main__':
    unittest.main()
