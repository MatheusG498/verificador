"""
Lê um janus.plugin.streaming.jcfg (formato libconfig do Janus) e extrai os DVRs/câmeras dos mountpoints RTSP.

Cada mountpoint `type = "rtsp"` com `url = "rtsp://..."` vira uma câmera; agrupadas por IP viram um DVR.
Do URL tira ip/porta/canal/subtype/marca e, se não estiver no URL, usa rtsp_user/rtsp_pwd do bloco.

Formato tolerante: aceita com e sem ponto-e-vírgula (o jcfg do EASiBox não usa), comentários # e // (fora de aspas)
e blocos /* */. Autoteste: `python core/janus_import.py`.
"""
import re
from typing import Any, Dict, List, Optional


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)  # blocos /* */
    out = []
    for line in text.splitlines():
        q, buf, i = False, [], 0
        while i < len(line):
            c = line[i]
            if c == '"':
                q = not q
            elif not q and (c == "#" or (c == "/" and line[i + 1:i + 2] == "/")):
                break  # "//" dentro de rtsp:// está entre aspas, então q=True e não corta aqui
            buf.append(c)
            i += 1
        out.append("".join(buf))
    return "\n".join(out)


def _parse_kv(body: str) -> Dict[str, Any]:
    d: Dict[str, Any] = {}
    for stmt in re.split(r"[;\n]", body):
        m = re.match(r"\s*([A-Za-z0-9_\-]+)\s*=\s*(.+?)\s*$", stmt)
        if not m:
            continue
        k, v = m.group(1), m.group(2).strip().rstrip(";").strip()
        if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
            d[k] = v[1:-1]
        elif v in ("true", "false"):
            d[k] = v == "true"
        else:
            try:
                d[k] = int(v)
            except ValueError:
                d[k] = v
    return d


def parse_jcfg(text: str) -> List[Dict[str, Any]]:
    """Blocos nomeados do jcfg -> [{'_name': <bloco>, <chaves>...}]. Mountpoints são planos (sem sub-blocos)."""
    text = _strip_comments(text)
    blocks, i, n = [], 0, len(text)
    name_re = re.compile(r"([A-Za-z0-9_\-]+)\s*:\s*\{")
    while True:
        m = name_re.search(text, i)
        if not m:
            break
        depth, j = 1, m.end()
        while j < n and depth:
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            j += 1
        block = _parse_kv(text[m.end():j - 1])
        block["_name"] = m.group(1)
        blocks.append(block)
        i = j
    return blocks


