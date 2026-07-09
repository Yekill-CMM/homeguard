"""
HomeGuard AI — Módulo de presencia WiFi y estados de armado
============================================================

Detecta qué teléfonos del hogar están conectados a la LAN mediante
escaneo ARP periódico, mantiene el estado de presencia por persona y
gestiona la máquina de estados de armado (desarmado / parcial / total).

Requisitos en el NUC:
    sudo apt install arp-scan
    # Permitir que arp-scan corra sin sudo desde el servicio:
    sudo setcap cap_net_raw+ep /usr/sbin/arp-scan

Uso básico (integración con el loop principal de homeguard):

    from presence import PresenceMonitor

    monitor = PresenceMonitor(db_path="~/homeguard/data/homeguard.db")
    monitor.start()          # hilo en background, escanea cada SCAN_INTERVAL

    # En el pipeline de eventos, antes de procesar un evento:
    if not monitor.should_process_event(event_type, camera_id):
        return  # sistema desarmado o evento filtrado por el modo actual

Registro de dispositivos (una vez, vía sqlite3 o endpoint):

    INSERT INTO presence_devices (person_name, device_name, mac, notify_arrival)
    VALUES ('Claudio', 'iPhone de Claudio', 'AA:BB:CC:DD:EE:FF', 1);

Nota iPhone/Android: usar la MAC que el teléfono presenta EN LA RED DE CASA.
iOS usa la misma "dirección privada" de forma estable por red, así que basta
con leerla en Ajustes > Wi-Fi > (i) de la red, o desactivar dirección privada
para esa red. Si el usuario la rota manualmente hay que re-registrarla.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass
from fastapi import Request as FastAPIRequest
from datetime import datetime, timedelta
from typing import Callable, Optional

log = logging.getLogger("homeguard.presence")

# ----------------------------------------------------------------------------
# Configuración
# ----------------------------------------------------------------------------

SCAN_INTERVAL = 45          # segundos entre escaneos ARP
AWAY_TIMEOUT = 25 * 60      # seg sin ver el dispositivo antes de marcar ausente
                            # (los iPhone se desconectan del WiFi al dormir;
                            #  un timeout corto genera salidas fantasma)
ARRIVAL_DEBOUNCE = 60       # seg mínimos ausente antes de notificar una llegada
                            # (evita ráfagas si el teléfono parpadea en la red)
NETWORK_CIDR = "192.168.1.0/24"
ARP_SCAN_BIN = "/usr/sbin/arp-scan"

# Modos de armado
DISARMED = "disarmed"       # gente en casa, día: solo eventos safety
ARMED_PARTIAL = "partial"   # noche con gente durmiendo: perímetro + safety
ARMED_FULL = "full"         # casa vacía: todo activo

# Qué tipos de evento se procesan en cada modo.
# Ajustar a los event_type reales de events/database.py.
# Eventos de safety: NUNCA se filtran, en ningún modo (garantía 24/7).
SAFETY_ALWAYS = {"fire", "gas", "smoke", "co2", "tamper"}

EVENT_POLICY = {
    DISARMED: {"fire", "gas", "tamper"},                  # safety siempre (futuro)
    ARMED_PARTIAL: {"fire", "gas", "tamper",
                    "intrusion"},                          # perímetro de noche
    ARMED_FULL: {"fire", "gas", "tamper",
                 "intrusion", "person", "motion"},         # todo
}
EVENT_POLICY = {m: s | SAFETY_ALWAYS for m, s in EVENT_POLICY.items()}

# Cámaras interiores que se ignoran en modo parcial (IDs de la tabla cameras).
# Vacío = en parcial aplican todas las cámaras según EVENT_POLICY.
PARTIAL_IGNORED_CAMERAS: set[int] = set()

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


# ----------------------------------------------------------------------------
# Esquema
# ----------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS presence_devices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    person_name     TEXT NOT NULL,
    device_name     TEXT NOT NULL,
    mac             TEXT NOT NULL UNIQUE COLLATE NOCASE,
    is_home         INTEGER NOT NULL DEFAULT 0,
    last_seen       TEXT,                -- ISO 8601, última vez vista la MAC
    last_arrival    TEXT,                -- ISO 8601, última transición a casa
    last_departure  TEXT,                -- ISO 8601, última transición a ausente
    notify_arrival  INTEGER NOT NULL DEFAULT 1,
    enabled         INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS arm_state (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    mode        TEXT NOT NULL,           -- disarmed | partial | full
    changed_at  TEXT NOT NULL,           -- ISO 8601
    changed_by  TEXT NOT NULL            -- 'auto' | 'user:<nombre>' | 'schedule'
);

CREATE TABLE IF NOT EXISTS presence_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   INTEGER NOT NULL REFERENCES presence_devices(id),
    event       TEXT NOT NULL,           -- 'arrival' | 'departure'
    at          TEXT NOT NULL            -- ISO 8601
);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Device:
    id: int
    person_name: str
    device_name: str
    mac: str
    is_home: bool
    last_seen: Optional[datetime]
    notify_arrival: bool


# ----------------------------------------------------------------------------
# Escaneo de la LAN
# ----------------------------------------------------------------------------

def mac_from_ip(ip: str) -> str | None:
    """Devuelve la MAC asociada a una IP consultando la tabla ARP del kernel.
    Primero intenta ip neigh (instantáneo), luego arp-scan dirigido si no
    encuentra resultado — el dispositivo acaba de hacer una petición HTTP
    así que su entrada ARP debería estar fresca.
    """
    try:
        out = subprocess.run(
            ["ip", "neigh", "show", ip],
            capture_output=True, text=True, timeout=5
        )
        for line in out.stdout.splitlines():
            for token in line.split():
                if MAC_RE.match(token):
                    return token.upper()
    except Exception as exc:
        log.warning("ip neigh fallo para %s: %s", ip, exc)

    # Fallback: arp-scan dirigido a la IP específica
    try:
        out = subprocess.run(
            [ARP_SCAN_BIN, "--localnet", "--quiet", "--ignoredups", ip],
            capture_output=True, text=True, timeout=10
        )
        for line in out.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and MAC_RE.match(parts[1].strip()):
                return parts[1].strip().upper()
    except Exception as exc:
        log.warning("arp-scan dirigido fallo para %s: %s", ip, exc)

    return None


def scan_lan_macs() -> set[str]:
    """Devuelve el set de MACs visibles en la LAN (mayúsculas).

    Estrategia primaria: arp-scan (activo, despierta a los dispositivos).
    Fallback: tabla de vecinos del kernel (`ip neigh`), pasiva y menos
    confiable, pero no requiere capacidades especiales.
    """
    macs: set[str] = set()
    try:
        out = subprocess.run(
            [ARP_SCAN_BIN, "--localnet", "--quiet", "--ignoredups",
             "--retry=3", "--timeout=500"],
            capture_output=True, text=True, timeout=30,
        )
        for line in out.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and MAC_RE.match(parts[1].strip()):
                macs.add(parts[1].strip().upper())
        if macs:
            return macs
        log.warning("arp-scan no devolvió resultados, usando ip neigh")
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log.warning("arp-scan no disponible (%s), usando ip neigh", exc)

    try:
        out = subprocess.run(["ip", "neigh", "show"],
                             capture_output=True, text=True, timeout=10)
        for line in out.stdout.splitlines():
            if "REACHABLE" in line or "STALE" in line or "DELAY" in line:
                for token in line.split():
                    if MAC_RE.match(token):
                        macs.add(token.upper())
    except Exception as exc:  # noqa: BLE001 — fallback best-effort
        log.error("Fallo al leer ip neigh: %s", exc)
    return macs


# ----------------------------------------------------------------------------
# Monitor de presencia + máquina de estados
# ----------------------------------------------------------------------------

class PresenceMonitor:
    """Escanea la LAN, mantiene presencia por persona y gestiona el armado.

    Callbacks opcionales (para notificaciones push, logs, etc.):
        on_arrival(device)            — alguien llegó a casa
        on_departure(device)          — alguien se fue
        on_mode_change(old, new, by)  — cambió el modo de armado
    """

    def __init__(
        self,
        db_path: str,
        auto_arm: bool = True,
        on_arrival: Optional[Callable[[Device], None]] = None,
        on_departure: Optional[Callable[[Device], None]] = None,
        on_mode_change: Optional[Callable[[str, str, str], None]] = None,
    ) -> None:
        self.db_path = os.path.expanduser(db_path)
        self.auto_arm = auto_arm
        self.on_arrival = on_arrival
        self.on_departure = on_departure
        self.on_mode_change = on_mode_change
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._init_db()

    # -- infraestructura -----------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            cur = conn.execute("SELECT COUNT(*) AS n FROM arm_state")
            if cur.fetchone()["n"] == 0:
                conn.execute(
                    "INSERT INTO arm_state (mode, changed_at, changed_by) "
                    "VALUES (?, ?, ?)",
                    (DISARMED, _now(), "init"),
                )

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="presence-monitor", daemon=True
        )
        self._thread.start()
        log.info("Monitor de presencia iniciado (cada %ss)", SCAN_INTERVAL)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_once()
            except Exception:  # noqa: BLE001 — el loop no debe morir
                log.exception("Error en ciclo de presencia")
            self._stop.wait(SCAN_INTERVAL)

    # -- lógica de presencia ---------------------------------------------------

    def scan_once(self) -> None:
        """Un ciclo: escanea la LAN y actualiza presencia + auto-armado."""
        visible = scan_lan_macs()
        now = datetime.now()
        arrivals: list[Device] = []
        departures: list[Device] = []

        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM presence_devices WHERE enabled = 1"
            ).fetchall()

            for row in rows:
                dev = self._row_to_device(row)
                seen_now = dev.mac.upper() in visible

                if seen_now:
                    conn.execute(
                        "UPDATE presence_devices SET last_seen = ? WHERE id = ?",
                        (now.isoformat(timespec="seconds"), dev.id),
                    )
                    if not dev.is_home:
                        # Transición ausente -> en casa (llegada)
                        away_long_enough = (
                            dev.last_seen is None
                            or (now - dev.last_seen).total_seconds()
                            > ARRIVAL_DEBOUNCE
                        )
                        if away_long_enough:
                            conn.execute(
                                "UPDATE presence_devices "
                                "SET is_home = 1, last_arrival = ? WHERE id = ?",
                                (now.isoformat(timespec="seconds"), dev.id),
                            )
                            conn.execute(
                                "INSERT INTO presence_log (device_id, event, at)"
                                " VALUES (?, 'arrival', ?)",
                                (dev.id, now.isoformat(timespec="seconds")),
                            )
                            dev.is_home = True
                            arrivals.append(dev)
                else:
                    if dev.is_home and dev.last_seen is not None:
                        # Solo marcar ausente tras AWAY_TIMEOUT sin verlo
                        # (los teléfonos duermen y sueltan el WiFi)
                        if (now - dev.last_seen).total_seconds() > AWAY_TIMEOUT:
                            conn.execute(
                                "UPDATE presence_devices "
                                "SET is_home = 0, last_departure = ? "
                                "WHERE id = ?",
                                (now.isoformat(timespec="seconds"), dev.id),
                            )
                            conn.execute(
                                "INSERT INTO presence_log (device_id, event, at)"
                                " VALUES (?, 'departure', ?)",
                                (dev.id, now.isoformat(timespec="seconds")),
                            )
                            dev.is_home = False
                            departures.append(dev)

        # Callbacks fuera del lock/transacción
        for dev in arrivals:
            log.info("Llegada detectada: %s (%s)", dev.person_name, dev.device_name)
            if dev.notify_arrival and self.on_arrival:
                self.on_arrival(dev)
        for dev in departures:
            log.info("Salida detectada: %s (%s)", dev.person_name, dev.device_name)
            if self.on_departure:
                self.on_departure(dev)

        if self.auto_arm and (arrivals or departures):
            self._auto_arm()

    @staticmethod
    def _row_to_device(row: sqlite3.Row) -> Device:
        last_seen = (
            datetime.fromisoformat(row["last_seen"]) if row["last_seen"] else None
        )
        return Device(
            id=row["id"],
            person_name=row["person_name"],
            device_name=row["device_name"],
            mac=row["mac"],
            is_home=bool(row["is_home"]),
            last_seen=last_seen,
            notify_arrival=bool(row["notify_arrival"]),
        )

    # -- máquina de estados de armado -----------------------------------------

    def anyone_home(self) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM presence_devices "
                "WHERE enabled = 1 AND is_home = 1"
            ).fetchone()
            return row["n"] > 0

    def get_mode(self) -> str:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT mode FROM arm_state ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return row["mode"] if row else DISARMED

    def set_mode(self, mode: str, changed_by: str = "user") -> str:
        if mode not in (DISARMED, ARMED_PARTIAL, ARMED_FULL):
            raise ValueError(f"Modo inválido: {mode}")
        old = self.get_mode()
        if old == mode:
            return old
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO arm_state (mode, changed_at, changed_by) "
                "VALUES (?, ?, ?)",
                (mode, _now(), changed_by),
            )
        log.info("Modo de armado: %s -> %s (%s)", old, mode, changed_by)
        if self.on_mode_change:
            self.on_mode_change(old, mode, changed_by)
        return mode

    def _auto_arm(self) -> None:
        """Regla conservadora de auto-armado:
        - Si NADIE está en casa (todos ausentes por WiFi) -> armado total.
        - Si alguien llega y el sistema estaba en total -> desarmar.
        El modo parcial (nocturno) no se toca automáticamente: lo gestiona
        el usuario o un horario, no la presencia.
        """
        mode = self.get_mode()
        home = self.anyone_home()
        if not home and mode == DISARMED:
            self.set_mode(ARMED_FULL, changed_by="auto")
        elif home and mode == ARMED_FULL:
            self.set_mode(DISARMED, changed_by="auto")

    # -- filtro para el pipeline de eventos ------------------------------------

    def should_process_event(self, event_type: str, camera_id: int) -> bool:
        """Decide si un evento debe procesarse según el modo actual.

        Llamar ANTES del pre-filtro YOLO: si devuelve False se evita
        inferencia local Y costo de API.
        """
        mode = self.get_mode()
        allowed = EVENT_POLICY.get(mode, EVENT_POLICY[ARMED_FULL])
        if event_type not in allowed:
            return False
        if mode == ARMED_PARTIAL and camera_id in PARTIAL_IGNORED_CAMERAS:
            return False
        return True

    # -- consultas para la API / dashboard --------------------------------------

    def status(self) -> dict:
        with self._conn() as conn:
            devices = [
                dict(row)
                for row in conn.execute(
                    "SELECT id, person_name, device_name, is_home, "
                    "last_seen, last_arrival, last_departure "
                    "FROM presence_devices WHERE enabled = 1 "
                    "ORDER BY person_name"
                ).fetchall()
            ]
        return {
            "mode": self.get_mode(),
            "anyone_home": self.anyone_home(),
            "devices": devices,
        }


# ----------------------------------------------------------------------------
# Prueba manual: python presence.py
# ----------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    monitor = PresenceMonitor(db_path="~/homeguard/data/homeguard.db",
                              auto_arm=False)
    print("MACs visibles en la LAN:")
    for mac in sorted(scan_lan_macs()):
        print("  ", mac)
    print("\nEjecutando un ciclo de presencia...")
    monitor.scan_once()
    print("\nEstado:", monitor.status())


# ----------------------------------------------------------------------------
# Rutas FastAPI (mismo patrón que add_push_routes)
# ----------------------------------------------------------------------------

def add_presence_routes(app, monitor: "PresenceMonitor") -> None:
    from fastapi import Body, Request, Depends
    from fastapi.responses import JSONResponse
    from dashboard.api import require_auth, require_role

    @app.get("/api/presence")
    async def presence_status(user: dict = Depends(require_auth)):
        return monitor.status()

    @app.post("/api/arm-state")
    async def set_arm_state(payload: dict = Body(...), user: dict = Depends(require_auth)):
        mode = payload.get("mode", "")
        by = payload.get("by", "dashboard")
        try:
            new_mode = monitor.set_mode(mode, changed_by=f"user:{by}")
            return {"ok": True, "mode": new_mode}
        except ValueError as exc:
            return JSONResponse(status_code=400,
                                content={"ok": False, "error": str(exc)})

    @app.get("/api/presence/log")
    async def presence_log(limit: int = 50, user: dict = Depends(require_auth)):
        with monitor._conn() as conn:
            rows = conn.execute(
                "SELECT pl.at, pd.person_name, pd.device_name, pl.event "
                "FROM presence_log pl "
                "JOIN presence_devices pd ON pd.id = pl.device_id "
                "ORDER BY pl.id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    @app.get("/api/presence/devices")
    async def list_presence_devices(user: dict = Depends(require_auth)):
        """Lista todos los dispositivos de presencia."""
        with monitor._conn() as conn:
            rows = conn.execute(
                "SELECT id, person_name, device_name, mac, is_home, "
                "last_seen, last_arrival, last_departure, enabled "
                "FROM presence_devices ORDER BY person_name"
            ).fetchall()
        return [dict(r) for r in rows]

    @app.delete("/api/presence/devices/{device_id}")
    async def delete_presence_device(device_id: int, user: dict = Depends(require_role("admin"))):
        """Elimina un dispositivo de presencia."""
        with monitor._conn() as conn:
            row = conn.execute(
                "SELECT id FROM presence_devices WHERE id = ?", (device_id,)
            ).fetchone()
            if not row:
                from fastapi.responses import JSONResponse
                return JSONResponse(status_code=404,
                    content={"ok": False, "error": "Dispositivo no encontrado"})
            conn.execute("DELETE FROM presence_log WHERE device_id = ?", (device_id,))
            conn.execute("DELETE FROM presence_devices WHERE id = ?", (device_id,))
            conn.commit()
        log.info("Dispositivo de presencia eliminado: id=%s", device_id)
        return {"ok": True}

    @app.patch("/api/presence/devices/{device_id}")
    async def update_presence_device(device_id: int, payload: dict = Body(...), user: dict = Depends(require_role("admin"))):
        """Actualiza nombre o estado habilitado de un dispositivo."""
        allowed = {"person_name", "device_name", "enabled"}
        updates = {k: v for k, v in payload.items() if k in allowed}
        if not updates:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=400,
                content={"ok": False, "error": "Sin campos válidos para actualizar"})
        sets = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [device_id]
        with monitor._conn() as conn:
            conn.execute(f"UPDATE presence_devices SET {sets} WHERE id = ?", vals)
            conn.commit()
        return {"ok": True}

    @app.get("/api/presence/check")
    async def check_device(req: FastAPIRequest):
        """Verifica si este dispositivo ya está registrado por su MAC/IP."""
        client_ip = req.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if not client_ip:
            client_ip = req.client.host if req.client else ""
        if not client_ip:
            return {"registered": False, "reason": "no_ip"}
        mac = mac_from_ip(client_ip)
        if not mac:
            return {"registered": False, "reason": "no_mac", "ip": client_ip}
        with monitor._conn() as conn:
            row = conn.execute(
                "SELECT person_name FROM presence_devices WHERE mac = ? AND enabled = 1",
                (mac,)
            ).fetchone()
        if row:
            return {"registered": True, "person_name": row["person_name"], "mac": mac}
        return {"registered": False, "reason": "not_found", "mac": mac}

    @app.post("/api/presence/register")
    async def register_device(request: Request, payload: dict = Body(...)):
        """Registra un dispositivo móvil detectando su MAC por IP de origen.
        Payload: { person_name, device_name }
        """
        # IP real del cliente (detrás de Tailscale/proxy)
        client_ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if not client_ip:
            client_ip = request.client.host if request.client else ""

        if not client_ip:
            return JSONResponse(status_code=400,
                content={"ok": False, "error": "No se pudo determinar la IP del dispositivo"})

        person_name = payload.get("person_name", "").strip()
        device_name = payload.get("device_name", "").strip()
        if not person_name:
            return JSONResponse(status_code=400,
                content={"ok": False, "error": "Nombre requerido"})

        # Resolver MAC desde la IP
        mac = mac_from_ip(client_ip)
        if not mac:
            return JSONResponse(status_code=422, content={
                "ok": False,
                "error": "No se pudo obtener la MAC de este dispositivo. "
                         "Asegúrate de estar conectado al WiFi del hogar.",
                "ip": client_ip,
            })

        # Verificar si ya existe
        with monitor._conn() as conn:
            existing = conn.execute(
                "SELECT id, person_name FROM presence_devices WHERE mac = ?",
                (mac,)
            ).fetchone()
            if existing:
                return {"ok": True, "already_registered": True,
                        "person_name": existing["person_name"], "mac": mac}

            conn.execute(
                "INSERT INTO presence_devices "
                "(person_name, device_name, mac, enabled, notify_arrival) "
                "VALUES (?, ?, ?, 1, 1)",
                (person_name, device_name or f"Móvil de {person_name}", mac)
            )
            conn.commit()

        log.info("Dispositivo registrado via QR: %s (%s) MAC=%s IP=%s",
                 person_name, device_name, mac, client_ip)
        return {"ok": True, "already_registered": False,
                "person_name": person_name, "mac": mac, "ip": client_ip}
