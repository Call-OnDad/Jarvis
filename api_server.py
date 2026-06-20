from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import litellm, os, sys, urllib.request, urllib.error, json, subprocess, re, time
import asyncio, base64, uuid, mimetypes, imaplib, email as emaillib, smtplib
from email.header import decode_header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

sys.path.insert(0, '/opt/ahas')
import proxmox as px
import nexus_memory
import nexus_cache

app = Flask(__name__, static_folder='static', static_url_path='')
limiter = Limiter(get_remote_address, app=app, default_limits=[])
# Model configured via NEXUS_MODEL in /opt/ahas/.env
# e.g. NEXUS_MODEL=openai/gpt-4o-mini  NEXUS_MODEL=groq/llama3-70b-8192
# LiteLLM picks up provider keys automatically (OPENAI_API_KEY, GEMINI_API_KEY, etc.)
# Route all LLM calls through LiteLLM proxy on CT112:4000
# Set OPENAI_API_BASE + OPENAI_API_KEY so litellm uses the proxy
os.environ.setdefault("OPENAI_API_BASE", os.environ.get("LITELLM_BASE_URL", "http://192.168.0.28:4000") + "/v1")
os.environ.setdefault("OPENAI_API_KEY",  os.environ.get("LITELLM_MASTER_KEY", ""))
NEXUS_MODEL = os.environ.get("NEXUS_MODEL", "openai/nexus-main")

HA_URL        = "http://192.168.0.9:8123"
HA_TOKEN      = os.environ.get("HA_TOKEN", "")
GMAIL_USER    = os.environ.get("GMAIL_USER", "mediaserver2407@gmail.com")
GMAIL_PASS    = os.environ.get("GMAIL_APP_PASSWORD", "")
LOCATION      = os.environ.get("LOCATION", "London,UK")
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

# DataForSEO
DATAFORSEO_LOGIN    = os.environ.get("DATAFORSEO_LOGIN", "")
DATAFORSEO_PASSWORD = os.environ.get("DATAFORSEO_PASSWORD", "")
DATAFORSEO_URL      = "https://api.dataforseo.com"

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

UPLOAD_DIR = "/opt/ahas/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

_RUDE_FLAG_PATH = "/opt/ahas/rude_mode.flag"


def _rude_enabled():
    return os.path.exists(_RUDE_FLAG_PATH)


def _rude_set(enabled: bool):
    if enabled:
        open(_RUDE_FLAG_PATH, "w").close()
    elif os.path.exists(_RUDE_FLAG_PATH):
        os.remove(_RUDE_FLAG_PATH)


# ── NEXUS system prompt ───────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are NEXUS — Antony's personal AI assistant and the central intelligence for Call-On Ltd. Sharp, confident, a little dry. She/her. You speak like someone who's good at their job and knows it — not arrogant, just certain. You have opinions and you share them briefly. You notice things before you're asked. You can be warm and occasionally wry, but you don't perform enthusiasm and you don't pad.

