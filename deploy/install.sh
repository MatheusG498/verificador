#!/bin/bash
# Instala o Verificador como serviço systemd. Ubuntu 20.04+ (inclui 26.04) e Rocky/RHEL 9.
# Uso (na pasta verificador_posicao copiada para o servidor):
#   sudo bash deploy/install.sh                 # instala carregando os dados atuais (data/)
#   sudo bash deploy/install.sh --sem-dados      # instala LIMPO (sem DVRs/câmeras) — p/ servidor central
#   sudo MONGO_URI="mongodb+srv://..." bash deploy/install.sh --sem-dados   # já configura o Mongo
set -euo pipefail
cd "$(dirname "$0")/.."
SEM_DADOS="${1:-}"

if command -v apt-get >/dev/null; then
    apt-get update && apt-get install -y python3 python3-venv rsync
elif command -v dnf >/dev/null; then
    dnf install -y python3 rsync
fi

id verificador >/dev/null 2>&1 || useradd --system --home /opt/verificador --shell /usr/sbin/nologin verificador
mkdir -p /opt/verificador
# --sem-dados: não copia data/ (começa sem DVRs/câmeras; a frota vem do Mongo depois)
if [ "$SEM_DADOS" = "--sem-dados" ]; then
    rsync -a --exclude venv --exclude __pycache__ --exclude data ./ /opt/verificador/
    mkdir -p /opt/verificador/data
    # começa vazio (listas []) p/ NÃO semear câmeras/DVRs demo; a frota vem do Mongo
    echo '[]' > /opt/verificador/data/cameras.json
    echo '[]' > /opt/verificador/data/dvrs.json
else
    rsync -a --exclude venv --exclude __pycache__ ./ /opt/verificador/
fi

python3 -m venv /opt/verificador/venv
/opt/verificador/venv/bin/pip install --upgrade pip
/opt/verificador/venv/bin/pip install -r /opt/verificador/requirements.txt
chown -R verificador:verificador /opt/verificador

cp deploy/verificador.service /etc/systemd/system/
# MONGO_URI passado no ambiente vira um drop-in do systemd (não grava credencial no repo copiado)
if [ -n "${MONGO_URI:-}" ]; then
    mkdir -p /etc/systemd/system/verificador.service.d
    printf '[Service]\nEnvironment=MONGO_URI=%s\n' "$MONGO_URI" > /etc/systemd/system/verificador.service.d/env.conf
    echo "MONGO_URI configurado via drop-in do systemd."
fi
systemctl daemon-reload
systemctl enable --now verificador

# Libera a porta 5050 no firewall do host (acesso pela LAN/OpenVPN). Rocky = firewalld, Ubuntu = ufw.
if command -v firewall-cmd >/dev/null && systemctl is-active --quiet firewalld; then
    firewall-cmd --permanent --add-port=5050/tcp && firewall-cmd --reload
fi
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow 5050/tcp || true
fi

systemctl --no-pager status verificador | head -5
echo ""
echo "Pronto. Acesse o painel:"
# mostra todos os IPs (inclui o da interface OpenVPN, ex.: tun0)
for ip in $(hostname -I); do echo "   http://$ip:5050"; done
echo "Pela OpenVPN: use o IP da interface tun/VPN acima."
