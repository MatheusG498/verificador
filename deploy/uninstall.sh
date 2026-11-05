#!/bin/bash
# Remove o serviço Verificador de Posição. Ubuntu e Rocky/RHEL.
# Uso:  sudo bash deploy/uninstall.sh            (remove o serviço, mantém /opt/verificador)
#       sudo bash deploy/uninstall.sh --purge    (remove também /opt/verificador e o usuário)
set -euo pipefail
PURGE="${1:-}"

# Para e desabilita o serviço
systemctl disable --now verificador 2>/dev/null || true
rm -f /etc/systemd/system/verificador.service
rm -rf /etc/systemd/system/verificador.service.d
systemctl daemon-reload 2>/dev/null || true

# Fecha a porta 5050 no firewall do host, se tiver sido aberta
if command -v firewall-cmd >/dev/null && systemctl is-active --quiet firewalld; then
    firewall-cmd --permanent --remove-port=5050/tcp 2>/dev/null || true
    firewall-cmd --reload 2>/dev/null || true
fi
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw delete allow 5050/tcp 2>/dev/null || true
fi

if [ "$PURGE" = "--purge" ]; then
    rm -rf /opt/verificador
    id verificador >/dev/null 2>&1 && userdel verificador 2>/dev/null || true
    echo "Removido com --purge: serviço, /opt/verificador (dados, referências, config) e usuário 'verificador'."
else
    echo "Serviço removido. /opt/verificador foi MANTIDO (dados/config). Use --purge para apagar tudo."
fi
