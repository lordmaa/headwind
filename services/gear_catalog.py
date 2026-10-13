"""The parts an enthusiast actually changes, with sensible starting intervals. These are only DEFAULTS: every number can be edited per part.

Distances are metres (like everything in Headwind); the miles below are what people think in. `check_*` is "look at / measure / lube this", `replace_*`
is "swap it". An item can use distance, days, or both (whichever comes first). `service_only` items are things you maintain rather than replace.
"""
MI = 1609.344


def _mi(x):
    return None if x is None else round(x * MI)


def _item(kind, label, group, check_mi=None, replace_mi=None, check_days=None, replace_days=None, note='', service_only=False):
    return {'kind': kind, 'label': label, 'group': group, 'check_m': _mi(check_mi), 'replace_m': _mi(replace_mi), 'check_days': check_days,
            'replace_days': replace_days, 'note': note, 'service_only': service_only}


CATALOG = [
    _item('chain', 'Chain', 'Drivetrain', 1000, 2500, note='Measure stretch with a chain checker; replace at 0.5-0.75% (before it wears the cassette).'),
    _item('chain_lube', 'Clean and lube chain', 'Drivetrain', 150, None, service_only=True, note='Wet conditions: more often.'),
    _item('cassette', 'Cassette', 'Drivetrain', 5000, 8000, note='Usually outlasts two or three chains if you replace the chain on time.'),
    _item('chainrings', 'Chainrings', 'Drivetrain', 6000, 12000),
    _item('jockey_wheels', 'Jockey wheels', 'Drivetrain', 3000, 6000),
    _item('bottom_bracket', 'Bottom bracket', 'Drivetrain', 4000, 10000, note='Creaks or roughness mean it is due.'),
    _item('cleats', 'Cleats', 'Drivetrain', 1500, 4000, note='Worn cleats release too easily or stick.'),
    _item('tyre_front', 'Front tyre', 'Wheels', 1000, 4000, note='Check for cuts, flats spots and a squared-off profile.'),
    _item('tyre_rear', 'Rear tyre', 'Wheels', 1000, 2500),
    _item('sealant', 'Tubeless sealant', 'Wheels', check_days=45, replace_days=120, note='Dries out: top up or replace every few months (less in hot weather).', service_only=True),
    _item('wheel_bearings', 'Hub / wheel bearings', 'Wheels', 3000, 10000),
    _item('spokes_true', 'True wheels / check spoke tension', 'Wheels', 3000, None, check_days=365, service_only=True),
    _item('brake_pads', 'Brake pads', 'Brakes', 500, 2500, note='Wear depends heavily on wet riding and hills.'),
    _item('brake_rotors', 'Brake rotors', 'Brakes', 2000, 8000),
    _item('brake_fluid', 'Brake fluid (hydraulic)', 'Brakes', check_days=365, replace_days=730, note='Bleed every one to two years.'),
    _item('cables', 'Gear / brake cables and housing', 'Cockpit', 3000, 6000, replace_days=730),
    _item('bar_tape', 'Bar tape / grips', 'Cockpit', 1500, 4000, replace_days=540),
    _item('headset', 'Headset bearings', 'Cockpit', 3000, 12000, note='Strip, clean and re-grease yearly; replace if notchy.'),
    _item('suspension', 'Fork / shock service', 'Suspension', check_days=180, replace_days=365, service_only=True),
    _item('dropper_post', 'Dropper post service', 'Suspension', check_days=180, replace_days=365, service_only=True),
    _item('custom', 'Other part', 'Other', note='Anything else you change: pick your own intervals.'),
]
BY_KIND = {c['kind']: c for c in CATALOG}
GROUPS = []
for _c in CATALOG:
    if _c['group'] not in GROUPS:
        GROUPS.append(_c['group'])
