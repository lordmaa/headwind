import requests
from database import query_db


def send_notification(title, body, click_url=None):
    settings = query_db('SELECT ntfyUrl FROM Settings WHERE id=1', one=True)
    url = settings['ntfyUrl'] if settings else None
    if not url:
        return

    headers = {'Title': title}
    if click_url:
        headers['Click'] = click_url

    try:
        requests.post(url, data=body.encode('utf-8'), headers=headers, timeout=5)
    except Exception:
        pass
