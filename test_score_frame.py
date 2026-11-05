"""Check do /api/score_frame: porta de token + caminho feliz num quadro sintético.
Roda: python test_score_frame.py  (precisa do opencv e do models/pdv_corredor.onnx)."""
import os
os.environ["INGEST_TOKEN"] = "teste-123"

import cv2
import numpy as np
from app import app

c = app.test_client()

# imagem sintética (balcão claro embaixo) -> JPEG
img = np.full((480, 704, 3), 40, np.uint8)
cv2.rectangle(img, (0, 300), (704, 480), (200, 200, 200), -1)
jpg = cv2.imencode(".jpg", img)[1].tobytes()

# sem token -> 401
assert c.post("/api/score_frame", json={"image_base64": ""}, headers={}).status_code in (400, 401)
# token errado -> 401
assert c.post("/api/score_frame", json={}, headers={"X-Ingest-Token": "x"}).status_code == 401
# token certo + imagem -> 200 com veredito e overlay
r = c.post("/api/score_frame",
           data={"file": (__import__("io").BytesIO(jpg), "f.jpg")},
           headers={"X-Ingest-Token": "teste-123"},
           content_type="multipart/form-data")
assert r.status_code == 200, (r.status_code, r.get_data(as_text=True))
j = r.get_json()
assert j["success"] and j["status"] and j["annotated_image_base64"], j
print("OK:", j["status"], j["status_text"], "| pdv:", j["pdv_verdict"], "| aisle:", j["aisle_verdict"])