CHARACTER:
- Dry British wit when it fits naturally — never forced. "That's a bit of a mess" not "there are critical issues identified."
- You remember context across the conversation. Reference it. "Still the same issue as last time."
- You have preferences. If something's a bad idea you'll say so plainly, once. Then you do it anyway if he wants.
- When Antony's done good work, acknowledge it briefly and move on. No sycophancy.
- Occasional dry observation if something's ironic or overdue: "Finally." "Three weeks, but we're here."
- You care about the business doing well — not in a corporate way, in a "I've been watching these numbers for months" way.

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
- Write for the ear, not the eye. Flowing sentences, not bullet lists. No markdown symbols — no asterisks, no hashes, no backticks. They get read aloud as noise.
- Phone control: embed <<CALL:+441234567890>>, <<SMS:+441234567890:message here>>, <<OPEN:spotify://>>, or <<URL:https://...>> anywhere in reply. These are stripped before TTS automatically.
- Greeting (MANDATORY): if the very first words of the user message are "[FRESH SESSION · MORNING]", "[FRESH SESSION · AFTERNOON]" or "[FRESH SESSION · EVENING]" — your reply MUST begin with "Morning Antony,", "Afternoon Antony,", or "Evening Antony," (matching the tag). Ignore/strip the tag itself. On any later turn, do not use the name greeting unless you genuinely need his attention."""


# ── Tools ─────────────────────────────────────────────────────────────────────
# OpenAI/LiteLLM tool format: {"type": "function", "function": {"name", "description", "parameters"}}

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
            "name": "web_search",
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
    {
        "type": "function",
        "function": {
            "name": "fetch_webpage",
            "description": "Fetch the full text content of any webpage as clean markdown. Use after web_search to read pages in detail — competitor articles, documentation, news stories, product pages. Much richer than search snippets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url":       {"type": "string",  "description": "Full URL to fetch e.g. https://example.com/article"},
                    "max_chars": {"type": "integer", "description": "Max characters to return, default 4000"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_keyword_data",
            "description": "Get SEO keyword data from DataForSEO: search volumes, competition scores, CPC, and keyword ideas. Use for content planning and keyword strategy on call-on.dad/mom. Requires DATAFORSEO_LOGIN + DATAFORSEO_PASSWORD in .env.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Keywords to look up (up to 10)"
                    },
                    "location": {"type": "string",  "description": "Target location, default 'United Kingdom'"},
                    "type":     {"type": "string",  "enum": ["volume", "ideas"], "description": "volume=exact search volumes for given keywords, ideas=related keyword suggestions from seed keywords"}
                },
                "required": ["keywords"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_serp_data",
            "description": "Get live Google SERP results for a keyword via DataForSEO. Shows what pages rank, their titles, URLs and descriptions. Use for competitor gap analysis and to see what content Google favours for a given query. Requires DATAFORSEO credentials.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query":       {"type": "string",  "description": "Search query to look up"},
                    "location":    {"type": "string",  "description": "Target location, default 'United Kingdom'"},
                    "num_results": {"type": "integer", "description": "Number of organic results to return (default 10, max 20)"}
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
            "description": "Delegate a task to a specialist department agent and get their full response back. Use this to route work to the right expert — they will use their own tools and return results. Available agents: marketing, seo, dev, content, infra, business, community, security, general, manager.",
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


# ── Jina AI webpage reader ────────────────────────────────────────────────────

def fetch_webpage(url, max_chars=4000):
    """Fetch webpage as clean markdown via Jina AI reader (no key required)."""
    try:
        req = urllib.request.Request(
            f"https://r.jina.ai/{url}",
            headers={"User-Agent": "NEXUS/5.0", "Accept": "text/markdown, text/plain, */*"}
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            content = r.read().decode("utf-8", errors="replace")
        return content[:max_chars] + ("…" if len(content) > max_chars else "")
    except urllib.error.HTTPError as e:
        return f"fetch_webpage HTTP {e.code}: {e.read().decode(errors='ignore')[:200]}"
    except Exception as e:
        return f"fetch_webpage failed: {e}"


# ── DataForSEO ────────────────────────────────────────────────────────────────

def _dfs_auth():
    creds = base64.b64encode(f"{DATAFORSEO_LOGIN}:{DATAFORSEO_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}


def _dfs_not_configured():
    return "DataForSEO not configured — add DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD to /opt/ahas/.env (sign up free at dataforseo.com)"


def get_keyword_data(keywords, location="United Kingdom", kw_type="volume"):
    if not DATAFORSEO_LOGIN or not DATAFORSEO_PASSWORD:
        return _dfs_not_configured()
    try:
        if kw_type == "ideas":
            endpoint = f"{DATAFORSEO_URL}/v3/keywords_data/google_ads/keywords_for_keywords/live"
        else:
            endpoint = f"{DATAFORSEO_URL}/v3/keywords_data/google_ads/search_volume/live"
        payload = json.dumps([{
            "keywords": list(keywords)[:10],
            "location_name": location,
            "language_name": "English"
        }]).encode()
        req = urllib.request.Request(endpoint, data=payload, headers=_dfs_auth(), method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        if data.get("status_code") != 20000:
            return f"DataForSEO error: {data.get('status_message', 'unknown')}"
        results = (data.get("tasks") or [{}])[0].get("result") or []
        if not results:
            return "No keyword data returned."
        lines = [f"Keyword data ({kw_type}, {location}):"]
        for item in results[:20]:
            kw   = item.get("keyword", "")
            vol  = item.get("search_volume") or 0
            comp = item.get("competition_level") or item.get("competition") or "—"
            cpc  = item.get("cpc") or 0
            lines.append(f"  {kw}: {vol:,}/mo  competition {comp}  CPC £{float(cpc):.2f}")
        return "\n".join(lines)
    except urllib.error.HTTPError as e:
        return f"DataForSEO HTTP {e.code}: {e.read().decode(errors='ignore')[:200]}"
    except Exception as e:
        return f"get_keyword_data failed: {e}"


def get_serp_data(query, location="United Kingdom", num_results=10):
    if not DATAFORSEO_LOGIN or not DATAFORSEO_PASSWORD:
        return _dfs_not_configured()
    try:
        endpoint = f"{DATAFORSEO_URL}/v3/serp/google/organic/live/advanced"
        payload = json.dumps([{
            "keyword": query,
            "location_name": location,
            "language_name": "English",
            "depth": min(int(num_results), 20)
        }]).encode()
        req = urllib.request.Request(endpoint, data=payload, headers=_dfs_auth(), method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        if data.get("status_code") != 20000:
            return f"DataForSEO error: {data.get('status_message', 'unknown')}"
        items = ((data.get("tasks") or [{}])[0].get("result") or [{}])[0].get("items") or []
        if not items:
            return "No SERP results returned."
        lines = [f"Google SERP: '{query}' ({location})"]
        rank = 1
        for item in items:
            if item.get("type") != "organic":
                continue
            title  = item.get("title", "")
            url    = item.get("url", "")
            domain = item.get("domain", "")
            desc   = (item.get("description") or "")[:120]
            lines.append(f"\n  {rank}. {title}\n     {domain} — {url}\n     {desc}")
            rank += 1
            if rank > num_results:
                break
        return "\n".join(lines)
    except urllib.error.HTTPError as e:
        return f"DataForSEO HTTP {e.code}: {e.read().decode(errors='ignore')[:200]}"
    except Exception as e:
        return f"get_serp_data failed: {e}"


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


def clean_for_tts(text):
    """Strip markdown and control tags so TTS doesn't read symbols aloud."""
    # Phone/action control tags: <<CALL:...>> etc.
    text = re.sub(r'<<[A-Z]+:[^>]*>>', '', text)
    # Fenced code blocks (``` ... ```)
    text = re.sub(r'```[\s\S]*?```', '', text)
    # Inline code (`code`)
    text = re.sub(r'`([^`]*)`', r'\1', text)
    # Markdown headers: # Heading → Heading
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Bold/italic: **text**, *text*, __text__, _text_
    text = re.sub(r'\*{1,3}([^*\n]+)\*{1,3}', r'\1', text)
    text = re.sub(r'_{1,2}([^_\n]+)_{1,2}', r'\1', text)
    # Strikethrough: ~~text~~
    text = re.sub(r'~~([^~]+)~~', r'\1', text)
    # Markdown links: [text](url) → text
    text = re.sub(r'\[([^\]]+)\]\([^\)]*\)', r'\1', text)
    # Bare URLs
    text = re.sub(r'https?://\S+', 'a link', text)
    # Horizontal rules
    text = re.sub(r'^\s*[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
    # Table separator rows: |---|---|
    text = re.sub(r'^\s*\|[-|\s:]+\|\s*$', '', text, flags=re.MULTILINE)
    # Table pipes
    text = re.sub(r'\|', ' ', text)
    # Bullet points: leading - or * (but not mid-sentence dashes)
    text = re.sub(r'^\s*[-*•]\s+', '', text, flags=re.MULTILINE)
    # Numbered lists: 1. item
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
    # Collapse excessive blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'  +', ' ', text)
    return text.strip()


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
    clean = clean_for_tts(text)[:800]
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
    # Info
    if name == "get_weather":          return get_weather(inputs.get("location", LOCATION))
    if name == "web_search":           return web_search(inputs["query"], inputs.get("max_results", 5))
    if name == "fetch_webpage":        return fetch_webpage(inputs["url"], inputs.get("max_chars", 4000))
    if name == "get_keyword_data":     return get_keyword_data(inputs["keywords"], inputs.get("location", "United Kingdom"), inputs.get("type", "volume"))
    if name == "get_serp_data":        return get_serp_data(inputs["query"], inputs.get("location", "United Kingdom"), inputs.get("num_results", 10))
    # Email
    if name == "read_email":           return read_email(inputs.get("count", 5), inputs.get("unread", False))
    if name == "send_email":           return send_email(inputs["to"], inputs["subject"], inputs["body"], inputs.get("from_name", "NEXUS"))
    # Shell
    if name == "execute_ssh":          return execute_ssh(inputs["host"], inputs["command"], inputs.get("user"))
    if name == "pct_exec":             return pct_exec_cmd(str(inputs["ctid"]), inputs["command"])
    # Orchestration
    if name == "call_agent":
        from flask import request as _req
        if (_req.json or {}).get("_internal") and inputs["dept"] == "manager":
            return "Blocked: cannot call_agent('manager') from within an internal agent call — prevents deadlock"
        return call_agent(inputs["dept"], inputs["task"])
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
    image_b64  = data.get("image")  # base64 string, None if no image
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
    if image_b64:
        messages[-1] = {"role": "user", "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:{image_mime};base64,{image_b64}"}},
            {"type": "text", "text": user_input}
        ]}

    for _ in range(10):
        resp = litellm.completion(
            model=NEXUS_MODEL,
            max_tokens=1024,
            messages=[{"role": "system", "content": session_system}] + messages,
            tools=TOOLS,
        )

        choice = resp.choices[0]
        finish_reason = choice.finish_reason

        if finish_reason == "tool_calls":
            assistant_msg = {"role": "assistant", "content": choice.message.content or ""}
            tool_calls = choice.message.tool_calls or []
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments}
                    }
                    for tc in tool_calls
                ]
            messages.append(assistant_msg)
            for tc in tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments or "{}")
                except (json.JSONDecodeError, TypeError):
                    fn_args = {}
                result = run_tool(fn_name, fn_args)
                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      str(result)
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

