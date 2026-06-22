"""
Agrega input para event_dedup_s en la sección de límites Claude Vision
y actualiza saveLimits() para incluirlo.
"""
import sys
from pathlib import Path

target = Path(sys.argv[1] if len(sys.argv) > 1 else "mobile.html")
text = target.read_text()

# Patch 1: Agregar input HTML para event_dedup_s antes del botón
html_insert_after = '''        <div class="setting-row" style="flex-direction:column;align-items:flex-start;gap:6px">
          <div class="setting-label">Costo estimado por llamada (USD)</div>
          <input type="number" id="lim-cost" min="0.001" step="0.001"
                 placeholder="0.015" style="background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:9px 12px;color:var(--text);font-family:var(--mono);font-size:13px;outline:none;width:100%;box-sizing:border-box">
        </div>'''

html_new_input = '''        <div class="setting-row" style="flex-direction:column;align-items:flex-start;gap:6px">
          <div class="setting-label">Costo estimado por llamada (USD)</div>
          <input type="number" id="lim-cost" min="0.001" step="0.001"
                 placeholder="0.015" style="background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:9px 12px;color:var(--text);font-family:var(--mono);font-size:13px;outline:none;width:100%;box-sizing:border-box">
        </div>
        <div class="setting-row" style="flex-direction:column;align-items:flex-start;gap:6px">
          <div class="setting-label">Ventana de dedup eventos (seg)</div>
          <input type="number" id="lim-dedup" min="10" max="600"
                 placeholder="60" style="background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:9px 12px;color:var(--text);font-family:var(--mono);font-size:13px;outline:none;width:100%;box-sizing:border-box">
        </div>'''

if html_insert_after not in text:
    print("ERROR: no se encontró punto de inserción HTML")
    sys.exit(1)

text = text.replace(html_insert_after, html_new_input)

# Patch 2: Actualizar función saveLimits()
func_old = '''async function saveLimits() {
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
}'''

func_new = '''async function saveLimits() {
  const daily    = document.getElementById('lim-daily').value;
  const budget   = document.getElementById('lim-budget').value;
  const cooldown = document.getElementById('lim-cooldown').value;
  const cost     = document.getElementById('lim-cost').value;
  const dedup    = document.getElementById('lim-dedup').value;

  if (!daily || !budget || !cooldown || !cost || !dedup) {
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
        event_dedup_s:      parseInt(dedup),
      }),
    });
    const data = await res.json();
    alert(data.ok ? '✅ Límites guardados' : '❌ Error: ' + (data.message || ''));
    if (data.ok) await loadClaudeStats();
  } catch(e) {
    alert('Error: ' + e.message);
  }
}'''

if func_old not in text:
    print("ERROR: no se encontró función saveLimits() exacta")
    sys.exit(1)

text = text.replace(func_old, func_new)

target.write_text(text)
print(f"OK: event_dedup_s agregado a mobile.html (HTML + función)")
