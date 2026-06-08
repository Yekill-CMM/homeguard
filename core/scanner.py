"""
HomeGuard AI — Escáner de red LAN
Descubre cámaras IP, sensores y dispositivos IoT en la red local.
No requiere nmap — usa sockets puros para máxima compatibilidad en macOS.
"""

import asyncio
import socket
import ipaddress
import logging
import aiohttp
import re
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# Puertos típicos de dispositivos de seguridad
CAMERA_PORTS = {
    554:  "RTSP",
    8554: "RTSP-alt",
    80:   "HTTP",
    443:  "HTTPS",
    8080: "HTTP-alt",
    8000: "ONVIF",
    37777:"Dahua",
    34567:"Dahua-alt",
}

SENSOR_PORTS = {
    1883: "MQTT",
    8883: "MQTT-SSL",
    502:  "Modbus",
    102:  "S7",
}

# Fingerprints de marcas por headers HTTP / banners
BRAND_SIGNATURES = {
    "hikvision":  ["hikvision", "hik-connect", "webs", "davinci"],
    "dahua":      ["dahua", "dh-", "ipc-", "nvr"],
    "hanwha":     ["hanwha", "wisenet", "samsung techwin", "sno-", "snv-"],
    "axis":       ["axis", "vapix", "axiscam"],
    "reolink":    ["reolink"],
    "tplink":     ["tp-link", "tapo", "tplink"],
    "amcrest":    ["amcrest"],
    "uniview":    ["uniview", "unv"],
    "foscam":     ["foscam"],
    "mqtt":       ["mosquitto", "mqtt"],
}

BRAND_RTSP_TEMPLATES = {
    "hikvision": "rtsp://{user}:{pass}@{ip}:554/Streaming/Channels/101",
    "dahua":     "rtsp://{user}:{pass}@{ip}:554/cam/realmonitor?channel=1&subtype=0",
    "hanwha":    "rtsp://{user}:{pass}@{ip}:554/profile2/media.smp",
    "axis":      "rtsp://{user}:{pass}@{ip}:554/axis-media/media.amp",
    "reolink":   "rtsp://{user}:{pass}@{ip}:554/h264Preview_01_main",
    "tplink":    "rtsp://{user}:{pass}@{ip}:554/stream1",
    "generic":   "rtsp://{user}:{pass}@{ip}:554/stream",
}


@dataclass
class DiscoveredDevice:
    """Dispositivo descubierto en la red."""
    ip: str
    hostname: Optional[str] = None
    open_ports: list[int] = field(default_factory=list)
    device_type: str = "unknown"   # camera | sensor | unknown
    brand: str = "unknown"
    model: Optional[str] = None
    rtsp_url: Optional[str] = None
    onvif_url: Optional[str] = None
    http_title: Optional[str] = None
    source_type: str = "rtsp"      # rtsp | onvif
    scan_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "ip":          self.ip,
            "hostname":    self.hostname,
            "open_ports":  self.open_ports,
            "device_type": self.device_type,
            "brand":       self.brand,
            "model":       self.model,
            "rtsp_url":    self.rtsp_url,
            "onvif_url":   self.onvif_url,
            "http_title":  self.http_title,
            "source_type": self.source_type,
            "scan_ms":     self.scan_ms,
        }


