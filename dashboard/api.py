"""
HomeGuard AI — API REST + QR de instalación
"""

import io
import socket
import logging
import qrcode
from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
_db = None


def get_local_ip() -> str:
    """Obtiene la IP local del servidor en la red LAN."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


def create_app(db, port: int = 8000) -> FastAPI:
    global _db
    _db = db

    app = FastAPI(title="HomeGuard AI", version="1.0.0")
    app.add_middleware(CORSMiddleware, allow_origins=["*"],
                       allow_methods=["*"], allow_headers=["*"])

    static_path = Path(__file__).parent / "static"
    if static_path.exists():
        app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

    # -------------------------------------------------------
    # Páginas
    # -------------------------------------------------------

    @app.get("/")
    async def dashboard():
        return FileResponse(str(static_path / "index.html"))

    @app.get("/mobile")
    async def mobile():
        """App móvil PWA — se accede desde el QR."""
        return FileResponse(str(static_path / "mobile.html"))

    @app.get("/admin")
    async def admin():
        """Panel de administración del sistema."""
        return FileResponse(str(static_path / "admin.html"))

    @app.get("/install")
    async def install_page():
        """Página de instalación que se muestra al escanear el QR."""
        ip = get_local_ip()
        html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes">
<link rel="manifest" href="/static/manifest.json">
<link rel="apple-touch-icon" href="/static/icon-192.png">
<title>Instalar HomeGuard AI</title>
<style>
  body {{ font-family: -apple-system, sans-serif; background: #080c10;
         color: #c8d8e8; display: flex; align-items: center;
         justify-content: center; min-height: 100vh; margin: 0; padding: 20px; }}
  .card {{ background: #0d1318; border: 1px solid #1e2d3d; border-radius: 16px;
           padding: 32px 24px; text-align: center; max-width: 340px; width: 100%; }}
  .icon {{ font-size: 56px; margin-bottom: 16px; }}
  h1 {{ font-size: 24px; font-weight: 700; color: #fff; margin: 0 0 8px; }}
  p  {{ font-size: 14px; color: #4a6070; margin: 0 0 24px; line-height: 1.5; }}
  .btn {{ display: block; background: #00d4ff; color: #080c10; border: none;
          border-radius: 10px; padding: 14px; font-size: 16px; font-weight: 700;
          cursor: pointer; text-decoration: none; margin-bottom: 10px; }}
  .btn-sec {{ background: transparent; color: #00d4ff;
              border: 1px solid #00d4ff; border-radius: 10px;
              padding: 14px; font-size: 15px; font-weight: 600;
              cursor: pointer; display: block; text-decoration: none; }}
  .note {{ font-size: 12px; color: #4a6070; margin-top: 20px; }}
</style>
</head>
<body>
<div class="card">
  <div class="icon">🛡️</div>
  <h1>HomeGuard AI</h1>
  <p>Sistema de seguridad residencial inteligente conectado a tu red local.</p>
  <a class="btn" href="/mobile">Abrir la app</a>
  <a class="btn-sec" href="/mobile">Ver en navegador</a>
  <div class="note">
    iOS: toca "Compartir" → "Agregar a inicio"<br>
    Android: toca "Instalar app" cuando aparezca
  </div>
</div>
<script>
  // Redirigir directamente a la app en móviles
  if (/iPhone|iPad|iPod|Android/i.test(navigator.userAgent)) {{
    setTimeout(() => window.location.href = '/mobile', 1500);
  }}
</script>
</body>
</html>"""
        from fastapi.responses import HTMLResponse
        return HTMLResponse(html)

    # -------------------------------------------------------
    # QR de instalación
    # -------------------------------------------------------

    @app.get("/api/qr")
    async def qr_code():
        """Genera el QR que apunta a la app móvil en la LAN."""
        ip = get_local_ip()
        url = f"http://{ip}:{port}/install"

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=8,
            border=3,
        )
        qr.add_data(url)
        qr.make(fit=True)

        img = qr.make_image(fill_color="#080c10", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        return StreamingResponse(buf, media_type="image/png",
                                 headers={"Cache-Control": "no-cache"})

    @app.get("/api/install-url")
    async def install_url():
        ip = get_local_ip()
        return {"url": f"http://{ip}:{port}/install",
                "mobile_url": f"http://{ip}:{port}/mobile",
                "ip": ip, "port": port}

    # -------------------------------------------------------
    # API de datos
    # -------------------------------------------------------

    @app.get("/api/summary")
    async def summary():
        return _db.get_summary()

    @app.get("/api/events")
    async def events(
        camera_id: Optional[str] = None,
        event_type: Optional[str] = None,
        severity: Optional[str] = None,
        alerts_only: bool = False,
        positive_only: bool = False,
        min_confidence: float = 0.0,
        hours: int = Query(default=24, le=168),
        limit: int = Query(default=50, le=200),
        offset: int = 0,
    ):
        return _db.get_events(
            camera_id=camera_id, event_type=event_type,
            severity=severity, alerts_only=alerts_only,
            positive_only=positive_only, min_confidence=min_confidence,
            hours=hours, limit=limit, offset=offset,
        )

    @app.get("/api/alerts")
    async def alerts(limit: int = Query(default=20, le=100)):
        return _db.get_recent_alerts(limit=limit)

    @app.get("/api/stats")
    async def stats(days: int = Query(default=7, le=30)):
        return _db.get_daily_stats(days=days)

    @app.get("/api/cameras")
    async def cameras():
        return _db.get_cameras()

    @app.get("/api/events/{event_id}")
    async def event_detail(event_id: str):
        event = _db.get_event(event_id)
        if not event:
            return JSONResponse(status_code=404, content={"error": "No encontrado"})
        return event

    @app.get("/api/events/{event_id}/snapshot")
    async def event_snapshot(event_id: str):
        """Sirve el snapshot JPEG de un evento."""
        from fastapi.responses import FileResponse
        import os
        event = _db.get_event(event_id)
        if not event or not event.get("snapshot_path"):
            return JSONResponse(status_code=404, content={"error": "Sin snapshot"})

        rel_path = event["snapshot_path"]

        # Intentar varias rutas base posibles
        candidates = [
            rel_path,
            os.path.join(os.getcwd(), rel_path),
            os.path.join(os.path.dirname(_db.db_path), "..", rel_path),
            os.path.join(os.path.expanduser("~"), "homeguard", "data", rel_path),
            os.path.join(os.path.expanduser("~"), "homeguard", rel_path),
        ]

        for path in candidates:
            path = os.path.normpath(path)
            if os.path.exists(path):
                return FileResponse(path, media_type="image/jpeg")

        return JSONResponse(status_code=404, content={"error": f"Archivo no encontrado: {rel_path}"})

    @app.get("/api/health")
    async def health():
        s = _db.get_summary()
        return {"status": "online", "db_size_mb": round(_db.db_size_mb(), 2),
                "total_events": s["total_events"]}

    return app


def add_push_routes(app: FastAPI, notifier, vapid_manager):
    """Agrega endpoints de push notifications a la app FastAPI."""
    from notifications.push import PushSubscription
    from pydantic import BaseModel

    class SubscribeRequest(BaseModel):
        device_id: str
        device_name: str = "Dispositivo"
        endpoint: str
        p256dh: str
        auth: str

    @app.get("/api/push/vapid-key")
    async def vapid_public_key():
        """La app móvil obtiene la clave pública VAPID para suscribirse."""
        return {"public_key": vapid_manager.public_key}

    @app.post("/api/push/subscribe")
    async def subscribe(req: SubscribeRequest):
        """Registra un dispositivo para recibir notificaciones push."""
        from datetime import datetime
        sub = PushSubscription(
            device_id=req.device_id,
            device_name=req.device_name,
            endpoint=req.endpoint,
            p256dh=req.p256dh,
            auth=req.auth,
            created_at=datetime.now().isoformat(),
        )
        ok = notifier.save_subscription(sub)
        return {"ok": ok, "devices": notifier.subscription_count()}

    @app.delete("/api/push/subscribe/{device_id}")
    async def unsubscribe(device_id: str):
        """Cancela las notificaciones para un dispositivo."""
        notifier.remove_subscription(device_id)
        return {"ok": True}

    @app.get("/api/push/devices")
    async def devices():
        """Lista dispositivos suscritos."""
        subs = notifier.get_subscriptions()
        return [{"device_id": s.device_id, "device_name": s.device_name,
                 "created_at": s.created_at} for s in subs]

    @app.post("/api/push/test")
    async def test_push():
        """Envía una notificación de prueba a todos los dispositivos."""
        await notifier.notify_raw(
            title="🛡️ HomeGuard AI — Prueba",
            body="Las notificaciones push están funcionando correctamente",
            severity="low",
        )
        return {"ok": True, "devices": notifier.subscription_count()}


def add_admin_routes(app: FastAPI, db, core=None):
    """Endpoints de administración del sistema."""
    from pydantic import BaseModel
    from typing import Optional
    from datetime import datetime

    # ── Modelos ──────────────────────────────────────────────

    class CameraCreate(BaseModel):
        id: Optional[str] = None
        name: str
        rtsp_url: str
        source_type: str = "rtsp"   # rtsp | onvif
        analysis_fps: int = 5
        enabled: bool = True
        onvif_user: str = ""
        onvif_password: str = ""
        ai_confidence_threshold: float = 0.85

    class SensorCreate(BaseModel):
        id: Optional[str] = None
        name: str
        type: str          # pir | smoke | gas | co2 | door | window | vibration
        location: str      # zona del hogar
        mqtt_topic: str
        enabled: bool = True

    class UserCreate(BaseModel):
        id: Optional[str] = None
        name: str
        role: str = "viewer"   # admin | viewer
        pin: Optional[str] = None

    class ZoneCreate(BaseModel):
        id: Optional[str] = None
        name: str
        type: str = "interior"   # interior | exterior | perimeter
        armed: bool = True

    # ── Setup de tablas ──────────────────────────────────────

    def ensure_admin_tables():
        with db._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sensors (
                    id          TEXT PRIMARY KEY,
                    name        TEXT NOT NULL,
                    type        TEXT NOT NULL,
                    location    TEXT NOT NULL,
                    mqtt_topic  TEXT NOT NULL,
                    enabled     INTEGER DEFAULT 1,
                    last_seen   TEXT,
                    created_at  TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS users (
                    id          TEXT PRIMARY KEY,
                    name        TEXT NOT NULL,
                    role        TEXT DEFAULT 'viewer',
                    pin_hash    TEXT,
                    created_at  TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS zones (
                    id          TEXT PRIMARY KEY,
                    name        TEXT NOT NULL,
                    type        TEXT DEFAULT 'interior',
                    armed       INTEGER DEFAULT 1,
                    created_at  TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS camera_config (
                    id                      TEXT PRIMARY KEY,
                    name                    TEXT NOT NULL,
                    rtsp_url                TEXT,
                    source_type             TEXT DEFAULT 'rtsp',
                    analysis_fps            INTEGER DEFAULT 5,
                    enabled                 INTEGER DEFAULT 1,
                    onvif_user              TEXT DEFAULT '',
                    onvif_password          TEXT DEFAULT '',
                    ai_confidence_threshold REAL DEFAULT 0.85,
                    created_at              TEXT NOT NULL
                );
            """)
            conn.commit()

    ensure_admin_tables()

    # Sincronizar cámaras del sistema → camera_config
    def sync_cameras():
        from datetime import datetime
        ts = datetime.now().isoformat()
        with db._connect() as conn:
            existing = conn.execute("SELECT id FROM camera_config").fetchall()
            existing_ids = {r[0] for r in existing}
            system_cams = conn.execute("SELECT id, name, rtsp_url, source_type FROM cameras").fetchall()
            for cam in system_cams:
                if cam[0] not in existing_ids:
                    conn.execute("""
                        INSERT OR IGNORE INTO camera_config
                        (id, name, rtsp_url, source_type, analysis_fps, enabled,
                         onvif_user, onvif_password, ai_confidence_threshold, created_at)
                        VALUES (?,?,?,?,5,1,'','',0.85,?)
                    """, (cam[0], cam[1], cam[2], cam[3] or 'rtsp', ts))
            conn.commit()

    sync_cameras()

    def new_id(prefix: str) -> str:
        import uuid
        return f"{prefix}_{uuid.uuid4().hex[:8]}"

    def now() -> str:
        return datetime.now().isoformat()

    # ── CÁMARAS ─────────────────────────────────────────────

    @app.get("/api/admin/cameras")
    async def admin_get_cameras():
        with db._connect() as conn:
            # Combinar camera_config (agregadas desde admin) y cameras (del sistema)
            rows = conn.execute("""
                SELECT id, name, rtsp_url, source_type,
                       analysis_fps, enabled, onvif_user,
                       onvif_password, ai_confidence_threshold, created_at
                FROM camera_config
                UNION
                SELECT id, name, rtsp_url, source_type,
                       5 as analysis_fps, enabled,
                       '' as onvif_user, '' as onvif_password,
                       0.85 as ai_confidence_threshold, created_at
                FROM cameras
                WHERE id NOT IN (SELECT id FROM camera_config)
                ORDER BY created_at DESC
            """).fetchall()
        return [dict(r) for r in rows]

    @app.post("/api/admin/cameras")
    async def admin_add_camera(cam: CameraCreate):
        cam_id = cam.id or new_id("cam")
        with db._connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO camera_config
                (id, name, rtsp_url, source_type, analysis_fps, enabled,
                 onvif_user, onvif_password, ai_confidence_threshold, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (cam_id, cam.name, cam.rtsp_url, cam.source_type,
                  cam.analysis_fps, 1 if cam.enabled else 0,
                  cam.onvif_user, cam.onvif_password,
                  cam.ai_confidence_threshold, now()))
            conn.commit()
        # Registrar también en tabla cameras del sistema
        db.register_camera(cam_id, cam.name, cam.rtsp_url or "", cam.source_type)
        return {"ok": True, "id": cam_id}

    @app.put("/api/admin/cameras/{cam_id}")
    async def admin_update_camera(cam_id: str, cam: CameraCreate):
        with db._connect() as conn:
            conn.execute("""
                UPDATE camera_config SET name=?, rtsp_url=?, source_type=?,
                analysis_fps=?, enabled=?, onvif_user=?, onvif_password=?,
                ai_confidence_threshold=? WHERE id=?
            """, (cam.name, cam.rtsp_url, cam.source_type, cam.analysis_fps,
                  1 if cam.enabled else 0, cam.onvif_user, cam.onvif_password,
                  cam.ai_confidence_threshold, cam_id))
            conn.commit()
        return {"ok": True}

    @app.delete("/api/admin/cameras/{cam_id}")
    async def admin_delete_camera(cam_id: str):
        with db._connect() as conn:
            conn.execute("DELETE FROM camera_config WHERE id=?", (cam_id,))
            conn.execute("DELETE FROM cameras WHERE id=?", (cam_id,))
            conn.commit()
        # Limpiar del HealthMonitor
        monitor = getattr(core, "health_monitor", None) if core else None
        if monitor:
            monitor._devices.pop(cam_id, None)
            monitor._alert_history = [
                a for a in monitor._alert_history if a.device_id != cam_id
            ]
        return {"ok": True}

    # ── SENSORES ─────────────────────────────────────────────

    @app.get("/api/admin/sensors")
    async def admin_get_sensors():
        with db._connect() as conn:
            rows = conn.execute("SELECT * FROM sensors ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    @app.post("/api/admin/sensors")
    async def admin_add_sensor(s: SensorCreate):
        sid = s.id or new_id("sen")
        with db._connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO sensors
                (id, name, type, location, mqtt_topic, enabled, created_at)
                VALUES (?,?,?,?,?,?,?)
            """, (sid, s.name, s.type, s.location, s.mqtt_topic,
                  1 if s.enabled else 0, now()))
            conn.commit()
        return {"ok": True, "id": sid}

    @app.put("/api/admin/sensors/{sid}")
    async def admin_update_sensor(sid: str, s: SensorCreate):
        with db._connect() as conn:
            conn.execute("""
                UPDATE sensors SET name=?, type=?, location=?,
                mqtt_topic=?, enabled=? WHERE id=?
            """, (s.name, s.type, s.location, s.mqtt_topic,
                  1 if s.enabled else 0, sid))
            conn.commit()
        return {"ok": True}

    @app.delete("/api/admin/sensors/{sid}")
    async def admin_delete_sensor(sid: str):
        with db._connect() as conn:
            conn.execute("DELETE FROM sensors WHERE id=?", (sid,))
            conn.commit()
        # Limpiar del HealthMonitor
        monitor = getattr(core, "health_monitor", None) if core else None
        if monitor:
            monitor._devices.pop(sid, None)
            monitor._alert_history = [
                a for a in monitor._alert_history if a.device_id != sid
            ]
        return {"ok": True}

    # ── USUARIOS ─────────────────────────────────────────────

    @app.get("/api/admin/users")
    async def admin_get_users():
        with db._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, role, created_at FROM users ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    @app.post("/api/admin/users")
    async def admin_add_user(u: UserCreate):
        import hashlib
        uid = u.id or new_id("usr")
        pin_hash = hashlib.sha256(u.pin.encode()).hexdigest() if u.pin else None
        with db._connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO users (id, name, role, pin_hash, created_at)
                VALUES (?,?,?,?,?)
            """, (uid, u.name, u.role, pin_hash, now()))
            conn.commit()
        return {"ok": True, "id": uid}

    @app.delete("/api/admin/users/{uid}")
    async def admin_delete_user(uid: str):
        with db._connect() as conn:
            conn.execute("DELETE FROM users WHERE id=?", (uid,))
            conn.commit()
        return {"ok": True}

    # ── ZONAS ────────────────────────────────────────────────

    @app.get("/api/admin/zones")
    async def admin_get_zones():
        with db._connect() as conn:
            rows = conn.execute("SELECT * FROM zones ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    @app.post("/api/admin/zones")
    async def admin_add_zone(z: ZoneCreate):
        zid = z.id or new_id("zon")
        with db._connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO zones (id, name, type, armed, created_at)
                VALUES (?,?,?,?,?)
            """, (zid, z.name, z.type, 1 if z.armed else 0, now()))
            conn.commit()
        return {"ok": True, "id": zid}

    @app.put("/api/admin/zones/{zid}")
    async def admin_toggle_zone(zid: str, z: ZoneCreate):
        with db._connect() as conn:
            conn.execute(
                "UPDATE zones SET name=?, type=?, armed=? WHERE id=?",
                (z.name, z.type, 1 if z.armed else 0, zid)
            )
            conn.commit()
        return {"ok": True}

    @app.delete("/api/admin/zones/{zid}")
    async def admin_delete_zone(zid: str):
        with db._connect() as conn:
            conn.execute("DELETE FROM zones WHERE id=?", (zid,))
            conn.commit()
        return {"ok": True}

    # ── RESUMEN ADMIN ────────────────────────────────────────

    @app.get("/api/admin/summary")
    async def admin_summary():
        with db._connect() as conn:
            cams    = conn.execute("""
                SELECT COUNT(*) FROM (
                    SELECT id FROM camera_config
                    UNION
                    SELECT id FROM cameras
                )
            """).fetchone()[0]
            sensors = conn.execute("SELECT COUNT(*) FROM sensors").fetchone()[0]
            users   = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            zones   = conn.execute("SELECT COUNT(*) FROM zones").fetchone()[0]
            devices = conn.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0] if \
                      conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='push_subscriptions'").fetchone() else 0
        return {"cameras": cams, "sensors": sensors,
                "users": users, "zones": zones, "devices": devices}


def add_audio_routes(app: FastAPI, db, core=None):
    """
    Endpoints de audio para HomeGuard AI.
    Recibe webhooks de Home Assistant con eventos de sonido detectados.

    Payload esperado de HA:
    {
      "sound_type": "scream|glass|bark|alarm|cry|voice|noise",
      "confidence": 0.95,
      "zone": "Sala principal",
      "device_name": "Micrófono sala",
      "duration_ms": 1200,
      "db_level": 72.5
    }
    """
    import uuid as _uuid
    import datetime as _dt
    import json as _json

    # Tipos válidos y sus etiquetas para UI
    SOUND_TYPES = {
        "scream": {"label": "Grito",           "emoji": "😱", "severity": "critical"},
        "glass":  {"label": "Cristal roto",    "emoji": "🪟", "severity": "critical"},
        "alarm":  {"label": "Sirena / alarma", "emoji": "🚨", "severity": "critical"},
        "cry":    {"label": "Llanto",          "emoji": "😢", "severity": "high"},
        "bark":   {"label": "Ladrido",         "emoji": "🐕", "severity": "medium"},
        "voice":  {"label": "Voz detectada",   "emoji": "🗣️", "severity": "medium"},
        "noise":  {"label": "Ruido anormal",   "emoji": "🔊", "severity": "low"},
    }

    async def _send_audio_alert(event: dict):
        """Envía notificación push para eventos críticos/high."""
        notifier = getattr(core, "notifier", None) if core else None
        if not notifier:
            return
        info = SOUND_TYPES.get(event["sound_type"], {})
        severity = info.get("severity", "low")
        if severity not in ("critical", "high"):
            return
        emoji = info.get("emoji", "🔊")
        label = info.get("label", event["sound_type"])
        zone  = event.get("zone", "Zona desconocida")
        conf  = int(event.get("confidence", 0) * 100)
        await notifier.notify_raw(
            title=f"{emoji} {label} detectado",
            body=f"{zone} — Confianza {conf}%",
            severity=severity,
        )

    # ── POST /api/audio/event — receptor webhook de HA ──────
    @app.post("/api/audio/event")
    async def audio_webhook(body: dict):
        """
        Receptor de webhooks desde Home Assistant.
        HA llama a este endpoint cuando detecta un sonido clasificado.
        """
        sound_type = body.get("sound_type", "noise").lower()
        if sound_type not in SOUND_TYPES:
            sound_type = "noise"

        info = SOUND_TYPES[sound_type]
        now  = _dt.datetime.now().isoformat()

        event = {
            "id":          str(_uuid.uuid4()),
            "timestamp":   body.get("timestamp", now),
            "sound_type":  sound_type,
            "confidence":  float(body.get("confidence", 0.0)),
            "zone":        body.get("zone"),
            "device_name": body.get("device_name"),
            "duration_ms": body.get("duration_ms"),
            "db_level":    body.get("db_level"),
            "severity":    info["severity"],
            "raw_payload": body,
        }

        ok = db.save_audio_event(event)

        if ok:
            import asyncio
            asyncio.create_task(_send_audio_alert(event))

        logger.info(
            f"[Audio] {info['emoji']} {info['label']} — "
            f"zona: {event.get('zone','?')} — "
            f"confianza: {int(event['confidence']*100)}%"
        )
        return {"ok": ok, "id": event["id"], "severity": info["severity"]}

    # ── GET /api/audio/events — listado con filtros ──────────
    @app.get("/api/audio/events")
    async def audio_events(
        sound_type: str = None,
        severity:   str = None,
        hours:      int = 24,
        limit:      int = 100,
        offset:     int = 0,
    ):
        return db.get_audio_events(
            sound_type=sound_type,
            severity=severity,
            hours=hours,
            limit=limit,
            offset=offset,
        )

    # ── GET /api/audio/summary — resumen para dashboard ─────
    @app.get("/api/audio/summary")
    async def audio_summary(hours: int = 24):
        return db.get_audio_summary(hours=hours)

    # ── GET /api/audio/sound-types — catálogo ───────────────
    @app.get("/api/audio/sound-types")
    async def audio_sound_types():
        return [
            {"value": k, "label": v["label"],
             "emoji": v["emoji"], "severity": v["severity"]}
            for k, v in SOUND_TYPES.items()
        ]

    # ── POST /api/audio/test — simular evento para pruebas ──
    @app.post("/api/audio/test")
    async def audio_test(body: dict):
        """Simula un evento de audio para probar la integración."""
        sound_type = body.get("sound_type", "bark")
        test_payload = {
            "sound_type":  sound_type,
            "confidence":  body.get("confidence", 0.92),
            "zone":        body.get("zone", "Sala principal"),
            "device_name": "TEST — Simulación",
            "duration_ms": 800,
            "db_level":    65.0,
        }
        return await audio_webhook(test_payload)


def add_infra_routes(app: FastAPI, db, core=None):
    """
    CRUD completo para dispositivos de infraestructura.
    Tipos: nvr | router | ups | server | hub | intrusion_panel | fire_panel | other
    """
    from pydantic import BaseModel
    from typing import Optional as Opt
    import uuid
    import datetime as _dt

    VALID_TYPES = {
        "nvr", "router", "ups", "server",
        "hub", "intrusion_panel", "fire_panel", "other"
    }

    class InfraDeviceIn(BaseModel):
        name: str
        device_type: str
        host: str
        port: int = 80
        location: Opt[str] = None
        brand: Opt[str] = None
        model: Opt[str] = None
        notes: Opt[str] = None
        enabled: bool = True
        monitor_health: bool = True

    def _sync_health(device: dict):
        if not core:
            return
        monitor = getattr(core, "health_monitor", None)
        if not monitor:
            return
        if device.get("enabled") and device.get("monitor_health"):
            from core.health_monitor import DeviceHealth
            monitor.register_device(DeviceHealth(
                device_id=device["id"],
                device_name=device["name"],
                device_type=device["device_type"],
                host=device["host"],
                port=int(device.get("port") or 80),
            ))
        else:
            monitor._devices.pop(device["id"], None)

    @app.get("/api/infra/devices")
    async def infra_list(enabled_only: bool = False):
        devices = db.get_infra_devices(enabled_only=enabled_only)
        monitor = getattr(core, "health_monitor", None) if core else None
        health_map = {s["device_id"]: s for s in monitor.get_status()} if monitor else {}
        for d in devices:
            h = health_map.get(d["id"])
            d["health"] = {
                "online":     h["online"],
                "latency_ms": h["latency_ms"],
                "last_seen":  h["last_seen"],
            } if h else None
        return devices

    @app.get("/api/infra/devices/{device_id}")
    async def infra_get(device_id: str):
        from fastapi import HTTPException
        d = db.get_infra_device(device_id)
        if not d:
            raise HTTPException(status_code=404, detail="Dispositivo no encontrado")
        return d

    @app.post("/api/infra/devices")
    async def infra_create(body: InfraDeviceIn):
        from fastapi import HTTPException
        if body.device_type not in VALID_TYPES:
            raise HTTPException(status_code=400,
                detail=f"device_type invalido. Opciones: {', '.join(sorted(VALID_TYPES))}")
        now = _dt.datetime.now().isoformat()
        device = {
            "id":             f"infra_{uuid.uuid4().hex[:8]}",
            "name":           body.name,
            "device_type":    body.device_type,
            "host":           body.host,
            "port":           body.port,
            "location":       body.location,
            "brand":          body.brand,
            "model":          body.model,
            "notes":          body.notes,
            "enabled":        body.enabled,
            "monitor_health": body.monitor_health,
            "created_at":     now,
            "updated_at":     now,
        }
        ok = db.save_infra_device(device)
        if ok:
            _sync_health(device)
        return {"ok": ok, "id": device["id"]}

    @app.put("/api/infra/devices/{device_id}")
    async def infra_update(device_id: str, body: InfraDeviceIn):
        from fastapi import HTTPException
        if not db.get_infra_device(device_id):
            raise HTTPException(status_code=404, detail="Dispositivo no encontrado")
        if body.device_type not in VALID_TYPES:
            raise HTTPException(status_code=400,
                detail=f"device_type invalido. Opciones: {', '.join(sorted(VALID_TYPES))}")
        ok = db.update_infra_device(device_id, body.model_dump())
        if ok:
            _sync_health(db.get_infra_device(device_id))
        return {"ok": ok}

    @app.patch("/api/infra/devices/{device_id}/toggle")
    async def infra_toggle(device_id: str):
        from fastapi import HTTPException
        d = db.get_infra_device(device_id)
        if not d:
            raise HTTPException(status_code=404, detail="Dispositivo no encontrado")
        new_state = not bool(d["enabled"])
        ok = db.update_infra_device(device_id, {"enabled": new_state})
        if ok:
            _sync_health(db.get_infra_device(device_id))
        return {"ok": ok, "enabled": new_state}

    @app.delete("/api/infra/devices/{device_id}")
    async def infra_delete(device_id: str):
        monitor = getattr(core, "health_monitor", None) if core else None
        if monitor:
            monitor._devices.pop(device_id, None)
        return {"ok": db.delete_infra_device(device_id)}

    @app.get("/api/infra/device-types")
    async def infra_types():
        return [
            {"value": "nvr",             "label": "NVR / Grabador"},
            {"value": "router",          "label": "Router / Switch"},
            {"value": "ups",             "label": "UPS / Fuente de poder"},
            {"value": "server",          "label": "Servidor / NUC"},
            {"value": "hub",             "label": "Hub / Central de sensores"},
            {"value": "intrusion_panel", "label": "Central de intrusion"},
            {"value": "fire_panel",      "label": "Central de incendio / gas"},
            {"value": "other",           "label": "Otro"},
        ]


def add_scanner_routes(app: FastAPI, db):
    """Endpoints del escáner de red LAN."""
    from core.scanner import NetworkScanner
    import json

    scanner = NetworkScanner(timeout=0.8, max_concurrent=50)

    @app.get("/api/scanner/subnet")
    async def get_subnet():
        subnet = scanner._detect_local_subnet()
        return {"subnet": subnet}

    @app.get("/api/scanner/scan")
    async def scan_network(subnet: str = None):
        """Escanea la red y retorna dispositivos encontrados."""
        try:
            devices = await scanner.scan(subnet=subnet)
            return {
                "devices": [d.to_dict() for d in devices],
                "total": len(devices),
            }
        except Exception as e:
            return {"error": str(e), "devices": [], "total": 0}

    @app.post("/api/scanner/probe")
    async def probe_ip(body: dict):
        """Analiza una IP específica ingresada manualmente."""
        ip = body.get("ip", "").strip()
        if not ip:
            return {"error": "IP requerida"}
        try:
            device = await scanner.probe_ip(ip)
            return device.to_dict()
        except Exception as e:
            return {"error": str(e), "ip": ip}


def add_health_routes(app: FastAPI, core):
    """Endpoints del monitor de salud del sistema."""

    @app.get("/api/health/devices")
    async def health_devices():
        """Estado de salud de todos los dispositivos."""
        monitor = getattr(core, 'health_monitor', None)
        if not monitor:
            return []
        return monitor.get_status()

    @app.get("/api/health/alerts")
    async def health_alerts(limit: int = 50):
        """Últimas alertas del monitor de salud."""
        monitor = getattr(core, 'health_monitor', None)
        if not monitor:
            return []
        return monitor.get_alerts(limit=limit)

    @app.get("/api/health/summary")
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
