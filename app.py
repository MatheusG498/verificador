import os
import io
import json
import time
import base64
import cv2
import numpy as np
from flask import Flask, render_template, request, jsonify, send_file, Response, session, redirect, url_for
from core.camera_manager import CameraManager, SNAPSHOTS_DIR, REFERENCES_DIR, read_cv2_image
from core.dvr_manager import DVRManager
from core.vision_engine import VisionEngine

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["JSON_AS_ASCII"] = False
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.jinja_env.auto_reload = True

# ==============================================================================
# AUTENTICAÇÃO — logins do MongoDB (easicash.logins, senha bcrypt). Só entra quem tem auditoria=true.
# ==============================================================================
import secrets
from core.mongo_store import check_login as _check_login, enabled as _mongo_enabled

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
# chave de sessão persistente (sobrevive a restart): env SECRET_KEY, ou arquivo, ou gera e salva
if os.environ.get("SECRET_KEY"):
    app.secret_key = os.environ["SECRET_KEY"]
else:
    _skf = os.path.join(_DATA_DIR, "secret_key")
    try:
        app.secret_key = open(_skf, encoding="utf-8").read().strip()
        assert app.secret_key
    except Exception:
        app.secret_key = secrets.token_hex(32)
        try:
            os.makedirs(_DATA_DIR, exist_ok=True)
            with open(_skf, "w", encoding="utf-8") as _f:
                _f.write(app.secret_key)
        except Exception:
            pass
# Secure fica OFF de propósito: o painel é acessado tanto por HTTPS (túnel) quanto por HTTP (VPN).
from datetime import timedelta
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  PERMANENT_SESSION_LIFETIME=timedelta(days=30))

_PUBLIC_PREFIXES = ("/login", "/static/", "/favicon.ico", "/api/score_frame")