class NetworkScanner:
    """
    Escáner de red LAN para HomeGuard AI.
    Descubre cámaras IP y sensores IoT sin depender de nmap.
    """

    def __init__(self, timeout: float = 0.8, max_concurrent: int = 50):
        self.timeout = timeout
        self.max_concurrent = max_concurrent

    async def scan(
        self,
        subnet: Optional[str] = None,
        progress_callback=None,
    ) -> list[DiscoveredDevice]:
        """
        Escanea la subred LAN completa.

        Args:
            subnet: CIDR a escanear (ej: "192.168.1.0/24").
                    Si es None, autodetecta la red local.
            progress_callback: función async(percent, message) para UI en tiempo real.

        Returns:
            Lista de dispositivos encontrados con sus características.
        """
        if subnet is None:
            subnet = self._detect_local_subnet()

        try:
            network = ipaddress.IPv4Network(subnet, strict=False)
        except ValueError as e:
            logger.error(f"Subred inválida: {subnet} — {e}")
            return []

        hosts = [str(h) for h in network.hosts()]
        total = len(hosts)
        logger.info(f"Escaneando {total} hosts en {subnet}...")

        if progress_callback:
            await progress_callback(0, f"Escaneando {total} hosts en {subnet}...")

        # Fase 1: ping masivo (socket TCP al puerto 80)
        sem = asyncio.Semaphore(self.max_concurrent)
        alive_ips = []

        async def check_alive(ip, idx):
            async with sem:
                if await self._port_open(ip, 80, timeout=0.3) or \
                   await self._port_open(ip, 554, timeout=0.3) or \
                   await self._port_open(ip, 1883, timeout=0.3):
                    alive_ips.append(ip)
                if progress_callback and idx % 20 == 0:
                    pct = int(idx / total * 50)
                    await progress_callback(pct, f"Buscando hosts... {idx}/{total}")

        await asyncio.gather(*[check_alive(ip, i) for i, ip in enumerate(hosts)])

        logger.info(f"Hosts activos: {len(alive_ips)}")
        if progress_callback:
            await progress_callback(50, f"{len(alive_ips)} hosts activos — analizando...")

        # Fase 2: análisis detallado de hosts activos
        devices = []
        for i, ip in enumerate(alive_ips):
            device = await self._analyze_host(ip)
            if device.device_type != "unknown":
                devices.append(device)
            if progress_callback:
                pct = 50 + int(i / max(len(alive_ips), 1) * 50)
                await progress_callback(pct, f"Analizando {ip}...")

        if progress_callback:
            await progress_callback(100, f"Escaneo completo — {len(devices)} dispositivos encontrados")

        logger.info(f"Dispositivos encontrados: {len(devices)}")
        return devices

    async def probe_ip(self, ip: str) -> DiscoveredDevice:
        """
        Analiza una IP específica ingresada manualmente.
        Más exhaustivo que el escaneo masivo.
        """
        logger.info(f"Analizando IP manual: {ip}")
        device = await self._analyze_host(ip, thorough=True)
        # Intentar resolver hostname
        try:
            device.hostname = socket.gethostbyaddr(ip)[0]
        except Exception:
            pass
        return device

    # ------------------------------------------------------------------
    # Métodos internos
    # ------------------------------------------------------------------

    def _detect_local_subnet(self) -> str:
        """Detecta la subred local del sistema."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            # Asumir /24 (clase C doméstica)
            parts = local_ip.rsplit(".", 1)
            return f"{parts[0]}.0/24"
        except Exception:
            return "192.168.1.0/24"

    async def _port_open(self, ip: str, port: int, timeout: float = None) -> bool:
        """Verifica si un puerto TCP está abierto."""
        t = timeout or self.timeout
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port),
                timeout=t,
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except Exception:
            return False

    async def _analyze_host(self, ip: str, thorough: bool = False) -> DiscoveredDevice:
        """Análisis completo de un host: puertos, marca, tipo, URLs."""
        start = datetime.now()
        device = DiscoveredDevice(ip=ip)

        # Puertos a verificar
        all_ports = {**CAMERA_PORTS, **SENSOR_PORTS}
        if thorough:
            extra_ports = [8888, 9000, 4567, 5000, 8081, 9527]
            for p in extra_ports:
                all_ports[p] = "custom"

        # Escanear todos los puertos en paralelo
        tasks = {port: self._port_open(ip, port) for port in all_ports}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        open_ports = [port for port, open_ in zip(tasks.keys(), results) if open_ is True]
        device.open_ports = sorted(open_ports)

        if not open_ports:
            device.scan_ms = int((datetime.now() - start).total_seconds() * 1000)
            return device

        # Clasificar tipo de dispositivo
        has_rtsp    = any(p in open_ports for p in [554, 8554])
        has_http    = any(p in open_ports for p in [80, 443, 8080, 8000])
        has_mqtt    = any(p in open_ports for p in [1883, 8883])
        has_dahua   = any(p in open_ports for p in [37777, 34567])

        if has_mqtt and not has_rtsp:
            device.device_type = "sensor"
        elif has_rtsp or has_dahua:
            device.device_type = "camera"
        elif has_http:
            device.device_type = "camera"  # puede ser cámara sin RTSP activo

        # Fingerprint de marca via HTTP
        if has_http:
            brand, title, model = await self._http_fingerprint(ip, open_ports)
            if brand:
                device.brand = brand
            device.http_title = title
            device.model = model

        # Detectar ONVIF
        if 8000 in open_ports or 80 in open_ports:
            onvif = await self._check_onvif(ip, open_ports)
            if onvif:
                device.onvif_url = onvif
                device.source_type = "onvif"

        # Construir URL RTSP sugerida
        if has_rtsp or device.device_type == "camera":
            template = BRAND_RTSP_TEMPLATES.get(device.brand, BRAND_RTSP_TEMPLATES["generic"])
            device.rtsp_url = template.format(
                user="admin", **{"pass": "password"}, ip=ip
            )

        device.scan_ms = int((datetime.now() - start).total_seconds() * 1000)
        return device

    async def _http_fingerprint(self, ip: str, open_ports: list[int]) -> tuple[str, str, str]:
        """Intenta identificar la marca del dispositivo via HTTP."""
        brand, title, model = "", "", ""

        http_port = next((p for p in [80, 8080, 443, 8000] if p in open_ports), None)
        if not http_port:
            return brand, title, model

        scheme = "https" if http_port == 443 else "http"
        url = f"{scheme}://{ip}:{http_port}/"

        try:
            timeout = aiohttp.ClientTimeout(total=2)
            conn = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(
                timeout=timeout,
                connector=conn,
            ) as session:
                async with session.get(url, allow_redirects=True) as resp:
                    text = (await resp.text(errors="replace")).lower()
                    server = resp.headers.get("Server", "").lower()
                    combined = text[:2000] + server

                    # Buscar marca
                    for b, sigs in BRAND_SIGNATURES.items():
                        if any(s in combined for s in sigs):
                            brand = b
                            break

                    # Extraer título
                    m = re.search(r"<title>(.*?)</title>", text, re.IGNORECASE)
                    if m:
                        title = m.group(1).strip()[:60]

                    # Extraer modelo (patrones comunes)
                    for pattern in [
                        r"(ds-\w+)", r"(ipc-\w+)", r"(sno-\w+)",
                        r"(snv-\w+)", r"(dh-\w+)", r"model[:\s]+(\w+[-\w]+)"
                    ]:
                        m2 = re.search(pattern, combined)
                        if m2:
                            model = m2.group(1).upper()
                            break

        except Exception as e:
            logger.debug(f"HTTP fingerprint {ip}: {e}")

        return brand, title, model

    async def _check_onvif(self, ip: str, open_ports: list[int]) -> Optional[str]:
        """Verifica si el dispositivo responde a ONVIF."""
        onvif_port = next((p for p in [8000, 80] if p in open_ports), None)
        if not onvif_port:
            return None

        # Prueba rápida con SOAP GetSystemDateAndTime
        soap = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
               xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
  <soap:Body><tds:GetSystemDateAndTime/></soap:Body>
</soap:Envelope>"""

        try:
            timeout = aiohttp.ClientTimeout(total=2)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                url = f"http://{ip}:{onvif_port}/onvif/device_service"
                async with session.post(
                    url, data=soap,
                    headers={"Content-Type": "application/soap+xml"},
                ) as resp:
                    if resp.status in (200, 401):
                        return url
        except Exception:
            pass

        return None
