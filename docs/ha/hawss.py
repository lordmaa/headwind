"""Tiny Home Assistant websocket client (uses Headwind's saved URL + token; nothing printed but results)."""
import asyncio, json, sys
sys.path.insert(0, '/home/rob/bike-flask')
import websockets

def creds():
    from app import create_app
    app = create_app()
    with app.app_context():
        from services import homeassistant as ha
        c = ha._cfg()
        return c['url'], c['token']

async def _run(cmds):
    url, tok = creds()
    ws_url = url.replace('http', 'ws', 1).rstrip('/') + '/api/websocket'
    out = []
    async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
        await ws.recv()
        await ws.send(json.dumps({'type': 'auth', 'access_token': tok}))
        if json.loads(await ws.recv()).get('type') != 'auth_ok':
            raise SystemExit('auth failed')
        for i, cmd in enumerate(cmds, 1):
            await ws.send(json.dumps({'id': i, **cmd}))
            while True:
                m = json.loads(await ws.recv())
                if m.get('id') == i:
                    out.append(m); break
    return out

def call(*cmds):
    return asyncio.run(_run(list(cmds)))
