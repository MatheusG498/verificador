"""
Motor de Visão Computacional para Detecção de Câmeras Tortas / Desalinhadas.
Combina Homografia (ORB Matching) com Transformada de Hough para Alinhamento Estrutural.
"""
import os
import time
import math
import cv2
import numpy as np
import urllib.request
from typing import Tuple, Optional, Dict, Any

# Configurações do OpenCV para RTSP TCP
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

class VisionEngine:
    # Movimento em relação à referência (ORB). Ruído medido em 162 pares da mesma câmera com gente/produtos
    # mudando: giro <= 0.027°, deslocamento <= 0.26px. Os limites ficam ~20x acima disso.
    TILT_TOLERANCE_ALIGNED = 0.5      # até 0.5° = alinhada
    TILT_TOLERANCE_WARNING = 2.0      # 0.5°–2° = desvio leve; acima = torta
    SHIFT_TOLERANCE_PX = 10.0         # deslocamento do centro (em imagem de 1280px de largura)
    SHIFT_CRITICAL_PX = 40.0

    # Nível do balcão sem referência (LSD + correção de lente). Erro ~0.02° a giros conhecidos, ~0.3° de
    # variação com movimento na cena; só decide com várias leituras concordando (ver CameraManager).
    LEVEL_TOLERANCE = 1.0             # até 1.0° = nivelada → pode virar referência automática
    LEVEL_WARNING = 3.0               # 1°–3° = desvio leve; acima = torta

    # Faixas do topo/rodapé onde o DVR escreve data/hora e nome do canal (OSD). O texto não se move
    # com a câmera, então casar pontos nele "prova" alinhamento falso. Ajuste se o OSD estiver no meio.
    OSD_MASK_PCT = 0.10
    # Pontos de casamento precisam cobrir ao menos esta fração da imagem (evita âncora em logo/texto)
    MIN_INLIER_SPREAD = 0.10
    # Medido nos 27 PDVs: mesma câmera (escuro, girada, com gente) >= 73 pontos; caixas vizinhos com
    # móveis idênticos chegam a 49. Abaixo disso o casamento não prova que é a mesma cena.
    MIN_INLIERS = 60

    @classmethod
    def capture_dvr_snapshot(
        cls,
        ip: str,
        http_port: int = 80,
        username: str = "admin",
        password: str = "",
        channel: int = 1,
        brand: str = "intelbras",
        timeout_s: float = 2.5,
        tunnel_host: Optional[str] = None,
        tunnel_mac: Optional[str] = None
    ) -> Tuple[bool, Optional[np.ndarray], str]:
        """
        Puxa a imagem diretamente do DVR via API HTTP Snapshot (Intelbras, Dahua, Hikvision, Axis).
        Com tunnel_host/mac, abre um túnel SSH sob demanda até o DVR na LAN da loja. Digest/Basic auto.
        """
        if not ip or not ip.strip():
            return False, None, "IP não informado"

        ip = ip.strip()
        brand_lower = (brand or "generic").lower()
        from core.tunnel import dvr_tunnel

        try:
            with dvr_tunnel(tunnel_host, tunnel_mac, ip, int(http_port)) as (h, p):
                if brand_lower in ("intelbras", "dahua"):
                    url = f"http://{h}:{p}/cgi-bin/snapshot.cgi?channel={channel}"
                elif brand_lower == "hikvision":
                    url = f"http://{h}:{p}/ISAPI/Streaming/channels/{channel}01/picture"
                elif brand_lower == "axis":
                    url = f"http://{h}:{p}/axis-cgi/jpg/image.cgi?camera={channel}"
                else:
                    url = f"http://{h}:{p}/cgi-bin/snapshot.cgi?channel={channel}"

                password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
                password_mgr.add_password(None, url, username, password)
                handler_digest = urllib.request.HTTPDigestAuthHandler(password_mgr)
                handler_basic = urllib.request.HTTPBasicAuthHandler(password_mgr)
                opener = urllib.request.build_opener(handler_digest, handler_basic)

                req = urllib.request.Request(url, headers={"User-Agent": "CFTV-Auditor/1.0"})
                with opener.open(req, timeout=timeout_s) as resp:
                    data = resp.read()
                    if len(data) > 1000:
                        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                        if img is not None and img.size > 0:
                            return True, img, "Snapshot capturado via API HTTP do DVR"
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return False, None, f"Autenticação recusada pelo DVR (Verifique usuário/senha)"
            return False, None, f"Erro HTTP {e.code} ao requisitar snapshot"
        except Exception as e:
            return False, None, f"Falha na requisição HTTP Snapshot: {str(e)}"

        return False, None, "Dados inválidos de imagem recebidos do DVR"

    @classmethod
    def capture_frame(
        cls,
        rtsp_url: str,
        timeout_s: float = 3.5,
        camera_meta: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, Optional[np.ndarray], str]:
        """
        Captura um frame individual a partir de:
        1. API HTTP Snapshot do DVR (método mais rápido e estável para Intelbras/Dahua/Hikvision);
        2. Stream RTSP via rede (OpenCV TCP);
        3. Arquivo de vídeo local (.avi, .mp4, etc.).
        """
        if not rtsp_url or rtsp_url.startswith("mock://") or rtsp_url.startswith("sim://"):
            return True, VisionEngine.generate_synthetic_checkout_frame(), "Simulação ativa"

        # 1. Se for um arquivo de vídeo ou imagem local existente
        if os.path.exists(rtsp_url) and os.path.isfile(rtsp_url):
            try:
                # Se já for imagem direta
                if rtsp_url.lower().endswith((".jpg", ".jpeg", ".png")):
                    with open(rtsp_url, "rb") as f:
                        img = cv2.imdecode(np.frombuffer(f.read(), np.uint8), cv2.IMREAD_COLOR)
                        if img is not None and img.size > 0:
                            return True, img, "Frame de imagem local carregado"

                cap = cv2.VideoCapture(rtsp_url)
                if not cap.isOpened():
                    return False, None, "Não foi possível abrir o arquivo de vídeo"
                total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 1)
                target_frame = min(max(int(total_frames * 0.15), 0), max(total_frames - 1, 0))
                cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
                ret, frame = cap.read()
                if not ret or frame is None:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ret, frame = cap.read()
                cap.release()
                if ret and frame is not None and frame.size > 0:
                    return True, frame, "Frame de vídeo extraído com sucesso"
                return False, None, "Arquivo de vídeo não possui frames válidos"
            except Exception as e:
                return False, None, f"Erro ao ler vídeo: {str(e)}"

        # 2. Tentar captura direta via HTTP Snapshot se tiver metadados de IP
        if camera_meta and camera_meta.get("ip"):
            ip = camera_meta.get("ip")
            http_port = int(camera_meta.get("http_port") or 80)
            username = camera_meta.get("username") or "admin"
            password = camera_meta.get("password") or ""
            channel = int(camera_meta.get("channel") or 1)
            brand = camera_meta.get("brand") or "intelbras"

            snap_ok, snap_img, snap_msg = cls.capture_dvr_snapshot(
                ip=ip, http_port=http_port, username=username,
                password=password, channel=channel, brand=brand,
                timeout_s=min(timeout_s, 2.5)
            )
            if snap_ok and snap_img is not None:
                return True, snap_img, snap_msg

        # 3. Conexão RTSP via rede (fallback para stream ao vivo)
        cap = None
        try:
            params = [
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(timeout_s * 1000),
                cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(timeout_s * 1000)
            ]
            try:
                cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG, params)
            except Exception:
                cap = cv2.VideoCapture(rtsp_url)

            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            t0 = time.time()
            frame = None
            ret = False
            
            while time.time() - t0 < timeout_s:
                if cap.grab():
                    ret, frame = cap.retrieve()
                    if ret and frame is not None and frame.size > 0:
                        break
                time.sleep(0.05)

            if ret and frame is not None:
                return True, frame, "Captura realizada via RTSP do DVR"
            else:
                return False, None, "Timeout ou sem sinal do DVR"

        except Exception as e:
            return False, None, f"Erro na conexão com DVR: {str(e)}"
        finally:
            if cap is not None:
                cap.release()

    @classmethod
    def detect_orb_homography(
        cls,
        ref_img: np.ndarray,
        curr_img: np.ndarray
    ) -> Tuple[bool, float, float, float, int, float]:
        """
        Calcula o desvio angular (graus) e translação em relação ao frame de referência usando ORB e RANSAC.
        Retorna: (sucesso, angulo_graus, translacao_px, escala, num_inliers, confianca)
        """
        # Redimensiona para largura padrão mantendo a proporção da referência
        # (esticar para 16:9 escala X e Y diferente e distorce o ângulo medido)
        std_w = 1280
        std_h = round(ref_img.shape[0] * std_w / ref_img.shape[1])
        ref_resized = cv2.resize(ref_img, (std_w, std_h))
        curr_resized = cv2.resize(curr_img, (std_w, std_h))

        gray_ref = cv2.cvtColor(ref_resized, cv2.COLOR_BGR2GRAY)
        gray_curr = cv2.cvtColor(curr_resized, cv2.COLOR_BGR2GRAY)

        # Extrator ORB com 1500 pontos
        osd = int(std_h * cls.OSD_MASK_PCT)
        mask = np.zeros((std_h, std_w), np.uint8)
        mask[osd:std_h - osd, :] = 255

        orb = cv2.ORB_create(nfeatures=1500, fastThreshold=15)
        kp1, des1 = orb.detectAndCompute(gray_ref, mask)
        kp2, des2 = orb.detectAndCompute(gray_curr, mask)

        if des1 is None or des2 is None or len(kp1) < 10 or len(kp2) < 10:
            return False, 0.0, 0.0, 1.0, 0, 0.0

        # Matcher BF com Hamming
        bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
        matches = bf.knnMatch(des1, des2, k=2)

        # Filtro de razão de Lowe
        good_matches = []
        for m_pair in matches:
            if len(m_pair) == 2:
                m, n = m_pair
                if m.distance < 0.78 * n.distance:
                    good_matches.append(m)

        if len(good_matches) < 8:
            return False, 0.0, 0.0, 1.0, len(good_matches), 0.0

        src_pts = np.float32([kp1[m.queryIdx].pt for m in good_matches]).reshape(-1, 1, 2)
        dst_pts = np.float32([kp2[m.trainIdx].pt for m in good_matches]).reshape(-1, 1, 2)

        # Estima transformação afim parcial (rotação, escala, translação) com RANSAC
        matrix, inliers = cv2.estimateAffinePartial2D(src_pts, dst_pts, method=cv2.RANSAC, ransacReprojThreshold=4.0)

        if matrix is None or inliers is None:
            return False, 0.0, 0.0, 1.0, 0, 0.0

        num_inliers = int(np.sum(inliers))
        confidence = float(num_inliers / max(len(good_matches), 1))

        # Inliers amontoados num canto = casamento em texto/logo, não na cena
        pts = dst_pts[inliers.ravel() == 1].reshape(-1, 2)
        x, y, bw, bh = cv2.boundingRect(pts)
        if bw * bh < cls.MIN_INLIER_SPREAD * std_w * std_h:
            return False, 0.0, 0.0, 1.0, num_inliers, 0.0

        # Extração de ângulo: atan2(M[1,0], M[0,0])
        rot_rad = math.atan2(matrix[1, 0], matrix[0, 0])
        rot_deg = math.degrees(rot_rad)

        # Translação: quanto o centro da imagem andou (tx,ty puros incluem o efeito da rotação em torno de (0,0))
        cx, cy = std_w / 2, std_h / 2
        trans_px = math.hypot(matrix[0, 0] * cx + matrix[0, 1] * cy + matrix[0, 2] - cx,
                              matrix[1, 0] * cx + matrix[1, 1] * cy + matrix[1, 2] - cy)

        # Escala
        scale = math.hypot(matrix[0, 0], matrix[1, 0])

        return True, rot_deg, trans_px, scale, num_inliers, confidence

    # IA própria (treino/treinar_modelo.py) que pinta cada pixel como outro / balcão-PDV / corredor.
    # Sem o arquivo, a checagem de corredor fica desligada e o resto funciona igual.
    AISLE_MODEL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "pdv_corredor.onnx")
    # % mínima da imagem mostrando o corredor de carrinhos. Provisório: a IA de carrinhos do cliente reprovou o
    # CX12 da loja de teste (~23% de corredor); ajuste com os veredictos dela.
    AISLE_MIN_PCT = 25.0
    # Incerteza média da IA (1 - probabilidade da classe escolhida) acima da qual ela não decide sozinha:
    # vira "conferir" em vez de arriscar um "Alinhada"/"Sem Corredor" errado.
    AISLE_MAX_UNCERTAINTY = 0.12
    # Faixa de dúvida em volta do limite: o % de corredor da IA erra ~5 pontos em lojas novas, e os erros de
    # decisão medidos ficaram todos colados no limite (ex.: previsto 24% x real 27%). Dentro da faixa, pede conferência.
    AISLE_MARGIN_PCT = 5.0
    # Posição do PDV (medida nas 331 câmeras boas revisadas: pdv_pct mín. 24% / p5 35%; centro_x sempre 0.39–0.61).
    # Piso de tamanho com folga p/ erro da IA; banda horizontal larga: só pega o PDV grosseiramente mal enquadrado
    # ("do lado oposto"). Encostar na borda lateral/base é normal (balcão atravessa a imagem), então não é sinal.
    PDV_MIN_PCT = 15.0
    PDV_CENTER_MIN = 0.25
    PDV_CENTER_MAX = 0.75
    _aisle_net = None

    @classmethod
    def detect_aisle(cls, img: np.ndarray) -> Optional[Dict[str, Any]]:
        """% da imagem que é corredor/PDV, lado do corredor e incerteza da IA. None se não houver modelo."""
        if cls._aisle_net is None:
            if not os.path.exists(cls.AISLE_MODEL):
                return None
            # pelos bytes: no Windows o OpenCV não abre caminho com acento ("posição")
            cls._aisle_net = cv2.dnn.readNetFromONNX(np.fromfile(cls.AISLE_MODEL, np.uint8))
        x = cv2.cvtColor(cv2.resize(img, (448, 256)), cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        x = (x - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
        x = x.transpose(2, 0, 1)
        # Dupla checagem: imagem normal + espelhada (desespelhada de volta), probabilidades somadas
        outs = []
        for xi in (x, x[:, :, ::-1]):  # ONNX exportado com lote fixo de 1
            cls._aisle_net.setInput(np.ascontiguousarray(xi)[None])
            outs.append(cls._aisle_net.forward()[0])
        logits = outs[0] + outs[1][:, :, ::-1]
        prob = np.exp(logits - logits.max(0))
        prob /= prob.sum(0)
        uncertainty = float(1 - prob.max(0).mean())
        mask = prob.argmax(0).astype(np.uint8)  # 0 outro, 1 pdv, 2 corredor
        ys = np.arange(mask.shape[0])[:, None]
        pdv_y = float(ys.repeat(mask.shape[1], 1)[mask == 1].mean()) if (mask == 1).any() else None
        cor_y = float(ys.repeat(mask.shape[1], 1)[mask == 2].mean()) if (mask == 2).any() else None
        side = None if pdv_y is None or cor_y is None else ("cima" if cor_y < pdv_y else "baixo")
        # centro horizontal do PDV (0 esquerda, 1 direita): detecta o PDV jogado pro lado / "do lado oposto"
        pdv_cx = float(np.where(mask == 1)[1].mean() / mask.shape[1]) if (mask == 1).any() else None
        return {"aisle_pct": round(float((mask == 2).mean() * 100), 1),
                "pdv_pct": round(float((mask == 1).mean() * 100), 1),
                "pdv_center_x": round(pdv_cx, 2) if pdv_cx is not None else None,
                "aisle_side": side, "uncertainty": round(uncertainty, 3), "mask": mask}

    @classmethod
    def aisle_verdict(cls, aisle: Optional[Dict[str, Any]]) -> Optional[str]:
        """'ok' | 'insufficient' | 'doubt' (IA incerta ou % dentro da margem do limite) | None (sem modelo)."""
        if not aisle:
            return None
        if aisle["uncertainty"] > cls.AISLE_MAX_UNCERTAINTY or abs(aisle["aisle_pct"] - cls.AISLE_MIN_PCT) < cls.AISLE_MARGIN_PCT:
            return "doubt"
        return "insufficient" if aisle["aisle_pct"] < cls.AISLE_MIN_PCT else "ok"

    @classmethod
    def pdv_verdict(cls, aisle: Optional[Dict[str, Any]]) -> Optional[str]:
        """Posição do PDV: 'ok' | 'missing' (quase não aparece) | 'offside' (jogado pro lado) | None.
        A IA incerta não acusa (evita falso alarme); quem decide o 'doubt' é o aisle_verdict."""
        if not aisle or aisle["uncertainty"] > cls.AISLE_MAX_UNCERTAINTY:
            return None if not aisle else "ok"
        if aisle["pdv_pct"] < cls.PDV_MIN_PCT:
            return "missing"
        cx = aisle["pdv_center_x"]
        if cx is not None and not (cls.PDV_CENTER_MIN <= cx <= cls.PDV_CENTER_MAX):
            return "offside"
        return "ok"

    # Curvatura da lente grande-angular (modelo radial k1, focal ~0.5×largura). Estimada pelo método
    # plumb-line nas 7 câmeras do Assaí Casa Verde: -0.175 a -0.225 (mesmo modelo de lente).
    LENS_K1 = -0.20

    @classmethod
    def detect_counter_angle(cls, img: np.ndarray) -> Optional[float]:
        """
        Nível do balcão sem referência: ângulo das bordas longas quase horizontais em relação à horizontal da
        imagem (câmera vista de cima → balcão reto = câmera alinhada). None se não houver bordas nítidas.
        - corrige a curvatura da lente antes (sem isso a mesma borda lia 0° numa metade e +3.7° na outra);
        - LSD (segmentos sub-pixel), só a faixa central (rodapé tem gôndolas), mediana ponderada por comprimento².
        Medido: giro conhecido com erro mediano 0.02°; leitura estável (<=0.09°) em 6/7 caixas ao vivo e
        variação típica de 0.32° entre momentos numa loja movimentada.
        """
        W, H = 1280, 720  # substream D1 (704x480) é 16:9 espremido; esticar restaura a proporção real
        K = np.array([[W * 0.5, 0, W / 2], [0, W * 0.5, H / 2], [0, 0, 1]], np.float64)
        flat = cv2.undistort(cv2.resize(img, (W, H)), K, np.array([cls.LENS_K1, 0, 0, 0], np.float64))
        segs = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD).detect(cv2.cvtColor(flat, cv2.COLOR_BGR2GRAY))[0]
        if segs is None:
            return None
        ang, wt = [], []
        for x1, y1, x2, y2 in segs.reshape(-1, 4):
            length = math.hypot(x2 - x1, y2 - y1)
            if length < W * 0.08:
                continue
            if min(x1, x2) < W * 0.05 or max(x1, x2) > W * 0.95 or min(y1, y2) < H * 0.12 or max(y1, y2) > H * 0.80:
                continue
            a = ((math.degrees(math.atan2(y2 - y1, x2 - x1)) + 90) % 180) - 90
            if abs(a) > 12.0:
                continue
            ang.append(a)
            wt.append(length * length)
        if not ang:
            return None
        ang, wt = np.array(ang), np.array(wt)
        order = np.argsort(ang)
        cum = np.cumsum(wt[order])
        return float(ang[order][np.searchsorted(cum, cum[-1] / 2)])

    @classmethod
    def _avg_aisle(cls, primary: Dict[str, Any], extras: list) -> Dict[str, Any]:
        """Média dos % de corredor/PDV sobre vários frames (corta o ruído de frame único: gente/carrinho passando).
        Mantém a máscara e o lado do frame principal (categóricos, estáveis); só suaviza os números da decisão."""
        reads = [primary] + [e for e in extras if e]
        primary["aisle_pct"] = round(float(np.mean([r["aisle_pct"] for r in reads])), 1)
        primary["pdv_pct"] = round(float(np.mean([r["pdv_pct"] for r in reads])), 1)
        primary["uncertainty"] = round(float(np.mean([r["uncertainty"] for r in reads])), 3)
        cxs = [r["pdv_center_x"] for r in reads if r["pdv_center_x"] is not None]
        primary["pdv_center_x"] = round(float(np.mean(cxs)), 2) if cxs else None
        primary["n_frames"] = len(reads)
        return primary

    @classmethod
    def analyze_camera(
        cls,
        curr_frame: np.ndarray,
        ref_frame: Optional[np.ndarray] = None,
        aisle_extra_frames: Optional[list] = None
    ) -> Dict[str, Any]:
        """
        Executa a análise completa unindo comparação com referência e linhas estruturais.
        """
        has_ref = ref_frame is not None and ref_frame.size > 0
        level = cls.detect_counter_angle(curr_frame)

        rotation_deg = 0.0
        translation_px = 0.0
        scale = 1.0
        inliers_count = 0
        confidence = 0.0
        ref_ok = False

        if has_ref:
            ref_ok, rotation_deg, translation_px, scale, inliers_count, confidence = cls.detect_orb_homography(
                ref_frame, curr_frame
            )

        # Com referência: mede MOVIMENTO (preciso, imune a gente passando). Sem referência: nível do balcão.
        matched = ref_ok and confidence > 0.25 and inliers_count >= cls.MIN_INLIERS
        scene_mismatch = has_ref and not matched  # tem gabarito mas a cena não bate = câmera virada/deslocada
        tol_ok, tol_warn = cls.TILT_TOLERANCE_ALIGNED, cls.TILT_TOLERANCE_WARNING
        if matched:
            effective_angle = rotation_deg
            method_used = "Comparação com Referência Calibrada"
        elif has_ref:
            effective_angle = 0.0
            method_used = "Cena Não Corresponde à Referência"
        elif level is not None:
            effective_angle = level
            tol_ok, tol_warn = cls.LEVEL_TOLERANCE, cls.LEVEL_WARNING
            method_used = "Nível do Balcão (sem referência)"
            confidence = 0.0
        else:
            effective_angle = 0.0
            method_used = "Sem referência e sem bordas do balcão nítidas"
            confidence = 0.0

        # Verificação de qualidade da imagem (Obstrução e Foco)
        gray_curr = cv2.cvtColor(curr_frame, cv2.COLOR_BGR2GRAY) if len(curr_frame.shape) == 3 else curr_frame
        std_dev = float(np.std(gray_curr))
        mean_brightness = float(np.mean(gray_curr))
        focus_score = float(cv2.Laplacian(gray_curr, cv2.CV_64F).var())
        is_obstructed = std_dev < 9.0 or mean_brightness < 5.0
        is_blurry = focus_score < 18.0

        abs_angle = abs(effective_angle)
        is_shifted = bool(matched and translation_px > cls.SHIFT_TOLERANCE_PX)
        shift_critical = bool(matched and translation_px > cls.SHIFT_CRITICAL_PX)

        if is_obstructed:
            status = "OBSTRUCTED"
            status_text = "Obstruída / Sem Imagem"
            status_color = "#64748b"
            severity = "FALHA"
            effective_angle = 0.0
            abs_angle = 0.0
            is_alignment_ok = False
            diag_reason = "Lente obstruída, sem iluminação ou sem sinal de vídeo do caixa."
        elif is_blurry and focus_score < 15.0:
            status = "BLURRY"
            status_text = "Desfocada / Lente Suja"
            status_color = "#8b5cf6"
            severity = "ATENÇÃO"
            is_alignment_ok = False
            diag_reason = f"Perda severa de nitidez óptica (Score: {focus_score:.1f}). Lente precisa de limpeza ou foco."
        elif scene_mismatch:
            status = "CROOKED"
            status_text = "Fora de Posição"
            status_color = "#ef4444"
            severity = "CRÍTICO"
            is_alignment_ok = False
            diag_reason = "A imagem atual não corresponde à referência calibrada: câmera virada, deslocada ou apontando para outro local."
        elif not has_ref and level is None:
            # Sem gabarito e sem bordas para medir: não há como afirmar que está alinhada
            status = "PENDING"
            status_text = "Nível Não Mensurável"
            status_color = "#64748b"
            severity = "ATENÇÃO"
            is_alignment_ok = False
            diag_reason = "Sem referência e sem bordas de balcão nítidas para medir o nível. Calibre pelo link do técnico."
        elif abs_angle > tol_warn or shift_critical:
            status = "CROOKED"
            status_text = "Torta / Crítica"
            status_color = "#ef4444"  # Vermelho coral
            severity = "CRÍTICO"
            is_alignment_ok = False
            diag_reason = (f"Inclinação de {abs_angle:.2f}° (limite {tol_warn}°)" if abs_angle > tol_warn
                           else f"Deslocamento de {translation_px:.1f}px (limite {cls.SHIFT_CRITICAL_PX:.0f}px)") + \
                          " em relação à posição correta."
        elif abs_angle > tol_ok:
            status = "MILD_DRIFT"
            status_text = "Desvio Leve"
            status_color = "#f59e0b"  # Amarelo âmbar
            severity = "ATENÇÃO"
            is_alignment_ok = False
            diag_reason = f"Inclinação leve de {abs_angle:.2f}° (alinhada até {tol_ok}°). Ajuste a câmera."
        elif is_shifted:
            status = "MILD_DRIFT"
            status_text = "Deslocamento de Posição"
            status_color = "#f59e0b"  # Amarelo âmbar
            severity = "ATENÇÃO"
            is_alignment_ok = False
            diag_reason = f"Nível correto ({abs_angle:.2f}°), mas a câmera se deslocou {translation_px:.1f}px em relação à referência."
        else:
            status = "ALIGNED"
            status_text = "Alinhada (100% OK)"
            status_color = "#10b981"  # Verde esmeralda
            severity = "OK"
            is_alignment_ok = True
            diag_reason = f"Alinhamento verificado. Rotação ({abs_angle:.2f}°) e posição dentro da tolerância."

        # Corredor de carrinhos: mesmo reta, a câmera não serve se não mostrar o corredor à frente do caixa
        aisle = None if is_obstructed else cls.detect_aisle(curr_frame)
        if aisle is not None and aisle_extra_frames:  # média de N frames: suaviza o % (ruído de gente/carrinho passando)
            aisle = cls._avg_aisle(aisle, [cls.detect_aisle(f) for f in aisle_extra_frames])
        verdict = cls.aisle_verdict(aisle)
        if aisle:
            aisle["verdict"] = verdict
        # Dúvida só substitui "Alinhada" (nunca esconde um desvio já detectado); falta de corredor é grave e
        # substitui também "Desvio Leve".
        if verdict == "doubt" and status in ("ALIGNED", "PENDING"):
            status = "PENDING"
            status_color = "#64748b"
            severity = "ATENÇÃO"
            is_alignment_ok = False
            if aisle["uncertainty"] > cls.AISLE_MAX_UNCERTAINTY:
                status_text = "Corredor: IA em Dúvida"
                diag_reason = (f"A IA não tem certeza de onde está o corredor nesta imagem (incerteza "
                               f"{aisle['uncertainty']:.0%}). Confira a câmera pelo Diagnóstico.")
            else:
                status_text = "Corredor no Limite"
                diag_reason = (f"A câmera mostra ~{aisle['aisle_pct']:.0f}% de corredor, perto do mínimo de "
                               f"{cls.AISLE_MIN_PCT:.0f}% (margem de erro da IA: ±{cls.AISLE_MARGIN_PCT:.0f} pontos). "
                               f"Confira se o corredor aparece o suficiente para a IA de carrinhos.")
        elif verdict == "insufficient" and status in ("ALIGNED", "MILD_DRIFT", "PENDING"):
            status = "NO_AISLE"
            status_text = "Corredor Insuficiente"
            status_color = "#ef4444"
            severity = "CRÍTICO"
            is_alignment_ok = False
            diag_reason = (f"Só {aisle['aisle_pct']:.0f}% da imagem mostra o corredor de carrinhos (mínimo "
                           f"{cls.AISLE_MIN_PCT:.0f}%). Reposicione a câmera para enquadrar o corredor à frente do caixa"
                           + (f" (fica {'acima' if aisle['aisle_side'] == 'cima' else 'abaixo'} do PDV)." if aisle["aisle_side"] else "."))

        # Posição do PDV: além do corredor, a câmera precisa enquadrar o próprio PDV (há casos dele no lado oposto).
        # Entra no mesmo balde "reposicionar" (NO_AISLE), só com texto próprio; não mexe em câmera já marcada torta.
        pdv_v = cls.pdv_verdict(aisle)
        if aisle:
            aisle["pdv_verdict"] = pdv_v
        if pdv_v in ("missing", "offside") and status in ("ALIGNED", "MILD_DRIFT", "PENDING"):
            status = "NO_AISLE"
            status_text = "PDV Fora de Enquadramento"
            status_color = "#ef4444"
            severity = "CRÍTICO"
            is_alignment_ok = False
            if pdv_v == "missing":
                diag_reason = (f"O PDV quase não aparece na imagem (só {aisle['pdv_pct']:.0f}% da tela). "
                               f"A câmera pode estar apontada para o lado errado — reposicione para enquadrar o caixa.")
            else:
                lado = "esquerda" if aisle["pdv_center_x"] < 0.5 else "direita"
                diag_reason = (f"O PDV está jogado para a {lado} da imagem (centro em {aisle['pdv_center_x']:.0%} da "
                               f"largura). Centralize o caixa no enquadramento.")

        # Gera o frame com overlay gráfico de diagnóstico
        annotated_frame = cls.draw_diagnostic_overlay(
            curr_frame,
            effective_angle=effective_angle,
            status=status,
            status_text=status_text,
            status_color=status_color,
            method_used=method_used,
            confidence=confidence,
            translation_px=translation_px,
            focus_score=focus_score,
            aisle=aisle
        )

        return {
            "status": status,
            "status_text": status_text,
            "status_color": status_color,
            "severity": severity,
            "is_alignment_ok": is_alignment_ok,
            "diag_reason": diag_reason,
            "rotation_deg": round(effective_angle, 2),
            "abs_rotation_deg": round(abs_angle, 2),
            "translation_px": round(translation_px, 1),
            "scale": round(scale, 2),
            "focus_score": round(focus_score, 1),
            "is_blurry": is_blurry,
            "is_obstructed": is_obstructed,
            "level_angle_deg": round(level, 2) if level is not None else None,
            "inliers_count": inliers_count,
            "confidence": round(confidence * 100, 1),
            "method_used": method_used,
            "has_reference": has_ref,
            "aisle_pct": aisle["aisle_pct"] if aisle else None,
            "aisle_side": aisle["aisle_side"] if aisle else None,
            "aisle": aisle,  # inclui a máscara (não serializável): quem devolve JSON remove esta chave
            "annotated_frame": annotated_frame
        }

    @staticmethod
    def draw_diagnostic_overlay(
        frame: np.ndarray,
        effective_angle: float,
        status: str,
        status_text: str,
        status_color: str,
        method_used: str,
        confidence: float,
        translation_px: float,
        focus_score: float = 50.0,
        aisle: Optional[Dict[str, Any]] = None
    ) -> np.ndarray:
        """
        Desenha grade técnica de nível, inclinômetro virtual e dados diagnósticos na imagem.
        """
        out = frame.copy()
        h, w = out.shape[:2]
        cx, cy = w // 2, h // 2

        if aisle is not None:  # corredor detectado pela IA em verde translúcido
            cor = cv2.resize(aisle["mask"], (w, h), interpolation=cv2.INTER_NEAREST) == 2
            tint = out.copy()
            tint[cor] = (60, 200, 60)
            cv2.addWeighted(tint, 0.35, out, 0.65, 0, out)

        # Cor BGR para o OpenCV baseada no status
        if status == "ALIGNED":
            bgr_color = (129, 185, 16)   # Verde (#10b981)
        elif status == "MILD_DRIFT":
            bgr_color = (11, 158, 245)   # Amarelo (#f59e0b)
        elif status == "BLURRY":
            bgr_color = (246, 92, 139)   # Roxo (#8b5cf6)
        elif status == "OBSTRUCTED":
            bgr_color = (139, 116, 100)  # Cinza slate
        elif status == "PENDING":
            bgr_color = (139, 116, 100)  # Cinza slate
        else:
            bgr_color = (68, 68, 239)    # Vermelho (#ef4444)

        # 1. Linhas guias horizontais e verticais de centro (Grid sutil)
        grid_overlay = out.copy()
        cv2.line(grid_overlay, (0, cy), (w, cy), (200, 200, 200), 1, cv2.LINE_AA)
        cv2.line(grid_overlay, (cx, 0), (cx, h), (200, 200, 200), 1, cv2.LINE_AA)
        cv2.circle(grid_overlay, (cx, cy), 80, (200, 200, 200), 1, cv2.LINE_AA)
        cv2.circle(grid_overlay, (cx, cy), 160, (200, 200, 200), 1, cv2.LINE_AA)
        cv2.addWeighted(grid_overlay, 0.35, out, 0.65, 0, out)

        # 2. Linha artificial do horizonte rotacionada com o ângulo detectado
        # Ângulo positivo = conteúdo girado no sentido horário na tela (lado direito desce, y cresce para baixo);
        # a linha acompanha a borda do balcão, não o espelho dela
        rad = math.radians(effective_angle)
        line_len = min(w, h) * 0.45
        x1 = int(cx - line_len * math.cos(rad))
        y1 = int(cy - line_len * math.sin(rad))
        x2 = int(cx + line_len * math.cos(rad))
        y2 = int(cy + line_len * math.sin(rad))
        
        cv2.line(out, (x1, y1), (x2, y2), bgr_color, 3, cv2.LINE_AA)
        cv2.circle(out, (cx, cy), 6, bgr_color, -1, cv2.LINE_AA)

        # 3. Banner superior com informações técnicas
        banner_h = 98
        overlay_banner = out.copy()
        cv2.rectangle(overlay_banner, (0, 0), (w, banner_h), (20, 24, 33), -1)
        cv2.addWeighted(overlay_banner, 0.82, out, 0.18, 0, out)

        # Borda colorida no banner
        cv2.line(out, (0, banner_h), (w, banner_h), bgr_color, 3)

        # Textos informativos
        status_label = f"STATUS: {status_text.upper()}"
        cv2.putText(out, status_label, (25, 32), cv2.FONT_HERSHEY_DUPLEX, 0.85, bgr_color, 2, cv2.LINE_AA)

        angle_sign = "+" if effective_angle > 0 else ""
        angle_label = f"Inclinacao: {angle_sign}{effective_angle:.2f} deg  |  Deslocamento: {translation_px:.1f}px  |  Foco: {focus_score:.0f}"
        if aisle is not None:
            angle_label += f"  |  Corredor: {aisle['aisle_pct']:.0f}%"
        cv2.putText(out, angle_label, (25, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (240, 240, 240), 1, cv2.LINE_AA)

        meta_label = f"Metodo: {method_used} (Confianca: {confidence:.0f}%)"
        cv2.putText(out, meta_label, (25, 86), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (180, 190, 200), 1, cv2.LINE_AA)

        return out

    @staticmethod
    def generate_synthetic_checkout_frame(tilt_angle: float = 0.0) -> np.ndarray:
        """
        Gera um frame realista de simulação de caixa de supermercado/PDV
        para testes mesmo sem câmeras físicas conectadas.
        """
        w, h = 1280, 720
        # Fundo do piso de supermercado
        frame = np.full((h, w, 3), (220, 222, 225), dtype=np.uint8)

        # Desenhar balcão e esteira do caixa
        cv2.rectangle(frame, (180, 180), (1100, 580), (95, 95, 100), -1)      # Balcão
        cv2.rectangle(frame, (230, 220), (820, 540), (45, 45, 48), -1)        # Esteira preta
        cv2.rectangle(frame, (840, 240), (1050, 520), (190, 190, 195), -1)    # Área de leitura / inox

        # Linhas de demarcação do caixa
        cv2.line(frame, (230, 220), (1050, 220), (255, 255, 255), 2)
        cv2.line(frame, (230, 540), (1050, 540), (255, 255, 255), 2)
        cv2.line(frame, (830, 220), (830, 540), (20, 200, 20), 3)             # Divisão leitor

        # Simula leitor óptico e balança
        cv2.rectangle(frame, (880, 310), (1010, 450), (30, 30, 35), -1)
        cv2.circle(frame, (945, 380), 25, (0, 0, 230), -1)                    # Feixe laser leitor

        # Se houver inclinação simulada, rotaciona a imagem inteira
        if abs(tilt_angle) > 0.01:
            M = cv2.getRotationMatrix2D((w // 2, h // 2), tilt_angle, 1.0)
            frame = cv2.warpAffine(frame, M, (w, h), borderMode=cv2.BORDER_REFLECT)

        return frame
