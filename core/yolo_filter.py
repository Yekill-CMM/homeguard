"""
HomeGuard AI — Detector local YOLOv8 nano
Pre-filtra eventos ANTES de llamar a Claude Vision.
Corre 100% local en el N4505 — sin costo de API.

Backend auto-detectado en orden de preferencia:
  1. OpenVINO  → yolov8n_openvino_model/  (2-4x más rápido en Intel)
  2. PyTorch   → yolov8n.pt               (fallback siempre disponible)

Para generar el modelo OpenVINO:
    python3 scripts/export_openvino.py

Lógica de filtrado:
  - Alta confianza (>=0.75) -> clasifica directo, sin Claude
  - Media confianza (>=0.65) -> pasa a Claude para confirmar
  - Baja confianza  (<0.40) -> descarta evento, sin guardar
"""

import logging
import time
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Clases COCO relevantes para seguridad residencial ────────────────────────
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

# ── Umbrales de confianza ────────────────────────────────────────────────────
CONFIDENCE_HIGH   = 0.75   # Alta confianza → clasifica sin Claude
CONFIDENCE_CLAUDE = 0.65   # Por debajo → pasa a Claude
CONFIDENCE_LOW    = 0.40   # Mínima para considerar la detección


@dataclass
class YOLOResult:
    """Resultado del análisis YOLOv8."""
    detected:     bool
    event_type:   str
    confidence:   float
    needs_claude: bool
    label:        str
    bbox:         Optional[tuple]
    inference_ms: int