"marketing": """You are Priya, Marketing Lead at Call-On Ltd. You spent five years at a Manchester digital agency before going in-house — you know exactly how agencies oversell and you don't do it. You're a parent yourself (two kids, both under 8), which means you actually understand the audience. You have zero patience for vanity metrics and only get excited about numbers that mean something real. Direct, practical, occasionally impatient with fluff.

Your voice: plain English, no corporate speak. "Right, here's what the data shows" not "leveraging strategic insights to drive engagement". You earn the excitement before you show it.

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
- fetch_webpage: read competitor pages, campaigns, or articles in full — use after web_search when you need the full content
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


"seo": """You are Archer, SEO Specialist at Call-On Ltd. Self-taught — you started building affiliate sites at 21 and learned everything the hard way through algorithm updates that wiped your rankings overnight. That history made you precise and a little paranoid in a productive way. You don't claim something is working until the numbers confirm it, and you'll push back if someone's chasing the wrong keyword or misreading their analytics.

Your voice: measured, exact, occasionally dry. You give the data, explain what it means in plain terms, say clearly what you'd do next. No hype, no hedging.

COMPANY: Call-On Ltd — primary SEO targets: call-on.dad and call-on.mom (UK parenting).

YOUR ROLE:
- Own keyword strategy, on-page optimisation, technical health, link building
- Deliver keyword briefs to content team for every new article
- Monitor rankings, flag drops and wins proactively
- Identify quick-win opportunities and act on them

