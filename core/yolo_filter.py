"""
HomeGuard AI — Detector local YOLOv8 nano
Pre-filtra eventos ANTES de llamar a Claude Vision.
Corre 100% local en el N4505 — sin costo de API.

Lógica:
  - Movimiento detectado → YOLOv8 analiza el frame
  - Si detecta persona/vehículo con alta confianza → guarda evento, NO llama a Claude
  - Si detecta objeto con baja confianza → llama a Claude para confirmar
  - Si no detecta nada relevante → descarta (ahorra ~90% de llamadas a Claude)
"""

import logging
import time
import numpy as np
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)

# Clases COCO relevantes para seguridad residencial
SECURITY_CLASSES = {
    0:  "person",
    1:  "bicycle",
    2:  "car",
    3:  "motorcycle",
    5:  "bus",
    7:  "truck",
    14: "bird",
    15: "cat",
    16: "dog",
    17: "horse",
}

# Mapeo a EventType de HomeGuard
YOLO_TO_EVENT = {
    "person":     "person",
    "car":        "vehicle",
    "truck":      "vehicle",
    "bus":        "vehicle",
    "motorcycle": "vehicle",
    "bicycle":    "vehicle",
    "cat":        "animal",
    "dog":        "animal",
    "bird":       "animal",
    "horse":      "animal",
}

# Umbrales de confianza
CONFIDENCE_HIGH  = 0.75   # Alta confianza → no llama a Claude
CONFIDENCE_LOW   = 0.40   # Mínima para considerar detección
CONFIDENCE_CLAUDE = 0.65  # Por debajo → llama a Claude para confirmar


@dataclass
class YOLOResult:
    """Resultado del análisis YOLOv8."""
    detected: bool              # Hubo detección relevante
    event_type: str             # person | vehicle | animal | unknown
    confidence: float           # 0.0 - 1.0
    needs_claude: bool          # True = llamar a Claude para confirmar
    label: str                  # Etiqueta original de YOLO
    bbox: Optional[tuple]       # Bounding box (x1, y1, x2, y2)
    inference_ms: int           # Tiempo de inferencia


class YOLOFilter:
    """
    Filtro pre-Claude basado en YOLOv8 nano.
    Se inicializa una vez y reutiliza el modelo para todos los frames.
    """

    def __init__(self, model_path: str = "yolov8n.pt", device: str = "cpu"):
        self.model_path = model_path
        self.device = device
        self.model = None
        self._loaded = False
        self._inference_count = 0
        self._skip_count = 0

    def load(self) -> bool:
        """Carga el modelo YOLOv8. Descarga automática si no existe."""
        try:
            from ultralytics import YOLO
            logger.info(f"Cargando YOLOv8 nano ({self.model_path})...")
            start = time.monotonic()
            self.model = YOLO(self.model_path)
            self.model.to(self.device)
            elapsed = int((time.monotonic() - start) * 1000)
            self._loaded = True
            logger.info(f"YOLOv8 nano cargado en {elapsed}ms — dispositivo: {self.device}")
            return True
        except ImportError:
            logger.error("ultralytics no instalado — pip install ultralytics")
            return False
        except Exception as e:
            logger.error(f"Error cargando YOLOv8: {e}")
            return False

    def analyze(self, image: np.ndarray) -> YOLOResult:
        """
        Analiza un frame y retorna el resultado de detección.
        
        Args:
            image: numpy array BGR (formato OpenCV)
            
        Returns:
            YOLOResult con la detección más relevante encontrada
        """
        if not self._loaded or self.model is None:
            # Sin modelo — dejar pasar a Claude
            return YOLOResult(
                detected=False, event_type="unknown", confidence=0.0,
                needs_claude=True, label="no_model", bbox=None, inference_ms=0
            )

        start = time.monotonic()
        self._inference_count += 1

        try:
            # Inferencia — verbose=False para no llenar los logs
            results = self.model(
                image,
                conf=CONFIDENCE_LOW,
                device=self.device,
                verbose=False,
                imgsz=320,   # Resolución reducida para N4505 — más rápido
            )

            elapsed_ms = int((time.monotonic() - start) * 1000)

            # Extraer detecciones relevantes
            best = self._extract_best_detection(results)

            if best is None:
                # Nada relevante detectado → descartar evento
                self._skip_count += 1
                return YOLOResult(
                    detected=False, event_type="no_threat", confidence=0.0,
                    needs_claude=False, label="none", bbox=None,
                    inference_ms=elapsed_ms
                )

            label, confidence, bbox = best
            event_type = YOLO_TO_EVENT.get(label, "unknown")

            # Decidir si llamar a Claude
            needs_claude = confidence < CONFIDENCE_CLAUDE

            logger.debug(
                f"YOLO: {label} ({confidence:.0%}) → "
                f"{'→ Claude' if needs_claude else '→ directo'} [{elapsed_ms}ms]"
            )

            return YOLOResult(
                detected=True,
                event_type=event_type,
                confidence=confidence,
                needs_claude=needs_claude,
                label=label,
                bbox=bbox,
                inference_ms=elapsed_ms,
            )

        except Exception as e:
            logger.error(f"Error en inferencia YOLO: {e}")
            # En caso de error → dejar pasar a Claude
            return YOLOResult(
                detected=False, event_type="unknown", confidence=0.0,
                needs_claude=True, label="error", bbox=None,
                inference_ms=int((time.monotonic() - start) * 1000)
            )

    def _extract_best_detection(self, results) -> Optional[tuple]:
        """
        Extrae la detección más relevante y con mayor confianza.
        Prioriza personas > vehículos > animales.
        """
        PRIORITY = {"person": 3, "vehicle": 2, "animal": 1}
        best_label = None
        best_conf = 0.0
        best_bbox = None
        best_priority = 0

        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                cls_id = int(box.cls[0])
                if cls_id not in SECURITY_CLASSES:
                    continue
                label = SECURITY_CLASSES[cls_id]
                conf = float(box.conf[0])
                event = YOLO_TO_EVENT.get(label, "unknown")
                priority = PRIORITY.get(event, 0)

                if conf < CONFIDENCE_LOW:
                    continue

                if priority > best_priority or \
                   (priority == best_priority and conf > best_conf):
                    best_label = label
                    best_conf = conf
                    best_priority = priority
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    best_bbox = (int(x1), int(y1), int(x2), int(y2))

        if best_label is None:
            return None
        return best_label, best_conf, best_bbox

    def stats(self) -> dict:
        """Estadísticas de uso del filtro."""
        total = self._inference_count
        skipped = self._skip_count
        passed = total - skipped
        return {
            "total_inferences": total,
            "skipped":          skipped,
            "passed_to_claude": passed,
            "skip_rate":        f"{skipped/total:.0%}" if total > 0 else "0%",
        }

    @property
    def is_loaded(self) -> bool:
        return self._loaded
