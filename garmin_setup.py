#!/usr/bin/env python3
"""
Run this once to authenticate with Garmin Connect and save tokens.
After this, the app logs in silently using the saved tokens.

Usage:
    python3 /home/rob/bike-flask/garmin_setup.py your@email.com yourpassword [mfa_code]
"""
import sys
from pathlib import Path
from garminconnect import Garmin

if len(sys.argv) < 3:
    print('Usage: python3 garmin_setup.py <email> <password> [mfa_code]')
    sys.exit(1)

email    = sys.argv[1]
password = sys.argv[2]
mfa_code = sys.argv[3] if len(sys.argv) > 3 else None

TOKEN_DIR = Path(__file__).parent / '.garmin_tokens'
TOKEN_DIR.mkdir(exist_ok=True)

api = Garmin(email=email, password=password, prompt_mfa=lambda: mfa_code)
api.login(tokenstore=str(TOKEN_DIR))

print(f'Done — tokens saved to {TOKEN_DIR}')
print(f'Display name: {api.display_name}')
