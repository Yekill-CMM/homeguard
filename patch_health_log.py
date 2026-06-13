#!/usr/bin/env python3
"""
patch_health_log.py — HomeGuard AI
Mejora el log de eventos de salud del sistema en 3 archivos:
  1. core/health_monitor.py  → tabla health_events persistente
  2. dashboard/api.py        → endpoint /api/health/log
  3. dashboard/static/admin.html → UI mejorada con filtros y log
"""
from pathlib import Path
import sys

BASE = Path.home() / "homeguard"
OK   = "✅"
ERR  = "❌"


def patch(path: Path, old: str, new: str, label: str) -> bool:
    text = path.read_text()
    if old not in text:
        print(f"{ERR} {label}: cadena no encontrada en {path.name}")
        return False
    path.write_text(text.replace(old, new, 1))
    print(f"{OK} {label}")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# 1. core/health_monitor.py
#    • Crear tabla health_events en __init__
#    • Reemplazar _save_alert_to_db para escribir en esa tabla
# ─────────────────────────────────────────────────────────────────────────────

HM = BASE / "core" / "health_monitor.py"

patch(HM,
    # OLD
    "        # Crear tabla de log persistente\n        if self.db:\n            self._ensure_health_events_table()",
    # Si ya estaba de una pasada anterior, no hacer nada
    "        # Crear tabla de log persistente\n        if self.db:\n            self._ensure_health_events_table()",
    "health_monitor: tabla ya inicializada (skip)",
) or patch(HM,
    # OLD — buscar el final del __init__ sin la llamada
    '        self._prev_internet_ok: Optional[bool] = None\n        self._prev_disk_status: Optional[str]  = None   # "OK" | "WARNING" | "CRITICAL"',
    # NEW
    '        self._prev_internet_ok: Optional[bool] = None\n        self._prev_disk_status: Optional[str]  = None   # "OK" | "WARNING" | "CRITICAL"\n\n        # Crear tabla de log persistente\n        if self.db:\n            self._ensure_health_events_table()',
    "health_monitor: inicializar tabla en __init__",
)

patch(HM,
    # OLD _save_alert_to_db (escribe en system_config)
    '''    def _save_alert_to_db(self, alert: HealthAlert):
        """Guarda la alerta en la tabla system_config de la DB."""
        try:
            import json
            key = f"health_alert_{alert.device_id}_{int(alert.timestamp.timestamp())}"
            value = json.dumps({
                "device_name": alert.device_name,
                "alert_type":  alert.alert_type,
                "message":     alert.message,
                "timestamp":   alert.timestamp.isoformat(),
            })
            with self.db._connect() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO system_config (key, value, updated_at)
                       VALUES (?, ?, ?)""",
                    (key, value, alert.timestamp.isoformat()),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Error guardando alerta en DB: {e}")''',
    # NEW — tabla propia health_events
    '''    def _ensure_health_events_table(self) -> None:
        """Crea la tabla health_events si no existe."""
        try:
            with self.db._connect() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS health_events (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp   TEXT    NOT NULL,
                        component   TEXT    NOT NULL,
                        device_name TEXT    NOT NULL,
                        device_type TEXT    NOT NULL,
                        alert_type  TEXT    NOT NULL,
                        message     TEXT,
                        is_recovery INTEGER NOT NULL DEFAULT 0
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_hev_ts ON health_events(timestamp)"
                )
                conn.commit()
        except Exception as e:
            logger.warning(f"No se pudo crear tabla health_events: {e}")

    def _save_alert_to_db(self, alert: HealthAlert):
        """Persiste la alerta en la tabla health_events."""
        try:
            with self.db._connect() as conn:
                conn.execute("""
                    INSERT INTO health_events
                        (timestamp, component, device_name, device_type,
                         alert_type, message, is_recovery)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    alert.timestamp.isoformat(),
                    alert.device_id,
                    alert.device_name,
                    alert.device_type,
                    alert.alert_type,
                    alert.message,
                    1 if alert.alert_type == "recovered" else 0,
                ))
                conn.commit()
        except Exception as e:
            logger.error(f"Error guardando alerta en DB: {e}")''',
    "health_monitor: _save_alert_to_db → tabla health_events",
)

# ─────────────────────────────────────────────────────────────────────────────
# 2. dashboard/api.py
#    • Agregar /api/health/log con filtros
# ─────────────────────────────────────────────────────────────────────────────

API = BASE / "dashboard" / "api.py"

