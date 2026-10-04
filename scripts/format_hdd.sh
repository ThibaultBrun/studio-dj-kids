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

echo ">> Terminé"
lsblk -f "$DISK"
df -h /data
