 #!/usr/bin/env bash
# ==============================================================================
# Production Installation Script for DNS Tunnel Messenger Server
# Target OS: Ubuntu 20.04 / 22.04 / 24.04 LTS
# Designed for coexistence with MasterDNS / BIND9 / systemd-resolved
# ==============================================================================

set -euo pipefail

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

INSTALL_DIR="/opt/dns-messenger"
SERVICE_NAME="dns-messenger"
APP_USER="dnsmsg"

echo -e "${CYAN}==================================================================${NC}"
echo -e "${CYAN}       DNS Tunnel Messenger - Ubuntu Production Installer        ${NC}"
echo -e "${CYAN}==================================================================${NC}"

# 1. Root Verification
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}[ERROR] This installer must be run as root or with sudo.${NC}"
   exit 1
fi

# 2. Check Ubuntu OS
if [ -f /etc/os-release ]; then
    . /etc/os-release
    if [[ "$ID" != "ubuntu" && "$ID_LIKE" != *"ubuntu"* && "$ID" != "debian" ]]; then
        echo -e "${YELLOW}[WARNING] Detected OS is $PRETTY_NAME. This script is optimized for Ubuntu/Debian.${NC}"
    else
        echo -e "${GREEN}[OK] Operating System: $PRETTY_NAME${NC}"
    fi
fi

# 3. Detect existing DNS services on Port 53
PORT_53_STATUS="FREE"
if ss -ulnp 2>/dev/null | grep -q ":53 "; then
    PORT_53_STATUS="OCCUPIED"
    OCCUPIED_BY=$(ss -ulnp 2>/dev/null | grep ":53 " | head -n1 | awk '{print $NF}')
    echo -e "${YELLOW}[NOTICE] Port 53 UDP is currently in use by: ${OCCUPIED_BY}${NC}"
fi

# 4. Interactive Configuration (or use environment variables)
echo -e "\n${BLUE}--- Configuration Settings ---${NC}"

if [[ -z "${BASE_DOMAIN:-}" ]]; then
    read -rp "Enter Base Domain for DNS Tunnel (e.g. msg.yourdomain.com): " BASE_DOMAIN
    while [[ -z "$BASE_DOMAIN" ]]; do
        echo -e "${RED}Base domain cannot be empty!${NC}"
        read -rp "Enter Base Domain: " BASE_DOMAIN
    done
fi

DEFAULT_PORT="5354"
if [[ "$PORT_53_STATUS" == "FREE" ]]; then
    DEFAULT_PORT="53"
fi

if [[ -z "${LISTEN_PORT:-}" ]]; then
    read -rp "Enter Server Listen Port [default: $DEFAULT_PORT]: " LISTEN_PORT
    LISTEN_PORT="${LISTEN_PORT:-$DEFAULT_PORT}"
fi

DEFAULT_UPSTREAM=""
if [[ "$LISTEN_PORT" == "53" ]]; then
    DEFAULT_UPSTREAM="8.8.8.8:53"
else
    # If MasterDNS is running on port 53, fallback to local 53
    DEFAULT_UPSTREAM="127.0.0.1:53"
fi

if [[ -z "${FORWARD_UPSTREAM:-}" ]]; then
    read -rp "Enter Forward Upstream DNS (for MasterDNS coexistence / non-tunnel queries) [default: $DEFAULT_UPSTREAM]: " FORWARD_UPSTREAM
    FORWARD_UPSTREAM="${FORWARD_UPSTREAM:-$DEFAULT_UPSTREAM}"
fi

MAX_FILE_MB="${MAX_FILE_MB:-20}"

echo -e "\n${CYAN}Summary Configuration:${NC}"
echo -e "  - Base Domain       : ${GREEN}${BASE_DOMAIN}${NC}"
echo -e "  - Server Port       : ${GREEN}${LISTEN_PORT}${NC}"
echo -e "  - Forward Upstream  : ${GREEN}${FORWARD_UPSTREAM:-None}${NC}"
echo -e "  - Max File Size     : ${GREEN}${MAX_FILE_MB} MB${NC}"
echo -e "  - Install Path      : ${GREEN}${INSTALL_DIR}${NC}\n"

# 5. Handle systemd-resolved conflict if user selected port 53 and resolved is running
if [[ "$LISTEN_PORT" == "53" && "$PORT_53_STATUS" == "OCCUPIED" ]]; then
    if systemctl is-active --quiet systemd-resolved 2>/dev/null; then
        echo -e "${YELLOW}[CONFIG] systemd-resolved is active on port 53. Disabling DNSStubListener...${NC}"
        mkdir -p /etc/systemd/resolved.conf.d/
        cat > /etc/systemd/resolved.conf.d/disable-stub.conf << 'EOF'
[Resolve]
DNSStubListener=no
EOF
        systemctl restart systemd-resolved || true
        # Keep resolv.conf pointing to real upstream or 8.8.8.8 so server doesn't lose internet
        if [ -L /etc/resolv.conf ]; then
            rm -f /etc/resolv.conf
            echo "nameserver 1.1.1.1" > /etc/resolv.conf
            echo "nameserver 8.8.8.8" >> /etc/resolv.conf
        fi
        echo -e "${GREEN}[OK] Port 53 freed for DNS Tunnel Server.${NC}"
    fi
fi

# 6. Install System Dependencies
echo -e "${BLUE}[1/6] Updating APT repositories and installing prerequisites...${NC}"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git dnsutils ufw iptables curl >/dev/null

# 7. Create Dedicated User & Directory
echo -e "${BLUE}[2/6] Setting up application directory and dedicated user '${APP_USER}'...${NC}"
if ! id -u "$APP_USER" >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$APP_USER"
fi

