import os
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

CONFIG_FILE = DATA_DIR / "config.json"
RESOLVERS_FILE = BASE_DIR / "resolvers.txt"

DEFAULT_RESOLVERS = [
    "1.1.1.1:53",
    "8.8.8.8:53",
    "9.9.9.9:53",
    "77.88.8.8:53",
    "4.2.2.4:53",
    "208.67.222.222:53",
    "185.228.168.9:53",
    "94.140.14.14:53",
    "10.202.10.202:53",
    "10.202.10.102:53"
]

def load_resolvers() -> list:
    """Load DNS resolvers from resolvers.txt supporting 'IP:PORT' or 'IP' format."""
    target_path = RESOLVERS_FILE if RESOLVERS_FILE.exists() else (DATA_DIR / "resolvers.txt")
    if not target_path.exists():
        save_resolvers(DEFAULT_RESOLVERS)
        return DEFAULT_RESOLVERS.copy()

    resolvers = []
    try:
        with open(target_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("//"):
                    continue
                if ":" in line:
                    parts = line.split(":", 1)
                    ip = parts[0].strip()
                    try:
                        port = int(parts[1].strip())
                    except ValueError:
                        port = 53
                    resolvers.append(f"{ip}:{port}")
                else:
                    resolvers.append(f"{line}:53")
    except Exception:
        return DEFAULT_RESOLVERS.copy()

    return resolvers if resolvers else DEFAULT_RESOLVERS.copy()

def save_resolvers(resolvers: list) -> None:
    """Save resolvers list to resolvers.txt in IP:PORT format."""
    target_path = RESOLVERS_FILE
    content = [
        "# =================================================================",
        "# DNS Resolvers List for DNS Tunnel Messenger",
        "# Supported formats: IP or IP:PORT (default port: 53)",
        "# Empty lines and lines starting with '#' are ignored",
        "# =================================================================",
        ""
    ]
    for r in resolvers:
        r = r.strip()
        if not r:
            continue
        if ":" not in r:
            content.append(f"{r}:53")
        else:
            content.append(r)

    with open(target_path, "w", encoding="utf-8") as f:
        f.write("\n".join(content) + "\n")

DEFAULT_CONFIG = {
    # DNS Tunnel Settings
    "base_domain": "msg.example.com",
    "dns_resolvers": [
        "8.8.8.8",
        "1.1.1.1",
        "9.9.9.9",
        "77.88.8.8",
        "4.2.2.4",
        "208.67.222.222"
    ],
    "dns_query_timeout": 2.5,
    "max_retries": 4,
    
    # Server Coexistence & Network
    "server_listen_host": "0.0.0.0",
    "server_listen_port": 5354,  # Use 53 if standalone, or 5354 to coexist with MasterDNS
    "forward_dns_upstream": "127.0.0.1:53",  # Transparent forward for MasterDNS queries if port 53 is used
    
    # Storage and History Settings (Disk-backed with 100-message limit per channel)
    "channel_history_limit": 100,  # Automatically retain the last 100 messages per channel on disk
    "max_file_size_mb": 20,
    "chunk_size_bytes": 110,  # Optimal for base32 labels within 253-byte DNS domain
    
    # Web UI Settings (Client)
    "web_host": "127.0.0.1",
    "web_port": 8080,

    # Server Admin Panel Settings (Server VPS)
    "server_admin_host": "0.0.0.0",
    "server_admin_port": 8081,
    
    # Kurigram Telegram Self-Bot Tracker Settings
    "kurigram_tracker": {
        "enabled": False,
        "api_id": 0,
        "api_hash": "",
        "session_string": "",
        "channels_map": [
            # {"tg_channel": "@example_channel", "messenger_channel": "tech_news", "password": "securepassword"}
        ]
    }
}

def load_config() -> dict:
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            # merge defaults for missing keys
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
            return cfg
    except Exception:
        return DEFAULT_CONFIG.copy()

def save_config(cfg: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
