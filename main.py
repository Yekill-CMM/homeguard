"""
HomeGuard AI — Punto de entrada principal v2
Arquitectura de adaptadores: cada fuente se conecta al Core via SecurityEvent.
"""

import asyncio
import logging
import signal
import sys
import os
from datetime import datetime

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config.settings import load_config
from core.engine import HomeGuardCore
from adapters.rtsp_adapter import RTSPAdapter
from adapters.onvif_adapter import ONVIFAdapter
from adapters.mqtt_adapter import MQTTAdapter
from dashboard.api import create_app, add_push_routes
from notifications.vapid import VAPIDManager
from notifications.push import PushNotifier
from core.health_monitor import HealthMonitor

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

    # Iniciar Core
    core = HomeGuardCore(config.claude, config.storage)

    # Iniciar sistema de notificaciones push
    vapid = VAPIDManager(core.db)
    notifier = PushNotifier(core.db, vapid)
    core.notifier = notifier
    logger.info(f"Push notifications listas — "
                f"{notifier.subscription_count()} dispositivo(s) registrado(s)")

    # Construir adaptadores según configuración
    adapters = []

    for cam in config.cameras:
        if not cam.enabled:
            continue

        if cam.use_onvif:
            # Cámara con edge analytics
            adapter = ONVIFAdapter(cam)
            logger.info(f"[{cam.name}] Modo: ONVIF (edge analytics)")
        else:
            # Cámara básica RTSP
            adapter = RTSPAdapter(cam, config.motion)
            logger.info(f"[{cam.name}] Modo: RTSP stream")

        adapter.register_callback(core.handle_event)
        adapters.append(adapter)

    # Adaptador MQTT (sensores IoT) — opcional
    if config.mqtt_enabled:
        mqtt = MQTTAdapter(config.mqtt_broker, config.mqtt_port)
        mqtt.register_callback(core.handle_event)
        adapters.append(mqtt)

    # Iniciar todos los adaptadores
    started = 0
    for adapter in adapters:
        if await adapter.start():
            started += 1
            logger.info(f"✓ {adapter.adapter_name}")
        else:
            logger.error(f"✗ {adapter.adapter_name} — no se pudo iniciar")

    if started == 0:
        logger.error("Ningún adaptador pudo iniciar.")
        sys.exit(1)

    logger.info(f"Sistema activo — {started}/{len(adapters)} adaptadores online")
    logger.info("Pipeline corriendo. Ctrl+C para detener.")

    # ── Monitor de salud ────────────────────────────────────────────
    health = HealthMonitor(
        notifier=notifier,
        db=core.db,
        check_interval=30,
    )
    health.register_cameras(config.cameras)
    await health.start()
    core.health_monitor = health
    logger.info("Monitor de salud activo")

    # Arrancar dashboard web en background
    import uvicorn
    app = create_app(core.db, config.api_port)
    add_push_routes(app, notifier, vapid)
    # Rutas de administración
    from dashboard.api import add_admin_routes
    add_admin_routes(app, core.db)
    from dashboard.api import add_scanner_routes
    add_scanner_routes(app, core.db)
    from dashboard.api import add_health_routes
    add_health_routes(app, core)
    dashboard_task = asyncio.create_task(
        uvicorn.Server(uvicorn.Config(
            app,
            host="0.0.0.0",
            port=config.api_port,
            log_level="warning",
        )).serve()
    )
    logger.info(f"Dashboard disponible en http://localhost:{config.api_port}")

    # Manejar Ctrl+C
    loop = asyncio.get_event_loop()

    def shutdown(sig, frame):
        logger.info("\nDeteniendo sistema...")
        asyncio.create_task(_shutdown(adapters, core))

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Mantener el loop vivo
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