@app.before_request
def _require_login():
    if request.path.startswith(_PUBLIC_PREFIXES) or session.get("user"):
        return
    if request.path.startswith("/api/"):
        return jsonify({"success": False, "message": "Não autenticado"}), 401
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    erro = None
    if request.method == "POST":
        res = _check_login((request.form.get("username") or "").strip(), request.form.get("password") or "")
        if res and res.get("auditoria"):
            session["user"] = res["username"]
            session.permanent = bool(request.form.get("remember"))  # "Manter conectado" (30 dias)
            return redirect(url_for("index"))
        erro = "Seu usuário não tem permissão de auditoria." if res else "Usuário ou senha inválidos."
    return render_template("login.html", erro=erro, mongo_ok=_mongo_enabled())

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.after_request
def add_header(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@app.route("/")
def index():
    return render_template("index.html", user=session.get("user"))

@app.route("/report")
def report():
    cameras = CameraManager.load_cameras()
    total = len(cameras)
    aligned = sum(1 for c in cameras if c.get("last_check_status") == "ALIGNED")
    warning = sum(1 for c in cameras if c.get("last_check_status") == "MILD_DRIFT")
    crooked = sum(1 for c in cameras if c.get("last_check_status") in ("CROOKED", "NO_AISLE"))
    offline = sum(1 for c in cameras if c.get("last_check_status") in ("OFFLINE", "PENDING"))
    
    compliance_rate = round((aligned / total * 100), 1) if total > 0 else 0.0

    return render_template(
        "report.html",
        cameras=cameras,
        total=total,
        aligned=aligned,
        warning=warning,
        crooked=crooked,
        offline=offline,
        compliance_rate=compliance_rate,
        generated_at=time.strftime("%d/%m/%Y às %H:%M:%S")
    )

# ==============================================================================
# ROTAS DE GERENCIAMENTO DE DVRS & AUTO-IMPORTAÇÃO
# ==============================================================================

@app.route("/api/dvrs", methods=["GET"])
def get_dvrs():
    dvrs = DVRManager.load_dvrs()
    cameras = CameraManager.load_cameras()
    # Adiciona contagem de câmeras cadastradas em cada DVR
    for d in dvrs:
        d_id = d.get("id")
        d["assigned_cameras_count"] = sum(1 for c in cameras if c.get("dvr_id") == d_id)
    return jsonify({"success": True, "dvrs": dvrs})

@app.route("/api/dvrs", methods=["POST"])
def add_dvr():
    data = request.get_json(silent=True) or {}
    auto_create = data.get("auto_create_pdvs", True)
    res = DVRManager.add_dvr(data, auto_create_pdvs=auto_create)
    return jsonify(res)

@app.route("/api/dvrs/<dvr_id>", methods=["PUT"])
def update_dvr(dvr_id):
    data = request.get_json(silent=True) or {}
    sync_cams = data.get("sync_linked_cameras", True)
    dvr = DVRManager.update_dvr(dvr_id, data, sync_linked_cameras=sync_cams)
    if dvr:
        return jsonify({"success": True, "dvr": dvr})
    return jsonify({"success": False, "message": "DVR não encontrado"}), 404

@app.route("/api/dvrs/<dvr_id>", methods=["DELETE"])
def delete_dvr(dvr_id):
    delete_pdvs = request.args.get("delete_pdvs", "true").lower() in ("true", "1")
    ok = DVRManager.delete_dvr(dvr_id, delete_associated_pdvs=delete_pdvs)
    return jsonify({"success": ok})

@app.route("/api/dvrs/test_connection", methods=["POST"])
def test_dvr_connection():
    data = request.get_json(silent=True) or {}
    ip = data.get("ip", "")
    port = int(data.get("port") or 554)
    http_port = int(data.get("http_port") or 80)
    res = DVRManager.test_connection(ip, port, http_port)
    return jsonify(res)

@app.route("/api/import_user_media", methods=["POST"])
def import_user_media():
    res = DVRManager.import_user_media()
    return jsonify(res)

@app.route("/api/cameras", methods=["GET"])
def get_cameras():
    cameras = CameraManager.load_cameras()
    total = len(cameras)
    aligned = sum(1 for c in cameras if c.get("last_check_status") == "ALIGNED")
    warning = sum(1 for c in cameras if c.get("last_check_status") == "MILD_DRIFT")
    crooked = sum(1 for c in cameras if c.get("last_check_status") in ("CROOKED", "NO_AISLE"))
    offline = sum(1 for c in cameras if c.get("last_check_status") == "OFFLINE")

    return jsonify({
        "cameras": cameras,
        "stats": {
            "total": total,
            "aligned": aligned,
            "warning": warning,
            "crooked": crooked,
            "offline": offline,
            "compliance_rate": round((aligned / total * 100), 1) if total > 0 else 0.0
        }
    })

@app.route("/api/cameras", methods=["POST"])
def add_camera():
    data = request.get_json(silent=True) or {}
    cam = CameraManager.add_camera(data)
    return jsonify({"success": True, "camera": cam})

@app.route("/api/cameras/<camera_id>", methods=["PUT"])
def update_camera(camera_id):
    data = request.get_json(silent=True) or {}
    cam = CameraManager.update_camera(camera_id, data)
    if cam:
        return jsonify({"success": True, "camera": cam})
    return jsonify({"success": False, "message": "Câmera não encontrada"}), 404

@app.route("/api/cameras/<camera_id>", methods=["DELETE"])
def delete_camera(camera_id):
    ok = CameraManager.delete_camera(camera_id)
    return jsonify({"success": ok})

@app.route("/api/cameras/<camera_id>/scenario", methods=["POST"])
def set_camera_scenario(camera_id):
    data = request.get_json(silent=True) or {}
    scenario = data.get("scenario", "alinhada")
    res = CameraManager.apply_test_scenario(camera_id, scenario)
    return jsonify(res)

@app.route("/api/cameras/<camera_id>/upload_image", methods=["POST"])
def upload_camera_test_image(camera_id):
    if "file" not in request.files:
        return jsonify({"success": False, "message": "Nenhum arquivo enviado"}), 400
    file = request.files["file"]
    as_ref = request.form.get("as_reference", "0") in ("1", "true", "True")
    res = CameraManager.upload_test_image(camera_id, file.read(), as_reference=as_ref)
    return jsonify(res)

@app.route("/api/test_videos", methods=["GET"])
def list_test_videos():
    vid_dir = os.path.join(os.path.dirname(__file__), "test_videos")
    videos = []
    if os.path.exists(vid_dir):
        for f in os.listdir(vid_dir):
            if f.lower().endswith((".mp4", ".avi", ".mkv", ".mov", ".ts")):
                videos.append(f)
    return jsonify({"videos": sorted(videos)})

@app.route("/api/cameras/<camera_id>/assign_video", methods=["POST"])
def assign_camera_video(camera_id):
    data = request.get_json(silent=True) or {}
    video_name = data.get("video_name")
    if not video_name:
        return jsonify({"success": False, "message": "Nome do vídeo ausente"}), 400
    vid_path = os.path.join(os.path.dirname(__file__), "test_videos", video_name)
    if not os.path.exists(vid_path):
        return jsonify({"success": False, "message": "Arquivo de vídeo não encontrado"}), 404
    
    # Atualiza a URL da câmera para o arquivo de vídeo
    CameraManager.update_camera(camera_id, {
        "custom_url": vid_path,
        "is_simulation": False
    })
    # Executa a análise no frame do vídeo
    res = CameraManager.analyze_camera(camera_id)
    return jsonify({"success": True, "analysis": res})

@app.route("/api/cameras/<camera_id>/upload_video", methods=["POST"])
def upload_camera_video(camera_id):
    if "file" not in request.files:
        return jsonify({"success": False, "message": "Nenhum vídeo enviado"}), 400
    file = request.files["file"]
    vid_dir = os.path.join(os.path.dirname(__file__), "test_videos")
    os.makedirs(vid_dir, exist_ok=True)
    safe_name = f"{camera_id}_{file.filename}"
    vid_path = os.path.join(vid_dir, safe_name)
    file.save(vid_path)

    CameraManager.update_camera(camera_id, {
        "custom_url": vid_path,
        "is_simulation": False
    })
    res = CameraManager.analyze_camera(camera_id)
    return jsonify({"success": True, "analysis": res})

# ==============================================================================
# AGENDADOR DE VARREDURA AUTOMÁTICA EM BACKGROUND
# ==============================================================================
import threading

auto_scan_state = {
    "enabled": False,
    "interval_seconds": 60,
    "last_scan": None,
    "total_scans": 0,
    "is_scanning": False
}

def auto_scan_loop():
    while True:
        try:
            if auto_scan_state["enabled"] and not auto_scan_state["is_scanning"]:
                auto_scan_state["is_scanning"] = True
                CameraManager.analyze_all(pull_from_dvr=True)
                auto_scan_state["last_scan"] = time.strftime("%Y-%m-%d %H:%M:%S")
                auto_scan_state["total_scans"] += 1
                auto_scan_state["is_scanning"] = False
        except Exception:
            auto_scan_state["is_scanning"] = False
        time.sleep(auto_scan_state.get("interval_seconds", 60))

scan_thread = threading.Thread(target=auto_scan_loop, daemon=True)
scan_thread.start()

# ------------------------------------------------------------------------------
# WATCHER DO ARQUIVO DO JANUS: no serviço (Ubuntu/Rocky), sempre que o jcfg muda,
# adiciona automaticamente os DVRs/PDVs novos (idempotente; detecta canais ativos).
# ------------------------------------------------------------------------------
JANUS_JCFG_PATHS = [
    "/opt/janus/etc/janus/janus.plugin.streaming.jcfg",
    "/etc/janus/janus.plugin.streaming.jcfg",
    "/usr/local/etc/janus/janus.plugin.streaming.jcfg",
]
JANUS_JCFG = os.environ.get("JANUS_JCFG") or next((p for p in JANUS_JCFG_PATHS if os.path.isfile(p)), "")
JANUS_SYNC_INTERVAL = max(15, int(os.environ.get("JANUS_SYNC_INTERVAL", "60")))
janus_state = {"path": JANUS_JCFG, "last_sync": None, "last_result": None, "last_error": None}

def janus_watch_loop():
    last_mtime = 0.0
    while True:
        try:
            if JANUS_JCFG and os.path.isfile(JANUS_JCFG):
                mt = os.path.getmtime(JANUS_JCFG)
                if mt != last_mtime:  # só re-sincroniza quando o arquivo muda
                    with open(JANUS_JCFG, encoding="utf-8", errors="ignore") as f:
                        res = DVRManager.import_from_janus(f.read())  # idempotente: só adiciona o que falta
                    last_mtime = mt
                    janus_state.update(last_sync=time.strftime("%Y-%m-%d %H:%M:%S"), last_result=res, last_error=None)
                    if res.get("success") and (res.get("dvrs_novos") or res.get("total_pdvs")):
                        print(f"[janus-sync] +{res.get('dvrs_novos',0)} DVR(s), +{res.get('total_pdvs',0)} PDV(s)", flush=True)
        except Exception as e:
            janus_state["last_error"] = str(e)
        time.sleep(JANUS_SYNC_INTERVAL)

if JANUS_JCFG:
    threading.Thread(target=janus_watch_loop, daemon=True).start()
    print(f"[janus-sync] vigiando {JANUS_JCFG} a cada {JANUS_SYNC_INTERVAL}s", flush=True)

@app.route("/api/janus/status", methods=["GET"])
def get_janus_status():
    return jsonify(janus_state)

@app.route("/api/auto_scan/status", methods=["GET"])
def get_auto_scan_status():
    return jsonify(auto_scan_state)

@app.route("/api/auto_scan/toggle", methods=["POST"])
def toggle_auto_scan():
    data = request.get_json(silent=True) or {}
    if "enabled" in data:
        auto_scan_state["enabled"] = bool(data["enabled"])
    if "interval_seconds" in data:
        auto_scan_state["interval_seconds"] = max(10, int(data["interval_seconds"]))
    return jsonify({"success": True, "state": auto_scan_state})

@app.route("/api/dvrs/<dvr_id>/sync", methods=["POST"])
def sync_dvr(dvr_id):
    res = DVRManager.sync_dvr(dvr_id)
    return jsonify(res)

@app.route("/api/dvrs/<dvr_id>/redetect", methods=["POST"])
def redetect_channels(dvr_id):
    """Relê os canais ativos do DVR e corrige os PDVs (nomes reais, remove canais vazios)."""
    res = DVRManager.redetect_channels(dvr_id)
    return jsonify(res), (200 if res.get("success") else 400)

@app.route("/api/mongo/sync_stores", methods=["POST"])
def mongo_sync_stores():
    """Liga DVRs/PDVs às lojas do MongoDB (cliente/loja) pelo IP."""
    res = DVRManager.sync_stores()
    return jsonify(res), (200 if res.get("success") else 400)

@app.route("/api/mongo/fleet_tree", methods=["GET"])
def mongo_fleet_tree():
    """{cliente: [lojas]} de toda a frota no Mongo (lojas com '[' no nome são ignoradas)."""
    from core.mongo_store import clientes_lojas
    return jsonify({"success": True, "tree": clientes_lojas()})

@app.route("/api/mongo/import_fleet", methods=["POST"])
def mongo_import_fleet():
    """Descobre a frota pela lojas_v2 e cadastra os DVRs. Body: {cliente?, limit?, dry_run?}."""
    data = request.get_json(silent=True) or {}
    res = DVRManager.import_from_mongo(cliente=data.get("cliente") or None,
                                       limit=data.get("limit") or None,
                                       dry_run=bool(data.get("dry_run", False)),
                                       loja=data.get("loja") or None)
    return jsonify(res), (200 if res.get("success") else 400)

@app.route("/api/cameras/<camera_id>/calibrate", methods=["POST"])
def calibrate_camera(camera_id):
    res = CameraManager.calibrate_camera(camera_id)
    return jsonify(res)

@app.route("/api/cameras/<camera_id>/analyze", methods=["POST"])
def analyze_camera(camera_id):
    data = request.get_json(silent=True) or {}
    pull_from_dvr = data.get("pull_from_dvr", False)
    res = CameraManager.analyze_camera(camera_id, pull_from_dvr=pull_from_dvr)
    return jsonify(res)

@app.route("/api/cameras/analyze_all", methods=["POST"])
def analyze_all():
    data = request.get_json(silent=True) or {}
    pull_from_dvr = data.get("pull_from_dvr", False)
    results = CameraManager.analyze_all(pull_from_dvr=pull_from_dvr)
    return jsonify({"success": True, "results": results})

@app.route("/api/score_frame", methods=["POST"])
def score_frame():
    """Pontua um JPEG/PNG avulso (ex.: snapshot que o Darwin Connect puxa do DVR pelo
    túnel SSH dele) e devolve o veredito + a imagem com o overlay. Sem referência
    calibrada: usa nível do balcão + IA de corredor — igual à análise ao vivo sem gabarito.
    Protegido por token (header X-Ingest-Token == env INGEST_TOKEN); falha fechado."""
    token = os.environ.get("INGEST_TOKEN", "").strip()
    if not token:
        return jsonify({"success": False, "message": "Ingestão desativada: defina INGEST_TOKEN no serviço."}), 503
    if request.headers.get("X-Ingest-Token", "") != token:
        return jsonify({"success": False, "message": "Token de ingestão inválido."}), 401

    raw = None
    if "file" in request.files:
        raw = request.files["file"].read()
    else:
        data = request.get_json(silent=True) or {}
        b64 = data.get("image_base64") or data.get("image") or ""
        if "," in b64:  # descarta prefixo "data:image/...;base64,"
            b64 = b64.split(",", 1)[1]
        if b64:
            try:
                raw = base64.b64decode(b64)
            except Exception:
                return jsonify({"success": False, "message": "image_base64 inválido."}), 400
    if not raw:
        return jsonify({"success": False, "message": "Envie 'file' (multipart) ou 'image_base64' (JSON)."}), 400

    frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if frame is None or frame.size == 0:
        return jsonify({"success": False, "message": "Não foi possível decodificar a imagem (JPEG/PNG)."}), 400

    diag = VisionEngine.analyze_camera(frame, ref_frame=None)
    annotated = VisionEngine.draw_diagnostic_overlay(
        frame, diag["rotation_deg"], diag["status"], diag["status_text"], diag["status_color"],
        diag["method_used"], diag["confidence"], diag["translation_px"], diag["focus_score"], diag.get("aisle"))
    ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 85])
    aisle = diag.get("aisle")
    return jsonify({
        "success": True,
        "status": diag["status"],
        "status_text": diag["status_text"],
        "rotation_deg": diag["rotation_deg"],
        "translation_px": diag["translation_px"],
        "method_used": diag["method_used"],
        "confidence": diag["confidence"],
        "diag_reason": diag.get("diag_reason"),
        "aisle_verdict": (aisle or {}).get("verdict"),
        "aisle_pct": diag.get("aisle_pct"),
        "aisle_side": diag.get("aisle_side"),
        "pdv_verdict": VisionEngine.pdv_verdict(aisle),
        "annotated_image_base64": (base64.b64encode(buf.tobytes()).decode("ascii") if ok else None),
    })

