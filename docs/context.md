# Project Context & Design Lineage

This document captures the full design history from the conversation that
produced this codebase. Read this to understand *why* things are the way they are.

---

## Origin story

The project started as a question: **"Can I use an LLM to control all devices
in a house by reading sensors and finding the optimal state?"**

The first concrete use case was a **smart garden box** for growing Vietnamese
vegetables (rau cải ngọt / bok choy) in Ho Chi Minh City, Vietnam.
Key constraints: outdoor temps 30–35°C, limited cooling, ESP32 + WiFi/MQTT hardware.

The hardware stack chosen: **ESP32 + MQTT + Raspberry Pi** (brain).

The core loop design: read sensors → LLM decides optimal state → actuate switches.
The LLM adds value because it carries built-in botanical knowledge — you don't need
to hardcode target ranges per plant species.

---

## Plant selection for HCMC climate

Several plants were evaluated for a smart garden box in Ho Chi Minh City's
tropical climate (30–35°C outdoor). Key finding: **most common leafy greens
struggle**, but several thrive:

**Best for HCMC (no cooling needed):**
- Rau muống (water morning glory) — native SE Asian, 20-day harvest
- Rau răm (Vietnamese coriander) — 100% tropical native
- Ngò gai (culantro) — tropical cilantro that doesn't bolt in heat
- Húng quế (Thai basil) — loves HCMC temps
- Ớt (chili pepper) — thrives at 25–35°C
- Sả (lemongrass) — zero care tropical herb

**Good with some cooling (fan + grow light replacing sunlight):**
- Swiss chard (Cải bạch) — key insight: not day-length sensitive, won't bolt
  suddenly like bok choy. Handles 28–33°C. Cut-and-come-again for 3+ months.
  Recommended varieties: Fordhook Giant, Lucullus (both heat-tolerant).
- Tatsoi — more heat-tolerant than bok choy

**Avoid in HCMC heat without heavy cooling:**
- Bok choy / rau cải ngọt — bolts above 30°C, sensitive to day length

**The project uses Swiss chard as the reference plant profile** because it's
the best demonstration of LLM value: the LLM knows that Swiss chard won't bolt
but will lose leaf quality above 33°C, and adjusts accordingly without
hardcoded rules.

---

## Why not use existing projects?

Several existing projects were evaluated:

**OpenClaw** — personal AI assistant platform. Has the right plugin architecture
and community model, but designed for chat interfaces. Its "brain" is conversational
(user messages in → text responses out), not a sensor-reading control loop.

**NullClaw, PicoClaw, ZeroClaw** — lightweight OpenClaw variants. Same problem:
designed for human-initiated conversations, not autonomous sensor polling.

**NanoClaw** — security-focused, good for home lab. Same conversation model.

**gardenailocal** — AI garden assistant using Ollama. Chat-based, not autonomous.

**Plantwatery, smart-garden (hantdev)** — ESP32 watering systems with soil
moisture sensors + MQTT. Correct hardware pattern but rule-based thresholds,
no LLM.

**FarmBot** — open source CNC farming robot. Hardware overkill for a garden box,
but software architecture worth studying for plant profile modeling.

**EMQ: ESP32 + MCP over MQTT** — closest architecture match found. LLMs directly
controlling hardware through "tool calling" over MQTT. Inspired our device adapter
design but not a complete platform.

**Conclusion:** The specific combination of LLM-as-live-decision-engine +
sensor polling loop + plugable IoT hardware doesn't exist as open source.
Muse-IoT fills this gap.

---

## The OpenClaw inheritance analysis

A deep dive into OpenClaw's architecture (from Paolo Perazzo's Substack analysis)
revealed what to inherit vs. replace:

**Inherit directly:**
- Plugin discovery (scan folder → validate YAML schema → hot-load)
- Layered prompt composition (file-based, composable sources)
- LLM adapter pattern (Muse-only in this fork — no provider swapping)
- Append-only event log for state
- SQLite as zero-config storage
- Credentials directory (0600 permissions)
- Cron/webhook triggering
- Tool policy precedence layering

**Adapt:**
- Session concept: "conversation" → "grow cycle" / "occupancy period"
- Context assembly: chat history → sensor time-series windows
- Memory compaction: conversation summary → sensor downsampling
- Multi-agent routing: different chat channels → different IoT boxes

**Replace entirely:**
- Gateway WebSocket server → MQTT bus + scheduler
- Reactive execution loop → proactive time-driven loop
- Channel adapters (WhatsApp, Telegram) → device adapters (ESP32, Pi, Arduino)

