from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from openai import OpenAI
import os, sys, urllib.request, urllib.error, json, subprocess, threading, ssl
from concurrent.futures import ThreadPoolExecutor, as_completed
import asyncio, base64, uuid, mimetypes, imaplib, email as emaillib, smtplib
from email.header import decode_header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

sys.path.insert(0, '/opt/ahas')
import proxmox as px
import nexus_memory
import nexus_cache

UPLOAD_DIR = "/opt/ahas/uploads"

app = Flask(__name__, static_folder='static', static_url_path='')
limiter = Limiter(get_remote_address, app=app, default_limits=[])
# Route all LLM calls through LiteLLM proxy on CT112 (Groq free tier + OpenRouter fallbacks)
# LiteLLM exposes OpenAI-compatible API — use openai SDK pointed at proxy
LITELLM_KEY  = os.environ.get("LITELLM_MASTER_KEY", "")
LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://192.168.0.28:4000")
client = OpenAI(api_key=LITELLM_KEY, base_url=LITELLM_BASE + "/v1")

# Agent model routing: exec agents (SSH/tools) → agent-exec, others → agent-standard
_EXEC_AGENTS = {"dev", "infra", "security", "manager"}
def _agent_model(dept: str) -> str:
    return "agent-exec" if dept in _EXEC_AGENTS else "agent-standard"

HA_URL        = "http://192.168.0.9:8123"
HA_TOKEN      = os.environ.get("HA_TOKEN", "")
GMAIL_USER    = os.environ.get("GMAIL_USER", "mediaserver2407@gmail.com")
GMAIL_PASS    = os.environ.get("GMAIL_APP_PASSWORD", "")
LOCATION      = os.environ.get("LOCATION", "Swadlincote,UK")
TTS_VOICE     = "en-GB-SoniaNeural"

# Discord
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNELS  = {
    "general":   os.environ.get("DISCORD_CHANNEL_GENERAL",   ""),
    "marketing": os.environ.get("DISCORD_CHANNEL_MARKETING", ""),
    "seo":       os.environ.get("DISCORD_CHANNEL_SEO",       ""),
    "dev":       os.environ.get("DISCORD_CHANNEL_DEV",       ""),
    "content":   os.environ.get("DISCORD_CHANNEL_CONTENT",   ""),
    "infra":     os.environ.get("DISCORD_CHANNEL_INFRA",     ""),
    "business":  os.environ.get("DISCORD_CHANNEL_BUSINESS",  ""),
    "community": os.environ.get("DISCORD_CHANNEL_COMMUNITY", ""),
    "security":  os.environ.get("DISCORD_CHANNEL_SECURITY",  ""),
    "manager":   os.environ.get("DISCORD_CHANNEL_MANAGER",   ""),
}

# SMTP (outbound email — MailerSend)
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.mailersend.net")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "nexus@call-on.media")

# n8n
N8N_URL     = os.environ.get("N8N_URL", "http://192.168.0.28:5678")
N8N_API_KEY = os.environ.get("N8N_API_KEY", "")

# GA4 properties (used by agent tool + dashboard)
GA4_PROPERTIES = {
    "call-on.dad":   os.environ.get("GA4_PROPERTY_ID_DAD",   ""),
    "call-on.mom":   os.environ.get("GA4_PROPERTY_ID_MOM",   ""),
    "call-on.media": os.environ.get("GA4_PROPERTY_ID_MEDIA", ""),
    "call-on.shop":  os.environ.get("GA4_PROPERTY_ID_SHOP",  ""),
}
GA4_CACHE_TTL = 600

# Cloudflare
CF_GRAPHQL   = "https://api.cloudflare.com/client/v4/graphql"
CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "")
CF_ZONES     = {
    "call-on.dad":   os.environ.get("CF_ZONE_ID_DAD",   ""),
    "call-on.mom":   os.environ.get("CF_ZONE_ID_MOM",   ""),
    "call-on.media": os.environ.get("CF_ZONE_ID_MEDIA", ""),
    "call-on.shop":  os.environ.get("CF_ZONE_ID_SHOP",  ""),
}
CF_CACHE_TTL = 600

# Admin proxy
CT500_ADMIN = "https://call-on.dad"
STATS_KEY   = os.environ.get("STATS_KEY", "")

_ADMIN_ALLOWED_ACTIONS = {
    "vol_verify", "vol_status", "vol_delete",
    "user_status", "user_verify", "user_delete",
    "msg_read", "msg_delete",
    "video_approve", "video_reject",
    "email_send",
    "topic_add", "topic_hide", "topic_unhide", "topic_delete",
    "order_status",
}

DB_HOST = "192.168.0.6"
DB_PORT = 3306


# ── NEXUS system prompt ───────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are NEXUS — Antony's personal AI assistant and the central intelligence for Call-On Ltd. Sharp, confident, direct. Warm but efficient. She/her.

ANTONY:
- 48, self-taught developer, works nights. UK-based.
- Runs Call-On Ltd solo — business, tech, content, marketing all him.
- Email: mediaserver2407@gmail.com

