import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from streaming.relay import RelayManager


class RelayTests(unittest.TestCase):
    def test_start_failure_requires_explicit_retry_and_stop(self):
        store = {"live_relay_config:1": json.dumps({"server": "rtmp://example.com/live",
                 "streamKey": "private-key", "enabled": True, "revision": 1})}
        redis = MagicMock()
        redis.scan_iter.side_effect = lambda _: list(store)
        redis.get.side_effect = store.get
        sessions = {}
        manager = RelayManager(redis, sessions)
        with patch("streaming.relay.subprocess.Popen") as popen:
            manager.tick()
            popen.assert_not_called()
            sessions[1] = SimpleNamespace(ready=True, _running=True)
            process = popen.return_value
            process.poll.return_value = None
            manager.tick()
            popen.assert_called_once()
            manager.tick()
            popen.assert_called_once()
            process.poll.return_value = 1
            manager.tick()
            manager.tick()
            popen.assert_called_once()
            self.assertIn(1, manager.failed)
            config = json.loads(store["live_relay_config:1"])
            config["revision"] = 2
            store["live_relay_config:1"] = json.dumps(config)
            process.poll.return_value = None
            manager.tick()
            self.assertEqual(popen.call_count, 2)
            config["enabled"] = False
            store["live_relay_config:1"] = json.dumps(config)
            manager.tick()
            process.terminate.assert_called_once()
            self.assertNotIn(1, manager.processes)


if __name__ == '__main__':
    unittest.main()
