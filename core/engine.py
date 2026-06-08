"""
HomeGuard AI — Core del sistema
Recibe SecurityEvents de cualquier adaptador y coordina:
1. Análisis con Claude (solo si needs_ai_analysis = True)
2. Ajuste de severidad según resultado
3. Disparo de alertas
4. Persistencia en log
"""

import asyncio
import logging
import base64
import json
from datetime import datetime
from typing import Optional

import anthropic

from core.event import SecurityEvent, EventType, Severity
from config.settings import ClaudeConfig, StorageConfig
from events.database import EventDatabase
from events.snapshot_store import SnapshotStore
from notifications.push import PushNotifier

logger = logging.getLogger(__name__)

# Mapeo de clasificación Claude → EventType y Severity internos
CLAUDE_EVENT_MAP = {
    "person":    (EventType.PERSON,   Severity.MEDIUM),
    "vehicle":   (EventType.VEHICLE,  Severity.MEDIUM),
    "animal":    (EventType.ANIMAL,   Severity.LOW),
    "intrusion": (EventType.INTRUSION,Severity.HIGH),
    "no_threat": (EventType.NO_THREAT,Severity.LOW),
    "unclear":   (EventType.UNKNOWN,  Severity.LOW),
}

YOLO_EVENT_MAP = {
    "person":   (EventType.PERSON,   Severity.MEDIUM),
    "vehicle":  (EventType.VEHICLE,  Severity.MEDIUM),
    "animal":   (EventType.ANIMAL,   Severity.LOW),
    "no_threat":(EventType.NO_THREAT,Severity.LOW),
    "unknown":  (EventType.UNKNOWN,  Severity.LOW),
}


