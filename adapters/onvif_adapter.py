"""
HomeGuard AI — Adaptador ONVIF para Hanwha Wisenet
Usa onvif-zeep para PullPoint subscription y recibe eventos de analítica
(Virtual Line, Motion Detection, Tampering) directamente de la cámara.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from adapters.base import BaseAdapter
from core.event import SecurityEvent, SourceType, EventType, Severity
from config.settings import CameraConfig

logger = logging.getLogger(__name__)

# Mapeo de topics Hanwha → EventType HomeGuard
HANWHA_TOPIC_MAP = {
    "VideoAnalytics":      (EventType.INTRUSION, Severity.HIGH,   0.90, False),
    "MotionDetection":     (EventType.MOTION,    Severity.LOW,    0.70, True),
    "MotionAlarm":         (EventType.MOTION,    Severity.MEDIUM, 0.80, True),
    "TamperingDetection":  (EventType.TAMPER,    Severity.HIGH,   0.95, False),
    "ImageTooBlurry":      (EventType.TAMPER,    Severity.MEDIUM, 0.90, False),
    "DigitalInput":        (EventType.INTRUSION, Severity.HIGH,   0.95, False),
}

CONFIDENCE_SKIP_AI = 0.85


class ONVIFAdapter(BaseAdapter):
    """
    Adaptador ONVIF para cámaras Hanwha Wisenet con edge analytics.
    Usa PullPoint subscription para recibir eventos en tiempo real.
    """

    def __init__(self, camera_config: CameraConfig):
        super().__init__(
            adapter_id=f"onvif_{camera_config.id}",
            adapter_name=camera_config.name,
        )
        self.camera_config = camera_config
        self._poll_task: Optional[asyncio.Task] = None
        self._cam = None
        self._pullpoint = None
        self._host = self._extract_host(camera_config.rtsp_url)
        self._port = 80
        self._user = camera_config.onvif_user or "admin"
        self._password = camera_config.onvif_password or ""

    async def start(self) -> bool:
        try:
            from onvif import ONVIFCamera
            self.logger.info(f"Conectando ONVIF a {self._host}:{self._port}")

            self._cam = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: ONVIFCamera(self._host, self._port, self._user, self._password)
            )

            ok = await self._create_pullpoint()
            if not ok:
                return False

            self._running = True
            self._poll_task = asyncio.create_task(
                self._poll_loop(),
                name=f"onvif_{self.camera_config.id}",
            )
            self.logger.info(f"ONVIF activo — escuchando eventos de {self._host}")
            return True

        except ImportError:
            self.logger.error("onvif-zeep no instalado — pip install onvif-zeep")
            return False
        except Exception as e:
            self.logger.error(f"Error iniciando ONVIF: {e}")
            return False

    async def stop(self):
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
        self.logger.info("Adaptador ONVIF detenido")

    def is_healthy(self) -> bool:
        return (
            self._running and
            self._poll_task is not None and
            not self._poll_task.done()
        )

    async def _create_pullpoint(self) -> bool:
        try:
            self._pullpoint = await asyncio.get_event_loop().run_in_executor(
                None, self._cam.create_pullpoint_service
            )
            self.logger.info("PullPoint subscription creada")
            return True
        except Exception as e:
            self.logger.error(f"Error creando PullPoint: {e}")
            return False

    async def _poll_loop(self):
        while self._running:
            try:
                messages = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._pullpoint.PullMessages({
                        'MessageLimit': 10,
                        'Timeout': 'PT2S',
                    })
                )

                if messages and hasattr(messages, 'NotificationMessage'):
                    notifications = messages.NotificationMessage
                    if notifications:
                        if not isinstance(notifications, list):
                            notifications = [notifications]
                        for msg in notifications:
                            event = self._parse_notification(msg)
                            if event:
                                await self.emit(event)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error en polling ONVIF: {e} — reconectando...")
                await asyncio.sleep(5)
                try:
                    await self._create_pullpoint()
                except Exception:
                    pass

            await asyncio.sleep(0.5)

    def _parse_notification(self, msg) -> Optional[SecurityEvent]:
        try:
            topic_str = ""
            if hasattr(msg, 'Topic') and msg.Topic:
                topic_val = msg.Topic._value_1 if hasattr(msg.Topic, '_value_1') else str(msg.Topic)
                topic_str = str(topic_val) if topic_val else ""

            mapping = None
            matched_topic = None
            for topic_key, config in HANWHA_TOPIC_MAP.items():
                if topic_key.lower() in topic_str.lower():
                    mapping = config
                    matched_topic = topic_key
                    break

            if not mapping:
                self.logger.debug(f"Topic ONVIF sin mapeo: {topic_str}")
                return None

            event_type, severity, confidence, needs_ai = mapping

            state = self._extract_state(msg)
            if state is False:
                return None

            needs_ai_analysis = needs_ai or (confidence < CONFIDENCE_SKIP_AI)

            self.logger.info(
                f"[{self.camera_config.name}] ONVIF: {matched_topic} → "
                f"{event_type.value} ({confidence:.0%}) — "
                f"{'→ Claude' if needs_ai_analysis else '→ directo'}"
            )

            snapshot = self._get_snapshot()

            return SecurityEvent(
                camera_id=self.camera_config.id,
                camera_name=self.camera_config.name,
                timestamp=datetime.now(),
                source_type=SourceType.EDGE,
                event_type=event_type,
                severity=severity,
                confidence=confidence,
                snapshot=snapshot,
                needs_ai_analysis=needs_ai_analysis,
                raw_metadata={
                    "onvif_topic": topic_str,
                    "matched_topic": matched_topic,
                    "source": "edge_analytics",
                    "camera_ip": self._host,
                },
            )

        except Exception as e:
            self.logger.error(f"Error parseando notificación ONVIF: {e}")
            return None

    def _extract_state(self, msg) -> Optional[bool]:
        try:
            if hasattr(msg, 'Message') and msg.Message:
                m = msg.Message
                if hasattr(m, '_value_1') and m._value_1:
                    inner = m._value_1
                    if hasattr(inner, 'Data') and inner.Data:
                        items = getattr(inner.Data, 'SimpleItem', [])
                        if not isinstance(items, list):
                            items = [items]
                        for item in items:
                            if hasattr(item, 'Value'):
                                val = str(item.Value).lower()
                                if val in ('0', 'false', 'inactive'):
                                    return False
                                if val in ('1', 'true', 'active'):
                                    return True
        except Exception:
            pass
        return True

    def _get_snapshot(self) -> Optional[bytes]:
        try:
            import urllib.request
            url = f"http://{self._host}/onvif/snapshot"
            password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
            password_mgr.add_password(None, url, self._user, self._password)
            auth_handler = urllib.request.HTTPDigestAuthHandler(password_mgr)
            opener = urllib.request.build_opener(auth_handler)
            with opener.open(url, timeout=3) as response:
                return response.read()
        except Exception:
            return None

    def _extract_host(self, rtsp_url: str) -> str:
        try:
            without_scheme = rtsp_url.replace("rtsp://", "")
            if "@" in without_scheme:
                without_scheme = without_scheme.split("@")[1]
            return without_scheme.split("/")[0].split(":")[0]
        except Exception:
            return "192.168.1.100"
