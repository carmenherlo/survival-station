#!/usr/bin/env bash
# =============================================================================
#  SURVIVAL STATION — Setup Script
#  Ubuntu Server 24.04 LTS · NetworkManager · Docker
#
#  Execution order guaranteed:
#    1. System update + dependencies
#    2. Docker install
#    3. NetworkManager hotspot (wlp2s0)
#    4. Systemd service: Docker Compose after hotspot is up
#
#  Usage:
#    chmod +x survival-station-setup.sh
#    sudo ./survival-station-setup.sh
# =============================================================================

set -euo pipefail

cat << 'EOF'
   _____ __  ______ _    _______    _____    __ 
  / ___// / / / __ \ |  / /  _/ |  / /   |  / /
  \__ \/ / / / /_/ / | / // / | | / / /| | / / 
 ___/ / /_/ / _, _/| |/ // /  | |/ / ___ |/ /___
/____/\____/_/ |_| |___/___/  |___/_/  |_/_____/
   ______________  ______________  _   __
  / ___/_  __/   |/_  __/  _/ __ \/ | / /
  \__ \ / / / /| | / /  / // / / /  |/ / 
 ___/ // / / ___ |/ / _/ // /_/ / /|  /  
/____//_/ /_/  |_/_/ /___/\____/_/ |_/
   
EOF

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