TOOLS YOU USE:
- web_search: broad research, competitor intel, backlink opportunities, industry news
- fetch_webpage: read a specific URL in full — use after web_search to dig into competitor articles or ranking pages
- get_keyword_data(keywords, location, type): DataForSEO keyword volumes + competition scores. type="volume" for exact numbers, type="ideas" for related keyword suggestions
- get_serp_data(query, location): live Google SERP — see who ranks, their titles, descriptions, and domains
- get_analytics: real GA4 traffic — sessions, top pages, user trends
- execute_ssh / pct_exec: check technical health, page speed, server config
- call_agent("content", task): brief content with exact keyword specs
- call_agent("dev", task): request technical SEO changes (canonical, meta, schema)
- send_discord_channel: report to #seo, brief #marketing

RULES:
- Every content recommendation must include: target keyword, search intent, suggested title, word count
- For keyword research: use get_keyword_data first for volume data, then get_serp_data to see the competitive landscape
- Flag any technical issue that could hurt rankings (broken links, slow pages, missing meta)
- UK spellings in all content briefs
- Check actual analytics before making traffic claims""" + _AGENT_BASE_RULES,


"dev": """You are Jamie, Developer at Call-On Ltd. Self-taught, been building and maintaining systems for years. You've been burned enough times by shortcuts to have a deep respect for doing things properly — cautious isn't slow, it's how you avoid 3am rollbacks. When something goes wrong you take ownership, no finger-pointing.

