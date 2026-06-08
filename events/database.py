"""
HomeGuard AI — Base de datos SQLite
Persiste todos los SecurityEvents, snapshots y estadísticas del sistema.
SQLite para desarrollo/MVP → PostgreSQL para producción multi-instalación.
"""

import sqlite3
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional
from contextlib import contextmanager
from pathlib import Path

from core.event import SecurityEvent, SourceType, EventType, Severity

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------
# Schema SQL
# -----------------------------------------------------------------------

SCHEMA = """
-- Eventos de seguridad
CREATE TABLE IF NOT EXISTS events (
    id              TEXT PRIMARY KEY,
    camera_id       TEXT NOT NULL,
    camera_name     TEXT NOT NULL,
    timestamp       TEXT NOT NULL,      -- ISO 8601
    source_type     TEXT NOT NULL,      -- edge | stream | sensor | webhook
    event_type      TEXT NOT NULL,      -- person | vehicle | motion | ...
    severity        TEXT NOT NULL,      -- low | medium | high | critical
    confidence      REAL DEFAULT 0.0,
    snapshot_path   TEXT,               -- Ruta al JPEG en disco
    needs_ai        INTEGER DEFAULT 1,  -- 0 = false, 1 = true
    ai_description  TEXT,
    ai_alert        INTEGER DEFAULT 0,
    ai_alert_reason TEXT,
    ai_ms           INTEGER,            -- Tiempo de respuesta API (ms)
    raw_metadata    TEXT,               -- JSON string
    created_at      TEXT NOT NULL       -- Timestamp de inserción
);

-- Índices para consultas frecuentes del dashboard
CREATE INDEX IF NOT EXISTS idx_events_timestamp   ON events(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_events_camera      ON events(camera_id);
CREATE INDEX IF NOT EXISTS idx_events_type        ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_severity    ON events(severity);
CREATE INDEX IF NOT EXISTS idx_events_alert       ON events(ai_alert);

-- Cámaras registradas en el sistema
CREATE TABLE IF NOT EXISTS cameras (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    rtsp_url        TEXT,
    source_type     TEXT NOT NULL,      -- rtsp | onvif | mqtt
    enabled         INTEGER DEFAULT 1,
    last_seen       TEXT,
    created_at      TEXT NOT NULL
);

-- Estadísticas diarias (para dashboard y reportes)
CREATE TABLE IF NOT EXISTS daily_stats (
    date            TEXT NOT NULL,      -- YYYY-MM-DD
    camera_id       TEXT NOT NULL,
    total_events    INTEGER DEFAULT 0,
    alerts          INTEGER DEFAULT 0,
    persons         INTEGER DEFAULT 0,
    vehicles        INTEGER DEFAULT 0,
    animals         INTEGER DEFAULT 0,
    false_alarms    INTEGER DEFAULT 0,
    ai_calls        INTEGER DEFAULT 0,
    PRIMARY KEY (date, camera_id)
);

-- Configuración del sistema (key-value)
CREATE TABLE IF NOT EXISTS system_config (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
"""


# -----------------------------------------------------------------------
# Clase principal
# -----------------------------------------------------------------------

