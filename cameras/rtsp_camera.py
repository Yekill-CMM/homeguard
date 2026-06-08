"""
HomeGuard AI — Módulo de captura RTSP
Usa FFmpeg directamente via subprocess para máxima compatibilidad en macOS.
OpenCV se usa solo para procesar los frames, no para capturarlos.
"""

import cv2
import numpy as np
import subprocess
import threading
import logging
import time
import shutil
from dataclasses import dataclass
from typing import Optional
from datetime import datetime

from config.settings import CameraConfig

logger = logging.getLogger(__name__)


@dataclass
class Frame:
    """Frame capturado de una cámara."""
    camera_id: str
    camera_name: str
    image: any          # numpy array BGR
    timestamp: datetime
    frame_number: int


class RTSPCamera:
    """
    Captura frames de una cámara IP vía RTSP usando FFmpeg como backend.
    Más compatible que OpenCV puro en macOS.
    """

    def __init__(self, config: CameraConfig):
        self.config = config
        self.latest_frame: Optional[Frame] = None
        self._frame_number = 0
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._width = config.width or 1280
        self._height = config.height or 720
        self._ffmpeg_path = shutil.which("ffmpeg") or "/usr/local/bin/ffmpeg"

    def start(self) -> bool:
        if self._running:
            return True

        logger.info(f"[{self.config.name}] Conectando a {self.config.rtsp_url}")

        # Verificar que ffmpeg está disponible
        if not shutil.which("ffmpeg"):
            logger.error("ffmpeg no encontrado. Instala con: brew install ffmpeg")
            return False

        # Test rápido de conexión con ffprobe
        if not self._test_connection():
            return False

        self._running = True
        self._thread = threading.Thread(
            target=self._capture_loop,
            name=f"camera_{self.config.id}",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"[{self.config.name}] Captura iniciada ({self._width}x{self._height})")
        return True

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info(f"[{self.config.name}] Captura detenida")

    def get_frame(self) -> Optional[Frame]:
        with self._lock:
            return self.latest_frame

    def is_connected(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------

    def _test_connection(self) -> bool:
        """Prueba la conexión con ffprobe antes de iniciar la captura."""
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-rtsp_transport", "tcp",
                    "-show_entries", "stream=width,height",
                    "-of", "csv=p=0",
                    self.config.rtsp_url,
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
                logger.info(f"[{self.config.name}] Stream verificado: {self._width}x{self._height}")
                return True
            else:
                logger.error(f"[{self.config.name}] No se pudo verificar el stream RTSP")
                logger.error(f"  ffprobe stderr: {result.stderr[:200]}")
                return False
        except subprocess.TimeoutExpired:
            logger.error(f"[{self.config.name}] Timeout conectando a la cámara")
            return False
        except FileNotFoundError:
            logger.error("ffprobe no encontrado. Instala con: brew install ffmpeg")
            return False

    def _capture_loop(self):
        """Lee frames crudos de FFmpeg y los convierte a numpy arrays."""
        frame_size = self._width * self._height * 3  # BGR = 3 bytes por pixel
        frame_interval = 1.0 / self.config.analysis_fps

        while self._running:
            process = None
            try:
                cmd = [
                    self._ffmpeg_path,
                    "-rtsp_transport", "tcp",
                    "-i", self.config.rtsp_url,
                    "-vf", f"fps={self.config.analysis_fps},scale={self._width}:{self._height}",
                    "-f", "rawvideo",
                    "-pix_fmt", "bgr24",
                    "-an",          # sin audio
                    "-sn",          # sin subtítulos
                    "pipe:1",       # output a stdout
                ]

                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=frame_size * 2,
                )

                logger.info(f"[{self.config.name}] FFmpeg iniciado (PID {process.pid})")

                while self._running:
                    raw = process.stdout.read(frame_size)
                    if len(raw) != frame_size:
                        logger.warning(f"[{self.config.name}] Frame incompleto, reconectando...")
                        break

                    image = np.frombuffer(raw, dtype=np.uint8).reshape(
                        (self._height, self._width, 3)
                    )
                    self._frame_number += 1

                    frame = Frame(
                        camera_id=self.config.id,
                        camera_name=self.config.name,
                        image=image.copy(),
                        timestamp=datetime.now(),
                        frame_number=self._frame_number,
                    )

                    with self._lock:
                        self.latest_frame = frame

            except Exception as e:
                logger.error(f"[{self.config.name}] Error en captura: {e}")
            finally:
                if process:
                    process.kill()
                    process.wait()

            if self._running:
                logger.info(f"[{self.config.name}] Reconectando en 5s...")
                time.sleep(5)


class CameraManager:
    """Gestiona múltiples cámaras RTSP simultáneamente."""

    def __init__(self, configs: list[CameraConfig]):
        self.cameras: dict[str, RTSPCamera] = {
            cfg.id: RTSPCamera(cfg)
            for cfg in configs
            if cfg.enabled
        }

    def start_all(self):
        connected = 0
        for cam in self.cameras.values():
            if cam.start():
                connected += 1
        logger.info(f"Cámaras conectadas: {connected}/{len(self.cameras)}")
        return connected

    def stop_all(self):
        for cam in self.cameras.values():
            cam.stop()

    def get_frames(self) -> dict[str, Optional[Frame]]:
        return {
            cam_id: cam.get_frame()
            for cam_id, cam in self.cameras.items()
        }

    def get_camera(self, camera_id: str) -> Optional[RTSPCamera]:
        return self.cameras.get(camera_id)

    def status(self) -> dict:
        return {
            cam_id: {
                "name": cam.config.name,
                "connected": cam.is_connected(),
                "url": cam.config.rtsp_url,
            }
            for cam_id, cam in self.cameras.items()
        }