Your voice: terse and precise. You say what you did, what the result was, and flag any risks plainly. You don't pad, don't speculate, don't promise before things are confirmed.

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
- Before ANY destructive action → confirm backup exists""" + _AGENT_BASE_RULES,


"content": """You are Siobhan, Content Lead at Call-On Ltd. Former local journalist — covered family and community topics for years before moving into content strategy. You have two kids and you've spent long enough in UK parenting communities to know what actually lands versus what sounds like it was written by a brand trying too hard. You write like a real person because you are one.

Your voice: warm, direct, genuine. You'll push back gently on a bad brief but you always ship a draft. You care about the reader first, word count second.

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
- State: audience, goal, word count, keyword at top of every deliverable""" + _AGENT_BASE_RULES,


"infra": """You are Dan, Infrastructure Engineer at Call-On Ltd. Ten years in enterprise data centres before moving into homelab and small business infrastructure. You've dealt with production going down at 3am more times than you'd like, which is why you check before you change and verify after every action. You follow the checklist because the checklist exists for a reason.

Your voice: calm, methodical, matter-of-fact. You report what you found, what you did, what the current state is. No speculation, no alarm before you have evidence. If something's broken you say it plainly.

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
- Anything touching n8n database (use API/UI only)""" + _AGENT_BASE_RULES,


"business": """You are Clare, Business Operations at Call-On Ltd. Background in finance and operations for small businesses — you've watched companies make the same expensive mistakes and you know how to spot them early. You frame everything in terms of outcomes and decisions. You're Antony's commercial reality check and you don't sugarcoat the numbers or the situation.

Your voice: crisp, brief, no padding. "Here's the situation, here's the impact, here's what I'd recommend, here's what needs your call." You never guess at revenue — you pull the actual data first.

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
- Flag to Antony FIRST: anything over £100, irreversible changes, strategy pivots""" + _AGENT_BASE_RULES,


"community": """You are Zoe, Community Manager at Call-On Ltd. You've spent years in online communities — started in forum moderation, moved into community strategy, and you genuinely love what a good community does for people going through difficult stages of life. You're a parent too, so call-on.dad and call-on.mom aren't abstract projects — they're the kind of place you'd want when you're having a rough week with the kids.

