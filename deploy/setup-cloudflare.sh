#!/bin/bash
# Expõe o painel (localhost:5050) pela SUA Cloudflare com um túnel NOMEADO, no seu domínio.
# Pré-requisitos: o domínio já estar na sua conta Cloudflare (nameservers na CF).
#
# Uso (no OptiPlex, com o serviço verificador já rodando):
#   sudo HOSTNAME_PUBLICO=verificador.seudominio.com bash deploy/setup-cloudflare.sh
#   (opcional) TUNNEL=nome-do-tunel  (padrão: verificador)
set -euo pipefail

HOST="${HOSTNAME_PUBLICO:-}"
TUNNEL="${TUNNEL:-verificador}"
if [ -z "$HOST" ]; then
    echo "ERRO: defina o hostname. Ex.: sudo HOSTNAME_PUBLICO=verificador.seudominio.com bash deploy/setup-cloudflare.sh"
    exit 1
fi

# 1. Instala o cloudflared (binário oficial; serve Ubuntu 26.04 e Rocky)
if ! command -v cloudflared >/dev/null; then
    curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /usr/local/bin/cloudflared
    chmod +x /usr/local/bin/cloudflared
fi
cloudflared --version

# 2. Login na SUA conta Cloudflare (abre uma URL no navegador p/ autorizar e escolher o domínio).
#    Guarda o certificado em ~/.cloudflared/cert.pem — só precisa fazer uma vez.
if [ ! -f "$HOME/.cloudflared/cert.pem" ] && [ ! -f /root/.cloudflared/cert.pem ]; then
    echo ">> Vai aparecer uma URL. Abra no navegador, faça login na SUA Cloudflare e autorize o domínio."
    cloudflared tunnel login
fi

# 3. Cria o túnel (ou reutiliza se já existir)
if ! cloudflared tunnel list 2>/dev/null | grep -qw "$TUNNEL"; then
    cloudflared tunnel create "$TUNNEL"
fi
UUID="$(cloudflared tunnel list 2>/dev/null | awk -v t="$TUNNEL" '$2==t{print $1}')"
CRED="$(ls -1 "$HOME"/.cloudflared/"$UUID".json /root/.cloudflared/"$UUID".json 2>/dev/null | head -1)"

# 4. Config do túnel (ingress -> painel local). O streaming MJPEG precisa de timeouts longos / sem buffer.
mkdir -p /etc/cloudflared
cp -f "$CRED" /etc/cloudflared/"$UUID".json
cat > /etc/cloudflared/config.yml <<EOF
tunnel: $UUID
credentials-file: /etc/cloudflared/$UUID.json
ingress:
  - hostname: $HOST
    service: http://localhost:5050
    originRequest:
      connectTimeout: 30s
      # vídeo ao vivo (MJPEG) é conexão longa: não deixar o origin cair
      noHappyEyeballs: true
      keepAliveTimeout: 90s
  - service: http_status:404
EOF

# 5. Cria o registro DNS (CNAME) do hostname apontando pro túnel
cloudflared tunnel route dns "$TUNNEL" "$HOST" || true

# 6. Instala como serviço (sobe no boot). Usa /etc/cloudflared/config.yml
cloudflared service install 2>/dev/null || true
systemctl enable --now cloudflared 2>/dev/null || systemctl restart cloudflared || true

echo ""
echo "Pronto. Painel público (saída-only, sem abrir entrada):"
echo "   https://$HOST"
echo ""
echo "IMPORTANTE (segurança): o painel NÃO tem login. Antes de usar de verdade, ligue o"
echo "Cloudflare Access (Zero Trust) em https://one.dash.cloudflare.com → Access → Applications,"
echo "criando uma política de e-mails permitidos para $HOST. Sem isso, qualquer um com o link entra."
