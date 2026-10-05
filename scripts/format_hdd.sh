#!/bin/bash
# Formate le disque dur 1 To (/dev/sda) en une seule partition ext4 "Donnees" montée sur /data
set -euo pipefail
DISK=/dev/sda
USER_NAME=djpiloupilou

MODEL=$(lsblk -dno MODEL "$DISK" | xargs)
[ "$MODEL" = "ST1000LM035-1RK172" ] || { echo "ABANDON: $DISK est '$MODEL', pas le disque attendu"; exit 1; }
findmnt -rno SOURCE | grep -q "^$DISK" && { echo "ABANDON: une partition de $DISK est montée"; exit 1; }

echo ">> Effacement des anciennes partitions"
for p in ${DISK}?*; do wipefs -a "$p" || true; done
wipefs -a "$DISK"

echo ">> Création d'une partition unique"
parted -s "$DISK" mklabel gpt mkpart Donnees ext4 0% 100%
udevadm settle
PART=${DISK}1

echo ">> Formatage ext4"
mkfs.ext4 -F -q -L Donnees -m 0 "$PART"

echo ">> Montage automatique sur /data"
UUID=$(blkid -s UUID -o value "$PART")
mkdir -p /data
sed -i '\#[[:space:]]/data[[:space:]]#d' /etc/fstab
echo "UUID=$UUID /data ext4 defaults,noatime,nofail 0 2" >> /etc/fstab
systemctl daemon-reload
mount /data
chown "$USER_NAME:$USER_NAME" /data

echo ">> Dossiers de l'enfant sur le disque dur (musiques, projets LMMS, documents…)"
USER_HOME=$(getent passwd "$USER_NAME" | cut -d: -f6)
for d in Musique Images Vidéos Téléchargements Documents lmms; do
    if [ -d "$USER_HOME/$d" ] && [ ! -L "$USER_HOME/$d" ]; then mv "$USER_HOME/$d" "/data/$d"; fi
    mkdir -p "/data/$d"
    chown "$USER_NAME:$USER_NAME" "/data/$d"
    sudo -u "$USER_NAME" ln -sfn "/data/$d" "$USER_HOME/$d"
done

echo ">> Terminé"
lsblk -f "$DISK"
df -h /data
