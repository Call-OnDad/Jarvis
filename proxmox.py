"""
AHAS -- proxmox.py
Proxmox API functions using the token from config.
"""

import requests
import urllib3
from config import (
    PROXMOX_HOST, PROXMOX_PORT, PROXMOX_NODE,
    PROXMOX_API_TOKEN, PROXMOX_VERIFY_SSL, CONTAINER_NAMES, SERVICES
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = f"https://{PROXMOX_HOST}:{PROXMOX_PORT}/api2/json"
HEADERS = {"Authorization": PROXMOX_API_TOKEN}


def _get(path):
    try:
        r = requests.get(f"{BASE}{path}", headers=HEADERS, verify=PROXMOX_VERIFY_SSL, timeout=10)
        r.raise_for_status()
        return r.json().get("data", {})
    except Exception as e:
        return {"error": str(e)}


def _post(path):
    try:
        r = requests.post(f"{BASE}{path}", headers=HEADERS, verify=PROXMOX_VERIFY_SSL, timeout=10)
        r.raise_for_status()
        return r.json().get("data", {})
    except Exception as e:
        return {"error": str(e)}


def get_host_status():
    data = _get(f"/nodes/{PROXMOX_NODE}/status")
    if "error" in data:
        return f"Could not reach Proxmox: {data['error']}"

    cpu   = round(data.get("cpu", 0) * 100, 1)
    mem   = data.get("memory", {})
    mem_used  = round(mem.get("used", 0) / 1024**3, 1)
    mem_total = round(mem.get("total", 0) / 1024**3, 1)
    mem_pct   = round(mem_used / mem_total * 100, 1) if mem_total else 0
    swap  = data.get("swap", {})
    swap_used  = round(swap.get("used", 0) / 1024**3, 1)
    swap_total = round(swap.get("total", 0) / 1024**3, 1)
    load  = data.get("loadavg", [0, 0, 0])

    return (
        f"Proxmox host is online. "
        f"CPU at {cpu}%, "
        f"RAM {mem_used} of {mem_total} gigabytes used ({mem_pct}%), "
        f"Swap {swap_used} of {swap_total} gigabytes, "
        f"Load average {load[0]}, {load[1]}, {load[2]}."
    )


def get_container_list():
    data = _get(f"/nodes/{PROXMOX_NODE}/lxc")
    if isinstance(data, dict) and "error" in data:
        return f"Could not list containers: {data['error']}"

    running, stopped = [], []
    for ct in sorted(data, key=lambda x: int(x.get("vmid", 0))):
        vmid   = str(ct.get("vmid"))
        name   = CONTAINER_NAMES.get(vmid, ct.get("name", f"CT{vmid}"))
        status = ct.get("status", "unknown")
        if status == "running":
            running.append(f"{name} (CT{vmid})")
        else:
            stopped.append(f"{name} (CT{vmid})")

    result = f"{len(running)} containers running: {', '.join(running)}. "
    if stopped:
        result += f"{len(stopped)} stopped: {', '.join(stopped)}."
    return result


def container_action(vmid: str, action: str):
    if action not in ("start", "stop"):
        return "Invalid action. Use start or stop."
    name = CONTAINER_NAMES.get(vmid, f"CT{vmid}")
    result = _post(f"/nodes/{PROXMOX_NODE}/lxc/{vmid}/status/{action}")
    if isinstance(result, dict) and "error" in result:
        return f"Failed to {action} {name}: {result['error']}"
    return f"{name} {action} command sent successfully."


def get_disk_usage():
    data = _get(f"/nodes/{PROXMOX_NODE}/storage")
    if isinstance(data, dict) and "error" in data:
        return f"Could not retrieve storage: {data['error']}"

    lines = []
    for store in data:
        name  = store.get("storage", "?")
        total = store.get("total", 0)
        used  = store.get("used", 0)
        if total:
            pct = round(used / total * 100, 1)
            total_gb = round(total / 1024**3, 0)
            lines.append(f"{name}: {pct}% of {int(total_gb)} gigabytes")
    return "Storage: " + ", ".join(lines) + "."


def check_services():
    import requests as req
    up, down = [], []
    for name, url in SERVICES.items():
        try:
            r = req.get(url, timeout=5, allow_redirects=True)
            if r.status_code < 500:
                up.append(name)
            else:
                down.append(name)
        except Exception:
            down.append(name)

    result = f"{len(up)} services online: {', '.join(up)}. "
    if down:
        result += f"{len(down)} unreachable: {', '.join(down)}."
    return result
