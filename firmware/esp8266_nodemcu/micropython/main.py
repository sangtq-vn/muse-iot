import dht
import machine
import time
from umqtt.simple import MQTTClient  # built into MicroPython for ESP8266

from config import (
    MQTT_BROKER, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD, MQTT_CLIENT_ID,
    TOPIC_PREFIX, DHT11_PIN, SOIL_DRY, SOIL_WET, POLL_INTERVAL_SEC,
)

sensor_dht = dht.DHT11(machine.Pin(DHT11_PIN))
adc = machine.ADC(0)   # A0 — only ADC on ESP8266


def mqtt_connect():
    client = MQTTClient(
        MQTT_CLIENT_ID,
        MQTT_BROKER,
        port=MQTT_PORT,
        user=MQTT_USERNAME or None,
        password=MQTT_PASSWORD or None,
        keepalive=30,
    )
    client.connect()
    print("MQTT connected to", MQTT_BROKER)
    return client


def soil_percent():
    raw = adc.read()  # 0–1023
    # Resistive sensor: dry = high raw value, wet = low raw value (inverted)
    pct = (SOIL_DRY - raw) / max(SOIL_DRY - SOIL_WET, 1) * 100
    return round(max(0.0, min(100.0, pct)), 1)


def publish(client, sensor_id, value):
    topic = "{}/sensors/{}".format(TOPIC_PREFIX, sensor_id)
    client.publish(topic, str(value))
    print("  {} = {}".format(topic, value))


def main():
    client = mqtt_connect()

    while True:
        try:
            sensor_dht.measure()
            publish(client, "dht11_temp",     sensor_dht.temperature())
            publish(client, "dht11_humidity", sensor_dht.humidity())
            publish(client, "soil_resistive", soil_percent())

        except OSError as e:
            # MQTT connection dropped — reconnect once
            print("Error:", e, "— reconnecting MQTT")
            try:
                client = mqtt_connect()
            except Exception as e2:
                print("Reconnect failed:", e2)

        time.sleep(POLL_INTERVAL_SEC)


main()
