"""
HomeGuard AI — Limitador de uso de Claude Vision
Controla el gasto en API en tres dimensiones:

  1. Llamadas por día     → CLAUDE_DAILY_LIMIT     (default: 200)
  2. Presupuesto mensual  → CLAUDE_MONTHLY_BUDGET  (default: $15.00 USD)
  3. Cooldown por cámara  → CLAUDE_COOLDOWN_S      (default: 30s)

Configuración vía .env:
  CLAUDE_DAILY_LIMIT=200
  CLAUDE_MONTHLY_BUDGET=15.0
  CLAUDE_COOLDOWN_S=30
  CLAUDE_COST_PER_CALL=0.015

Persistencia: tabla `claude_usage` en SQLite.
Los contadores diarios y mensuales sobreviven reinicios del servicio.
"""

import logging
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Configuración por defecto (sobreescribible desde .env) ───────────────────
DAILY_LIMIT    = int(os.environ.get("CLAUDE_DAILY_LIMIT",    "200"))
MONTHLY_BUDGET = float(os.environ.get("CLAUDE_MONTHLY_BUDGET", "15.0"))
COOLDOWN_S     = int(os.environ.get("CLAUDE_COOLDOWN_S",      "30"))
COST_PER_CALL  = float(os.environ.get("CLAUDE_COST_PER_CALL", "0.015"))


