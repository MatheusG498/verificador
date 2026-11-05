"""
Módulo de construção de URLs RTSP e conexão para diversas marcas de CFTV.
Adaptado e aprimorado a partir da engenharia reversa do sistema original.
"""
from enum import Enum
from typing import Optional, Tuple
import urllib.parse

class CameraBrand(str, Enum):
    INTELBRAS = "intelbras"
    HIKVISION = "hikvision"
    DAHUA = "dahua"
    AXIS = "axis"
    HANWHA = "hanwha"
    UNIVIEW = "uniview"
    GENERIC = "generic"

def build_rtsp_url(
    brand: str,
    ip: str,
    port: int = 554,
    username: str = "admin",
    password: str = "",
    channel: int = 1,
    substream: bool = True,
    custom_url: Optional[str] = None
) -> str:
    """
    Constrói a URL RTSP adequada de acordo com o protocolo e padrão de cada fabricante.
    """
    if custom_url and custom_url.strip():
        return custom_url.strip()

    brand_lower = brand.lower() if brand else "generic"
    stream_id = 2 if substream else 1
    
    # URL encode credentials safely
    user_encoded = urllib.parse.quote(username, safe="")
    pass_encoded = urllib.parse.quote(password, safe="")
    auth = f"{user_encoded}:{pass_encoded}@" if username else ""

    if brand_lower in ("intelbras", "dahua"):
        # Padrão Dahua / Intelbras: rtsp://user:pass@ip:port/cam/realmonitor?channel=1&subtype=0|1
        subtype = 1 if substream else 0
        return f"rtsp://{auth}{ip}:{port}/cam/realmonitor?channel={channel}&subtype={subtype}"

    elif brand_lower == "hikvision":
        # Padrão Hikvision: rtsp://user:pass@ip:port/Streaming/Channels/101 (101=main, 102=sub)
        ch_str = f"{channel}0{stream_id}"
        return f"rtsp://{auth}{ip}:{port}/Streaming/Channels/{ch_str}"

    elif brand_lower == "axis":
        # Padrão Axis: rtsp://user:pass@ip:port/axis-media/media.amp?videocodec=h264
        return f"rtsp://{auth}{ip}:{port}/axis-media/media.amp?videocodec=h264&camera={channel}"

    elif brand_lower == "hanwha":
        # Hanwha / Samsung
        profile = 2 if substream else 1
        return f"rtsp://{auth}{ip}:{port}/profile{profile}/media.smp"

    elif brand_lower == "uniview":
        # Uniview
        return f"rtsp://{auth}{ip}:{port}/unicast/c{channel}/s{stream_id}/live"

    else:
        # Genérico RTSP
        return f"rtsp://{auth}{ip}:{port}/live/ch{channel}"

def mask_rtsp_url(url: str) -> str:
    """Oculta a senha na URL para exibição segura na interface."""
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.password:
            user = parsed.username or ""
            netloc = f"{user}:****@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
            return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
    except Exception:
        pass
    return url
