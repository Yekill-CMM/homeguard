"""
HomeGuard AI — Monitor de salud del sistema
Detecta cámaras caídas, sensores sin señal y latencia elevada.
Genera alertas y notificaciones push cuando un dispositivo falla.
"""

import asyncio
import logging
import time
import socket
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class DeviceHealth:
    """Estado de salud de un dispositivo."""
    device_id: str
    device_name: str
    device_type: str        # camera | sensor | recorder | router | other
    host: str
    port: int = 80
    # Estado actual
    online: bool = True
    last_seen: Optional[datetime] = None
    latency_ms: int = 0
    consecutive_failures: int = 0
    # Umbrales
    max_latency_ms: int = 500
    max_failures: int = 3   # Fallos consecutivos antes de alertar


@dataclass
class HealthAlert:
    """Alerta generada por el monitor."""
    device_id: str
    device_name: str
    device_type: str
    alert_type: str         # offline | high_latency | recovered
    message: str
    timestamp: datetime = field(default_factory=datetime.now)
    latency_ms: int = 0


class HealthMonitor:
    """
    Monitor de salud de todos los dispositivos del sistema.
    Corre en background y genera alertas cuando detecta problemas.
    """

    def __init__(self, notifier=None, db=None, check_interval: int = 30):
        self.notifier = notifier
        self.db = db
        self.check_interval = check_interval  # segundos entre checks
        self._devices: dict[str, DeviceHealth] = {}
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._alert_history: list[HealthAlert] = []

    def register_device(self, device: DeviceHealth):
        """Registra un dispositivo para monitoreo."""
        self._devices[device.device_id] = device
        logger.info(f"Monitor: registrado {device.device_name} ({device.host}:{device.port})")

    def register_cameras(self, cameras: list):
        """Registra todas las cámaras activas para monitoreo."""
        for cam in cameras:
            host = self._extract_host(cam.rtsp_url)
            self._devices[cam.id] = DeviceHealth(
                device_id=cam.id,
                device_name=cam.name,
                device_type="camera",
                host=host,
                port=554,
                last_seen=datetime.now(),
            )

    def update_last_seen(self, device_id: str):
        """Actualiza el último contacto de un dispositivo."""
        if device_id in self._devices:
            self._devices[device_id].last_seen = datetime.now()
            self._devices[device_id].consecutive_failures = 0
            if not self._devices[device_id].online:
                self._devices[device_id].online = True
                asyncio.create_task(self._alert_recovered(self._devices[device_id]))

    async def start(self):
        """Inicia el monitor en background."""
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info(f"Monitor de salud iniciado — {len(self._devices)} dispositivo(s)")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    def get_status(self) -> list[dict]:
        """Estado actual de todos los dispositivos."""
        return [
            {
                "device_id":   d.device_id,
                "device_name": d.device_name,
                "device_type": d.device_type,
                "host":        d.host,
                "online":      d.online,
                "latency_ms":  d.latency_ms,
                "last_seen":   d.last_seen.isoformat() if d.last_seen else None,
                "failures":    d.consecutive_failures,
            }
            for d in self._devices.values()
        ]

    def get_alerts(self, limit: int = 50) -> list[dict]:
        """Últimas alertas del monitor."""
        return [
            {
                "device_id":   a.device_id,
                "device_name": a.device_name,
                "device_type": a.device_type,
                "alert_type":  a.alert_type,
                "message":     a.message,
                "timestamp":   a.timestamp.isoformat(),
                "latency_ms":  a.latency_ms,
            }
            for a in sorted(
                self._alert_history, key=lambda x: x.timestamp, reverse=True
            )[:limit]
        ]

    # ------------------------------------------------------------------

    async def _monitor_loop(self):
        """Loop principal de monitoreo."""
        while self._running:
            await asyncio.gather(*[
                self._check_device(device)
                for device in list(self._devices.values())
            ])
            await asyncio.sleep(self.check_interval)

    async def _check_device(self, device: DeviceHealth):
        """Verifica la conectividad y latencia de un dispositivo."""
        start = time.monotonic()
        reachable = await self._ping_tcp(device.host, device.port)
        latency_ms = int((time.monotonic() - start) * 1000)

        device.latency_ms = latency_ms

        if not reachable:
            device.consecutive_failures += 1
            device.latency_ms = 9999

            if device.consecutive_failures == device.max_failures:
                # Marcar como offline y alertar
                if device.online:
                    device.online = False
                    await self._alert_offline(device)

        else:
            # Dispositivo alcanzable
            device.last_seen = datetime.now()

            if not device.online:
                # Se recuperó
                device.online = True
                device.consecutive_failures = 0
                await self._alert_recovered(device)
            else:
                device.consecutive_failures = 0

            # Verificar latencia elevada
            if latency_ms > device.max_latency_ms:
                await self._alert_high_latency(device, latency_ms)

        logger.debug(
            f"[Health] {device.device_name}: "
            f"{'✓' if reachable else '✗'} {latency_ms}ms"
        )

    async def _ping_tcp(self, host: str, port: int, timeout: float = 3.0) -> bool:
        """Verifica conectividad TCP."""
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=timeout
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except Exception:
            return False

    async def _alert_offline(self, device: DeviceHealth):
        """Dispara alerta de dispositivo offline."""
        msg = f"{device.device_name} sin respuesta — dispositivo offline"
        logger.warning(f"🔴 OFFLINE: {msg}")

        alert = HealthAlert(
            device_id=device.device_id,
            device_name=device.device_name,
            device_type=device.device_type,
            alert_type="offline",
            message=msg,
        )
        self._alert_history.append(alert)

        # Guardar en DB
        if self.db:
            self._save_alert_to_db(alert)

        # Notificación push
        if self.notifier:
            await self.notifier.notify_raw(
                title=f"🔴 {device.device_name} OFFLINE",
                body=msg,
                severity="high",
            )

    async def _alert_recovered(self, device: DeviceHealth):
        """Dispara alerta de dispositivo recuperado."""
        msg = f"{device.device_name} volvió a estar en línea"
        logger.info(f"🟢 RECUPERADO: {msg}")

        alert = HealthAlert(
            device_id=device.device_id,
            device_name=device.device_name,
            device_type=device.device_type,
            alert_type="recovered",
            message=msg,
        )
        self._alert_history.append(alert)

        if self.notifier:
            await self.notifier.notify_raw(
                title=f"🟢 {device.device_name} recuperado",
                body=msg,
                severity="low",
            )

    async def _alert_high_latency(self, device: DeviceHealth, latency_ms: int):
        """Dispara alerta de latencia elevada."""
        msg = f"{device.device_name} con latencia elevada: {latency_ms}ms"
        logger.warning(f"🟡 LATENCIA: {msg}")

        alert = HealthAlert(
            device_id=device.device_id,
            device_name=device.device_name,
            device_type=device.device_type,
            alert_type="high_latency",
            message=msg,
            latency_ms=latency_ms,
        )
        self._alert_history.append(alert)

    def _save_alert_to_db(self, alert: HealthAlert):
        """Guarda la alerta en la tabla system_config de la DB."""
        try:
            import json
            key = f"health_alert_{alert.device_id}_{int(alert.timestamp.timestamp())}"
            value = json.dumps({
                "device_name": alert.device_name,
                "alert_type":  alert.alert_type,
                "message":     alert.message,
                "timestamp":   alert.timestamp.isoformat(),
            })
            with self.db._connect() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO system_config (key, value, updated_at)
                    VALUES (?, ?, ?)
                """, (key, value, alert.timestamp.isoformat()))
                conn.commit()
        except Exception as e:
            logger.error(f"Error guardando alerta en DB: {e}")

    def _extract_host(self, rtsp_url: str) -> str:
        try:
            without_scheme = rtsp_url.replace("rtsp://", "")
            if "@" in without_scheme:
                without_scheme = without_scheme.split("@")[1]
            return without_scheme.split("/")[0].split(":")[0]
        except Exception:
            return "localhost"
