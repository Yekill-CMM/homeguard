"""
learning/context_builder.py — Genera contexto enriquecido para Claude Vision
Combina score estadístico + historial de feedback del usuario.
Retorna un string listo para inyectar en el prompt.
"""
import logging
from datetime import datetime

logger = logging.getLogger("homeguard.learning")


class ContextBuilder:
    """
    Uso en engine.py:
        self._learning = ContextBuilder(storage_config.db_path)
        ctx, score = self._learning.build(event.camera_name, event.timestamp, event.event_type.value)
    """

    def __init__(self, db_path: str):
        from learning.baseline import AnomalyScorer
        from learning.feedback import FeedbackStore
        self.scorer   = AnomalyScorer(db_path)
        self.feedback = FeedbackStore(db_path)
        logger.info("ContextBuilder inicializado")

    def build(self, camera_name: str, timestamp: datetime, event_type: str) -> tuple:
        """
        Retorna (context_string, anomaly_score).
        context_string: listo para inyectar en el prompt de Claude.
        anomaly_score: 0.0-1.0 (para métricas / filtrado futuro).
        """
        try:
            baseline     = self.scorer.score(camera_name, timestamp, event_type)
            feedback_ctx = self.feedback.camera_context(camera_name)

            lines = [baseline["context"]]
            if feedback_ctx:
                lines.append(f"Aprendizaje previo del usuario: {feedback_ctx}")

            context = "\n".join(lines)
            return context, baseline["score"]

        except Exception as e:
            logger.warning(f"ContextBuilder.build error: {e}")
            return "", 0.5