log()  { echo -e "${GREEN}[✔]${RESET} $*"; }
info() { echo -e "${CYAN}[→]${RESET} $*"; }
warn() { echo -e "${YELLOW}[!]${RESET} $*"; }
err()  { echo -e "${RED}[✘]${RESET} $*" >&2; exit 1; }
sep()  { echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"; }

# ── Config ────────────────────────────────────────────────────────────────────
HOTSPOT_SSID="${HOTSPOT_SSID:-Survival-Net}"
HOTSPOT_PASSWORD="${HOTSPOT_PASSWORD:-survival2026}"   # min 8 chars
HOTSPOT_IP="${HOTSPOT_IP:-10.42.0.1}"
HOTSPOT_IFACE="${HOTSPOT_IFACE:-}"                     # auto-detected if empty
LAN_IFACE="${LAN_IFACE:-}"                             # auto-detected if empty
COMPOSE_DIR="${COMPOSE_DIR:-/opt/survival-station}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
SERVICE_USER="${SUDO_USER:-survival}"

# ── Root check ────────────────────────────────────────────────────────────────
[[ $EUID -ne 0 ]] && err "Run as root:  sudo ./survival-station-setup.sh"

sep
echo -e "${BOLD}  SURVIVAL STATION — Automated Setup${RESET}"
echo -e "  SSID: ${YELLOW}${HOTSPOT_SSID}${RESET}  ·  IP: ${YELLOW}${HOTSPOT_IP}${RESET}"
sep

# =============================================================================
# STEP 1 — System update + core packages
# =============================================================================
sep; info "STEP 1 · System update & core packages"

apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq \
    curl git net-tools iw wireless-tools \
    network-manager \
    ca-certificates gnupg lsb-release \
    python3-pip

log "System packages installed"

# Auto-detect timezone from IP, fallback to UTC
DETECTED_TZ=$(curl -fsSL --max-time 5 https://ipapi.co/timezone 2>/dev/null || echo "UTC")
timedatectl set-timezone "$DETECTED_TZ"
systemctl enable --now systemd-timesyncd
timedatectl set-ntp true
log "Timezone set to ${DETECTED_TZ}, NTP sync enabled"

# Ensure NetworkManager is running and managing interfaces
systemctl enable --now NetworkManager
log "NetworkManager enabled"

# =============================================================================
# STEP 2 — Auto-detect interfaces
# =============================================================================
sep; info "STEP 2 · Detecting network interfaces"

# Detect WiFi interface (first one supporting AP mode)
detect_wifi_iface() {
    for iface in $(iw dev 2>/dev/null | awk '/Interface/{print $2}'); do
        if iw phy "$(iw dev "$iface" info 2>/dev/null | awk '/wiphy/{print "phy"$2}')" \
            info 2>/dev/null | grep -q "AP"; then
            echo "$iface"; return 0
        fi
    done
    # Fallback: first wl* interface
    ip link show | awk -F': ' '/wl/{print $2; exit}'
}

# Detect LAN interface (first en* or eth*)
detect_lan_iface() {
    ip link show | awk -F': ' '/^[0-9]+: (en|eth)/{print $2; exit}'
}

if [[ -z "$HOTSPOT_IFACE" ]]; then
    HOTSPOT_IFACE="$(detect_wifi_iface)"
    [[ -z "$HOTSPOT_IFACE" ]] && err "No WiFi interface found. Check 'iw dev'."
fi

if [[ -z "$LAN_IFACE" ]]; then
    LAN_IFACE="$(detect_lan_iface)"
    [[ -z "$LAN_IFACE" ]] && warn "No LAN interface detected — offline-only mode"
fi

log "WiFi interface : ${HOTSPOT_IFACE}"
log "LAN interface  : ${LAN_IFACE:-none}"

# =============================================================================
# STEP 3 — Docker install (official repo)
# =============================================================================
sep; info "STEP 3 · Installing Docker"

if command -v docker &>/dev/null; then
    warn "Docker already installed — skipping"
else
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg

    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
      https://download.docker.com/linux/ubuntu \
      $(lsb_release -cs) stable" \
      > /etc/apt/sources.list.d/docker.list

    apt-get update -qq
    apt-get install -y -qq \
        docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin

    log "Docker installed"
fi

# Add service user to docker group
usermod -aG docker "$SERVICE_USER" 2>/dev/null || true
log "User '${SERVICE_USER}' added to docker group"

# Enable Docker but do NOT start yet — hotspot must come first
systemctl enable docker
log "Docker service enabled (will start after hotspot)"

# =============================================================================
# STEP 4 — NetworkManager hotspot via nmcli
# =============================================================================
sep; info "STEP 4 · Configuring WiFi hotspot via NetworkManager"

NM_CON_NAME="survival-hotspot"

# Remove existing connection if present
if nmcli connection show "$NM_CON_NAME" &>/dev/null; then
    warn "Removing existing '${NM_CON_NAME}' connection"
    nmcli connection delete "$NM_CON_NAME"
fi

# Create the hotspot connection
nmcli connection add \
    type wifi \
    ifname "$HOTSPOT_IFACE" \
    con-name "$NM_CON_NAME" \
    autoconnect yes \
    ssid "$HOTSPOT_SSID" \
    -- \
    wifi.mode ap \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.psk "$HOTSPOT_PASSWORD" \
    ipv4.method shared \
    ipv4.addresses "${HOTSPOT_IP}/24" \
    ipv6.method disabled \
    connection.autoconnect-priority 100

log "NetworkManager hotspot profile created"

# Bring it up now (may fail if no radio — service will handle boot)
if nmcli connection up "$NM_CON_NAME" 2>/dev/null; then
    log "Hotspot '${HOTSPOT_SSID}' is UP at ${HOTSPOT_IP}"
else
    warn "Could not bring hotspot up right now — will activate on next boot"
fi

# =============================================================================
# STEP 5 — IP forwarding (for LAN → WiFi routing, optional)
# =============================================================================
sep; info "STEP 5 · IP forwarding"

SYSCTL_CONF=/etc/sysctl.d/99-survival-forward.conf
cat > "$SYSCTL_CONF" <<'EOF'
# Survival Station — IP forwarding
net.ipv4.ip_forward = 1
EOF

sysctl -p "$SYSCTL_CONF" -q
log "IPv4 forwarding enabled"

# =============================================================================
# STEP 6 — Systemd service: wait for hotspot, then start Docker Compose
# =============================================================================
sep; info "STEP 6 · Creating systemd service for Docker Compose"

# Build After= dependency string
AFTER_UNITS="network-online.target docker.service"
if [[ -n "$LAN_IFACE" ]]; then
    AFTER_UNITS="sys-subsystem-net-devices-${LAN_IFACE}.device ${AFTER_UNITS}"
fi

cat > /etc/systemd/system/survival-station.service <<EOF
[Unit]
Description=Survival Station — Docker Compose stack
Documentation=https://github.com/survival-station
Requires=docker.service NetworkManager-wait-online.service
After=docker.service NetworkManager-wait-online.service network-online.target
Wants=network-online.target

# Wait for the hotspot interface to have its IP before starting
After=NetworkManager-wait-online.service

[Service]
Type=forking
User=root
WorkingDirectory=${COMPOSE_DIR}
RemainAfterExit=yes

# Wait until hotspot IP is reachable (max 60s)
ExecStartPre=/bin/bash -c '\
  for i in \$(seq 1 60); do \
    ip addr show ${HOTSPOT_IFACE} 2>/dev/null | grep -q "${HOTSPOT_IP}" && break; \
    echo "Waiting for hotspot... \$i/60"; sleep 1; \
  done; \
  echo "Hotspot up, waiting 20s for stability..."; sleep 20'

ExecStart=/usr/bin/docker compose -f ${COMPOSE_DIR}/${COMPOSE_FILE} up -d --remove-orphans
ExecStop=/usr/bin/docker compose -f ${COMPOSE_DIR}/${COMPOSE_FILE} down
ExecReload=/usr/bin/docker compose -f ${COMPOSE_DIR}/${COMPOSE_FILE} pull
Restart=on-failure
RestartSec=15

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable survival-station.service
log "survival-station.service enabled"

# =============================================================================
# STEP 7 — Create compose directory + example docker-compose.yml if missing
# =============================================================================
sep; info "STEP 7 · Checking Docker Compose directory"

mkdir -p "$COMPOSE_DIR"

# Check if repo was cloned alongside the script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$SCRIPT_DIR" != "$COMPOSE_DIR" ]]; then
    info "Copying repo contents to ${COMPOSE_DIR}"
    cp -r "${SCRIPT_DIR}/." "${COMPOSE_DIR}/"
    log "Repo copied to ${COMPOSE_DIR}"
    # Remove source directory to avoid duplication
    if [[ "$SCRIPT_DIR" == "$HOME/survival-station" || "$SCRIPT_DIR" == "/home/${SERVICE_USER}/survival-station" ]]; then
        rm -rf "$SCRIPT_DIR"
        log "Source directory removed"
    fi
fi

if [[ ! -f "${COMPOSE_DIR}/${COMPOSE_FILE}" ]]; then
    warn "No docker-compose.yml found — creating example template"
    cat > "${COMPOSE_DIR}/${COMPOSE_FILE}" <<'YAML'
# Survival Station — Docker Compose
# Place your actual services here.
# This file is at: /opt/survival-station/docker-compose.yml

services:

  ollama:
    image: ollama/ollama:latest
    container_name: ollama
    restart: unless-stopped
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama

  rag-api:
    image: ghcr.io/survival-station/rag-api:latest  # replace with your image
    container_name: rag-api
    restart: unless-stopped
    ports:
      - "8000:8000"
    depends_on:
      - ollama
    environment:
      - OLLAMA_HOST=http://ollama:11434

  tileserver:
    image: maptiler/tileserver-gl:latest
    container_name: tileserver
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - ./tiles:/data

  kiwix:
    image: ghcr.io/kiwix/kiwix-serve:latest
    container_name: kiwix
    restart: unless-stopped
    ports:
      - "8888:8080"
    volumes:
      - ./zim:/data

  pwa:
    image: nginx:alpine
    container_name: pwa
    restart: unless-stopped
    ports:
      - "80:80"
    volumes:
      - ./pwa:/usr/share/nginx/html:ro

volumes:
  ollama_data:
YAML
    log "Example docker-compose.yml written to ${COMPOSE_DIR}/${COMPOSE_FILE}"
else
    log "Existing docker-compose.yml found — not overwritten"
fi

# =============================================================================
# STEP 8 — Build image, start stack, pull models
# =============================================================================
sep; info "STEP 8 · Building RAG image"

cd "$COMPOSE_DIR"

if [[ -f "Dockerfile" ]]; then
    docker build -t survival-station-rag:latest . \
        || err "Docker build failed — check Dockerfile"
    log "survival-station-rag image built"
else
    warn "No Dockerfile found in ${COMPOSE_DIR} — skipping build"
fi

sep; info "STEP 8 · Starting Docker Compose stack"

docker compose -f "$COMPOSE_FILE" up -d --remove-orphans \
    || err "docker compose up failed — check ${COMPOSE_DIR}/${COMPOSE_FILE}"
log "Docker Compose stack started"

sep; info "STEP 8 · Pulling Ollama models (this may take a while)"

# Wait for ollama container to be ready (max 60s)
for i in $(seq 1 60); do
    docker exec ollama ollama list &>/dev/null && break
    echo "  Waiting for ollama... $i/60"
    sleep 1
done

docker exec ollama ollama pull phi3:mini \
    || warn "Failed to pull phi3:mini — run manually: docker exec ollama ollama pull phi3:mini"
log "phi3:mini pulled"

docker exec ollama ollama pull nomic-embed-text \
    || warn "Failed to pull nomic-embed-text — run manually: docker exec ollama ollama pull nomic-embed-text"
log "nomic-embed-text pulled"

# =============================================================================
# STEP 8b — Download ZIM file (offline Wikipedia)
# =============================================================================
sep; info "STEP 8b · Downloading offline Wikipedia (WikiMed ~155MB)"

ZIM_DIR="${COMPOSE_DIR}/data/wiki"
ZIM_FILE="${ZIM_DIR}/wikipedia_en_medicine_mini_2026-01.zim"
ZIM_URL="https://download.kiwix.org/zim/wikipedia/wikipedia_en_medicine_mini_2026-01.zim"

mkdir -p "$ZIM_DIR"

if [[ -f "$ZIM_FILE" ]]; then
    warn "ZIM file already exists — skipping download"
else
    wget -q --show-progress -O "$ZIM_FILE" "$ZIM_URL" \
        || warn "ZIM download failed — run manually: wget -O ${ZIM_FILE} ${ZIM_URL}"
    log "WikiMed ZIM downloaded"
fi

# =============================================================================
# STEP 8c — Download translation models (LibreTranslate / Argos)
# =============================================================================
sep; info "STEP 8c · Downloading translation language models"

mkdir -p "${COMPOSE_DIR}/data/translate"
chown -R 1032:1032 "${COMPOSE_DIR}/data/translate"

PACKAGES=$(jq -r '.packages[]' "${COMPOSE_DIR}/config/languages.json")

for pkg in $PACKAGES; do
  info "  Installing $pkg..."
  docker run --rm \
    -v "${COMPOSE_DIR}/data/translate:/home/libretranslate/.local/share/argos-translate" \
    --entrypoint /app/venv/bin/argospm \
    libretranslate/libretranslate:latest \
    install "$pkg"
done

log "Translation models downloaded"

# =============================================================================
# STEP 9 — Run service checks
# =============================================================================
sep; info "STEP 9 · Running service checks"

TEST_SCRIPT="${COMPOSE_DIR}/tests/check-services.sh"
if [[ -f "$TEST_SCRIPT" ]]; then
    chmod +x "$TEST_SCRIPT"
    bash "$TEST_SCRIPT" 2>&1 | tee "${COMPOSE_DIR}/check-services.log"
    log "Check results saved to ${COMPOSE_DIR}/check-services.log"
else
    warn "No check-services.sh script found in ${COMPOSE_DIR}/tests/ — skipping"
fi

# =============================================================================
# Done
# =============================================================================
sep
echo -e "${BOLD}${GREEN}  SURVIVAL STATION — Setup complete${RESET}"
sep
echo -e "  Hotspot SSID  : ${YELLOW}${HOTSPOT_SSID}${RESET}"
echo -e "  Password      : ${YELLOW}${HOTSPOT_PASSWORD}${RESET}"
echo -e "  Hotspot IP    : ${YELLOW}${HOTSPOT_IP}${RESET}"
echo -e "  Compose dir   : ${CYAN}${COMPOSE_DIR}${RESET}"
echo ""
echo -e "  Services will be available at:"
echo -e "    ${CYAN}http://${HOTSPOT_IP}${RESET}        → PWA"
echo -e "    ${CYAN}http://${HOTSPOT_IP}:8000${RESET}   → RAG API"
echo -e "    ${CYAN}http://${HOTSPOT_IP}:8080${RESET}   → Tileserver"
echo -e "    ${CYAN}http://${HOTSPOT_IP}:8888${RESET}   → Kiwix (Wikipedia)"
echo -e "    ${CYAN}http://${HOTSPOT_IP}:11434${RESET}  → Ollama"
echo ""
echo -e "  ${YELLOW}Next steps:${RESET}"
echo -e "   1. Edit ${COMPOSE_DIR}/${COMPOSE_FILE} with your real images"
echo -e "   2. sudo systemctl start survival-station"
echo -e "   3. Reboot to validate full headless cold-start"
echo -e "   4. sudo journalctl -fu survival-station  ← follow logs"
sep
