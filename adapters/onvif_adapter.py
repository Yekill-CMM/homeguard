"""
HomeGuard AI — Adaptador ONVIF para Hanwha Wisenet
Parsea eventos ONVIF directamente desde el XML del mensaje,
ya que Hanwha no expone el topic en el campo estándar.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from adapters.base import BaseAdapter
from core.event import SecurityEvent, SourceType, EventType, Severity
from config.settings import CameraConfig

logger = logging.getLogger(__name__)

CONFIDENCE_SKIP_AI = 0.85


class ONVIFAdapter(BaseAdapter):
    """
    Adaptador ONVIF para cámaras Hanwha Wisenet.
    Detecta eventos de Virtual Line, Motion y Tampering
    parseando el XML del mensaje directamente.
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
        self._snapshot_url: Optional[str] = None  # Cache de URL snapshot

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
        """Loop de polling ONVIF."""
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
                            event = self._parse_message(msg)
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

            await asyncio.sleep(0.3)

    def _parse_message(self, msg) -> Optional[SecurityEvent]:
        """
        Parsea un mensaje ONVIF de Hanwha.
        Detecta el tipo de evento por los campos del mensaje,
        no por el topic (que Hanwha no expone correctamente).
        """
        try:
            elem = msg.Message._value_1
            if elem is None:
                return None

            NS = 'http://www.onvif.org/ver10/schema'
            op = elem.get('PropertyOperation', '')

            # Ignorar eventos de inicialización
            if op == 'Initialized':
                return None

            # Extraer datos del mensaje
            data_items = {}
            source_items = {}

            data_el = elem.find(f'{{{NS}}}Data')
            if data_el is not None:
                for item in data_el.findall(f'{{{NS}}}SimpleItem'):
                    data_items[item.get('Name', '')] = item.get('Value', '')

            source_el = elem.find(f'{{{NS}}}Source')
            if source_el is not None:
                for item in source_el.findall(f'{{{NS}}}SimpleItem'):
                    source_items[item.get('Name', '')] = item.get('Value', '')

            # ── Clasificar el evento por los datos ──────────────────

            # Virtual Line / IVA — State + Action
            if 'State' in data_items and 'Action' in data_items:
                state = str(data_items['State'])
                action = data_items.get('Action', '')

                # Solo procesar cuando State=1 (cruce activo)
                if state not in ('1', 'true', 'True'):
                    return None

                self.logger.info(
                    f"[{self.camera_config.name}] Virtual Line: "
                    f"State={state} Action={action} → INTRUSION"
                )
                return self._build_event(
                    event_type=EventType.INTRUSION,
                    severity=Severity.HIGH,
                    confidence=0.90,
                    needs_claude=False,
                    meta={'action': action, 'state': state, 'type': 'virtual_line'}
                )

            # Motion Detection — Motion field
            if 'Motion' in data_items:
                motion = str(data_items['Motion'])
                if motion not in ('1', 'true', 'True'):
                    return None
                window = source_items.get('Window', '0')
                self.logger.info(
                    f"[{self.camera_config.name}] Motion: window={window}"
                )
                return self._build_event(
                    event_type=EventType.MOTION,
                    severity=Severity.LOW,
                    confidence=0.70,
                    needs_claude=True,
                    meta={'window': window, 'type': 'motion'}
                )

            # Tampering
            if 'Tampering' in data_items:
                val = str(data_items['Tampering'])
                if val not in ('1', 'true', 'True'):
                    return None
                self.logger.info(f"[{self.camera_config.name}] Tampering detectado")
                return self._build_event(
                    event_type=EventType.TAMPER,
                    severity=Severity.HIGH,
                    confidence=0.95,
                    needs_claude=False,
                    meta={'type': 'tampering'}
                )

            # VideoAnalytics genérico — State sin Action
            if 'State' in data_items and 'Action' not in data_items:
                state = str(data_items['State'])
                if state not in ('1', 'true', 'True'):
                    return None
                self.logger.info(
                    f"[{self.camera_config.name}] VideoAnalytics: State={state}"
                )
                return self._build_event(
                    event_type=EventType.INTRUSION,
                    severity=Severity.MEDIUM,
                    confidence=0.80,
                    needs_claude=True,
                    meta={'state': state, 'type': 'analytics'}
                )

        except Exception as e:
            self.logger.error(f"Error parseando mensaje ONVIF: {e}")

        return None

    def _build_event(self, event_type, severity, confidence,
                     needs_claude, meta) -> SecurityEvent:
        """Construye un SecurityEvent con snapshot."""
        needs_ai = needs_claude or (confidence < CONFIDENCE_SKIP_AI)
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
            needs_ai_analysis=needs_ai,
            raw_metadata={
                **meta,
                "source": "edge_analytics",
                "camera_ip": self._host,
            },
        )

    def _get_snapshot(self) -> Optional[bytes]:
        """
        Obtiene snapshot JPEG via ONVIF GetSnapshotUri.
        Hanwha Wisenet usa /stw-cgi/video.cgi — la URL se obtiene
        dinámicamente del servicio de media para máxima compatibilidad.
        """
        # Usar URL cacheada si ya la tenemos
        if self._snapshot_url:
            result = self._fetch_snapshot_url(self._snapshot_url)
            if result:
                return result
            # Si falló, reintentar obteniendo la URL de nuevo
            self._snapshot_url = None

        # Obtener URL desde ONVIF GetSnapshotUri
        try:
            if self._cam is None:
                return None
            media = self._cam.create_media_service()
            profiles = media.GetProfiles()
            # Preferir profile2 (H.264 calidad media), fallback al primero
            target = None
            for p in profiles:
                if p.token in ("DefaultProfile-02", "profile2", "Profile_2"):
                    target = p.token
                    break
            if target is None and profiles:
                target = profiles[0].token
            if target is None:
                return None
            uri_response = media.GetSnapshotUri({"ProfileToken": target})
            self._snapshot_url = uri_response.Uri
            self.logger.debug(f"Snapshot URI: {self._snapshot_url}")
            return self._fetch_snapshot_url(self._snapshot_url)
        except Exception as e:
            self.logger.warning(f"GetSnapshotUri falló: {e}")
            return None

    def _fetch_snapshot_url(self, url: str) -> Optional[bytes]:
        """Descarga el snapshot desde la URL con autenticación Digest."""
        try:
            import urllib.request
            password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
            password_mgr.add_password(None, url, self._user, self._password)
            auth_handler = urllib.request.HTTPDigestAuthHandler(password_mgr)
            opener = urllib.request.build_opener(auth_handler)
            with opener.open(url, timeout=4) as response:
                data = response.read()
                # Verificar que es un JPEG válido
                if data and data[:2] == b'\xff\xd8':
                    return data
                self.logger.warning(f"Snapshot no es JPEG válido ({len(data)} bytes)")
                return None
        except Exception as e:
            self.logger.warning(f"Error descargando snapshot: {e}")
            return None

    def _extract_host(self, rtsp_url: str) -> str:
        try:
            without_scheme = rtsp_url.replace("rtsp://", "")
            if "@" in without_scheme:
                without_scheme = without_scheme.split("@")[1]
            return without_scheme.split("/")[0].split(":")[0]
        except Exception:
            return "192.168.1.100"
