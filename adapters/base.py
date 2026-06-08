"""
HomeGuard AI — Interfaz base de adaptadores
Todo adaptador de fuente (edge, stream, sensor, webhook) debe heredar de BaseAdapter.
El Core solo habla con esta interfaz — nunca con los adaptadores directamente.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Callable, Awaitable, Optional
from core.event import SecurityEvent

logger = logging.getLogger(__name__)

# Tipo del callback que el Core registra para recibir eventos
EventCallback = Callable[[SecurityEvent], Awaitable[None]]


class BaseAdapter(ABC):
    """
    Interfaz base para todos los adaptadores de HomeGuard.

    Cada adaptador es responsable de:
    1. Conectarse a su fuente (cámara, sensor, API)
    2. Detectar eventos
    3. Construir un SecurityEvent normalizado
    4. Llamar al callback para entregarlo al Core

    El Core no sabe nada de RTSP, ONVIF, MQTT o webhooks.
    Solo recibe SecurityEvents.
    """

    def __init__(self, adapter_id: str, adapter_name: str):
        self.adapter_id = adapter_id
        self.adapter_name = adapter_name
        self._callback: Optional[EventCallback] = None
        self._running = False
        self.logger = logging.getLogger(f"homeguard.adapter.{adapter_id}")

    def register_callback(self, callback: EventCallback):
        """El Core llama esto para registrar su handler de eventos."""
        self._callback = callback

    async def emit(self, event: SecurityEvent):
        """El adaptador llama esto cuando tiene un evento listo."""
        if self._callback:
            await self._callback(event)
        else:
            self.logger.warning(f"Evento generado pero no hay callback registrado: {event}")

    @abstractmethod
    async def start(self) -> bool:
        """Inicia el adaptador. Retorna True si conectó correctamente."""
        ...

    @abstractmethod
    async def stop(self):
        """Detiene el adaptador y libera recursos."""
        ...

    @abstractmethod
    def is_healthy(self) -> bool:
        """Retorna True si el adaptador está funcionando correctamente."""
        ...

    def status(self) -> dict:
        """Estado del adaptador para el dashboard y API."""
        return {
            "id":      self.adapter_id,
            "name":    self.adapter_name,
            "type":    self.__class__.__name__,
            "running": self._running,
            "healthy": self.is_healthy(),
        }