@app.route("/api/dvrs/import_janus", methods=["POST"])
def import_janus():
    """Importa DVRs/PDVs de um janus.plugin.streaming.jcfg (upload 'file', ou JSON com 'content'/'path')."""
    text = None
    if "file" in request.files:
        text = request.files["file"].read().decode("utf-8", errors="ignore")
    else:
        data = request.get_json(silent=True) or {}
        if data.get("content"):
            text = data["content"]
        elif data.get("path") and os.path.isfile(data["path"]):
            with open(data["path"], encoding="utf-8", errors="ignore") as f:
                text = f.read()
    if not text:
        return jsonify({"success": False, "message": "Envie o arquivo .jcfg (campo 'file') ou 'content'/'path'."}), 400
    res = DVRManager.import_from_janus(text)
    return jsonify(res), (200 if res.get("success") else 400)

@app.route("/api/cameras/import_csv", methods=["POST"])
def import_csv():
    if "file" not in request.files:
        return jsonify({"success": False, "message": "Nenhum arquivo enviado"}), 400
    file = request.files["file"]
    content = file.read().decode("utf-8", errors="ignore")
    count = CameraManager.import_csv(content)
    return jsonify({"success": True, "imported_count": count})

@app.route("/api/cameras/export_csv", methods=["GET"])
def export_csv():
    csv_data = CameraManager.export_csv()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=relatorio_cameras_pdv.csv"}
    )

