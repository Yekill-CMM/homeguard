"""
HomeGuard AI — Detector de movimiento local
Usa diferencia de frames (frame differencing) para detectar movimiento.
No requiere IA ni API externa — corre 100% en tu Mac.
"""

import cv2
import numpy as np
import logging
import time
from dataclasses import dataclass
from typing import Optional
from datetime import datetime

from cameras.rtsp_camera import Frame
from config.settings import MotionConfig

logger = logging.getLogger(__name__)


@dataclass
class MotionEvent:
    """Evento de movimiento detectado por el sistema local."""
    camera_id: str
    camera_name: str
    frame: Frame                # Frame en el momento del evento
    timestamp: datetime
    contours_count: int         # Número de regiones con movimiento
    total_motion_area: int      # Área total de movimiento (px²)
    motion_mask: any            # Máscara binaria para debug (numpy array)
    bounding_boxes: list[tuple] # Rectángulos de las zonas con movimiento


class MotionDetector:
    """
    Detector de movimiento por diferencia de frames adaptativa.

    Algoritmo:
    1. Convierte frame a escala de grises
    2. Aplica blur gaussiano para reducir ruido
    3. Calcula diferencia absoluta con el frame anterior
    4. Aplica umbral para obtener máscara binaria
    5. Dilata la máscara para conectar regiones cercanas
    6. Encuentra contornos y filtra por área mínima

    La detección es local y no consume API — solo se llama a Claude
    cuando esta función retorna un MotionEvent.
    """

    def __init__(self, camera_id: str, config: MotionConfig):
        self.camera_id = camera_id
        self.config = config
        self._prev_gray: Optional[np.ndarray] = None
        self._cooldown_remaining = 0
        self._last_analysis_time = 0.0
        self._frame_count = 0

    def process(self, frame: Frame) -> Optional[MotionEvent]:
        """
        Procesa un frame y retorna un MotionEvent si detecta movimiento.
        Retorna None si no hay movimiento o estamos en cooldown.
        """
        self._frame_count += 1

        # 1. Convertir a escala de grises
        gray = cv2.cvtColor(frame.image, cv2.COLOR_BGR2GRAY)

        # 2. Blur gaussiano para reducir ruido de sensor
        gray = cv2.GaussianBlur(
            gray,
            (self.config.blur_kernel, self.config.blur_kernel),
            0
        )

        # Inicializar con el primer frame
        if self._prev_gray is None:
            self._prev_gray = gray
            return None

        # 3. Diferencia absoluta entre frame actual y anterior
        diff = cv2.absdiff(self._prev_gray, gray)

        # 4. Umbral binario: solo píxeles con diferencia mayor al threshold
        _, thresh = cv2.threshold(diff, self.config.threshold, 255, cv2.THRESH_BINARY)

        # 5. Dilatar para conectar regiones fragmentadas (ruido de compresión RTSP)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        dilated = cv2.dilate(thresh, kernel, iterations=2)

        # 6. Encontrar contornos (regiones de movimiento)
        contours, _ = cv2.findContours(
            dilated,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE
        )

        # Actualizar frame anterior ANTES de retornar
        self._prev_gray = gray

        # Gestionar cooldown
        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1
            return None

        # Filtrar contornos por área mínima
        significant = [
            c for c in contours
            if cv2.contourArea(c) >= self.config.min_contour_area
        ]

        if not significant:
            return None

        # Verificar tiempo mínimo entre análisis (evitar spam a Claude)
        now = time.monotonic()
        elapsed = now - self._last_analysis_time
        if elapsed < self.config.min_seconds_between_analysis:
            return None

        # ¡Movimiento detectado! Calcular métricas
        total_area = sum(cv2.contourArea(c) for c in significant)
        bounding_boxes = [
            tuple(cv2.boundingRect(c))  # (x, y, w, h)
            for c in significant
        ]

        self._cooldown_remaining = self.config.cooldown_frames
        self._last_analysis_time = now

        event = MotionEvent(
            camera_id=frame.camera_id,
            camera_name=frame.camera_name,
            frame=frame,
            timestamp=frame.timestamp,
            contours_count=len(significant),
            total_motion_area=int(total_area),
            motion_mask=dilated,
            bounding_boxes=bounding_boxes,
        )

        logger.info(
            f"[{frame.camera_name}] Movimiento detectado — "
            f"{len(significant)} región(es), área total: {int(total_area)}px²"
        )
        return event

    def draw_motion_overlay(self, frame: Frame, event: MotionEvent) -> np.ndarray:
        """
        Dibuja rectángulos de movimiento sobre el frame (útil para debug/dashboard).
        Retorna una copia del frame con las anotaciones.
        """
        annotated = frame.image.copy()

        for (x, y, w, h) in event.bounding_boxes:
            cv2.rectangle(annotated, (x, y), (x + w, y + h), (0, 255, 100), 2)

        # Timestamp en la esquina superior izquierda
        ts = event.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        cv2.putText(
            annotated, ts,
            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2
        )
        cv2.putText(
            annotated, f"MOVIMIENTO DETECTADO",
            (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 100), 2
        )

        return annotated

    def reset(self):
        """Reinicia el detector (útil tras reconexión de cámara)."""
        self._prev_gray = None
        self._cooldown_remaining = 0
        self._last_analysis_time = 0.0


class MotionDetectorPool:
    """Gestiona un detector de movimiento por cada cámara."""

    def __init__(self, camera_ids: list[str], config: MotionConfig):
        self.detectors: dict[str, MotionDetector] = {
            cam_id: MotionDetector(cam_id, config)
            for cam_id in camera_ids
        }

    def process(self, camera_id: str, frame: Frame) -> Optional[MotionEvent]:
        """Procesa un frame en el detector correspondiente a esa cámara."""
        detector = self.detectors.get(camera_id)
        if not detector:
            return None
        return detector.process(frame)

    def draw_overlay(self, camera_id: str, frame: Frame, event: MotionEvent) -> np.ndarray:
        detector = self.detectors.get(camera_id)
        if not detector:
            return frame.image
        return detector.draw_motion_overlay(frame, event)
