"""
AHAS -- Antony's Home Automation System
config.py -- All settings in one place. Edit this, nothing else.
"""

import os

ASSISTANT_NAME   = "AHAS"
HOT_WORDS        = ["ahas", "jarvis"]
OWNER_NAME       = "Antony"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL      = "claude-sonnet-4-20250514"

SYSTEM_PROMPT = f"""You are {ASSISTANT_NAME}, the home automation and intelligence system for {OWNER_NAME}'s homelab.
You manage a Proxmox server, multiple LXC containers, web domains, and services.
Be concise, direct, and professional. When you need to run a command or action,
append a command tag at the end of your response using this exact format:
  #command:proxmox_status
  #command:container_list
  #command:container_start:101
  #command:container_stop:101
  #command:disk_health
  #command:service_status
Only append one command per response. For questions that don't need live data, answer directly.
"""

TTS_ENGINE       = "edge"
TTS_VOICE        = "en-GB-RyanNeural"
OPENAI_TTS_VOICE = "echo"

WHISPER_MODEL         = "tiny.en"
POST_SPEECH_SILENCE   = 0.1
SILERO_SENSITIVITY    = 0.4

PROXMOX_HOST      = "192.168.0.10"
PROXMOX_PORT      = 8006
PROXMOX_NODE      = "proxmox"
PROXMOX_API_TOKEN = os.getenv(
    "PROXMOX_API_TOKEN",
    "PVEAPIToken=devilscousin@pam!dashboard=d04256ec-107c-41ea-964f-7cd64a9f172c"
)
PROXMOX_VERIFY_SSL = False

CONTAINER_NAMES = {
    "101": "Media Stack",
    "102": "MariaDB",
    "103": "Recyclarr",
    "104": "Node.js Signaling",
    "105": "Pi-hole",
    "107": "Proxmox Backup Server",
    "109": "CrowdSec",
    "111": "Ollama",
    "112": "n8n",
    "113": "Open WebUI",
    "114": "WordPress",
    "115": "Postiz",
    "117": "AHAS",
    "500": "Caddy Proxy",
}

DB_HOST     = "192.168.0.6"
DB_PORT     = 3306
DB_NAME     = "homelab_monitor"
DB_USER     = os.getenv("DB_USER", "monitor_web")
DB_PASSWORD = os.getenv("DB_PASSWORD", "Mon1t0rR3ad!")

DOMAINS = ["call-on.dad", "call-on.mom", "call-on.media", "call-on.shop"]

SERVICES = {
    "Homarr":     "http://192.168.0.34:7575",
    "n8n":        "http://192.168.0.28:5678",
    "Open WebUI": "http://192.168.0.45:3000",
    "Plex":       "http://192.168.0.34:32400",
    "Ollama":     "http://192.168.0.41:11434",
    "Pi-hole":    "http://192.168.0.3/admin",
}
