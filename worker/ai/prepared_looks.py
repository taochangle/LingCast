"""Read operator-installed appearance descriptors; never prepare faces on a live request."""
import json
import re
from pathlib import Path
from ai.wardrobe_profiles import LOOK_PROFILES


def prepared_looks(path):
    path=Path(path)
    if not path.exists():return {}
    data=json.loads(path.read_text())
    profiles={str(value[0]):value[1] for value in LOOK_PROFILES.values()}
    if set(data)-set(profiles):raise ValueError('Only reserved Ruotang appearance IDs are supported')
    for key,look in data.items():
        if (look.get('profile')!=profiles[key] or not isinstance(look.get('base_key'),str)
                or not look['base_key'].startswith('scene_videos/')
                or not re.fullmatch('[a-f0-9]{64}',look.get('input_sha256',''))):
            raise ValueError('Invalid prepared Ruotang look')
    return data
