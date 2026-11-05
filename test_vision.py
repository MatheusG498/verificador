"""Checa o motor com vídeos reais da loja (pasta ../videos). Rode: python test_vision.py"""
import glob
import os
import cv2
from core.vision_engine import VisionEngine

def frame(path, pct):
    cap = cv2.VideoCapture(path)
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(cap.get(cv2.CAP_PROP_FRAME_COUNT) * pct))
    ok, f = cap.read()
    cap.release()
    assert ok, path
    return f

def rotate(img, angle):
    h, w = img.shape[:2]
    return cv2.warpAffine(img, cv2.getRotationMatrix2D((w / 2, h / 2), -angle, 1.0), (w, h),
                          borderMode=cv2.BORDER_REPLICATE)

# Lógica do veredito de posição do PDV (sem modelo): presente+centrado=ok, pouco=missing, no canto=offside, IA incerta=ok
_ok = {"pdv_pct": 55, "pdv_center_x": 0.49, "uncertainty": 0.02}
assert VisionEngine.pdv_verdict(_ok) == "ok"
assert VisionEngine.pdv_verdict({**_ok, "pdv_pct": 5}) == "missing"
assert VisionEngine.pdv_verdict({**_ok, "pdv_center_x": 0.85}) == "offside"
assert VisionEngine.pdv_verdict({**_ok, "pdv_center_x": 0.10}) == "offside"
assert VisionEngine.pdv_verdict({**_ok, "pdv_pct": 5, "uncertainty": 0.5}) == "ok"  # IA incerta não acusa
assert VisionEngine.pdv_verdict(None) is None
print("veredito posição do PDV (sintético): ok/missing/offside/incerta  OK")

# Média de N frames do corredor/PDV: suaviza os % e ignora center_x ausente num dos frames
_a = lambda ap, pp, cx: {"aisle_pct": ap, "pdv_pct": pp, "pdv_center_x": cx, "uncertainty": 0.1}
_m = VisionEngine._avg_aisle(_a(20, 30, 0.4), [_a(30, 50, 0.6), _a(40, 40, None)])
assert _m["aisle_pct"] == 30.0 and _m["pdv_pct"] == 40.0, _m          # média de 20,30,40 e 30,50,40
assert _m["pdv_center_x"] == 0.5 and _m["n_frames"] == 3, _m          # média de 0.4,0.6 (None ignorado)
print("média de 3 frames (sintético): suaviza % e ignora center_x ausente  OK")

pasta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "videos")
videos = sorted(glob.glob(os.path.join(pasta, "2026-07-29T19_57_*.avi")))  # loja de teste: 18 caixas, mesma montagem
assert videos, "Vídeos de 2026-07-29 não encontrados na pasta videos/ ao lado de verificador_posicao/"
ref = frame(videos[0], 0.15)  # 704x480, proporção != 16:9 de propósito

# Testes 1–4 são de ALINHAMENTO: IA de corredor desligada (ela é testada no 5)
modelo_corredor = VisionEngine.AISLE_MODEL
VisionEngine.AISLE_MODEL, VisionEngine._aisle_net = "", None

# 1. Ângulo medido contra a referência bate com o aplicado
for angle in (0.0, 3.2, -7.8, 12.5):
    ok, measured, *_ = VisionEngine.detect_orb_homography(ref, rotate(ref, angle))
    assert ok and abs(measured - angle) < 0.3, f"esperado {angle}°, medido {measured}°"
    print(f"rotação {angle:+6.1f}° -> {measured:+6.2f}°  OK")

# 2. Sensível: giro de 0.7° já sai de "alinhada"; mesma câmera em outro momento continua alinhada
assert VisionEngine.analyze_camera(rotate(ref, 0.7), ref)["status"] == "MILD_DRIFT"
r = VisionEngine.analyze_camera(frame(videos[0], 0.9), ref)
assert r["status"] == "ALIGNED", r["status"]
print("giro 0.7° -> MILD_DRIFT, mesma câmera outro momento -> ALIGNED  OK")

# 3. Câmera mostrando OUTRO caixa não pode passar como alinhada
#    (antes o relógio do DVR, fixo na tela, gerava casamento falso em 0°)
for other in videos[1:6]:
    r = VisionEngine.analyze_camera(frame(other, 0.5), ref)
    assert r["status"] == "CROOKED", f"{other}: {r['status']} {r['method_used']}"
print("outra cena x5 -> CROOKED  OK")

# 4. Nível do balcão sem referência acompanha giros conhecidos (é o que valida a referência automática).
#    Gira na proporção real 16:9: o vídeo 704x480 é anamórfico e girar os pixels espremidos não é um giro físico.
ref16 = cv2.resize(ref, (1280, 720))
base = VisionEngine.detect_counter_angle(ref16)
for angle in (-2.0, -1.0, 1.0, 2.0):
    lv = VisionEngine.detect_counter_angle(rotate(ref16, angle))
    assert lv is not None and abs(lv - base - angle) < 0.3, f"nível: giro {angle}°, medido {lv - base:+.2f}°"
print(f"nível do balcão (base {base:+.2f}°) acompanha giros de ±1° e ±2°  OK")

# 5. IA de corredor: acha o corredor do lado certo, inclusive com o PDV invertido na imagem
VisionEngine.AISLE_MODEL = modelo_corredor
if os.path.exists(modelo_corredor):
    for v in videos[:6]:
        f = frame(v, 0.5)
        normal, invertido = VisionEngine.detect_aisle(f), VisionEngine.detect_aisle(f[::-1])
        assert normal["aisle_side"] == "cima", f"{os.path.basename(v)}: corredor deveria estar em cima"
        assert invertido["aisle_side"] == "baixo", f"{os.path.basename(v)} invertido: corredor deveria estar embaixo"
        assert normal["aisle_pct"] > 15, f"{os.path.basename(v)}: corredor de só {normal['aisle_pct']}%"
        # PDV desta loja está bem enquadrado: aparece e fica centralizado -> veredito 'ok'
        assert normal["pdv_center_x"] is not None and 0.25 <= normal["pdv_center_x"] <= 0.75, \
            f"{os.path.basename(v)}: PDV descentralizado em {normal['pdv_center_x']}"
        assert VisionEngine.pdv_verdict(normal) == "ok", f"{os.path.basename(v)}: PDV deveria ser ok"
    print("corredor x6: lado certo normal (cima) e com PDV invertido (baixo)  OK")
    print("posição do PDV x6: aparece e centralizado -> ok  OK")
else:
    print("corredor: modelo não encontrado, teste pulado")