@app.route("/api/cameras/<camera_id>/snapshot")
def get_snapshot(camera_id):
    path = os.path.join(SNAPSHOTS_DIR, f"{camera_id}.jpg")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return Response(f.read(), mimetype="image/jpeg")
    img = VisionEngine.generate_synthetic_checkout_frame()
    is_success, buffer = cv2.imencode(".jpg", img)
    return Response(buffer.tobytes(), mimetype="image/jpeg")

# ==============================================================================
# MARCAÇÃO DE IMAGENS PARA TREINAR A IA DE BALCÃO/CORREDOR (dataset em ../dataset)
# ==============================================================================
DATASET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dataset")

def _dataset_path(*parts):
    base = os.path.realpath(DATASET_DIR)
    path = os.path.realpath(os.path.join(base, *parts))
    if not path.startswith(base + os.sep):  # nomes vêm da URL: não deixa sair da pasta do dataset
        raise ValueError("caminho inválido")
    return path

@app.route("/rotular")
def rotular():
    return render_template("rotular.html")

@app.route("/api/rotular/cameras")
def rotular_cameras():
    img_dir = os.path.join(DATASET_DIR, "images")
    out = []
    for key in sorted(os.listdir(img_dir)) if os.path.isdir(img_dir) else []:
        files = sorted(f for f in os.listdir(os.path.join(img_dir, key)) if f.lower().endswith(".jpg"))
        if not files:
            continue
        lp = os.path.join(DATASET_DIR, "labels", f"{key}.json")
        label = {}
        if os.path.exists(lp):
            with open(lp, encoding="utf-8") as f:
                label = json.load(f)
        out.append({"key": key, "images": files, "labeled": bool(label) and not label.get("rascunho"),
                    "draft": bool(label.get("rascunho")), "incerteza": label.get("incerteza")})
    # revisar primeiro: sem marcação, depois rascunhos mais incertos; revisadas no fim
    out.sort(key=lambda c: (c["labeled"], c["draft"], -(c["incerteza"] or 0), c["key"]))
    return jsonify({"cameras": out})

