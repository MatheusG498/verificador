"""
Consulta de status de DVRs via API HTTP do fabricante.
- Intelbras / Dahua: CGI (/cgi-bin/...)  — mesmo protocolo
- Hikvision: ISAPI (/ISAPI/...)
Todas as chamadas usam autenticação Digest (com fallback para Basic) e timeout curto,
para funcionar bem através de VPN.
"""
import re
import socket
from datetime import datetime
from typing import Dict, Any, List, Optional

import requests
from requests.auth import HTTPDigestAuth, HTTPBasicAuth

HTTP_TIMEOUT = 4.0
TIME_DRIFT_TOLERANCE_S = 120


def check_port(ip: str, port: int, timeout: float = 2.5) -> bool:
    """Testa se a porta TCP responde (diferencia DVR offline de credencial errada)."""
    try:
        with socket.create_connection((ip, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


class _HttpClient:
    def __init__(self, ip: str, port: int, username: str, password: str):
        self.base = f"http://{ip}:{port}"
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.auth = HTTPDigestAuth(username, password)

    def get(self, path: str) -> Optional[str]:
        url = self.base + path
        try:
            r = self.session.get(url, auth=self.auth, timeout=HTTP_TIMEOUT)
            if r.status_code == 401:
                # Alguns firmwares antigos aceitam apenas Basic
                r = self.session.get(url, auth=HTTPBasicAuth(self.username, self.password), timeout=HTTP_TIMEOUT)
                if r.status_code == 200:
                    self.auth = HTTPBasicAuth(self.username, self.password)
            if r.status_code == 401:
                raise PermissionError("Usuário ou senha inválidos (HTTP 401)")
            if r.status_code != 200:
                return None
            return r.text
        except PermissionError:
            raise
        except requests.RequestException:
            return None


def _parse_kv(text: Optional[str]) -> Dict[str, str]:
    """Converte respostas 'chave=valor' (formato CGI Dahua/Intelbras) em dict."""
    out: Dict[str, str] = {}
    if not text:
        return out
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _xml_all(tag: str, text: Optional[str]) -> List[str]:
    if not text:
        return []
    return re.findall(rf"<{tag}>(.*?)</{tag}>", text, flags=re.S)


def _xml_first(tag: str, text: Optional[str]) -> Optional[str]:
    found = _xml_all(tag, text)
    return found[0].strip() if found else None


def _time_drift(dvr_time_str: Optional[str]) -> Optional[int]:
    if not dvr_time_str:
        return None
    s = dvr_time_str.strip().replace("T", " ")[:19]
    try:
        dvr_dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return int((dvr_dt - datetime.now()).total_seconds())


# ----------------------------------------------------------------------------
# Intelbras / Dahua (CGI)
# ----------------------------------------------------------------------------
def _status_dahua(c: _HttpClient) -> Dict[str, Any]:
    st: Dict[str, Any] = {}

    sysinfo = _parse_kv(c.get("/cgi-bin/magicBox.cgi?action=getSystemInfo"))
    dtype = _parse_kv(c.get("/cgi-bin/magicBox.cgi?action=getDeviceType"))
    soft = _parse_kv(c.get("/cgi-bin/magicBox.cgi?action=getSoftwareVersion"))
    st["model"] = dtype.get("type") or sysinfo.get("deviceType")
    st["serial"] = sysinfo.get("serialNumber")
    st["firmware"] = soft.get("version")

    # Data/hora do DVR
    t = _parse_kv(c.get("/cgi-bin/global.cgi?action=getCurrentTime"))
    st["dvr_time"] = t.get("result")

    # HDs
    storage = _parse_kv(c.get("/cgi-bin/storageDevice.cgi?action=getDeviceAllInfo"))
    disks: Dict[str, Dict[str, Any]] = {}
    for k, v in storage.items():
        m = re.match(r"list\.info\[(\d+)\]\.(State|Detail\[\d+\]\.(TotalBytes|UsedBytes|IsError))", k)
        if not m:
            continue
        d = disks.setdefault(m.group(1), {"id": int(m.group(1)) + 1, "total": 0.0, "used": 0.0, "state": "", "error": False})
        field = m.group(2)
        if field == "State":
            d["state"] = v
        elif field.endswith("TotalBytes"):
            d["total"] += float(v or 0)
        elif field.endswith("UsedBytes"):
            d["used"] += float(v or 0)
        elif field.endswith("IsError"):
            d["error"] = d["error"] or v.lower() == "true"
    st["hdd"] = [
        {
            "id": d["id"],
            "total_gb": round(d["total"] / 1e9, 1),
            "used_gb": round(d["used"] / 1e9, 1),
            "state": "OK" if (d["state"].lower() == "success" and not d["error"]) else (d["state"] or "Erro"),
        }
        for d in disks.values()
    ]

    # Perda de vídeo (índices 0-based)
    vl_text = c.get("/cgi-bin/eventManager.cgi?action=getEventIndexes&code=VideoLoss")
    lost = []
    for k, v in _parse_kv(vl_text).items():
        if k.startswith("channels[") and v.isdigit():
            lost.append(int(v) + 1)
    st["video_loss_channels"] = sorted(lost)
    return st


# ----------------------------------------------------------------------------
# Hikvision (ISAPI)
# ----------------------------------------------------------------------------
def _status_hikvision(c: _HttpClient) -> Dict[str, Any]:
    st: Dict[str, Any] = {}

    info = c.get("/ISAPI/System/deviceInfo")
    st["model"] = _xml_first("model", info)
    st["serial"] = _xml_first("serialNumber", info)
    st["firmware"] = _xml_first("firmwareVersion", info)

    t = c.get("/ISAPI/System/time")
    st["dvr_time"] = _xml_first("localTime", t)

    storage = c.get("/ISAPI/ContentMgmt/Storage")
    hdds = []
    for block in _xml_all("hdd", storage):
        cap = float(_xml_first("capacity", block) or 0)      # MB
        free = float(_xml_first("freeSpace", block) or 0)    # MB
        status = (_xml_first("status", block) or "").lower()
        hdds.append({
            "id": int(_xml_first("id", block) or len(hdds) + 1),
            "total_gb": round(cap / 1024, 1),
            "used_gb": round((cap - free) / 1024, 1),
            "state": "OK" if status in ("ok", "normal") else (status or "Erro"),
        })
    st["hdd"] = hdds

    # Perda de vídeo: canais IP (NVR) e analógicos (DVR)
    lost = []
    proxy = c.get("/ISAPI/ContentMgmt/InputProxy/channels/status")
    for block in _xml_all("InputProxyChannelStatus", proxy):
        ch_id = _xml_first("id", block)
        online = (_xml_first("online", block) or "").lower()
        if ch_id and ch_id.isdigit() and online == "false":
            lost.append(int(ch_id))
    analog = c.get("/ISAPI/System/Video/inputs/channels")
    for block in _xml_all("VideoInputChannel", analog):
        ch_id = _xml_first("id", block)
        res = (_xml_first("resDesc", block) or "").upper()
        if ch_id and ch_id.isdigit() and "NO VIDEO" in res:
            lost.append(int(ch_id))
    st["video_loss_channels"] = sorted(set(lost))
    return st


def get_dvr_status(brand: str, ip: str, http_port: int, rtsp_port: int,
                   username: str, password: str) -> Dict[str, Any]:
    """Retorna o status consolidado do DVR (online, HD, data/hora, perda de vídeo)."""
    result: Dict[str, Any] = {
        "online": False,
        "http_ok": False,
        "rtsp_ok": False,
        "model": None,
        "serial": None,
        "firmware": None,
        "hdd": [],
        "hdd_ok": None,
        "dvr_time": None,
        "time_drift_s": None,
        "time_ok": None,
        "video_loss_channels": [],
        "api_error": None,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    result["rtsp_ok"] = check_port(ip, rtsp_port)
    result["http_ok"] = check_port(ip, http_port)
    result["online"] = result["rtsp_ok"] or result["http_ok"]

    if not result["online"]:
        result["api_error"] = "DVR inacessível (portas HTTP e RTSP não respondem). Verifique a VPN."
        return result
    if not result["http_ok"]:
        result["api_error"] = f"Porta HTTP {http_port} fechada — status do DVR indisponível, só imagens via RTSP."
        return result

    client = _HttpClient(ip, http_port, username, password)
    try:
        b = (brand or "").lower()
        if b == "hikvision":
            data = _status_hikvision(client)
        else:  # intelbras / dahua
            data = _status_dahua(client)
        result.update(data)
    except PermissionError as e:
        result["api_error"] = str(e)
        return result
    except Exception as e:  # resposta inesperada do firmware
        result["api_error"] = f"Falha ao ler API do DVR: {e}"

    if result["hdd"]:
        result["hdd_ok"] = all(h["state"] == "OK" for h in result["hdd"])
    elif result["api_error"] is None:
        result["hdd_ok"] = False  # DVR sem HD detectado = não está gravando

    drift = _time_drift(result["dvr_time"])
    result["time_drift_s"] = drift
    result["time_ok"] = None if drift is None else abs(drift) <= TIME_DRIFT_TOLERANCE_S

    if not any([result["model"], result["dvr_time"], result["hdd"]]) and result["api_error"] is None:
        result["api_error"] = "API não respondeu nos endpoints esperados (verifique a marca selecionada)."
    return result
