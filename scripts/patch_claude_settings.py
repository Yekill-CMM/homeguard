#!/usr/bin/env python3
"""
patch_claude_settings.py — HomeGuard AI
Agrega en mobile.html:
  - Toggle Claude Vision habilitado/deshabilitado
  - Input para actualizar la API key
  - Stats de uso (llamadas hoy, gasto mes, presupuesto)

Agrega en api.py:
  - GET  /api/claude/config
  - POST /api/claude/config

Agrega en engine.py:
  - Flag _claude_enabled (togglable desde la UI sin reiniciar)
"""
from pathlib import Path
import re

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
# 1. engine.py
#    • Flag _claude_enabled en __init__
#    • Respetar el flag antes de llamar al limiter
# ─────────────────────────────────────────────────────────────────────────────

ENGINE = BASE / "core" / "engine.py"

patch(ENGINE,
    "        # Inicializar limitador de uso de Claude Vision\n        from core.claude_limiter import ClaudeLimiter\n        self.limiter = ClaudeLimiter(db_path=storage_config.db_path)",
    "        # Flag de habilitación de Claude Vision (togglable desde la UI)\n        self._claude_enabled = True\n\n        # Inicializar limitador de uso de Claude Vision\n        from core.claude_limiter import ClaudeLimiter\n        self.limiter = ClaudeLimiter(db_path=storage_config.db_path)",
    "engine.py: flag _claude_enabled",
)

patch(ENGINE,
    '''        if event.needs_ai_analysis and event.snapshot:
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
                )''',
    '''        if event.needs_ai_analysis and event.snapshot:
            if not getattr(self, "_claude_enabled", True):
                self._stats["events_skipped_ai"] += 1
                logger.debug(f"[Claude] Deshabilitado — evento procesado sin IA")
            else:
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
                    )''',
    "engine.py: respetar _claude_enabled antes del limiter",
)

# ─────────────────────────────────────────────────────────────────────────────
# 2. api.py
#    • GET  /api/claude/config  → estado + stats + api key enmascarada
#    • POST /api/claude/config  → toggle enabled + actualizar api key en .env
# ─────────────────────────────────────────────────────────────────────────────

API = BASE / "dashboard" / "api.py"

patch(API,
    "    @app.get(\"/api/claude/limits\")\n    async def claude_limits():\n        \"\"\"Configuración actual de los límites de Claude Vision.\"\"\"\n        limiter = getattr(core, \"limiter\", None)\n        if not limiter:\n            return {}\n        return {\n            \"daily_limit\":          limiter.daily_limit,\n            \"monthly_budget_usd\":   limiter.monthly_budget_usd,\n            \"camera_cooldown_s\":    limiter.camera_cooldown_s,\n            \"cost_per_call_usd\":    limiter.cost_per_call_usd,\n        }",
    """    @app.get("/api/claude/limits")
    async def claude_limits():
        \"\"\"Configuración actual de los límites de Claude Vision.\"\"\"
        limiter = getattr(core, "limiter", None)
        if not limiter:
            return {}
        return {
            "daily_limit":          limiter.daily_limit,
            "monthly_budget_usd":   limiter.monthly_budget_usd,
            "camera_cooldown_s":    limiter.camera_cooldown_s,
            "cost_per_call_usd":    limiter.cost_per_call_usd,
        }

    @app.get("/api/claude/config")
    async def claude_config_get():
        \"\"\"Estado de Claude Vision: habilitado, api key, stats de uso.\"\"\"
        import os
        limiter = getattr(core, "limiter", None)

        # Estado habilitado desde engine
        enabled = getattr(core, "_claude_enabled", True)

        # API key enmascarada
        env_key = os.environ.get("ANTHROPIC_API_KEY", "")
        configured = len(env_key) > 20 and not env_key.startswith("sk-ant-demo")
        masked = f"sk-ant-...{env_key[-6:]}" if configured else "No configurada"

        return {
            "enabled":            enabled,
            "api_key_configured": configured,
            "api_key_masked":     masked,
            "stats":              limiter.stats() if limiter else {},
        }

    @app.post("/api/claude/config")
    async def claude_config_set(body: dict):
        \"\"\"Actualiza habilitación de Claude Vision y/o la API key.\"\"\"
        from datetime import datetime
        from pathlib import Path as P
        results = {}

        # Toggle enabled/disabled (sin reiniciar)
        if "enabled" in body:
            enabled = bool(body["enabled"])
            if hasattr(core, "_claude_enabled"):
                core._claude_enabled = enabled
            results["enabled"] = enabled

        # Actualizar API key en .env
        if "api_key" in body and body["api_key"]:
            api_key = body["api_key"].strip()
            if not api_key.startswith("sk-ant-"):
                return {"ok": False, "message": "API key inválida — debe comenzar con sk-ant-"}
            env_path = P.home() / "homeguard" / ".env"
            if env_path.exists():
                import re as _re
                env_text = env_path.read_text()
                if "ANTHROPIC_API_KEY=" in env_text:
                    env_text = _re.sub(r"ANTHROPIC_API_KEY=.*", f"ANTHROPIC_API_KEY={api_key}", env_text)
                else:
                    env_text += f"\\nANTHROPIC_API_KEY={api_key}\\n"
                env_path.write_text(env_text)
                results["api_key"] = "actualizada"
                results["message"] = "API key guardada en .env — reinicia el servicio para aplicar"
            else:
                return {"ok": False, "message": ".env no encontrado"}

        return {"ok": True, **results}""",
    "api.py: agregar /api/claude/config GET y POST",
)

# ─────────────────────────────────────────────────────────────────────────────
# 3. mobile.html
#    • Sección "claude vision" en ajustes con toggle + API key + stats
#    • Funciones JS: toggleClaudeVision, saveApiKey, loadClaudeStats
#    • Llamar loadClaudeStats al cargar la pestaña settings
# ─────────────────────────────────────────────────────────────────────────────

