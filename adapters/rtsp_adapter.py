"""
HomeGuard AI — Adaptador RTSP
Para cámaras IP básicas sin edge analytics.
Captura el stream, detecta movimiento localmente y emite SecurityEvents.
"""

import cv2
import numpy as np
import subprocess
import threading
import asyncio
import logging
import time
import shutil
from datetime import datetime
from typing import Optional

from adapters.base import BaseAdapter
from core.event import SecurityEvent, SourceType, EventType, Severity
from config.settings import CameraConfig, MotionConfig

logger = logging.getLogger(__name__)


class RTSPAdapter(BaseAdapter):
    """
    Adaptador para cámaras IP con stream RTSP.
    Detecta movimiento por diferencia de frames (100% local).
    Emite un SecurityEvent cuando detecta movimiento significativo.
    """

    def __init__(self, camera_config: CameraConfig, motion_config: MotionConfig):
        super().__init__(
            adapter_id=f"rtsp_{camera_config.id}",
            adapter_name=camera_config.name,
        )
        self.camera_config = camera_config
        self.motion_config = motion_config

        self._width = camera_config.width or 1280
        self._height = camera_config.height or 720
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._prev_gray: Optional[np.ndarray] = None
        self._cooldown_remaining = 0
        self._last_event_time = 0.0
        self._ffmpeg_path = shutil.which("ffmpeg") or "/usr/local/bin/ffmpeg"

    async def start(self) -> bool:
        if self._running:
            return True

        self.logger.info(f"Conectando a {self.camera_config.rtsp_url}")

        if not shutil.which("ffmpeg"):
            self.logger.error("ffmpeg no encontrado. Instala con: brew install ffmpeg")
            return False

        if not await self._test_connection():
            return False

        self._running = True
        self._loop = asyncio.get_event_loop()
        self._thread = threading.Thread(
            target=self._capture_loop,
            name=f"rtsp_{self.camera_config.id}",
            daemon=True,
        )
        self._thread.start()
        self.logger.info(f"Stream iniciado ({self._width}x{self._height})")
        return True

    async def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        self.logger.info("Adaptador RTSP detenido")

    def is_healthy(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------

    async def _test_connection(self) -> bool:
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-rtsp_transport", "tcp",
                    "-show_entries", "stream=width,height",
                    "-of", "csv=p=0",
                    self.camera_config.rtsp_url,
                ],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                parts = result.stdout.strip().split("\n")[0].split(",")
                if len(parts) >= 2:
                    try:
                        self._width = int(parts[0]) or self._width
                        self._height = int(parts[1]) or self._height
                    except ValueError:
                        pass
                self.logger.info(f"Stream verificado: {self._width}x{self._height}")
                return True
            self.logger.error(f"No se pudo verificar el stream: {result.stderr[:200]}")
            return False
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            self.logger.error(f"Error verificando stream: {e}")
            return False

    def _capture_loop(self):
        """Loop de captura FFmpeg + detección de movimiento."""
        frame_size = self._width * self._height * 3

        while self._running:
            process = None
            try:
                cmd = [
                    self._ffmpeg_path,
                    "-rtsp_transport", "tcp",
                    "-i", self.camera_config.rtsp_url,
                    "-vf", f"fps={self.camera_config.analysis_fps},scale={self._width}:{self._height}",
                    "-f", "rawvideo",
                    "-pix_fmt", "bgr24",
                    "-an", "-sn",
                    "pipe:1",
                ]
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=frame_size * 2,
                )

                self.logger.info(f"FFmpeg iniciado (PID {process.pid})")

                while self._running:
                    raw = process.stdout.read(frame_size)
                    if len(raw) != frame_size:
                        self.logger.warning("Frame incompleto — reconectando...")
                        break

                    image = np.frombuffer(raw, dtype=np.uint8).reshape(
                        (self._height, self._width, 3)
                    )

                    event = self._detect_motion(image)
                    if event:
                        # Emitir evento desde el thread hacia el event loop
                        if self._loop and self._loop.is_running():
                            asyncio.run_coroutine_threadsafe(
                                self.emit(event), self._loop
                            )

            except Exception as e:
                self.logger.error(f"Error en captura: {e}")
            finally:
                if process:
                    process.kill()
                    process.wait()

            if self._running:
                self.logger.info("Reconectando en 5s...")
                time.sleep(5)

    def _detect_motion(self, image: np.ndarray) -> Optional[SecurityEvent]:
        """Detección de movimiento local por diferencia de frames."""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (self.motion_config.blur_kernel, self.motion_config.blur_kernel), 0)

        if self._prev_gray is None:
            self._prev_gray = gray
            return None

        diff = cv2.absdiff(self._prev_gray, gray)
        _, thresh = cv2.threshold(diff, self.motion_config.threshold, 255, cv2.THRESH_BINARY)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        dilated = cv2.dilate(thresh, kernel, iterations=2)
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        self._prev_gray = gray

        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1
            return None

        significant = [c for c in contours if cv2.contourArea(c) >= self.motion_config.min_contour_area]
        if not significant:
            return None

        now = time.monotonic()
        if (now - self._last_event_time) < self.motion_config.min_seconds_between_analysis:
            return None

        self._cooldown_remaining = self.motion_config.cooldown_frames
        self._last_event_time = now

        total_area = sum(cv2.contourArea(c) for c in significant)

        # Encode snapshot
        _, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
        snapshot = jpeg.tobytes()

        self.logger.info(
            f"Movimiento detectado — {len(significant)} zona(s), área: {int(total_area)}px²"
        )

        return SecurityEvent(
            camera_id=self.camera_config.id,
            camera_name=self.camera_config.name,
            timestamp=datetime.now(),
            source_type=SourceType.STREAM,
            event_type=EventType.MOTION,     # Claude lo clasificará después
            severity=Severity.LOW,           # Claude lo ajustará después
            confidence=0.0,                  # Sin clasificar aún
            snapshot=snapshot,
            needs_ai_analysis=True,          # Siempre necesita Claude
            raw_metadata={
                "contours": len(significant),
                "motion_area_px": int(total_area),
                "resolution": f"{self._width}x{self._height}",
            },
        )
