#!/bin/bash
# Разовая подготовка VDS (Ubuntu) под бота. Повторный запуск безопасен. От root:
#   sudo bash deploy/setup-vds.sh "ssh-ed25519 AAAA... beout-github-actions"
# Секреты (/etc/beout/*) и базу кладут отдельно, см. README.
set -euo pipefail
deploy_key=$1
cd "$(dirname "$0")"

if ! command -v cloudflared >/dev/null; then
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg
    echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' \
        > /etc/apt/sources.list.d/cloudflared.list
    apt-get update -q
    apt-get install -yq cloudflared
fi
apt-get install -yq python3-venv

# beout запускает бота и видит только свои данные; beout-deploy может лишь выложить релиз.
id beout >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /srv/beout/data --shell /usr/sbin/nologin beout
id beout-deploy >/dev/null 2>&1 || useradd --create-home --shell /bin/bash beout-deploy

install -d -o beout-deploy -g beout-deploy -m 755 /srv/beout /srv/beout/releases
install -d -o beout -g beout -m 700 /srv/beout/data
install -d -o root -g beout -m 750 /etc/beout
[[ -x /srv/beout/venv/bin/python ]] || sudo -u beout-deploy python3 -m venv /srv/beout/venv

install -o root -g root -m 755 beout-deploy /usr/local/bin/beout-deploy
install -o root -g root -m 644 beout.service beout-tunnel.service /etc/systemd/system/
echo 'beout-deploy ALL=(root) NOPASSWD: /usr/bin/systemctl restart beout' > /etc/sudoers.d/beout-deploy
chmod 440 /etc/sudoers.d/beout-deploy
visudo -cf /etc/sudoers.d/beout-deploy

install -d -o beout-deploy -g beout-deploy -m 700 /home/beout-deploy/.ssh
echo "restrict,command=\"/usr/local/bin/beout-deploy\" $deploy_key" > /home/beout-deploy/.ssh/authorized_keys
chown beout-deploy:beout-deploy /home/beout-deploy/.ssh/authorized_keys
chmod 600 /home/beout-deploy/.ssh/authorized_keys

systemctl daemon-reload
systemctl enable beout beout-tunnel
