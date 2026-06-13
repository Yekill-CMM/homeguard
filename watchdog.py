"""
watchdog.py — HomeGuard AI
Módulo de supervisión de salud del sistema.
Corre como daemon thread interno del servicio principal.

Supervisa:
  - Conectividad de cada cámara (TCP al puerto RTSP 554)
  - Acceso a internet (DNS + fallback TCP)
  - Uso de disco en el directorio de datos

Notifica vía:
  - Log a journalctl (logging estándar de Python)
  - Registro en tabla `health_events` del SQLite local

Diseño de eventos:
  - Solo emite un evento cuando el estado CAMBIA (sin spam en DB)
  - Heartbeat INFO cada intervalo (visible con journalctl -f)
  - Distingue recuperaciones para facilitar correlación
"""

import logging
import shutil
import socket
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constantes configurables
# ---------------------------------------------------------------------------

CHECK_INTERVAL_SECONDS = 60          # frecuencia de chequeo
CAMERA_CONNECT_TIMEOUT = 5           # segundos timeout por cámara
INTERNET_CONNECT_TIMEOUT = 5         # segundos timeout internet
DISK_WARNING_PCT  = 80               # % uso disco → WARNING
DISK_CRITICAL_PCT = 90               # % uso disco → CRITICAL


# ---------------------------------------------------------------------------
# WatchdogMonitor
# ---------------------------------------------------------------------------

