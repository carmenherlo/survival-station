# Survival Station

> Portable offline-first server infrastructure — AI, maps, and knowledge accessible without internet.

Survival Station is a self-contained server built on a Mini PC running Ubuntu Server. It creates its own WiFi hotspot and serves a full stack of offline services: local LLM inference, semantic search (RAG), vector tile maps, and Wikipedia — all reachable from any browser at `http://10.42.0.1`.

No cloud. No connectivity required. Everything runs locally.

---

## Motivation

Most AI and knowledge tools assume a reliable internet connection. Survival Station removes that assumption. The use cases range from field operations and remote environments to privacy-sensitive deployments and off-grid scenarios.

---

## Architecture

```
                        ┌─────────────────────────────────────────┐
                        │           Mini PC — Ubuntu Server       │
                        │                                         │
                        │  ┌──────────────────────────────────┐   │
                        │  │         Docker Compose           │   │
                        │  │                                  │   │
                        │  │  ollama      :11434              │   │
                        │  │  rag-api     :8000               │   │
                        │  │  tileserver  :8080               │   │
                        │  │  kiwix       :8888               │   │
                        │  │  pwa (nginx) :80                 │   │
                        │  └──────────────────────────────────┘   │
                        │                                         │
                        │  wlp2s0 → hotspot "Survival-Net"        │
                        │           10.42.0.1                     │
                        └──────────────────┬──────────────────────┘
                                           │ WiFi
                              ┌────────────┴───────────┐
                              │                        │
                          Mobile                  Laptop
                      http://10.42.0.1          http://10.42.0.1
```

The server receives internet via ethernet and emits a dedicated WiFi hotspot (`Survival-Net`). When operating fully offline, all services remain available through the hotspot. Internet connectivity is optional — used only when updating map tiles, ZIM content, or language models.

---

## Hardware

Tested on a mini PC with the following specs:

| Component | Details |
|---|---|
| **CPU** | Intel N150 |
| **RAM** | 16GB |
| **Power consumption** | ~8W under load |
| **Price** | ~168€ |

The low power draw makes it suitable for field deployment powered by foldable solar panels or battery packs. No dedicated GPU required — the LLM runs entirely on CPU.

---

## Usage

Once connected to the `Survival-Net` hotspot, open a browser and navigate to `http://10.42.0.1`.

| What you want | Where to go |
|---|---|
| Ask a survival question (RAG) | `http://10.42.0.1` → main interface |
| Browse offline maps | `http://10.42.0.1:8080` |
| Search Wikipedia offline | `http://10.42.0.1:8888` |
| Check LLM status | `http://10.42.0.1:11434` |

The PWA can be installed on any mobile device directly from the browser (Add to Home Screen).


---

## Installation

Requires a Mini PC with Ubuntu Server 24.04 LTS and an ethernet connection for the initial setup.

Clone the repo and run the setup script:

```bash
git clone https://github.com/carmenherlo/survival-station
cd survival-station
sudo bash setup.sh
```

The script handles everything automatically:
- System update and dependencies
- Docker and Docker Compose
- WiFi hotspot via NetworkManager (`Survival-Net` · `10.42.0.1`)
- Systemd service with guaranteed boot order (hotspot → Docker)
- RAG image build, container startup, and model download
- Service validation

After setup, disconnect ethernet and reboot. The hotspot and all services start automatically.

**Offline Wikipedia (ZIM file):** not included in the repo due to file size. Downloaded automatically by `setup.sh` (~155MB).

**To change the hotspot password** (connect via SSH first, default password is `survival2026`):
```bash
nmcli connection modify survival-hotspot wifi-sec.psk "yournewpassword"
nmcli connection up survival-hotspot
```

---

## Prototype scope

This is a working prototype intended for testing and demonstration. It includes:

- **LLM + RAG** — `phi3:mini` model with a small survival knowledge base (`water`, `fire`, `shelter`, `first aid`, `food`). Models are downloaded automatically by `setup.sh`.
- **Offline maps** — a sample map of Zurich, Switzerland (included in the repo, ~23MB)
- **Offline Wikipedia** — WikiMed medical encyclopedia in English (not included, ~155MB — see download instructions above)

The knowledge base is intentionally minimal. The architecture is designed to scale with additional documents, maps, and ZIM files.

---

## Services

| Service | Port | Description |
|---|---|---|
| **Ollama** | 11434 | Local LLM inference — `phi3:mini` for generation, `nomic-embed-text` for embeddings |
| **RAG API** | 8000 | FastAPI service — FAISS vector index + Ollama for semantic search and retrieval-augmented generation |
| **Tileserver** | 8080 | Offline vector tile maps via `maptiler/tileserver-gl` |
| **Kiwix** | 8888 | Wikipedia offline (ZIM format) |
| **PWA** | 80 | Progressive Web App frontend served by nginx, accessible from any browser |

---

## Network Configuration

The hotspot is managed by NetworkManager and configured to autostart on boot with high priority. Hotspot address is always `10.42.0.1`.

---

## Stack

- **OS**: Ubuntu Server 24.04 LTS
- **Runtime**: Docker + Docker Compose
- **LLM**: [Ollama](https://ollama.com) — `phi3:mini`, `nomic-embed-text`
- **Vector search**: FAISS
- **API**: FastAPI (Python)
- **Maps**: maptiler/tileserver-gl + MBTiles
- **Offline content**: Kiwix
- **Frontend**: nginx + PWA (installable from any browser)
- **Network**: NetworkManager hotspot on wlp2s0

---

## Status

Functional. Validated headless cold-start without ethernet.

---

## Access

| Context | Address |
|---|---|
| Over LAN (ethernet) | `ssh survival@<lan-ip>` |
| From hotspot | `http://10.42.0.1` |

---

## Roadmap

**Core**
- [x] Fix boot ordering — Docker after hotspot, reliable headless startup without ethernet
- [ ] Multi-user chat — local messaging between connected devices, no internet required
- [ ] Field validation — response quality from LLM + RAG on real survival queries, UX fluidity from hotspot-connected mobile, actual power consumption vs estimates, and behaviour under concurrent users

**Communications**
- [ ] LoRa / Meshtastic integration — mesh messaging without internet or GSM
- [ ] SDR support — receive FM, emergency broadcasts, weather satellites (NOAA), ADS-B, AIS
- [ ] APRS — amateur radio data network, fully offline *(amateur radio license required under normal conditions; emergency use may be exempt depending on local regulations)*

**Positioning**
- [ ] GNSS receiver (USB/serial) — offline GPS combined with local tileserver for fully autonomous navigation
- [ ] gpsd integration — standard GPS daemon exposing position data to all services
- [ ] GPS-denied navigation — in the event of absent, jammed, degraded, or spoofed GNSS signal (as observed in several recent conflict scenarios), positioning and tracking via dead reckoning and sensor fusion; implemented on `survival-go`, a companion portable device whose form factor makes it suitable for inertial navigation algorithms, route tracking, and on-the-fly map generation for areas not downloaded prior to deployment in offline and demanding environments

**Environment**
- [ ] Local weather station — temperature, pressure, humidity sensors; local forecasting without internet
- [ ] RTL-SDR weather satellite imagery — real NOAA satellite images, no subscription

**Power**
- [ ] Battery/solar monitoring — track available autonomy in field deployments
- [ ] UPS integration — graceful shutdown and power state awareness

**Long-range**
- [ ] Satellite messenger integration — last-resort alerting via Garmin inReach API when all other comms fail

---

## License

[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) — free to use and adapt with attribution. Commercial use requires explicit written permission from the author.