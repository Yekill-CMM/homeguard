#!/usr/bin/env python3
"""
patch_claude_admin.py — HomeGuard AI
1. Corrige api_key_configured: lee desde core.claude_config (no os.environ)
2. Agrega sección Claude Vision en admin.html:
   - Nav item "🤖 Claude Vision" en sidenav
   - Sección con métricas, toggle, API key, config e historial 7 días
"""
from pathlib import Path

BASE   = Path.home() / "homeguard"
OK     = "✅"
ERR    = "❌"


def patch(path: Path, old: str, new: str, label: str) -> bool:
    text = path.read_text()
    if old not in text:
        print(f"{ERR} {label}: cadena no encontrada en {path.name}")
        return False
    path.write_text(text.replace(old, new, 1))
    print(f"{OK} {label}")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# 1. api.py — fix api_key_configured: leer desde core.claude_config
# ─────────────────────────────────────────────────────────────────────────────

API = BASE / "dashboard" / "api.py"

patch(API,
    '''    @app.get("/api/claude/config")
    async def claude_config_get():
        """Estado de Claude Vision: habilitado, api key, stats de uso."""
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
        }''',
    '''    @app.get("/api/claude/config")
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
        }''',
    "api.py: fix api_key_configured desde core.claude_config",
)

# ─────────────────────────────────────────────────────────────────────────────
# 2. admin.html — nav item Claude Vision
# ─────────────────────────────────────────────────────────────────────────────

HTML = BASE / "dashboard" / "static" / "admin.html"

patch(HTML,
    '''      <button class="nav-item" onclick="showSection('health', this)" id="nav-health">
        <span class="nav-icon">❤️</span> Salud sistema
        <span class="nav-count" id="cnt-offline" style="background:rgba(255,59,59,.15);color:var(--red)">0</span>
      </button>''',
    '''      <button class="nav-item" onclick="showSection('health', this)" id="nav-health">
        <span class="nav-icon">❤️</span> Salud sistema
        <span class="nav-count" id="cnt-offline" style="background:rgba(255,59,59,.15);color:var(--red)">0</span>
      </button>
    </div>
    <div class="nav-section">
      <div class="nav-label">Inteligencia Artificial</div>
      <button class="nav-item" onclick="showSection('claude', this)" id="nav-claude">
        <span class="nav-icon">🤖</span> Claude Vision
        <span class="nav-count" id="cnt-claude">0</span>
      </button>''',
    "admin.html: nav item Claude Vision",
)

# ─────────────────────────────────────────────────────────────────────────────
# 3. admin.html — sección section-claude (antes del cierre de .content)
# ─────────────────────────────────────────────────────────────────────────────

