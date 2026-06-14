#!/usr/bin/env python3
"""
export_openvino.py — HomeGuard AI
Convierte yolov8n.pt al formato OpenVINO optimizado para Intel N4505.

Uso:
    cd ~/homeguard
    python3 scripts/export_openvino.py

Resultado:
    ~/homeguard/yolov8n_openvino_model/   ← directorio con modelo exportado
      yolov8n.xml
      yolov8n.bin
      metadata.yaml
"""

import sys
import time
import subprocess
from pathlib import Path

MODEL_PT   = Path.home() / "homeguard" / "yolov8n.pt"
MODEL_DIR  = Path.home() / "homeguard" / "yolov8n_openvino_model"
IMGSZ      = 320   # Misma resolución que usa el filtro actual


def check_dependencies() -> bool:
    missing = []
    try:
        import ultralytics
    except ImportError:
        missing.append("ultralytics")
    try:
        import openvino
    except ImportError:
        missing.append("openvino")

    if missing:
        print(f"\n❌ Dependencias faltantes: {', '.join(missing)}")
        print(f"   Instalar con:")
        for pkg in missing:
            print(f"   pip install {pkg} --break-system-packages")
        return False
    return True


def export() -> bool:
    if not MODEL_PT.exists():
        print(f"❌ Modelo no encontrado: {MODEL_PT}")
        print(f"   Asegúrate de que yolov8n.pt está en ~/homeguard/")
        return False

    if MODEL_DIR.exists():
        print(f"✅ Modelo OpenVINO ya existe: {MODEL_DIR}")
        print(f"   Para re-exportar: rm -rf {MODEL_DIR} && python3 {__file__}")
        return True

    print(f"\n📦 Exportando yolov8n.pt → OpenVINO (imgsz={IMGSZ})...")
    print(f"   Esto puede tardar 1-2 minutos en el N4505...\n")

    from ultralytics import YOLO
    start = time.monotonic()
    model = YOLO(str(MODEL_PT))
    export_path = model.export(
        format="openvino",
        imgsz=IMGSZ,
        half=False,       # FP32 — más estable en N4505 sin iGPU dedicada
        dynamic=False,
        simplify=True,
    )
    elapsed = int(time.monotonic() - start)

    if MODEL_DIR.exists():
        files = list(MODEL_DIR.iterdir())
        size_mb = sum(f.stat().st_size for f in files) / (1024 ** 2)
        print(f"\n✅ Exportación completada en {elapsed}s")
        print(f"   Directorio: {MODEL_DIR}")
        print(f"   Archivos:   {[f.name for f in files]}")
        print(f"   Tamaño:     {size_mb:.1f} MB")
        return True
    else:
        print(f"❌ Export falló — directorio no creado")
        return False


def benchmark() -> None:
    """Compara latencia PyTorch vs OpenVINO en el hardware actual."""
    import cv2
    import numpy as np
    from ultralytics import YOLO

    print("\n⏱  Benchmark (10 inferencias por backend)...")
    dummy = np.random.randint(0, 255, (IMGSZ, IMGSZ, 3), dtype=np.uint8)
    RUNS = 10

    # PyTorch
    pt_model = YOLO(str(MODEL_PT))
    pt_times = []
    for _ in range(RUNS):
        t = time.monotonic()
        pt_model(dummy, conf=0.4, verbose=False, imgsz=IMGSZ)
        pt_times.append((time.monotonic() - t) * 1000)
    pt_avg = sum(pt_times) / len(pt_times)

    # OpenVINO
    ov_model = YOLO(str(MODEL_DIR))
    ov_times = []
    for _ in range(RUNS):
        t = time.monotonic()
        ov_model(dummy, conf=0.4, verbose=False, imgsz=IMGSZ)
        ov_times.append((time.monotonic() - t) * 1000)
    ov_avg = sum(ov_times) / len(ov_times)

    speedup = pt_avg / ov_avg if ov_avg > 0 else 0

    print(f"\n   PyTorch:  {pt_avg:6.1f} ms/frame")
    print(f"   OpenVINO: {ov_avg:6.1f} ms/frame")
    print(f"   Speedup:  {speedup:.1f}x {'🚀' if speedup > 1.5 else '→'}")


if __name__ == "__main__":
    print("=" * 55)
    print("  HomeGuard AI — Export YOLOv8 → OpenVINO")
    print("=" * 55)

    if not check_dependencies():
        sys.exit(1)

    if not export():
        sys.exit(1)

    # Benchmark opcional
    run_bench = "--bench" in sys.argv or input("\n¿Ejecutar benchmark? (s/N): ").strip().lower() == "s"
    if run_bench:
        benchmark()

    print("\n✅ Listo. Reinicia el servicio para activar OpenVINO:")
    print("   sudo systemctl restart homeguard")
    print("   journalctl -u homeguard -f | grep -i 'yolo\|openvino'")
