"""
HomeGuard AI — Modelo de datos central
SecurityEvent es el objeto unificado que fluye por todo el sistema.
No importa si viene de edge analytics o de stream local — siempre es este objeto.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
import uuid


class SourceType(str, Enum):
    """Origen del evento — cómo llegó al sistema."""
    EDGE      = "edge"    # La cámara lo detectó y clasificó sola
    STREAM    = "stream"  # HomeGuard lo detectó procesando el stream RTSP
    SENSOR    = "sensor"  # Sensor IoT (PIR, humo, gas) vía MQTT
    WEBHOOK   = "webhook" # Cámara envió HTTP event (Reolink, Tapo)
    MANUAL    = "manual"  # Generado manualmente (tests, dashboard)
    AUDIO     = "audio"   # Evento de audio (Home Assistant / micrófono)


class EventType(str, Enum):
    """Clasificación del evento detectado."""
    MOTION       = "motion"      # Movimiento genérico (sin clasificar)
    PERSON       = "person"      # Persona detectada
    VEHICLE      = "vehicle"     # Vehículo detectado
    ANIMAL       = "animal"      # Animal detectado
    INTRUSION    = "intrusion"   # Intrusión perimetral confirmada
    FIRE         = "fire"        # Fuego / humo detectado
    GAS          = "gas"         # Gas / CO2 detectado
    ACCESS       = "access"      # Evento de control de accesos
    TAMPER       = "tamper"      # Manipulación de cámara
    NO_THREAT    = "no_threat"   # Analizado — sin amenaza
    UNKNOWN      = "unknown"     # No clasificado aún
    # ── Audio ──────────────────────────────────────────────
    AUDIO_SCREAM     = "audio_scream"     # Grito / voz de alarma
    AUDIO_GLASS      = "audio_glass"      # Cristal roto
    AUDIO_BARK       = "audio_bark"       # Ladrido de perro
    AUDIO_ALARM      = "audio_alarm"      # Sirena / alarma
    AUDIO_CRY        = "audio_cry"        # Llanto
    AUDIO_VOICE      = "audio_voice"      # Voz / conversación detectada
    AUDIO_NOISE      = "audio_noise"      # Ruido anormal (nivel alto)


class Severity(str, Enum):
    """Nivel de severidad del evento."""
    LOW      = "low"      # Informativo — registrar pero no alertar
    MEDIUM   = "medium"   # Atención — notificar al usuario
    HIGH     = "high"     # Urgente — alerta inmediata
    CRITICAL = "critical" # Crítico — sirena + llamada + todos los canales


@dataclass
class SecurityEvent:
    """
    Objeto unificado que representa cualquier evento de seguridad en el sistema.

    El Event Adapter de cada fuente (edge, stream, sensor, webhook) es
    responsable de construir este objeto. A partir de aquí, el Core
    solo trabaja con SecurityEvents — sin saber de dónde vienen.
    """

    # Identidad
    id: str                     = field(default_factory=lambda: str(uuid.uuid4()))
    camera_id: str              = ""
    camera_name: str            = ""
    timestamp: datetime         = field(default_factory=datetime.now)

    # Origen
    source_type: SourceType     = SourceType.STREAM

    # Clasificación
    event_type: EventType       = EventType.UNKNOWN
    severity: Severity          = Severity.LOW
    confidence: float           = 0.0   # 0.0–1.0

    # Imagen del evento
    snapshot: Optional[bytes]   = None  # JPEG raw
    snapshot_path: Optional[str]= None  # Ruta en disco si ya se guardó

    # Decisión de análisis IA
    needs_ai_analysis: bool     = True
    # False cuando:
    #   - Edge analytics ya clasificó con confianza >= umbral
    #   - Es un sensor físico (humo, gas) que no necesita visión
    #   - Es un evento de accesos con credencial verificada

    # Resultado del análisis IA (se llena después)
    ai_description: Optional[str]   = None
    ai_alert: bool                  = False
    ai_alert_reason: Optional[str]  = None
    ai_analysis_ms: Optional[int]   = None

    # Metadata original del fabricante (sin tocar)
    raw_metadata: dict          = field(default_factory=dict)

    def __repr__(self):
        return (
            f"SecurityEvent("
            f"source={self.source_type.value}, "
            f"type={self.event_type.value}, "
            f"severity={self.severity.value}, "
            f"confidence={self.confidence:.0%}, "
            f"camera={self.camera_name})"
        )

    def is_high_priority(self) -> bool:
        return self.severity in (Severity.HIGH, Severity.CRITICAL)

    def to_dict(self) -> dict:
        """Serialización para API REST y base de datos."""
        return {
            "id":               self.id,
            "camera_id":        self.camera_id,
            "camera_name":      self.camera_name,
            "timestamp":        self.timestamp.isoformat(),
            "source_type":      self.source_type.value,
            "event_type":       self.event_type.value,
            "severity":         self.severity.value,
            "confidence":       self.confidence,
            "snapshot_path":    self.snapshot_path,
            "needs_ai_analysis":self.needs_ai_analysis,
            "ai_description":   self.ai_description,
            "ai_alert":         self.ai_alert,
            "ai_alert_reason":  self.ai_alert_reason,
            "ai_analysis_ms":   self.ai_analysis_ms,
            "raw_metadata":     self.raw_metadata,
        }