MOBILE = BASE / "dashboard" / "static" / "mobile.html"

# 3a. HTML — sección claude vision entre notificaciones y sistema
patch(MOBILE,
    '      <div class="section-title">sistema</div>',
    '''      <div class="section-title" style="margin-top:16px">claude vision</div>
      <div class="settings-section">
        <div class="setting-row">
          <div class="setting-info">
            <div class="setting-label">Análisis IA habilitado</div>
            <div class="setting-desc">Clasifica eventos con Claude Vision</div>
          </div>
          <button class="toggle on" id="toggle-claude" onclick="toggleClaudeVision(this)"></button>
        </div>
        <div class="setting-row" style="flex-direction:column;align-items:flex-start;gap:10px">
          <div class="setting-info" style="width:100%">
            <div class="setting-label">API Key Anthropic</div>
            <div class="setting-desc" id="api-key-status">Cargando...</div>
          </div>
          <div style="display:flex;gap:8px;width:100%">
            <input type="password" id="api-key-input"
                   placeholder="sk-ant-api03-..."
                   style="flex:1;background:var(--bg);border:1px solid var(--border);
                          border-radius:8px;padding:9px 12px;color:var(--text);
                          font-family:var(--mono);font-size:12px;outline:none">
            <button onclick="saveApiKey()"
                    style="background:var(--accent);color:#080c10;border:none;
                           border-radius:8px;padding:9px 16px;font-weight:700;
                           font-family:var(--ui);font-size:13px;cursor:pointer;
                           white-space:nowrap">
              Guardar
            </button>
          </div>
        </div>
      </div>

      <div class="section-title" style="margin-top:12px">uso claude vision</div>
      <div class="settings-section">
        <div class="setting-row">
          <div class="setting-info"><div class="setting-label">Hoy</div></div>
          <div class="setting-value" id="cv-today">—</div>
        </div>
        <div class="setting-row">
          <div class="setting-info"><div class="setting-label">Este mes</div></div>
          <div class="setting-value" id="cv-month">—</div>
        </div>
        <div class="setting-row">
          <div class="setting-info"><div class="setting-label">Presupuesto</div></div>
          <div class="setting-value" id="cv-budget">—</div>
        </div>
        <div class="setting-row">
          <div class="setting-info"><div class="setting-label">Disponible mes</div></div>
          <div class="setting-value" id="cv-remaining" style="color:var(--green)">—</div>
        </div>
      </div>

      <div class="section-title" style="margin-top:16px">sistema</div>''',
    "mobile.html: sección Claude Vision en ajustes",
)

# 3b. JS — funciones toggleClaudeVision, saveApiKey, loadClaudeStats
patch(MOBILE,
    "// Convertir clave VAPID base64 a Uint8Array (requerido por la API)",
    '''// ─── Claude Vision ────────────────────────────────────────
async function toggleClaudeVision(btn) {
  const enabled = !btn.classList.contains('on');
  btn.classList.toggle('on');
  try {
    await fetch(`${API}/api/claude/config`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({enabled}),
    });
  } catch(e) {
    console.warn('Error toggling Claude:', e);
    btn.classList.toggle('on'); // revertir en caso de error
  }
}

async function saveApiKey() {
  const key = document.getElementById('api-key-input').value.trim();
  if (!key) { alert('Ingresa una API key'); return; }
  if (!key.startsWith('sk-ant-')) {
    alert('API key inválida\\nDebe comenzar con sk-ant-');
    return;
  }
  try {
    const res  = await fetch(`${API}/api/claude/config`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({api_key: key}),
    });
    const data = await res.json();
    alert(data.message || (data.ok ? '✅ Guardado' : '❌ Error'));
    document.getElementById('api-key-input').value = '';
    await loadClaudeStats();
  } catch(e) {
    alert('Error guardando API key: ' + e.message);
  }
}

async function loadClaudeStats() {
  try {
    const data = await fetch(`${API}/api/claude/config`).then(r => r.json());

    // Toggle estado
    const tc = document.getElementById('toggle-claude');
    if (tc) {
      data.enabled ? tc.classList.add('on') : tc.classList.remove('on');
    }

    // API key status
    const ks = document.getElementById('api-key-status');
    if (ks) {
      ks.textContent = data.api_key_configured
        ? `✅ ${data.api_key_masked}`
        : '❌ No configurada';
      ks.style.color = data.api_key_configured ? 'var(--green)' : 'var(--red)';
    }

    // Stats de uso
    const s  = data.stats || {};
    const td = s.today  || {};
    const mn = s.month  || {};

    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    set('cv-today',     `${td.calls ?? 0} llamadas · USD ${(td.cost_usd ?? 0).toFixed(3)}`);
    set('cv-month',     `USD ${(mn.cost_usd ?? 0).toFixed(3)} (${mn.pct ?? 0}%)`);
    set('cv-budget',    `USD ${mn.budget_usd ?? 15} / mes`);
    set('cv-remaining', `USD ${(mn.remaining ?? 15).toFixed(3)}`);
  } catch(e) {
    console.warn('Error cargando stats Claude:', e);
  }
}

// Convertir clave VAPID base64 a Uint8Array (requerido por la API)''',
    "mobile.html: JS Claude Vision toggle, saveApiKey, loadClaudeStats",
)

# 3c. Llamar loadClaudeStats al mostrar pestaña settings
patch(MOBILE,
    "  if (name === 'health')    return loadHealth();",
    "  if (name === 'health')    return loadHealth();\n  if (name === 'settings')   return loadClaudeStats();",
    "mobile.html: cargar stats Claude al abrir ajustes",
)

print("\nPatch completado.")