patch(API,
    # OLD — fin de add_health_routes
    '''    @app.get("/api/health/summary")
    async def health_summary():
        """Resumen de salud del sistema."""
        monitor = getattr(core, 'health_monitor', None)
        if not monitor:
            return {"total": 0, "online": 0, "offline": 0, "warning": 0}
        devices = monitor.get_status()
        online  = sum(1 for d in devices if d["online"])
        offline = sum(1 for d in devices if not d["online"])
        warning = sum(1 for d in devices if d["online"] and d["latency_ms"] > 300)
        return {
            "total":   len(devices),
            "online":  online,
            "offline": offline,
            "warning": warning,
        }''',
    # NEW — igual + endpoint log
    '''    @app.get("/api/health/summary")
    async def health_summary():
        """Resumen de salud del sistema."""
        monitor = getattr(core, 'health_monitor', None)
        if not monitor:
            return {"total": 0, "online": 0, "offline": 0, "warning": 0}
        devices = monitor.get_status()
        online  = sum(1 for d in devices if d["online"])
        offline = sum(1 for d in devices if not d["online"])
        warning = sum(1 for d in devices if d["online"] and d["latency_ms"] > 300)
        return {
            "total":   len(devices),
            "online":  online,
            "offline": offline,
            "warning": warning,
        }

    @app.get("/api/health/log")
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
    "api.py: agregar /api/health/log",
)

# ─────────────────────────────────────────────────────────────────────────────
# 3. dashboard/static/admin.html
#    • Mejorar sección health: disco/internet + filtros + log persistente
# ─────────────────────────────────────────────────────────────────────────────

HTML = BASE / "dashboard" / "static" / "admin.html"

# 3a. HTML — reemplazar contenido de section-health
patch(HTML,
    # OLD
    '''      <!-- Lista de dispositivos con estado -->
      <div class="section-title" style="margin-bottom:12px">DISPOSITIVOS MONITOREADOS</div>
      <div class="items-grid" id="health-list"></div>

      <!-- Alertas recientes -->
      <div class="section-title" style="margin:20px 0 12px">ALERTAS RECIENTES</div>
      <div class="items-grid" id="health-alerts-list"></div>''',
    # NEW
    '''      <!-- Estado sistema: disco e internet -->
      <div class="section-title" style="margin-bottom:12px">SISTEMA</div>
      <div class="items-grid" id="health-system-list"></div>

      <!-- Lista de dispositivos con estado -->
      <div class="section-title" style="margin:20px 0 12px">DISPOSITIVOS MONITOREADOS</div>
      <div class="items-grid" id="health-list"></div>

      <!-- Log de eventos con filtros -->
      <div style="display:flex;align-items:center;justify-content:space-between;margin:20px 0 12px">
        <div class="section-title" style="margin:0">LOG DE EVENTOS</div>
        <div style="display:flex;gap:6px;flex-wrap:wrap" id="health-log-filters">
          <button class="hf-btn active" onclick="setHealthFilter('all',this)">Todos</button>
          <button class="hf-btn" onclick="setHealthFilter('offline',this)">🔴 Offline</button>
          <button class="hf-btn" onclick="setHealthFilter('disk',this)">💾 Disco</button>
          <button class="hf-btn" onclick="setHealthFilter('internet',this)">🌐 Internet</button>
          <button class="hf-btn" onclick="setHealthFilter('high_latency',this)">🟡 Latencia</button>
          <button class="hf-btn" onclick="setHealthFilter('recovered',this)">🟢 Recuperados</button>
        </div>
      </div>
      <div id="health-log-list"></div>''',
    "admin.html: mejorar HTML sección health",
)

# 3b. CSS — agregar estilos para filtros del log (después de .section-title o similar)
patch(HTML,
    ".section { display: none; }",
    """.hf-btn {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--muted);
  border-radius: 6px;
  padding: 5px 12px;
  font-family: var(--ui, sans-serif);
  font-size: 12px;
  cursor: pointer;
  transition: all .15s;
}
.hf-btn:hover { border-color: var(--accent); color: var(--accent); }
.hf-btn.active { background: var(--accent); color: #080c10; border-color: var(--accent); font-weight: 700; }
.log-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}
.log-row:last-child { border-bottom: none; }
.log-ts { color: var(--muted); font-size: 11px; white-space: nowrap; min-width: 130px; }
.log-msg { color: var(--text); flex: 1; }
.log-wrap { background: var(--s1, #0d1318); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
.section { display: none; }""",
    "admin.html: CSS filtros log",
)

# 3c. JS — reemplazar loadHealth() con versión mejorada
patch(HTML,
    # OLD
    '''// ─── Salud del sistema ─────────────────────────────────────
async function loadHealth() {
  try {
    const [summary, devices, alerts] = await Promise.all([
      fetch(`${API}/api/health/summary`).then(r=>r.json()),
      fetch(`${API}/api/health/devices`).then(r=>r.json()),
      fetch(`${API}/api/health/alerts?limit=20`).then(r=>r.json()),
    ]);

    // Métricas
    document.getElementById('hm-total').textContent   = summary.total   ?? 0;
    document.getElementById('hm-online').textContent  = summary.online  ?? 0;
    document.getElementById('hm-offline').textContent = summary.offline ?? 0;
    document.getElementById('hm-warning').textContent = summary.warning ?? 0;
    document.getElementById('cnt-offline').textContent = summary.offline ?? 0;

    // Dispositivos
    const TYPE_ICONS = { camera:'📹', sensor:'📡', recorder:'🎬', router:'🌐', other:'🔌' };
    const list = document.getElementById('health-list');
    if (!devices.length) {
      list.innerHTML = emptyState('❤️', 'Sin dispositivos monitoreados', 'Inicia el sistema para comenzar el monitoreo');
    } else {
      list.innerHTML = devices.map(d => {
        const icon = TYPE_ICONS[d.device_type] || '🔌';
        const statusColor = !d.online ? 'var(--red)' : d.latency_ms > 300 ? 'var(--amber)' : 'var(--green)';
        const statusText  = !d.online ? 'OFFLINE' : d.latency_ms > 300 ? `${d.latency_ms}ms` : `${d.latency_ms}ms`;
        const lastSeen    = d.last_seen ? new Date(d.last_seen).toLocaleTimeString('es-CL') : '—';
        return `
          <div class="item-card">
            <div class="item-icon" style="background:rgba(0,0,0,.2)">${icon}</div>
            <div class="item-info">
              <div class="item-name">${d.device_name}</div>
              <div class="item-detail">${d.host} · Último contacto: ${lastSeen}</div>
            </div>
            <div class="item-actions">
              <span class="badge" style="color:${statusColor};border-color:${statusColor}20;background:${statusColor}15">
                ${d.online ? '●' : '○'} ${statusText}
              </span>
              ${d.failures > 0 ? `<span class="badge badge-off">${d.failures} fallos</span>` : ''}
            </div>
          </div>`;
      }).join('');
    }

    // Alertas
    const alertList = document.getElementById('health-alerts-list');
    const ALERT_ICONS = { offline:'🔴', recovered:'🟢', high_latency:'🟡' };
    if (!alerts.length) {
      alertList.innerHTML = `<div class="empty-state"><div class="empty-icon">✅</div><div class="empty-title">Sin alertas recientes</div><div class="empty-desc">Todos los dispositivos operando normalmente</div></div>`;
    } else {
      alertList.innerHTML = alerts.map(a => `
        <div class="item-card" style="border-left:3px solid ${a.alert_type==='offline'?'var(--red)':a.alert_type==='recovered'?'var(--green)':'var(--amber)'}">
          <div class="item-icon" style="font-size:22px;background:none">${ALERT_ICONS[a.alert_type]||'⚠️'}</div>
          <div class="item-info">
            <div class="item-name">${a.message}</div>
            <div class="item-detail">${new Date(a.timestamp).toLocaleString('es-CL')}</div>
          </div>
        </div>`).join('');
    }

  } catch(e) {
    console.warn('Error cargando salud:', e);
  }
}''',
    # NEW
    '''// ─── Salud del sistema ─────────────────────────────────────
let _healthFilter = 'all';
let _healthAutoRefresh = null;

function setHealthFilter(type, btn) {
  _healthFilter = type;
  document.querySelectorAll('.hf-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  loadHealthLog();
}

function toggleHealthAutoRefresh() {
  if (_healthAutoRefresh) {
    clearInterval(_healthAutoRefresh);
    _healthAutoRefresh = null;
  } else {
    _healthAutoRefresh = setInterval(loadHealth, 30000);
  }
}

async function loadHealth() {
  try {
    const [summary, devices] = await Promise.all([
      fetch(`${API}/api/health/summary`).then(r=>r.json()),
      fetch(`${API}/api/health/devices`).then(r=>r.json()),
    ]);

    // Métricas
    document.getElementById('hm-total').textContent    = summary.total   ?? 0;
    document.getElementById('hm-online').textContent   = summary.online  ?? 0;
    document.getElementById('hm-offline').textContent  = summary.offline ?? 0;
    document.getElementById('hm-warning').textContent  = summary.warning ?? 0;
    document.getElementById('cnt-offline').textContent = summary.offline ?? 0;

    // Tarjetas de sistema (disco + internet)
    const sysList = document.getElementById('health-system-list');
    const sysDevices = devices.filter(d => d.device_type === 'system');
    const SYSTEM_ICONS = { disk: '💾', internet: '🌐', Disk: '💾', Internet: '🌐' };
    if (sysDevices.length) {
      sysList.innerHTML = sysDevices.map(d => {
        const color = !d.online ? 'var(--red)' : 'var(--green)';
        const icon  = SYSTEM_ICONS[d.device_id] || SYSTEM_ICONS[d.device_name] || '⚙️';
        return `
          <div class="item-card">
            <div class="item-icon" style="background:rgba(0,0,0,.2)">${icon}</div>
            <div class="item-info">
              <div class="item-name">${d.device_name}</div>
              <div class="item-detail">${d.host || '—'}</div>
            </div>
            <div class="item-actions">
              <span class="badge" style="color:${color};border-color:${color}20;background:${color}15">
                ${d.online ? '● OK' : '○ ALERTA'}
              </span>
            </div>
          </div>`;
      }).join('');
    } else {
      sysList.innerHTML = '';
    }

    // Dispositivos (cámaras, infra)
    const TYPE_ICONS = { camera:'📹', sensor:'📡', recorder:'🎬', router:'🌐', server:'🖥️', nvr:'📼', other:'🔌' };
    const hwDevices = devices.filter(d => d.device_type !== 'system');
    const list = document.getElementById('health-list');
    if (!hwDevices.length) {
      list.innerHTML = emptyState('❤️', 'Sin dispositivos monitoreados', 'Inicia el sistema para comenzar el monitoreo');
    } else {
      list.innerHTML = hwDevices.map(d => {
        const icon        = TYPE_ICONS[d.device_type] || '🔌';
        const statusColor = !d.online ? 'var(--red)' : d.latency_ms > 300 ? 'var(--amber)' : 'var(--green)';
        const statusText  = !d.online ? 'OFFLINE' : `${d.latency_ms}ms`;
        const lastSeen    = d.last_seen ? new Date(d.last_seen).toLocaleTimeString('es-CL') : '—';
        return `
          <div class="item-card">
            <div class="item-icon" style="background:rgba(0,0,0,.2)">${icon}</div>
            <div class="item-info">
              <div class="item-name">${d.device_name}</div>
              <div class="item-detail">${d.host} · Último contacto: ${lastSeen}</div>
            </div>
            <div class="item-actions">
              <span class="badge" style="color:${statusColor};border-color:${statusColor}20;background:${statusColor}15">
                ${d.online ? '●' : '○'} ${statusText}
              </span>
              ${d.failures > 0 ? `<span class="badge badge-off">${d.failures} fallos</span>` : ''}
            </div>
          </div>`;
      }).join('');
    }

    // Cargar log
    await loadHealthLog();

  } catch(e) {
    console.warn('Error cargando salud:', e);
  }
}

async function loadHealthLog() {
  const logList = document.getElementById('health-log-list');
  if (!logList) return;
  try {
    const url = `${API}/api/health/log?limit=100` + (_healthFilter !== 'all' ? `&alert_type=${_healthFilter}` : '');
    const logs = await fetch(url).then(r => r.json());

    const ALERT_ICONS = {
      offline:      '🔴',
      recovered:    '🟢',
      high_latency: '🟡',
      disk:         '💾',
      internet:     '🌐',
    };
    const ALERT_COLORS = {
      offline:      'var(--red)',
      recovered:    'var(--green)',
      high_latency: 'var(--amber)',
      disk:         'var(--amber)',
      internet:     'var(--red)',
    };

    if (!logs.length) {
      logList.innerHTML = `<div class="empty-state"><div class="empty-icon">✅</div><div class="empty-title">Sin eventos registrados</div><div class="empty-desc">Los eventos de salud aparecerán aquí</div></div>`;
      return;
    }

    logList.innerHTML = `<div class="log-wrap">${logs.map(a => {
      const icon  = ALERT_ICONS[a.alert_type]  || '⚠️';
      const color = ALERT_COLORS[a.alert_type] || 'var(--muted)';
      const ts    = new Date(a.timestamp).toLocaleString('es-CL', {
        day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit'
      });
      return `<div class="log-row">
        <span style="font-size:16px">${icon}</span>
        <span class="log-ts">${ts}</span>
        <span class="log-msg" style="color:${color}">${a.device_name}</span>
        <span class="log-msg">${a.message || '—'}</span>
      </div>`;
    }).join('')}</div>`;
  } catch(e) {
    console.warn('Error cargando log:', e);
  }
}''',
    "admin.html: JS loadHealth() mejorado con log y filtros",
)

print("\nPatch completado. Revisar resultados arriba.")