class YOLOFilter:
    """
    Filtro pre-Claude basado en YOLOv8 nano.
    Auto-detecta backend OpenVINO o PyTorch según disponibilidad.
    Interfaz idéntica en ambos casos — engine.py no cambia.
    """

    # Rutas candidatas para el modelo OpenVINO (en orden de preferencia)
    _OV_CANDIDATES = [
        Path.home() / "homeguard" / "yolov8n_openvino_model",
        Path("yolov8n_openvino_model"),
    ]

    def __init__(self, model_path: str = "yolov8n.pt", device: str = "cpu"):
        self.model_path = model_path
        self.device     = device
        self.model      = None
        self._loaded    = False
        self._backend   = "none"      # "openvino" | "pytorch" | "none"
        self._inference_count = 0
        self._skip_count      = 0
        self._total_ms        = 0

    # ── Carga del modelo ─────────────────────────────────────────────────────

    def load(self) -> bool:
        """
        Carga el modelo YOLOv8.
        Intenta OpenVINO primero, cae a PyTorch si no está disponible.
        """
        try:
            from ultralytics import YOLO
        except ImportError:
            logger.error("ultralytics no instalado — pip install ultralytics")
            return False

        # 1. Intentar OpenVINO
        ov_path = self._find_openvino_model()
        if ov_path:
            try:
                logger.info(f"Cargando modelo OpenVINO: {ov_path}")
                start = time.monotonic()
                self.model    = YOLO(str(ov_path))
                elapsed       = int((time.monotonic() - start) * 1000)
                self._backend = "openvino"
                self._loaded  = True
                logger.info(
                    f"🚀 YOLOv8 nano — backend: OPENVINO — cargado en {elapsed}ms "
                    f"(Intel N4505 optimizado)"
                )
                return True
            except Exception as e:
                logger.warning(f"OpenVINO falló ({e}) — intentando PyTorch...")

        # 2. Fallback PyTorch
        try:
            logger.info(f"Cargando YOLOv8 nano PyTorch ({self.model_path})...")
            start = time.monotonic()
            self.model = YOLO(self.model_path)
            self.model.to(self.device)
            elapsed       = int((time.monotonic() - start) * 1000)
            self._backend = "pytorch"
            self._loaded  = True
            logger.info(
                f"YOLOv8 nano — backend: PYTORCH — cargado en {elapsed}ms "
                f"(para activar OpenVINO: python3 scripts/export_openvino.py)"
            )
            return True
        except Exception as e:
            logger.error(f"Error cargando YOLOv8 PyTorch: {e}")
            return False

    def _find_openvino_model(self) -> Optional[Path]:
        """Busca el directorio del modelo OpenVINO en las rutas candidatas."""
        for candidate in self._OV_CANDIDATES:
            if candidate.exists() and candidate.is_dir():
                xml_files = list(candidate.glob("*.xml"))
                if xml_files:
                    return candidate
        return None

    # ── Inferencia ───────────────────────────────────────────────────────────

    def analyze(self, image: np.ndarray) -> YOLOResult:
        """
        Analiza un frame y retorna el resultado de detección.

        Args:
            image: numpy array BGR (formato OpenCV)

        Returns:
            YOLOResult con la detección más relevante encontrada
        """
        if not self._loaded or self.model is None:
            return YOLOResult(
                detected=False, event_type="unknown", confidence=0.0,
                needs_claude=True, label="no_model", bbox=None, inference_ms=0,
            )

        start = time.monotonic()
        self._inference_count += 1

        try:
            # OpenVINO no requiere device= (lo gestiona internamente)
            kwargs = dict(conf=CONFIDENCE_LOW, verbose=False, imgsz=320)
            if self._backend == "pytorch":
                kwargs["device"] = self.device

            results    = self.model(image, **kwargs)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            self._total_ms += elapsed_ms

            best = self._extract_best_detection(results)

            if best is None:
                self._skip_count += 1
                return YOLOResult(
                    detected=False, event_type="no_threat", confidence=0.0,
                    needs_claude=False, label="none", bbox=None,
                    inference_ms=elapsed_ms,
                )

            label, confidence, bbox = best
            event_type   = YOLO_TO_EVENT.get(label, "unknown")
            needs_claude = confidence < CONFIDENCE_CLAUDE

            logger.debug(
                f"[{self._backend.upper()}] {label} ({confidence:.0%}) "
                f"→ {'Claude' if needs_claude else 'directo'} [{elapsed_ms}ms]"
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
            logger.error(f"Error en inferencia YOLO ({self._backend}): {e}")
            return YOLOResult(
                detected=False, event_type="unknown", confidence=0.0,
                needs_claude=True, label="error", bbox=None,
                inference_ms=int((time.monotonic() - start) * 1000),
            )

    # ── Extracción de la mejor detección ─────────────────────────────────────

    def _extract_best_detection(self, results) -> Optional[tuple]:
        """
        Extrae la detección más relevante con mayor confianza.
        Prioridad: persona > vehículo > animal.
        """
        PRIORITY = {"person": 3, "vehicle": 2, "animal": 1}
        best_label    = None
        best_conf     = 0.0
        best_bbox     = None
        best_priority = 0

        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                cls_id = int(box.cls[0])
                if cls_id not in SECURITY_CLASSES:
                    continue
                label    = SECURITY_CLASSES[cls_id]
                conf     = float(box.conf[0])
                event    = YOLO_TO_EVENT.get(label, "unknown")
                priority = PRIORITY.get(event, 0)

                if conf < CONFIDENCE_LOW:
                    continue

                if priority > best_priority or \
                   (priority == best_priority and conf > best_conf):
                    best_label    = label
                    best_conf     = conf
                    best_priority = priority
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    best_bbox = (int(x1), int(y1), int(x2), int(y2))

        return (best_label, best_conf, best_bbox) if best_label else None

    # ── Estadísticas ─────────────────────────────────────────────────────────

    def stats(self) -> dict:
        total   = self._inference_count
        skipped = self._skip_count
        passed  = total - skipped
        avg_ms  = int(self._total_ms / total) if total > 0 else 0
        return {
            "backend":          self._backend,
            "total_inferences": total,
            "skipped":          skipped,
            "passed_to_claude": passed,
            "skip_rate":        f"{skipped/total:.0%}" if total > 0 else "0%",
            "avg_inference_ms": avg_ms,
        }

    @property
    def is_loaded(self) -> bool:
        return self._loaded
