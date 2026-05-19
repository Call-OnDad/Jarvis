import requests
import os
from dotenv import load_dotenv

CONFIG_SERVER = "http://192.168.0.6/get_config.php"

_config = {}

def _load():
    global _config
    try:
        resp = requests.get(CONFIG_SERVER, timeout=5)
        resp.raise_for_status()
        _config = resp.json()
        print("[config] Loaded from server.")
        return
    except Exception as e:
        print(f"[config] Server unreachable ({e}), falling back to .env")

    load_dotenv()
    _config = dict(os.environ)

def get(key, default=""):
    return _config.get(key, default)

_load()
