"""
Liga os DVRs do app às lojas do cliente (MongoDB easicash.lojas_v2), para filtrar por cliente/loja.
Vínculo: o IP do DVR cadastrado = um item de `ip_dvr` da loja. Daí vêm `cliente` e `loja`.

Config da conexão: variável de ambiente MONGO_URI (serviço) ou o arquivo data/mongo_uri (dev).
Tudo tolerante a falha: sem URI ou Mongo fora do ar, as funções devolvem vazio e o app segue sem o filtro.
"""
import os
import threading
import time
from typing import Any, Dict, List, Optional

_DB_NAME = "easicash"
_COL = "lojas_v2"
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

_lock = threading.Lock()
_client = None
_cache: Dict[str, Optional[Dict[str, str]]] = {}   # ip -> {cliente, loja} (ou None)
_cache_at = 0.0


def _uri() -> str:
    uri = os.environ.get("MONGO_URI", "").strip()
    if uri:
        return uri
    f = os.path.join(_DATA_DIR, "mongo_uri")
    if os.path.isfile(f):
        try:
            return open(f, encoding="utf-8").read().strip()
        except Exception:
            return ""
    return ""


def _db():
    global _client
    if _client is None:
        from pymongo import MongoClient  # import tardio: sem Mongo configurado, nem carrega o driver
        uri = _uri()
        if not uri:
            return None
        _client = MongoClient(uri, serverSelectionTimeoutMS=4000, connectTimeoutMS=4000)
    return _client[_DB_NAME]


def enabled() -> bool:
    return bool(_uri())


def store_by_ip(ip: str) -> Optional[Dict[str, str]]:
    """{'cliente':..., 'loja':...} da loja cujo ip_dvr contém este IP, ou None. Cacheado por 10 min."""
    if not ip:
        return None
    if ip in _cache:
        return _cache[ip]
    try:
        with _lock:
            db = _db()
            if db is None:
                return None
            doc = db[_COL].find_one({"ip_dvr": ip}, {"cliente": 1, "loja": 1})
        res = {"cliente": doc.get("cliente") or "", "loja": doc.get("loja") or ""} if doc else None
        _cache[ip] = res
        return res
    except Exception:
        return None


def refresh_cache():
    _cache.clear()


def iter_stores(cliente: Optional[str] = None, limit: Optional[int] = None, loja: Optional[str] = None):
    """Percorre as lojas de easicash.lojas_v2 (para descoberta da frota). Vazio se não houver Mongo.
    loja: filtra uma loja específica (por nome exato)."""
    db = _db()
    if db is None:
        return
    filtro: Dict[str, Any] = {}
    if cliente:
        filtro["cliente"] = cliente
    if loja:
        filtro["loja"] = loja
    cur = db[_COL].find(filtro, {"cliente": 1, "loja": 1, "ip_dvr": 1, "modelo_dvr": 1,
                                 "porta_dvr": 1, "porta_rtsp_dvr": 1, "usuario_dvr": 1, "senha_dvr": 1,
                                 "mac": 1, "ip_openvpn": 1})
    if limit:
        cur = cur.limit(int(limit))
    for doc in cur:
        yield doc


def openvpn_by_mac(mac: str) -> Optional[str]:
    """ip_openvpn ATUAL da loja pelo mac (chave estável; o ip_openvpn muda de tempo em tempo). None se inválido."""
    if not mac:
        return None
    try:
        db = _db()
        if db is None:
            return None
        d = db[_COL].find_one({"mac": mac}, {"ip_openvpn": 1})
        ip = str((d or {}).get("ip_openvpn") or "").strip()
        return ip if ip and ip != "0.0.0.0" else None
    except Exception:
        return None


def clientes_lojas() -> Dict[str, list]:
    """{cliente: [lojas ordenadas]} de toda a lojas_v2, ignorando lojas com nome começando em '[' (desativadas/tag)."""
    try:
        db = _db()
        if db is None:
            return {}
        arvore: Dict[str, set] = {}
        for d in db[_COL].find({}, {"cliente": 1, "loja": 1}):
            cli = (d.get("cliente") or "").strip()
            loja = (d.get("loja") or "").strip()
            if not cli or not loja or loja.startswith("["):
                continue
            arvore.setdefault(cli, set()).add(loja)
        return {c: sorted(arvore[c]) for c in sorted(arvore)}
    except Exception:
        return {}


def check_login(username: str, password: str) -> Optional[Dict[str, Any]]:
    """Valida usuário/senha contra easicash.logins (senha em bcrypt).
    Retorna {'username','auditoria','patcher'} se a senha bater (mesmo sem permissão), ou None se inválido."""
    if not username or not password:
        return None
    try:
        import bcrypt
        db = _db()
        if db is None:
            return None
        u = db["logins"].find_one({"username": username})
        if not u or not u.get("password"):
            return None
        if not bcrypt.checkpw(password.encode("utf-8"), str(u["password"]).encode("utf-8")):
            return None
        return {"username": u.get("username"), "auditoria": bool(u.get("auditoria")),
                "patcher": bool(u.get("patcher"))}
    except Exception:
        return None


def list_clientes() -> list:
    """Lista de clientes distintos em lojas_v2 (para a UI). [] se não houver Mongo."""
    try:
        db = _db()
        return sorted(c for c in db[_COL].distinct("cliente") if c) if db is not None else []
    except Exception:
        return []
