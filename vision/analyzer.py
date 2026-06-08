"""
HomeGuard AI — Análisis de escena con Claude Vision
Se invoca SOLO cuando el detector de movimiento local dispara un evento.
Esto minimiza el costo de API y la latencia.
"""

import cv2
import base64
import json
import logging
import asyncio
from dataclasses import dataclass
from typing import Optional
from datetime import datetime

import anthropic

from motion.detector import MotionEvent
from config.settings import ClaudeConfig

logger = logging.getLogger(__name__)


@dataclass
class SceneAnalysis:
    """Resultado del análisis de Claude Vision sobre un frame."""
    camera_id: str
    camera_name: str
    timestamp: datetime
    # Clasificación del evento
    event_type: str          # "person" | "vehicle" | "animal" | "no_threat" | "unclear"
    confidence: float        # 0.0 - 1.0
    description: str         # Descripción en lenguaje natural
    alert: bool              # True si requiere alerta inmediata
    alert_reason: Optional[str]
    # Metadata
    motion_area: int
    raw_response: str        # Respuesta completa de Claude (para debug)
    analysis_ms: int         # Tiempo de respuesta de la API


class VisionAnalyzer:
    """
    Envía frames a Claude Vision API para clasificación de eventos de seguridad.

    El flujo es:
      MotionEvent → encode frame → llamada API → SceneAnalysis

    Uso asíncrono para no bloquear el pipeline de captura.
    """

    def __init__(self, config: ClaudeConfig):
        self.config = config
        self.client = anthropic.Anthropic(api_key=config.api_key)
        self._call_count = 0
        self._alert_count = 0

    async def analyze(self, event: MotionEvent, frame_jpeg_quality: int = 85) -> Optional[SceneAnalysis]:
        """
        Analiza un frame con Claude Vision.

        Args:
            event: MotionEvent con el frame capturado
            frame_jpeg_quality: calidad JPEG para la imagen enviada (50-95)

        Returns:
            SceneAnalysis con la clasificación, o None si falla la llamada
        """
        start_ms = _now_ms()

        # 1. Codificar frame como JPEG → base64
        image_b64 = self._encode_frame(event.frame.image, frame_jpeg_quality)
        if image_b64 is None:
            return None

        # 2. Construir prompt contextual con información del evento
        user_prompt = (
            f"Cámara: {event.camera_name}\n"
            f"Hora: {event.timestamp.strftime('%H:%M:%S')}\n"
            f"Movimiento detectado en {event.contours_count} zona(s), "
            f"área total: {event.total_motion_area}px²\n\n"
            f"Analiza la imagen y clasifica el evento de seguridad."
        )

        # 3. Llamada a la API (en thread para no bloquear el event loop)
        try:
            raw_response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._call_api(image_b64, user_prompt)
            )
        except Exception as e:
            logger.error(f"[{event.camera_name}] Error en Claude API: {e}")
            return None

        elapsed_ms = _now_ms() - start_ms

        # 4. Parsear respuesta JSON
        analysis_data = self._parse_response(raw_response)
        if analysis_data is None:
            return None

        self._call_count += 1
        if analysis_data.get("alert"):
            self._alert_count += 1

        result = SceneAnalysis(
            camera_id=event.camera_id,
            camera_name=event.camera_name,
            timestamp=event.timestamp,
            event_type=analysis_data.get("event_type", "unclear"),
            confidence=float(analysis_data.get("confidence", 0.0)),
            description=analysis_data.get("description", "Sin descripción"),
            alert=bool(analysis_data.get("alert", False)),
            alert_reason=analysis_data.get("alert_reason"),
            motion_area=event.total_motion_area,
            raw_response=raw_response,
            analysis_ms=elapsed_ms,
        )

        log_level = logging.WARNING if result.alert else logging.INFO
        logger.log(
            log_level,
            f"[{event.camera_name}] {result.event_type.upper()} "
            f"({result.confidence:.0%}) — {result.description} "
            f"[{elapsed_ms}ms]"
        )

        if result.alert:
            logger.warning(
                f"[ALERTA] {event.camera_name}: {result.alert_reason}"
            )

        return result

    def stats(self) -> dict:
        """Estadísticas de uso de la API."""
        return {
            "total_calls": self._call_count,
            "total_alerts": self._alert_count,
            "alert_rate": (
                self._alert_count / self._call_count
                if self._call_count > 0 else 0.0
            ),
        }

    # ------------------------------------------------------------------
    # Métodos internos
    # ------------------------------------------------------------------

    def _encode_frame(self, image, quality: int) -> Optional[str]:
        """Convierte numpy array BGR → JPEG → base64 string."""
        try:
            encode_params = [cv2.IMWRITE_JPEG_QUALITY, quality]
            ret, buffer = cv2.imencode(".jpg", image, encode_params)
            if not ret:
                logger.error("No se pudo codificar el frame como JPEG")
                return None
            return base64.standard_b64encode(buffer.tobytes()).decode("utf-8")
        except Exception as e:
            logger.error(f"Error codificando frame: {e}")
            return None

    def _call_api(self, image_b64: str, user_prompt: str) -> str:
        """Llamada sincrónica a la API de Claude (se ejecuta en executor)."""
        message = self.client.messages.create(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            system=self.config.system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": image_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": user_prompt,
                        },
                    ],
                }
            ],
        )
        return message.content[0].text

    def _parse_response(self, raw: str) -> Optional[dict]:
        """Parsea la respuesta JSON de Claude."""
        try:
            # Limpiar posibles bloques de código markdown
            clean = raw.strip()
            if clean.startswith("```"):
                lines = clean.split("\n")
                clean = "\n".join(lines[1:-1])
            return json.loads(clean)
        except json.JSONDecodeError as e:
            logger.error(f"No se pudo parsear respuesta JSON de Claude: {e}\nRespuesta: {raw}")
            return None


def _now_ms() -> int:
    """Timestamp en milisegundos."""
    return int(datetime.now().timestamp() * 1000)