mkdir -p "$INSTALL_DIR"
SCRIPT_SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Copy files from source repository
cp -r "${SCRIPT_SOURCE_DIR}/dns_messenger" "$INSTALL_DIR/"
cp "${SCRIPT_SOURCE_DIR}/run.py" "$INSTALL_DIR/"
cp "${SCRIPT_SOURCE_DIR}/requirements.txt" "$INSTALL_DIR/"
cp "${SCRIPT_SOURCE_DIR}/resolvers.txt" "$INSTALL_DIR/"

mkdir -p "$INSTALL_DIR/data"

# 8. Setup Python Virtual Environment
echo -e "${BLUE}[3/6] Building Python virtual environment and installing dependencies...${NC}"
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip -q
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -q

# 9. Write Production Server Configuration
echo -e "${BLUE}[4/6] Generating server configuration file...${NC}"
cat > "$INSTALL_DIR/data/config.json" << EOF
{
  "base_domain": "${BASE_DOMAIN}",
  "server_listen_host": "0.0.0.0",
  "server_listen_port": ${LISTEN_PORT},
  "forward_dns_upstream": "${FORWARD_UPSTREAM}",
  "max_file_size_mb": ${MAX_FILE_MB},
  "chunk_size_bytes": 110,
  "web_host": "127.0.0.1",
  "web_port": 8080,
  "server_admin_host": "0.0.0.0",
  "server_admin_port": 8081,
  "kurigram_tracker": {
    "enabled": false,
    "api_id": 0,
    "api_hash": "",
    "session_string": "",
    "channels_map": []
  }
}
EOF

# Set proper permissions
chown -R "$APP_USER:$APP_USER" "$INSTALL_DIR"
chmod -R 750 "$INSTALL_DIR"
chmod -R 770 "$INSTALL_DIR/data"

# 10. Install and Enable Systemd Service
echo -e "${BLUE}[5/6] Creating systemd service '${SERVICE_NAME}.service'...${NC}"
cat > "/etc/systemd/system/${SERVICE_NAME}.service" << EOF
[Unit]
Description=DNS Tunnel Messenger Authoritative Server
After=network.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/venv/bin/python ${INSTALL_DIR}/run.py server
Restart=always
RestartSec=3
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
LimitNOFILE=65535
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

# 11. Firewall Configuration (UFW)
if ufw status | grep -q "Status: active"; then
    echo -e "${BLUE}[6/6] Allowing UDP port ${LISTEN_PORT} and Admin Web Port 8081 in UFW firewall...${NC}"
    ufw allow "${LISTEN_PORT}/udp" comment "DNS Messenger UDP" >/dev/null || true
    ufw allow "8081/tcp" comment "DNS Messenger Admin Web" >/dev/null || true
fi

# 12. Verification & Self-Test
sleep 2
echo -e "\n${CYAN}==================================================================${NC}"
if systemctl is-active --quiet "${SERVICE_NAME}"; then
    echo -e "${GREEN}  SUCCESS: DNS Messenger Server is ACTIVE and RUNNING!${NC}"
else
    echo -e "${RED}  ERROR: Service failed to start. Showing logs:${NC}"
    journalctl -u "${SERVICE_NAME}" -n 20 --no-pager
    exit 1
fi

PUBLIC_IP=$(curl -s -4 ifconfig.me || curl -s -4 icanhazip.com || echo "YOUR_SERVER_IP")

echo -e "${CYAN}==================================================================${NC}"
echo -e "${GREEN}Server Web Admin Dashboard: http://${PUBLIC_IP}:8081${NC}"
echo -e "  (Open in your browser to monitor stats, login to Telegram Kurigram bot & map channels)"
echo -e "${CYAN}==================================================================${NC}"
echo -e "${YELLOW}Next Steps - Configure DNS Records on Cloudflare or Domain Registrar:${NC}"
echo -e "  1. Create an ${GREEN}A Record${NC}:"
echo -e "     Name:  ${CYAN}ns1.${BASE_DOMAIN}${NC}"
echo -e "     IPv4:  ${CYAN}${PUBLIC_IP}${NC}"
echo -e "     Proxy: ${RED}DNS Only (Grey Cloud / No Proxy)${NC}"
echo -e ""
echo -e "  2. Create an ${GREEN}NS Record${NC}:"
echo -e "     Name:  ${CYAN}${BASE_DOMAIN}${NC}"
echo -e "     Host:  ${CYAN}ns1.${BASE_DOMAIN}${NC}"
echo -e ""
echo -e "${YELLOW}Coexistence with MasterDNS:${NC}"
if [[ "$LISTEN_PORT" != "53" ]]; then
    echo -e "  - MasterDNS continues running on port 53."
    echo -e "  - DNS Messenger is listening on port ${LISTEN_PORT}."
    echo -e "  - If using MasterDNS forwarding: forward '${BASE_DOMAIN}' queries to 127.0.0.1:${LISTEN_PORT}."
else
    echo -e "  - DNS Messenger is handling port 53 directly."
    echo -e "  - MasterDNS or non-tunnel queries are transparently forwarded to: ${FORWARD_UPSTREAM}."
fi
echo -e ""
echo -e "${YELLOW}Service Management Commands:${NC}"
echo -e "  - Status  : ${CYAN}systemctl status ${SERVICE_NAME}${NC}"
echo -e "  - Logs    : ${CYAN}journalctl -u ${SERVICE_NAME} -f${NC}"
echo -e "  - Restart : ${CYAN}systemctl restart ${SERVICE_NAME}${NC}"
echo -e "${CYAN}==================================================================${NC}\n"
