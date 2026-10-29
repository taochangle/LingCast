"""Manual speech, gifts, likes, comment replies, then welcomes; room revisions cancel stale events."""
import json


def pop_live_job(client, queue_key, avatar_id):
    raw=client.lpop(queue_key)
    if raw:
        return raw
    raw = None
    for kind in ('gift', 'like', 'external', 'welcome'):
        raw = client.lpop(f'live_{kind}_queue:{avatar_id}')
        if raw:
            break
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
