"""Manual jobs precede external events; room revisions cancel stale replies."""
import json


def pop_live_job(client, queue_key, avatar_id):
    raw=client.lpop(queue_key)
    if raw:
        return raw
    raw=client.lpop(f'live_external_queue:{avatar_id}')
    if not raw:
        return None
    try:
        event=json.loads(raw)
        config=json.loads(client.get(f'live_xhs_config:{avatar_id}') or '{}')
        if config.get('enabled') and event.get('xhs_revision')==config.get('revision'):
            return raw
    except (ValueError,TypeError,AttributeError):
        pass
    return None
