#!/usr/bin/env python3
"""
health_status.py — HomeGuard AI
Consulta rápida del estado de salud del sistema desde la terminal.

Uso:
    python3 health_status.py              # últimos 20 eventos
    python3 health_status.py --limit 50   # últimos 50 eventos
    python3 health_status.py --current    # solo estado actual por componente
    python3 health_status.py --hoy        # solo eventos de hoy

Ejemplo de salida:
    ──────────────────────────────────────────────────────
    ESTADO ACTUAL DEL SISTEMA — HomeGuard AI
    ──────────────────────────────────────────────────────
    internet              ✅ OK
    disk                  ✅ OK
    camera_1 (Entrada)    🔴 CRÍTICO   — [Entrada] Timeout al conectar 192.168.1.110:554
    camera_2 (Patio)      ✅ OK
    ──────────────────────────────────────────────────────
"""

import argparse
import sqlite3
from datetime import date
from pathlib import Path

DB_PATH = Path.home() / "homeguard" / "data" / "homeguard.db"

STATUS_ICON = {
    "OK":       "✅ OK      ",
    "WARNING":  "🟡 WARNING ",
    "CRITICAL": "🔴 CRÍTICO ",
}


def get_current_status(conn: sqlite3.Connection) -> None:
    """Muestra el último estado conocido por componente."""
    rows = conn.execute("""
        SELECT component, status, detail, timestamp
        FROM health_events
        WHERE id IN (
            SELECT MAX(id) FROM health_events GROUP BY component
        )
        ORDER BY component
    """).fetchall()

    print("\n" + "─" * 62)
    print("  ESTADO ACTUAL DEL SISTEMA — HomeGuard AI")
    print("─" * 62)

    if not rows:
        print("  Sin datos. El watchdog aún no ha registrado eventos.")
    else:
        for component, status, detail, ts in rows:
            icon = STATUS_ICON.get(status, "❓")
            print(f"  {component:<22} {icon}  {ts}")
            if status != "OK":
                print(f"  {'':22}   └─ {detail}")

    print("─" * 62 + "\n")


def get_recent_events(conn: sqlite3.Connection, limit: int, hoy: bool) -> None:
    """Muestra los últimos N eventos (o solo los de hoy)."""
    query = "SELECT timestamp, component, status, detail, is_recovery FROM health_events"
    params: list = []

    if hoy:
        query += " WHERE date(timestamp) = ?"
        params.append(str(date.today()))

    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()

    print("\n" + "─" * 62)
    titulo = f"  ÚLTIMOS {limit} EVENTOS" + (" (HOY)" if hoy else "")
    print(titulo)
    print("─" * 62)

    if not rows:
        print("  Sin eventos registrados.")
    else:
        for ts, component, status, detail, is_recovery in rows:
            if is_recovery:
                icon = "✅ RECOVERY"
            else:
                icon = STATUS_ICON.get(status, "❓").strip()
            print(f"  {ts}  {component:<18}  {icon}")
            if detail:
                print(f"  {'':19}  └─ {detail}")

    print("─" * 62 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Estado de salud — HomeGuard AI")
    parser.add_argument("--limit",   type=int, default=20, help="Número de eventos a mostrar")
    parser.add_argument("--current", action="store_true",  help="Solo estado actual por componente")
    parser.add_argument("--hoy",     action="store_true",  help="Solo eventos de hoy")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"❌ Base de datos no encontrada: {DB_PATH}")
        raise SystemExit(1)

    with sqlite3.connect(str(DB_PATH)) as conn:
        if args.current:
            get_current_status(conn)
        else:
            get_recent_events(conn, limit=args.limit, hoy=args.hoy)


if __name__ == "__main__":
    main()
