"""
HomeGuard AI — Configuración central del sistema v2
"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CameraConfig:
    id: str
    name: str
    rtsp_url: str
    enabled: bool = True
    width: Optional[int] = None
    height: Optional[int] = None
    analysis_fps: int = 5
    # Modo de integración
    use_onvif: bool = False          # True = edge analytics vía ONVIF
    onvif_user: str = ""
    onvif_password: str = ""
    ai_confidence_threshold: float = 0.85  # Por encima de esto no llama a Claude


@dataclass
class MotionConfig:
    min_contour_area: int = 1500
    threshold: int = 25
    blur_kernel: int = 21
    cooldown_frames: int = 30
    min_seconds_between_analysis: float = 5.0


@dataclass
class ClaudeConfig:
    api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 512
    system_prompt: str = (
        "Eres un sistema de análisis de seguridad residencial. "
        "Analiza la imagen y clasifica lo que ves. "
        "Responde SIEMPRE en JSON con este formato exacto:\n"
        '{"event_type": "person|vehicle|animal|intrusion|no_threat|unclear", '
        '"confidence": 0.0-1.0, '
        '"description": "descripción breve en español", '
        '"alert": true|false, '
        '"alert_reason": "razón si alert es true, null si false"}'
    )


@dataclass
class StorageConfig:
    base_path: str = "./data"
    clips_path: str = "./data/clips"
    frames_path: str = "./data/frames"
    db_path: str = "./data/homeguard.db"
    jpeg_quality: int = 85
    retention_days: int = 30


@dataclass
class AppConfig:
    cameras: list[CameraConfig] = field(default_factory=list)
    motion: MotionConfig = field(default_factory=MotionConfig)
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    api_port: int = 8000
    log_level: str = "INFO"
    # MQTT para sensores IoT
    mqtt_enabled: bool = False
    mqtt_broker: str = "localhost"
    mqtt_port: int = 1883


def load_config() -> AppConfig:
    return AppConfig(
        cameras=[
            CameraConfig(
                id="cam_01",
                name="Entrada principal",
                rtsp_url=os.environ.get(
                    "CAMERA_01_URL",
                    "rtsp://admin:password@192.168.1.100:554/stream"
                ),
                analysis_fps=5,
                use_onvif=os.environ.get("CAMERA_01_ONVIF", "false").lower() == "true",
                onvif_user=os.environ.get("CAMERA_01_USER", "admin"),
                onvif_password=os.environ.get("CAMERA_01_PASS", ""),
            ),
        ],
        motion=MotionConfig(),
        claude=ClaudeConfig(),
        storage=StorageConfig(),
        mqtt_enabled=os.environ.get("MQTT_ENABLED", "false").lower() == "true",
        mqtt_broker=os.environ.get("MQTT_BROKER", "localhost"),
    )