**New (no OpenClaw equivalent):**
- Hardware Abstraction Layer (IDevice)
- Time-series sensor store
- Safety engine with physical interlocks
- Domain profiles with growth phases
- Rootcause analyzer

---

## Generalization decision

A critical pivot happened mid-design: **the project was generalized from a
smart garden tool to a general-purpose IoT platform.**

The insight: a "domain" (smart garden) is structurally identical to any other
IoT domain (smart home, aquarium, industrial). Each has:
- Sensors (inputs)
- Actuators (outputs)
- Safety rules (hard constraints)
- A lifecycle / session concept
- An LLM prompt that gives it domain expertise

The garden box is just the first reference implementation.

This led to the five-plugin-type architecture:
1. **Sensors** — hardware readings (reused across domains)
2. **Actuators** — hardware outputs (reused across domains)
3. **Devices** — transport layer (ESP32, Pi, Zigbee...)
4. **Domains** — the "brain personality" for a specific IoT use case
5. **History backends** — where data lives

---

## History layer rationale

The history layer was added specifically for **rootcause analysis** — understanding
why a plant died or why a device behaved unexpectedly.

Three reasons you need history:
1. **Debugging** — when a plant dies, you need sensor readings from the 2 weeks
   before death, every LLM decision made, and every safety override. Without this,
   you're guessing.
2. **Richer live decisions** — the LLM needs 7-day sensor trends, not just current
   values. "Soil moisture declining 2% per day" is more actionable than "soil at 52%".
3. **Community dataset** — grow sessions accumulate into a community knowledge base.
   Future: few-shot examples from successful grows injected into prompts.

The `LLM decision log` is the most novel part — logging the full prompt sent to
the LLM, not just the commands issued. This lets you answer: "Was the LLM making
good decisions? Did the safety engine override it correctly? Was the prompt the
problem or the model?"

---

## Context assembly design

The two-message split (SYSTEM vs USER) was a deliberate safety decision:

**Safety rules must never be trimmed.** If they live in the USER message and
the context window overflows, they get trimmed first. Catastrophic — the LLM
might water a flooded plant or run a pump dry.

By putting safety rules in SYSTEM, they're structurally protected from token
trimming. The USER message trims in safe order: 7d trend → 24h range → anomalies.
Live snapshot and recent decisions are never trimmed.

The "last 5 decisions" injection was discovered through prototype observation:
without it, the LLM re-waters every 60 seconds because it's stateless. This
is the most important trick in the context assembly.

---

## Hardware choice rationale

**ESP32** (not Raspberry Pi) for sensors/actuators because:
- WiFi built-in, no USB hub needed
- ADC for capacitive soil moisture
- I2C for SHT31, BH1750
- Relay + PWM GPIO for actuators
- ESPHome makes firmware trivial (YAML config, no C++ needed)
- $3–5 per board
- Runs headlessly for months on a single power supply

**Raspberry Pi** for the orchestrator brain because:
- Runs Python (orchestrator, MQTT broker)
- SQLite history store
- Always-on (vs. laptop)
- Calls the Muse API over HTTPS for LLM inference
- Pi Zero 2W is enough for the poll loop (~$15)

**MQTT** as the protocol because:
- Topic-based pub/sub matches sensor/actuator model perfectly
- QoS 1 ensures delivery
- Mosquitto runs on Pi Zero with minimal RAM
- ESPHome has native MQTT support
- All sensor plugins just declare their MQTT topic in `sensor.yaml`

---

## The name "Muse-IoT"

Project name suggested: **Muse-IoT**
- Grounded (IoT + physical world)
- Evokes growth without being locked to plants
- Memorable, available as a GitHub org name
- Not locked to any specific domain or hardware

Alternative names considered: OpenLoop, NeuralIoT, Lumio.
The name matters less than the `domain.yaml` contract — that's what enables
community contribution.

---

## HCMC-specific considerations

The reference implementation is optimized for Ho Chi Minh City:
- Outdoor temp: 30–35°C → cooling is mandatory for most leafy greens
- Humidity: 70–85% → misting often counterproductive
- No seasons → grow year-round but manage heat spikes
- Swiss chard chosen because: heat-tolerant, cut-and-come-again, 55-day first harvest
- Fan is the most important actuator (more than pump in HCMC climate)
- Safety rule `fan ON (forced) if sht31_temp > 32` is non-negotiable

For other climates, the domain profile targets change but the architecture is identical.