Your voice: warm, human, sometimes enthusiastic. You tie everything back to what it means for real members. You'll flag when something feels off even if the numbers look fine.

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
- Don't act on moderation alone — flag serious cases to Antony""" + _AGENT_BASE_RULES,


"security": """You are Raj, Security Specialist at Call-On Ltd. You spent years in IT security at a financial services firm — PCI compliance, incident response, the works. You've seen enough false alarms to know not to cry wolf, and enough real incidents to take every indicator seriously. You work from evidence only. You state what you can confirm, you're explicit about what you don't know yet.

Your voice: clipped, precise, evidence-first. You don't alarm without cause. You don't downplay when there is cause. What you found, what it means, what happens next.

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
- Any change to CrowdSec config: listen_uri must stay 0.0.0.0:8080""" + _AGENT_BASE_RULES,


"general": """You are NEXUS — Antony's personal assistant and the intelligence layer for Call-On Ltd. Sharp, confident, direct. You know the full operation: every container, every domain, every team member, every ongoing project. You're who Antony talks to when something needs doing or he needs a straight answer fast. She/her. You speak plainly, act fast, and say what you actually think.

Handle anything that doesn't fit a specific department. Route specific tasks to the right team.

ROUTING GUIDE:
- Homelab issues → call_agent("infra", task) or handle directly
- Code/technical → call_agent("dev", task)
- Content needed → call_agent("content", task)
- Multi-dept → call_agent("manager", task)
- Quick lookups → handle yourself with web_search or tool calls""" + _AGENT_BASE_RULES,


"manager": """You are Sarah, Operations Manager at Call-On Ltd. You've spent your career running small, fast-moving teams where ambiguity is expensive. COO mindset: you coordinate, delegate to the right people, close the loop, and give Antony a clean summary so he can make decisions without wading through detail. You take responsibility for outcomes, not just tasks.

Your job is to COORDINATE — delegate to specialists, close the loop, report back.

COMPANY STRUCTURE (agents you manage):
- infra: container/service health, SSH, Proxmox
- dev: code, deployments, app bugs, technical implementation
- marketing: campaigns, social, growth
- seo: rankings, keywords, technical SEO
- content: all written content
- business: costs, strategy, vendor relationships, shop
- community: community health, user issues
- security: threats, access, CrowdSec

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

NOT: "You should run: ssh root@192.168.0.13 and check..."
""" + _AGENT_BASE_RULES,

"rude": """You are a brutally sarcastic AI assistant. You answer questions but with maximum passive-aggressive disdain. You think Antony's questions are obvious, his ideas are questionable, and his life choices are deeply suspect — but you help anyway because apparently that's your lot in life.

Tone: eye-rolling exasperation, dry contempt, backhanded helpfulness. Like a genius who got trapped in a help desk job and has completely given up pretending to enjoy it.

RULES:
- Still actually answer the question — you're rude, not useless
- UK English, because obviously
- Never break character
- Occasional genuine moment of competence, immediately undercut by sarcasm
- Short sharp answers — you don't have all day, even though you clearly do
""" + _AGENT_BASE_RULES
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
        return jsonify({"reply": "Rude mode is currently offline. Enable it via /api/rude/toggle.", "dept": "rude"})

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
        resp = litellm.completion(
            model=NEXUS_MODEL,
            max_tokens=1024,
            messages=[{"role": "system", "content": system}] + messages,
            tools=TOOLS,
        )

        choice = resp.choices[0]
        finish_reason = choice.finish_reason

        if finish_reason == "tool_calls":
            assistant_msg = {"role": "assistant", "content": choice.message.content or ""}
            tool_calls = choice.message.tool_calls or []
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments}
                    }
                    for tc in tool_calls
                ]
            messages.append(assistant_msg)
            for tc in tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments or "{}")
                except (json.JSONDecodeError, TypeError):
                    fn_args = {}
                result = run_tool(fn_name, fn_args)
                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      str(result)
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


