#!/usr/bin/env python3
import os, sys, base64
from pathlib import Path
try:
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
except ImportError:
    print("ERROR: pip install cryptography --break-system-packages")
    sys.exit(1)

DATA_DIR = Path.home() / "homeguard/data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
PRIVATE_KEY_PATH = DATA_DIR / "vapid_private.pem"

if PRIVATE_KEY_PATH.exists():
    print(f"Clave existente en {PRIVATE_KEY_PATH}, cargando...")
    with open(PRIVATE_KEY_PATH, "rb") as f:
        private_key = serialization.load_pem_private_key(f.read(), password=None)
else:
    print("Generando claves VAPID...")
    private_key = ec.generate_private_key(ec.SECP256R1())
    with open(PRIVATE_KEY_PATH, "wb") as f:
        f.write(private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()
        ))
    os.chmod(PRIVATE_KEY_PATH, 0o600)
    print(f"Clave privada: {PRIVATE_KEY_PATH}")

pub_bytes = private_key.public_key().public_bytes(
    serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
pub_b64 = base64.urlsafe_b64encode(pub_bytes).decode().rstrip("=")

print("\n" + "="*65)
print("Agrega esto a ~/homeguard/.env:")
print("="*65)
print(f"VAPID_PUBLIC_KEY={pub_b64}")
print(f"VAPID_PRIVATE_KEY_PATH={PRIVATE_KEY_PATH}")
print("VAPID_MAILTO=mailto:admin@homeguard.local")
print("="*65)
