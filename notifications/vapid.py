"""
HomeGuard AI — Claves VAPID
Genera y persiste las claves para Web Push sin Firebase.
"""

import base64
import logging
import tempfile
import os
from py_vapid import Vapid
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat
)

logger = logging.getLogger(__name__)


class VAPIDManager:
    """Gestiona las claves VAPID del servidor HomeGuard."""

    def __init__(self, db):
        self.db = db
        self._public_key_b64: str | None = None
        self._private_pem: str | None = None
        self._vapid: Vapid | None = None
        self._load_or_generate()

    def _load_or_generate(self):
        pub  = self._get_config("vapid_public_key")
        priv = self._get_config("vapid_private_key")

        if pub and priv:
            self._public_key_b64 = pub
            self._private_pem    = priv
            self._vapid = Vapid.from_pem(priv.encode())
            logger.info("Claves VAPID cargadas desde DB")
        else:
            self._generate_keys()

    def _generate_keys(self):
        v = Vapid()
        v.generate_keys()

        # Guardar en archivos temporales para leer como PEM
        with tempfile.TemporaryDirectory() as d:
            priv_path = os.path.join(d, "priv.pem")
            pub_path  = os.path.join(d, "pub.pem")
            v.save_key(priv_path)
            v.save_public_key(pub_path)
            self._private_pem = open(priv_path).read()

        # Clave pública en formato uncompressed base64url (para browsers)
        pub_bytes = v.public_key.public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint
        )
        self._public_key_b64 = base64.urlsafe_b64encode(pub_bytes).decode().rstrip("=")
        self._vapid = Vapid.from_pem(self._private_pem.encode())

        self._set_config("vapid_public_key",  self._public_key_b64)
        self._set_config("vapid_private_key", self._private_pem)
        logger.info(f"Claves VAPID generadas — pub: {self._public_key_b64[:30]}...")

    def _get_config(self, key: str) -> str | None:
        try:
            with self.db._connect() as conn:
                row = conn.execute(
                    "SELECT value FROM system_config WHERE key = ?", (key,)
                ).fetchone()
                return row[0] if row else None
        except Exception:
            return None

    def _set_config(self, key: str, value: str):
        from datetime import datetime
        try:
            with self.db._connect() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO system_config (key, value, updated_at)
                    VALUES (?, ?, ?)
                """, (key, value, datetime.now().isoformat()))
                conn.commit()
        except Exception as e:
            logger.error(f"Error guardando {key}: {e}")

    @property
    def public_key(self) -> str:
        return self._public_key_b64

    @property
    def private_key(self) -> str:
        return self._private_pem

    @property
    def vapid(self) -> Vapid:
        return self._vapid