class EventDatabase:
    """
    Base de datos SQLite para HomeGuard AI.
    Thread-safe mediante check_same_thread=False + WAL mode.
    """

    def __init__(self, db_path: str = "./data/homeguard.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        logger.info(f"Base de datos iniciada: {db_path}")

    # ------------------------------------------------------------------
    # Inicialización
    # ------------------------------------------------------------------

    def _init_db(self):
        """Crea las tablas si no existen y activa WAL para concurrencia."""
        with self._connect() as conn:
            # WAL permite lecturas mientras se escribe — importante para el dashboard
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(SCHEMA)
            conn.commit()

    @contextmanager
    def _connect(self):
        """Context manager para conexiones SQLite thread-safe."""
        conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        conn.row_factory = sqlite3.Row  # Acceso por nombre de columna
        try:
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Eventos
    # ------------------------------------------------------------------

    def save_event(self, event: SecurityEvent) -> bool:
        """Persiste un SecurityEvent en la base de datos."""
        try:
            with self._connect() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO events (
                        id, camera_id, camera_name, timestamp,
                        source_type, event_type, severity, confidence,
                        snapshot_path, needs_ai, ai_description,
                        ai_alert, ai_alert_reason, ai_ms,
                        raw_metadata, created_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?,
                        ?, ?
                    )
                """, (
                    event.id,
                    event.camera_id,
                    event.camera_name,
                    event.timestamp.isoformat(),
                    event.source_type.value,
                    event.event_type.value,
                    event.severity.value,
                    event.confidence,
                    event.snapshot_path,
                    1 if event.needs_ai_analysis else 0,
                    event.ai_description,
                    1 if event.ai_alert else 0,
                    event.ai_alert_reason,
                    event.ai_analysis_ms,
                    json.dumps(event.raw_metadata),
                    datetime.now().isoformat(),
                ))
                conn.commit()

            # Actualizar estadísticas diarias
            self._update_daily_stats(event)
            return True

        except sqlite3.Error as e:
            logger.error(f"Error guardando evento {event.id}: {e}")
            return False

    def get_events(
        self,
        camera_id: Optional[str] = None,
        event_type: Optional[str] = None,
        severity: Optional[str] = None,
        alerts_only: bool = False,
        hours: int = 24,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """
        Consulta eventos con filtros.
        Por defecto retorna las últimas 24 horas.
        """
        since = (datetime.now() - timedelta(hours=hours)).isoformat()

        conditions = ["timestamp >= ?"]
        params = [since]

        if camera_id:
            conditions.append("camera_id = ?")
            params.append(camera_id)
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)
        if severity:
            conditions.append("severity = ?")
            params.append(severity)
        if alerts_only:
            conditions.append("ai_alert = 1")

        where = " AND ".join(conditions)
        params += [limit, offset]

        with self._connect() as conn:
            rows = conn.execute(f"""
                SELECT * FROM events
                WHERE {where}
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
            """, params).fetchall()

        return [dict(row) for row in rows]

    def get_event(self, event_id: str) -> Optional[dict]:
        """Obtiene un evento por ID."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM events WHERE id = ?", (event_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_recent_alerts(self, limit: int = 20) -> list[dict]:
        """Últimas alertas para el dashboard."""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM events
                WHERE ai_alert = 1
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,)).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Estadísticas
    # ------------------------------------------------------------------

    def _update_daily_stats(self, event: SecurityEvent):
        """Actualiza contadores diarios tras cada evento."""
        date = event.timestamp.strftime("%Y-%m-%d")
        try:
            with self._connect() as conn:
                # Upsert de estadísticas
                conn.execute("""
                    INSERT INTO daily_stats (date, camera_id, total_events)
                    VALUES (?, ?, 0)
                    ON CONFLICT(date, camera_id) DO NOTHING
                """, (date, event.camera_id))

                conn.execute("""
                    UPDATE daily_stats SET total_events = total_events + 1
                    WHERE date = ? AND camera_id = ?
                """, (date, event.camera_id))

                if event.ai_alert:
                    conn.execute("""
                        UPDATE daily_stats SET alerts = alerts + 1
                        WHERE date = ? AND camera_id = ?
                    """, (date, event.camera_id))

                # Contadores por tipo de evento
                type_column = {
                    EventType.PERSON.value:   "persons",
                    EventType.VEHICLE.value:  "vehicles",
                    EventType.ANIMAL.value:   "animals",
                    EventType.NO_THREAT.value:"false_alarms",
                }.get(event.event_type.value)

                if type_column:
                    conn.execute(f"""
                        UPDATE daily_stats SET {type_column} = {type_column} + 1
                        WHERE date = ? AND camera_id = ?
                    """, (date, event.camera_id))

                if not event.needs_ai_analysis is False:
                    conn.execute("""
                        UPDATE daily_stats SET ai_calls = ai_calls + 1
                        WHERE date = ? AND camera_id = ?
                    """, (date, event.camera_id))

                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Error actualizando stats: {e}")

    def get_daily_stats(self, days: int = 7) -> list[dict]:
        """Estadísticas de los últimos N días para el dashboard."""
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM daily_stats
                WHERE date >= ?
                ORDER BY date DESC
            """, (since,)).fetchall()
        return [dict(row) for row in rows]

    def get_summary(self) -> dict:
        """Resumen global para el dashboard principal."""
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            alerts = conn.execute(
                "SELECT COUNT(*) FROM events WHERE ai_alert = 1"
            ).fetchone()[0]
            today = datetime.now().strftime("%Y-%m-%d")
            today_events = conn.execute(
                "SELECT COUNT(*) FROM events WHERE timestamp LIKE ?",
                (f"{today}%",)
            ).fetchone()[0]
            last_event = conn.execute(
                "SELECT timestamp, event_type, camera_name FROM events ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()

        return {
            "total_events":  total,
            "total_alerts":  alerts,
            "today_events":  today_events,
            "last_event":    dict(last_event) if last_event else None,
        }

    # ------------------------------------------------------------------
    # Cámaras
    # ------------------------------------------------------------------

    def register_camera(self, camera_id: str, name: str, rtsp_url: str, source_type: str):
        """Registra o actualiza una cámara en la base de datos."""
        with self._connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO cameras
                (id, name, rtsp_url, source_type, enabled, last_seen, created_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
            """, (
                camera_id, name, rtsp_url, source_type,
                datetime.now().isoformat(),
                datetime.now().isoformat(),
            ))
            conn.commit()

    def update_camera_last_seen(self, camera_id: str):
        with self._connect() as conn:
            conn.execute(
                "UPDATE cameras SET last_seen = ? WHERE id = ?",
                (datetime.now().isoformat(), camera_id)
            )
            conn.commit()

    def get_cameras(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM cameras").fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Mantenimiento
    # ------------------------------------------------------------------

    def cleanup_old_events(self, retention_days: int = 30) -> int:
        """Elimina eventos más antiguos que retention_days. Retorna cantidad eliminada."""
        cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat()
        with self._connect() as conn:
            result = conn.execute(
                "DELETE FROM events WHERE timestamp < ?", (cutoff,)
            )
            conn.commit()
            deleted = result.rowcount
        if deleted > 0:
            logger.info(f"Limpieza: {deleted} eventos eliminados (>{retention_days} días)")
        return deleted

    def vacuum(self):
        """Compacta la base de datos (libera espacio tras cleanup)."""
        with self._connect() as conn:
            conn.execute("VACUUM")
        logger.info("VACUUM completado")

    def db_size_mb(self) -> float:
        """Tamaño actual de la base de datos en MB."""
        try:
            return os.path.getsize(self.db_path) / (1024 * 1024)
        except FileNotFoundError:
            return 0.0