class ClaudeLimiter:
    """
    Limita el uso de Claude Vision en tres niveles:
      • Cooldown por cámara  — evita ráfagas desde una misma cámara
      • Límite diario        — techo de llamadas por día calendario
      • Presupuesto mensual  — techo de gasto estimado por mes

    Uso en engine.py:
        allowed, reason = self.limiter.can_call(camera_id)
        if allowed:
            await self._analyze_with_claude(event)
            self.limiter.record_call(camera_id)
    """

    def __init__(
        self,
        db_path: str,
        daily_limit: int      = DAILY_LIMIT,
        monthly_budget_usd: float = MONTHLY_BUDGET,
        camera_cooldown_s: int = COOLDOWN_S,
        cost_per_call_usd: float = COST_PER_CALL,
    ):
        self.db_path            = db_path
        self.daily_limit        = daily_limit
        self.monthly_budget_usd = monthly_budget_usd
        self.camera_cooldown_s  = camera_cooldown_s
        self.cost_per_call_usd  = cost_per_call_usd

        # Cooldown en memoria (monotonic clock — más preciso que DB para esto)
        self._camera_last_call: dict[str, float] = {}
        self._blocked_count = 0

        self._ensure_table()
        logger.info(
            f"[Limiter] Activo — "
            f"diario: {daily_limit} llamadas | "
            f"mensual: USD {monthly_budget_usd:.2f} | "
            f"cooldown: {camera_cooldown_s}s/cámara | "
            f"costo est.: USD {cost_per_call_usd:.4f}/llamada"
        )

    # ── API pública ──────────────────────────────────────────────────────────

    def can_call(self, camera_id: str) -> tuple[bool, str]:
        """
        Verifica si se permite una llamada a Claude Vision.
        Retorna (permitido, motivo_si_bloqueado).
        """
        # 1. Cooldown por cámara
        last = self._camera_last_call.get(camera_id)
        if last is not None:
            elapsed = time.monotonic() - last
            if elapsed < self.camera_cooldown_s:
                remaining = int(self.camera_cooldown_s - elapsed)
                reason = f"cooldown cámara {camera_id}: {remaining}s restantes"
                self._blocked_count += 1
                logger.debug(f"[Limiter] Bloqueado — {reason}")
                return False, reason

        # 2. Límite diario
        today_calls = self._today_calls()
        if today_calls >= self.daily_limit:
            reason = (
                f"límite diario alcanzado: {today_calls}/{self.daily_limit} llamadas"
            )
            self._blocked_count += 1
            logger.warning(f"[Limiter] 🔴 {reason}")
            return False, reason

        # 3. Presupuesto mensual
        monthly_cost = self._monthly_cost()
        if monthly_cost >= self.monthly_budget_usd:
            reason = (
                f"presupuesto mensual alcanzado: "
                f"USD {monthly_cost:.2f}/{self.monthly_budget_usd:.2f}"
            )
            self._blocked_count += 1
            logger.warning(f"[Limiter] 🔴 {reason}")
            return False, reason

        return True, ""

    def record_call(self, camera_id: str) -> None:
        """Registra una llamada exitosa a Claude Vision."""
        self._camera_last_call[camera_id] = time.monotonic()
        now  = datetime.now()
        date = now.strftime("%Y-%m-%d")
        month = now.strftime("%Y-%m")

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """INSERT INTO claude_usage
                       (timestamp, camera_id, cost_usd, date, month)
                       VALUES (?, ?, ?, ?, ?)""",
                    (now.isoformat(), camera_id, self.cost_per_call_usd, date, month),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"[Limiter] Error registrando llamada: {e}")

    def stats(self) -> dict:
        """Estadísticas de uso para el dashboard."""
        now          = datetime.now()
        today        = now.strftime("%Y-%m-%d")
        month        = now.strftime("%Y-%m")
        today_calls  = self._today_calls()
        monthly_cost = self._monthly_cost()

        # Porcentajes
        daily_pct   = round(today_calls / self.daily_limit * 100, 1) if self.daily_limit else 0
        monthly_pct = round(monthly_cost / self.monthly_budget_usd * 100, 1) if self.monthly_budget_usd else 0

        # Historial diario — últimos 7 días
        history = self._daily_history(days=7)

        return {
            "today": {
                "calls":     today_calls,
                "limit":     self.daily_limit,
                "pct":       daily_pct,
                "cost_usd":  round(today_calls * self.cost_per_call_usd, 4),
                "remaining": max(0, self.daily_limit - today_calls),
            },
            "month": {
                "label":      month,
                "cost_usd":   round(monthly_cost, 4),
                "budget_usd": self.monthly_budget_usd,
                "pct":        monthly_pct,
                "remaining":  round(max(0.0, self.monthly_budget_usd - monthly_cost), 4),
            },
            "config": {
                "daily_limit":        self.daily_limit,
                "monthly_budget_usd": self.monthly_budget_usd,
                "camera_cooldown_s":  self.camera_cooldown_s,
                "cost_per_call_usd":  self.cost_per_call_usd,
            },
            "session": {
                "blocked_calls": self._blocked_count,
                "cameras_tracked": len(self._camera_last_call),
            },
            "history": history,
        }

    # ── Consultas a DB ───────────────────────────────────────────────────────

    def _today_calls(self) -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM claude_usage WHERE date = ?", (today,)
                ).fetchone()
                return row[0] if row else 0
        except Exception:
            return 0

    def _monthly_cost(self) -> float:
        month = datetime.now().strftime("%Y-%m")
        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT COALESCE(SUM(cost_usd), 0) FROM claude_usage WHERE month = ?",
                    (month,),
                ).fetchone()
                return float(row[0]) if row else 0.0
        except Exception:
            return 0.0

    def _daily_history(self, days: int = 7) -> list[dict]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    """SELECT date,
                              COUNT(*)          AS calls,
                              SUM(cost_usd)     AS cost_usd
                       FROM claude_usage
                       WHERE date >= date('now', ?)
                       GROUP BY date
                       ORDER BY date DESC""",
                    (f"-{days} days",),
                ).fetchall()
                return [
                    {"date": r[0], "calls": r[1], "cost_usd": round(r[2], 4)}
                    for r in rows
                ]
        except Exception:
            return []

    # ── Setup DB ─────────────────────────────────────────────────────────────

    def _ensure_table(self) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS claude_usage (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp   TEXT    NOT NULL,
                        camera_id   TEXT    NOT NULL,
                        cost_usd    REAL    NOT NULL DEFAULT 0.015,
                        date        TEXT    NOT NULL,
                        month       TEXT    NOT NULL
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_cu_date  ON claude_usage(date)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_cu_month ON claude_usage(month)"
                )
                conn.commit()
        except Exception as e:
            logger.error(f"[Limiter] Error creando tabla claude_usage: {e}")
