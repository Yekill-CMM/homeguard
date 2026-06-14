#!/usr/bin/env python3
"""
patch_claude_limiter.py — HomeGuard AI
Integra el ClaudeLimiter en engine.py y api.py.
"""
from pathlib import Path

BASE = Path.home() / "homeguard"
OK  = "✅"
ERR = "❌"


def patch(path: Path, old: str, new: str, label: str) -> bool:
    text = path.read_text()
    if old not in text:
        print(f"{ERR} {label}: cadena no encontrada en {path.name}")
        return False
    path.write_text(text.replace(old, new, 1))
    print(f"{OK} {label}")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# 1. engine.py — inicializar limiter en __init__
# ─────────────────────────────────────────────────────────────────────────────

ENGINE = BASE / "core" / "engine.py"

patch(ENGINE,
    # OLD — fin del bloque YOLO en __init__
    '''        self._stats = {
            "events_received": 0,
            "events_analyzed": 0,
            "events_skipped_ai": 0,
            "alerts_triggered": 0,
        }''',
    # NEW — igual + inicialización del limiter
    '''        self._stats = {
            "events_received": 0,
            "events_analyzed": 0,
            "events_skipped_ai": 0,
            "events_blocked_limiter": 0,
            "alerts_triggered": 0,
        }

        # Inicializar limitador de uso de Claude Vision
        from core.claude_limiter import ClaudeLimiter
        self.limiter = ClaudeLimiter(db_path=storage_config.db_path)''',
    "engine.py: inicializar ClaudeLimiter en __init__",
)

# ─────────────────────────────────────────────────────────────────────────────
# 2. engine.py — usar limiter antes de llamar a Claude
# ─────────────────────────────────────────────────────────────────────────────

patch(ENGINE,
    # OLD
    '''        # ── Análisis Claude (si necesario) ────────────────────────────
        if event.needs_ai_analysis and event.snapshot:
            await self._analyze_with_claude(event)
            self._stats["events_analyzed"] += 1
        elif not event.needs_ai_analysis:
            pass  # Ya clasificado por YOLO
        else:
            self._stats["events_skipped_ai"] += 1
            logger.info(f"[{event.camera_name}] Sin snapshot — omitiendo análisis")''',
    # NEW
    '''        # ── Análisis Claude (si necesario) ────────────────────────────
        if event.needs_ai_analysis and event.snapshot:
            allowed, block_reason = self.limiter.can_call(str(event.camera_id))
            if allowed:
                await self._analyze_with_claude(event)
                self.limiter.record_call(str(event.camera_id))
                self._stats["events_analyzed"] += 1
            else:
                self._stats["events_blocked_limiter"] += 1
                self._stats["events_skipped_ai"] += 1
                logger.warning(
                    f"[Limiter] {event.camera_name} — bloqueado: {block_reason}"
                )
        elif not event.needs_ai_analysis:
            pass  # Ya clasificado por YOLO
        else:
            self._stats["events_skipped_ai"] += 1
            logger.info(f"[{event.camera_name}] Sin snapshot — omitiendo análisis")''',
    "engine.py: integrar limiter antes de llamar a Claude",
)

# ─────────────────────────────────────────────────────────────────────────────
# 3. api.py — agregar endpoint /api/claude/stats
# ─────────────────────────────────────────────────────────────────────────────

API = BASE / "dashboard" / "api.py"

patch(API,
    # OLD — fin de add_health_routes
    '''    @app.get("/api/health/log")
    async def health_log(
        alert_type: Optional[str] = None,
        limit: int = Query(default=100, le=500),
        offset: int = 0,
    ):
        """Log persistente de eventos de salud desde la DB."""
        try:
            with _db._connect() as conn:
                q = "SELECT * FROM health_events"
                params = []
                if alert_type and alert_type != "all":
                    q += " WHERE alert_type = ?"
                    params.append(alert_type)
                q += " ORDER BY id DESC LIMIT ? OFFSET ?"
                params += [limit, offset]
                rows = conn.execute(q, params).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"health_log error: {e}")
            return []''',
    # NEW — igual + endpoints de Claude
    '''    @app.get("/api/health/log")
    async def health_log(
        alert_type: Optional[str] = None,
        limit: int = Query(default=100, le=500),
        offset: int = 0,
    ):
        """Log persistente de eventos de salud desde la DB."""
        try:
            with _db._connect() as conn:
                q = "SELECT * FROM health_events"
                params = []
                if alert_type and alert_type != "all":
                    q += " WHERE alert_type = ?"
                    params.append(alert_type)
                q += " ORDER BY id DESC LIMIT ? OFFSET ?"
                params += [limit, offset]
                rows = conn.execute(q, params).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"health_log error: {e}")
            return []

    @app.get("/api/claude/stats")
    async def claude_stats():
        """Estadísticas de uso y gasto de Claude Vision."""
        limiter = getattr(core, "limiter", None)
        if not limiter:
            return {"error": "Limiter no disponible"}
        return limiter.stats()

    @app.get("/api/claude/limits")
    async def claude_limits():
        """Configuración actual de los límites de Claude Vision."""
        limiter = getattr(core, "limiter", None)
        if not limiter:
            return {}
        return {
            "daily_limit":          limiter.daily_limit,
            "monthly_budget_usd":   limiter.monthly_budget_usd,
            "camera_cooldown_s":    limiter.camera_cooldown_s,
            "cost_per_call_usd":    limiter.cost_per_call_usd,
        }''',
    "api.py: agregar /api/claude/stats y /api/claude/limits",
)

print("\nPatch completado.")
