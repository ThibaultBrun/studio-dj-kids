#!/bin/bash
# Installe Studio DJ Kids pour le compte de l'enfant.
# Usage (depuis un compte administrateur, dans le dossier du dépôt) :
#   sudo ./scripts/install.sh <compte-enfant>
# Le dépôt doit être lisible par l'enfant (par ex. dans /opt/studio-dj-kids).
# Le formatage d'un disque de données est à part : voir scripts/format_hdd.sh.
set -euo pipefail

CHILD=${1:?"Usage : sudo $0 <compte-enfant>"}
REPO=$(cd "$(dirname "$0")/.." && pwd)
CHILD_HOME=$(getent passwd "$CHILD" | cut -d: -f6)
[ "$(id -u)" = 0 ] || { echo "À lancer avec sudo"; exit 1; }
[ -d "$CHILD_HOME" ] || { echo "Compte $CHILD introuvable"; exit 1; }
as_child() { sudo -u "$CHILD" -H bash -c "$1"; }

echo ">> Paquets"
apt-get install -y -qq mixxx ffmpeg curl unzip git build-essential cmake alsa-utils \
    python3-pyqt6 python3-mutagen libfluidsynth3 fluid-soundfont-gm \
    lmms hydrogen gcompris-qt tuxpaint tuxmath ktouch stellarium kgeography marble kturtle khangman blinken

echo ">> Port MIDI virtuel pour que Bip pilote Mixxx"
echo snd-virmidi > /etc/modules-load.d/bip-virmidi.conf
echo "options snd-virmidi midi_devs=1 id=VirMIDI" > /etc/modprobe.d/bip-virmidi.conf
modprobe snd-virmidi midi_devs=1 id=VirMIDI || true

echo ">> IA locale (Ollama + Gemma 3)"
command -v ollama >/dev/null || curl -fsSL https://ollama.com/install.sh | sh
ollama pull gemma3:4b

echo ">> Voix de Bip (Piper, voix Tom)"
VOIX="$REPO/bip/voix"
mkdir -p "$VOIX"
if [ ! -x "$VOIX/piper/piper" ]; then
    curl -fsSL https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_x86_64.tar.gz | tar xz -C "$VOIX"
fi
H=https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/tom/medium/fr_FR-tom-medium
for ext in onnx onnx.json; do
    [ -f "$VOIX/fr_FR-tom-medium.$ext" ] || curl -fsSL -o "$VOIX/fr_FR-tom-medium.$ext" "$H.$ext"
done
chmod -R a+rX "$VOIX"

echo ">> Outils de l'enfant : yt-dlp, deno, whisper.cpp"
as_child '
set -e
mkdir -p ~/.local/bin && cd ~/.local/bin
[ -x yt-dlp ] || { curl -fsSL -o yt-dlp https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp && chmod +x yt-dlp; }
if [ ! -x deno ]; then
    curl -fsSL -o /tmp/deno.zip https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip
    unzip -o -q /tmp/deno.zip -d . && rm /tmp/deno.zip
fi
W=~/.local/share/whisper.cpp
[ -d $W ] || git clone -q --depth 1 https://github.com/ggml-org/whisper.cpp.git $W
cd $W
[ -x build/bin/whisper-cli ] || { cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON >/dev/null && cmake --build build -j"$(nproc)" --target whisper-cli; }
[ -f models/ggml-small.bin ] || sh ./models/download-ggml-model.sh small
'

echo ">> Applis, raccourcis et réglages du bureau"
as_child "
set -e
mkdir -p ~/.local/share/applications ~/.local/share/icons/ma-musique-web ~/.mixxx/controllers \"\$(xdg-user-dir DESKTOP)/Apprendre\"
ln -sfn '$REPO/ma-musique' ~/.local/share/ma-musique
ln -sfn '$REPO/bip' ~/.local/share/bip
ln -sfn '$REPO/mon-studio' ~/.local/share/mon-studio
cp '$REPO'/icons/*.svg ~/.local/share/icons/ma-musique-web/
ln -sf '$REPO/mixxx/Bip.midi.xml' '$REPO/mixxx/Bip-scripts.js' ~/.mixxx/controllers/
"
DESKTOP=$(sudo -u "$CHILD" -H xdg-user-dir DESKTOP)
for f in "$REPO"/desktop/*.desktop; do
    name=$(basename "$f")
    target="$CHILD_HOME/.local/share/applications/$name"
    sed "s#/home/djpiloupilou#$CHILD_HOME#g" "$f" > "$target"
    case "$name" in
        web-scratch.desktop|web-vikidia.desktop|web-lumni.desktop) cp "$target" "$DESKTOP/Apprendre/" ;;
        *) cp "$target" "$DESKTOP/" ;;
    esac
done
cp /usr/share/applications/org.mixxx.Mixxx.desktop /usr/share/applications/org.hydrogenmusic.Hydrogen.desktop "$DESKTOP/"
# Projet LMMS de départ ouvert par « Faire des beats »
install -D -o "$CHILD" -g "$CHILD" -m 644 "$REPO/lmms/Mon-premier-beat.mmp" "$CHILD_HOME/lmms/projects/Mon-premier-beat.mmp"
for app in tuxpaint tuxmath org.kde.ktouch org.stellarium.Stellarium org.kde.kgeography org.kde.marble \
           org.kde.kturtle org.kde.khangman org.kde.blinken org.kde.gcompris; do
    cp "/usr/share/applications/$app.desktop" "$DESKTOP/Apprendre/"
done
printf '[Desktop Entry]\nIcon=folder-blue\n' > "$DESKTOP/Apprendre/.directory"
chown -R "$CHILD:$CHILD" "$DESKTOP" "$CHILD_HOME/.local/share/applications"
chmod +x "$DESKTOP"/*.desktop "$DESKTOP"/Apprendre/*.desktop

as_child '
set -e
# Lancer les raccourcis au lieu de les ouvrir dans un éditeur (réglage Ubuntu Studio)
kwriteconfig6 --file kiorc --group "Executable scripts" --key behaviourOnLaunch execute
# Bip s’ouvre sur le côté droit de l’écran, au-dessus des autres fenêtres
kwriteconfig6 --file kwinrulesrc --group General --key count 1
kwriteconfig6 --file kwinrulesrc --group General --key rules bip-sidebar
for kv in "Description=Bip sur le côté droit" wmclass=bip wmclassmatch=1 wmclasscomplete=false \
          position=1480,0 positionrule=3 size=440,1030 sizerule=3 above=true aboverule=3; do
    kwriteconfig6 --file kwinrulesrc --group bip-sidebar --key "${kv%%=*}" "${kv#*=}"
done
'

echo ">> Terminé. Ouvrez Mixxx une première fois depuis le compte $CHILD pour créer sa configuration."
