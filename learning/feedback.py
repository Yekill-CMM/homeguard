"""
learning/feedback.py — Almacén de feedback del usuario
Guarda las correcciones del usuario (verdadero positivo / falso positivo)
y genera contexto textual para inyectar en el prompt de Claude.
"""
import sqlite3
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("homeguard.learning.feedback")


class FeedbackStore:

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init()

    def _init(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS event_feedback (
                        id              INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_id        TEXT    NOT NULL,
                        camera_name     TEXT    NOT NULL,
                        event_type      TEXT    NOT NULL,
                        feedback        TEXT    NOT NULL,
                        event_timestamp TEXT,
                        created_at      TEXT    DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(event_id)
                    )
                """)
                conn.commit()
        except Exception as e:
            logger.error(f"Error inicializando event_feedback: {e}")

    def save(self, event_id: str, camera_name: str,
             event_type: str, feedback: str,
             event_timestamp: str = "") -> bool:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO event_feedback
                       (event_id, camera_name, event_type, feedback, event_timestamp)
                       VALUES (?, ?, ?, ?, ?)""",
                    (event_id, camera_name, event_type, feedback, event_timestamp)
                )
                conn.commit()
            logger.info(f"Feedback guardado: {event_id[:12]} → {feedback}")
            return True
        except Exception as e:
            logger.error(f"Error guardando feedback: {e}")
            return False

    def camera_context(self, camera_name: str, days: int = 21) -> str:
        """Retorna string con el historial de feedback para inyectar en prompt."""
        since = (datetime.now() - timedelta(days=days)).isoformat()
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute("""
                    SELECT event_type, feedback, COUNT(*) AS cnt
                    FROM event_feedback
                    WHERE camera_name = ? AND created_at >= ?
                    GROUP BY event_type, feedback
                    ORDER BY cnt DESC
                """, (camera_name, since)).fetchall()
        except Exception as e:
            logger.warning(f"Error leyendo feedback: {e}")
            return ""

        if not rows:
            return ""

        fp_parts, tp_parts = [], []
        for etype, fb, cnt in rows:
            if fb == "false_positive":
                fp_parts.append(f"{cnt}x '{etype}'")
            else:
                tp_parts.append(f"{cnt}x '{etype}'")

        parts = []
        if fp_parts:
            parts.append(f"Falsos positivos recientes en {camera_name}: {', '.join(fp_parts)}")
        if tp_parts:
            parts.append(f"Alertas reales confirmadas: {', '.join(tp_parts)}")

        return " | ".join(parts)

    def stats(self) -> dict:
        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT feedback, COUNT(*) FROM event_feedback GROUP BY feedback"
                ).fetchall()
            return {r[0]: r[1] for r in row}
        except Exception:
            return {}