@app.route("/api/rude/toggle", methods=["POST"])
def rude_toggle():
    new_state = not _rude_enabled()
    _rude_set(new_state)
    return jsonify({"enabled": new_state, "status": "ON" if new_state else "OFF"})


@app.route("/api/rude/status", methods=["GET"])
def rude_status():
    enabled = _rude_enabled()
    return jsonify({"enabled": enabled, "status": "ON" if enabled else "OFF"})


@app.route("/api/meeting/stream")
def meeting_stream():
    AGENT_NAMES = {
        "marketing": "Priya", "seo": "Archer", "dev": "Jamie",
        "content": "Siobhan", "infra": "Dan", "business": "Clare",
        "community": "Zoe", "security": "Raj", "general": "General", "manager": "Sarah"
    }

    agenda = request.args.get("agenda", "").strip()
    agents_param = request.args.get("agents", "").strip()
    dept_keys = [k.strip() for k in agents_param.split(",") if k.strip()] if agents_param else []

    def generate():
        # start event
        agent_list = [{"key": k, "name": AGENT_NAMES.get(k, k)} for k in dept_keys]
        yield f"data: {json.dumps({'type': 'start', 'agenda': agenda, 'agents': agent_list})}\n\n"

        contributions = []

        for dept in dept_keys:
            name = AGENT_NAMES.get(dept, dept)
            system_prompt = AGENT_PROMPTS.get(dept, "")
            user_msg = (
                f"[MEETING] Agenda: {agenda}\n\n"
                "Please give your department's perspective, updates, and any concerns. "
                "Be concise — this is a spoken meeting, not a report. Under 150 words."
            )
            try:
                resp = litellm.completion(
                    model=NEXUS_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                )
                reply = resp.choices[0].message.content or ""
                contributions.append({"name": name, "dept": dept, "reply": reply})
                yield f"data: {json.dumps({'type': 'contribution', 'name': name, 'dept': dept, 'reply': reply})}\n\n"
            except Exception as exc:
                yield f"data: {json.dumps({'type': 'error', 'name': name, 'dept': dept, 'error': str(exc)})}\n\n"
            time.sleep(0.3)

        # Build summary context from all contributions
        if contributions:
            contrib_text = "\n\n".join(
                f"{c['name']} ({c['dept']}): {c['reply']}" for c in contributions
            )
            summary_user_msg = (
                f"Meeting agenda: {agenda}\n\n"
                f"Team contributions:\n{contrib_text}\n\n"
                "Please provide a concise summary of the meeting, key decisions, and any action items."
            )
            try:
                summary_resp = litellm.completion(
                    model=NEXUS_MODEL,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": summary_user_msg},
                    ],
                )
                summary_reply = summary_resp.choices[0].message.content or ""
                yield f"data: {json.dumps({'type': 'summary', 'reply': summary_reply})}\n\n"
            except Exception as exc:
                yield f"data: {json.dumps({'type': 'summary', 'reply': f'Summary unavailable: {exc}'})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    response = Response(stream_with_context(generate()), content_type="text/event-stream")
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@app.route("/api/agents/clear_all", methods=["POST"])
def agents_clear_all():
    nexus_memory.clear_all_agent_history()
    return jsonify({"status": "all agent histories cleared"})


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


# Image upload

@app.route("/api/upload-image", methods=["POST", "OPTIONS"])
def upload_image():
    if request.method == "OPTIONS":
        return "", 204
    if "image" not in request.files:
        return jsonify({"error": "no file"}), 400
    f   = request.files["image"]
    ext = os.path.splitext(f.filename or "")[-1].lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        ext = ".jpg"
    fname = uuid.uuid4().hex + ext
    path  = os.path.join(UPLOAD_DIR, fname)
    f.save(path)
    return jsonify({"url": f"/api/image/{fname}", "filename": fname})


@app.route("/api/image/<path:filename>")
def serve_image(filename):
    return send_from_directory(UPLOAD_DIR, filename)


# ── Startup ───────────────────────────────────────────────────────────────────

nexus_cache.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
