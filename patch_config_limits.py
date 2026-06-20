"""
Agrega event_dedup_s a la respuesta de GET /api/claude/config
dentro del campo 'stats', para que loadClaudeAdmin pueda repoblar el formulario.
"""
import sys
from pathlib import Path

target = Path(sys.argv[1] if len(sys.argv) > 1 else "api.py")
text = target.read_text()

old_block = '''    @app.get("/api/claude/config")
    async def claude_config_get():
        """Estado de Claude Vision: habilitado, api key, stats de uso."""
        limiter = getattr(core, "limiter", None)

        # Estado habilitado desde engine
        enabled = getattr(core, "_claude_enabled", True)

        # API key desde claude_config del engine (más fiable que os.environ)
        api_key = ""
        claude_cfg = getattr(core, "claude_config", None)
        if claude_cfg:
            api_key = getattr(claude_cfg, "api_key", "") or ""
        if not api_key:
            import os
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")

        configured = (
            len(api_key) > 20
            and not api_key.startswith("sk-ant-demo")
            and "PLACEHOLDER" not in api_key.upper()
            and "TU_API" not in api_key.upper()
        )
        masked = f"sk-ant-...{api_key[-6:]}" if configured else "No configurada"

        return {
            "enabled":            enabled,
            "api_key_configured": configured,
            "api_key_masked":     masked,
            "stats":              limiter.stats() if limiter else {},
        }'''

new_block = '''    @app.get("/api/claude/config")
    async def claude_config_get():
        """Estado de Claude Vision: habilitado, api key, stats de uso."""
        limiter = getattr(core, "limiter", None)

        # Estado habilitado desde engine
        enabled = getattr(core, "_claude_enabled", True)

        # API key desde claude_config del engine (más fiable que os.environ)
        api_key = ""
        claude_cfg = getattr(core, "claude_config", None)
        if claude_cfg:
            api_key = getattr(claude_cfg, "api_key", "") or ""
        if not api_key:
            import os
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")

        configured = (
            len(api_key) > 20
            and not api_key.startswith("sk-ant-demo")
            and "PLACEHOLDER" not in api_key.upper()
            and "TU_API" not in api_key.upper()
        )
        masked = f"sk-ant-...{api_key[-6:]}" if configured else "No configurada"

        stats = limiter.stats() if limiter else {}
        # Agregar config de límites (incluyendo event_dedup_s desde core)
        stats["config"] = {
            "daily_limit":        limiter.daily_limit if limiter else 200,
            "monthly_budget_usd": limiter.monthly_budget_usd if limiter else 15.0,
            "camera_cooldown_s":  limiter.camera_cooldown_s if limiter else 6,
            "cost_per_call_usd":  limiter.cost_per_call_usd if limiter else 0.015,
            "event_dedup_s":      getattr(core, "_dedup_window", 60) if core else 60,
        }

        return {
            "enabled":            enabled,
            "api_key_configured": configured,
            "api_key_masked":     masked,
            "stats":              stats,
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
print(f"OK: config + event_dedup_s agregados a GET /api/claude/config")
