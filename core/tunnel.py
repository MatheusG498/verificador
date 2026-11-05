"""
Túnel SSH sob demanda pra alcançar o DVR na LAN da loja, que só a máquina da loja enxerga.
Caminho: central --ssh--> ip_openvpn (10.8.x) --direct-tcpip--> ip_dvr (LAN local da loja).

Usa paramiko direto (port-forward local). Usuário padrão 'inwave', senha = 'eb' + mac da loja.
O ip_openvpn muda de tempo em tempo -> resolve o ATUAL pelo mac (estável) no MongoDB a cada uso.
Sob demanda: abre no uso e fecha ao sair (context manager).
"""
import os
import select
import socketserver
import threading
from contextlib import contextmanager
from typing import Optional

SSH_USER = os.environ.get("TUNNEL_SSH_USER", "inwave")
SSH_PORT = int(os.environ.get("TUNNEL_SSH_PORT", "22"))


def senha_loja(mac: str) -> str:
    """Senha SSH da máquina da loja: 'eb' + mac (sem separadores)."""
    return "eb" + (mac or "").replace(":", "").replace("-", "").strip()


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        srv = self.server
        try:
            chan = srv.ssh_transport.open_channel(
                "direct-tcpip", (srv.remote_host, srv.remote_port), self.request.getpeername())
        except Exception:
            return
        if chan is None:
            return
        try:
            while True:
                r, _, _ = select.select([self.request, chan], [], [], 2.0)
                if self.request in r:
                    data = self.request.recv(32768)
                    if not data:
                        break
                    chan.sendall(data)
                if chan in r:
                    data = chan.recv(32768)
                    if not data:
                        break
                    self.request.sendall(data)
        except Exception:
            pass
        finally:
            try:
                chan.close()
            except Exception:
                pass
            try:
                self.request.close()
            except Exception:
                pass


class _ForwardServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


@contextmanager
def dvr_tunnel(ssh_host: Optional[str], mac: Optional[str], dvr_ip: str, dvr_port: int = 80, timeout: float = 15.0):
    """Abre um túnel SSH (port-forward local) até dvr_ip:dvr_port da loja e devolve ('127.0.0.1', porta_local).
    Fecha ao sair do with. Sem host (ou sem paramiko) -> devolve (dvr_ip, dvr_port) direto (sem túnel)."""
    if not dvr_ip:
        yield (dvr_ip, dvr_port)
        return
    # ip_openvpn é dinâmico: resolve o ATUAL pelo mac (estável); ssh_host é só fallback
    host = ssh_host
    if mac:
        try:
            from core.mongo_store import openvpn_by_mac
            fresh = openvpn_by_mac(mac)
            if fresh:
                host = fresh
        except Exception:
            pass
    if not host:
        yield (dvr_ip, dvr_port)
        return
    try:
        import paramiko
    except Exception:
        yield (dvr_ip, dvr_port)
        return

    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    srv = None
    try:
        cli.connect(host, port=SSH_PORT, username=SSH_USER, password=senha_loja(mac),
                    timeout=timeout, banner_timeout=timeout, auth_timeout=timeout,
                    allow_agent=False, look_for_keys=False)
        transport = cli.get_transport()
        transport.set_keepalive(5)
        srv = _ForwardServer(("127.0.0.1", 0), _Handler)
        srv.ssh_transport = transport
        srv.remote_host = dvr_ip
        srv.remote_port = int(dvr_port)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        yield ("127.0.0.1", srv.server_address[1])
    finally:
        if srv is not None:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:
                pass
        try:
            cli.close()
        except Exception:
            pass
