"""
HomeGuard AI — Hub de streaming para el videowall.

Antes, cada visor del videowall disparaba un proceso ffmpeg nuevo que
volvía a conectarse por RTSP a la cámara — además de la conexión que ya
mantenía el pipeline principal (adapters/rtsp_adapter.py) para detección
de movimiento. La mayoría de cámaras IP domésticas solo aceptan 1-2
sesiones RTSP simultáneas, así que esas conexiones duplicadas hacían que
las cámaras cortaran sesiones y el HealthMonitor las reportara caídas o
con latencia alta.

Este hub evita eso de dos formas:
  1. Si la cámara tiene un RTSPAdapter corriendo, reutiliza el último
     frame que ese adaptador ya captura — cero conexiones RTSP nuevas.
  2. Si no hay adaptador con frames disponibles (p. ej. cámaras ONVIF,
     que no mantienen un stream RTSP propio), comparte un único proceso
     ffmpeg entre todos los visores de esa cámara en vez de abrir uno
     por visor.
"""

import asyncio
import logging
import shutil
from typing import Optional

logger = logging.getLogger("homeguard.videowall")

ADAPTER_POLL_INTERVAL = 0.5       # ~2 fps hacia el navegador
BROADCAST_IDLE_TIMEOUT = 10.0     # segundos sin visores antes de matar el ffmpeg compartido

_FRAME_HEADER = b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "


def _multipart_chunk(frame: bytes) -> bytes:
    return _FRAME_HEADER + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n"


class _Broadcaster:
    """Un único proceso ffmpeg por cámara, compartido entre N visores."""

    def __init__(self, rtsp_url: str, fps: int = 2, width: int = 640):
        self.rtsp_url = rtsp_url
        self.fps = fps
        self.width = width
        self._subscribers: set[asyncio.Queue] = set()
        self._task: Optional[asyncio.Task] = None
        self._proc = None
        self._idle_handle: Optional[asyncio.TimerHandle] = None

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2)
        self._subscribers.add(q)
        if self._idle_handle:
            self._idle_handle.cancel()
            self._idle_handle = None
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self._subscribers.discard(q)
        if not self._subscribers and self._idle_handle is None:
            loop = asyncio.get_running_loop()
            self._idle_handle = loop.call_later(BROADCAST_IDLE_TIMEOUT, self._stop)

    def _stop(self):
        self._idle_handle = None
        if self._task and not self._task.done():
            self._task.cancel()

    async def _run(self):
        ffmpeg_path = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
        args = [
            ffmpeg_path, "-rtsp_transport", "tcp", "-i", self.rtsp_url,
            "-vf", f"fps={self.fps},scale={self.width}:-1",
            "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "6",
            "-loglevel", "error", "pipe:1",
        ]
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            logger.info(f"Broadcast ffmpeg iniciado para {self.rtsp_url} (PID {self._proc.pid})")
            buf = b""
            while True:
                chunk = await self._proc.stdout.read(4096)
                if not chunk:
                    break
                buf += chunk
                while True:
                    start = buf.find(b"\xff\xd8")
                    end = buf.find(b"\xff\xd9")
                    if start == -1 or end == -1 or end <= start:
                        break
                    frame = buf[start:end + 2]
                    buf = buf[end + 2:]
                    for q in list(self._subscribers):
                        if q.full():
                            try:
                                q.get_nowait()
                            except asyncio.QueueEmpty:
                                pass
                        try:
                            q.put_nowait(frame)
                        except asyncio.QueueFull:
                            pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"Error en broadcast ffmpeg ({self.rtsp_url}): {e}")
        finally:
            if self._proc and self._proc.returncode is None:
                self._proc.kill()
                await self._proc.wait()
            logger.info(f"Broadcast ffmpeg detenido para {self.rtsp_url}")


class StreamHub:
    """Punto único de acceso a frames de cámara para el videowall."""

    def __init__(self):
        self._adapters: dict[str, object] = {}   # cam_id -> adaptador con get_latest_jpeg()
        self._broadcasters: dict[str, _Broadcaster] = {}

    def register_adapters(self, adapters_by_cam_id: dict) -> None:
        self._adapters.update(adapters_by_cam_id)
        logger.info(f"StreamHub: {len(adapters_by_cam_id)} adaptador(es) registrados para reuso de frames")

    async def stream(self, cam_id: str, rtsp_url: str):
        """Generador async de frames MJPEG (multipart) para un cam_id."""
        adapter = self._adapters.get(cam_id)
        if adapter is not None and hasattr(adapter, "get_latest_jpeg"):
            async for chunk in self._stream_from_adapter(adapter):
                yield chunk
            return

        async for chunk in self._stream_from_broadcast(cam_id, rtsp_url):
            yield chunk

    async def _stream_from_adapter(self, adapter):
        last_frame = None
        while True:
            frame = adapter.get_latest_jpeg()
            if frame and frame is not last_frame:
                last_frame = frame
                yield _multipart_chunk(frame)
            await asyncio.sleep(ADAPTER_POLL_INTERVAL)

    async def _stream_from_broadcast(self, cam_id: str, rtsp_url: str):
        b = self._broadcasters.get(cam_id)
        if b is None:
            b = _Broadcaster(rtsp_url)
            self._broadcasters[cam_id] = b
        q = b.subscribe()
        try:
            while True:
                frame = await q.get()
                yield _multipart_chunk(frame)
        finally:
            b.unsubscribe(q)


# Instancia única compartida entre main.py (registra adaptadores) y
# dashboard/api.py (sirve el stream).
hub = StreamHub()
