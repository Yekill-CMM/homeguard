"""
Agrega event_dedup_s a la respuesta de GET /api/claude/limits
para que el dashboard pueda repoblar el campo correctamente.
"""
import sys
from pathlib import Path

target = Path(sys.argv[1] if len(sys.argv) > 1 else "api.py")
text = target.read_text()

old_block = '''    @app.get("/api/claude/limits")
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
        }'''

new_block = '''    @app.get("/api/claude/limits")
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
            "event_dedup_s":        getattr(core, "_dedup_window", 60) if core else 60,
        }'''

count = text.count(old_block)
if count == 0:
    print("ERROR: no se encontro el bloque GET exacto. No se modifico nada.")
    sys.exit(1)
if count > 1:
    print(f"ERROR: el bloque aparece {count} veces, se esperaba 1. No se modifico nada.")
    sys.exit(1)

new_text = text.replace(old_block, new_block)
target.write_text(new_text)
print(f"OK: event_dedup_s agregado al GET en {target}")