SECTION_HTML = '''
    <!-- CLAUDE VISION -->
    <div class="section" id="section-claude">
      <div class="page-header">
        <div>
          <div class="page-title">CLAUDE VISION — IA</div>
          <div class="page-desc">Análisis contextual de eventos · Uso y configuración</div>
        </div>
        <button class="btn" style="background:transparent;border:1px solid var(--border);color:var(--muted);border-radius:8px;padding:10px 18px;cursor:pointer;font-family:var(--ui);font-size:14px" onclick="loadClaudeAdmin()">↻ Actualizar</button>
      </div>

      <!-- Métricas -->
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px">
        <div class="metric-card">
          <div class="metric-val c-accent" id="cl-today-calls">—</div>
          <div class="metric-lbl">Llamadas hoy</div>
        </div>
        <div class="metric-card">
          <div class="metric-val c-green" id="cl-today-cost">—</div>
          <div class="metric-lbl">Costo hoy (USD)</div>
        </div>
        <div class="metric-card">
          <div class="metric-val c-amber" id="cl-month-cost">—</div>
          <div class="metric-lbl">Costo mes (USD)</div>
        </div>
        <div class="metric-card">
          <div class="metric-val c-green" id="cl-remaining">—</div>
          <div class="metric-lbl">Disponible mes</div>
        </div>
      </div>

      <!-- Estado y API key -->
      <div class="section-title" style="margin-bottom:12px">CONFIGURACIÓN</div>
      <div class="items-grid" style="margin-bottom:20px">

        <!-- Toggle habilitado -->
        <div class="item-card">
          <div class="item-icon">🤖</div>
          <div class="item-info">
            <div class="item-name">Claude Vision</div>
            <div class="item-detail">Análisis inteligente de escenas por IA</div>
          </div>
          <div class="item-actions">
            <button class="toggle-sw" id="cl-toggle"
                    onclick="toggleClaudeAdmin(this)"
                    style="cursor:pointer"></button>
          </div>
        </div>

        <!-- API key -->
        <div class="item-card" style="flex-direction:column;align-items:flex-start;gap:12px">
          <div style="display:flex;align-items:center;gap:12px;width:100%">
            <div class="item-icon">🔑</div>
            <div class="item-info">
              <div class="item-name">API Key Anthropic</div>
              <div class="item-detail" id="cl-key-status">Cargando...</div>
            </div>
          </div>
          <div style="display:flex;gap:8px;width:100%">
            <input type="password" id="cl-key-input"
                   placeholder="sk-ant-api03-..."
                   style="flex:1;background:var(--bg);border:1px solid var(--border);
                          border-radius:8px;padding:9px 14px;color:var(--text);
                          font-family:var(--mono);font-size:13px;outline:none">
            <button onclick="saveClaudeKey()"
                    class="btn-primary" style="padding:9px 20px;white-space:nowrap">
              Guardar
            </button>
          </div>
          <div style="font-size:11px;color:var(--muted)">
            ⚠️ Al guardar la API key reinicia el servicio para aplicarla:
            <code style="color:var(--accent)">sudo systemctl restart homeguard</code>
          </div>
        </div>
      </div>

      <!-- Límites configurados -->
      <div class="section-title" style="margin-bottom:12px">LÍMITES ACTIVOS</div>
      <div class="items-grid" style="margin-bottom:20px" id="cl-limits-list"></div>

      <!-- Historial 7 días -->
      <div class="section-title" style="margin-bottom:12px">HISTORIAL — ÚLTIMOS 7 DÍAS</div>
      <div id="cl-history-list"></div>
    </div>

  </div>
</div>'''

patch(HTML,
    "  </div>\n</div>\n\n</body>",
    SECTION_HTML + "\n\n</body>",
    "admin.html: sección section-claude",
)

# ─────────────────────────────────────────────────────────────────────────────
# 4. admin.html — JS loadClaudeAdmin, toggleClaudeAdmin, saveClaudeKey
# ─────────────────────────────────────────────────────────────────────────────

