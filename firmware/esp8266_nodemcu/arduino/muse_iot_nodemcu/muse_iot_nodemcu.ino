/*
 * Muse-IoT — ESP8266 NodeMCU firmware
 *
 * Board  : NodeMCU 1.0 (ESP-12E Module)
 * Wiring :
 *   D2 (GPIO4)  → DHT11 data pin  [add 10kΩ pull-up resistor to 3.3V]
 *   A0          → AO pin of LM393 soil moisture board (analog output)
 *   D0 (GPIO16) → relay_pump IN pin  (active-LOW: GND = ON, 3.3V = OFF)
 *   D1 (GPIO5)  → relay_fan  IN pin  (active-LOW: GND = ON, 3.3V = OFF)
 *   3.3V / GND  → sensors and relay VCC/GND
 *
 * Required libraries — install via Arduino IDE → Tools → Manage Libraries:
 *   1. "PubSubClient"          by Nick O'Leary     (MQTT)
 *   2. "DHT sensor library"    by Adafruit         (DHT11)
 *   3. "Adafruit Unified Sensor" by Adafruit       (DHT dependency)
 *   4. "ArduinoJson"           by Benoit Blanchon  (JSON parsing)
 *
 * Board package (if not installed):
 *   File → Preferences → Additional Board URLs →
 *   add: http://arduino.esp8266.com/stable/package_esp8266com_index.json
 *   Then: Tools → Board Manager → search "esp8266" → install
 */

#include <ESP8266WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>
#include <ArduinoJson.h>

// ── Credentials — fill these in before flashing ──────────────────────────────

const char* WIFI_SSID      = "";
const char* WIFI_PASSWORD  = "";

const char* MQTT_BROKER    = "192.168.1.X";  // broker IP, or "mqtt" for Docker
const int   MQTT_PORT      = 1883;
const char* MQTT_USERNAME  = "";   // leave empty string if broker has no auth
const char* MQTT_PASSWORD  = "";

// ── Muse-IoT topic settings ───────────────────────────────────────────────────

const char* TOPIC_PREFIX   = "muse-iot";   // must match config.yaml topic_prefix

// ── Hardware pins ─────────────────────────────────────────────────────────────

#define DHT_PIN        4    // D2 = GPIO4
#define DHT_TYPE       DHT11

#define RELAY_PUMP_PIN 16   // D0 = GPIO16  (active-LOW)
#define RELAY_FAN_PIN  5    // D1 = GPIO5   (active-LOW)

// Relay logic: GND (LOW) activates the relay, 3.3V (HIGH) deactivates it
#define RELAY_ON  LOW
#define RELAY_OFF HIGH

// A0 is the only ADC on ESP8266 — no define needed, always use analogRead(A0)

// ── Soil moisture calibration ─────────────────────────────────────────────────
//
// Calibrate once with your specific probe:
//   Step 1 — leave probe in dry air, open Serial Monitor, note ADC raw value
//             → set SOIL_DRY to that number
//   Step 2 — submerge probe in a glass of water, note ADC raw value
//             → set SOIL_WET to that number
//
// The raw values are always printed to Serial to make this easy.

const int SOIL_DRY = 1023;   // ADC value in dry conditions  (0 % moisture)
const int SOIL_WET = 300;    // ADC value when fully wet      (100 % moisture)

// ── Poll interval ─────────────────────────────────────────────────────────────

const unsigned long POLL_MS = 30000;   // 30 s — matches sensor poll_interval_sec

// ─────────────────────────────────────────────────────────────────────────────

DHT         dht(DHT_PIN, DHT_TYPE);
WiFiClient  wifiClient;
PubSubClient mqtt(wifiClient);

// Unique client ID based on chip ID so multiple boards don't collide
String clientId = "muse-iot-nodemcu-" + String(ESP.getChipId(), HEX);

unsigned long lastPublishAt = 0;

// ── Relay control ─────────────────────────────────────────────────────────────

void setRelay(uint8_t pin, const char* state) {
  if (strcmp(state, "on") == 0) {
    digitalWrite(pin, RELAY_ON);
    Serial.printf("  relay %d → ON  (GND)\n", pin);
  } else {
    digitalWrite(pin, RELAY_OFF);
    Serial.printf("  relay %d → OFF (3.3V)\n", pin);
  }
}

// ── WiFi ──────────────────────────────────────────────────────────────────────

void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) return;

  Serial.printf("\nConnecting to WiFi: %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\nWiFi OK — IP: %s\n", WiFi.localIP().toString().c_str());
}

// ── MQTT ──────────────────────────────────────────────────────────────────────

