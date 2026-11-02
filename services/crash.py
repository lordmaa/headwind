"""Crash reports from the phone app: stored as files and published to MQTT so they show up in Home Assistant / MQTT Explorer.
The phone sends them itself over MQTT when it has a broker configured; this is the fallback through the server (paired phones with no broker)."""
import json
import logging
import os
import re
import time

log = logging.getLogger(__name__)
DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'crash-reports')
KEEP = 50


def store(report, who='rider'):
    if not isinstance(report, dict):
        return None
    os.makedirs(DIR, exist_ok=True)
    slug = re.sub(r'[^a-z0-9]+', '_', (who or 'rider').lower()).strip('_') or 'rider'
    name = '%s-%s.json' % (time.strftime('%Y%m%d-%H%M%S'), slug)
    with open(os.path.join(DIR, name), 'w') as f:
        json.dump(report, f, indent=1)
    for old in sorted(os.listdir(DIR))[:-KEEP]:
        try:
            os.remove(os.path.join(DIR, old))
        except OSError:
            pass
    return name, slug


def publish(report, slug):
    from services import live, mqtt
    conn = live._connection()
    if not conn:
        return False
    try:
        mqtt._broker_send([{'topic': 'headwind/crash/%s' % slug, 'payload': json.dumps(report), 'retain': False, 'qos': 1}], *conn)
        return True
    except Exception as e:
        log.warning('crash report publish failed (non-fatal): %s', e)
        return False