class HomeGuardCore:
    """
    Núcleo central de HomeGuard AI.

    Recibe SecurityEvents normalizados de cualquier adaptador.
    No sabe si vienen de RTSP, ONVIF, MQTT o webhooks.
    Solo procesa, analiza y actúa.
    """

    def __init__(self, claude_config: ClaudeConfig, storage_config: StorageConfig,
                 notifier: PushNotifier | None = None):
        self.claude_config = claude_config
        self.client = anthropic.Anthropic(api_key=claude_config.api_key)
        self.db = EventDatabase(storage_config.db_path)
        self.snapshots = SnapshotStore(storage_config.frames_path)
        self.notifier = notifier

        # Intentar cargar YOLOv8 como pre-filtro
        self.yolo = None
        try:
            from core.yolo_filter import YOLOFilter
            self.yolo = YOLOFilter()
            if self.yolo.load():
                logger.info("YOLOv8 nano activo — pre-filtro Claude habilitado")
            else:
                self.yolo = None
                logger.warning("YOLOv8 no disponible — usando solo Claude Vision")
        except Exception as e:
            logger.warning(f"YOLOv8 no disponible: {e} — usando solo Claude Vision")
        self._stats = {
            "events_received": 0,
            "events_analyzed": 0,
            "events_skipped_ai": 0,
            "alerts_triggered": 0,
        }

    async def handle_event(self, event: SecurityEvent):
        """Punto de entrada principal — llamado por los adaptadores."""
        self._stats["events_received"] += 1

        # ── Pre-filtro YOLO (si está disponible) ──────────────────────
        if self.yolo and event.snapshot:
            import cv2
            import numpy as np

            # Decodificar snapshot JPEG a numpy array
            nparr = np.frombuffer(event.snapshot, np.uint8)
            image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if image is not None:
                yolo_result = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self.yolo.analyze(image)
                )

                if not yolo_result.detected and not yolo_result.needs_claude:
                    # YOLO descartó el evento — sin objeto relevante
                    self._stats["events_skipped_ai"] += 1
                    logger.debug(
                        f"[{event.camera_name}] YOLO: sin objeto relevante — descartado"
                    )
                    return  # No persistir ni alertar

                if yolo_result.detected and not yolo_result.needs_claude:
                    # YOLO clasificó con alta confianza — no necesita Claude
                    mapping = YOLO_EVENT_MAP.get(
                        yolo_result.event_type, (EventType.UNKNOWN, Severity.LOW)
                    )
                    event.event_type = mapping[0]
                    event.severity   = mapping[1]
                    event.confidence = yolo_result.confidence
                    event.ai_description = (
                        f"{yolo_result.label} detectado (YOLO {yolo_result.confidence:.0%})"
                    )
                    event.needs_ai_analysis = False
                    self._stats["events_skipped_ai"] += 1
                    logger.info(
                        f"[{event.camera_name}] YOLO → {event.event_type.value} "
                        f"({event.confidence:.0%}) [{yolo_result.inference_ms}ms] — sin Claude"
                    )
                else:
                    # YOLO detectó algo con baja confianza → pasar a Claude
                    event.needs_ai_analysis = True
                    logger.info(
                        f"[{event.camera_name}] YOLO: {yolo_result.label} "
                        f"({yolo_result.confidence:.0%}) → pasando a Claude"
                    )

        # ── Análisis Claude (si necesario) ────────────────────────────
        if event.needs_ai_analysis and event.snapshot:
            await self._analyze_with_claude(event)
            self._stats["events_analyzed"] += 1
        elif not event.needs_ai_analysis:
            pass  # Ya clasificado por YOLO
        else:
            self._stats["events_skipped_ai"] += 1
            logger.info(f"[{event.camera_name}] Sin snapshot — omitiendo análisis")

        await self._evaluate_alert(event)
        await self._persist(event)

    async def _analyze_with_claude(self, event: SecurityEvent):
        """Envía el snapshot a Claude Vision y actualiza el evento."""
        start = _now_ms()
        try:
            image_b64 = base64.standard_b64encode(event.snapshot).decode()

            prompt = (
                f"Cámara: {event.camera_name}\n"
                f"Hora: {event.timestamp.strftime('%H:%M:%S')}\n"
                f"Fuente: {event.source_type.value}\n"
                f"Evento previo: {event.event_type.value} "
                f"(confianza previa: {event.confidence:.0%})\n\n"
                f"Analiza la imagen y confirma o corrige la clasificación."
            )

            message = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.client.messages.create(
                    model=self.claude_config.model,
                    max_tokens=self.claude_config.max_tokens,
                    system=self.claude_config.system_prompt,
                    messages=[{
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": image_b64,
                                },
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }],
                )
            )

            raw = message.content[0].text
            data = self._parse_claude_response(raw)

            if data:
                # Actualizar el evento con el resultado de Claude
                claude_type = data.get("event_type", "unclear")
                mapping = CLAUDE_EVENT_MAP.get(claude_type, (EventType.UNKNOWN, Severity.LOW))
                event.event_type = mapping[0]
                event.severity   = mapping[1]
                event.confidence = float(data.get("confidence", 0.0))
                event.ai_description = data.get("description")
                event.ai_alert       = bool(data.get("alert", False))
                event.ai_alert_reason= data.get("alert_reason")
                event.ai_analysis_ms = _now_ms() - start

                level = logging.WARNING if event.ai_alert else logging.INFO
                logger.log(
                    level,
                    f"Claude → {event.event_type.value} "
                    f"({event.confidence:.0%}) "
                    f"[{event.ai_analysis_ms}ms] — {event.ai_description}"
                )

        except Exception as e:
            logger.error(f"Error en análisis Claude: {e}")

    async def _evaluate_alert(self, event: SecurityEvent):
        """Decide si hay que disparar una alerta y por qué canal."""
        should_alert = (
            event.ai_alert or
            event.severity in (Severity.HIGH, Severity.CRITICAL) or
            event.event_type in (EventType.INTRUSION, EventType.FIRE, EventType.GAS, EventType.TAMPER)
        )

        if should_alert:
            self._stats["alerts_triggered"] += 1
            await self._trigger_alert(event)

    async def _trigger_alert(self, event: SecurityEvent):
        """Dispara alertas por los canales configurados."""
        msg = (
            f"[{event.severity.value.upper()}] {event.camera_name} — "
            f"{event.event_type.value}"
        )
        if event.ai_alert_reason:
            msg += f": {event.ai_alert_reason}"
        elif event.ai_description:
            msg += f": {event.ai_description}"

        logger.warning(f"🚨 ALERTA: {msg}")

        # Push notification a dispositivos móviles
        if self.notifier:
            await self.notifier.notify_event(event)

    async def _persist(self, event: SecurityEvent):
        """Guarda snapshot en disco y evento en SQLite."""
        # 1. Guardar JPEG en disco
        if event.snapshot:
            path = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self.snapshots.save(event)
            )
            event.snapshot_path = path
            event.snapshot = None  # Liberar memoria tras guardar

        # 2. Guardar en SQLite
        saved = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self.db.save_event(event)
        )

        if saved:
            logger.info(
                f"📝 Guardado: [{event.event_type.value}] "
                f"{event.camera_name} @ {event.timestamp.strftime('%H:%M:%S')} "
                f"→ {event.snapshot_path or 'sin snapshot'}"
            )

    def stats(self) -> dict:
        base = dict(self._stats)
        if self.yolo:
            base["yolo"] = self.yolo.stats()
        return base


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)