def mountpoint_conn(mp: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Mountpoint RTSP -> dados de conexão (ip/porta/canal/subtype/marca/usuário/senha). None se não for RTSP válido."""
    if mp.get("type") != "rtsp":
        return None
    m = re.match(r"rtsp://(?:([^:@/]+)(?::([^@/]*))?@)?([^:/]+)(?::(\d+))?(/.*)?$", mp.get("url", ""))
    if not m:
        return None
    uuser, upwd, host, uport, path = m.groups()
    path = path or ""
    channel, subtype, brand = 1, 0, "generic"
    q = re.search(r"channel=(\d+)", path)
    if "/cam/realmonitor" in path or q:            # Intelbras/Dahua
        brand = "intelbras"
        channel = int(q.group(1)) if q else 1
        s = re.search(r"subtype=(\d+)", path)
        subtype = int(s.group(1)) if s else 0
    else:
        hk = re.search(r"/[Ss]treaming/[Cc]hannels/(\d+)", path)   # Hikvision: <camera><stream>, ex. 102 = cam1 sub
        if hk:
            brand, num = "hikvision", int(hk.group(1))
            channel = num // 100 if num >= 100 else num
            subtype = 1 if num % 100 == 2 else 0
    return {
        "ip": host,
        "port": int(uport or 554),
        "username": mp.get("rtsp_user") or uuser or "admin",
        "password": mp.get("rtsp_pwd") or upwd or "",
        "channel": channel,
        "subtype": subtype,
        "brand": brand,
        "mount_id": str(mp.get("id") or mp.get("_name") or ""),
    }


def extract_cameras(text: str) -> List[Dict[str, Any]]:
    """jcfg -> lista de conexões de câmera (uma por mountpoint RTSP)."""
    return [c for c in (mountpoint_conn(mp) for mp in parse_jcfg(text)) if c]


def parse_channel_titles(text: str, brand: str = "intelbras") -> Dict[int, str]:
    """Resposta da API de nomes de canais do DVR -> {canal(base 1) -> nome}. Canais vazios ('---') ficam de fora.
    Intelbras/Dahua: linhas 'table.ChannelTitle[i].Name=...' (i base 0). Hikvision: XML com <id>/<name>."""
    titles: Dict[int, str] = {}
    if (brand or "").lower() == "hikvision":
        # ISAPI (XML), por bloco de canal: VideoInputChannel (entradas analógicas BNC) ou InputProxyChannel
        # (câmeras IP de NVR). O <id> é o número usado no RTSP (/Streaming/Channels/<id>01): analógico 1..16,
        # IP de NVR 17.. — build_rtsp_url já monta certo. Pula entrada analógica sem câmera (videoInputEnabled=false).
        for m in re.finditer(r"<(VideoInputChannel|InputProxyChannel)\b.*?</\1>", text, re.S):
            block = m.group(0)
            en = re.search(r"<videoInputEnabled>\s*(true|false)\s*</videoInputEnabled>", block)
            if en and en.group(1).lower() == "false":
                continue
            idm = re.search(r"<id>\s*(\d+)\s*</id>", block)
            nm = re.search(r"<name>(.*?)</name>", block)
            if idm and nm and nm.group(1).strip() and nm.group(1).strip() != "---":
                titles[int(idm.group(1))] = nm.group(1).strip()
    else:
        for line in text.splitlines():
            m = re.match(r"table\.ChannelTitle\[(\d+)\]\.Name=(.*)$", line.strip())
            if m:
                nome = m.group(2).strip()
                if nome and nome != "---":
                    titles[int(m.group(1)) + 1] = nome   # índice base 0 -> canal base 1
    return titles


if __name__ == "__main__":
    # Autoteste: os dois padrões (Intelbras e Hikvision), sem ponto-e-vírgula, com creds no bloco e no URL.
    amostra = """
general: { string_ids = true }
dvr: { type = "rtp"  id = "dvr"  video = true }   # não é câmera, deve ser ignorado
# comentado, deve ser ignorado
# live_x: { type = "rtsp"  id = "pos_x"  url = "rtsp://10.0.0.9:554/x" }
live_1: {
    type = "rtsp"
    id = "pos_1"
    url = "rtsp://192.168.1.100:554/Streaming/channels/401"
    rtsp_user = "admin"
    rtsp_pwd = "senha123"
}
live_2: {
    type = "rtsp"
    id = "pos_2"
    url = "rtsp://op:pw@10.59.151.131:554/cam/realmonitor?channel=4&subtype=1"
}
"""
    cams = extract_cameras(amostra)
    assert len(cams) == 2, cams
    hik, intel = cams
    assert hik["brand"] == "hikvision" and hik["channel"] == 4 and hik["subtype"] == 0, hik  # 402 -> cam4 main
    assert hik["username"] == "admin" and hik["password"] == "senha123" and hik["ip"] == "192.168.1.100", hik
    assert intel["brand"] == "intelbras" and intel["channel"] == 4 and intel["subtype"] == 1, intel
    assert intel["username"] == "op" and intel["password"] == "pw", intel  # creds do próprio URL
    # o "//" do rtsp:// não pode ser confundido com comentário
    assert intel["ip"] == "10.59.151.131", intel

    # nomes de canais: Intelbras (base 0 -> canal base 1), '---' ignorado; Hikvision XML
    intel_resp = ("table.ChannelTitle[0].Name=PDV 05\n"
                  "table.ChannelTitle[3].Name=PDV 08\n"
                  "table.ChannelTitle[9].Name=---\n")
    t = parse_channel_titles(intel_resp, "intelbras")
    assert t == {1: "PDV 05", 4: "PDV 08"}, t   # canal 4 = "PDV 08"; canal 10 ('---') fora
    # Analógico desabilitado (BNC sem câmera) é pulado; só os habilitados entram, com o id como canal.
    hik_analog = ("<VideoInputChannelList>"
                  "<VideoInputChannel><id>1</id><inputPort>1</inputPort><videoInputEnabled>false</videoInputEnabled><name>Camera 01</name></VideoInputChannel>"
                  "<VideoInputChannel><id>2</id><inputPort>2</inputPort><videoInputEnabled>true</videoInputEnabled><name>Caixa 02</name></VideoInputChannel>"
                  "</VideoInputChannelList>")
    assert parse_channel_titles(hik_analog, "hikvision") == {2: "Caixa 02"}, parse_channel_titles(hik_analog, "hikvision")
    # NVR com câmeras IP (InputProxy): id 17.. é o canal do RTSP (/Streaming/Channels/1701), nome real preservado
    hik_ip = ("<InputProxyChannelList size=\"2\">"
              "<InputProxyChannel><id>17</id><name>PDV 1</name>"
              "<sourceInputPortDescriptor><userName>admin</userName><srcInputPort>1</srcInputPort></sourceInputPortDescriptor></InputProxyChannel>"
              "<InputProxyChannel><id>18</id><name>PDV 2</name>"
              "<sourceInputPortDescriptor><userName>admin</userName><srcInputPort>2</srcInputPort></sourceInputPortDescriptor></InputProxyChannel>"
              "</InputProxyChannelList>")
    assert parse_channel_titles(hik_ip, "hikvision") == {17: "PDV 1", 18: "PDV 2"}, parse_channel_titles(hik_ip, "hikvision")
    print("janus_import: parser OK (Intelbras + Hikvision, com/sem creds no URL, sem ';', // preservado)")
    print("janus_import: nomes de canais OK (Intelbras base0->1 e '---' fora; Hikvision XML)")
