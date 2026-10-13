"""Alcohol and recovery: how the morning after a drinking day compares with the morning after a dry day, from the Garmin sleep and heart-rate data
already in Headwind. Correlation, not proof (a late night out also means a short night), so the page says so."""
import re
from datetime import date, timedelta

MIN_DRINK_DAYS = 5          # fewer than this and the comparison is noise
HEAVY_UNITS = 2.0           # a "drinking day" is 2+ units; a lighter day is left out of both groups
METRICS = [('restingHR', 'Resting heart rate (next morning)', 'bpm', 0, False),
           ('hrv', 'Heart-rate variability (next morning)', 'ms', 0, True),
           ('sleepHours', 'Sleep that night', 'h', 1, True)]      # (column, label, unit, decimals, higher is better)


def recovery_comparison(db, rider_id):
    """None if there is nothing logged yet, else {'drink_days', 'dry_days', 'rows': [{label, unit, drink, dry, diff, better_when_higher, n_drink, n_dry}], 'enough'}."""
    units = {r['logDate']: r['u'] for r in db.execute(
        "SELECT logDate, COALESCE(SUM(alcoholUnits), 0) u FROM FoodLog WHERE riderId=? GROUP BY logDate", [rider_id])}
    logged_food = {r[0] for r in db.execute("SELECT DISTINCT logDate FROM FoodLog WHERE riderId=? AND calories > 0", [rider_id])}
    if not any(u > 0 for u in units.values()):
        return None
    garmin = {r['date']: r for r in db.execute('SELECT date, restingHR, hrv, sleepHours FROM GarminDaily')}
    drink, dry = [], []
    for day in logged_food:                 # a day only counts as dry if food was logged that day (an unlogged day is unknown, not dry)
        try:
            nxt = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
        except ValueError:
            continue
        g = garmin.get(nxt)
        if not g:
            continue
        u = units.get(day, 0)
        if u >= HEAVY_UNITS:
            drink.append(g)
        elif u == 0:
            dry.append(g)
    rows = []
    for col, label, unit, dec, higher in METRICS:
        a = [g[col] for g in drink if g[col] is not None]
        b = [g[col] for g in dry if g[col] is not None]
        if not a or not b:
            continue
        ma, mb = sum(a) / len(a), sum(b) / len(b)
        rows.append({'label': label, 'unit': unit, 'drink': round(ma, dec) if dec else round(ma), 'dry': round(mb, dec) if dec else round(mb),
                     'diff': round(ma - mb, dec) if dec else round(ma - mb), 'better_when_higher': higher, 'n_drink': len(a), 'n_dry': len(b)})
    return {'drink_days': len(drink), 'dry_days': len(dry), 'rows': rows, 'enough': len(drink) >= MIN_DRINK_DAYS and bool(rows)}


# ── Recognising a drink in the food database ─────────────────────────────
# Open Food Facts often has no strength (or even category) for a drink: a scanned Jack Daniel's had calories and nothing else. So a drink is recognised by what it is
# called, the card then asks for the strength (pre-filled with a typical one, which you can change), and the strength is remembered for that product.
# (keyword, typical % ABV). Order matters: the first match wins, so specific names come before general ones.
_TYPICAL_ABV = [
    ('jack daniel', 40), ('jameson', 40), ('smirnoff', 37.5), ('gordon', 37.5), ('bacardi', 37.5), ('captain morgan', 35), ('baileys', 17), ('jagermeister', 35), ('jägermeister', 35),
    ('guinness', 4.2), ('stella', 4.6), ('carling', 4), ('heineken', 5), ('corona', 4.5), ('peroni', 5.1), ('budweiser', 4.5), ('carlsberg', 3.8), ('kronenbourg', 4.2), ('san miguel', 5),
    ('prosecco', 11), ('champagne', 12), ('cava', 11.5), ('sherry', 17), ('port wine', 19.5),
    ('whisky', 40), ('whiskey', 40), ('bourbon', 40), ('scotch', 40), ('vodka', 40), ('gin', 40), ('rum', 40), ('tequila', 40), ('brandy', 40), ('cognac', 40), ('liqueur', 20),
    ('lager', 4.5), ('stout', 4.5), ('ipa', 5), ('bitter', 4.2), ('pale ale', 4.5), ('beer', 4.5), ('cider', 4.5), ('ale', 4.5),
    ('red wine', 13), ('white wine', 12.5), ('rose wine', 12), ('rosé', 12), ('wine', 12.5), ('spritz', 8), ('hard seltzer', 4.5),
]
# words that mean "this is not the drink": sauces and food flavoured with it, and alcohol-free versions
_NOT_A_DRINK = ('sauce', 'glaze', 'marinade', 'seasoning', 'flavour', 'flavor', 'flavoured', 'chocolate', 'cake', 'ice cream', 'pudding', 'jelly', 'sausage', 'bbq', 'barbecue', 'gravy',
                'alcohol free', 'alcohol-free', 'non-alcoholic', 'non alcoholic', 'zero alcohol', '0.0', '0%', 'free from alcohol', 'ginger', 'gingerbread', 'ginger beer', 'root beer', 'rum raisin')


# a spirit's name on a ready-to-drink can or a mixer ("Jack Daniel's Cola", a seltzer) is a weak drink, not 40%: still a drink, but no typical strength is guessed
_READY_TO_DRINK = ('cola', 'coke', 'seltzer', 'soda', 'tonic', 'lemonade', 'mixer', 'premix', 'pre-mix', 'ready to drink', ' & ', ' and ', 'cocktail', 'spritz', 'rtd', 'shot', 'jelly')
_ZERO = re.compile(r'(^|\s)0(\.0)?\s*%?(\s|$)')           # "Guinness 0", "Lager 0.0", "0%"


def recognise(name, brand=None):
    """(is_a_drink, typical_abv_or_None) for a food named `name`. A drink is recognised by what it is called; the strength is only a typical one for the card to show for
    you to confirm, and is left out when the product is a ready-to-drink mix, where the spirit's own 40% would be far too high."""
    text = f' {name or ""} {brand or ""} '.lower()
    if any(w in text for w in _NOT_A_DRINK) or _ZERO.search(text):
        return False, None
    for key, abv in _TYPICAL_ABV:
        # whole-word match for the short keywords (gin, rum, ale, ipa) so "ginger", "rumble" or "pale" do not match
        hit = bool(re.search(r'(?<![a-z])' + re.escape(key) + r'(?![a-z])', text)) if len(key) <= 4 else key in text
        if hit:
            return True, (None if any(w in text for w in _READY_TO_DRINK) else abv)
    return False, None


def typical_abv(name, brand=None):
    """A typical % ABV for a drink recognised by name, else None (see `recognise`)."""
    return recognise(name, brand)[1]


def remember_abv(db, barcode, abv):
    """Keep a drink's strength with its food-database record, so the next scan or search needs no typing. Does not commit; a barcode with no cached record is ignored."""
    if barcode and abv and 0 < float(abv) <= 100:
        db.execute('UPDATE FoodCache SET abv=? WHERE key=?', [float(abv), str(barcode)])
