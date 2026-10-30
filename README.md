# Muse-IoT

**LLM-driven IoT platform.** The LLM provides intelligence; Muse-IoT provides the IoT operating system.

<p align="center">
  <img src="assets/banner.png" alt="Muse-IoT" width="100%">
</p>

Reads sensors → assembles context → calls LLM → validates safety → actuates. Every 60 seconds. Forever.

```
sensors → MQTT → orchestrator → [reflection engine] → [context assembler] → LLM → [safety engine] → actuators
                                       ↑  (every 6h)          ↑                         ↓
                                  history store ←─────────────────────────── decision log
```

---

## Quick start (Swiss chard garden box)

```bash
git clone https://github.com/muse-iot/muse-iot
cd muse-iot
pip install -e ".[dev]"

# Edit config — set META_API_KEY, plant and device as needed
cp config.example.yaml config.yaml
nano config.yaml

# Validate everything loads
muse-iot validate

# Start
muse-iot start
```

---

## Five plugin types — contribute any without touching core

| Plugin | What it does | Min required |
|---|---|---|
| **Sensor** | Maps a hardware sensor to an MQTT topic | `sensor.yaml` |
| **Actuator** | Maps a relay/PWM output with safety interlocks | `actuator.yaml` |
| **Device adapter** | Implements `read()` / `write()` for a hardware platform | `device.yaml` + `adapter.py` |
| **Domain** | Defines sensors, actuators, safety rules, and LLM prompt for one IoT use case | `domain.yaml` + `prompt.md` |
| **History backend** | Storage target for sensor time series and events | `backend.yaml` + `backend.py` |

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to write each type.

---

## Reference implementations

| Domain | Profile | Hardware | Status |
|---|---|---|---|
| Smart garden | Swiss chard, bok choy, Thai basil | ESP32 + MQTT | ✅ included |
| Smart home | - | ESP32 / Zigbee | ✅ included |
| Aquarium | - | ESP32 + MQTT | 🚧 planned |

---

## Hardware support

| Device | Transport | Status |
|---|---|---|
| ESP32 / ESP8266 (ESPHome or Arduino) | MQTT | ✅ included |
| Multiple ESP boards (multi-node) | MQTT | ✅ included |
| Raspberry Pi | Direct GPIO | 🚧 planned |
| Arduino | Serial / USB | 🚧 planned |
| Zigbee (via coordinator) | Zigbee2MQTT | 🚧 planned |

---

## LLM — Muse only

Muse (Meta Model API) is the only supported LLM. Get a key at
https://dev.meta.ai and set `META_API_KEY` or `llm.api_key`.

```yaml
llm:
  provider: muse
  model: muse-spark-1.3
  api_key: YOUR_META_API_KEY
```

---

## CLI

```bash
muse-iot start                        # Start poll loop
muse-iot status                       # Last LLM decision + sensor state
muse-iot history --hours 24           # Sensor history table
muse-iot observe "Leaves yellowing"   # Add observation (fed to LLM)
muse-iot analyze <session_id>         # Rootcause analysis (LLM post-mortem)
muse-iot plugins                      # List all loaded plugins
muse-iot validate                     # Check config + plugins
```

---

## Architecture

Inspired by OpenClaw's plugin system and context assembly pattern.
Extended with: hardware abstraction layer, time-series history,
physical safety engine, domain profiles, and rootcause analyzer.

See [docs/architecture.md](docs/architecture.md) for full design.

---

## License

MIT
