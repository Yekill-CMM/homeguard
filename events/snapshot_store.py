"""
HomeGuard AI — Almacenamiento de snapshots
Guarda los frames JPEG en disco organizado por fecha y cámara.
"""

import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.event import SecurityEvent

logger = logging.getLogger(__name__)


class SnapshotStore:
    """
    Guarda snapshots JPEG en disco con estructura organizada:
    data/frames/
        YYYY-MM-DD/
            cam_01/
                HH-MM-SS_event_type.jpg
    """

    def __init__(self, base_path: str = "./data/frames"):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def save(self, event: SecurityEvent) -> Optional[str]:
        """
        Guarda el snapshot del evento en disco.
        Retorna la ruta relativa del archivo, o None si falla.
        """
        if not event.snapshot:
            return None

        try:
            date_str = event.timestamp.strftime("%Y-%m-%d")
            time_str = event.timestamp.strftime("%H-%M-%S")
            event_label = event.event_type.value

            folder = self.base_path / date_str / event.camera_id
            folder.mkdir(parents=True, exist_ok=True)

            filename = f"{time_str}_{event_label}_{event.id[:8]}.jpg"
            filepath = folder / filename

            filepath.write_bytes(event.snapshot)

            relative = str(filepath.relative_to(self.base_path.parent))
            logger.debug(f"Snapshot guardado: {relative}")
            return relative

        except Exception as e:
            logger.error(f"Error guardando snapshot: {e}")
            return None

    def cleanup_old(self, retention_days: int = 30) -> int:
        """Elimina snapshots más antiguos que retention_days."""
        from datetime import timedelta
        import shutil

        cutoff = datetime.now() - timedelta(days=retention_days)
        deleted = 0

        for date_folder in self.base_path.iterdir():
            if not date_folder.is_dir():
                continue
            try:
                folder_date = datetime.strptime(date_folder.name, "%Y-%m-%d")
                if folder_date < cutoff:
                    shutil.rmtree(date_folder)
                    deleted += 1
                    logger.info(f"Carpeta eliminada: {date_folder.name}")
            except ValueError:
                continue

        return deleted



