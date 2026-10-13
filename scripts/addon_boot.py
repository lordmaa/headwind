"""Home Assistant add-on start-up: runs once before gunicorn, only when Supervisor's token is present.

 - MQTT: asks Supervisor for the broker (the Mosquitto add-on) and stores it in Headwind's Settings, so there is nothing to type.
 - Home Assistant: points Headwind's HA connection at Supervisor's proxy with the add-on token, so the dashboard wizard just works.
Never raises past the caller's `|| echo`: a missing broker must not stop Headwind starting. Idempotent; refreshed every start
(Supervisor rotates the token). It never overwrites an MQTT host the user chose themselves unless that host is the one it set earlier.
"""
import os
import sqlite3
import sys

import requests

SUP = 'http://supervisor'


def supervisor(path):
    r = requests.get(SUP + path, headers={'Authorization': 'Bearer ' + os.environ['SUPERVISOR_TOKEN']}, timeout=10)
    r.raise_for_status()
    return r.json().get('data') or {}


def main():
    if not os.environ.get('SUPERVISOR_TOKEN'):
        print('addon_boot: not running under Supervisor, nothing to do')
        return 0
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app import create_app      # creates + migrates the database
    app = create_app()
    db_path = app.config['DATABASE']
    con = sqlite3.connect(db_path, timeout=30)
    con.execute('INSERT OR IGNORE INTO Settings (id) VALUES (1)')
    con.execute('UPDATE Settings SET haUrl=?, haToken=? WHERE id=1', [SUP + '/core', os.environ['SUPERVISOR_TOKEN']])
    try:
        m = supervisor('/services/mqtt')
    except Exception as e:
        m = {}
        print(f'addon_boot: no MQTT broker offered by Supervisor ({e.__class__.__name__}). Install the "Mosquitto broker" add-on, then restart Headwind.')
    if m.get('host'):
        cur = con.execute('SELECT mqttHost FROM Settings WHERE id=1').fetchone()[0] or ''
        marker = os.path.join(os.path.dirname(os.path.abspath(db_path)), '.addon_mqtt_host')
        ours = open(marker).read().strip() if os.path.exists(marker) else ''
        if not cur or cur == ours:
            con.execute('UPDATE Settings SET mqttHost=?, mqttPort=?, mqttUser=?, mqttPassword=? WHERE id=1',
                        [m['host'], int(m.get('port') or 1883), m.get('username') or '', m.get('password') or ''])
            open(marker, 'w').write(m['host'])
            print(f"addon_boot: MQTT set to {m['host']}:{m.get('port')}")
        else:
            print(f'addon_boot: leaving your own MQTT host ({cur}) alone')
    con.commit()
    con.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
