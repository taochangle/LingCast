"""Reserved appearances of the same presenter, not separate people/voices."""
LOOK_PROFILES = {
    'blue': (3001, 'ruotang_blue'),
    'burgundy': (3002, 'ruotang_burgundy'),
    'champagne': (3003, 'ruotang_champagne'),
}
LOOK_RENDERERS = {'white': 3, **{name: value[0] for name, value in LOOK_PROFILES.items()}}
