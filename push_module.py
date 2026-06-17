"""
push_module.py — Web Push Notifications para HomeGuard AI
Integración en main.py:
    from push_module import push_router, init_push_db
    app.include_router(push_router)
    init_push_db()
"""
import json, sqlite3, os, logging
from pathlib import Path
from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger("homeguard.push")
push_router = APIRouter()

VAPID_PUBLIC_KEY   = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_PATH = os.getenv("VAPID_PRIVATE_KEY_PATH",
                     str(Path.home() / "homeguard/data/vapid_private.pem"))
VAPID_MAILTO       = os.getenv("VAPID_MAILTO", "mailto:admin@homeguard.local")
DB_PATH            = Path(os.getenv("DB_PATH",
                     str(Path.home() / "homeguard/data/homeguard.db")))

EVENT_EMOJI = {
    "person":"🚶","vehicle":"🚗","intrusion":"🚨","animal":"🐾",
    "fire":"🔥","smoke":"💨","tamper":"⚠️","gas":"☣️","health":"🩺",
}

def init_push_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint TEXT UNIQUE NOT NULL,
            p256dh   TEXT NOT NULL,
            auth     TEXT NOT NULL,
            label    TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_push  TIMESTAMP,
            last_error TEXT)""")
        conn.commit()
    logger.info("push_subscriptions table ready")

def _db(): return sqlite3.connect(DB_PATH)

class PushSubscription(BaseModel):
    endpoint: str
    keys: dict

class UnsubscribeBody(BaseModel):
    endpoint: str

@push_router.get("/api/push/vapid-public")
def get_vapid_public():
    if not VAPID_PUBLIC_KEY:
        return {"public_key": None, "configured": False}
    return {"public_key": VAPID_PUBLIC_KEY, "configured": True}

@push_router.post("/api/push/subscribe")
async def subscribe(sub: PushSubscription):
    p256dh = sub.keys.get("p256dh","")
    auth   = sub.keys.get("auth","")
    with _db() as conn:
        conn.execute(
            """INSERT INTO push_subscriptions (endpoint,p256dh,auth)
               VALUES (?,?,?)
               ON CONFLICT(endpoint) DO UPDATE SET
               p256dh=excluded.p256dh, auth=excluded.auth, last_error=NULL""",
            (sub.endpoint, p256dh, auth))
        conn.commit()
    logger.info(f"Push suscrito: {sub.endpoint[:50]}...")
    return {"status":"ok"}

@push_router.delete("/api/push/subscribe")
async def unsubscribe(body: UnsubscribeBody):
    with _db() as conn:
        conn.execute("DELETE FROM push_subscriptions WHERE endpoint=?",(body.endpoint,))
        conn.commit()
    return {"status":"ok"}

@push_router.get("/api/push/subscriptions")
def list_subs():
    with _db() as conn:
        rows = conn.execute(
            "SELECT id,endpoint,label,created_at,last_push,last_error "
            "FROM push_subscriptions ORDER BY created_at DESC").fetchall()
    return {"count":len(rows),"subscriptions":[
        {"id":r[0],"endpoint":r[1][:50]+"...","label":r[2],
         "created":r[3],"last_push":r[4],"last_error":r[5]} for r in rows]}

@push_router.post("/api/push/test")
async def test_push():
    return send_push_to_all("🔔 HomeGuard AI",
                            "Notificaciones activadas correctamente",
                            {"type":"test","url":"/mobile"})

def send_push_to_all(title:str, body:str,
                     data:Optional[dict]=None,
                     icon:str="/static/icon-192.png") -> dict:
    if not VAPID_PUBLIC_KEY:
        logger.warning("Push skip: VAPID_PUBLIC_KEY no configurada")
        return {"sent":0,"failed":0,"total":0,"error":"vapid_not_configured"}
    if not Path(VAPID_PRIVATE_PATH).exists():
        logger.warning(f"Push skip: clave privada no existe en {VAPID_PRIVATE_PATH}")
        return {"sent":0,"failed":0,"total":0,"error":"private_key_missing"}
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        logger.error("pywebpush no instalado")
        return {"sent":0,"failed":0,"total":0,"error":"pywebpush_missing"}

    payload = json.dumps({"title":title,"body":body,"icon":icon,"data":data or {}})
    vapid_claims = {"sub": VAPID_MAILTO}

    with _db() as conn:
        rows = conn.execute(
            "SELECT id,endpoint,p256dh,auth FROM push_subscriptions").fetchall()
    if not rows:
        return {"sent":0,"failed":0,"total":0}

    sent=failed=0
    stale=[]
    for sub_id, endpoint, p256dh, auth in rows:
        try:
            webpush(
                subscription_info={"endpoint":endpoint,"keys":{"p256dh":p256dh,"auth":auth}},
                data=payload,
                vapid_private_key=VAPID_PRIVATE_PATH,
                vapid_claims=vapid_claims)
            sent+=1
            with _db() as conn:
                conn.execute("UPDATE push_subscriptions SET last_push=CURRENT_TIMESTAMP,"
                             "last_error=NULL WHERE id=?",(sub_id,))
        except Exception as ex:
            failed+=1
            err=str(ex)
            if any(c in err for c in ("404","410")):
                stale.append(sub_id)
            else:
                logger.warning(f"Push error id={sub_id}: {err[:120]}")
                with _db() as conn:
                    conn.execute("UPDATE push_subscriptions SET last_error=? WHERE id=?",
                                 (err[:200],sub_id))
    if stale:
        with _db() as conn:
            conn.executemany("DELETE FROM push_subscriptions WHERE id=?",
                             [(s,) for s in stale])
            conn.commit()
    logger.info(f"Push sent={sent} failed={failed} total={len(rows)}")
    return {"sent":sent,"failed":failed,"total":len(rows)}

def send_push_event(event_type:str, camera_name:str,
                    description:str="", url:str="/mobile") -> dict:
    emoji = EVENT_EMOJI.get(event_type.lower(),"🔔")
    return send_push_to_all(
        title=f"{emoji} HomeGuard — {camera_name}",
        body=description or f"Evento: {event_type}",
        data={"type":event_type,"camera":camera_name,"url":url})
