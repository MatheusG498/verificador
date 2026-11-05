"""
Gerenciador de DVRs (Digital Video Recorders / NVRs)
Permite cadastrar DVRs de lojas, gerenciar canais em lote para PDVs e testar conectividade RTSP/HTTP.
"""
import os
import json
import time
import uuid
import socket
import re
import threading
from typing import List, Dict, Any, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
DVRS_FILE = os.path.join(DATA_DIR, "dvrs.json")
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
FRAMES_DIR = os.path.join(ROOT_DIR, "frames")
VIDEOS_DIR = os.path.join(ROOT_DIR, "videos")

os.makedirs(DATA_DIR, exist_ok=True)

class DVRManager:
    _lock = threading.RLock()

    @classmethod
    def load_dvrs(cls) -> List[Dict[str, Any]]:
        """Carrega lista de DVRs cadastrados."""
        with cls._lock:
            bak_file = DVRS_FILE + ".bak"
            if not os.path.exists(DVRS_FILE) or os.path.getsize(DVRS_FILE) == 0:
                if os.path.exists(bak_file) and os.path.getsize(bak_file) > 0:
                    try:
                        with open(bak_file, "r", encoding="utf-8") as bf:
                            data = json.load(bf)
                            if data:
                                cls.save_dvrs(data)
                                return data
                    except Exception:
                        pass

                default_dvrs = [
                    {
                        "id": "dvr-intelbras-01",
                        "name": "DVR Loja 01 — Intelbras MHDX",
                        "brand": "intelbras",
                        "ip": "10.59.151.131",
                        "port": 554,
                        "http_port": 80,
                        "username": "admin",
                        "password": "",
                        "channel_count": 16,
                        "status": "ONLINE",
                        "last_ping": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
                    }
                ]
                cls.save_dvrs(default_dvrs)
                return default_dvrs

            try:
                with open(DVRS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # [] é estado válido (usuário apagou tudo); o .bak só cobre arquivo corrompido (except abaixo)
                    return data if isinstance(data, list) else []
            except Exception:
                if os.path.exists(bak_file) and os.path.getsize(bak_file) > 0:
                    try:
                        with open(bak_file, "r", encoding="utf-8") as bf:
                            return json.load(bf)
                    except Exception:
                        pass
                return []

    @classmethod
    def save_dvrs(cls, dvrs: List[Dict[str, Any]]):
        """Salva a lista de DVRs em JSON com substituição atômica."""
        with cls._lock:
            tmp_file = DVRS_FILE + ".tmp"
            bak_file = DVRS_FILE + ".bak"
            try:
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(dvrs, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_file, DVRS_FILE)
                if dvrs:
                    with open(bak_file, "w", encoding="utf-8") as bf:
                        json.dump(dvrs, bf, ensure_ascii=False, indent=2)
            except Exception:
                with open(DVRS_FILE, "w", encoding="utf-8") as f:
                    json.dump(dvrs, f, ensure_ascii=False, indent=2)

    @classmethod
    def get_dvr(cls, dvr_id: str) -> Optional[Dict[str, Any]]:
        dvrs = cls.load_dvrs()
        for d in dvrs:
            if d.get("id") == dvr_id:
                return d
        return None

    @classmethod
    def update_dvr(cls, dvr_id: str, data: Dict[str, Any], sync_linked_cameras: bool = True) -> Optional[Dict[str, Any]]:
        """Atualiza dados do DVR e propaga alterações para as câmeras associadas."""
        from core.camera_manager import CameraManager

        dvrs = cls.load_dvrs()
        target_dvr = None
        for idx, d in enumerate(dvrs):
            if d.get("id") == dvr_id:
                for k, v in data.items():
                    if k != "id":
                        d[k] = v
                dvrs[idx] = d
                target_dvr = d
                break

        if not target_dvr:
            return None

        cls.save_dvrs(dvrs)

        # Se sync_linked_cameras=True, propaga IP, portas e credenciais para todos os PDVs vinculados
        if sync_linked_cameras:
            cameras = CameraManager.load_cameras()
            changed = False
            for c in cameras:
                if c.get("dvr_id") == dvr_id:
                    for field in ("ip", "port", "http_port", "username", "password", "brand"):
                        if field in target_dvr:
                            c[field] = target_dvr[field]
                    from core.rtsp_builder import build_rtsp_url, mask_rtsp_url
                    c["rtsp_url"] = build_rtsp_url(
                        brand=c.get("brand", "intelbras"), ip=c.get("ip", ""), port=int(c.get("port") or 554),
                        username=c.get("username", "admin"), password=c.get("password", ""),
                        channel=int(c.get("channel") or 1), substream=bool(c.get("substream", True)),
                        custom_url=c.get("custom_url")
                    )
                    c["masked_url"] = mask_rtsp_url(c["rtsp_url"])
                    changed = True
            if changed:
                CameraManager.save_cameras(cameras)

        return target_dvr

    @classmethod
    def test_connection(cls, ip: str, port: int = 554, http_port: int = 80, timeout: float = 2.5) -> Dict[str, Any]:
        """Testa conectividade TCP nas portas RTSP e HTTP do DVR."""
        if not ip or not ip.strip():
            return {"success": False, "message": "Endereço IP não informado"}

        ip = ip.strip()
        rtsp_ok = False
        http_ok = False
        latency_ms = 0.0

        t0 = time.time()
        # Teste RTSP
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((ip, int(port)))
            s.close()
            rtsp_ok = True
            latency_ms = (time.time() - t0) * 1000
        except Exception:
            rtsp_ok = False

        # Teste HTTP
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((ip, int(http_port)))
            s.close()
            http_ok = True
            if not rtsp_ok:
                latency_ms = (time.time() - t0) * 1000
        except Exception:
            http_ok = False

        success = rtsp_ok or http_ok
        if rtsp_ok and http_ok:
            msg = f"Conexão bem-sucedida! Portas RTSP ({port}) e HTTP ({http_port}) respondendo ({latency_ms:.0f}ms)."
        elif rtsp_ok:
            msg = f"Porta RTSP ({port}) aberta e respondendo ({latency_ms:.0f}ms)."
        elif http_ok:
            msg = f"Porta Web HTTP ({http_port}) aberta e respondendo ({latency_ms:.0f}ms)."
        else:
            msg = f"Não foi possível conectar ao DVR no IP {ip}. Verifique VPN ou se o equipamento está ligado."

        return {
            "success": success,
            "ip": ip,
            "rtsp_ok": rtsp_ok,
            "http_ok": http_ok,
            "latency_ms": round(latency_ms, 1),
            "message": msg
        }

    @classmethod
    def _http_get(cls, url: str, username: str, password: str, timeout: float = 4.0) -> str:
        import urllib.request
        pm = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        pm.add_password(None, url, username or "", password or "")
        opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(pm),
                                             urllib.request.HTTPBasicAuthHandler(pm))
        with opener.open(urllib.request.Request(url, headers={"User-Agent": "CFTV-Auditor/1.0"}), timeout=timeout) as r:
            return r.read().decode("utf-8", "ignore")

    @classmethod
    def fetch_channel_titles(cls, ip: str, http_port: int = 80, username: str = "admin",
                             password: str = "", brand: str = "intelbras", timeout: float = 4.0,
                             tunnel_host: Optional[str] = None, tunnel_mac: Optional[str] = None) -> Dict[int, str]:
        """Nomes dos canais ATIVOS lidos do próprio DVR (Intelbras/Dahua: ChannelTitle; Hikvision: ISAPI).
        {canal(base 1) -> nome}. Canais vazios ('---') ficam de fora. {} se o DVR não responder.
        Com tunnel_host/mac, abre um túnel SSH sob demanda até o DVR na LAN da loja."""
        from core.janus_import import parse_channel_titles
        from core.tunnel import dvr_tunnel
        brand = (brand or "").lower()
        try:
            with dvr_tunnel(tunnel_host, tunnel_mac, ip, int(http_port)) as (h, p):
                if brand == "hikvision":
                    # InputProxy (câmeras IP, nomes reais) primeiro; depois System/Video (analógico).
                    for ep in ("/ISAPI/ContentMgmt/InputProxy/channels", "/ISAPI/System/Video/inputs/channels"):
                        try:
                            t = parse_channel_titles(cls._http_get(f"http://{h}:{p}{ep}", username, password, timeout), "hikvision")
                            if t:
                                return t
                        except Exception:
                            continue
                    return {}
                url = f"http://{h}:{p}/cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle"
                return parse_channel_titles(cls._http_get(url, username, password, timeout), brand)
        except Exception:
            return {}

    @classmethod
    def import_from_janus(cls, text: str) -> Dict[str, Any]:
        """Lê um janus.plugin.streaming.jcfg, agrupa os mountpoints RTSP por IP (um DVR por IP) e cria os PDVs.
        A quantidade/nome dos PDVs vem dos canais ATIVOS do próprio DVR (quando responde); senão, dos mountpoints."""
        from core.janus_import import extract_cameras
        from core.camera_manager import CameraManager
        cams = extract_cameras(text)
        if not cams:
            return {"success": False, "message": "Nenhum mountpoint RTSP encontrado no arquivo."}
        by_ip: Dict[Any, list] = {}
        for c in cams:
            by_ip.setdefault((c["ip"], c["port"]), []).append(c)

        dvrs_por_ip = {d.get("ip"): d for d in cls.load_dvrs()}  # idempotente: reusa DVR já cadastrado por IP
        resumo, novos_dvrs, novos_pdvs = [], 0, 0
        for (ip, port), grupo in by_ip.items():
            g0 = grupo[0]
            http_port = 80  # o jcfg só traz RTSP; snapshot/API de nomes usam a porta web padrão
            titulos = cls.fetch_channel_titles(ip, http_port, g0["username"], g0["password"], g0["brand"])
            existente = dvrs_por_ip.get(ip)
            if existente:
                dvr_id, name = existente["id"], existente.get("name", f"DVR {ip}")
            else:
                name = f"DVR {ip}"
                nova = cls.add_dvr({"ip": ip, "port": port, "http_port": http_port, "username": g0["username"],
                                    "password": g0["password"], "brand": g0["brand"], "name": name,
                                    "channel_count": len(titulos) or len(grupo)}, auto_create_pdvs=False)
                dvr_id = nova["dvr"]["id"]
                novos_dvrs += 1
            # canais já cadastrados neste DVR (não recria)
            tem = {int(c.get("channel") or 0) for c in CameraManager.load_cameras() if c.get("dvr_id") == dvr_id}
            feitos = 0
            for c in grupo:
                # decide pelos ativos do DVR: canal sem nome no DVR (inativo) é ignorado quando a detecção respondeu
                if titulos and c["channel"] not in titulos:
                    continue
                if c["channel"] in tem:
                    continue  # já existe -> adição idempotente
                pdv_name = titulos.get(c["channel"]) or c.get("mount_id") or f"PDV {c['channel']:02d}"
                CameraManager.add_camera({
                    "name": f"{pdv_name} — {name}", "dvr_id": dvr_id, "dvr_name": name,
                    "channel": c["channel"], "brand": c["brand"], "ip": ip, "port": port,
                    "username": c["username"], "password": c["password"],
                    "substream": c["subtype"] >= 1, "is_simulation": False})
                tem.add(c["channel"])
                feitos += 1
            novos_pdvs += feitos
            resumo.append({"ip": ip, "dvr_id": dvr_id, "pdvs_criados": feitos, "dvr_novo": not existente,
                           "canais_ativos_detectados": len(titulos)})
        cls.sync_stores()  # liga os novos DVRs/PDVs às lojas do Mongo (cliente/loja)
        return {"success": True, "dvrs": resumo, "dvrs_novos": novos_dvrs, "total_pdvs": novos_pdvs}

    @classmethod
    def redetect_channels(cls, dvr_id: str) -> Dict[str, Any]:
        """Relê os canais ATIVOS do DVR e ajusta os PDVs: renomeia os que batem, cria os que faltam e remove os
        canais que não existem mais no DVR. Preserva referência/calibração dos canais que continuam ativos."""
        from core.camera_manager import CameraManager
        dvr = cls.get_dvr(dvr_id)
        if not dvr:
            return {"success": False, "message": "DVR não encontrado"}
        tmac = dvr.get("tunnel_mac") or None
        titulos = {}
        for _ in range(3 if tmac else 1):   # túnel: IP dinâmico falha às vezes
            titulos = cls.fetch_channel_titles(dvr["ip"], int(dvr.get("http_port") or 80),
                                               dvr.get("username"), dvr.get("password"), dvr.get("brand"),
                                               tunnel_host=dvr.get("tunnel_host") or None, tunnel_mac=tmac)
            if titulos:
                break
            time.sleep(1.5)
        if not titulos:
            return {"success": False, "message": "O DVR não respondeu à leitura de canais (confira marca, usuário/senha e rede)."}
        por_canal = {int(c.get("channel") or 0): c for c in CameraManager.load_cameras() if c.get("dvr_id") == dvr_id}
        name = dvr.get("name")
        renomeados = criados = removidos = 0
        for ch, titulo in titulos.items():
            novo_nome = f"{titulo} — {name}"
            if ch in por_canal:
                if por_canal[ch].get("name") != novo_nome:
                    CameraManager.update_camera(por_canal[ch]["id"], {"name": novo_nome})
                    renomeados += 1
            else:
                CameraManager.add_camera({
                    "name": novo_nome, "dvr_id": dvr_id, "dvr_name": name, "channel": ch,
                    "brand": dvr.get("brand"), "ip": dvr["ip"], "port": int(dvr.get("port") or 554),
                    "username": dvr.get("username"), "password": dvr.get("password"),
                    "substream": True, "is_simulation": False})
                criados += 1
        for ch, c in por_canal.items():
            if ch not in titulos:                 # canal sem câmera no DVR (genérico do cadastro antigo) -> remove
                CameraManager.delete_camera(c["id"])
                removidos += 1
        cls.update_dvr(dvr_id, {"channel_count": len(titulos)})
        cls.sync_stores()  # garante cliente/loja nos PDVs recriados
        return {"success": True, "detectados": len(titulos), "renomeados": renomeados,
                "criados": criados, "removidos": removidos}

    @classmethod
    def sync_stores(cls) -> Dict[str, Any]:
        """Liga cada DVR/câmera à sua loja no MongoDB (easicash.lojas_v2) pelo IP, gravando cliente/loja.
        Idempotente e tolerante: sem Mongo configurado, não faz nada."""
        from core.mongo_store import store_by_ip, enabled, refresh_cache
        from core.camera_manager import CameraManager
        if not enabled():
            return {"success": False, "message": "MongoDB não configurado (defina MONGO_URI ou data/mongo_uri)."}
        refresh_cache()
        dvrs = cls.load_dvrs()
        dvr_vinc = 0
        for d in dvrs:
            info = store_by_ip(d.get("ip"))
            if info:
                d["cliente"], d["loja"] = info["cliente"], info["loja"]
                dvr_vinc += 1
        cls.save_dvrs(dvrs)
        cams = CameraManager.load_cameras()
        cam_upd = 0
        for c in cams:
            info = store_by_ip(c.get("ip"))
            if info and (c.get("cliente") != info["cliente"] or c.get("loja") != info["loja"]):
                c["cliente"], c["loja"] = info["cliente"], info["loja"]
                cam_upd += 1
        CameraManager.save_cameras(cams)
        return {"success": True, "dvrs_vinculados": dvr_vinc, "cameras_atualizadas": cam_upd}

    @classmethod
    def import_from_mongo(cls, cliente: Optional[str] = None, limit: Optional[int] = None,
                          dry_run: bool = False, loja: Optional[str] = None) -> Dict[str, Any]:
        """Descobre a frota pela easicash.lojas_v2 e cadastra os DVRs (idempotente por IP).
        Cada loja tem listas paralelas ip_dvr/modelo_dvr/porta_dvr/porta_rtsp_dvr/usuario_dvr/senha_dvr.
        dry_run=True só devolve o que SERIA importado, sem criar nada. cliente/limit restringem o escopo."""
        from core.mongo_store import iter_stores, enabled
        if not enabled():
            return {"success": False, "message": "MongoDB não configurado (MONGO_URI ou data/mongo_uri)."}
        existentes = {d.get("ip") for d in cls.load_dvrs()}
        plano, novos, lojas, ignoradas, total_pdvs = [], 0, 0, 0, 0
        for doc in iter_stores(cliente, limit, loja=loja):
            loja_nome = str(doc.get("loja") or "").strip()
            if loja_nome.startswith("["):   # loja desativada/com tag entre colchetes -> ignora (e o que está dentro)
                ignoradas += 1
                continue
            lojas += 1
            ips = doc.get("ip_dvr") or []
            marca = doc.get("modelo_dvr") or []
            hp = doc.get("porta_dvr") or []
            rp = doc.get("porta_rtsp_dvr") or []
            us = doc.get("usuario_dvr") or []
            pw = doc.get("senha_dvr") or []
            # listas podem ter 1 valor p/ vários DVRs (ex.: 2 ip_dvr, 1 senha) -> reusa o único/último valor
            def pega(lst, i, d=None):
                if not lst:
                    return d
                v = lst[i] if i < len(lst) else lst[-1]
                return v if v not in (None, "") else (lst[0] if lst[0] not in (None, "") else d)
            vpn = str(doc.get("ip_openvpn") or "").strip()
            tunnel_host = vpn if vpn and vpn != "0.0.0.0" else None   # máquina da loja na OpenVPN (para o túnel SSH)
            tunnel_mac = str(doc.get("mac") or "").strip() or None
            for i, ip in enumerate(ips):
                ip = str(ip or "").strip()
                if not ip or ip == "0.0.0.0":
                    continue
                item = {"ip": ip, "cliente": doc.get("cliente"), "loja": doc.get("loja"),
                        "brand": str(pega(marca, i, "intelbras")).lower(), "ja_existe": ip in existentes}
                plano.append(item)
                if ip in existentes or dry_run:
                    continue
                nova = cls.add_dvr({
                    "ip": ip, "brand": item["brand"],
                    "http_port": int(pega(hp, i, 80)), "port": int(pega(rp, i, 554)),
                    "username": str(pega(us, i, "admin")), "password": str(pega(pw, i, "")),
                    "name": str(doc.get("loja") or f"DVR {ip}"),
                    "tunnel_host": tunnel_host, "tunnel_mac": tunnel_mac,
                }, auto_create_pdvs=True)  # detecta canais ativos (pelo túnel SSH) e nomeia pelos nomes reais
                total_pdvs += nova.get("created_pdvs_count", 0)
                existentes.add(ip)
                novos += 1
        if not dry_run and novos:
            cls.sync_stores()  # garante cliente/loja
        return {"success": True, "dry_run": dry_run, "lojas": lojas, "ignoradas_colchete": ignoradas,
                "dvrs_no_mongo": len(plano), "ja_cadastrados": sum(1 for p in plano if p["ja_existe"]),
                "novos": novos if not dry_run else sum(1 for p in plano if not p["ja_existe"]),
                "total_pdvs": total_pdvs,
                "plano": plano if dry_run else None}

    @classmethod
    def add_dvr(cls, data: Dict[str, Any], auto_create_pdvs: bool = True) -> Dict[str, Any]:
        """Adiciona um novo DVR e opcionalmente gera os PDVs para cada canal."""
        from core.camera_manager import CameraManager

        dvrs = cls.load_dvrs()
        new_id = data.get("id") or f"dvr-{str(uuid.uuid4())[:8]}"

        brand = (data.get("brand") or "intelbras").strip().lower()
        ip = (data.get("ip") or "").strip()
        port = int(data.get("port") or 554)
        http_port = int(data.get("http_port") or 80)
        username = (data.get("username") or "admin").strip()
        password = data.get("password") or ""
        name = (data.get("name") or f"DVR {ip}").strip()
        channel_count = int(data.get("channel_count") or 16)
        tunnel_host = data.get("tunnel_host") or None   # máquina da loja (ip_openvpn) p/ o túnel SSH até o DVR
        tunnel_mac = data.get("tunnel_mac") or None

        # Decide os PDVs pelos canais ATIVOS do próprio DVR (nome != '---').
        # Via túnel o ip_openvpn é dinâmico e falha às vezes -> tenta algumas vezes antes de desistir.
        titulos = {}
        if auto_create_pdvs:
            for _ in range(3 if tunnel_mac else 1):
                titulos = cls.fetch_channel_titles(ip, http_port, username, password, brand,
                                                   tunnel_host=tunnel_host, tunnel_mac=tunnel_mac)
                if titulos:
                    break
                time.sleep(1.5)
        # Com túnel e detecção falhando: NÃO cria 16 genéricos (evita PDVs repetidos); deixa 0 p/ re-puxar.
        # Sem túnel (cadastro manual): cai no número informado.
        canais = sorted(titulos) if titulos else ([] if tunnel_mac else list(range(1, channel_count + 1)))
        if titulos:
            channel_count = len(canais)

        # Testa conexão preliminar
        test_res = cls.test_connection(ip, port, http_port, timeout=1.5)
        status = "ONLINE" if test_res["success"] else "OFFLINE"

        dvr_record = {
            "id": new_id,
            "name": name,
            "brand": brand,
            "ip": ip,
            "port": port,
            "http_port": http_port,
            "username": username,
            "password": password,
            "channel_count": channel_count,
            "status": status,
            "tunnel_host": tunnel_host,
            "tunnel_mac": tunnel_mac,
            "last_ping": time.strftime("%Y-%m-%d %H:%M:%S"),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }

        dvrs.append(dvr_record)
        cls.save_dvrs(dvrs)

        created_pdvs = []
        if auto_create_pdvs and canais:
            for ch in canais:
                pdv_name = f"{titulos.get(ch, f'PDV {ch:02d}')} — {name}"  # nome real do canal no DVR quando houver
                cam_data = {
                    "name": pdv_name,
                    "pos_code": ch,
                    "dvr_id": new_id,
                    "dvr_name": name,
                    "channel": ch,
                    "brand": brand,
                    "ip": ip,
                    "port": port,
                    "http_port": http_port,
                    "username": username,
                    "password": password,
                    "tunnel_host": tunnel_host,
                    "tunnel_mac": tunnel_mac,
                    "substream": True,
                    "is_simulation": False
                }
                cam = CameraManager.add_camera(cam_data)
                created_pdvs.append(cam)

        if created_pdvs:
            cls.sync_stores()  # liga o novo DVR/PDVs à loja do Mongo (cliente/loja)

        return {
            "success": True,
            "dvr": dvr_record,
            "created_pdvs_count": len(created_pdvs),
            "detected_active_channels": len(titulos),  # 0 = DVR não respondeu, usou a quantidade informada
            "test_connection": test_res
        }

    @classmethod
    def delete_dvr(cls, dvr_id: str, delete_associated_pdvs: bool = True) -> bool:
        """Remove um DVR e opcionalmente suas câmeras vinculadas."""
        from core.camera_manager import CameraManager

        dvrs = cls.load_dvrs()
        filtered = [d for d in dvrs if d.get("id") != dvr_id]
        if len(filtered) < len(dvrs):
            cls.save_dvrs(filtered)

            if delete_associated_pdvs:
                cameras = CameraManager.load_cameras()
                cams_to_keep = []
                for c in cameras:
                    if c.get("dvr_id") == dvr_id:
                        CameraManager.delete_camera(c["id"])
                    else:
                        cams_to_keep.append(c)
            return True
        return False

    @classmethod
    def import_user_media(cls) -> Dict[str, Any]:
        """
        Escaneia as pastas de teste 'frames/' e 'videos/' fornecidas pelo usuário,
        cadastra os DVRs e PDVs correspondentes e define os frames de calibração.
        """
        from core.camera_manager import CameraManager, REFERENCES_DIR, CURRENT_TEST_FRAMES_DIR, SNAPSHOTS_DIR, write_cv2_image, read_cv2_image

        if not os.path.exists(FRAMES_DIR):
            return {"success": False, "message": f"Pasta {FRAMES_DIR} não encontrada"}

        frame_files = [f for f in os.listdir(FRAMES_DIR) if f.lower().endswith((".jpg", ".jpeg", ".png"))]
        video_files = [f for f in os.listdir(VIDEOS_DIR) if f.lower().endswith((".avi", ".mp4", ".mkv"))] if os.path.exists(VIDEOS_DIR) else []

        # Padrão: 2026-07-29-POS-101-DVR-1.jpg ou 2026-08-18-POS-8001-DVR-1.jpg
        pattern = re.compile(r"POS-(\d+)-DVR-(\d+)", re.IGNORECASE)

        created_count = 0
        existing_cameras = {c.get("pos_code"): c for c in CameraManager.load_cameras()}

        # Garante DVR de teste para os frames
        dvr_id = "dvr-material-teste"
        dvrs = cls.load_dvrs()
        if not any(d["id"] == dvr_id for d in dvrs):
            dvrs.append({
                "id": dvr_id,
                "name": "DVR Testes — Material de Loja",
                "brand": "intelbras",
                "ip": "10.59.151.131",
                "port": 554,
                "http_port": 80,
                "username": "admin",
                "password": "",
                "channel_count": 18,
                "status": "ONLINE",
                "last_ping": time.strftime("%Y-%m-%d %H:%M:%S"),
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
            })
            cls.save_dvrs(dvrs)

        for frame_file in sorted(frame_files):
            match = pattern.search(frame_file)
            if not match:
                continue

            pos_code = int(match.group(1))
            dvr_ch = int(match.group(2))

            # Procura vídeo correspondente em videos/
            matched_video = None
            for vf in video_files:
                if f"POS-{pos_code}-DVR-{dvr_ch}" in vf:
                    matched_video = os.path.join(VIDEOS_DIR, vf)
                    break

            frame_full_path = os.path.join(FRAMES_DIR, frame_file)
            frame_img = read_cv2_image(frame_full_path)

            cam_id = f"pdv-{pos_code}"
            cam_name = f"PDV {pos_code} (Canal {dvr_ch})"

            cam_data = {
                "id": cam_id,
                "name": cam_name,
                "pos_code": pos_code,
                "dvr_id": dvr_id,
                "dvr_name": "DVR Testes — Material de Loja",
                "channel": dvr_ch,
                "brand": "intelbras",
                "ip": "10.59.151.131",
                "port": 554,
                "username": "admin",
                "password": "",
                "custom_url": matched_video or frame_full_path,
                "substream": True,
                "is_simulation": False
            }

            cam = CameraManager.add_camera(cam_data)

            # Salva o frame como referência e snapshot inicial
            if frame_img is not None:
                write_cv2_image(os.path.join(REFERENCES_DIR, f"{cam['id']}.jpg"), frame_img)
                write_cv2_image(os.path.join(SNAPSHOTS_DIR, f"{cam['id']}.jpg"), frame_img)
                write_cv2_image(os.path.join(CURRENT_TEST_FRAMES_DIR, f"{cam['id']}.jpg"), frame_img)
                CameraManager.update_camera(cam["id"], {"has_reference": True})

            created_count += 1

        return {
            "success": True,
            "imported_count": created_count,
            "message": f"{created_count} PDVs importados com fotos e vídeos reais com sucesso!"
        }

    @classmethod
    def sync_dvr(cls, dvr_id: str) -> Dict[str, Any]:
        """
        Puxa automaticamente as imagens ao vivo de todos os canais do DVR e executa a auditoria de alinhamento.
        """
        from core.camera_manager import CameraManager

        dvr = cls.get_dvr(dvr_id)
        if not dvr:
            return {"success": False, "message": "DVR não encontrado"}

        cameras = [c for c in CameraManager.load_cameras() if c.get("dvr_id") == dvr_id]
        if not cameras:
            return {"success": False, "message": "Nenhum PDV vinculado a este DVR"}

        results = []
        for cam in cameras:
            res = CameraManager.analyze_camera(cam["id"], pull_from_dvr=True)
            results.append(res)

        aligned = sum(1 for r in results if r.get("status") == "ALIGNED")
        warning = sum(1 for r in results if r.get("status") == "MILD_DRIFT")
        crooked = sum(1 for r in results if r.get("status") == "CROOKED")
        offline = sum(1 for r in results if r.get("status") in ("OFFLINE", "OBSTRUCTED"))

        # Atualiza status do DVR
        dvr["last_sync"] = time.strftime("%Y-%m-%d %H:%M:%S")
        dvr["status"] = "ONLINE" if (aligned + warning + crooked) > 0 else "OFFLINE"
        dvrs = cls.load_dvrs()
        for idx, d in enumerate(dvrs):
            if d.get("id") == dvr_id:
                dvrs[idx] = dvr
                break
        cls.save_dvrs(dvrs)

        return {
            "success": True,
            "dvr_id": dvr_id,
            "dvr_name": dvr["name"],
            "total_channels": len(cameras),
            "aligned": aligned,
            "warning": warning,
            "crooked": crooked,
            "offline": offline,
            "results": results
        }

