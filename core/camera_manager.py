"""
Gerenciador de Câmeras, persistência JSON, importação CSV e calibração de referências.
"""
import os
import json
import csv
import io
import time
import cv2
import uuid
import numpy as np
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Optional

from core.rtsp_builder import build_rtsp_url, mask_rtsp_url
from core.vision_engine import VisionEngine

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
TEST_IMAGES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "test_images")
CAMERAS_FILE = os.path.join(DATA_DIR, "cameras.json")
REFERENCES_DIR = os.path.join(DATA_DIR, "references")
SNAPSHOTS_DIR = os.path.join(DATA_DIR, "snapshots")
CURRENT_TEST_FRAMES_DIR = os.path.join(DATA_DIR, "current_test_frames")

os.makedirs(REFERENCES_DIR, exist_ok=True)
os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
os.makedirs(CURRENT_TEST_FRAMES_DIR, exist_ok=True)

def read_cv2_image(path: str) -> Optional[np.ndarray]:
    """Lê imagem usando numpy/imdecode compatível com acentos e caracteres especiais no Windows."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            return cv2.imdecode(np.frombuffer(f.read(), np.uint8), cv2.IMREAD_COLOR)
    except Exception:
        return None

def write_cv2_image(path: str, img: np.ndarray) -> bool:
    """Grava imagem usando numpy/imencode compatível com acentos e caracteres especiais no Windows."""
    try:
        ok, buf = cv2.imencode(".jpg", img)
        if ok:
            with open(path, "wb") as f:
                f.write(buf)
            return True
    except Exception:
        pass
    return False

class CameraManager:
    _lock = threading.RLock()

    @classmethod
    def load_cameras(cls) -> List[Dict[str, Any]]:
        with cls._lock:
            bak_file = CAMERAS_FILE + ".bak"
            if not os.path.exists(CAMERAS_FILE) or os.path.getsize(CAMERAS_FILE) == 0:
                if os.path.exists(bak_file) and os.path.getsize(bak_file) > 0:
                    try:
                        with open(bak_file, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            if data:
                                cls.save_cameras(data)
                                return data
                    except Exception:
                        pass
                
                from core.dvr_manager import DVRManager
                imported = DVRManager.import_user_media()  # devolve dict {"success": ...}
                if isinstance(imported, dict) and imported.get("success") and \
                        os.path.exists(CAMERAS_FILE) and os.path.getsize(CAMERAS_FILE) > 0:
                    with open(CAMERAS_FILE, "r", encoding="utf-8") as f:
                        return json.load(f)

                default_cameras = cls._get_default_cameras()
                cls.save_cameras(default_cameras)
                return default_cameras
            
            try:
                with open(CAMERAS_FILE, "r", encoding="utf-8") as f:
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
    def save_cameras(cls, cameras: List[Dict[str, Any]]):
        with cls._lock:
            tmp_file = CAMERAS_FILE + ".tmp"
            bak_file = CAMERAS_FILE + ".bak"
            try:
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(cameras, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_file, CAMERAS_FILE)
                if cameras:
                    with open(bak_file, "w", encoding="utf-8") as bf:
                        json.dump(cameras, bf, ensure_ascii=False, indent=2)
            except Exception:
                with open(CAMERAS_FILE, "w", encoding="utf-8") as f:
                    json.dump(cameras, f, ensure_ascii=False, indent=2)

    @classmethod
    def get_camera(cls, camera_id: str) -> Optional[Dict[str, Any]]:
        cameras = cls.load_cameras()
        for cam in cameras:
            if cam.get("id") == camera_id:
                return cam
        return None

    @classmethod
    def add_camera(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        cameras = cls.load_cameras()
        new_id = data.get("id") or str(uuid.uuid4())[:8]
        
        # Se já existe, atualiza em vez de duplicar
        existing = cls.get_camera(new_id)
        if existing:
            cls.update_camera(new_id, data)
            return cls.get_camera(new_id)

        brand = data.get("brand", "intelbras")
        ip = data.get("ip", "")
        port = int(data.get("port") or 554)
        username = data.get("username", "admin")
        password = data.get("password", "")
        channel = int(data.get("channel") or 1)
        substream = bool(data.get("substream", True))
        custom_url = data.get("custom_url", "").strip()

        rtsp_url = build_rtsp_url(
            brand=brand, ip=ip, port=port, username=username,
            password=password, channel=channel, substream=substream,
            custom_url=custom_url
        )

        camera_record = {
            "id": new_id,
            "name": data.get("name", f"PDV {len(cameras) + 1}"),
            "pos_code": data.get("pos_code", len(cameras) + 1),
            "dvr_id": data.get("dvr_id", ""),
            "dvr_name": data.get("dvr_name", ""),
            "cliente": data.get("cliente", ""),   # preenchido pelo MongoDB (easicash.lojas_v2) via DVRManager.sync_stores
            "loja": data.get("loja", ""),
            "tunnel_host": data.get("tunnel_host") or "",   # ip_openvpn da loja: túnel SSH sob demanda até o DVR
            "tunnel_mac": data.get("tunnel_mac") or "",
            "brand": brand,
            "ip": ip,
            "port": port,
            "http_port": int(data.get("http_port") or 80),
            "username": username,
            "password": password,
            "channel": channel,
            "substream": substream,
            "custom_url": custom_url,
            "rtsp_url": rtsp_url,
            "masked_url": mask_rtsp_url(rtsp_url),
            "has_reference": False,
            "last_check_status": "PENDING",
            "last_rotation_deg": 0.0,
            "last_translation_px": 0.0,
            "last_confidence": 0.0,
            "last_check_method": "Não verificado",
            "last_checked_at": None,
            "is_simulation": bool(data.get("is_simulation", False)),
            "sim_angle": float(data.get("sim_angle", 0.0))
        }

        cameras.append(camera_record)
        cls.save_cameras(cameras)
        return camera_record

    @classmethod
    def update_camera(cls, camera_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        cameras = cls.load_cameras()
        for idx, cam in enumerate(cameras):
            if cam.get("id") == camera_id:
                for k in ["name", "pos_code", "dvr_id", "dvr_name", "brand", "ip", "port", "http_port", "username", "password", "channel", "substream", "custom_url", "is_simulation", "sim_angle", "tunnel_host", "tunnel_mac", "cliente", "loja"]:
                    if k in data:
                        cam[k] = data[k]

                cam["rtsp_url"] = build_rtsp_url(
                    brand=cam.get("brand"), ip=cam.get("ip"), port=int(cam.get("port") or 554),
                    username=cam.get("username"), password=cam.get("password"), channel=int(cam.get("channel") or 1),
                    substream=bool(cam.get("substream", True)), custom_url=cam.get("custom_url")
                )
                cam["masked_url"] = mask_rtsp_url(cam["rtsp_url"])
                cameras[idx] = cam
                cls.save_cameras(cameras)
                return cam
        return None

    @classmethod
    def delete_camera(cls, camera_id: str) -> bool:
        cameras = cls.load_cameras()
        filtered = [c for c in cameras if c.get("id") != camera_id]
        if len(filtered) < len(cameras):
            cls.save_cameras(filtered)
            ref_path = os.path.join(REFERENCES_DIR, f"{camera_id}.jpg")
            snap_path = os.path.join(SNAPSHOTS_DIR, f"{camera_id}.jpg")
            custom_frame = os.path.join(CURRENT_TEST_FRAMES_DIR, f"{camera_id}.jpg")
            for p in (ref_path, snap_path, custom_frame):
                if os.path.exists(p):
                    try: os.remove(p)
                    except: pass
            return True
        return False

    @classmethod
    def calibrate_camera(cls, camera_id: str) -> Dict[str, Any]:
        """Define o frame atual como Referência Oficial."""
        camera = cls.get_camera(camera_id)
        if not camera:
            return {"success": False, "message": "Câmera não encontrada"}

        custom_path = os.path.join(CURRENT_TEST_FRAMES_DIR, f"{camera_id}.jpg")
        if os.path.exists(custom_path):
            frame = read_cv2_image(custom_path)
            ok = frame is not None
        elif camera.get("is_simulation"):
            sample_ref = os.path.join(TEST_IMAGES_DIR, "pdv_referencia_alinhada.jpg")
            frame = read_cv2_image(sample_ref)
            if frame is None:
                frame = VisionEngine.generate_synthetic_checkout_frame(tilt_angle=0.0)
            ok = True
        else:
            ok, frame, msg = VisionEngine.capture_frame(camera["rtsp_url"])
            if not ok or frame is None:
                return {"success": False, "message": f"Falha ao capturar frame: {msg}"}

        ref_path = os.path.join(REFERENCES_DIR, f"{camera_id}.jpg")
        write_cv2_image(ref_path, frame)

        cameras = cls.load_cameras()
        for c in cameras:
            if c.get("id") == camera_id:
                c["has_reference"] = True
                c["level_samples"] = []  # nova posição: leituras antigas do nível não valem mais
                break
        cls.save_cameras(cameras)

        level = VisionEngine.detect_counter_angle(frame)
        if level is not None and abs(level) > VisionEngine.LEVEL_TOLERANCE:
            return {"success": True, "level_deg": round(level, 2),
                    "message": f"Referência salva, mas ATENÇÃO: o balcão parece inclinado {level:+.1f}°. "
                               f"Se não for proposital, ajuste a câmera e salve de novo."}
        return {"success": True, "level_deg": None if level is None else round(level, 2),
                "message": "Frame de referência calibrado com sucesso!"}

    @classmethod
    def analyze_camera(cls, camera_id: str, pull_from_dvr: bool = False) -> Dict[str, Any]:
        """
        Executa a análise de alinhamento em uma câmera individual.
        Se pull_from_dvr=True ou se a câmera tiver stream ativo, puxa a imagem fresca do DVR.
        """
        camera = cls.get_camera(camera_id)
        if not camera:
            return {"success": False, "message": "Câmera não encontrada"}

        # Herda parâmetros do DVR associado se disponíveis
        if camera.get("dvr_id"):
            try:
                from core.dvr_manager import DVRManager
                dvr = DVRManager.get_dvr(camera.get("dvr_id"))
                if dvr:
                    for field in ("ip", "port", "http_port", "username", "password", "brand"):
                        if not camera.get(field) and dvr.get(field):
                            camera[field] = dvr[field]
            except Exception:
                pass

        ref_path = os.path.join(REFERENCES_DIR, f"{camera_id}.jpg")
        ref_frame = read_cv2_image(ref_path)

        custom_path = os.path.join(CURRENT_TEST_FRAMES_DIR, f"{camera_id}.jpg")
        curr_frame = None
        capture_note = ""

        # 1. Se foi solicitada captura direta/ao vivo do DVR
        if pull_from_dvr:
            # 1a. Tenta via Snapshot HTTP da fabricante (Intelbras, Dahua, Hikvision)
            if camera.get("ip"):
                snap_ok, snap_img, snap_msg = VisionEngine.capture_dvr_snapshot(
                    ip=camera.get("ip"),
                    http_port=int(camera.get("http_port") or 80),
                    username=camera.get("username") or "admin",
                    password=camera.get("password") or "",
                    channel=int(camera.get("channel") or 1),
                    brand=camera.get("brand") or "intelbras",
                    timeout_s=4.0,
                    tunnel_host=camera.get("tunnel_host") or None,
                    tunnel_mac=camera.get("tunnel_mac") or None
                )
                if snap_ok and snap_img is not None:
                    curr_frame = snap_img
                    write_cv2_image(custom_path, curr_frame)
                    capture_note = "Frame atualizado via HTTP Snapshot do DVR"
                else:
                    capture_note = f"Falha HTTP: {snap_msg}"

            # 1b. Fallback para RTSP do DVR se snapshot não capturou
            if curr_frame is None and camera.get("rtsp_url") and not camera.get("custom_url"):
                rtsp_ok, rtsp_img, rtsp_msg = VisionEngine.capture_frame(
                    camera.get("rtsp_url", ""),
                    camera_meta=camera,
                    timeout_s=3.0
                )
                if rtsp_ok and rtsp_img is not None:
                    curr_frame = rtsp_img
                    write_cv2_image(custom_path, curr_frame)
                    capture_note = "Frame capturado via RTSP ao vivo do DVR"
                else:
                    capture_note += f" | Falha RTSP: {rtsp_msg}"

        # 2. Se temos frame atual salvo em disco (seja de teste, upload, cenário ou cache)
        if curr_frame is None and os.path.exists(custom_path):
            curr_frame = read_cv2_image(custom_path)
            if curr_frame is not None:
                capture_note = "Usando frame atual em cache/teste"

        # 3. Se não temos frame em disco, verificar arquivo de vídeo/imagem local vinculado
        if curr_frame is None and camera.get("custom_url"):
            ok, vid_frame, vid_msg = VisionEngine.capture_frame(
                camera.get("custom_url"),
                timeout_s=3.0
            )
            if ok and vid_frame is not None:
                curr_frame = vid_frame
                write_cv2_image(custom_path, curr_frame)
                capture_note = "Frame de vídeo/mídia local carregado"

        # 4. Modo Simulação
        if curr_frame is None and camera.get("is_simulation"):
            sim_tilt = float(camera.get("sim_angle", 0.0))
            sample_ref = os.path.join(TEST_IMAGES_DIR, "pdv_referencia_alinhada.jpg")
            base = read_cv2_image(sample_ref)
            if base is not None:
                h, w = base.shape[:2]
                M = cv2.getRotationMatrix2D((w // 2, h // 2), sim_tilt, 1.0)
                curr_frame = cv2.warpAffine(base, M, (w, h), borderMode=cv2.BORDER_REFLECT)
            else:
                curr_frame = VisionEngine.generate_synthetic_checkout_frame(tilt_angle=sim_tilt)
            capture_note = "Simulação sintética ativa"

        # 5. Se nenhuma fonte forneceu imagem, marcar como Offline
        if curr_frame is None:
            err_msg = capture_note or "Não foi possível capturar a imagem do DVR ou das mídias locais."
            cls._update_camera_status(camera_id, {
                "last_check_status": "OFFLINE",
                "last_check_method": f"Offline: {err_msg}",
                "last_checked_at": time.strftime("%Y-%m-%d %H:%M:%S")
            })
            return {
                "success": False,
                "camera_id": camera_id,
                "camera_name": camera.get("name"),
                "status": "OFFLINE",
                "status_text": "Sem Sinal / Offline",
                "message": err_msg
            }

        try:
            # 2 frames extras decorrelacionados (~3s) via snapshot HTTP: reaproveitados p/ a média do corredor/PDV
            # (corta o ruído de frame único) e p/ o consenso de nível. RTSP/cache: sem extras, usa só o frame atual.
            extra_imgs = []
            if "HTTP Snapshot" in capture_note and not camera.get("is_simulation"):
                for _ in range(2):
                    time.sleep(1.5)
                    ok, img, _ = VisionEngine.capture_dvr_snapshot(
                        ip=camera.get("ip"), http_port=int(camera.get("http_port") or 80),
                        username=camera.get("username") or "admin", password=camera.get("password") or "",
                        channel=int(camera.get("channel") or 1), brand=camera.get("brand") or "intelbras",
                        tunnel_host=camera.get("tunnel_host") or None, tunnel_mac=camera.get("tunnel_mac") or None)
                    if ok and img is not None:
                        extra_imgs.append(img)

            # Executa motor de visão computacional (ORB vs referência + nível do balcão + obstrução/foco)
            diag = VisionEngine.analyze_camera(curr_frame, ref_frame, aisle_extra_frames=extra_imgs)

            # Sem referência: decide pelo nível do balcão, mas só com 3 leituras concordando (±0.4°). Uma leitura
            # isolada oscila graus quando há gente/produtos se mexendo; as leituras se acumulam entre verificações.
            extra = {}
            if (ref_frame is None and not camera.get("is_simulation")
                    and diag["status"] not in ("OBSTRUCTED", "BLURRY", "PENDING")):
                levels = [diag["level_angle_deg"]] + [lv for lv in
                         (VisionEngine.detect_counter_angle(i) for i in extra_imgs) if lv is not None]
                samples = ((camera.get("level_samples") or []) + levels)[-6:]
                # maior grupo concordante; reversed = em empate vence o mais recente (ex.: depois do ajuste)
                group = max(([s for s in samples if abs(s - c) <= 0.4] for c in reversed(samples)), key=len)
                level = float(np.median(group))
                extra["level_samples"] = samples
                if len(group) < 3:
                    diag.update(status="PENDING", status_text=f"Medindo Nível ({len(group)}/3)",
                                status_color="#64748b", severity="ATENÇÃO", is_alignment_ok=False,
                                rotation_deg=round(level, 2), translation_px=0.0,
                                method_used="Nível do Balcão (sem referência)",
                                diag_reason="As leituras do nível do balcão ainda não concordam (movimento na cena). "
                                            "Nova tentativa na próxima verificação.")
                elif abs(level) <= VisionEngine.LEVEL_TOLERANCE and (diag.get("aisle") or {}).get("verdict") in ("insufficient", "doubt"):
                    # Nivelada, mas o corredor falta ou é duvidoso: não vira referência (a posição correta precisa
                    # mostrar o corredor com certeza)
                    diag.update(rotation_deg=round(level, 2), translation_px=0.0)
                    if diag["aisle"]["verdict"] == "doubt" and diag["status"] != "PENDING":
                        diag.update(status="PENDING", status_text="Corredor no Limite", status_color="#64748b",
                                    severity="ATENÇÃO", is_alignment_ok=False,
                                    diag_reason=f"Balcão nivelado ({level:+.2f}°), mas a câmera mostra ~"
                                                f"{diag['aisle']['aisle_pct']:.0f}% de corredor, perto do mínimo de "
                                                f"{VisionEngine.AISLE_MIN_PCT:.0f}%. Confira antes de usar como referência.")
                elif abs(level) <= VisionEngine.LEVEL_TOLERANCE:
                    write_cv2_image(ref_path, curr_frame)
                    ref_frame = curr_frame
                    extra["level_samples"] = []
                    diag.update(status="ALIGNED", status_text="Alinhada (100% OK)", status_color="#10b981",
                                severity="OK", is_alignment_ok=True, rotation_deg=round(level, 2), translation_px=0.0,
                                method_used="Nível do Balcão OK - Referência Automática",
                                diag_reason=f"Balcão nivelado ({level:+.2f}°, {len(group)} leituras concordando). Posição "
                                            f"salva como referência: a partir de agora qualquer giro acima de "
                                            f"{VisionEngine.TILT_TOLERANCE_ALIGNED}° ou deslocamento acima de "
                                            f"{VisionEngine.SHIFT_TOLERANCE_PX:.0f}px gera alerta.")
                else:
                    crooked = abs(level) > VisionEngine.LEVEL_WARNING
                    diag.update(status="CROOKED" if crooked else "MILD_DRIFT",
                                status_text="Torta / Crítica" if crooked else "Desvio Leve",
                                status_color="#ef4444" if crooked else "#f59e0b",
                                severity="CRÍTICO" if crooked else "ATENÇÃO", is_alignment_ok=False,
                                rotation_deg=round(level, 2), translation_px=0.0,
                                method_used="Nível do Balcão (sem referência)",
                                diag_reason=f"Balcão inclinado {level:+.2f}° ({len(group)} leituras concordando; alinhada "
                                            f"até {VisionEngine.LEVEL_TOLERANCE}°). Ajuste a câmera pelo link do técnico; "
                                            f"quando ficar nivelada, a referência é salva automaticamente.")
                diag["annotated_frame"] = VisionEngine.draw_diagnostic_overlay(
                    curr_frame, diag["rotation_deg"], diag["status"], diag["status_text"], diag["status_color"],
                    diag["method_used"], diag["confidence"], diag["translation_px"], diag["focus_score"], diag["aisle"])

            snap_path = os.path.join(SNAPSHOTS_DIR, f"{camera_id}.jpg")
            write_cv2_image(snap_path, diag["annotated_frame"])

            method_display = diag["method_used"]
            if capture_note and "Frame atualizado" in capture_note:
                method_display = f"{method_display} (DVR ao vivo)"

            cls._update_camera_status(camera_id, {
                "last_check_status": diag["status"],
                "last_rotation_deg": diag["rotation_deg"],
                "last_translation_px": diag["translation_px"],
                "last_confidence": diag["confidence"],
                "last_check_method": method_display,
                "last_status_text": diag.get("status_text"),
                "last_reason": diag.get("diag_reason"),
                "last_aisle_pct": diag.get("aisle_pct"),
                "last_aisle_side": diag.get("aisle_side"),
                "has_reference": bool(ref_frame is not None),
                "last_checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                **extra
            })

            return {
                "success": True,
                "camera_id": camera_id,
                "camera_name": camera.get("name"),
                "capture_note": capture_note,
                **{k: v for k, v in diag.items() if k not in ("annotated_frame", "aisle")}
            }
        except Exception as e:
            cls._update_camera_status(camera_id, {
                "last_check_status": "OFFLINE",
                "last_check_method": f"Erro de processamento: {str(e)}",
                "last_checked_at": time.strftime("%Y-%m-%d %H:%M:%S")
            })
            return {
                "success": False,
                "camera_id": camera_id,
                "camera_name": camera.get("name"),
                "status": "OFFLINE",
                "status_text": "Erro no Diagnóstico",
                "message": f"Erro interno na análise visual: {str(e)}"
            }

    @classmethod
    def apply_test_scenario(cls, camera_id: str, scenario: str) -> Dict[str, Any]:
        """
        Aplica um cenário de teste realista imediato à câmera especificada:
        - 'alinhada': Imagem em ângulo normal (0.0°)
        - 'desvio_leve': Imagem com rotação de +3.2°
        - 'torta': Imagem com inclinação de -7.8°
        - 'critica': Imagem com inclinação severa de +12.5°
        """
        scenario_angles = {"alinhada": 0.0, "desvio_leve": 3.2, "torta": -7.8, "critica": 12.5}
        angle = scenario_angles.get(scenario, 0.0)

        # Garante que a referência calibrada está presente
        ref_path = os.path.join(REFERENCES_DIR, f"{camera_id}.jpg")
        if not os.path.exists(ref_path):
            ref_base = read_cv2_image(os.path.join(TEST_IMAGES_DIR, "pdv_referencia_alinhada.jpg"))
            if ref_base is not None:
                write_cv2_image(ref_path, ref_base)

        ref_img = read_cv2_image(ref_path)
        if ref_img is None:
            return {"success": False, "message": "Referência da câmera não encontrada"}

        # Gira a própria referência do PDV: comparar com foto de outra cena não mede nada
        h, w = ref_img.shape[:2]
        M = cv2.getRotationMatrix2D((w / 2, h / 2), -angle, 1.0)
        test_img = cv2.warpAffine(ref_img, M, (w, h), borderMode=cv2.BORDER_REPLICATE)

        custom_curr_path = os.path.join(CURRENT_TEST_FRAMES_DIR, f"{camera_id}.jpg")
        write_cv2_image(custom_curr_path, test_img)

        return cls.analyze_camera(camera_id)

    @classmethod
    def upload_test_image(cls, camera_id: str, file_bytes: bytes, as_reference: bool = False) -> Dict[str, Any]:
        """Recebe uma imagem enviada pelo usuário para teste de alinhamento ou calibração."""
        img = cv2.imdecode(np.frombuffer(file_bytes, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return {"success": False, "message": "Formato de imagem inválido"}

        if as_reference:
            ref_path = os.path.join(REFERENCES_DIR, f"{camera_id}.jpg")
            write_cv2_image(ref_path, img)
            cls._update_camera_status(camera_id, {"has_reference": True})
            return {"success": True, "message": "Imagem de referência enviada e salva!"}
        else:
            curr_path = os.path.join(CURRENT_TEST_FRAMES_DIR, f"{camera_id}.jpg")
            write_cv2_image(curr_path, img)
            return cls.analyze_camera(camera_id)

    @classmethod
    def analyze_all(cls, pull_from_dvr: bool = False) -> List[Dict[str, Any]]:
        cameras = cls.load_cameras()
        results = []
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(cls.analyze_camera, cam["id"], pull_from_dvr) for cam in cameras]
            for future in futures:
                try:
                    results.append(future.result())
                except Exception as e:
                    results.append({"success": False, "message": str(e)})
        return results

    @classmethod
    def _update_camera_status(cls, camera_id: str, updates: Dict[str, Any]):
        with cls._lock:
            cameras = cls.load_cameras()
            for c in cameras:
                if c.get("id") == camera_id:
                    c.update(updates)
                    break
            cls.save_cameras(cameras)

    @classmethod
    def import_csv(cls, csv_text: str) -> int:
        reader = csv.DictReader(io.StringIO(csv_text))
        imported_count = 0
        for row in reader:
            if not row.get("nome") and not row.get("ip"):
                continue
            data = {
                "name": row.get("nome", "").strip() or f"PDV Importado {imported_count + 1}",
                "ip": row.get("ip", "").strip(),
                "port": int(row.get("porta") or 554),
                "username": row.get("usuario", "admin").strip(),
                "password": row.get("senha", "").strip(),
                "brand": row.get("marca", "intelbras").strip().lower(),
                "channel": int(row.get("canal") or 1),
                "custom_url": row.get("rtsp_url", "").strip(),
                "is_simulation": row.get("simulacao", "0").strip() in ("1", "true", "True")
            }
            cls.add_camera(data)
            imported_count += 1
        return imported_count

    @classmethod
    def export_csv(cls) -> str:
        cameras = cls.load_cameras()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID", "PDV", "Marca", "IP", "Porta", "Status", "Inclinacao (Graus)", "Deslocamento (px)", "Ultima Verificacao"])
        for c in cameras:
            writer.writerow([
                c.get("id"),
                c.get("name"),
                c.get("brand"),
                c.get("ip"),
                c.get("port"),
                c.get("last_check_status"),
                c.get("last_rotation_deg"),
                c.get("last_translation_px"),
                c.get("last_checked_at")
            ])
        return output.getvalue()

    @classmethod
    def _get_default_cameras(cls) -> List[Dict[str, Any]]:
        cams = [
            {
                "id": "pdv-01",
                "name": "PDV 01 - Caixa Rápido",
                "pos_code": 1,
                "brand": "intelbras",
                "ip": "192.168.1.101",
                "port": 554,
                "username": "admin",
                "password": "password123",
                "channel": 1,
                "substream": True,
                "custom_url": "",
                "rtsp_url": "rtsp://admin:password123@192.168.1.101:554/cam/realmonitor?channel=1&subtype=1",
                "masked_url": "rtsp://admin:****@192.168.1.101:554/cam/realmonitor?channel=1&subtype=1",
                "has_reference": True,
                "last_check_status": "ALIGNED",
                "last_rotation_deg": 0.0,
                "last_translation_px": 0.0,
                "last_confidence": 98.0,
                "last_check_method": "Comparação com Referência Calibrada",
                "last_checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "is_simulation": True,
                "sim_angle": 0.0
            },
            {
                "id": "pdv-02",
                "name": "PDV 02 - Caixa Central",
                "pos_code": 2,
                "brand": "hikvision",
                "ip": "192.168.1.102",
                "port": 554,
                "username": "admin",
                "password": "password123",
                "channel": 1,
                "substream": True,
                "custom_url": "",
                "rtsp_url": "rtsp://admin:password123@192.168.1.102:554/Streaming/Channels/102",
                "masked_url": "rtsp://admin:****@192.168.1.102:554/Streaming/Channels/102",
                "has_reference": True,
                "last_check_status": "MILD_DRIFT",
                "last_rotation_deg": 3.2,
                "last_translation_px": 8.0,
                "last_confidence": 92.5,
                "last_check_method": "Comparação com Referência Calibrada",
                "last_checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "is_simulation": True,
                "sim_angle": 3.2
            },
            {
                "id": "pdv-03",
                "name": "PDV 03 - Atendimento Preferencial",
                "pos_code": 3,
                "brand": "dahua",
                "ip": "192.168.1.103",
                "port": 554,
                "username": "admin",
                "password": "password123",
                "channel": 1,
                "substream": True,
                "custom_url": "",
                "rtsp_url": "rtsp://admin:password123@192.168.1.103:554/cam/realmonitor?channel=1&subtype=1",
                "masked_url": "rtsp://admin:****@192.168.1.103:554/cam/realmonitor?channel=1&subtype=1",
                "has_reference": True,
                "last_check_status": "CROOKED",
                "last_rotation_deg": -7.8,
                "last_translation_px": 24.5,
                "last_confidence": 88.0,
                "last_check_method": "Comparação com Referência Calibrada",
                "last_checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "is_simulation": True,
                "sim_angle": -7.8
            },
            {
                "id": "pdv-04",
                "name": "PDV 04 - Retirada Balcão",
                "pos_code": 4,
                "brand": "axis",
                "ip": "192.168.1.104",
                "port": 554,
                "username": "root",
                "password": "password123",
                "channel": 1,
                "substream": True,
                "custom_url": "",
                "rtsp_url": "rtsp://root:password123@192.168.1.104:554/axis-media/media.amp?videocodec=h264&camera=1",
                "masked_url": "rtsp://root:****@192.168.1.104:554/axis-media/media.amp?videocodec=h264&camera=1",
                "has_reference": True,
                "last_check_status": "CROOKED",
                "last_rotation_deg": 12.5,
                "last_translation_px": 39.0,
                "last_confidence": 84.0,
                "last_check_method": "Comparação com Referência Calibrada",
                "last_checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "is_simulation": True,
                "sim_angle": 12.5
            }
        ]

        ref_base_path = os.path.join(TEST_IMAGES_DIR, "pdv_referencia_alinhada.jpg")
        ref_img = read_cv2_image(ref_base_path)

        for c in cams:
            ref_path = os.path.join(REFERENCES_DIR, f"{c['id']}.jpg")
            snap_path = os.path.join(SNAPSHOTS_DIR, f"{c['id']}.jpg")
            if ref_img is not None:
                write_cv2_image(ref_path, ref_img)
                h, w = ref_img.shape[:2]
                M = cv2.getRotationMatrix2D((w // 2, h // 2), c["sim_angle"], 1.0)
                curr_img = cv2.warpAffine(ref_img, M, (w, h), borderMode=cv2.BORDER_REFLECT)
                diag = VisionEngine.analyze_camera(curr_img, ref_img)
                write_cv2_image(snap_path, diag["annotated_frame"])

        return cams