@app.route("/api/rotular/imagem/<key>/<name>")
def rotular_imagem(key, name):
    return send_file(_dataset_path("images", key, name), mimetype="image/jpeg")

@app.route("/api/rotular/label/<key>", methods=["GET", "POST"])
def rotular_label(key):
    path = _dataset_path("labels", f"{key}.json")
    if request.method == "GET":
        if not os.path.exists(path):
            return jsonify({})
        with open(path, "r", encoding="utf-8") as f:
            return Response(f.read(), mimetype="application/json")
    data = request.get_json(silent=True) or {}
    label = {k: data.get(k) for k in ("image", "pdv", "corredor", "sem_corredor")}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(label, f, ensure_ascii=False, indent=1)
    return jsonify({"success": True})

@app.route("/ajuste/<camera_id>")
def ajuste(camera_id):
    """Página de celular para o técnico ajustar a câmera olhando o vídeo ao vivo com a guia."""
    cam = CameraManager.get_camera(camera_id)
    if not cam:
        return "Câmera não encontrada", 404
    return render_template("ajuste.html", id=camera_id, name=cam.get("name"), channel=cam.get("channel"),
                           has_ref=os.path.exists(os.path.join(REFERENCES_DIR, f"{camera_id}.jpg")))

@app.route("/api/cameras/<camera_id>/live")
def live_stream(camera_id):
    """Stream MJPEG ao vivo do canal (RTSP do DVR) ou do vídeo local vinculado, em loop."""
    camera = CameraManager.get_camera(camera_id)
    if not camera:
        return jsonify({"success": False, "message": "Câmera não encontrada"}), 404
    source = camera.get("custom_url") or camera.get("rtsp_url")
    if not source:
        return jsonify({"success": False, "message": "Câmera sem URL RTSP ou vídeo"}), 400
    is_file = os.path.isfile(source)
    guide = request.args.get("guide", "1") != "0"
    ref = read_cv2_image(os.path.join(REFERENCES_DIR, f"{camera_id}.jpg")) if guide else None  # cv2.imread falha com "ç" no caminho

    def encode(frame):
        _, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg.tobytes() + b"\r\n"

    state = {"diag": None, "at": 0.0, "edges": None, "size": None}

    def with_guide(frame):
        """Guia para o técnico ajustar: contorno da referência em ciano + ângulo/deslocamento ao vivo."""
        # Tamanho fixo pelo 1º frame (snapshot 16:9). O substream 704x480 é a mesma cena espremida
        # (anamórfico), então esticar para o mesmo tamanho restaura a proporção real.
        if state["size"] is None:
            h, w = frame.shape[:2]
            state["size"] = (960, round(h * 960 / w))
        frame = cv2.resize(frame, state["size"])
        if not guide:
            return frame
        if time.time() - state["at"] > 1.0:  # análise 1x/s; os frames entre elas reaproveitam o resultado
            state["diag"] = VisionEngine.analyze_camera(frame, ref)
            state["at"] = time.time()
        d = state["diag"]
        out = VisionEngine.draw_diagnostic_overlay(
            frame, d["rotation_deg"], d["status"], d["status_text"], d["status_color"],
            d["method_used"], d["confidence"], d["translation_px"], d["focus_score"], d["aisle"])
        if ref is not None:
            h, w = out.shape[:2]
            if state["edges"] is None:
                # Só as retas longas da referência (balcão, esteira, móveis): o contorno completo cobre piso e
                # sombras e polui a tela. Ignora as faixas do OSD do DVR.
                g = cv2.cvtColor(cv2.resize(ref, (w, h)), cv2.COLOR_BGR2GRAY)  # mesmo campo de visão, outra resolução
                edges = cv2.Canny(cv2.GaussianBlur(g, (5, 5), 0), 60, 150)
                osd = int(h * VisionEngine.OSD_MASK_PCT)
                edges[:osd], edges[h - osd:] = 0, 0
                segs = cv2.HoughLinesP(edges, 1, np.pi / 360, threshold=50, minLineLength=int(w * 0.08), maxLineGap=8)
                state["edges"] = [] if segs is None else segs.reshape(-1, 4).tolist()
            for x1, y1, x2, y2 in state["edges"]:
                cv2.line(out, (x1, y1), (x2, y2), (255, 255, 0), 2, cv2.LINE_AA)
        return out

    def frames():
        # O RTSP do DVR leva 6-10s para abrir pela VPN: abre em paralelo e, enquanto isso,
        # manda snapshots HTTP (~1/s) para o técnico já ver a câmera.
        # Lojas com túnel: abre um túnel SSH na porta RTSP (fica aberto durante o stream) e reescreve a URL.
        from core.tunnel import dvr_tunnel
        from core.rtsp_builder import build_rtsp_url
        th = camera.get("tunnel_host") or None
        tm = camera.get("tunnel_mac") or None
        rtsp_port = int(camera.get("port") or 554)
        with dvr_tunnel(th if not is_file else None, tm, camera.get("ip"), rtsp_port) as (rhost, rport):
            if th and not is_file:  # tunelado: refaz a URL RTSP apontando pro endpoint local do túnel
                rtsp_src = build_rtsp_url(brand=camera.get("brand"), ip=rhost, port=rport,
                                          username=camera.get("username"), password=camera.get("password"),
                                          channel=int(camera.get("channel") or 1), substream=bool(camera.get("substream", True)))
            else:
                rtsp_src = source
            holder = {}
            opener = threading.Thread(daemon=True, target=lambda: holder.update(
                cap=cv2.VideoCapture(rtsp_src, cv2.CAP_FFMPEG, [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 10000])))
            opener.start()
            try:
                while not is_file and camera.get("ip") and opener.is_alive():
                    ok, snap, _ = VisionEngine.capture_dvr_snapshot(
                        ip=camera["ip"], http_port=int(camera.get("http_port") or 80),
                        username=camera.get("username") or "admin", password=camera.get("password") or "",
                        channel=int(camera.get("channel") or 1), brand=camera.get("brand") or "intelbras",
                        tunnel_host=th, tunnel_mac=tm)
                    if ok:
                        yield encode(with_guide(snap))
                    else:
                        time.sleep(0.5)
                opener.join()
                cap = holder["cap"]
                fps = cap.get(cv2.CAP_PROP_FPS) or 10
                delay = 1.0 / min(fps, 15) if is_file else 0  # RTSP já chega no ritmo real
                while True:
                    ok, frame = cap.read()
                    if not ok:
                        if not is_file:
                            break  # DVR caiu: navegador mostra a última imagem
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    yield encode(with_guide(frame))
                    if delay:
                        time.sleep(delay)
            finally:  # roda quando o navegador fecha a conexão (inclusive durante a abertura)
                opener.join()
                if "cap" in holder:
                    holder["cap"].release()

    return Response(frames(), mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/api/cameras/<camera_id>/reference")
def get_reference(camera_id):
    path = os.path.join(REFERENCES_DIR, f"{camera_id}.jpg")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return Response(f.read(), mimetype="image/jpeg")
    
    # Se ainda não foi calibrada, exibe a imagem de referência padrão
    sample_ref = os.path.join(os.path.dirname(__file__), "test_images", "pdv_referencia_alinhada.jpg")
    if os.path.exists(sample_ref):
        with open(sample_ref, "rb") as f:
            return Response(f.read(), mimetype="image/jpeg")

    img = VisionEngine.generate_synthetic_checkout_frame(tilt_angle=0.0)
    is_success, buffer = cv2.imencode(".jpg", img)
    return Response(buffer.tobytes(), mimetype="image/jpeg")

if __name__ == "__main__":
    import argparse
    # Como sidecar do Darwin Connect, a porta/host/token vêm do ambiente (o launcher
    # passa 127.0.0.1 + porta livre + INGEST_TOKEN aleatório — nunca exposto na rede).
    # Standalone (gunicorn/systemd) segue igual: sem env, cai nos defaults de antes.
    parser = argparse.ArgumentParser(description="Verificador de Posição de Câmeras de PDV")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "5050")), help="Porta HTTP")
    parser.add_argument("--host", default=os.environ.get("FLASK_HOST", "0.0.0.0"), help="Host do servidor")
    args = parser.parse_args()

    # Sidecar: se o launcher não passou um token, cunha um (CSPRNG) e o anuncia no stdout
    # para o processo pai (Darwin Connect) ler. A rota /api/score_frame lê este mesmo env.
    if not os.environ.get("INGEST_TOKEN", "").strip():
        os.environ["INGEST_TOKEN"] = secrets.token_hex(16)
    print(f"INGEST_READY {os.environ['INGEST_TOKEN']}", flush=True)

    # Sob o Darwin Connect o stdout é um pipe aberto com CREATE_NO_WINDOW; nesse modo o
    # Flask/werkzeug/click estouram OSError [Errno 22] ao escrever o banner e os logs de
    # request, matando o sidecar antes do bind. Se não há terminal (stdout não é tty),
    # silencia stdout/stderr DEPOIS de anunciar o token. Em terminal real, nada muda.
    import sys
    try:
        tem_terminal = sys.stdout.isatty()
    except Exception:
        tem_terminal = False
    if tem_terminal:
        print(f"Iniciando Verificador de Posição em http://{args.host}:{args.port}", flush=True)
    else:
        _null = open(os.devnull, "w")
        sys.stdout = _null
        sys.stderr = _null
    app.run(host=args.host, port=args.port, debug=False)
