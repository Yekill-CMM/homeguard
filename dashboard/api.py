"""
HomeGuard AI — API REST + QR de instalación
"""

import io
import socket
import logging
import qrcode
from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi import Request as FastAPIRequest, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
_db = None


def _get_user_by_token(token: str):
    """Busca un usuario por su token de sesión (Bearer). Usa el _db global,
    compartido por todas las funciones add_*_routes de este módulo."""
    if not token:
        return None
    with _db._connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE token=? AND (pin_expiry IS NULL OR pin_expiry > datetime('now'))",
            (token,)
        ).fetchone()
    return dict(row) if row else None


async def require_auth(authorization: str = Header(None)) -> dict:
    """Dependency: exige un Bearer token válido. Usar en rutas que requieren sesión."""
    token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
    user = _get_user_by_token(token) if token else None
    if not user:
        raise HTTPException(status_code=401, detail="No autenticado")
    return user


def require_role(*roles: str):
    """Dependency factory: exige sesión válida Y rol dentro de `roles`."""
    async def _dep(user: dict = Depends(require_auth)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Permiso insuficiente")
        return user
    return _dep


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


    @app.get('/sw.js')
    async def service_worker():
        from fastapi.responses import FileResponse
        r = FileResponse(str(static_path / 'sw.js'), media_type='application/javascript')
        r.headers['Service-Worker-Allowed'] = '/'
        r.headers['Cache-Control'] = 'no-cache'
        return r

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
    async def qr_code(url: str = ""):
        """Genera el QR que apunta a la app móvil.
        Si se pasa ?url=... usa esa URL, si no genera la URL local por defecto.
        """
        if not url:
            ip = get_local_ip()
            url = f"http://{ip}:{port}/mobile?register=1"

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
    async def summary(user: dict = Depends(require_auth)):
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
        user: dict = Depends(require_auth),
    ):
        return _db.get_events(
            camera_id=camera_id, event_type=event_type,
            severity=severity, alerts_only=alerts_only,
            positive_only=positive_only, min_confidence=min_confidence,
            hours=hours, limit=limit, offset=offset,
        )

    @app.get("/api/alerts")
    async def alerts(limit: int = Query(default=20, le=100), user: dict = Depends(require_auth)):
        return _db.get_recent_alerts(limit=limit)

    @app.get("/api/stats")
    async def stats(days: int = Query(default=7, le=30), user: dict = Depends(require_auth)):
        return _db.get_daily_stats(days=days)

    @app.get("/api/cameras")
    async def cameras(user: dict = Depends(require_auth)):
        return _db.get_cameras()

    @app.get("/api/events/{event_id}")
    async def event_detail(event_id: str, user: dict = Depends(require_auth)):
        event = _db.get_event(event_id)
        if not event:
            return JSONResponse(status_code=404, content={"error": "No encontrado"})
        return event

    @app.get("/api/events/{event_id}/snapshot")
    async def event_snapshot(event_id: str, token: Optional[str] = None,
                              authorization: str = Header(None)):
        """Sirve el snapshot JPEG de un evento.
        Acepta el token por header (fetch) o por query param (<img src=...>,
        que no puede mandar headers custom)."""
        bearer = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
        if not _get_user_by_token(bearer or token):
            raise HTTPException(status_code=401, detail="No autenticado")
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
        person_name: str = ""
        endpoint: str
        p256dh: str
        auth: str

    @app.get("/api/push/vapid-key")
    async def vapid_public_key():
        """La app móvil obtiene la clave pública VAPID para suscribirse."""
        return {"public_key": vapid_manager.public_key}

    @app.post("/api/push/subscribe")
    async def subscribe(req: SubscribeRequest, request: FastAPIRequest):
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
        # Resolver person_name: del payload, o desde presence_devices por MAC/IP
        person_name = req.person_name
        if not person_name:
            try:
                from presence import mac_from_ip
                client_ip = request.headers.get("x-forwarded-for","").split(",")[0].strip()
                if not client_ip:
                    client_ip = request.client.host if request.client else ""
                mac = mac_from_ip(client_ip) if client_ip else None
                if mac:
                    with notifier.db._connect() as conn:
                        row = conn.execute(
                            "SELECT person_name FROM presence_devices "
                            "WHERE mac = ? COLLATE NOCASE AND enabled = 1",
                            (mac,)
                        ).fetchone()
                        if row:
                            person_name = row[0]
            except Exception:
                pass
        if person_name:
            try:
                with notifier.db._connect() as conn:
                    conn.execute(
                        "UPDATE push_subscriptions SET person_name = ? WHERE device_id = ?",
                        (person_name, req.device_id)
                    )
                    conn.commit()
            except Exception:
                pass
        return {"ok": ok, "devices": notifier.subscription_count()}

    @app.delete("/api/push/subscribe/{device_id}")
    async def unsubscribe(device_id: str, user: dict = Depends(require_role("admin"))):
        """Cancela las notificaciones para un dispositivo."""
        notifier.remove_subscription(device_id)
        return {"ok": True}

    @app.get("/api/push/devices")
    async def devices(user: dict = Depends(require_auth)):
        """Lista dispositivos suscritos."""
        subs = notifier.get_subscriptions()
        with notifier.db._connect() as conn:
            rows = conn.execute(
                "SELECT device_id, device_name, person_name, created_at, last_used "
                "FROM push_subscriptions ORDER BY created_at"
            ).fetchall()
        return [dict(r) for r in rows]

    @app.post("/api/push/test")
    async def test_push(user: dict = Depends(require_role("admin"))):
        """Envía una notificación de prueba a todos los dispositivos."""
        await notifier.notify_raw(
            title="🛡️ HomeGuard AI — Prueba",
            body="Las notificaciones push están funcionando correctamente",
            severity="low",
        )
        return {"ok": True, "devices": notifier.subscription_count()}



    class FeedbackBody(BaseModel):
        feedback: str  # "true_positive" | "false_positive"

    @app.post("/api/events/{event_id}/feedback")
    async def event_feedback(event_id: str, body: FeedbackBody, user: dict = Depends(require_auth)):
        if body.feedback not in ("true_positive", "false_positive"):
            return JSONResponse({"ok": False, "error": "valor inválido"}, status_code=400)
        try:
            with _db._connect() as conn:
                row = conn.execute(
                    "SELECT camera_name, event_type, timestamp FROM events WHERE id=?",
                    (event_id,)
                ).fetchone()
                if not row:
                    return JSONResponse({"ok": False, "error": "evento no encontrado"}, status_code=404)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS event_feedback (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_id TEXT UNIQUE NOT NULL,
                        camera_name TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        feedback TEXT NOT NULL,
                        event_timestamp TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )""")
                conn.execute(
                    "INSERT OR REPLACE INTO event_feedback "
                    "(event_id, camera_name, event_type, feedback, event_timestamp) "
                    "VALUES (?,?,?,?,?)",
                    (event_id, row[0], row[1], body.feedback, row[2])
                )
                conn.commit()
            return {"ok": True, "feedback": body.feedback}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

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
        role: str = "family"   # admin | family | tech
        pin: Optional[str] = None
        pin_expiry: Optional[str] = None

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
                    role        TEXT DEFAULT 'family',
                    pin_hash    TEXT,
                    token       TEXT,
                    pin_expiry  TEXT,
                    last_login  TEXT,
                    created_at  TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_users_token ON users(token);

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
    async def admin_get_cameras(user: dict = Depends(require_role("admin"))):
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
    async def admin_add_camera(cam: CameraCreate, user: dict = Depends(require_role("admin"))):
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
    async def admin_update_camera(cam_id: str, cam: CameraCreate, user: dict = Depends(require_role("admin"))):
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
    async def admin_delete_camera(cam_id: str, user: dict = Depends(require_role("admin"))):
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
    async def admin_get_sensors(user: dict = Depends(require_role("admin"))):
        with db._connect() as conn:
            rows = conn.execute("SELECT * FROM sensors ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    @app.post("/api/admin/sensors")
    async def admin_add_sensor(s: SensorCreate, user: dict = Depends(require_role("admin"))):
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
    async def admin_update_sensor(sid: str, s: SensorCreate, user: dict = Depends(require_role("admin"))):
        with db._connect() as conn:
            conn.execute("""
                UPDATE sensors SET name=?, type=?, location=?,
                mqtt_topic=?, enabled=? WHERE id=?
            """, (s.name, s.type, s.location, s.mqtt_topic,
                  1 if s.enabled else 0, sid))
            conn.commit()
        return {"ok": True}

    @app.delete("/api/admin/sensors/{sid}")
    async def admin_delete_sensor(sid: str, user: dict = Depends(require_role("admin"))):
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


    # ── AUTENTICACIÓN ────────────────────────────────────────
    from fastapi import Header, HTTPException
    import hashlib as _hl, uuid as _uuid

    def _get_user_by_token(token: str):
        if not token: return None
        with db._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE token=? AND (pin_expiry IS NULL OR pin_expiry > datetime('now'))",
                (token,)
            ).fetchone()
        return dict(row) if row else None

    class PinLogin(BaseModel):
        pin: str

    @app.post("/api/auth/login")
    async def auth_login(body: PinLogin):
        pin_hash = _hl.sha256(body.pin.encode()).hexdigest()
        with db._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE pin_hash=? AND (pin_expiry IS NULL OR pin_expiry > datetime('now'))",
                (pin_hash,)
            ).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="PIN incorrecto")
        user = dict(row)
        token = str(_uuid.uuid4()).replace("-", "")
        with db._connect() as conn:
            conn.execute("UPDATE users SET token=?, last_login=datetime('now') WHERE id=?",
                         (token, user["id"]))
            conn.commit()
        return {"ok": True, "token": token,
                "user": {"id": user["id"], "name": user["name"], "role": user["role"]}}

    @app.post("/api/auth/logout")
    async def auth_logout(authorization: str = Header(None)):
        token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
        if token:
            with db._connect() as conn:
                conn.execute("UPDATE users SET token=NULL WHERE token=?", (token,))
                conn.commit()
        return {"ok": True}

    @app.get("/api/auth/me")
    async def auth_me(authorization: str = Header(None)):
        token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
        user = _get_user_by_token(token) if token else None
        if not user: raise HTTPException(status_code=401, detail="No autenticado")
        return {"id": user["id"], "name": user["name"], "role": user["role"]}

    @app.get("/api/admin/users")
    async def admin_get_users(user: dict = Depends(require_role("admin"))):
        with db._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, role, created_at FROM users ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    @app.post("/api/admin/users")
    async def admin_add_user(u: UserCreate, user: dict = Depends(require_role("admin"))):
        import hashlib
        uid = u.id or new_id("usr")
        pin_hash = hashlib.sha256(u.pin.encode()).hexdigest() if u.pin else None
        with db._connect() as conn:
            for col in ["ALTER TABLE users ADD COLUMN token TEXT",
                        "ALTER TABLE users ADD COLUMN pin_expiry TEXT",
                        "ALTER TABLE users ADD COLUMN last_login TEXT"]:
                try: conn.execute(col)
                except Exception: pass
            conn.execute("""
                INSERT OR REPLACE INTO users
                    (id, name, role, pin_hash, pin_expiry, created_at)
                VALUES (?,?,?,?,?,?)
            """, (uid, u.name, u.role, pin_hash, u.pin_expiry, now()))
            conn.commit()
        return {"ok": True, "id": uid}

    @app.put("/api/admin/users/{uid}")
    async def admin_update_user(uid: str, u: UserCreate, user: dict = Depends(require_role("admin"))):
        import hashlib
        with db._connect() as conn:
            if u.pin:
                pin_hash = hashlib.sha256(u.pin.encode()).hexdigest()
                conn.execute(
                    "UPDATE users SET name=?, role=?, pin_hash=?, pin_expiry=? WHERE id=?",
                    (u.name, u.role, pin_hash, u.pin_expiry, uid)
                )
            else:
                conn.execute(
                    "UPDATE users SET name=?, role=?, pin_expiry=? WHERE id=?",
                    (u.name, u.role, u.pin_expiry, uid)
                )
            conn.commit()
        return {"ok": True}

    @app.delete("/api/admin/users/{uid}")
    async def admin_delete_user(uid: str, user: dict = Depends(require_role("admin"))):
        with db._connect() as conn:
            conn.execute("DELETE FROM users WHERE id=?", (uid,))
            conn.commit()
        return {"ok": True}

    # ── ZONAS ────────────────────────────────────────────────

    @app.get("/api/admin/zones")
    async def admin_get_zones(user: dict = Depends(require_role("admin"))):
        with db._connect() as conn:
            rows = conn.execute("SELECT * FROM zones ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    @app.post("/api/admin/zones")
    async def admin_add_zone(z: ZoneCreate, user: dict = Depends(require_role("admin"))):
        zid = z.id or new_id("zon")
        with db._connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO zones (id, name, type, armed, created_at)
                VALUES (?,?,?,?,?)
            """, (zid, z.name, z.type, 1 if z.armed else 0, now()))
            conn.commit()
        return {"ok": True, "id": zid}

    @app.put("/api/admin/zones/{zid}")
    async def admin_toggle_zone(zid: str, z: ZoneCreate, user: dict = Depends(require_role("admin"))):
        with db._connect() as conn:
            conn.execute(
                "UPDATE zones SET name=?, type=?, armed=? WHERE id=?",
                (z.name, z.type, 1 if z.armed else 0, zid)
            )
            conn.commit()
        return {"ok": True}

    @app.delete("/api/admin/zones/{zid}")
    async def admin_delete_zone(zid: str, user: dict = Depends(require_role("admin"))):
        with db._connect() as conn:
            conn.execute("DELETE FROM zones WHERE id=?", (zid,))
            conn.commit()
        return {"ok": True}

    # ── RESUMEN ADMIN ────────────────────────────────────────

    @app.get("/api/admin/summary")
    async def admin_summary(user: dict = Depends(require_role("admin"))):
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
        user: dict = Depends(require_auth),
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
    async def audio_summary(hours: int = 24, user: dict = Depends(require_auth)):
        return db.get_audio_summary(hours=hours)

    # ── GET /api/audio/sound-types — catálogo ───────────────
    @app.get("/api/audio/sound-types")
    async def audio_sound_types(user: dict = Depends(require_auth)):
        return [
            {"value": k, "label": v["label"],
             "emoji": v["emoji"], "severity": v["severity"]}
            for k, v in SOUND_TYPES.items()
        ]

    # ── POST /api/audio/test — simular evento para pruebas ──
    @app.post("/api/audio/test")
    async def audio_test(body: dict, user: dict = Depends(require_role("admin"))):
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
    async def infra_list(enabled_only: bool = False, user: dict = Depends(require_auth)):
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
    async def infra_get(device_id: str, user: dict = Depends(require_auth)):
        from fastapi import HTTPException
        d = db.get_infra_device(device_id)
        if not d:
            raise HTTPException(status_code=404, detail="Dispositivo no encontrado")
        return d

    @app.post("/api/infra/devices")
    async def infra_create(body: InfraDeviceIn, user: dict = Depends(require_role("admin"))):
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
    async def infra_update(device_id: str, body: InfraDeviceIn, user: dict = Depends(require_role("admin"))):
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
    async def infra_toggle(device_id: str, user: dict = Depends(require_role("admin"))):
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
    async def infra_delete(device_id: str, user: dict = Depends(require_role("admin"))):
        monitor = getattr(core, "health_monitor", None) if core else None
        if monitor:
            monitor._devices.pop(device_id, None)
        return {"ok": db.delete_infra_device(device_id)}

    @app.get("/api/infra/device-types")
    async def infra_types(user: dict = Depends(require_auth)):
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
    async def get_subnet(user: dict = Depends(require_role("admin"))):
        subnet = scanner._detect_local_subnet()
        return {"subnet": subnet}

    @app.get("/api/scanner/scan")
    async def scan_network(subnet: str = None, user: dict = Depends(require_role("admin"))):
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
    async def probe_ip(body: dict, user: dict = Depends(require_role("admin"))):
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
    async def health_devices(user: dict = Depends(require_auth)):
        """Estado de salud de todos los dispositivos."""
        monitor = getattr(core, 'health_monitor', None)
        if not monitor:
            return []
        return monitor.get_status()

    @app.get("/api/health/alerts")
    async def health_alerts(limit: int = 50, user: dict = Depends(require_auth)):
        """Últimas alertas del monitor de salud."""
        monitor = getattr(core, 'health_monitor', None)
        if not monitor:
            return []
        return monitor.get_alerts(limit=limit)

    @app.get("/api/health/summary")
    async def health_summary(user: dict = Depends(require_auth)):
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
        user: dict = Depends(require_auth),
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
            return []

    @app.get("/api/claude/stats")
    async def claude_stats(user: dict = Depends(require_auth)):
        """Estadísticas de uso y gasto de Claude Vision."""
        limiter = getattr(core, "limiter", None)
        if not limiter:
            return {"error": "Limiter no disponible"}
        return limiter.stats()

    @app.get("/api/claude/limits")
    async def claude_limits(user: dict = Depends(require_auth)):
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
        }

    @app.post("/api/claude/limits")
    async def claude_limits_set(body: dict, user: dict = Depends(require_role("admin"))):
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

            if "event_dedup_s" in body:
                val = max(10, int(body["event_dedup_s"]))
                if core and hasattr(core, "_dedup_window"):
                    core._dedup_window = val
                updated["EVENT_DEDUP_S"] = str(val)

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
                    env_text += f"\n{key}={val}\n"
            env_path.write_text(env_text)

        return {
            "ok":     True,
            "saved":  list(updated.keys()),
            "limits": {
                "daily_limit":        limiter.daily_limit        if limiter else None,
                "monthly_budget_usd": limiter.monthly_budget_usd if limiter else None,
                "camera_cooldown_s":  limiter.camera_cooldown_s  if limiter else None,
                "cost_per_call_usd":  limiter.cost_per_call_usd  if limiter else None,
                "event_dedup_s":       getattr(core, "_dedup_window", 60) if core else 60,
            }
        }

    @app.get("/api/claude/config")
    async def claude_config_get(user: dict = Depends(require_auth)):
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
        }

    @app.post("/api/claude/config")
    async def claude_config_set(body: dict, user: dict = Depends(require_role("admin"))):
        """Actualiza habilitación de Claude Vision y/o la API key."""
        from datetime import datetime
        from pathlib import Path as P
        results = {}

        # Toggle enabled/disabled (sin reiniciar)
        if "enabled" in body:
            enabled = bool(body["enabled"])
            # No permitir habilitar sin key válida
            if enabled and not getattr(core, "_key_valid", False):
                return {
                    "ok": False,
                    "message": "No se puede habilitar Claude Vision sin una API key válida",
                }
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
                    env_text += f"\nANTHROPIC_API_KEY={api_key}\n"
                env_path.write_text(env_text)
                results["api_key"] = "actualizada"
                # Reiniciar servicio automáticamente para aplicar la nueva key
                import subprocess
                try:
                    subprocess.run(
                        ["sudo", "systemctl", "restart", "homeguard"],
                        timeout=10, check=False
                    )
                    results["message"] = "API key guardada — servicio reiniciando..."
                    results["restarting"] = True
                except Exception as ex:
                    results["message"] = f"API key guardada en .env — reinicia manualmente: {ex}"
            else:
                return {"ok": False, "message": ".env no encontrado"}

        return {"ok": True, **results}
