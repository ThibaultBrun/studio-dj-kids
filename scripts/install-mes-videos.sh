#!/bin/bash
# Installe Mes Vidéos (YouTube en liste blanche) et l'historique des sites pour le compte de l'enfant.
# Usage (depuis un compte administrateur, dans le dossier du dépôt) :
#   sudo CG_AGENT_TOKEN=… ./scripts/install-mes-videos.sh <compte-enfant>
# Le jeton vient du serveur Charly Guard (fichier .env sur le VPS). Sans CG_AGENT_TOKEN,
# le réglage déjà présent dans /etc/studio-dj-kids/guard.conf est gardé.
set -euo pipefail

CHILD=${1:?"Usage : sudo CG_AGENT_TOKEN=… $0 <compte-enfant>"}
REPO=$(cd "$(dirname "$0")/.." && pwd)
CHILD_HOME=$(getent passwd "$CHILD" | cut -d: -f6)
CONF=/etc/studio-dj-kids/guard.conf
[ "$(id -u)" = 0 ] || { echo "À lancer avec sudo"; exit 1; }
[ -d "$CHILD_HOME" ] || { echo "Compte $CHILD introuvable"; exit 1; }

echo ">> Lecteur vidéo"
apt-get install -y -qq mpv

echo ">> Réglages du serveur Charly Guard ($CONF)"
install -d -m 755 /etc/studio-dj-kids
if [ -n "${CG_AGENT_TOKEN:-}" ]; then
    printf 'CG_BASE=%s\nCG_AGENT_TOKEN=%s\n' "${CG_BASE:-https://guard.tbrun.dev}" "$CG_AGENT_TOKEN" > "$CONF"
fi
[ -f "$CONF" ] || { echo "Il manque CG_AGENT_TOKEN (voir l'en-tête du script)"; exit 1; }
# L'enfant peut lire le jeton (l'appli en a besoin) mais pas le changer
chown root:"$CHILD" "$CONF"
chmod 640 "$CONF"

echo ">> Historique des sites (domaines + heures, seulement pendant la session de $CHILD)"
install -m 755 "$REPO/mes-videos/charly-dns-log.py" /usr/local/sbin/charly-dns-log
sed "s#@CHILD@#$CHILD#" "$REPO/mes-videos/charly-dns-log.service" > /etc/systemd/system/charly-dns-log.service
systemctl daemon-reload
systemctl enable --now charly-dns-log.service
systemctl restart charly-dns-log.service

echo ">> Horaires réglés depuis la page admin (remplacent le couvre-feu fixe 8h-20h)"
install -m 755 "$REPO/mes-videos/charly-horaires.py" /usr/local/sbin/charly-horaires
sed "s#@CHILD@#$CHILD#" "$REPO/mes-videos/charly-horaires.service" > /etc/systemd/system/charly-horaires.service
install -m 644 "$REPO/mes-videos/charly-horaires.timer" /etc/systemd/system/charly-horaires.timer
systemctl daemon-reload
systemctl disable --now charly-couvre-feu-avertir.timer charly-couvre-feu-fermer.timer 2>/dev/null || true
systemctl enable --now charly-horaires.timer

echo ">> Appli et raccourci"
sudo -u "$CHILD" -H ln -sfn "$REPO/mes-videos" "$CHILD_HOME/.local/share/mes-videos"
DESKTOP=$(sudo -u "$CHILD" -H xdg-user-dir DESKTOP)
sed "s#/home/djpiloupilou#$CHILD_HOME#g" "$REPO/desktop/mes-videos.desktop" > "$CHILD_HOME/.local/share/applications/mes-videos.desktop"
cp "$CHILD_HOME/.local/share/applications/mes-videos.desktop" "$DESKTOP/"
chown "$CHILD:$CHILD" "$CHILD_HOME/.local/share/applications/mes-videos.desktop" "$DESKTOP/mes-videos.desktop"
chmod +x "$DESKTOP/mes-videos.desktop"

echo ">> Terminé : « Mes Vidéos » est sur le bureau de $CHILD."
