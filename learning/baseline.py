"""
learning/baseline.py — Scorer de anomalías estadístico
Calcula qué tan inusual es un evento nuevo comparando contra el historial.
Score 0.0 = completamente normal | 1.0 = altamente anómalo
Sin dependencias externas — Python puro + SQLite.
"""
import math
import sqlite3
import statistics
import logging
from datetime import datetime

logger = logging.getLogger("homeguard.learning.baseline")

DOW_ES = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]


class AnomalyScorer:

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_tables()

    def _init_tables(self):
        """Crea tabla baseline_resets para auditoría."""
        try:
            import sqlite3
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS baseline_resets (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        camera_name TEXT NOT NULL,
                        reason      TEXT,
                        timestamp   TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.commit()
        except Exception as e:
            logger.warning(f"Error creating baseline_resets table: {e}")

    def score(self, camera_name: str, timestamp: datetime, event_type: str) -> dict:
        """
        Retorna:
            score       : float 0-1 (1 = muy anómalo)
            context     : str listo para inyectar en prompt de Claude
        """
        hour = timestamp.hour
        # SQLite %w: 0=domingo. Python weekday: 0=lunes
        sqlite_dow = (timestamp.weekday() + 1) % 7

        historical = self._historical_hourly_counts(camera_name, hour, sqlite_dow)
        current    = self._count_this_hour(camera_name, timestamp)

        if len(historical) < 3:
            return {
                "score": 0.5,
                "avg": 0,
                "current": current,
                "context": f"Historial insuficiente para {camera_name} — evento tratado como neutro"
            }

        avg = statistics.mean(historical)
        std = statistics.stdev(historical) if len(historical) > 1 else 0.0

        # Z-score → sigmoid → 0-1
        if std < 0.5:
            raw_score = 0.0 if current <= avg + 1 else 0.9
        else:
            z = (current - avg) / std
            raw_score = 1 / (1 + math.exp(-z))

        # Penalización nocturna (00:00-06:00)
        if 0 <= hour < 6:
            raw_score = min(1.0, raw_score * 1.4)

        score = round(raw_score, 3)
        context = self._build_context(camera_name, timestamp, avg, current, score)

        return {"score": score, "avg": round(avg, 1), "current": current, "context": context}

    def _historical_hourly_counts(self, camera_name: str, hour: int, sqlite_dow: int) -> list:
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute("""
                    SELECT strftime('%Y-%m-%d', timestamp) AS day, COUNT(*) AS cnt
                    FROM events
                    WHERE camera_name = ?
                      AND CAST(strftime('%H', timestamp) AS INTEGER) = ?
                      AND CAST(strftime('%w', timestamp) AS INTEGER) = ?
                    GROUP BY day
                    ORDER BY day DESC
                    LIMIT 45
                """, (camera_name, hour, sqlite_dow)).fetchall()
            return [r[1] for r in rows]
        except Exception as e:
            logger.warning(f"baseline query error: {e}")
            return []

    def _count_this_hour(self, camera_name: str, ts: datetime) -> int:
        start = ts.strftime('%Y-%m-%dT%H:00:00')
        end   = ts.strftime('%Y-%m-%dT%H:59:59')
        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM events WHERE camera_name=? AND timestamp BETWEEN ? AND ?",
                    (camera_name, start, end)
                ).fetchone()
            return row[0] if row else 0
        except Exception:
            return 0

    def _build_context(self, camera_name, ts, avg, current, score) -> str:
        day  = DOW_ES[ts.weekday()]
        hour = ts.hour

        if score >= 0.85:
            nivel = f"ACTIVIDAD MUY INUSUAL (score anomalía: {score:.2f})"
        elif score >= 0.65:
            nivel = f"actividad inusualmente alta (score: {score:.2f})"
        elif score >= 0.4:
            nivel = f"actividad moderada (score: {score:.2f})"
        else:
            nivel = f"actividad dentro del patrón normal (score: {score:.2f})"

        if 0 <= hour < 6:
            nivel = f"⚠️ HORARIO NOCTURNO + {nivel}"

        return (
            f"Patrón estadístico — {day} {hour:02d}:xx en {camera_name}: "
            f"{nivel}. Promedio histórico: {avg:.0f} eventos/hora, "
            f"actual: {current} eventos esta hora."
        )
