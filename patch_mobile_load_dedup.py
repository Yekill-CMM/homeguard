"""
Agrega setInp para event_dedup_s en loadClaudeStats()
"""
import sys
from pathlib import Path

target = Path(sys.argv[1] if len(sys.argv) > 1 else "mobile.html")
text = target.read_text()

old_lines = '''    setInp('lim-daily',    cfg.daily_limit);
    setInp('lim-budget',   cfg.monthly_budget_usd);
    setInp('lim-cooldown', cfg.camera_cooldown_s);
    setInp('lim-cost',     cfg.cost_per_call_usd);
    set('cv-today','''

new_lines = '''    setInp('lim-daily',    cfg.daily_limit);
    setInp('lim-budget',   cfg.monthly_budget_usd);
    setInp('lim-cooldown', cfg.camera_cooldown_s);
    setInp('lim-cost',     cfg.cost_per_call_usd);
    setInp('lim-dedup',    cfg.event_dedup_s);
    set('cv-today','''

if old_lines not in text:
    print("ERROR: no se encontró el bloque setInp exacto")
    sys.exit(1)

text = text.replace(old_lines, new_lines)
target.write_text(text)
print(f"OK: setInp para event_dedup_s agregado a {target}")
