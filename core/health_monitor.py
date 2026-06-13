"""
HomeGuard AI — Monitor de salud del sistema
Detecta cámaras caídas, sensores sin señal y latencia elevada.
Supervisa disco local e internet.
Genera alertas y notificaciones push cuando un dispositivo falla.
"""

import asyncio
import logging
import shutil
import socket
import time
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Umbrales de disco ────────────────────────────────────────────────────────
DISK_WARNING_PCT  = 80   # % uso → WARNING
DISK_CRITICAL_PCT = 90   # % uso → CRITICAL


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
    alert_type: str         # offline | high_latency | recovered | disk | internet
    message: str
    timestamp: datetime = field(default_factory=datetime.now)
    latency_ms: int = 0


class HealthMonitor:
    """
    Monitor de salud de todos los dispositivos del sistema.
    Corre en background y genera alertas cuando detecta problemas.
    """

    def __init__(
        self,
        notifier=None,
        db=None,
        check_interval: int = 30,
        data_dir: Optional[str] = None,
    ):
        self.notifier = notifier
        self.db = db
        self.check_interval = check_interval
        self.data_dir = str(Path(data_dir or "~/homeguard/data").expanduser())

        self._devices: dict[str, DeviceHealth] = {}
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._alert_history: list[HealthAlert] = []

        # Estado previo de disco e internet (evita spam de alertas)
        self._prev_internet_ok: Optional[bool] = None
        self._prev_disk_status: Optional[str]  = None   # "OK" | "WARNING" | "CRITICAL"

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

    # ── Loop principal ───────────────────────────────────────────────────────

    async def _monitor_loop(self):
        """Loop principal de monitoreo."""
        while self._running:
            await asyncio.gather(
                *[self._check_device(device) for device in list(self._devices.values())],
                self._check_internet(),
                self._check_disk(),
            )
            await asyncio.sleep(self.check_interval)

    # ── Chequeos de dispositivos ─────────────────────────────────────────────

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
                if device.online:
                    device.online = False
                    await self._alert_offline(device)

        else:
            device.last_seen = datetime.now()

            if not device.online:
                device.online = True
                device.consecutive_failures = 0
                await self._alert_recovered(device)
            else:
                device.consecutive_failures = 0

            if latency_ms > device.max_latency_ms:
                await self._alert_high_latency(device, latency_ms)

        logger.debug(
            f"[Health] {device.device_name}: "
            f"{'OK' if reachable else 'FAIL'} {latency_ms}ms"
        )

    async def _ping_tcp(self, host: str, port: int, timeout: float = 3.0) -> bool:
        """Verifica conectividad TCP."""
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=timeout,
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except Exception:
            return False

    # ── Chequeo de internet ──────────────────────────────────────────────────

    async def _check_internet(self) -> None:
        """
        Verifica acceso a internet vía DNS lookup + fallback TCP.
        Solo emite alerta cuando el estado cambia.
        """
        ok = await asyncio.get_event_loop().run_in_executor(None, self._internet_reachable)

        if self._prev_internet_ok is None:
            # Primera pasada: solo loguear si hay problema
            if not ok:
                logger.warning("[Health] Sin acceso a internet al iniciar")
                await self._alert_system(
                    component="internet",
                    alert_type="internet",
                    message="Sin acceso a internet — sistema en modo offline",
                    severity="high",
                )
        elif self._prev_internet_ok and not ok:
            # Perdió internet
            logger.warning("[Health] 🔴 Internet: conexión perdida")
            await self._alert_system(
                component="internet",
                alert_type="internet",
                message="Conexión a internet perdida — operando en modo offline",
                severity="high",
            )
        elif not self._prev_internet_ok and ok:
            # Recuperó internet
            logger.info("[Health] ✅ Internet: conexión restaurada")
            await self._alert_system(
                component="internet",
                alert_type="recovered",
                message="Conexión a internet restaurada",
                severity="low",
            )

        self._prev_internet_ok = ok

    def _internet_reachable(self) -> bool:
        """Bloquea el hilo brevemente; se llama desde executor para no bloquear el loop."""
        # Intento 1: DNS lookup
        try:
            socket.setdefaulttimeout(5)
            socket.getaddrinfo("dns.google", 80)
            return True
        except socket.gaierror:
            pass
        finally:
            socket.setdefaulttimeout(None)
        # Intento 2: TCP directo a 8.8.8.8:53
        try:
            with socket.create_connection(("8.8.8.8", 53), timeout=5):
                return True
        except OSError:
            return False

    # ── Chequeo de disco ─────────────────────────────────────────────────────

    async def _check_disk(self) -> None:
        """
        Verifica uso de disco en el directorio de datos.
        Solo emite alerta cuando el estado cambia o empeora.
        """
        try:
            usage   = shutil.disk_usage(self.data_dir)
            pct     = usage.used / usage.total * 100
            free_gb = usage.free / (1024 ** 3)
        except Exception as exc:
            logger.warning(f"[Health] No se pudo verificar disco: {exc}")
            return

        if pct >= DISK_CRITICAL_PCT:
            new_status = "CRITICAL"
        elif pct >= DISK_WARNING_PCT:
            new_status = "WARNING"
        else:
            new_status = "OK"

        # Solo actuar si el estado cambió
        if new_status == self._prev_disk_status:
            return

        if new_status == "CRITICAL":
            msg = f"Disco crítico: {pct:.1f}% usado — solo {free_gb:.1f} GB libres"
            logger.error(f"[Health] 🔴 {msg}")
            await self._alert_system(
                component="disk",
                alert_type="disk",
                message=msg,
                severity="high",
            )
        elif new_status == "WARNING":
            msg = f"Disco al {pct:.1f}% — {free_gb:.1f} GB libres"
            logger.warning(f"[Health] 🟡 {msg}")
            await self._alert_system(
                component="disk",
                alert_type="disk",
                message=msg,
                severity="medium",
            )
        elif new_status == "OK" and self._prev_disk_status is not None:
            # Solo loguear recuperación si antes había problema
            msg = f"Disco normalizado: {pct:.1f}% usado — {free_gb:.1f} GB libres"
            logger.info(f"[Health] ✅ {msg}")
            await self._alert_system(
                component="disk",
                alert_type="recovered",
                message=msg,
                severity="low",
            )

        self._prev_disk_status = new_status

    # ── Métodos de alerta ────────────────────────────────────────────────────

    async def _alert_offline(self, device: DeviceHealth):
        msg = f"{device.device_name} sin respuesta — dispositivo offline"
        logger.warning(f"[Health] OFFLINE: {msg}")

        alert = HealthAlert(
            device_id=device.device_id,
            device_name=device.device_name,
            device_type=device.device_type,
            alert_type="offline",
            message=msg,
        )
        self._alert_history.append(alert)
        if self.db:
            self._save_alert_to_db(alert)
        if self.notifier:
            await self.notifier.notify_raw(
                title=f"🔴 {device.device_name} OFFLINE",
                body=msg,
                severity="high",
            )

    async def _alert_recovered(self, device: DeviceHealth):
        msg = f"{device.device_name} volvió a estar en línea"
        logger.info(f"[Health] RECUPERADO: {msg}")

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
        msg = f"{device.device_name} con latencia elevada: {latency_ms}ms"
        logger.warning(f"[Health] LATENCIA: {msg}")

        alert = HealthAlert(
            device_id=device.device_id,
            device_name=device.device_name,
            device_type=device.device_type,
            alert_type="high_latency",
            message=msg,
            latency_ms=latency_ms,
        )
        self._alert_history.append(alert)

    async def _alert_system(
        self, component: str, alert_type: str, message: str, severity: str
    ) -> None:
        """Alerta genérica para componentes de sistema (disco, internet)."""
        alert = HealthAlert(
            device_id=component,
            device_name=component.capitalize(),
            device_type="system",
            alert_type=alert_type,
            message=message,
        )
        self._alert_history.append(alert)
        if self.db:
            self._save_alert_to_db(alert)
        if self.notifier and severity in ("high", "medium"):
            icon = "🔴" if severity == "high" else "🟡"
            await self.notifier.notify_raw(
                title=f"{icon} Sistema — {component.capitalize()}",
                body=message,
                severity=severity,
            )

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
                conn.execute(
                    """INSERT OR REPLACE INTO system_config (key, value, updated_at)
                       VALUES (?, ?, ?)""",
                    (key, value, alert.timestamp.isoformat()),
                )
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