class WatchdogMonitor:
    """
    Monitor de salud del sistema HomeGuard AI.

    Uso típico en main.py:
        watchdog = WatchdogMonitor(db_path=DB_PATH, cameras=cameras, data_dir=DATA_DIR)
        watchdog.start()
        ...
        watchdog.stop()   # en shutdown del servicio
    """

    def __init__(self, db_path: str, cameras: list[dict], data_dir: str):
        """
        Args:
            db_path:  Ruta al SQLite, ej. "/home/cmm1973/homeguard/data/homeguard.db"
            cameras:  Lista de dicts con keys: camera_id, name, ip
                      Ejemplo:
                        [{"camera_id": 1, "name": "Entrada", "ip": "192.168.1.110"}, ...]
            data_dir: Directorio a monitorear por espacio, ej. "~/homeguard/data"
        """
        self.db_path  = db_path
        self.cameras  = cameras
        self.data_dir = str(Path(data_dir).expanduser())

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        # Estado anterior por componente — solo se emite evento al cambiar
        self._prev_status: dict[str, str] = {}

        self._ensure_table()

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Arranca el watchdog como daemon thread."""
        self._thread = threading.Thread(
            target=self._loop,
            name="watchdog",
            daemon=True,
        )
        self._thread.start()
        logger.info("[WATCHDOG] Módulo de salud iniciado — intervalo %ds", CHECK_INTERVAL_SECONDS)

    def stop(self, timeout: float = 10.0) -> None:
        """Detiene el watchdog limpiamente."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        logger.info("[WATCHDOG] Módulo de salud detenido")

    # ------------------------------------------------------------------
    # Loop principal
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._run_checks()
            except Exception as exc:  # noqa: BLE001
                logger.error("[WATCHDOG] Error inesperado en ciclo de chequeo: %s", exc)
            self._stop_event.wait(timeout=CHECK_INTERVAL_SECONDS)

    def _run_checks(self) -> None:
        """Ejecuta todos los chequeos y emite eventos ante cambios de estado."""
        results: dict[str, tuple[str, str]] = {}

        # 1. Cámaras
        for cam in self.cameras:
            key = f"camera_{cam['camera_id']}"
            status, detail = self._check_camera(cam)
            results[key] = (status, detail)

        # 2. Internet
        results["internet"] = self._check_internet()

        # 3. Disco
        results["disk"] = self._check_disk()

        # Emitir eventos solo cuando cambia el estado
        for component, (status, detail) in results.items():
            self._evaluate(component, status, detail)

        # Heartbeat siempre visible en journalctl
        ok_count = sum(1 for s, _ in results.values() if s == "OK")
        total    = len(results)
        logger.info(
            "[WATCHDOG] Heartbeat — %d/%d OK | %s",
            ok_count,
            total,
            " | ".join(f"{k}={s}" for k, (s, _) in results.items()),
        )

    def _evaluate(self, component: str, status: str, detail: str) -> None:
        """Compara con estado previo y loguea solo si cambió."""
        prev = self._prev_status.get(component)

        if prev is None:
            # Primera pasada: solo registrar si hay problema
            if status != "OK":
                self._log_event(component, status, detail, is_recovery=False)
        elif prev != "OK" and status == "OK":
            # Recuperación
            self._log_event(component, status, detail, is_recovery=True)
        elif prev == "OK" and status != "OK":
            # Nuevo problema detectado
            self._log_event(component, status, detail, is_recovery=False)
        elif prev != status:
            # Cambio de severidad (WARNING → CRITICAL o viceversa)
            self._log_event(component, status, detail, is_recovery=False)
        # Sin cambio → silencio (solo el heartbeat INFO general)

        self._prev_status[component] = status

    # ------------------------------------------------------------------
    # Chequeos individuales
    # ------------------------------------------------------------------

    def _check_camera(self, cam: dict) -> tuple[str, str]:
        """Conectividad vía TCP al puerto RTSP 554."""
        ip   = cam["ip"]
        name = cam.get("name", ip)
        port = cam.get("rtsp_port", 554)
        try:
            with socket.create_connection((ip, port), timeout=CAMERA_CONNECT_TIMEOUT):
                return "OK", f"[{name}] Puerto RTSP {port} accesible"
        except socket.timeout:
            return "CRITICAL", f"[{name}] Timeout al conectar {ip}:{port}"
        except ConnectionRefusedError:
            return "CRITICAL", f"[{name}] Conexión rechazada en {ip}:{port}"
        except OSError as exc:
            return "CRITICAL", f"[{name}] Sin respuesta desde {ip}: {exc}"

    def _check_internet(self) -> tuple[str, str]:
        """DNS lookup a dns.google; fallback TCP a 8.8.8.8:53."""
        try:
            socket.setdefaulttimeout(INTERNET_CONNECT_TIMEOUT)
            socket.getaddrinfo("dns.google", 80)
            return "OK", "Conectividad a internet activa"
        except socket.gaierror:
            pass
        finally:
            socket.setdefaulttimeout(None)

        try:
            with socket.create_connection(("8.8.8.8", 53), timeout=INTERNET_CONNECT_TIMEOUT):
                return "OK", "DNS Google accesible (fallback TCP)"
        except OSError:
            return "CRITICAL", "Sin acceso a internet — servicio en modo offline"

    def _check_disk(self) -> tuple[str, str]:
        """Uso de disco en el directorio de datos."""
        try:
            usage   = shutil.disk_usage(self.data_dir)
            pct     = usage.used / usage.total * 100
            free_gb = usage.free / (1024 ** 3)

            if pct >= DISK_CRITICAL_PCT:
                return "CRITICAL", f"Disco al {pct:.1f}% — solo {free_gb:.1f} GB libres"
            if pct >= DISK_WARNING_PCT:
                return "WARNING",  f"Disco al {pct:.1f}% — {free_gb:.1f} GB libres"
            return "OK", f"Disco al {pct:.1f}% — {free_gb:.1f} GB libres"

        except Exception as exc:  # noqa: BLE001
            return "WARNING", f"No se pudo verificar disco: {exc}"

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------

    def _ensure_table(self) -> None:
        """Crea la tabla health_events si no existe."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS health_events (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp    TEXT    NOT NULL,
                    component    TEXT    NOT NULL,
                    status       TEXT    NOT NULL,
                    detail       TEXT,
                    is_recovery  INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_health_ts ON health_events(timestamp)"
            )
            conn.commit()

    def _log_event(
        self, component: str, status: str, detail: str, is_recovery: bool
    ) -> None:
        """Persiste el evento en DB y emite el nivel de log correspondiente."""
        ts = datetime.now().isoformat(timespec="seconds")

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO health_events (timestamp, component, status, detail, is_recovery)
                   VALUES (?, ?, ?, ?, ?)""",
                (ts, component, status, detail, int(is_recovery)),
            )
            conn.commit()

        if is_recovery:
            logger.info(  "[WATCHDOG] ✅ RECUPERADO  | %s | %s", component, detail)
        elif status == "CRITICAL":
            logger.error( "[WATCHDOG] 🔴 CRÍTICO     | %s | %s", component, detail)
        else:
            logger.warning("[WATCHDOG] 🟡 ADVERTENCIA | %s | %s", component, detail)
