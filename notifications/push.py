"""
HomeGuard AI — Notificaciones Push
Gestiona suscripciones de dispositivos y envío de notificaciones Web Push.
Funciona en red local sin Firebase ni servicios externos.
"""

import json
import asyncio
import logging
from datetime import datetime
from typing import Optional
from dataclasses import dataclass

from pywebpush import webpush, WebPushException

from core.event import SecurityEvent, Severity, EventType
from notifications.vapid import VAPIDManager

logger = logging.getLogger(__name__)

# Dirección del remitente (requerida por VAPID — puede ser ficticia en LAN)
VAPID_CLAIMS_EMAIL = "https://5228.tailfc504d.ts.net"


@dataclass
class PushSubscription:
    """Suscripción Web Push de un dispositivo."""
    device_id: str
    device_name: str
    endpoint: str
    p256dh: str      # Clave pública del cliente
    auth: str        # Token de autenticación
    created_at: str


class PushNotifier:
    """
    Envía notificaciones push a todos los dispositivos suscritos.
    
    Flujo:
    1. La app móvil se suscribe → POST /api/push/subscribe
    2. La suscripción se guarda en la DB
    3. Cuando hay alerta → PushNotifier.notify(event)
    4. Se envía la notificación a cada dispositivo registrado
    """

    def __init__(self, db, vapid_manager: VAPIDManager):
        self.db = db
        self.vapid = vapid_manager
        self._ensure_subscriptions_table()

    def _ensure_subscriptions_table(self):
        """Crea la tabla de suscripciones si no existe."""
        with self.db._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS push_subscriptions (
                    device_id   TEXT PRIMARY KEY,
                    device_name TEXT NOT NULL,
                    endpoint    TEXT NOT NULL,
                    p256dh      TEXT NOT NULL,
                    auth        TEXT NOT NULL,
                    created_at  TEXT NOT NULL,
                    last_used   TEXT
                )
            """)
            conn.commit()

    # ------------------------------------------------------------------
    # Suscripciones
    # ------------------------------------------------------------------

    def save_subscription(self, sub: PushSubscription) -> bool:
        """Guarda o actualiza la suscripción de un dispositivo."""
        try:
            with self.db._connect() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO push_subscriptions
                    (device_id, device_name, endpoint, p256dh, auth, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    sub.device_id, sub.device_name, sub.endpoint,
                    sub.p256dh, sub.auth, sub.created_at,
                ))
                conn.commit()
            logger.info(f"Suscripción guardada: {sub.device_name} ({sub.device_id[:8]}...)")
            return True
        except Exception as e:
            logger.error(f"Error guardando suscripción: {e}")
            return False

    def remove_subscription(self, device_id: str):
        """Elimina la suscripción de un dispositivo."""
        with self.db._connect() as conn:
            conn.execute(
                "DELETE FROM push_subscriptions WHERE device_id = ?",
                (device_id,)
            )
            conn.commit()

    def get_subscriptions(self) -> list[PushSubscription]:
        """Retorna todas las suscripciones activas."""
        with self.db._connect() as conn:
            rows = conn.execute(
                "SELECT device_id, device_name, endpoint, p256dh, auth, created_at FROM push_subscriptions"
            ).fetchall()
        return [PushSubscription(**dict(row)) for row in rows]

    def subscription_count(self) -> int:
        with self.db._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM push_subscriptions"
            ).fetchone()[0]

    # ------------------------------------------------------------------
    # Envío de notificaciones
    # ------------------------------------------------------------------

    async def notify_event(self, event: SecurityEvent):
        """
        Envía notificación push para un SecurityEvent.
        Solo envía si es alerta o severidad alta/crítica.
        """
        if not self._should_notify(event):
            return

        payload = self._build_payload(event)
        await self._send_to_all(payload)

    async def notify_raw(self, title: str, body: str,
                          severity: str = "medium",
                          url: str = "/mobile"):
        """Envía notificación personalizada a todos los dispositivos."""
        payload = {
            "title": title,
            "body": body,
            "severity": severity,
            "url": url,
            "tag": "homeguard-manual",
        }
        await self._send_to_all(payload)

    def _should_notify(self, event: SecurityEvent) -> bool:
        """Determina si el evento merece notificación push."""
        return (
            event.ai_alert or
            event.severity in (Severity.HIGH, Severity.CRITICAL) or
            event.event_type in (
                EventType.INTRUSION,
                EventType.FIRE,
                EventType.GAS,
                EventType.TAMPER,
            )
        )

    def _build_payload(self, event: SecurityEvent) -> dict:
        """Construye el payload de la notificación según el evento."""
        icons = {
            EventType.PERSON:    "👤",
            EventType.VEHICLE:   "🚗",
            EventType.INTRUSION: "⚠️",
            EventType.FIRE:      "🔥",
            EventType.GAS:       "💨",
            EventType.TAMPER:    "🎥",
            EventType.ANIMAL:    "🐾",
        }
        icon = icons.get(event.event_type, "🚨")

        severity_titles = {
            Severity.CRITICAL: "🚨 CRÍTICO",
            Severity.HIGH:     "⚠️ ALERTA",
            Severity.MEDIUM:   "📢 Aviso",
            Severity.LOW:      "ℹ️ Evento",
        }
        title = f"{icon} HomeGuard — {severity_titles.get(event.severity, 'Evento')}"

        body = event.ai_alert_reason or event.ai_description or \
               f"{event.event_type.value} en {event.camera_name}"

        return {
            "title":     title,
            "body":      f"{event.camera_name}: {body}",
            "severity":  event.severity.value,
            "event_type": event.event_type.value,
            "event_id":  event.id,
            "url":       f"/mobile",
            "tag":       f"homeguard-{event.camera_id}",
            "timestamp": event.timestamp.isoformat(),
        }

    async def _send_to_all(self, payload: dict):
        """Envía el payload a todos los dispositivos suscritos."""
        subscriptions = self.get_subscriptions()

        if not subscriptions:
            logger.debug("Sin dispositivos suscritos — notificación omitida")
            return

        logger.info(
            f"Enviando push a {len(subscriptions)} dispositivo(s): "
            f"{payload.get('title', '')[:50]}"
        )

        failed_devices = []

        for sub in subscriptions:
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda s=sub: self._send_one(s, payload)
                )
                # Actualizar last_used
                with self.db._connect() as conn:
                    conn.execute(
                        "UPDATE push_subscriptions SET last_used = ? WHERE device_id = ?",
                        (datetime.now().isoformat(), sub.device_id)
                    )
                    conn.commit()

            except WebPushException as e:
                logger.error(f"Error push a {sub.device_name}: {e}")
                # Si la suscripción expiró o es inválida, eliminarla
                if e.response and e.response.status_code in (404, 410):
                    logger.info(f"Suscripción expirada — eliminando {sub.device_name}")
                    failed_devices.append(sub.device_id)
            except Exception as e:
                logger.error(f"Error enviando a {sub.device_name}: {e}")

        # Limpiar suscripciones caducadas
        for device_id in failed_devices:
            self.remove_subscription(device_id)

    def _send_one(self, sub: PushSubscription, payload: dict):
        """Envío sincrónico a un dispositivo (se ejecuta en thread pool)."""
        import tempfile, os
        with tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False) as f:
            f.write(self.vapid.private_key)
            tmp_path = f.name
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {
                        "p256dh": sub.p256dh,
                        "auth":   sub.auth,
                    },
                },
                data=json.dumps(payload),
                vapid_private_key=tmp_path,
                vapid_claims={
                    "sub": VAPID_CLAIMS_EMAIL,
                },
                content_encoding="aes128gcm",
            )
        finally:
            os.unlink(tmp_path)
        logger.debug(f"✓ Push enviado a {sub.device_name}")
