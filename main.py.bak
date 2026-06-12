"""
HomeGuard AI — Punto de entrada principal v2
"""

import asyncio
import logging
import signal
import sys
import os

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config.settings import load_config, CameraConfig
from core.engine import HomeGuardCore
from core.health_monitor import HealthMonitor
from adapters.rtsp_adapter import RTSPAdapter
from adapters.onvif_adapter import ONVIFAdapter
from adapters.mqtt_adapter import MQTTAdapter
from dashboard.api import create_app, add_push_routes
from notifications.vapid import VAPIDManager
from notifications.push import PushNotifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("homeguard.main")


async def main():
    config = load_config()

    if not config.claude.api_key:
        logger.error("ANTHROPIC_API_KEY no configurada.")
        sys.exit(1)

    os.makedirs(config.storage.clips_path, exist_ok=True)
    os.makedirs(config.storage.frames_path, exist_ok=True)

    logger.info("=" * 60)
    logger.info("HomeGuard AI — Sistema de seguridad residencial v2")
    logger.info(f"Modelo: {config.claude.model}")
    logger.info("=" * 60)

    core = HomeGuardCore(config.claude, config.storage)

    vapid = VAPIDManager(core.db)
    notifier = PushNotifier(core.db, vapid)
    core.notifier = notifier
    logger.info(f"Push notifications listas — {notifier.subscription_count()} dispositivo(s)")

    # ── Cargar cámaras desde DB con fallback a settings.py ──────────
    db_cameras = []
    try:
        with core.db._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, rtsp_url, source_type, analysis_fps, "
                "enabled, onvif_user, onvif_password, ai_confidence_threshold "
                "FROM camera_config WHERE enabled=1"
            ).fetchall()
            for r in rows:
                cam = CameraConfig(
                    id=r[0], name=r[1], rtsp_url=r[2],
                    analysis_fps=r[4] or 5, enabled=bool(r[5]),
                    onvif_user=r[6] or '', onvif_password=r[7] or '',
                    ai_confidence_threshold=r[8] or 0.85,
                )
                cam.use_onvif = (r[3] or 'rtsp') == 'onvif'
                db_cameras.append(cam)
    except Exception as e:
        logger.warning(f"No se pudieron cargar cámaras desde DB: {e}")

    all_cameras = db_cameras if db_cameras else config.cameras
    logger.info(f"Cámaras a conectar: {len(all_cameras)} (fuente: {'DB' if db_cameras else 'settings'})")

    # ── Construir adaptadores ────────────────────────────────────────
    adapters = []
    for cam in all_cameras:
        if not cam.enabled:
            continue
        if getattr(cam, 'use_onvif', False):
            adapter = ONVIFAdapter(cam)
            logger.info(f"[{cam.name}] Modo: ONVIF (edge analytics)")
        else:
            adapter = RTSPAdapter(cam, config.motion)
            logger.info(f"[{cam.name}] Modo: RTSP stream")
        adapter.register_callback(core.handle_event)
        adapters.append(adapter)

    if config.mqtt_enabled:
        mqtt = MQTTAdapter(config.mqtt_broker, config.mqtt_port)
        mqtt.register_callback(core.handle_event)
        adapters.append(mqtt)

    # ── Iniciar adaptadores ──────────────────────────────────────────
    started = 0
    for adapter in adapters:
        if await adapter.start():
            started += 1
            logger.info(f"✓ {adapter.adapter_name}")
        else:
            logger.error(f"✗ {adapter.adapter_name} — no se pudo iniciar")

    if started == 0:
        logger.warning("Ningún adaptador pudo iniciar — modo dashboard.")

    logger.info(f"Sistema activo — {started}/{len(adapters)} adaptadores online")
    logger.info("Pipeline corriendo. Ctrl+C para detener.")

    # ── Monitor de salud ─────────────────────────────────────────────
    health = HealthMonitor(notifier=notifier, db=core.db, check_interval=30)
    health.register_cameras(all_cameras)

    # Registrar dispositivos de infraestructura desde la DB
    from core.health_monitor import DeviceHealth
    try:
        infra_devices = core.db.get_infra_devices(enabled_only=True)
        for d in infra_devices:
            if d.get("monitor_health"):
                health.register_device(DeviceHealth(
                    device_id=d["id"],
                    device_name=d["name"],
                    device_type=d["device_type"],
                    host=d["host"],
                    port=int(d.get("port") or 80),
                ))
        logger.info(f"Infraestructura: {len(infra_devices)} dispositivo(s) cargados para monitoreo")
    except Exception as e:
        logger.warning(f"No se pudieron cargar infra_devices: {e}")

    await health.start()
    core.health_monitor = health
    logger.info(f"Monitor de salud activo — {len(health._devices)} dispositivo(s) total")

    # ── Dashboard web ────────────────────────────────────────────────
    import uvicorn
    from dashboard.api import add_admin_routes, add_scanner_routes, add_health_routes, add_infra_routes, add_audio_routes

    app = create_app(core.db, config.api_port)
    add_push_routes(app, notifier, vapid)
    add_admin_routes(app, core.db, core)
    add_scanner_routes(app, core.db)
    add_health_routes(app, core)
    add_infra_routes(app, core.db, core)
    add_audio_routes(app, core.db, core)

    dashboard_task = asyncio.create_task(
        uvicorn.Server(uvicorn.Config(
            app, host="0.0.0.0", port=config.api_port, log_level="warning",
        )).serve()
    )
    logger.info(f"Dashboard disponible en http://localhost:{config.api_port}")

    def shutdown(sig, frame):
        logger.info("\nDeteniendo sistema...")
        asyncio.create_task(_shutdown(adapters, core))

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        while True:
            await asyncio.sleep(60)
            stats = core.stats()
            logger.info(
                f"Stats — Eventos: {stats['events_received']} | "
                f"Analizados: {stats['events_analyzed']} | "
                f"Sin AI: {stats['events_skipped_ai']} | "
                f"Alertas: {stats['alerts_triggered']}"
            )
    except asyncio.CancelledError:
        pass


async def _shutdown(adapters, core):
    for adapter in adapters:
        await adapter.stop()
    stats = core.stats()
    logger.info(f"Final — {stats}")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