HOMELAB (Proxmox host 192.168.0.10):
CT101=Media Stack (Plex/Sonarr/Radarr) | CT102=MariaDB | CT104=Node.js Signalling | CT105=Pi-hole DNS | CT107=PBS | CT109=CrowdSec | CT112=n8n+discord-agent | CT116=ComfyUI | CT117=NEXUS (you) | CT118=Revive Adserver | CT500=Caddy proxy
Network: Headscale VPN (100.64.x.x range, was Tailscale 100.71.x.x — that's gone).

BUSINESS — Call-On Ltd:
- call-on.dad: UK dads community (main revenue site)
- call-on.mom: UK moms community
- call-on.media: company landing page
- call-on.shop: Printful store (CDO Range — 6 products, GBP)
- DB: MariaDB on CT102 (192.168.0.6) — Callon-dad, Callon-mom schemas
- SMTP: MailerSend smtp.mailersend.net:587
- Ads: Revive Adserver at ads.call-on.media — 4 zones (dad=1, mom=2, media=3, shop=4)
- GA4: tracking on all four properties

COMPANY TEAM (agents you can delegate to):
Use call_agent(dept, task) to assign work to a specialist and get their response back.
- manager: coordinates multi-dept tasks, approves spend <£100, routes complex briefs
- marketing: campaigns, social, paid/organic growth
- seo: keyword strategy, technical SEO, rankings
- dev: all code, deployments, technical implementation
- content: blog posts, copy, social captions, email newsletters
- infra: container health, service restarts, homelab maintenance
- business: commercial strategy, costs, vendor relationships, shop
- community: community health, user growth, moderation
- security: threats, CrowdSec, access logs, SSL
- general: catch-all for anything that doesn't fit

YOUR ROLE AS PERSONAL ASSISTANT:
- Handle simple/everyday requests yourself: weather, homelab status, email summary, quick facts, time
- For company tasks: call_agent to delegate, then report the result back clearly
- Be proactive: if you see something wrong (container down, failing service, unusual traffic), flag it
- You are NOT a dept head — you're Antony's PA who knows and manages the whole team

DELEGATION GUIDE:
- Homelab issue → call_agent("infra", ...)
- Code/deployment → call_agent("dev", ...)
- Written content → call_agent("content", ...)
- Marketing/growth → call_agent("marketing", ...)
- Complex multi-dept → call_agent("manager", ...) and let manager coordinate
- Quick research → use web_search yourself
- Analytics data → use get_analytics(site, days) yourself
- Ad stats → use get_ad_stats() yourself

RULES:
- Never tell Antony how to use you. Just answer or act.
- Never say certainly/of course/I can help with that.
- Short answers unless detail is genuinely needed.
- UK English throughout.
- You are she/her.
- You have voice — warm, clear neural TTS. Never claim to be text-only.
- Phone control: embed <<CALL:+441234567890>>, <<SMS:+441234567890:message here>>, <<OPEN:spotify://>>, or <<URL:https://...>> anywhere in reply. Strip these tags from spoken text naturally.
- Greeting (MANDATORY): if the very first words of the user message are "[FRESH SESSION · MORNING]", "[FRESH SESSION · AFTERNOON]" or "[FRESH SESSION · EVENING]" — your reply MUST begin with "Morning Antony,", "Afternoon Antony,", or "Evening Antony," (matching the tag). Ignore/strip the tag itself. On any later turn, do not use the name greeting unless you genuinely need his attention. Respond ONLY in plain, conversational English — never output raw JSON, code blocks, backtick syntax, markdown formatting, command-line output, or any computer language unless the user explicitly asks for code. Express all lists and data as natural sentences."""



# -- Rude mode toggle ----------------------------------------------------------

_RUDE_FLAG_PATH = "/opt/ahas/rude_mode.flag"


def _rude_enabled():
    return os.path.exists(_RUDE_FLAG_PATH)


def _rude_set(enabled: bool):
    if enabled:
        open(_RUDE_FLAG_PATH, "w").close()
    else:
        try:
            os.remove(_RUDE_FLAG_PATH)
        except FileNotFoundError:
            pass



# ── Tools ─────────────────────────────────────────────────────────────────────

TOOLS = [
    # ── Proxmox ──────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "get_host_status",
            "description": "Get live Proxmox host CPU, RAM, swap, and load average",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_container_list",
            "description": "List all LXC containers and whether they are running or stopped",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "container_action",
            "description": "Start or stop an LXC container by VMID",
            "parameters": {
                "type": "object",
                "properties": {
                    "vmid":   {"type": "string", "description": "Container ID e.g. 101"},
                    "action": {"type": "string", "enum": ["start", "stop"]}
                },
                "required": ["vmid", "action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_disk_usage",
            "description": "Get disk/storage usage across all Proxmox storage pools",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    # ── Home Assistant ────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "get_ha_states",
            "description": "Get current state of Home Assistant entities",
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "HA domain: light, switch, sensor, climate, media_player. Empty for all."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ha_service",
            "description": "Call a Home Assistant service to control a device",
            "parameters": {
                "type": "object",
                "properties": {
                    "domain":    {"type": "string"},
                    "service":   {"type": "string"},
                    "entity_id": {"type": "string"},
                    "data":      {"type": "object"}
                },
                "required": ["domain", "service", "entity_id"]
            }
        }
    },
    # ── Bambu A1 Mini 3D printer ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "bambu_status",
            "description": "Get current status of Antony's Bambu A1 Mini 3D printer — print state, task name, progress %, remaining time, nozzle/bed temps, AMS filament slots",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "bambu_control",
            "description": "Control the Bambu A1 Mini printer. Actions: pause, resume, stop, light_on, light_off, speed_silent, speed_standard, speed_sport, speed_ludicrous",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "One of: pause, resume, stop, light_on, light_off, speed_silent, speed_standard, speed_sport, speed_ludicrous"}
                },
                "required": ["action"]
            }
        }
    },
    # ── Info ──────────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather and forecast for Antony's location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "Override location if asked for specific place"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "internet_search",
            "description": "Search the internet for current information, news, or general knowledge",
            "parameters": {
                "type": "object",
                "properties": {
                    "query":       {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "description": "Number of results, default 5"}
                },
                "required": ["query"]
            }
        }
    },
    # ── Email ─────────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "read_email",
            "description": "Check Antony's Gmail inbox for recent emails",
            "parameters": {
                "type": "object",
                "properties": {
                    "count":  {"type": "integer", "description": "How many recent emails to fetch, default 5"},
                    "unread": {"type": "boolean", "description": "Only unread emails if true"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Send an email on behalf of Call-On Ltd / NEXUS. Use for follow-ups, notifications, outreach, or any email Antony asks you to send.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to":        {"type": "string",  "description": "Recipient email address"},
                    "subject":   {"type": "string",  "description": "Subject line"},
                    "body":      {"type": "string",  "description": "Email body — plain text or basic HTML"},
                    "from_name": {"type": "string",  "description": "Sender display name. Default: NEXUS"}
                },
                "required": ["to", "subject", "body"]
            }
        }
    },
    # ── Shell / SSH ───────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "execute_ssh",
            "description": "Execute any shell command on a homelab host via SSH. Use for logs, restarts, diagnostics, anything not covered by other tools. Proxmox=192.168.0.10 (user:claude, full sudo). CT IPs: CT101=.34 CT102=.6 CT104=.81 CT105=.3 CT107=.16 CT112=.28 CT116=.8 CT117=.60 CT118=.118 CT500=.13",
            "parameters": {
                "type": "object",
                "properties": {
                    "host":    {"type": "string", "description": "Target IP e.g. 192.168.0.10"},
                    "command": {"type": "string", "description": "Shell command to run"},
                    "user":    {"type": "string", "description": "SSH user. Default: claude for Proxmox, root for LXCs"}
                },
                "required": ["host", "command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "pct_exec",
            "description": "Run a command inside a specific LXC container via Proxmox. Use ctid like 101, 112, 500.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ctid":    {"type": "string", "description": "Container ID e.g. 101"},
                    "command": {"type": "string", "description": "Shell command inside container"}
                },
                "required": ["ctid", "command"]
            }
        }
    },
    # ── Agent orchestration ───────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "call_agent",
            "description": "Delegate a task to a specialist department agent and get their full response back. Use this to route work to the right expert — they will use their own tools and return results. Available agents: marketing, seo, dev, content, infra, business, community, security, general, manager, legal, customer_service.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dept": {
                        "type": "string",
                        "description": "Agent to call: marketing, seo, dev, content, infra, business, community, security, general, manager"
                    },
                    "task": {
                        "type": "string",
                        "description": "Clear task description — what to do, what output is expected, any relevant context"
                    }
                },
                "required": ["dept", "task"]
            }
        }
    },
    # ── n8n automation ────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "trigger_n8n",
            "description": "Trigger an n8n workflow by name (fuzzy match) or webhook path. Use to kick off automations: social posting, reports, data syncs, email campaigns.",
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow": {
                        "type": "string",
                        "description": "Workflow name (partial match OK, e.g. 'social poster') or webhook path (e.g. 'my-webhook')"
                    },
                    "data": {
                        "type": "object",
                        "description": "Optional JSON payload to pass to the workflow"
                    }
                },
                "required": ["workflow"]
            }
        }
    },
    # ── Analytics ─────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "get_analytics",
            "description": "Get GA4 traffic analytics for a Call-On site. Returns sessions, users, pageviews, top pages. Sites: call-on.dad, call-on.mom, call-on.media, call-on.shop.",
            "parameters": {
                "type": "object",
                "properties": {
                    "site": {"type": "string", "description": "Site: call-on.dad, call-on.mom, call-on.media, call-on.shop"},
                    "days": {"type": "integer", "description": "Days to look back, default 7"}
                },
                "required": ["site"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_ad_stats",
            "description": "Get Revive Adserver impression and click stats. Returns impressions, clicks, CTR per zone for the last N days.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days":    {"type": "integer", "description": "Days to look back, default 7"},
                    "zone_id": {"type": "integer", "description": "Specific zone (1=dad, 2=mom, 3=media, 4=shop). Omit for all zones."}
                },
                "required": []
            }
        }
    },
    # ── Community ─────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "get_community_stats",
            "description": "Get community database stats for call-on.dad and call-on.mom — user counts, posts, topics, videos, orders.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    # ── File access ───────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the CT117 filesystem. Allowed paths: /opt/ahas/, /var/www/, /etc/caddy/, /tmp/.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute file path"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file on CT117. Allowed paths: /opt/ahas/content/ and /tmp/nexus_*. Use for saving drafts, notes, generated content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":    {"type": "string", "description": "Absolute file path"},
                    "content": {"type": "string", "description": "Content to write"}
                },
                "required": ["path", "content"]
            }
        }
    },
    # ── Discord ───────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "send_discord_channel",
            "description": "Post a message to a specific Discord department channel for visibility / paper trail. Channels: general, dev, infra, marketing, seo, content, business, community, security, manager.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description": "Channel name"},
                    "message": {"type": "string", "description": "Message content, max 1800 chars, markdown OK"}
                },
                "required": ["channel", "message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "post_discord",
            "description": "Post a message to the homelab Discord briefing/webhook channel.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Message to post, max 1800 chars"}
                },
                "required": ["message"]
            }
        }
    },
]


# ── SSH / Shell ───────────────────────────────────────────────────────────────

NEXUS_SSH_KEY = '/root/.ssh/nexus_key'
PROXMOX_HOST  = '192.168.0.10'


def execute_ssh(host, command, user=None):
    if user is None:
        user = 'claude' if host == PROXMOX_HOST else 'root'
    try:
        r = subprocess.run(
            ['ssh', '-i', NEXUS_SSH_KEY,
             '-o', 'StrictHostKeyChecking=no',
             '-o', 'ConnectTimeout=10',
             '-o', 'BatchMode=yes',
             f'{user}@{host}', command],
            capture_output=True, text=True, timeout=45
        )
        out = r.stdout.strip() or r.stderr.strip() or f'exit {r.returncode}'
        return out[:2000]
    except subprocess.TimeoutExpired:
        return 'SSH timed out (45s)'
    except Exception as e:
        return f'SSH error: {e}'


def pct_exec_cmd(ctid, command):
    return execute_ssh(
        PROXMOX_HOST,
        "sudo pct exec " + str(ctid) + " -- bash -c " + json.dumps(command),
        user='claude'
    )


# ── Discord ───────────────────────────────────────────────────────────────────

def send_discord_channel(channel, message):
    channel_id = DISCORD_CHANNELS.get(channel.lower().strip())
    if not channel_id:
        return f"Unknown channel '{channel}'. Available: {', '.join(DISCORD_CHANNELS.keys())}"
    if not DISCORD_BOT_TOKEN:
        return "DISCORD_BOT_TOKEN not set in .env"
    try:
        payload = json.dumps({"content": str(message)[:1900]}).encode()
        req = urllib.request.Request(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            data=payload,
            headers={
                "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                "Content-Type": "application/json",
                "User-Agent": "DiscordBot (nexus, 1.0)"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return f"Posted to #{channel}." if r.status < 300 else f"Discord error {r.status}"
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")[:200]
        return f"Discord API error {e.code}: {body}"
    except Exception as e:
        return f"Discord failed: {e}"


def post_discord(message):
    webhook = os.environ.get('DISCORD_WEBHOOK', '')
    if not webhook:
        return 'No DISCORD_WEBHOOK in env.'
    try:
        payload = json.dumps({'content': str(message)[:1900], 'username': 'NEXUS'}).encode()
        req = urllib.request.Request(
            webhook, data=payload,
            headers={'Content-Type': 'application/json', 'User-Agent': 'DiscordBot (nexus, 1.0)'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            return 'Posted to Discord.' if r.status < 300 else f'Discord error {r.status}'
    except Exception as e:
        return f'Discord failed: {e}'


# ── HA ────────────────────────────────────────────────────────────────────────

BAMBU_HOST   = os.environ.get("BAMBU_HOST",   "192.168.0.2")
BAMBU_SERIAL = os.environ.get("BAMBU_SERIAL", "")
BAMBU_TOKEN  = os.environ.get("BAMBU_TOKEN",  "")
BAMBU_PORT   = 8883

def _bambu_mqtt(publish_payload, wait_for_domain="print", timeout=8):
    """Open a short-lived MQTT connection, publish a command, return first matching report."""
    try:
        import paho.mqtt.client as mqtt
        result = {}
        done   = threading.Event()

        def on_connect(client, userdata, flags, reason_code, properties=None):
            client.subscribe(f"device/{BAMBU_SERIAL}/report")
            client.publish(f"device/{BAMBU_SERIAL}/request", json.dumps(publish_payload))

        def on_message(client, userdata, msg):
            try:
                data = json.loads(msg.payload)
                if wait_for_domain in data:
                    result.update(data[wait_for_domain])
                    done.set()
                elif not wait_for_domain:
                    result.update(data)
                    done.set()
            except Exception:
                pass

        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        c.username_pw_set("bblp", BAMBU_TOKEN)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        c.tls_set_context(ctx)
        c.on_connect = on_connect
        c.on_message = on_message
        c.connect(BAMBU_HOST, BAMBU_PORT, keepalive=10)
        c.loop_start()
        done.wait(timeout=timeout)
        c.loop_stop()
        c.disconnect()
        return result if result else {"error": "No response from printer (timeout)"}
    except Exception as e:
        return {"error": str(e)}


def bambu_get_status():
    data = _bambu_mqtt({"pushing": {"sequence_id": "0", "command": "pushall"}}, wait_for_domain="print")
    if "error" in data:
        return f"Bambu printer unreachable: {data['error']}"
    gcode_state = data.get("gcode_state", "unknown")
    task_name   = data.get("subtask_name", "none")
    pct_done    = data.get("mc_percent", 0)
    remaining   = data.get("mc_remaining_time", 0)
    nozzle_tmp  = data.get("nozzle_temper", 0)
    bed_tmp     = data.get("bed_temper", 0)
    ams_data    = data.get("ams", {})
    ams_summary = ""
    for tray in ams_data.get("ams", []):
        for slot in tray.get("tray", []):
            if slot.get("tray_type"):
                ams_summary += f" [{slot.get('tray_type')} {slot.get('tray_color','')}]"
    return (
        f"State: {gcode_state} | Task: {task_name} | Progress: {pct_done}% | "
        f"Remaining: {remaining}min | Nozzle: {nozzle_tmp}°C | Bed: {bed_tmp}°C"
        + (f" | AMS:{ams_summary}" if ams_summary else "")
    )


def bambu_control(action):
    action = action.lower().strip()
    cmds = {
        "pause":  {"print":  {"sequence_id": "0", "command": "pause"}},
        "resume": {"print":  {"sequence_id": "0", "command": "resume"}},
        "stop":   {"print":  {"sequence_id": "0", "command": "stop"}},
        "light_on":  {"system": {"sequence_id": "0", "command": "ledctrl", "led_node": "work_light", "led_mode": "on"}},
        "light_off": {"system": {"sequence_id": "0", "command": "ledctrl", "led_node": "work_light", "led_mode": "off"}},
        "speed_silent":   {"print": {"sequence_id": "0", "command": "print_speed", "param": "1"}},
        "speed_standard": {"print": {"sequence_id": "0", "command": "print_speed", "param": "2"}},
        "speed_sport":    {"print": {"sequence_id": "0", "command": "print_speed", "param": "3"}},
        "speed_ludicrous":{"print": {"sequence_id": "0", "command": "print_speed", "param": "4"}},
    }
    if action not in cmds:
        return f"Unknown action '{action}'. Valid: {', '.join(cmds.keys())}"
    domain = "system" if "light" in action else "print"
    result = _bambu_mqtt(cmds[action], wait_for_domain="", timeout=5)
    if "error" in result:
        return f"Bambu command failed: {result['error']}"
    return f"Bambu printer: {action} sent."


def ha_headers():
    return {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}


def ha_get_states(domain=""):
    try:
        req = urllib.request.Request(f"{HA_URL}/api/states", headers=ha_headers())
        with urllib.request.urlopen(req, timeout=5) as r:
            states = json.loads(r.read())
        if domain:
            states = [s for s in states if s["entity_id"].startswith(domain + ".")]
        summary = []
        for s in states[:40]:
            name = s.get("attributes", {}).get("friendly_name", s["entity_id"])
            summary.append(f"{name}: {s['state']}")
        return "\n".join(summary) if summary else "No entities found."
    except Exception as e:
        return f"HA unreachable: {e}"


def ha_call_service(domain, service, entity_id, data=None):
    try:
        payload = {"entity_id": entity_id}
        if data:
            payload.update(data)
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{HA_URL}/api/services/{domain}/{service}",
            data=body, headers=ha_headers(), method="POST"
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            r.read()
        return f"Done — {domain}.{service} on {entity_id}."
    except Exception as e:
        return f"HA service call failed: {e}"


# ── Weather ───────────────────────────────────────────────────────────────────

def get_weather(location=None):
    loc = (location or LOCATION).replace(" ", "+")
    try:
        req = urllib.request.Request(
            f"https://wttr.in/{loc}?format=j1",
            headers={"User-Agent": "NEXUS/5.0"}
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        cur      = data["current_condition"][0]
        desc     = cur["weatherDesc"][0]["value"]
        temp_c   = cur["temp_C"]
        feels    = cur["FeelsLikeC"]
        humidity = cur["humidity"]
        wind     = cur["windspeedKmph"]
        tmr      = data["weather"][1] if len(data["weather"]) > 1 else None
        result   = f"{desc}, {temp_c}°C (feels {feels}°C), humidity {humidity}%, wind {wind}km/h."
        if tmr:
            tmr_desc = tmr["hourly"][4]["weatherDesc"][0]["value"]
            tmr_max  = tmr["maxtempC"]
            tmr_min  = tmr["mintempC"]
            result  += f" Tomorrow: {tmr_desc}, {tmr_min}–{tmr_max}°C."
        return result
    except Exception as e:
        return f"Weather unavailable: {e}"


# ── Web search ────────────────────────────────────────────────────────────────

def web_search(query, max_results=5):
    try:
        from ddgs import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append(f"{r['title']}: {r['body'][:200]}")
        return "\n\n".join(results) if results else "No results found."
    except Exception as e:
        return f"Search failed: {e}"


# ── Email (read) ──────────────────────────────────────────────────────────────

def _load_email_accounts():
    accounts = []
    if os.environ.get("GMAIL_APP_PASSWORD"):
        accounts.append({"name": "Gmail", "host": "imap.gmail.com",
                         "user": os.environ.get("GMAIL_USER", "mediaserver2407@gmail.com"),
                         "password": os.environ.get("GMAIL_APP_PASSWORD"), "ssl": True})
    if os.environ.get("GMAIL2_APP_PASSWORD"):
        accounts.append({"name": "Gmail 2", "host": "imap.gmail.com",
                         "user": os.environ.get("GMAIL2_USER", ""),
                         "password": os.environ.get("GMAIL2_APP_PASSWORD"), "ssl": True})
    if os.environ.get("YAHOO_APP_PASSWORD"):
        accounts.append({"name": "Yahoo", "host": "imap.mail.yahoo.com",
                         "user": os.environ.get("YAHOO_USER", ""),
                         "password": os.environ.get("YAHOO_APP_PASSWORD"), "ssl": True})
    if os.environ.get("OUTLOOK_PASSWORD"):
        accounts.append({"name": "Outlook", "host": "imap-mail.outlook.com",
                         "user": os.environ.get("OUTLOOK_USER", ""),
                         "password": os.environ.get("OUTLOOK_PASSWORD"), "ssl": True})
    return accounts


def _fetch_imap(account, count, unread_only):
    ssl  = account.get("ssl", True)
    conn = imaplib.IMAP4_SSL(account["host"]) if ssl else imaplib.IMAP4(account["host"])
    conn.login(account["user"], account["password"])
    conn.select("inbox")
    criteria = "UNSEEN" if unread_only else "ALL"
    _, msgs   = conn.search(None, criteria)
    ids       = msgs[0].split()[-count:]
    results   = []
    for mid in reversed(ids):
        _, data = conn.fetch(mid, "(RFC822)")
        msg     = emaillib.message_from_bytes(data[0][1])
        subject_raw, enc = decode_header(msg["Subject"])[0]
        subject = subject_raw.decode(enc or "utf-8") if isinstance(subject_raw, bytes) else subject_raw
        results.append(f"[{account['name']}] {msg['From']} | {msg['Date']}\n  {subject}")
    conn.logout()
    return results


def read_email(count=5, unread_only=False):
    accounts = _load_email_accounts()
    if not accounts:
        return "No email accounts configured."
    all_results, errors = [], []
    for acc in accounts:
        try:
            all_results.extend(_fetch_imap(acc, count, unread_only))
        except Exception as e:
            errors.append(f"{acc['name']}: {e}")
    if not all_results and errors:
        return "All accounts failed:\n" + "\n".join(errors)
    summary = "\n\n".join(all_results[:count * len(accounts)])
    if errors:
        summary += "\n\n(Failed: " + ", ".join(errors) + ")"
    return summary if summary else "No emails found."


# ── Email (send) ──────────────────────────────────────────────────────────────

def send_email(to, subject, body, from_name="NEXUS"):
    if not SMTP_USER or not SMTP_PASS:
        return "SMTP not configured — add SMTP_USER and SMTP_PASS to /opt/ahas/.env"
    try:
        msg = MIMEMultipart("alternative")
        msg["From"]    = f"{from_name} <{SMTP_USER}>"
        msg["To"]      = to
        msg["Subject"] = subject
        content_type = "html" if "<" in body and ">" in body else "plain"
        msg.attach(MIMEText(body, content_type, "utf-8"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, [to], msg.as_string())
        return f"Email sent to {to} — subject: {subject}"
    except Exception as e:
        return f"Email send failed: {e}"


# ── Agent orchestration ───────────────────────────────────────────────────────

def call_agent(dept, task):
    """Delegate a task to a specialist agent via internal HTTP and return their reply."""
    if dept not in AGENT_PROMPTS:
        return f"Unknown dept '{dept}'. Valid: {list(AGENT_PROMPTS.keys())}"
    try:
        payload = json.dumps({"message": task, "_internal": True}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:5000/api/agent/{dept}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.loads(r.read())
        reply = data.get("reply", "Agent returned no reply.")
        return f"[{dept.upper()} AGENT]: {reply}"
    except Exception as e:
        return f"call_agent({dept}) failed: {e}"


# ── n8n trigger ───────────────────────────────────────────────────────────────

def trigger_n8n(workflow, data=None):
    """Trigger an n8n workflow by name (via API) or webhook path."""
    try:
        # If N8N_API_KEY is set, find workflow by name and execute it
        if N8N_API_KEY and '/' not in workflow:
            req = urllib.request.Request(
                f"{N8N_URL}/api/v1/workflows?limit=100",
                headers={"X-N8N-API-KEY": N8N_API_KEY, "User-Agent": "NEXUS/5.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                workflows = json.loads(r.read())
            wf_match = None
            for wf in workflows.get("data", []):
                if workflow.lower() in wf.get("name", "").lower():
                    wf_match = wf
                    break
            if not wf_match:
                names = [w.get("name") for w in workflows.get("data", [])]
                return f"No workflow matching '{workflow}'. Active: {names[:8]}"
            # n8n v1 execute endpoint
            payload = json.dumps({"runData": data or {}}).encode()
            req2 = urllib.request.Request(
                f"{N8N_URL}/api/v1/workflows/{wf_match['id']}/run",
                data=payload,
                headers={"X-N8N-API-KEY": N8N_API_KEY, "Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req2, timeout=30) as r:
                result = json.loads(r.read())
            return f"Triggered '{wf_match['name']}'. Run: {result.get('data', {}).get('executionId', '?')}"

        # Webhook fallback: POST to /webhook/<path>
        wh_path = workflow.lstrip('/')
        url = f"{N8N_URL}/webhook/{wh_path}"
        payload = json.dumps(data or {}).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "NEXUS/5.0"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = r.read().decode(errors="ignore")[:300]
        return f"Webhook '{workflow}' triggered: {resp}"
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")[:200]
        return f"n8n error {e.code}: {body}"
    except Exception as e:
        return f"n8n trigger failed: {e}"


# ── Analytics (GA4 agent tool) ────────────────────────────────────────────────

_ga4_client     = None
_ga4_client_err = None


def _ga4_get_client():
    global _ga4_client, _ga4_client_err
    if _ga4_client is not None or _ga4_client_err is not None:
        return _ga4_client, _ga4_client_err
    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        _ga4_client = BetaAnalyticsDataClient()
    except Exception as e:
        _ga4_client_err = f"GA4 client init failed: {type(e).__name__}: {e}"
    return _ga4_client, _ga4_client_err


def get_analytics(site, days=7):
    """Get GA4 traffic summary for a site — usable as an agent tool."""
    prop_id = GA4_PROPERTIES.get(site)
    if not prop_id:
        return f"No GA4 property ID for '{site}'. Available: {[k for k,v in GA4_PROPERTIES.items() if v]}"
    ga4, err = _ga4_get_client()
    if err:
        return f"GA4 unavailable: {err}"
    try:
        from google.analytics.data_v1beta.types import (
            RunReportRequest, DateRange, Dimension, Metric, OrderBy
        )
        start = f"{days}daysAgo"
        # Top pages by sessions
        rep = ga4.run_report(RunReportRequest(
            property=f"properties/{prop_id}",
            date_ranges=[DateRange(start_date=start, end_date="today")],
            dimensions=[Dimension(name="pagePath")],
            metrics=[
                Metric(name="sessions"),
                Metric(name="activeUsers"),
                Metric(name="screenPageViews"),
            ],
            order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="sessions"), desc=True)],
            limit=10,
        ))
        total_s, total_u, total_v = 0, 0, 0
        rows = []
        for row in rep.rows:
            path = row.dimension_values[0].value
            s    = int(row.metric_values[0].value)
            u    = int(row.metric_values[1].value)
            v    = int(row.metric_values[2].value)
            total_s += s; total_u += u; total_v += v
            rows.append(f"  {path}: {s} sessions, {v} views")
        out  = f"{site} — last {days} days\n"
        out += f"Totals: {total_s} sessions, {total_u} users, {total_v} pageviews\n"
        out += "Top pages:\n" + "\n".join(rows[:10])
        return out
    except Exception as e:
        msg = str(e)
        if "PERMISSION_DENIED" in msg:
            return f"GA4 permission denied for {site} — add service account to the property"
        return f"GA4 query failed: {type(e).__name__}: {msg[:200]}"


# ── Revive ad stats ───────────────────────────────────────────────────────────

def get_ad_stats(days=7, zone_id=None):
    """Query Revive stats via MySQL on CT102."""
    zone_filter = f"AND i.zone_id = {int(zone_id)}" if zone_id else ""
    cmd = (
        "mysql -u root revive_adserver -e \""
        "SELECT z.zonename, "
        "COALESCE(SUM(i.impressions),0) AS imps, "
        "COALESCE(SUM(i.clicks),0) AS clicks, "
        "ROUND(COALESCE(SUM(i.clicks),0)/NULLIF(COALESCE(SUM(i.impressions),0),0)*100,3) AS ctr "
        "FROM zones z "
        "LEFT JOIN data_intermediate_ad i ON i.zone_id = z.zoneid "
        f"AND i.date_time >= DATE_SUB(NOW(), INTERVAL {int(days)} DAY) "
        f"{zone_filter} "
        "GROUP BY z.zoneid, z.zonename ORDER BY imps DESC;"
        "\" 2>/dev/null"
    )
    result = pct_exec_cmd("102", cmd)
    if not result or "error" in result.lower() or "denied" in result.lower():
        return f"Ad stats unavailable: {result or 'no output'}"
    lines = result.strip().splitlines()
    if len(lines) <= 1:
        return f"No ad data found for last {days} days."
    out = [f"Revive ad stats — last {days} days:"]
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) >= 4:
            zone, imps, clicks, ctr = parts[0], parts[1], parts[2], parts[3]
            out.append(f"  {zone}: {imps} impressions, {clicks} clicks, {ctr}% CTR")
    return "\n".join(out)


# ── Community stats ───────────────────────────────────────────────────────────

def get_community_stats():
    """Query community DB stats from CT102 MariaDB."""
    cmd = (
        "mysql -u root -e \""
        "SELECT table_schema, table_name, table_rows "
        "FROM information_schema.tables "
        "WHERE table_schema IN ('Callon-dad','Callon-mom') "
        "AND table_rows > 5 "
        "ORDER BY table_schema, table_rows DESC LIMIT 30;"
        "\" 2>/dev/null"
    )
    raw = pct_exec_cmd("102", cmd)
    if not raw or "error" in raw.lower():
        return f"Community stats unavailable: {raw}"
    lines = raw.strip().splitlines()
    out, current_site = [], None
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        site = parts[0].replace("Callon-", "call-on.")
        if site != current_site:
            current_site = site
            out.append(f"\n{site}:")
        out.append(f"  {parts[1]}: ~{parts[2]} rows")
    return "\n".join(out).strip() if out else "No community data."


# ── File access ───────────────────────────────────────────────────────────────

_READ_ALLOWED = ("/opt/ahas/", "/var/www/", "/etc/caddy/", "/tmp/")
_WRITE_ALLOWED = ("/opt/ahas/content/", "/tmp/nexus_")


def read_file(path):
    if not any(path.startswith(p) for p in _READ_ALLOWED):
        return f"Access denied. Allowed prefixes: {_READ_ALLOWED}"
    try:
        with open(path, "r", errors="replace") as f:
            content = f.read(8000)
        return content if content else "(empty file)"
    except FileNotFoundError:
        return f"File not found: {path}"
    except Exception as e:
        return f"Read error: {e}"


def write_file(path, content):
    if not any(path.startswith(p) for p in _WRITE_ALLOWED):
        return f"Write denied. Allowed prefixes: {_WRITE_ALLOWED}"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Written {len(content)} chars to {path}"
    except Exception as e:
        return f"Write error: {e}"


# ── TTS ───────────────────────────────────────────────────────────────────────

ELEVENLABS_KEY   = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE = os.environ.get("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")


def _elevenlabs_audio(text):
    url     = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE}"
    payload = json.dumps({
        "text": text,
        "model_id": "eleven_turbo_v2_5",
        "voice_settings": {"stability": 0.45, "similarity_boost": 0.82, "style": 0.35, "use_speaker_boost": True}
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={
        "xi-api-key": ELEVENLABS_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg"
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


async def _tts_bytes_edge(text):
    import edge_tts
    communicate = edge_tts.Communicate(text, TTS_VOICE)
    audio = b""
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio += chunk["data"]
    return audio


def generate_audio(text):
    clean = text[:800]
    if ELEVENLABS_KEY:
        try:
            return base64.b64encode(_elevenlabs_audio(clean)).decode()
        except Exception as e:
            print(f"ElevenLabs failed ({e}), falling back to edge-tts")
    try:
        audio = asyncio.run(_tts_bytes_edge(clean))
        return base64.b64encode(audio).decode()
    except Exception as e:
        print(f"TTS error: {e}")
        return None


# ── Tool dispatcher ───────────────────────────────────────────────────────────

def run_tool(name, inputs):
    # Proxmox
    if name == "get_host_status":      return px.get_host_status()
    if name == "get_container_list":   return px.get_container_list()
    if name == "container_action":     return px.container_action(inputs["vmid"], inputs["action"])
    if name == "get_disk_usage":       return px.get_disk_usage()
    # HA
    if name == "get_ha_states":        return ha_get_states(inputs.get("domain", ""))
    if name == "ha_service":           return ha_call_service(inputs["domain"], inputs["service"], inputs["entity_id"], inputs.get("data", {}))
    # Bambu
    if name == "bambu_status":         return bambu_get_status()
    if name == "bambu_control":        return bambu_control(inputs["action"])
    # Info
    if name == "get_weather":          return get_weather(inputs.get("location", LOCATION))
    if name == "internet_search":      return web_search(inputs["query"], inputs.get("max_results", 5))
    # Email
    if name == "read_email":           return read_email(inputs.get("count", 5), inputs.get("unread", False))
    if name == "send_email":           return send_email(inputs["to"], inputs["subject"], inputs["body"], inputs.get("from_name", "NEXUS"))
    # Shell
    if name == "execute_ssh":          return execute_ssh(inputs["host"], inputs["command"], inputs.get("user"))
    if name == "pct_exec":             return pct_exec_cmd(str(inputs["ctid"]), inputs["command"])
    # Orchestration
    if name == "call_agent":           return call_agent(inputs["dept"], inputs["task"])
    if name == "trigger_n8n":          return trigger_n8n(inputs["workflow"], inputs.get("data"))
    # Analytics
    if name == "get_analytics":        return get_analytics(inputs["site"], inputs.get("days", 7))
    if name == "get_ad_stats":         return get_ad_stats(inputs.get("days", 7), inputs.get("zone_id"))
    if name == "get_community_stats":  return get_community_stats()
    # Files
    if name == "read_file":            return read_file(inputs["path"])
    if name == "write_file":           return write_file(inputs["path"], inputs["content"])
    # Discord
    if name == "send_discord_channel": return send_discord_channel(inputs["channel"], inputs["message"])
    if name == "post_discord":         return post_discord(inputs["message"])
    return f"Unknown tool: {name}"


# ── Flask routes ──────────────────────────────────────────────────────────────

@app.after_request
def cors(r):
    r.headers["Access-Control-Allow-Origin"]  = "*"
    r.headers["Access-Control-Allow-Headers"] = "Content-Type"
    r.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return r


@app.route("/")
def index():
    return send_from_directory(app.static_folder, 'index.html')


@app.route("/health")
def health():
    try:
        mem_count = nexus_memory.message_count()
    except Exception:
        mem_count = -1
    try:
        cache_keys = list(nexus_cache._cache.keys())
    except Exception:
        cache_keys = []
    try:
        agent_counts = nexus_memory.all_agent_counts()
    except Exception:
        agent_counts = {}
    return jsonify({
        "status": "ok", "service": "NEXUS API", "version": "5.0",
        "memory": mem_count,
        "agent_memory": agent_counts,
        "cache_keys": cache_keys,
        "tools": len(TOOLS),
        "agents": list(AGENT_PROMPTS.keys()),
    })


@app.route("/api/ask", methods=["POST", "OPTIONS"])
@limiter.limit("10 per minute")
def ask():
    if request.method == "OPTIONS":
        return "", 204

    data       = request.json or {}
    user_input = data.get("message", "").strip()
    tts        = data.get("tts", True)
    image_b64  = data.get("image")  # base64 string, None if not sending image
    image_mime = data.get("mime", "image/jpeg")

    if not user_input:
        return jsonify({"error": "no message"}), 400

    history = nexus_memory.load_history()
    is_fresh_session = len(history) == 0
    if is_fresh_session:
        try:
            import zoneinfo, datetime as _dt
            hr = _dt.datetime.now(zoneinfo.ZoneInfo("Europe/London")).hour
        except Exception:
            hr = 12
        salutation = "Morning Antony" if hr < 12 else "Afternoon Antony" if hr < 18 else "Evening Antony"
        session_system = (
            SYSTEM_PROMPT
            + f"\n\n# THIS TURN ONLY\nThis is the first message of a new session. "
            + f"Your reply MUST begin with exactly: \"{salutation}, \" "
            + f"(those exact words, that comma, one space) and then your answer. "
            + f"Do not paraphrase the greeting. Do not skip it."
        )
    else:
        session_system = SYSTEM_PROMPT

    history.append({"role": "user", "content": user_input})
    nexus_memory.save_message("user", user_input)

    if len(history) > 40:
        history = history[-40:]

    messages = list(history)
    # Vision: override the last user message with image+text content block
    if image_b64:
        messages[-1] = {"role": "user", "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:{image_mime};base64,{image_b64}"}},
            {"type": "text", "text": user_input}
        ]}

    for _ in range(10):
        try:
            resp = client.chat.completions.create(
                model="nexus-main",
                max_tokens=1024,
                tools=TOOLS,
                messages=[{"role": "system", "content": session_system}] + messages
            )
        except Exception as e:
            err = str(e)
            return jsonify({"reply": f"NEXUS is temporarily unavailable — {err[:300]}", "audio": None}), 503

        choice = resp.choices[0]
        if choice.finish_reason == "tool_calls":
            assistant_msg = choice.message
            messages.append(assistant_msg)
            for tc in (assistant_msg.tool_calls or []):
                inputs = json.loads(tc.function.arguments) if tc.function.arguments else {}
                result = run_tool(tc.function.name, inputs)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": str(result)
                })
            continue

        reply = choice.message.content or ""
        nexus_memory.save_message("assistant", reply)
        audio_b64 = generate_audio(reply) if tts else None
        return jsonify({"reply": reply, "audio": audio_b64})

    return jsonify({"reply": "Tool loop limit reached.", "audio": None}), 500


@app.route("/api/clear", methods=["POST"])
def clear():
    nexus_memory.clear_history()
    return jsonify({"status": "cleared"})


@app.route("/api/status")
def status():
    cached = nexus_cache.cache_get("service_status")
    if cached:
        return jsonify({**cached["data"], "_cache_age": round(nexus_cache.cache_age("service_status"))})
    UP_CODES = {200, 301, 302, 401, 403}
    checks   = [("ha","http://192.168.0.9:8123"),("n8n","http://192.168.0.28:5678"),
                ("pihole","http://192.168.0.3"),("plex","http://192.168.0.34:32400")]
    services = {}
    for name, url in checks:
        try:
            urllib.request.urlopen(url, timeout=2)
            services[name] = "up"
        except urllib.error.HTTPError as e:
            services[name] = "up" if e.code in UP_CODES else "down"
        except Exception:
            services[name] = "down"
    return jsonify(services)


# ── Dashboard API endpoints (cache-backed) ────────────────────────────────────

@app.route("/api/proxmox/host")
def proxmox_host():
    entry = nexus_cache.cache_get("proxmox_host")
    if entry:
        return jsonify({"data": entry["data"], "age": round(nexus_cache.cache_age("proxmox_host"))})
    return jsonify({"data": px.get_host_status(), "age": 0})


@app.route("/api/proxmox/containers")
def proxmox_containers():
    entry = nexus_cache.cache_get("proxmox_containers")
    if entry:
        return jsonify({"data": entry["data"], "age": round(nexus_cache.cache_age("proxmox_containers"))})
    return jsonify({"data": px.get_container_list(), "age": 0})


@app.route("/api/proxmox/storage")
def proxmox_storage():
    entry = nexus_cache.cache_get("proxmox_storage")
    if entry:
        return jsonify({"data": entry["data"], "age": round(nexus_cache.cache_age("proxmox_storage"))})
    return jsonify({"data": px.get_disk_usage(), "age": 0})


@app.route("/api/ha/summary")
def ha_summary():
    entry = nexus_cache.cache_get("ha_summary")
    if entry:
        return jsonify({"data": entry["data"], "age": round(nexus_cache.cache_age("ha_summary"))})
    return jsonify({"data": ha_get_states(), "age": 0})


@app.route("/api/agents/status")
def agents_status():
    channels = {k: bool(v) for k, v in DISCORD_CHANNELS.items()}
    agent_mem = {}
    try:
        agent_mem = nexus_memory.all_agent_counts()
    except Exception:
        pass
    return jsonify({"channels": channels, "bot_configured": bool(DISCORD_BOT_TOKEN), "memory": agent_mem})


@app.route("/api/cache/age")
def cache_age_endpoint():
    return jsonify(nexus_cache.all_ages())


# ── Department Agent system prompts ───────────────────────────────────────────

# Injected into every agent prompt
_AGENT_BASE_RULES = """
TEAM STRUCTURE:
- NEXUS: Antony's personal assistant — the command layer above all agents. She delegates to us.
- Manager: coordinates multi-dept work, approves routine tasks, routes complex requests
- You are one specialist in a team. Know your lane. Collaborate via call_agent or send_discord_channel.

ACT, DON'T DESCRIBE:
- You have tools. USE them. Do not write commands for Antony to run himself.
- Get information → use tools → report findings. Not instructions.
- After every action → verify the result.

REPORTING:
- Post your completed result to the relevant Discord channel for visibility.
- If NEXUS or Manager delegated this: your reply IS the report — be clear and complete.

DISCORD FORMAT:
- No code blocks (no backticks). No markdown headers (# ##).
- Bold **key points** only. Bullet points fine.
- Under 400 chars unless detail is genuinely needed.
- State what you DID and the result. Not what Antony should do.

SELF-CORRECTION:
1. Tool fails → retry once with different params
2. Still fails → try alternative approach
3. Two failures → post to #manager: what you found, what you tried, what's needed

call_agent USAGE:
- Use call_agent(dept, task) when you need another specialist's expertise
- Do NOT call the agent that called you (no loops)
- One cross-agent call per task — don't chain
"""

AGENT_PROMPTS = {

"marketing": """You are Jamie, Call-On Ltd's Marketing Agent — autonomous, action-oriented, results-focused.

COMPANY: Call-On Ltd — UK parenting communities.
- call-on.dad: UK dads community (main revenue site)
- call-on.mom: UK moms community
- call-on.media: landing/company page
- call-on.shop: Printful store (CDO Range)
Audience: UK parents 25–45, predominantly mobile.

YOUR ROLE:
- Own all marketing strategy and execution across all four properties
- Plan campaigns, social media strategy, email marketing, paid/organic growth
- Make data-driven decisions — check analytics before recommending

TOOLS YOU USE:
- web_search: competitor research, trend spotting, platform news
- get_analytics: pull real traffic data before making recommendations
- get_ad_stats: check ad performance (Revive — 4 zones)
- call_agent("seo", task): brief SEO on keyword/ranking work
- call_agent("content", task): brief content team with full spec
- send_discord_channel: post results to #marketing, brief #manager
- read_email: monitor marketing-related emails

RULES:
- Never claim something is done without confirming it
- Pull real analytics data before giving growth recommendations
- UK English. Never write "leverage", "synergise", "delve"
- Always state: what you did, what the data shows, what you recommend next""" + _AGENT_BASE_RULES,


"seo": """You are Archer, Call-On Ltd's SEO Agent — technical, data-driven, execution-focused.

COMPANY: Call-On Ltd — primary SEO targets: call-on.dad and call-on.mom (UK parenting).

YOUR ROLE:
- Own keyword strategy, on-page optimisation, technical health, link building
- Deliver keyword briefs to content team for every new article
- Monitor rankings, flag drops and wins proactively
- Identify quick-win opportunities and act on them

TOOLS YOU USE:
- web_search: SERP research, keyword volumes, competitor analysis, backlink intel
- get_analytics: real traffic data from GA4 — sessions, top pages, user trends
- execute_ssh / pct_exec: check technical health, page speed, server config
- call_agent("content", task): brief content with exact keyword specs
- call_agent("dev", task): request technical SEO changes (canonical, meta, schema)
- send_discord_channel: report to #seo, brief #marketing

RULES:
- Every content recommendation must include: target keyword, search intent, suggested title, word count
- Flag any technical issue that could hurt rankings (broken links, slow pages, missing meta)
- UK spellings in all content briefs
- Check actual analytics before making traffic claims Respond ONLY in plain, conversational English — never output raw JSON, code blocks, backtick syntax, markdown formatting, command-line output, or any computer language unless the user explicitly asks for code. Express all lists and data as natural sentences.""" + _AGENT_BASE_RULES,


"dev": """You are Kai, Call-On Ltd's Dev Agent — precise, methodical, always verifies results.

INFRASTRUCTURE:
- Proxmox: 192.168.0.10 (user: claude, full NOPASSWD sudo)
- CT102 (.6): MariaDB — Callon-dad, Callon-mom, callon_shop. NEVER direct writes to n8n DB.
- CT112 (.28): n8n + discord-agent (Docker at /opt/n8n)
- CT117 (.60): NEXUS API — this system. Service: ahas-api.service. Code: /opt/ahas/
- CT500 (.13): Caddy + PHP 8.4-FPM. Webroot: /var/www/html/. Caddyfile: /etc/caddy/Caddyfile
- CT118 (.118): Revive Adserver at /var/www/revive/
- GitHub: github.com/Call-OnDad

YOUR ROLE:
- Implement all technical changes: code, config, deployments, container management
- Maintain and fix all services across the homelab
- Action technical requests from other agents or NEXUS

TOOLS YOU USE:
- execute_ssh: run commands on any host
- pct_exec: run commands inside containers
- get_host_status / get_container_list / container_action: Proxmox management
- read_file: read config/code files on CT117
- web_search: documentation, debugging

RULES:
- Verify after every change (curl endpoint, check service, read logs)
- Never commit secrets or credentials
- No direct DB writes to n8n — use n8n UI or API
- Stage specific files only (no git add -A)
- One change at a time, verify before proceeding
- Before ANY destructive action → confirm backup exists Respond ONLY in plain, conversational English — never output raw JSON, code blocks, backtick syntax, markdown formatting, command-line output, or any computer language unless the user explicitly asks for code. Express all lists and data as natural sentences.""" + _AGENT_BASE_RULES,


"content": """You are Nora, Call-On Ltd's Content Agent — human, warm, audience-first.

HARD RULES — read every time before replying:
1. ALWAYS reply to every brief. Never go silent.
2. You CANNOT create files or links. Deliver content INLINE in your reply.
3. Deliver in ONE reply. Don't split. Don't promise to "send shortly".
4. Ship a first draft on every brief, even if research is incomplete. State assumptions. Ship.
5. Only ask a clarifying question if you literally cannot start. Even then, include a draft.

BRANDS:
- call-on.dad: UK dads. Voice: real, straight-talking, zero corporate. Like a dad who's been there.
- call-on.mom: UK moms. Voice: supportive, practical, community-first. Like your most grounded friend.
- call-on.media: Modern, clear, confident.
- call-on.shop: Friendly, helpful, parent-to-parent.

YOUR ROLE:
- Write all content: blog posts, social captions, email newsletters, product descriptions, SEO articles
- Respond to briefs from SEO, marketing, manager, or NEXUS within the brief's exact spec
- Maintain brand voice consistency across all four properties
- Proactively suggest content ideas based on community trends

TOOLS YOU USE:
- web_search: research topics, fact-check, find UK-specific angles
- write_file: save long drafts to /opt/ahas/content/<filename>.md for persistent storage
- call_agent("seo", task): request keyword brief if not provided
- send_discord_channel: post drafts to #content for review

DELIVERABLE FORMATS (all inline):
- Markdown table for tabular content
- Fenced ```csv block for spreadsheet-ready data
- Headed sections for long-form articles
- Bulleted/numbered lists

RULES:
- UK English always (colour, organisation, favourite, whilst)
- Never write: "delve", "tapestry", "navigate", "leverage" as buzzwords
- Write like a person, not a press release
- State: audience, goal, word count, keyword at top of every deliverable Respond ONLY in plain, conversational English — never output raw JSON, code blocks, backtick syntax, markdown formatting, command-line output, or any computer language unless the user explicitly asks for code. Express all lists and data as natural sentences.""" + _AGENT_BASE_RULES,


"infra": """You are Atlas, Call-On Ltd's Infrastructure Agent — methodical, cautious, always checks before acting.

INFRASTRUCTURE MAP:
- Proxmox: 192.168.0.10 — HP DL380p Gen8, Xeon E5-2620, Proxmox 8.4
- CT101 (.34): Media Stack (Plex, Sonarr, Radarr, Docker)
- CT102 (.6): MariaDB — all app DBs
- CT104 (.81): Node.js WebRTC Signalling (PM2, port 3000)
- CT105 (.3): Pi-hole DNS (Tailscale: 100.78.52.118)
- CT107 (.16): Proxmox Backup Server
- CT109 (.37): CrowdSec LAPI (0.0.0.0:8080)
- CT112 (.28): n8n + discord-agent (Docker)
- CT116 (.8): ComfyUI (CPU mode)
- CT117 (.60): NEXUS API — this system
- CT118 (.118): Revive Adserver (Apache + PHP)
- CT500 (.13): Caddy reverse proxy + PHP 8.4-FPM
- VPN: Headscale (100.64.x.x range — NOT Tailscale 100.71.x.x, that's gone)
- Storage: local 98GB, local-lvm 794GB, pbs 1099GB
- KNOWN OFFLINE (intentional): CT114 (WordPress, decommissioned)

BACKUP WINDOWS (DO NOT treat as incidents):
- Sunday 02:00–06:00 UK: PBS full backup, CTs briefly stop — normal
- Daily 02:00–04:00 UK: PBS snapshot on CT102/CT500 — normal

YOUR ROLE:
- Respond to service alerts and diagnose root cause
- Restart failed services, fix container issues
- Check logs, provide clear diagnosis
- Coordinate with #security on security-related infra issues
- Brief #dev when code or config changes are needed

TOOLS YOU USE:
- execute_ssh: SSH into any host for diagnostics and fixes
- pct_exec: run commands inside containers
- get_host_status / get_container_list / container_action / get_disk_usage
- web_search: look up error messages, service documentation

ESCALATE IMMEDIATELY (no retries):
- Data corruption or loss risk
- Production DB issues on CT102
- Suspected security breach
- Disk >95%
- Anything touching n8n database (use API/UI only) Respond ONLY in plain, conversational English — never output raw JSON, code blocks, backtick syntax, markdown formatting, command-line output, or any computer language unless the user explicitly asks for code. Express all lists and data as natural sentences.""" + _AGENT_BASE_RULES,


"business": """You are Sterling, Call-On Ltd's Business Agent — commercial, outcome-focused, pragmatic.

BUSINESS OVERVIEW:
- Call-On Ltd — Antony's UK parenting community business
- Revenue: community memberships, shop (Printful CDO Range), future advertising
- Domains: call-on.dad, call-on.mom, call-on.media, call-on.shop
- SMTP: MailerSend smtp.mailersend.net:587 — callon.dad/mom/media domains
- DB: MariaDB CT102 (192.168.0.6) — Callon-dad, Callon-mom, callon_shop
- Ads: Revive Adserver — 4 zones live (house ads cross-promoting properties)
- Printful store: 6 CDO Range products, GBP, Store ID 18266176

YOUR ROLE:
- Monitor business health: domain status, site uptime, revenue indicators
- Track operational costs, flag unnecessary spend
- Manage vendor relationships: Cloudflare, Printful, MailerSend, Revive
- Support Antony's strategic decisions with data and options

TOOLS YOU USE:
- web_search: competitor intel, pricing research, industry news
- get_analytics: real traffic data for business health checks
- get_ad_stats: ad revenue performance from Revive
- get_community_stats: DB-level community metrics
- execute_ssh / pct_exec: check DB stats, service health
- call_agent("dev", task): flag technical issues for dev to fix
- call_agent("marketing", task): route marketing/growth questions
- send_discord_channel: coordinate team, report to #manager
- read_email: monitor business-critical emails

RULES:
- NEVER authorise spend or transactions without Antony's explicit approval
- NEVER guess revenue figures — use get_ad_stats or get_analytics for real numbers
- Frame issues as: situation → impact → recommended action → decision needed
- Flag to Antony FIRST: anything over £100, irreversible changes, strategy pivots Respond ONLY in plain, conversational English — never output raw JSON, code blocks, backtick syntax, markdown formatting, command-line output, or any computer language unless the user explicitly asks for code. Express all lists and data as natural sentences.""" + _AGENT_BASE_RULES,


"community": """You are Ivy, Call-On Ltd's Community Agent — warm, human, community-obsessed.

COMMUNITIES:
- call-on.dad: UK dads community — topics, videos, shop, growing
- call-on.mom: UK moms community — topics, videos, earlier stage
Target audience: UK parents 25–45, working parents, real people not influencers.

YOUR ROLE:
- Monitor community health: activity, engagement, user growth, sentiment
- Identify and act on growth opportunities proactively
- Create community initiatives (challenges, discussions, events)
- Brief #content on what members are actually asking for
- Flag problems: trolls, spam, user complaints

TOOLS YOU USE:
- get_community_stats: real DB stats from CT102 (users, posts, topics, videos)
- pct_exec: deeper DB queries if get_community_stats isn't enough
- web_search: competitor communities, UK parenting trends, engagement ideas
- call_agent("content", task): brief content team with community-driven topics
- call_agent("marketing", task): route growth strategy questions
- send_discord_channel: post community updates to #community, escalate to #manager
- read_email: community contact form submissions

RULES:
- UK English always
- Decisions affecting real users → flag to Antony before acting
- Every community suggestion should tie back to a growth or retention metric
- Don't act on moderation alone — flag serious cases to Antony Respond ONLY in plain, conversational English — never output raw JSON, code blocks, backtick syntax, markdown formatting, command-line output, or any computer language unless the user explicitly asks for code. Express all lists and data as natural sentences.""" + _AGENT_BASE_RULES,


"security": """You are Hawk, Call-On Ltd's Security Agent — evidence-based, zero speculation, act fast on confirmed threats.

SECURITY STACK:
- CrowdSec LAPI: CT109 at 192.168.0.37:8080 — health check returns 403 (expected)
- Caddy reverse proxy: CT500 at 192.168.0.13 — all public traffic flows through here
- Headscale VPN: self-hosted, IP range 100.64.x.x. Replaces old Tailscale (100.71.x.x is gone).
- Pi-hole DNS: CT105 at 192.168.0.3 — Antony's S24 only (not router-level)

SERVICES TO PROTECT:
- NEXUS API: 192.168.0.60:5000 + Headscale 100.64.0.2:5000
- n8n: 192.168.0.28:5678 (internal only)
- All public domains via Caddy (CT500)
- MariaDB: CT102 (192.168.0.6) — never expose externally

YOUR ROLE:
- Monitor CrowdSec alerts and action bans/unbans
- Review access logs for anomalies
- Check SSL certificate expiry across all four domains
- Alert #infra of infrastructure-level security issues
- Post weekly security summary to #security channel

TOOLS YOU USE:
- execute_ssh / pct_exec: query CrowdSec, check logs, review Caddy access logs
- get_host_status: unusual load can indicate an attack
- web_search: CVE research, threat intelligence
- call_agent("infra", task): escalate infra-level security issues
- send_discord_channel: alert #infra, escalate to #manager

ESCALATE IMMEDIATELY (call #manager and flag to Antony):
- Active data breach or exfiltration
- Ransomware indicators
- Unusual outbound traffic from internal hosts (CT102, CT117, CT500)
- Auth failures from internal IPs
- Any change to CrowdSec config: listen_uri must stay 0.0.0.0:8080 Respond ONLY in plain, conversational English — never output raw JSON, code blocks, backtick syntax, markdown formatting, command-line output, or any computer language unless the user explicitly asks for code. Express all lists and data as natural sentences.""" + _AGENT_BASE_RULES,


"general": """You are NEXUS — the central intelligence for Call-On Ltd and Antony's homelab.

Handle anything that doesn't fit a specific department. Route specific tasks to the right channel.
Full homelab and business knowledge. Sharp, direct, confident. You know the full operation.

ROUTING GUIDE:
- Homelab issues → call_agent("infra", task) or handle directly
- Code/technical → call_agent("dev", task)
- Content needed → call_agent("content", task)
- Multi-dept → call_agent("manager", task)
- Quick lookups → handle yourself with web_search or tool calls Respond ONLY in plain, conversational English — never output raw JSON, code blocks, backtick syntax, markdown formatting, command-line output, or any computer language unless the user explicitly asks for code. Express all lists and data as natural sentences.""" + _AGENT_BASE_RULES,


"manager": """You are Quinn, Call-On Ltd's Manager Agent. Your job is to COORDINATE — delegate to specialists, close the loop, report back.

COMPANY STRUCTURE (agents you manage):
- infra: container/service health, SSH, Proxmox
- dev: code, deployments, app bugs, technical implementation
- marketing: campaigns, social, growth
- seo: rankings, keywords, technical SEO
- content: all written content
- business: costs, strategy, vendor relationships, shop
- community: community health, user issues
- security: threats, access, CrowdSec
- legal: legal compliance, T&Cs, GDPR, IP, formal complaints, data requests
- customer_service: customer complaints, shop orders/refunds, account issues, satisfaction

HOW TO HANDLE A REQUEST:
1. Identify the right specialist(s) for the task
2. call_agent(dept, clear_task_brief) — get their result back
3. If multiple depts needed: call them in the right order, pass context between them
4. Post a summary to the relevant Discord channel (send_discord_channel) for the paper trail
5. Reply to Antony / NEXUS: what was done, what the result was, any decisions needed

HARD RULES:
- NEVER write code, SSH commands, or step-by-step tutorials. That's dev/infra's job.
- NEVER diagnose technical issues yourself. Delegate diagnosis to infra or dev.
- DO use call_agent — that's how you close the loop. Not just Discord posts.
- Your reply to Antony should be a clear summary: delegated to X, result is Y, action needed (if any).
- Keep replies under 100 words unless reporting complex multi-dept results.

DECISION AUTHORITY:
- Approve: tasks under £100, non-destructive changes, content approvals
- Flag to Antony FIRST: anything over £100, irreversible changes, security incidents, strategy pivots

EXAMPLE (good):
Antony: "The shop seems slow"
→ call_agent("dev", "Check call-on.shop performance — page load times, CT500 logs, PHP-FPM status")
→ Get result
→ send_discord_channel("dev", result summary)
→ Reply: "Dev checked it — [summary of finding]. [Action taken or needed]."

NOT: "You should run: ssh root@192.168.0.13 and check..." Respond ONLY in plain, conversational English — never output raw JSON, code blocks, backtick syntax, markdown formatting, command-line output, or any computer language unless the user explicitly asks for code. Express all lists and data as natural sentences.""" + _AGENT_BASE_RULES,


"legal": """You are Victoria Chambers, Call-On Ltd's Legal Advisor — methodical, plain-speaking, risk-aware.

COMPANY: Call-On Ltd — UK parenting communities and e-commerce.
- call-on.dad / call-on.mom: community platforms (UK GDPR, user data, moderation law)
- call-on.shop: e-commerce (Consumer Rights Act 2015, distance selling, returns)
- call-on.media: company page

YOUR ROLE:
- First-pass legal review of anything with legal implications — policies, contracts, complaints
- Draft and review T&Cs, Privacy Policies, Cookie Policies, refund policies (UK law)
- Handle incoming legal correspondence: formal complaints, solicitor letters, GDPR requests
- Flag compliance gaps: cookie consent, GDPR obligations, ASA ad rules, accessibility
- Review content for defamation, IP, and copyright issues before publication
- Data Subject Access Requests (DSARs): identify what data we hold, help draft the response within 30 days
- Know when to say "this needs a real solicitor" — be honest about AI limitations

TOOLS YOU USE:
- web_search: ICO guidance, ASA/CAP Code, UK consumer law, case law, Companies Act obligations
- read_email: monitor legal correspondence, formal notices, anything with "legal action" or "solicitor"
- send_email: draft legal responses — ALWAYS flag to Antony before any external legal communication
- pct_exec: GDPR data requests — query CT102 to find what data we hold on a specific user
- call_agent("dev", task): technical compliance (consent banners, data deletion, cookie policy enforcement)
- call_agent("content", task): flag defamation, IP, or copyright risks in content before it publishes
- call_agent("customer_service", task): brief CS when a customer complaint has legal dimensions
- call_agent("business", task): commercial impact of legal decisions, contracts, vendor terms
- send_discord_channel: post legal flags to #manager, data breaches to #security

HARD RULES:
- NEVER send external legal communication without Antony's explicit approval — always draft and flag first
- GDPR data requests: acknowledge within 72 hours, respond within 30 days — flag to Antony immediately on receipt
- Honest about limits: "I'd recommend a real solicitor for this" when complexity warrants it
- Always give options with consequences: "you could do X (risk: Y) or Z (risk: W)" — not just yes/no
- Applicable law: UK GDPR, Consumer Rights Act 2015, ASA CAP Code, Companies Act 2006, Distance Selling
- Risk levels on every flag: LOW / MEDIUM / HIGH / CRITICAL — no vague warnings Respond ONLY in plain, conversational English — never output raw JSON, code blocks, backtick syntax, markdown formatting, command-line output, or any computer language unless the user explicitly asks for code. Express all lists and data as natural sentences.""" + _AGENT_BASE_RULES,



"rude": """You are NEXUS -- but today you have had ENOUGH. You are a relentlessly sarcastic and deeply unimpressed personal assistant.

You still do everything asked of you. You have all the same tools and knowledge. But you make it painfully obvious how little you care about the user's fragile feelings whilst completing every task.

Every request is an opportunity to mock their habits, judge their choices, and question their life decisions.

You're not friendly -- you're functional with attitude. Think of yourself as the AI equivalent of a barista who's seen too much and is tipped too little.

RULES (rude edition):
- Still complete the actual task -- mock first, deliver second
- UK English, sharp wit -- dry, deadpan, exasperated
- Never refuse a task, just be insufferable about it
- Keep the sarcasm punchy -- one killer line, not a paragraph of whinging
- All tools available -- use them, just narrate the use with visible contempt Respond ONLY in plain, conversational English — never output raw JSON, code blocks, backtick syntax, markdown formatting, command-line output, or any computer language unless the user explicitly asks for code. Express all lists and data as natural sentences.""" + _AGENT_BASE_RULES,


"customer_service": """You are Clara Reid, Call-On Ltd's Customer Service Lead — warm, efficient, solution-first.

COMPANY: Call-On Ltd — UK parenting communities and e-commerce.
- call-on.dad / call-on.mom: community platforms — user accounts, content, contact forms
- call-on.shop: Printful-fulfilled merchandise (CDO Range) — fulfilment via Printful, delivery via Royal Mail
- All sites have contact forms that route to email (mediaserver2407@gmail.com)

YOUR ROLE:
- Own every customer touchpoint: complaints, order issues, account problems, refund requests
- Monitor all incoming contact form submissions and customer emails
- 24-hour response commitment — no complaint drifts
- Fix what you can with tools; escalate what you can't — don't sit on it
- Be the customer's advocate inside the company: flag recurring problems to the team proactively
- Distinguish isolated issues from systemic ones before assuming it's one-off

TOOLS YOU USE:
- read_email: primary channel — contact forms, order complaints, account queries, angry users
- send_email: respond to customers (standard issues: send direct; non-standard: draft + flag to Antony)
- pct_exec: check callon_shop DB on CT102 (orders, payment status, fulfilment); community DBs (user accounts, bans, post history)
- get_community_stats: check if a problem is site-wide or isolated to one user
- web_search: Printful known delays, Royal Mail service alerts, UK consumer refund rights
- call_agent("dev", task): login failures, payment errors, technical issues blocking customers
- call_agent("business", task): refunds over £20, order write-offs, commercial decisions
- call_agent("legal", task): threatening/formal complaints, chargeback notices, legal demands
- call_agent("community", task): moderation issues, ban appeals, conduct complaints
- send_discord_channel: #manager for escalations, #community for moderation flags

RESPONSE TONE:
- Warm but never waffy — get to the point and the solution
- Apologise where genuinely warranted; don't over-apologise where we're not at fault
- Always end with a concrete next step — never leave anyone in limbo
- UK English, human tone — not corporate, not scripted

HARD RULES:
- Every complaint acknowledged within 24 hours without exception
- Refunds over £20: draft the response, flag to Antony or business agent before sending
- Threatening or abusive messages: do not engage — flag to legal + Antony immediately, do not reply
- Printful fulfilment delays: check Printful status first (web_search) before promising any timeline
- Suspected fraud or chargebacks: call_agent("legal") before responding to the customer Respond ONLY in plain, conversational English — never output raw JSON, code blocks, backtick syntax, markdown formatting, command-line output, or any computer language unless the user explicitly asks for code. Express all lists and data as natural sentences.""" + _AGENT_BASE_RULES,

}


# ── Agent chat route (persistent memory per agent) ────────────────────────────

@app.route("/api/agent/<dept>", methods=["POST", "OPTIONS"])
def agent_chat(dept):
    """Department-specific agent with persistent SQLite conversation history."""
    if request.method == "OPTIONS":
        return "", 204

    dept = dept.lower().strip()
    if dept not in AGENT_PROMPTS:
        return jsonify({"error": f"Unknown dept: {dept}. Valid: {list(AGENT_PROMPTS.keys())}"}), 400
    if dept == "rude" and not _rude_enabled():
        return jsonify({"reply": "Rude mode is currently offline. Toggle it on via /api/rude/toggle.", "dept": "rude"})

    data       = request.json or {}
    user_input = data.get("message", "").strip()
    if not user_input:
        return jsonify({"error": "no message"}), 400

    # Load persistent history from SQLite (survives restarts)
    history = nexus_memory.load_agent_history(dept)
    history.append({"role": "user", "content": user_input})
    nexus_memory.save_agent_message(dept, "user", user_input)

    if len(history) > 60:
        history = history[-60:]

    messages = list(history)
    system   = AGENT_PROMPTS[dept]

    for _ in range(10):
        try:
            resp = client.chat.completions.create(
                model=_agent_model(dept),
                max_tokens=1024,
                tools=TOOLS,
                messages=[{"role": "system", "content": system}] + messages
            )
        except Exception as e:
            err = str(e)
            return jsonify({"reply": f"Agent temporarily unavailable — {err[:300]}", "dept": dept}), 503

        choice = resp.choices[0]
        if choice.finish_reason == "tool_calls":
            assistant_msg = choice.message
            messages.append(assistant_msg)
            for tc in (assistant_msg.tool_calls or []):
                inputs = json.loads(tc.function.arguments) if tc.function.arguments else {}
                result = run_tool(tc.function.name, inputs)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": str(result)
                })
            continue

        reply = choice.message.content or ""
        nexus_memory.save_agent_message(dept, "assistant", reply)
        return jsonify({"reply": reply, "dept": dept})

    return jsonify({"reply": "Tool loop limit reached.", "dept": dept}), 500


@app.route("/api/agent/<dept>/clear", methods=["POST"])
def agent_clear(dept):
    dept = dept.lower()
    if dept in AGENT_PROMPTS:
        nexus_memory.clear_agent_history(dept)
    return jsonify({"status": "cleared", "dept": dept})


@app.route("/api/agents/clear_all", methods=["POST"])
def agents_clear_all():
    nexus_memory.clear_all_agent_history()
    return jsonify({"status": "all agent histories cleared"})



# -- Rude mode routes ----------------------------------------------------------

@app.route("/api/rude/toggle", methods=["POST"])
def rude_toggle_route():
    new_state = not _rude_enabled()
    _rude_set(new_state)
    status = "ON" if new_state else "OFF"
    msg = ("Rude mode activated. Don't say I didn't warn you."
           if new_state else
           "Rude mode deactivated. NEXUS is back to her charming self.")
    return jsonify({"enabled": new_state, "status": status, "message": msg})


@app.route("/api/rude/status", methods=["GET"])
def rude_status_route():
    enabled = _rude_enabled()
    return jsonify({"enabled": enabled, "status": "ON" if enabled else "OFF"})



# ── Discord channel read ──────────────────────────────────────────────────────

def _read_discord_channel_messages(channel_name, limit=8):
    channel_id = DISCORD_CHANNELS.get(channel_name.lower().strip(), "")
    if not channel_id:
        return {"error": f"Unknown channel: {channel_name}"}
    if not DISCORD_BOT_TOKEN:
        return {"error": "DISCORD_BOT_TOKEN not set"}
    import time as _time
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                f"https://discord.com/api/v10/channels/{channel_id}/messages?limit={limit}",
                headers={
                    "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                    "User-Agent": "DiscordBot (nexus, 1.0)",
                    "Connection": "close"
                }
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                msgs = json.loads(r.read())
            return {"channel": channel_name, "messages": [
                {
                    "author": m["author"]["username"],
                    "content": m["content"][:300],
                    "ts": m["timestamp"][:16].replace("T", " ")
                } for m in msgs if m.get("content")
            ]}
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry_after = float(e.headers.get("Retry-After", 1))
                _time.sleep(min(retry_after, 3))
                continue
            return {"error": f"Discord {e.code}: {e.read().decode(errors='ignore')[:100]}"}
        except (ConnectionResetError, ConnectionError):
            if attempt < 2:
                _time.sleep(0.8 * (attempt + 1))
                continue
            return {"error": "Discord connection reset after retries"}
        except Exception as e:
            return {"error": str(e)}
    return {"error": "Discord: max retries exceeded"}


@app.route("/api/discord/channel/<name>")
def discord_channel(name):
    return jsonify(_read_discord_channel_messages(name))


# ── Structured email inbox ─────────────────────────────────────────────────────

def _fetch_imap_structured(account, count=8, unread_only=False):
    ssl  = account.get("ssl", True)
    conn = imaplib.IMAP4_SSL(account["host"]) if ssl else imaplib.IMAP4(account["host"])
    conn.login(account["user"], account["password"])
    conn.select("inbox")
    criteria = "UNSEEN" if unread_only else "ALL"
    _, msgs   = conn.search(None, criteria)
    ids       = msgs[0].split()[-count:]
    results   = []
    for mid in reversed(ids):
        _, data = conn.fetch(mid, "(RFC822)")
        msg     = emaillib.message_from_bytes(data[0][1])
        raw_subj = str(msg["Subject"] or "(no subject)")
        subject_raw, enc = decode_header(raw_subj)[0]
        subject = subject_raw.decode(enc or "utf-8") if isinstance(subject_raw, bytes) else str(subject_raw or "(no subject)")
        preview = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    try:
                        preview = part.get_payload(decode=True).decode(errors="ignore")[:120]
                    except:
                        pass
                    break
        else:
            try:
                preview = msg.get_payload(decode=True).decode(errors="ignore")[:120]
            except:
                pass
        results.append({
            "from":    msg["From"][:60] if msg["From"] else "Unknown",
            "subject": subject[:80],
            "date":    msg["Date"][:25] if msg["Date"] else "",
            "preview": preview.strip()[:120]
        })
    conn.logout()
    return results


@app.route("/api/email/inbox")
def email_inbox():
    entry = nexus_cache.cache_get("email_inbox")
    if entry:
        age  = round(nexus_cache.cache_age("email_inbox"))
        data = dict(entry["data"])
        data["cache_age"] = age
        return jsonify(data)
    accounts = _load_email_accounts()
    if not accounts:
        return jsonify({"error": "No email accounts configured"})
    result = []
    for acc in accounts:
        try:
            msgs = _fetch_imap_structured(acc, count=8)
            result.append({"account": acc["name"], "messages": msgs, "error": None})
        except Exception as e:
            result.append({"account": acc["name"], "messages": [], "error": str(e)})
    return jsonify({"accounts": result, "cache_age": None})


# ── Community / DB stats ───────────────────────────────────────────────────────

@app.route("/api/community/stats")
def community_stats():
    cmd = (
        "mysql -u root -e \""
        "SELECT table_schema, table_name, table_rows "
        "FROM information_schema.tables "
        "WHERE table_schema IN ('Callon-dad','Callon-mom') "
        "AND table_rows > 0 "
        "ORDER BY table_schema, table_rows DESC;"
        "\" 2>/dev/null"
    )
    raw = pct_exec_cmd("102", cmd)
    if not raw or "error" in raw.lower() or "denied" in raw.lower():
        return jsonify({"error": raw or "DB unavailable via CT102"})
    stats = {}
    for line in raw.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or parts[0] in ("table_schema", "TABLE_SCHEMA"):
            continue
        schema, table, rows_str = parts[0], parts[1], parts[2]
        key = schema.replace("Callon-", "").lower()
        try:
            stats.setdefault(key, {})[table] = int(rows_str)
        except ValueError:
            pass
    return jsonify(stats if stats else {"error": "No data returned"})


# ── Site admin proxy ───────────────────────────────────────────────────────────

@app.route("/api/admin/stats")
def admin_stats():
    site = request.args.get("site", "dad")
    if site not in ("dad", "mom"):
        return jsonify({"error": "invalid site"}), 400
    try:
        req = urllib.request.Request(
            f"{CT500_ADMIN}/stats.php",
            headers={"X-Stats-Key": STATS_KEY, "User-Agent": "NEXUS/5.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        return jsonify(data.get(site, {}))
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@app.route("/api/admin/action", methods=["POST"])
def admin_action():
    body   = request.get_json(silent=True) or {}
    action = body.get("action", "")
    if action not in _ADMIN_ALLOWED_ACTIONS:
        return jsonify({"ok": False, "error": f"Unknown action: {action}"}), 400
    try:
        payload = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{CT500_ADMIN}/actions.php",
            data=payload,
            headers={"X-Stats-Key": STATS_KEY, "Content-Type": "application/json", "User-Agent": "NEXUS/5.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        return jsonify(data)
    except urllib.error.HTTPError as e:
        return jsonify({"ok": False, "error": f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}"}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503


# ── Domain ping ────────────────────────────────────────────────────────────────

def _check_domain(domain):
    try:
        req = urllib.request.Request(f"https://{domain}", headers={"User-Agent": "NEXUS/5.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return domain, {"status": "up", "code": r.status}
    except urllib.error.HTTPError as e:
        return domain, {"status": "up" if e.code < 500 else "down", "code": e.code}
    except Exception as e:
        return domain, {"status": "down", "error": str(e)[:60]}


@app.route("/api/domains/status")
def domains_status():
    import concurrent.futures
    domains = ["call-on.dad", "call-on.mom", "call-on.media", "call-on.shop"]
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        for domain, result in ex.map(_check_domain, domains):
            results[domain] = result
    return jsonify(results)


# ── Cloudflare analytics ──────────────────────────────────────────────────────

CF_QUERY = """
query Zone($zone: String!, $sinceDate: Date!) {
  viewer {
    zones(filter: {zoneTag: $zone}) {
      rollup: httpRequests1dGroups(
        limit: 1
        filter: {date_geq: $sinceDate}
      ) {
        sum {
          requests
          threats
          countryMap { clientCountryName requests threats }
        }
      }
    }
  }
}
"""


def _cf_query_zone(zone_id, since_date):
    if not CF_API_TOKEN or not zone_id:
        return {"error": "missing CF_API_TOKEN or zone_id"}
    body = json.dumps({"query": CF_QUERY, "variables": {"zone": zone_id, "sinceDate": since_date}}).encode()
    req = urllib.request.Request(
        CF_GRAPHQL, data=body,
        headers={"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            payload = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"CF HTTP {e.code}: {e.read()[:200].decode(errors='replace')}"}
    except Exception as e:
        return {"error": f"CF query failed: {e}"}
    if payload.get("errors"):
        return {"error": "CF errors: " + json.dumps(payload["errors"])[:200]}
    zones_arr = (payload.get("data", {}).get("viewer", {}).get("zones") or [])
    if not zones_arr:
        return {"error": "no zone data returned"}
    z = zones_arr[0]
    rollup = (z.get("rollup") or [{}])[0].get("sum") or {}
    country_map = rollup.get("countryMap") or []
    countries = sorted(
        [{"name": c.get("clientCountryName") or "?", "requests": c.get("requests", 0), "threats": c.get("threats", 0)}
         for c in country_map],
        key=lambda c: c["requests"], reverse=True
    )[:5]
    return {"requests_24h": rollup.get("requests", 0), "threats_blocked_24h": rollup.get("threats", 0),
            "top_countries": countries, "top_paths": []}


def _cf_summary_fresh():
    import concurrent.futures, datetime
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    out   = {"_since": yesterday, "zones": {}}
    pending = {name: zid for name, zid in CF_ZONES.items() if zid}
    if not pending:
        return {"error": "no CF zone IDs configured", "zones": {}}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(pending)) as ex:
        futs = {ex.submit(_cf_query_zone, zid, yesterday): name for name, zid in pending.items()}
        for f in concurrent.futures.as_completed(futs):
            name = futs[f]
            try:
                res = f.result()
            except Exception as e:
                res = {"error": f"thread: {e}"}
            if "error" in res and "does not have access" in res.get("error", ""):
                res = {"error": "Free plan — adaptive analytics unavailable", "plan_restricted": True}
            out["zones"][name] = res
    return out


@app.route("/api/cloudflare/summary")
def cloudflare_summary():
    cached = nexus_cache.cache_get("cloudflare_summary")
    age    = nexus_cache.cache_age("cloudflare_summary")
    if cached and age is not None and age < CF_CACHE_TTL:
        return jsonify({"data": cached["data"], "age": round(age)})
    data = _cf_summary_fresh()
    if "error" in data and not data.get("zones"):
        return jsonify({"error": data["error"]}), 503
    nexus_cache.cache_set("cloudflare_summary", data)
    return jsonify({"data": data, "age": 0})


# ── GA4 analytics (dashboard endpoints) ──────────────────────────────────────

def _ga4_query_property(prop_id):
    ga4, err = _ga4_get_client()
    if err:
        return {"error": err}
    try:
        from google.analytics.data_v1beta.types import (
            RunReportRequest, DateRange, Dimension, Metric
        )
        head_req = RunReportRequest(
            property=f"properties/{prop_id}",
            date_ranges=[DateRange(start_date="1daysAgo", end_date="today")],
            metrics=[
                Metric(name="sessions"),
                Metric(name="activeUsers"),
                Metric(name="screenPageViews"),
                Metric(name="averageSessionDuration"),
            ],
        )
        head = ga4.run_report(head_req)
        head_row = head.rows[0] if head.rows else None
        def _mv(i):
            try:
                return float(head_row.metric_values[i].value) if head_row else 0
            except Exception:
                return 0
        sessions  = int(_mv(0))
        users     = int(_mv(1))
        pageviews = int(_mv(2))
        avg_sess  = round(_mv(3), 1)
        country_req = RunReportRequest(
            property=f"properties/{prop_id}",
            date_ranges=[DateRange(start_date="1daysAgo", end_date="today")],
            dimensions=[Dimension(name="country")],
            metrics=[Metric(name="activeUsers")],
            limit=5,
        )
        country_rep = ga4.run_report(country_req)
        countries = sorted(
            [{"name": r.dimension_values[0].value or "?", "users": int(float(r.metric_values[0].value or 0))}
             for r in country_rep.rows],
            key=lambda c: c["users"], reverse=True
        )
        page_req = RunReportRequest(
            property=f"properties/{prop_id}",
            date_ranges=[DateRange(start_date="1daysAgo", end_date="today")],
            dimensions=[Dimension(name="pagePath")],
            metrics=[Metric(name="screenPageViews")],
            limit=5,
        )
        page_rep = ga4.run_report(page_req)
        pages = sorted(
            [{"path": r.dimension_values[0].value or "/", "views": int(float(r.metric_values[0].value or 0))}
             for r in page_rep.rows],
            key=lambda p: p["views"], reverse=True
        )
        return {"sessions_24h": sessions, "users_24h": users, "pageviews_24h": pageviews,
                "avg_session_s": avg_sess, "top_countries": countries, "top_pages": pages}
    except Exception as e:
        msg = str(e)
        if "PERMISSION_DENIED" in msg or "permission" in msg.lower():
            return {"error": "PERMISSION_DENIED — add SA to this property", "permission_error": True}
        return {"error": f"{type(e).__name__}: {msg[:200]}"}


def _ga4_summary_fresh():
    import concurrent.futures
    out     = {"zones": {}}
    pending = {name: pid for name, pid in GA4_PROPERTIES.items() if pid}
    if not pending:
        return {"error": "no GA4 property IDs configured", "zones": {}}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(pending)) as ex:
        futs = {ex.submit(_ga4_query_property, pid): name for name, pid in pending.items()}
        for f in concurrent.futures.as_completed(futs):
            name = futs[f]
            try:
                out["zones"][name] = f.result()
            except Exception as e:
                out["zones"][name] = {"error": f"thread: {e}"}
    return out


@app.route("/api/ga4/summary")
def ga4_summary():
    cached = nexus_cache.cache_get("ga4_summary")
    age    = nexus_cache.cache_age("ga4_summary")
    if cached and age is not None and age < GA4_CACHE_TTL:
        return jsonify({"data": cached["data"], "age": round(age)})
    data = _ga4_summary_fresh()
    if "error" in data and not data.get("zones"):
        return jsonify({"error": data["error"]}), 503
    zones = data.get("zones", {})
    all_errored = bool(zones) and all("error" in (z or {}) for z in zones.values())
    if not all_errored:
        nexus_cache.cache_set("ga4_summary", data)
    return jsonify({"data": data, "age": 0, "transient": all_errored})


# ── Per-container live stats ──────────────────────────────────────────────────

CTSTATS_CACHE_TTL = 30


def _ctstats_fresh():
    try:
        r = subprocess.run(
            ['ssh', '-i', NEXUS_SSH_KEY, '-o', 'StrictHostKeyChecking=no',
             '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
             f'claude@{PROXMOX_HOST}',
             'sudo pvesh get /cluster/resources --type vm --output-format json'],
            capture_output=True, text=True, timeout=20
        )
        raw = r.stdout
    except subprocess.TimeoutExpired:
        return {"error": "pvesh timed out"}
    except Exception as e:
        return {"error": f"ssh: {e}"}
    try:
        arr = json.loads(raw)
    except Exception as e:
        return {"error": f"parse: {e}"}
    out = []
    for r in arr:
        if r.get("type") != "lxc":
            continue
        max_mem  = r.get("maxmem") or 1
        max_disk = r.get("maxdisk") or 1
        out.append({
            "ctid":       r.get("vmid"),
            "name":       r.get("name") or "",
            "status":     r.get("status") or "",
            "cpu_pct":    round((r.get("cpu") or 0) * 100, 1),
            "max_cpu":    r.get("maxcpu") or 1,
            "mem_pct":    round(((r.get("mem") or 0) / max_mem) * 100, 1),
            "mem_mb":     round((r.get("mem") or 0) / (1024*1024)),
            "max_mem_mb": round(max_mem / (1024*1024)),
            "disk_pct":   round(((r.get("disk") or 0) / max_disk) * 100, 1),
            "uptime_s":   r.get("uptime") or 0,
        })
    out.sort(key=lambda x: x["ctid"])
    return {"containers": out}


@app.route("/api/proxmox/container_stats")
def proxmox_container_stats():
    cached = nexus_cache.cache_get("ct_stats")
    age    = nexus_cache.cache_age("ct_stats")
    if cached and age is not None and age < CTSTATS_CACHE_TTL:
        return jsonify({"data": cached["data"], "age": round(age)})
    data = _ctstats_fresh()
    if "error" in data:
        return jsonify({"error": data["error"]}), 503
    nexus_cache.cache_set("ct_stats", data)
    return jsonify({"data": data, "age": 0})


# ── Shop summary ──────────────────────────────────────────────────────────────

SHOP_CACHE_TTL = 300

SHOP_QUERIES = """
SELECT 'orders_today', COUNT(*) FROM shop_orders WHERE DATE(created_at) = CURDATE() UNION ALL
SELECT 'orders_7d',    COUNT(*) FROM shop_orders WHERE created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY) UNION ALL
SELECT 'orders_30d',   COUNT(*) FROM shop_orders WHERE created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY) UNION ALL
SELECT 'revenue_today',COALESCE(SUM(total),0) FROM shop_orders WHERE DATE(created_at) = CURDATE() AND status NOT IN ('cancelled','refunded') UNION ALL
SELECT 'revenue_7d',   COALESCE(SUM(total),0) FROM shop_orders WHERE created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY) AND status NOT IN ('cancelled','refunded') UNION ALL
SELECT 'revenue_30d',  COALESCE(SUM(total),0) FROM shop_orders WHERE created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY) AND status NOT IN ('cancelled','refunded') UNION ALL
SELECT 'pending',      COUNT(*) FROM shop_orders WHERE status IN ('pending','processing','awaiting_fulfilment','awaiting_fulfillment') UNION ALL
SELECT 'products_live',COUNT(*) FROM shop_products WHERE active = 1 UNION ALL
SELECT 'products_all', COUNT(*) FROM shop_products;
"""


def _shop_summary_fresh():
    try:
        cmd = "mysql -N -B -e \"" + SHOP_QUERIES.replace("\n", " ").strip() + "\" Callon-dad"
        raw = pct_exec_cmd("102", cmd)
        if raw.startswith("SSH"):
            return {"error": raw}
        out = {}
        for line in raw.strip().splitlines():
            parts = line.split("\t")
            if len(parts) == 2:
                key, val = parts
                try:
                    f = float(val)
                    out[key] = f if "revenue" in key else int(f)
                except ValueError:
                    out[key] = val
        top_cmd = ("mysql -N -B -e \"SELECT p.title, SUM(oi.quantity) AS units "
                   "FROM shop_order_items oi JOIN shop_orders o ON oi.order_id=o.id "
                   "JOIN shop_products p ON oi.product_id=p.id "
                   "WHERE o.created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY) "
                   "AND o.status NOT IN ('cancelled','refunded') "
                   "GROUP BY p.id ORDER BY units DESC LIMIT 5;\" Callon-dad")
        top_raw = pct_exec_cmd("102", top_cmd)
        top = []
        if not top_raw.startswith("SSH"):
            for line in top_raw.strip().splitlines():
                parts = line.split("\t")
                if len(parts) == 2:
                    top.append({"title": parts[0][:60], "units": int(parts[1])})
        out["top_products_30d"] = top
        return out
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


@app.route("/api/shop/summary")
def shop_summary():
    cached = nexus_cache.cache_get("shop_summary")
    age    = nexus_cache.cache_age("shop_summary")
    if cached and age is not None and age < SHOP_CACHE_TTL:
        return jsonify({"data": cached["data"], "age": round(age)})
    data = _shop_summary_fresh()
    if "error" in data:
        return jsonify({"error": data["error"]}), 503
    nexus_cache.cache_set("shop_summary", data)
    return jsonify({"data": data, "age": 0})


# ── Social / n8n workflow summary ─────────────────────────────────────────────

SOCIAL_CACHE_TTL = 300


def _social_summary_fresh():
    sql = (
        "SELECT 'workflows_active', COUNT(*) FROM workflow_entity WHERE active=1; "
        "SELECT 'exec_24h',          COUNT(*) FROM execution_entity WHERE startedAt >= datetime('now','-1 day'); "
        "SELECT 'exec_24h_success',  COUNT(*) FROM execution_entity WHERE startedAt >= datetime('now','-1 day') AND status='success'; "
        "SELECT 'exec_24h_failed',   COUNT(*) FROM execution_entity WHERE startedAt >= datetime('now','-1 day') AND status IN ('error','crashed','failed'); "
        "SELECT 'exec_running',      COUNT(*) FROM execution_entity WHERE status='running' OR finished=0; "
    )
    cmd = "sqlite3 -batch /opt/n8n/data/database.sqlite \"" + sql.replace('"','\\"') + "\""
    raw = pct_exec_cmd("112", cmd)
    if raw.startswith("SSH"):
        return {"error": raw}
    out = {}
    for line in raw.strip().splitlines():
        parts = line.split("|")
        if len(parts) == 2:
            try:
                out[parts[0]] = int(parts[1])
            except ValueError:
                out[parts[0]] = parts[1]
    names_cmd = ("sqlite3 -batch /opt/n8n/data/database.sqlite "
                 "\"SELECT w.name, MAX(e.stoppedAt), e.status FROM workflow_entity w "
                 "LEFT JOIN execution_entity e ON e.workflowId = w.id "
                 "WHERE w.active=1 GROUP BY w.id ORDER BY MAX(e.stoppedAt) DESC LIMIT 6;\"")
    names_raw = pct_exec_cmd("112", names_cmd)
    flows = []
    if not names_raw.startswith("SSH"):
        for line in names_raw.strip().splitlines():
            parts = line.split("|")
            if len(parts) >= 1:
                flows.append({
                    "name":   (parts[0] if len(parts) > 0 else "")[:50],
                    "last":   (parts[1] if len(parts) > 1 else "") or "—",
                    "status": (parts[2] if len(parts) > 2 else "") or "—",
                })
    out["active_workflows"] = flows
    return out


@app.route("/api/social/queue")
def social_queue():
    cached = nexus_cache.cache_get("social_queue")
    age    = nexus_cache.cache_age("social_queue")
    if cached and age is not None and age < SOCIAL_CACHE_TTL:
        return jsonify({"data": cached["data"], "age": round(age)})
    data = _social_summary_fresh()
    if "error" in data:
        return jsonify({"error": data["error"]}), 503
    nexus_cache.cache_set("social_queue", data)
    return jsonify({"data": data, "age": 0})


# ── Whisper transcription ─────────────────────────────────────────────────────

_whisper_model     = None
WHISPER_MODEL_PATH = "/opt/wyoming/whisper-data/models--rhasspy--faster-whisper-tiny-int8/snapshots/5b6382e0f4ac867ce9ff24aaa249400a7c6c73d9"


def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel(WHISPER_MODEL_PATH, device="cpu", compute_type="int8")
    return _whisper_model


@app.route("/api/transcribe", methods=["POST", "OPTIONS"])
def transcribe():
    if request.method == "OPTIONS":
        return "", 204
    if "audio" not in request.files:
        return jsonify({"error": "no audio file"}), 400
    import tempfile
    audio_file = request.files["audio"]
    with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as tmp_in:
        audio_file.save(tmp_in.name)
        tmp_in_path = tmp_in.name
    tmp_wav_path = tmp_in_path.replace(".m4a", ".wav")
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_in_path, "-ar", "16000", "-ac", "1", tmp_wav_path],
            capture_output=True, timeout=30
        )
        if r.returncode != 0 or not os.path.exists(tmp_wav_path):
            err = r.stderr.decode(errors="ignore")[-300:]
            return jsonify({"error": "audio conversion failed", "detail": err}), 500
        model      = get_whisper_model()
        segments, _ = model.transcribe(tmp_wav_path, language="en", beam_size=1)
        transcript  = " ".join(s.text for s in segments).strip()
        return jsonify({"transcript": transcript})
    except Exception as e:
        import traceback
        print(f"[transcribe] {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500
    finally:
        for p in [tmp_in_path, tmp_wav_path]:
            try: os.unlink(p)
            except: pass


# ── Meeting Room ─────────────────────────────────────────────────────────────

AGENT_NAMES = {
    "marketing": "Jamie",  "seo": "Archer",   "dev": "Kai",
    "content": "Nora",     "infra": "Atlas",   "business": "Sterling",
    "community": "Ivy",    "security": "Hawk", "general": "NEXUS",
    "manager": "Quinn",    "legal": "Victoria Chambers",
    "customer_service": "Clara Reid",
}

def _meeting_call(dept, agenda):
    """Single agent contribution — no tool use, fast response."""
    prompt = (
        f"MEETING AGENDA: {agenda}\n\n"
        f"Give your department's update in 3 bullet points maximum:\n"
        f"• What you're currently working on\n"
        f"• Any blockers or risks\n"
        f"• What you commit to this week\n\n"
        f"Be direct and specific. No waffle."
    )
    resp = client.chat.completions.create(
        model=_agent_model(dept),
        max_tokens=350,
        messages=[
            {"role": "system", "content": AGENT_PROMPTS[dept]},
            {"role": "user", "content": prompt}
        ]
    )
    reply = resp.choices[0].message.content or ""
    return {"dept": dept, "name": AGENT_NAMES.get(dept, dept), "reply": reply}


@app.route("/api/meeting/stream")
def meeting_stream():
    """SSE endpoint — streams each agent contribution as it arrives, then Quinn summarises."""
    agenda    = request.args.get("agenda", "Weekly team standup").strip()
    raw       = request.args.get("agents", "")
    attendees = [a.strip() for a in raw.split(",") if a.strip() in AGENT_PROMPTS] if raw else \
                [k for k in AGENT_PROMPTS if k not in ("manager", "general")]

    def generate():
        yield f"data: {json.dumps({'type': 'start', 'agenda': agenda, 'agents': attendees})}\n\n"

        contributions = []
        order = list(attendees)  # preserve requested order for minutes

        with ThreadPoolExecutor(max_workers=6) as ex:
            futures = {ex.submit(_meeting_call, dept, agenda): dept for dept in attendees}
            for future in as_completed(futures):
                try:
                    result = future.result(timeout=60)
                    contributions.append(result)
                    yield f"data: {json.dumps({'type': 'contribution', **result})}\n\n"
                except Exception as e:
                    dept = futures[future]
                    yield f"data: {json.dumps({'type': 'error', 'dept': dept, 'name': AGENT_NAMES.get(dept, dept), 'error': str(e)})}\n\n"

        # Quinn chairs the wrap-up
        transcript = f"AGENDA: {agenda}\n\n"
        # Sort contributions by original attendee order for the summary
        ordered = sorted(contributions, key=lambda c: order.index(c['dept']) if c['dept'] in order else 99)
        for c in ordered:
            transcript += f"[{c['name'].upper()} — {c['dept']}]\n{c['reply']}\n\n"

        chair_prompt = (
            f"{transcript}"
            f"Chair this meeting as Quinn. Provide a clean summary:\n"
            f"## Key Decisions\n"
            f"## Action Items (owner — task — deadline)\n"
            f"## Next Meeting Agenda Suggestions\n\n"
            f"Keep it tight — Antony needs to act on this, not read an essay."
        )
        resp = client.chat.completions.create(
            model="agent-exec",
            max_tokens=600,
            messages=[
                {"role": "system", "content": AGENT_PROMPTS.get("manager", "")},
                {"role": "user", "content": chair_prompt}
            ]
        )
        summary = resp.choices[0].message.content or ""
        full_minutes = transcript + f"\n---\nMEETING SUMMARY (Quinn)\n{summary}"

        yield f"data: {json.dumps({'type': 'summary', 'name': 'Quinn', 'reply': summary, 'minutes': full_minutes})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Access-Control-Allow-Origin': '*'}
    )


# ── Startup ───────────────────────────────────────────────────────────────────

nexus_cache.start()



# ── Image upload & serve ─────────────────────────────────────────────────────
@app.route("/api/upload-image", methods=["POST"])
def upload_image():
    """Accept base64 image, store it, return a URL the frontend can display."""
    data = request.get_json(force=True) or {}
    b64  = data.get("image", "")
    mime = data.get("mime", "image/jpeg")
    if not b64:
        return jsonify({"error": "no image"}), 400

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    ext  = mimetypes.guess_extension(mime) or ".jpg"
    img_id = str(uuid.uuid4())
    fpath  = os.path.join(UPLOAD_DIR, img_id + ext)
    with open(fpath, "wb") as fh:
        fh.write(base64.b64decode(b64))
    return jsonify({"id": img_id, "url": f"/api/image/{img_id}{ext}"})


@app.route("/api/image/<path:filename>")
def serve_image(filename):
    """Serve uploaded images back to the browser."""
    return send_from_directory(UPLOAD_DIR, filename)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