JS = '''
// ─── Claude Vision ─────────────────────────────────────────
async function loadClaudeAdmin() {
  try {
    const data = await fetch(`${API}/api/claude/config`).then(r => r.json());
    const s    = data.stats  || {};
    const td   = s.today     || {};
    const mn   = s.month     || {};
    const cfg  = s.config    || {};

    // Métricas
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    set('cl-today-calls', td.calls    ?? 0);
    set('cl-today-cost',  `$${(td.cost_usd ?? 0).toFixed(3)}`);
    set('cl-month-cost',  `$${(mn.cost_usd ?? 0).toFixed(3)}`);
    set('cl-remaining',   `$${(mn.remaining ?? 15).toFixed(2)}`);

    // Badge nav
    const badge = document.getElementById('cnt-claude');
    if (badge) badge.textContent = td.calls ?? 0;

    // Toggle
    const tog = document.getElementById('cl-toggle');
    if (tog) {
      tog.className = 'toggle-sw ' + (data.enabled ? 'on' : '');
    }

    // API key status
    const ks = document.getElementById('cl-key-status');
    if (ks) {
      ks.textContent  = data.api_key_configured
        ? `✅ ${data.api_key_masked}`
        : '❌ No configurada — ingresa tu clave Anthropic';
      ks.style.color = data.api_key_configured ? 'var(--green)' : 'var(--red)';
    }

    // Límites
    const ll = document.getElementById('cl-limits-list');
    if (ll && cfg) {
      const pctDay = td.pct ?? 0;
      const pctMon = mn.pct ?? 0;
      const barStyle = (pct, color) =>
        `<div style="height:4px;background:var(--border);border-radius:2px;margin-top:6px">
           <div style="height:4px;width:${Math.min(pct,100)}%;background:${color};border-radius:2px;transition:width .3s"></div>
         </div>`;

      ll.innerHTML = `
        <div class="item-card">
          <div class="item-icon">📅</div>
          <div class="item-info">
            <div class="item-name">Límite diario</div>
            <div class="item-detail">${td.calls ?? 0} / ${cfg.daily_limit ?? 200} llamadas (${pctDay}%)${barStyle(pctDay, pctDay>80?'var(--red)':'var(--accent)')}</div>
          </div>
        </div>
        <div class="item-card">
          <div class="item-icon">💰</div>
          <div class="item-info">
            <div class="item-name">Presupuesto mensual</div>
            <div class="item-detail">USD ${(mn.cost_usd ?? 0).toFixed(3)} / ${cfg.monthly_budget_usd ?? 15} (${pctMon}%)${barStyle(pctMon, pctMon>80?'var(--red)':'var(--green)')}</div>
          </div>
        </div>
        <div class="item-card">
          <div class="item-icon">⏱️</div>
          <div class="item-info">
            <div class="item-name">Cooldown por cámara</div>
            <div class="item-detail">${cfg.camera_cooldown_s ?? 30}s entre llamadas de la misma cámara</div>
          </div>
        </div>
        <div class="item-card">
          <div class="item-icon">💵</div>
          <div class="item-info">
            <div class="item-name">Costo estimado por llamada</div>
            <div class="item-detail">USD ${cfg.cost_per_call_usd ?? 0.015} · Bloqueadas esta sesión: ${s.session?.blocked_calls ?? 0}</div>
          </div>
        </div>`;
    }

    // Historial
    const hl = document.getElementById('cl-history-list');
    const hist = s.history || [];
    if (hl) {
      if (!hist.length) {
        hl.innerHTML = '<div class="empty-state"><div class="empty-icon">📊</div><div class="empty-title">Sin historial aún</div></div>';
      } else {
        const maxCalls = Math.max(...hist.map(h => h.calls), 1);
        hl.innerHTML = `<div class="log-wrap">${hist.map(h => {
          const pct = Math.round(h.calls / maxCalls * 100);
          return `<div class="log-row">
            <span class="log-ts">${h.date}</span>
            <div style="flex:1;display:flex;align-items:center;gap:10px">
              <div style="flex:1;height:6px;background:var(--border);border-radius:3px">
                <div style="height:6px;width:${pct}%;background:var(--accent);border-radius:3px"></div>
              </div>
              <span style="color:var(--text);min-width:70px;text-align:right">${h.calls} llamadas</span>
            </div>
            <span class="log-ts" style="text-align:right;color:var(--green)">$${h.cost_usd.toFixed(3)}</span>
          </div>`;
        }).join('')}</div>`;
      }
    }

  } catch(e) {
    console.warn('Error cargando Claude admin:', e);
  }
}

async function toggleClaudeAdmin(btn) {
  const enabled = !btn.classList.contains('on');
  btn.className = 'toggle-sw ' + (enabled ? 'on' : '');
  try {
    await fetch(`${API}/api/claude/config`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({enabled}),
    });
  } catch(e) {
    btn.className = 'toggle-sw ' + (enabled ? '' : 'on'); // revertir
    console.warn('Error toggling Claude:', e);
  }
}

async function saveClaudeKey() {
  const key = document.getElementById('cl-key-input').value.trim();
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
    alert(data.message || (data.ok ? '✅ API key guardada' : '❌ Error al guardar'));
    document.getElementById('cl-key-input').value = '';
    await loadClaudeAdmin();
  } catch(e) {
    alert('Error: ' + e.message);
  }
}
'''

patch(HTML,
    "// ─── Audio ────────────────────────────────────────────────",
    JS + "\n// ─── Audio ────────────────────────────────────────────────",
    "admin.html: JS Claude Vision admin",
)

# ─────────────────────────────────────────────────────────────────────────────
# 5. admin.html — cargar sección al hacer click en nav
# ─────────────────────────────────────────────────────────────────────────────

patch(HTML,
    "  if (name === 'health')    return loadHealth();",
    "  if (name === 'health')    return loadHealth();\n  if (name === 'claude')    return loadClaudeAdmin();",
    "admin.html: loadClaudeAdmin en showSection",
)

print("\nPatch completado.")