// Called when an actuator command arrives from Muse-IoT
void onMessage(char* topic, byte* payload, unsigned int length) {
  // Parse JSON payload: {"value": "on"/"off", "ts": ...}
  StaticJsonDocument<128> doc;
  DeserializationError err = deserializeJson(doc, payload, length);
  if (err) {
    Serial.printf("  [MQTT] JSON parse error on %s: %s\n", topic, err.c_str());
    return;
  }

  const char* value = doc["value"] | "";
  Serial.printf("  [MQTT] %s → %s\n", topic, value);

  // Build expected topic strings for comparison
  char pumpTopic[80], fanTopic[80];
  snprintf(pumpTopic, sizeof(pumpTopic), "%s/actuator/relay_pump/set", TOPIC_PREFIX);
  snprintf(fanTopic,  sizeof(fanTopic),  "%s/actuator/relay_fan/set",  TOPIC_PREFIX);

  if (strcmp(topic, pumpTopic) == 0) {
    setRelay(RELAY_PUMP_PIN, value);
  } else if (strcmp(topic, fanTopic) == 0) {
    setRelay(RELAY_FAN_PIN, value);
  }
}

void connectMqtt() {
  while (!mqtt.connected()) {
    Serial.printf("Connecting to MQTT %s:%d ... ", MQTT_BROKER, MQTT_PORT);

    bool ok = (strlen(MQTT_USERNAME) > 0)
      ? mqtt.connect(clientId.c_str(), MQTT_USERNAME, MQTT_PASSWORD)
      : mqtt.connect(clientId.c_str());

    if (ok) {
      Serial.println("connected");

      // Subscribe to actuator command topics
      char pumpTopic[80], fanTopic[80];
      snprintf(pumpTopic, sizeof(pumpTopic), "%s/actuator/relay_pump/set", TOPIC_PREFIX);
      snprintf(fanTopic,  sizeof(fanTopic),  "%s/actuator/relay_fan/set",  TOPIC_PREFIX);
      mqtt.subscribe(pumpTopic, /*qos=*/1);
      mqtt.subscribe(fanTopic,  /*qos=*/1);
      Serial.printf("  subscribed to %s\n", pumpTopic);
      Serial.printf("  subscribed to %s\n", fanTopic);
    } else {
      Serial.printf("failed (state=%d), retrying in 5 s\n", mqtt.state());
      delay(5000);
    }
  }
}

// Publish a float value to  <TOPIC_PREFIX>/sensors/<sensorId>
void publishSensor(const char* sensorId, float value) {
  char topic[80];
  snprintf(topic, sizeof(topic), "%s/sensors/%s", TOPIC_PREFIX, sensorId);

  char payload[16];
  dtostrf(value, 1, 1, payload);   // 1 decimal place, e.g. "24.5"

  mqtt.publish(topic, payload, /*retain=*/false);
  Serial.printf("  %-45s %s\n", topic, payload);
}

// ── Sensors ───────────────────────────────────────────────────────────────────

float soilPercent() {
  int raw = analogRead(A0);
  Serial.printf("  [calibration] ADC raw = %d  (dry=%d, wet=%d)\n",
                raw, SOIL_DRY, SOIL_WET);

  float pct = (float)(SOIL_DRY - raw) / (float)(SOIL_DRY - SOIL_WET) * 100.0f;
  pct = constrain(pct, 0.0f, 100.0f);
  return pct;
}

// ─────────────────────────────────────────────────────────────────────────────

void setup() {
  Serial.begin(115200);
  delay(100);
  Serial.println("\n\nMuse-IoT NodeMCU booting...");
  Serial.printf("Client ID: %s\n", clientId.c_str());

  // Relays OFF at startup — set pin mode + safe state before pinMode output
  // to avoid a brief LOW glitch on D0 during boot (GPIO16 floats low at reset)
  digitalWrite(RELAY_PUMP_PIN, RELAY_OFF);
  digitalWrite(RELAY_FAN_PIN,  RELAY_OFF);
  pinMode(RELAY_PUMP_PIN, OUTPUT);
  pinMode(RELAY_FAN_PIN,  OUTPUT);
  Serial.println("Relays initialised OFF");

  dht.begin();

  connectWifi();

  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  mqtt.setCallback(onMessage);
  mqtt.setKeepAlive(60);
  mqtt.setBufferSize(256);
  connectMqtt();

  // Publish immediately on first boot
  lastPublishAt = millis() - POLL_MS;
}

void loop() {
  // Keep WiFi and MQTT alive without blocking
  connectWifi();
  if (!mqtt.connected()) connectMqtt();
  mqtt.loop();   // processes incoming actuator commands

  if (millis() - lastPublishAt < POLL_MS) return;
  lastPublishAt = millis();

  Serial.println("\n--- poll ---");

  // DHT11 temperature + humidity
  float temp = dht.readTemperature();
  float hum  = dht.readHumidity();

  if (isnan(temp) || isnan(hum)) {
    Serial.println("  DHT11 read failed — check wiring on D2 and pull-up resistor");
  } else {
    publishSensor("dht11_temp",     temp);
    publishSensor("dht11_humidity", hum);
  }

  // Soil moisture
  publishSensor("soil_resistive", soilPercent());
}
