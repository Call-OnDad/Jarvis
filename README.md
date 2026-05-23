# AHAS -- Antony's Home Automation System

Voice-activated homelab command layer running on Proxmox.
Built on Concept-Bytes/Jarvis. Powered by Claude (Anthropic).

---

## What it does

- Wake word: **"AHAS"** or **"Jarvis"**
- Answers questions about your Proxmox host, containers, storage, services
- Starts/stops LXC containers by voice
- Feeds live system data back through Claude for intelligent responses
- British voice (Ryan Neural via edge-tts -- free, no API key needed)

---

## Quick Deploy (Proxmox LXC)

### 1. Create the container

```bash
sudo pct create 117 local:vztmpl/ubuntu-22.04-standard_22.04-1_amd64.tar.zst \
  --hostname ahas \
  --memory 1024 \
  --cores 2 \
  --rootfs local-lvm:8 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.0.60/24,gw=192.168.0.1 \
  --nameserver 192.168.0.3 \
  --features nesting=1 \
  --unprivileged 1 \
  --start 1
```

### 2. Install dependencies

```bash
apt-get update && apt-get install -y python3 python3-pip python3-venv git portaudio19-dev ffmpeg
cd /opt && git clone https://github.com/Call-OnDad/Jarvis ahas
cd ahas && python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt
```

### 3. Set your API key

```bash
cp /opt/ahas/.env.example /opt/ahas/.env
# Edit .env and set ANTHROPIC_API_KEY
```

### 4. Systemd service

```ini
[Unit]
Description=AHAS Voice Assistant
After=network.target

[Service]
WorkingDirectory=/opt/ahas
EnvironmentFile=/opt/ahas/.env
ExecStart=/opt/ahas/venv/bin/python ahas.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

`systemctl enable ahas && systemctl start ahas`

---

## File Structure

```
ahas/
|-- ahas.py          # Main loop
|-- assist.py        # Claude API + TTS
|-- tools.py         # Command dispatcher
|-- proxmox.py       # Proxmox API
|-- config.py        # All settings -- edit this
|-- requirements.txt
`-- .env.example
```

---

## Voice Commands

| You say | AHAS does |
|---------|-----------|
| "AHAS, how's the server?" | Queries Proxmox host status |
| "AHAS, list all containers" | Lists running/stopped LXCs |
| "AHAS, start container 114" | Starts WordPress CT |
| "AHAS, check disk space" | Reports storage usage |
| "AHAS, are all services up?" | HTTP checks all key services |
