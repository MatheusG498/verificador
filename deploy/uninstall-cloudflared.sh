#!/bin/bash
# Remove completamente o cloudflared (binário, serviço, processos e configs). Ubuntu e Rocky/RHEL.
# Uso:  sudo bash deploy/uninstall-cloudflared.sh
set -euo pipefail

# 1. Para o serviço, se tiver sido instalado como serviço (cloudflared service install)
systemctl disable --now cloudflared 2>/dev/null || true
rm -f /etc/systemd/system/cloudflared.service /etc/systemd/system/multi-user.target.wants/cloudflared.service 2>/dev/null || true
systemctl daemon-reload 2>/dev/null || true

# 2. Mata qualquer túnel rodando em foreground/background
pkill -f 'cloudflared tunnel' 2>/dev/null || true
pkill -x cloudflared 2>/dev/null || true

# 3. Remove o binário (instalação manual em /usr/local/bin ou /usr/bin)
rm -f /usr/local/bin/cloudflared /usr/bin/cloudflared

# 4. Remove configs e credenciais de túnel
rm -rf /etc/cloudflared ~/.cloudflared /root/.cloudflared 2>/dev/null || true

# 5. Se foi instalado por pacote (repo oficial), remove o pacote também
if command -v apt-get >/dev/null && dpkg -l 2>/dev/null | grep -q '^ii.*cloudflared'; then
    apt-get remove -y cloudflared 2>/dev/null || true
elif command -v dnf >/dev/null && rpm -q cloudflared >/dev/null 2>&1; then
    dnf remove -y cloudflared 2>/dev/null || true
fi

echo "cloudflared removido: serviço, processos, binário e configs."
command -v cloudflared >/dev/null && echo "Atenção: ainda há um 'cloudflared' em $(command -v cloudflared) — remova manualmente." || echo "Nenhum cloudflared restante no PATH."
