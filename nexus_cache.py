"""
nexus_cache.py — Background polling cache + monitoring loop.
Polls Proxmox, HA, and services every 60s.
Dashboard endpoints read from cache — instant responses, no upstream wait.
Monitoring: alerts via Discord webhook when things go wrong.
"""

import threading, time, json, os, re, urllib.request, urllib.error
import sys
sys.path.insert(0, '/opt/ahas')

_cache = {}
_lock  = threading.Lock()

POLL_INTERVAL   = 60    # seconds between polls
ALERT_EVERY     = 5     # alert check every N polls (= 5 minutes)
DISK_WARN_PCT   = 80
DISK_CRIT_PCT   = 90


# ── Cache read/write ──────────────────────────────────────────────────────────

def cache_get(key):
    """Return {data, ts} or None."""
    with _lock:
        return _cache.get(key)

def cache_set(key, value):
    with _lock:
        _cache[key] = {"data": value, "ts": time.time()}

def cache_age(key):
    """Return seconds since last update, or None if not cached."""
    entry = cache_get(key)
    if not entry:
        return None
    return time.time() - entry["ts"]

def all_ages():
    with _lock:
        return {k: round(time.time() - v["ts"]) for k, v in _cache.items()}


# ── Pollers ───────────────────────────────────────────────────────────────────

def _poll_proxmox():
    try:
        import proxmox as px
        cache_set("proxmox_host",       px.get_host_status())
        cache_set("proxmox_containers", px.get_container_list())
        cache_set("proxmox_storage",    px.get_disk_usage())
    except Exception as e:
        print(f"[cache] proxmox poll error: {e}")


def _poll_ha():
    try:
        HA_URL   = "http://192.168.0.9:8123"
        HA_TOKEN = os.environ.get("HA_TOKEN", "")
        if not HA_TOKEN:
            return
        req = urllib.request.Request(
            f"{HA_URL}/api/states",
            headers={"Authorization": f"Bearer {HA_TOKEN}"}
        )
        with urllib.request.urlopen(req, timeout=6) as r:
            states = json.loads(r.read())

        summary = {}
        domains = ("light", "switch", "climate", "media_player", "sensor", "binary_sensor")
        for s in states:
            domain = s["entity_id"].split(".")[0]
            if domain in domains:
                name = s.get("attributes", {}).get("friendly_name", s["entity_id"])
                attrs = {}
                if domain == "light" and s["state"] == "on":
                    attrs["brightness"] = s.get("attributes", {}).get("brightness")
                elif domain == "climate":
                    attrs["temp"] = s.get("attributes", {}).get("current_temperature")
                    attrs["target"] = s.get("attributes", {}).get("temperature")
                summary[name] = {"state": s["state"], **{k: v for k, v in attrs.items() if v is not None}}

        cache_set("ha_summary", summary)
    except Exception as e:
        print(f"[cache] HA poll error: {e}")


def _poll_services():
    UP_CODES = {200, 301, 302, 401, 403}
    checks = [
        ("ha",     "http://192.168.0.9:8123"),
        ("n8n",    "http://192.168.0.28:5678"),
        ("pihole", "http://192.168.0.3"),
        ("plex",   "http://192.168.0.34:32400"),
        ("nexus",  "http://127.0.0.1:5000/health"),
    ]
    statuses = {}
    for name, url in checks:
        try:
            urllib.request.urlopen(url, timeout=3)
            statuses[name] = "up"
        except urllib.error.HTTPError as e:
            statuses[name] = "up" if e.code in UP_CODES else "down"
        except Exception:
            statuses[name] = "down"
    cache_set("service_status", statuses)


# ── Monitoring alerts ─────────────────────────────────────────────────────────

_prev_alerts = set()   # avoid repeat-spamming same alert

def _check_alerts():
    alerts = []

    # Disk usage
    disk_entry = cache_get("proxmox_storage")
    if disk_entry:
        pcts = re.findall(r'(\d+)%', str(disk_entry["data"]))
        for pct in pcts:
            n = int(pct)
            if n >= DISK_CRIT_PCT:
                alerts.append(f"🔴 Storage at {pct}% — critical")
            elif n >= DISK_WARN_PCT:
                alerts.append(f"🟡 Storage at {pct}% — watch it")

    # Services down
    svc_entry = cache_get("service_status")
    if svc_entry:
        down = [k for k, v in svc_entry["data"].items() if v == "down"]
        if down:
            alerts.append(f"🔴 Services down: {', '.join(down)}")

    # Containers stopped (parse container list for "stopped")
    ctr_entry = cache_get("proxmox_containers")
    if ctr_entry and "stopped" in str(ctr_entry["data"]).lower():
        alerts.append("⚠️ Container(s) in stopped state — check Proxmox")

    # Only post new alerts (don't repeat every 5 min)
    new_alerts = [a for a in alerts if a not in _prev_alerts]
    _prev_alerts.clear()
    _prev_alerts.update(alerts)

    if new_alerts:
        msg = "**NEXUS Monitor**\n" + "\n".join(new_alerts)
        _post_alert(msg)


def _post_alert(message):
    webhook = os.environ.get("DISCORD_WEBHOOK", "")
    if not webhook:
        return
    try:
        payload = json.dumps({"content": message[:1900], "username": "NEXUS"}).encode()
        req = urllib.request.Request(
            webhook, data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "DiscordBot/nexus"},
            method="POST"
        )
        urllib.request.urlopen(req, timeout=8)
    except Exception as e:
        print(f"[cache] alert post failed: {e}")


# ── Background loop ───────────────────────────────────────────────────────────

def _loop():
    # Initial warm-up poll
    _poll_proxmox()
    _poll_ha()
    _poll_services()
    print("[cache] initial poll complete")

    tick = 0
    while True:
        time.sleep(POLL_INTERVAL)
        _poll_proxmox()
        _poll_ha()
        _poll_services()
        tick += 1
        if tick % ALERT_EVERY == 0:
            _check_alerts()


def start():
    """Start background polling thread. Call once at app startup."""
    t = threading.Thread(target=_loop, daemon=True, name="nexus-cache")
    t.start()
    print("[cache] background poller started (60s interval)")
