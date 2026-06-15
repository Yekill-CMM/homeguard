#!/usr/bin/env python3
"""
patch_claude_limits_edit.py — HomeGuard AI
Agrega formularios editables de límites Claude Vision en admin y mobile.

1. api.py      → POST /api/claude/limits (actualiza limiter en memoria + .env)
2. admin.html  → formulario editable con inputs + botón Guardar
3. mobile.html → sección "límites claude vision" con inputs + botón Guardar
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
# 1. api.py — POST /api/claude/limits
# ─────────────────────────────────────────────────────────────────────────────

API = BASE / "dashboard" / "api.py"

patch(API,
    '''    @app.get("/api/claude/limits")
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
    '''    @app.get("/api/claude/limits")
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
        }

    @app.post("/api/claude/limits")
    async def claude_limits_set(body: dict):
        """Actualiza límites de Claude Vision en memoria y en .env."""
        import re as _re
        from pathlib import Path as P

        limiter = getattr(core, "limiter", None)
        updated = {}

        # Validar y aplicar en memoria
        try:
            if "daily_limit" in body:
                val = max(1, int(body["daily_limit"]))
                if limiter: limiter.daily_limit = val
                updated["CLAUDE_DAILY_LIMIT"] = str(val)

            if "monthly_budget_usd" in body:
                val = max(0.1, float(body["monthly_budget_usd"]))
                if limiter: limiter.monthly_budget_usd = val
                updated["CLAUDE_MONTHLY_BUDGET"] = f"{val:.2f}"

            if "camera_cooldown_s" in body:
                val = max(5, int(body["camera_cooldown_s"]))
                if limiter: limiter.camera_cooldown_s = val
                updated["CLAUDE_COOLDOWN_S"] = str(val)

            if "cost_per_call_usd" in body:
                val = max(0.001, float(body["cost_per_call_usd"]))
                if limiter: limiter.cost_per_call_usd = val
                updated["CLAUDE_COST_PER_CALL"] = f"{val:.4f}"

        except (ValueError, TypeError) as e:
            return {"ok": False, "message": f"Valor inválido: {e}"}

        # Persistir en .env
        env_path = P.home() / "homeguard" / ".env"
        if env_path.exists() and updated:
            env_text = env_path.read_text()
            for key, val in updated.items():
                if key in env_text:
                    env_text = _re.sub(rf"^{key}=.*", f"{key}={val}", env_text, flags=_re.MULTILINE)
                else:
                    env_text += f"\\n{key}={val}\\n"
            env_path.write_text(env_text)

        return {
            "ok":     True,
            "saved":  list(updated.keys()),
            "limits": {
                "daily_limit":        limiter.daily_limit        if limiter else None,
                "monthly_budget_usd": limiter.monthly_budget_usd if limiter else None,
                "camera_cooldown_s":  limiter.camera_cooldown_s  if limiter else None,
                "cost_per_call_usd":  limiter.cost_per_call_usd  if limiter else None,
            }
        }''',
    "api.py: POST /api/claude/limits",
)


# ─────────────────────────────────────────────────────────────────────────────
# 2. admin.html — formulario editable en sección Claude Vision
# ─────────────────────────────────────────────────────────────────────────────

HTML = BASE / "dashboard" / "static" / "admin.html"

# 2a. HTML — agregar form inputs después del div cl-limits-list
patch(HTML,
    '      <div class="items-grid" style="margin-bottom:20px" id="cl-limits-list"></div>\n\n      <!-- Historial 7 días -->',
    '''      <div class="items-grid" style="margin-bottom:20px" id="cl-limits-list"></div>

      <!-- Formulario editable de límites -->
      <div class="section-title" style="margin:20px 0 12px">MODIFICAR LÍMITES</div>
      <div class="item-card" style="flex-direction:column;gap:16px;align-items:stretch">
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
          <div>
            <div class="form-label" style="margin-bottom:6px">Llamadas máx. / día</div>
            <input class="form-input" type="number" id="cl-inp-daily"
                   min="1" max="10000" placeholder="200">
          </div>
          <div>
            <div class="form-label" style="margin-bottom:6px">Presupuesto mensual (USD)</div>
            <input class="form-input" type="number" id="cl-inp-budget"
                   min="1" max="500" step="0.5" placeholder="15.0">
          </div>
          <div>
            <div class="form-label" style="margin-bottom:6px">Cooldown por cámara (seg)</div>
            <input class="form-input" type="number" id="cl-inp-cooldown"
                   min="5" max="300" placeholder="30">
          </div>
          <div>
            <div class="form-label" style="margin-bottom:6px">Costo est. por llamada (USD)</div>
            <input class="form-input" type="number" id="cl-inp-cost"
                   min="0.001" max="1" step="0.001" placeholder="0.015">
          </div>
        </div>
        <div style="display:flex;justify-content:flex-end">
          <button class="btn-primary" onclick="saveLimitsAdmin()">
            Guardar límites
          </button>
        </div>
      </div>

      <!-- Historial 7 días -->''',
    "admin.html: formulario editable de límites",
)

# 2b. JS — función saveLimitsAdmin + popular inputs al cargar
patch(HTML,
    "async function toggleClaudeAdmin(btn) {",
    '''async function saveLimitsAdmin() {
  const daily    = document.getElementById('cl-inp-daily').value;
  const budget   = document.getElementById('cl-inp-budget').value;
  const cooldown = document.getElementById('cl-inp-cooldown').value;
  const cost     = document.getElementById('cl-inp-cost').value;

  if (!daily || !budget || !cooldown || !cost) {
    alert('Completa todos los campos antes de guardar');
    return;
  }

  try {
    const res  = await fetch(`${API}/api/claude/limits`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        daily_limit:        parseInt(daily),
        monthly_budget_usd: parseFloat(budget),
        camera_cooldown_s:  parseInt(cooldown),
        cost_per_call_usd:  parseFloat(cost),
      }),
    });
    const data = await res.json();
    if (data.ok) {
      showToast('✅ Límites guardados y aplicados');
      await loadClaudeAdmin();
    } else {
      alert('Error: ' + (data.message || 'No se pudo guardar'));
    }
  } catch(e) {
    alert('Error al guardar: ' + e.message);
  }
}

async function toggleClaudeAdmin(btn) {''',
    "admin.html: JS saveLimitsAdmin",
)

# 2c. JS — poblar inputs al cargar loadClaudeAdmin
patch(HTML,
    "    // Historial\n    const hl = document.getElementById('cl-history-list');",
    '''    // Poblar inputs editables con valores actuales
    if (cfg) {
      const setInp = (id, val) => { const el = document.getElementById(id); if (el && val !== undefined) el.value = val; };
      setInp('cl-inp-daily',    cfg.daily_limit);
      setInp('cl-inp-budget',   cfg.monthly_budget_usd);
      setInp('cl-inp-cooldown', cfg.camera_cooldown_s);
      setInp('cl-inp-cost',     cfg.cost_per_call_usd);
    }

    // Historial
    const hl = document.getElementById('cl-history-list');''',
    "admin.html: poblar inputs al cargar",
)


# ─────────────────────────────────────────────────────────────────────────────
# 3. mobile.html — sección "límites claude vision" editable
# ─────────────────────────────────────────────────────────────────────────────

MOBILE = BASE / "dashboard" / "static" / "mobile.html"

INPUT_STYLE = ("background:var(--bg);border:1px solid var(--border);"
               "border-radius:8px;padding:9px 12px;color:var(--text);"
               "font-family:var(--mono);font-size:13px;outline:none;width:100%;box-sizing:border-box")

# 3a. HTML — nueva sección límites entre "uso claude vision" y "sistema"
patch(MOBILE,
    '      <div class="section-title" style="margin-top:16px">sistema</div>',
    f'''      <div class="section-title" style="margin-top:16px">límites claude vision</div>
      <div class="settings-section">
        <div class="setting-row" style="flex-direction:column;align-items:flex-start;gap:6px">
          <div class="setting-label">Llamadas máximas por día</div>
          <input type="number" id="lim-daily" min="1" max="10000"
                 placeholder="200" style="{INPUT_STYLE}">
        </div>
        <div class="setting-row" style="flex-direction:column;align-items:flex-start;gap:6px">
          <div class="setting-label">Presupuesto mensual (USD)</div>
          <input type="number" id="lim-budget" min="1" step="0.5"
                 placeholder="15.0" style="{INPUT_STYLE}">
        </div>
        <div class="setting-row" style="flex-direction:column;align-items:flex-start;gap:6px">
          <div class="setting-label">Cooldown por cámara (seg)</div>
          <input type="number" id="lim-cooldown" min="5" max="300"
                 placeholder="30" style="{INPUT_STYLE}">
        </div>
        <div class="setting-row" style="flex-direction:column;align-items:flex-start;gap:6px">
          <div class="setting-label">Costo estimado por llamada (USD)</div>
          <input type="number" id="lim-cost" min="0.001" step="0.001"
                 placeholder="0.015" style="{INPUT_STYLE}">
        </div>
        <div class="setting-row" style="justify-content:flex-end">
          <button onclick="saveLimits()"
                  style="background:var(--accent);color:#080c10;border:none;
                         border-radius:8px;padding:10px 20px;font-weight:700;
                         font-family:var(--ui);font-size:13px;cursor:pointer">
            Guardar límites
          </button>
        </div>
      </div>

      <div class="section-title" style="margin-top:16px">sistema</div>''',
    "mobile.html: sección límites editable",
)

# 3b. JS — función saveLimits + poblar inputs en loadClaudeStats
patch(MOBILE,
    "async function loadClaudeStats() {",
    '''async function saveLimits() {
  const daily    = document.getElementById('lim-daily').value;
  const budget   = document.getElementById('lim-budget').value;
  const cooldown = document.getElementById('lim-cooldown').value;
  const cost     = document.getElementById('lim-cost').value;

  if (!daily || !budget || !cooldown || !cost) {
    alert('Completa todos los campos');
    return;
  }
  try {
    const res  = await fetch(`${API}/api/claude/limits`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        daily_limit:        parseInt(daily),
        monthly_budget_usd: parseFloat(budget),
        camera_cooldown_s:  parseInt(cooldown),
        cost_per_call_usd:  parseFloat(cost),
      }),
    });
    const data = await res.json();
    alert(data.ok ? '✅ Límites guardados' : '❌ Error: ' + (data.message || ''));
    if (data.ok) await loadClaudeStats();
  } catch(e) {
    alert('Error: ' + e.message);
  }
}

async function loadClaudeStats() {''',
    "mobile.html: JS saveLimits",
)

# 3c. JS — poblar inputs dentro de loadClaudeStats
patch(MOBILE,
    "    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };",
    """    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };

    // Poblar inputs editables con valores actuales
    const cfg = s.config || {};
    const setInp = (id, val) => { const el = document.getElementById(id); if (el && val !== undefined) el.value = val; };
    setInp('lim-daily',    cfg.daily_limit);
    setInp('lim-budget',   cfg.monthly_budget_usd);
    setInp('lim-cooldown', cfg.camera_cooldown_s);
    setInp('lim-cost',     cfg.cost_per_call_usd);""",
    "mobile.html: poblar inputs en loadClaudeStats",
)

print("\nPatch completado.")
