# ── WiFi ─────────────────────────────────────────────────────────────────────
WIFI_SSID     = "YourWiFiSSID"
WIFI_PASSWORD = "YourWiFiPassword"

# ── MQTT ─────────────────────────────────────────────────────────────────────
MQTT_BROKER    = "192.168.1.100"   # broker IP, or "mqtt" when using Docker Compose
MQTT_PORT      = 1883
MQTT_USERNAME  = ""                # leave empty if no broker auth
MQTT_PASSWORD  = ""
MQTT_CLIENT_ID = "muse-iot-nodemcu"
TOPIC_PREFIX   = "muse-iot"        # must match config.yaml device.config.topic_prefix

# ── Hardware pins ─────────────────────────────────────────────────────────────
DHT11_PIN = 4    # D2 = GPIO4

# ── Calibration — resistive soil moisture (A0) ───────────────────────────────
# Read adc.read() (0–1023) with probe in dry soil → set SOIL_DRY
# Read adc.read() with probe submerged in water  → set SOIL_WET
SOIL_DRY = 1023   # raw ADC value in dry conditions  (adjust after calibration)
SOIL_WET = 300    # raw ADC value in saturated soil  (adjust after calibration)

# ── Poll interval ─────────────────────────────────────────────────────────────
POLL_INTERVAL_SEC = 30
