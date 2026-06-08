"""
HomeGuard AI — Adaptador MQTT
Para sensores IoT: PIR, humo, gas, CO2, apertura de puertas/ventanas.
Escucha topics MQTT y convierte mensajes a SecurityEvents.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

from adapters.base import BaseAdapter
from core.event import SecurityEvent, SourceType, EventType, Severity

logger = logging.getLogger(__name__)

# Mapeo de topics MQTT a clasificación de evento
# Formato: "topic_pattern": (EventType, Severity, needs_ai, description)
MQTT_TOPIC_MAP = {
    "homeguard/sensor/pir":     (EventType.MOTION,    Severity.LOW,      True,  "Sensor PIR activado"),
    "homeguard/sensor/smoke":   (EventType.FIRE,      Severity.CRITICAL, False, "Humo detectado"),
    "homeguard/sensor/gas":     (EventType.GAS,       Severity.CRITICAL, False, "Gas detectado"),
    "homeguard/sensor/co2":     (EventType.GAS,       Severity.HIGH,     False, "CO2 elevado"),
    "homeguard/sensor/door":    (EventType.ACCESS,    Severity.LOW,      False, "Puerta abierta"),
    "homeguard/sensor/window":  (EventType.INTRUSION, Severity.MEDIUM,   False, "Ventana abierta"),
    "homeguard/sensor/vibration":(EventType.INTRUSION,Severity.MEDIUM,   True,  "Vibración detectada"),
}


class MQTTAdapter(BaseAdapter):
    """
    Adaptador para sensores IoT vía MQTT.

    Los sensores de humo, gas y CO2 NO necesitan análisis de Claude —
    son señales físicas directas. El PIR y vibración sí pueden
    beneficiarse de correlación con cámaras vía Claude.
    """

    def __init__(self, broker_host: str = "localhost", broker_port: int = 1883):
        super().__init__(
            adapter_id="mqtt_sensors",
            adapter_name="Sensores IoT MQTT",
        )
        self.broker_host = broker_host
        self.broker_port = broker_port
        self._client = None
        self._listen_task: Optional[asyncio.Task] = None

    async def start(self) -> bool:
        try:
            import aiomqtt
            self._aiomqtt = aiomqtt
        except ImportError:
            self.logger.error(
                "aiomqtt no instalado. Ejecuta: pip install aiomqtt"
            )
            return False

        self._running = True
        self._listen_task = asyncio.create_task(
            self._listen_loop(),
            name="mqtt_listener",
        )
        self.logger.info(f"MQTT iniciado — broker: {self.broker_host}:{self.broker_port}")
        return True

    async def stop(self):
        self._running = False
        if self._listen_task:
            self._listen_task.cancel()
        self.logger.info("Adaptador MQTT detenido")

    def is_healthy(self) -> bool:
        return (
            self._running and
            self._listen_task is not None and
            not self._listen_task.done()
        )

    async def _listen_loop(self):
        """Loop principal de escucha MQTT."""
        while self._running:
            try:
                async with self._aiomqtt.Client(self.broker_host, self.broker_port) as client:
                    # Suscribirse a todos los topics de HomeGuard
                    await client.subscribe("homeguard/#")
                    self.logger.info("Suscrito a homeguard/#")

                    async for message in client.messages:
                        topic = str(message.topic)
                        try:
                            payload = json.loads(message.payload.decode())
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            payload = {"raw": message.payload.decode(errors="replace")}

                        event = self._build_event(topic, payload)
                        if event:
                            await self.emit(event)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error MQTT: {e} — reconectando en 10s...")
                await asyncio.sleep(10)

    def _build_event(self, topic: str, payload: dict) -> Optional[SecurityEvent]:
        """Construye un SecurityEvent desde un mensaje MQTT."""
        # Buscar en el mapa de topics
        mapping = None
        for pattern, config in MQTT_TOPIC_MAP.items():
            if topic.startswith(pattern) or topic == pattern:
                mapping = config
                break

        if not mapping:
            self.logger.debug(f"Topic MQTT sin mapeo: {topic}")
            return None

        event_type, severity, needs_ai, description = mapping

        # Los sensores de seguridad críticos no esperan análisis de IA
        # — la acción debe ser inmediata
        if severity == Severity.CRITICAL:
            needs_ai = False

        # Extraer camera_id del payload si viene correlacionado con una cámara
        camera_id = payload.get("camera_id", "sensor")
        camera_name = payload.get("camera_name", topic.split("/")[-1])

        self.logger.info(
            f"[MQTT] {topic} → {event_type.value} "
            f"(severity: {severity.value})"
        )

        return SecurityEvent(
            camera_id=camera_id,
            camera_name=camera_name,
            timestamp=datetime.now(),
            source_type=SourceType.SENSOR,
            event_type=event_type,
            severity=severity,
            confidence=1.0,          # Sensor físico — confianza total
            snapshot=None,           # Sin imagen
            needs_ai_analysis=needs_ai,
            ai_description=description,
            raw_metadata={
                "mqtt_topic": topic,
                "payload": payload,
            },
        )
