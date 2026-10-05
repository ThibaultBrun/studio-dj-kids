"""Faire un mashup : la voix d'une chanson sur la musique d'une autre.

On dit à l'enfant si les deux vont bien ensemble (tempo et tonalité), on lui fait écouter un extrait,
puis on ouvre Mixxx avec la voix sur la platine 1 et la musique sur la platine 2, déjà calées.
"""
import re
import subprocess
import sys
import time

from PyQt6.QtCore import QProcess, QProcessEnvironment, QSize, Qt, QTimer
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                             QVBoxLayout)

import mixxx_db
from bibliotheque import HOME, music_text, read_song

sys.path.insert(0, str(HOME / ".local/share/bip"))

NOTES = mixxx_db.NOTES
PREVIEW = HOME / ".cache/ma-musique/extrait-mashup.mp3"
PREVIEW_SECONDS = 30
CC_SHIFT = 0x73
MODE_MASHUP = 3
SMILEYS = ["😬", "😐", "😀"]


def parse_key(text):
    """« F#m » -> (6, True) ; None si inconnu."""
    if not text:
        return None
    minor = text.endswith("m")
    note = text[:-1] if minor else text
    return (NOTES.index(note), minor) if note in NOTES else None


def match(voice, music):
    """Est-ce que la voix ira bien sur la musique ? Renvoie une note (0, 1, 2), le réglage et les explications."""
    # Tempo : la voix suit la musique. On accepte aussi le double ou la moitié (même pulsation).
    ratio = min((music["bpm"] / (voice["bpm"] * k) for k in (0.5, 1, 2)), key=lambda r: abs(r - 1))
    change = ratio - 1
    tempo_score = 2 if abs(change) <= 0.04 else 1 if abs(change) <= 0.10 else 0
    if abs(change) < 0.005:
        tempo_text = f"🥁 Rythme : {voice['bpm']:.0f} et {music['bpm']:.0f} BPM, le même tempo, parfait !"
    else:
        tempo_text = (f"🥁 Rythme : {voice['bpm']:.0f} et {music['bpm']:.0f} BPM : la voix chantera "
                      f"{abs(change) * 100:.0f} % plus {'vite' if change > 0 else 'lentement'}.")

    # Tonalité : on compare les gammes (une gamme mineure = la majeure qui a les mêmes notes)
    shift, key_score = 0, 1
    kv, km = parse_key(voice.get("key")), parse_key(music.get("key"))
    if kv and km:
        major_v = (kv[0] + 3 * kv[1]) % 12
        major_m = (km[0] + 3 * km[1]) % 12
        diff = (major_m - major_v) % 12
        diff = diff - 12 if diff > 6 else diff
        if diff == 0:
            key_score, key_text = 2, f"🎼 Tonalité : {voice['key']} et {music['key']}, les mêmes notes, parfait !"
        elif abs(diff) == 5:
            key_score, key_text = 2, f"🎼 Tonalité : {voice['key']} et {music['key']}, des gammes amies, ça sonne bien !"
        else:
            shift = diff
            key_score = 1 if abs(diff) <= 2 else 0
            key_text = (f"🎼 Tonalité : {voice['key']} et {music['key']} : je "
                        f"{'monte' if diff > 0 else 'descends'} la voix de {abs(diff)} demi-ton"
                        f"{'s' if abs(diff) > 1 else ''}" + (" (elle sera un peu bizarre)." if key_score == 0 else "."))
    else:
        key_text = "🎼 Tonalité : je ne la connais pas, écoute bien si ça sonne juste !"
    score = min(tempo_score, key_score)
    return {"score": score, "ratio": ratio, "shift": shift, "texts": [tempo_text, key_text]}


def voice_start(voice_file, duration, info):
    """Un moment où la voix chante (après 25 % de la chanson), calé sur un début de mesure."""
    out = subprocess.run(["ffmpeg", "-v", "info", "-nostats", "-i", str(voice_file),
                          "-af", "silencedetect=n=-35dB:d=0.8", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", out)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", out)]
    t = duration * 0.25
    for start, end in zip(starts, ends + [duration]):
        if start <= t < end:
            t = end
    return snap(t, info)


def snap(t, info):
    """Ramène t sur le temps « 1 » d'une mesure de 4 temps, d'après la grille."""
    period = 60 / info["bpm"]
    bar = round((t - info["first_beat"]) / (4 * period))
    return max(0.0, info["first_beat"] + bar * 4 * period)


class MashupDialog(QDialog):
    def __init__(self, library):
        super().__init__(library)
        self.library = library
        self.setWindowTitle("🎤 + 🎶 Faire un mashup")
        self.resize(1000, 720)
        self.preview_proc = None
        self.mixxx = None

        root = QVBoxLayout(self)
        intro = QLabel("Choisis <b>la voix</b> d'une chanson et <b>la musique</b> d'une autre. "
                       "Je te dis si elles vont bien ensemble !")
        intro.setWordWrap(True)
        intro.setObjectName("status")
        root.addWidget(intro)

        lists = QHBoxLayout()
        self.voices = self.make_list(lists, "🎤 La voix de…")
        self.musics = self.make_list(lists, "🎶 Sur la musique de…")
        root.addLayout(lists, 1)

        verdict = QHBoxLayout()
        self.smiley = QLabel("🤔")
        self.smiley.setStyleSheet("font-size: 64px;")
        self.verdict = QLabel()
        self.verdict.setWordWrap(True)
        self.verdict.setMinimumHeight(110)
        self.verdict.setObjectName("status")
        verdict.addWidget(self.smiley)
        verdict.addWidget(self.verdict, 1)
        root.addLayout(verdict)

        buttons = QHBoxLayout()
        self.listen_btn = QPushButton("🎧 Écouter un extrait")
        self.listen_btn.setObjectName("play")
        self.mixxx_btn = QPushButton("🎚 Ouvrir dans Mixxx")
        self.mixxx_btn.setObjectName("done")
        close = QPushButton("Fermer")
        close.setObjectName("folder")
        for button in (self.listen_btn, self.mixxx_btn, close):
            button.setMinimumHeight(60)
            buttons.addWidget(button)
        self.listen_btn.clicked.connect(self.listen)
        self.mixxx_btn.clicked.connect(self.open_mixxx)
        close.clicked.connect(self.close)
        root.addLayout(buttons)

        self.songs = {}  # chemin -> ligne de la bibliothèque (pistes, durée…)
        for path, row in library.rows.items():
            if "Voix" in row.stems and "Sans voix" in row.stems:
                self.songs[path] = row
        self.fill(self.voices, list(self.songs))
        self.fill(self.musics, list(self.songs))
        self.voices.currentItemChanged.connect(self.voice_changed)
        self.musics.currentItemChanged.connect(lambda *_: self.update_verdict())
        self.update_verdict()

    def make_list(self, layout, title):
        column = QVBoxLayout()
        label = QLabel(title)
        label.setObjectName("title")
        widget = QListWidget()
        widget.setIconSize(QSize(56, 56))
        widget.setWordWrap(True)
        widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        widget.setStyleSheet("QListWidget { font-size: 17px; } QListWidget::item { padding: 6px; }")
        column.addWidget(label)
        column.addWidget(widget, 1)
        layout.addLayout(column, 1)
        return widget

    def fill(self, widget, paths, prefixes=None):
        widget.blockSignals(True)
        selected = widget.currentItem().data(Qt.ItemDataRole.UserRole) if widget.currentItem() else None
        widget.clear()
        for path in paths:
            row = self.songs[path]
            info = self.library.song_info(path)
            text = f"{path.stem}\n{music_text(info) or '⏳ J’écoute encore le rythme…'}"
            if prefixes:
                text = f"{prefixes[path]}  {text}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, path)
            cover = read_song(path)[3]
            pixmap = QPixmap()
            if cover and pixmap.loadFromData(cover):
                item.setIcon(QIcon(pixmap))
            widget.addItem(item)
            if path == selected:
                widget.setCurrentItem(item)
        widget.blockSignals(False)
        if not paths:
            widget.addItem("Sépare d'abord des chansons avec ✂️ !")

    def chosen(self, widget):
        item = widget.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def voice_changed(self, *_):
        """Range les musiques de la plus réussie à la moins réussie avec cette voix."""
        voice = self.chosen(self.voices)
        voice_info = self.library.song_info(voice) if voice else None
        if voice_info:
            scored = {}
            for path in self.songs:
                info = self.library.song_info(path)
                scored[path] = match(voice_info, info)["score"] if info and path != voice else -1
            paths = sorted((p for p in self.songs if p != voice), key=lambda p: -scored[p])
            self.fill(self.musics, paths, {p: SMILEYS[scored[p]] if scored[p] >= 0 else "⏳" for p in paths})
        self.update_verdict()

    def pair(self):
        """(voix, musique, infos voix, infos musique, accord) ou None."""
        voice, music = self.chosen(self.voices), self.chosen(self.musics)
        if not voice or not music:
            return None
        vi, mi = self.library.song_info(voice), self.library.song_info(music)
        if not vi or not mi:
            return voice, music, vi, mi, None
        return voice, music, vi, mi, match(vi, mi)

    def update_verdict(self):
        pair = self.pair()
        ready = bool(pair and pair[4] and pair[0] != pair[1])
        self.listen_btn.setEnabled(ready)
        self.mixxx_btn.setEnabled(ready)
        if not pair:
            self.smiley.setText("🤔")
            self.verdict.setText("Choisis une voix à gauche, puis une musique à droite.")
        elif pair[0] == pair[1]:
            self.smiley.setText("🙃")
            self.verdict.setText("C'est la même chanson ! Choisis une autre musique.")
        elif not pair[4]:
            self.smiley.setText("⏳")
            self.verdict.setText("J'écoute encore le rythme de ces chansons, attends un petit peu…")
            QTimer.singleShot(3000, self.update_verdict)
        else:
            result = pair[4]
            self.smiley.setText(SMILEYS[result["score"]])
            advice = ["Ça va super bien ensemble !", "Ça peut marcher, écoute l'extrait !",
                      "Ça risque de sonner bizarre… mais tu peux essayer !"][2 - result["score"]]
            self.verdict.setText(f"<b>{advice}</b><br>" + "<br>".join(result["texts"]))

    # --- Écouter un extrait ---
    def listen(self):
        pair = self.pair()
        if not pair or not pair[4] or self.preview_proc:
            return
        voice, music, vi, mi, result = pair
        if self.library.playing and self.library.playing[0] is self.listen_btn:
            self.library.toggle_play(PREVIEW, self.listen_btn, "🎧 Écouter un extrait")  # arrête
            return
        self.library.stop_playing()
        self.listen_btn.setText("⏳ Je prépare…")
        QApplication.processEvents()
        voice_file = self.songs[voice].stems["Voix"]
        music_file = self.songs[music].stems["Sans voix"]
        vstart = voice_start(voice_file, self.songs[voice].duration, vi)
        mstart = snap(self.songs[music].duration * 0.3, mi)
        tempo = result["ratio"]
        pitch = 2 ** (result["shift"] / 12)
        PREVIEW.parent.mkdir(parents=True, exist_ok=True)
        self.preview_proc = QProcess(self)
        self.preview_proc.setProgram("ffmpeg")
        self.preview_proc.setArguments([
            "-v", "error", "-y",
            "-ss", f"{vstart:.3f}", "-t", f"{PREVIEW_SECONDS * tempo + 1:.3f}", "-i", str(voice_file),
            "-ss", f"{mstart:.3f}", "-t", f"{PREVIEW_SECONDS}", "-i", str(music_file),
            "-filter_complex",
            f"[0:a]rubberband=tempo={tempo:.5f}:pitch={pitch:.5f}:pitchq=quality,volume=1.2[v];"
            f"[v][1:a]amix=inputs=2:duration=shortest:normalize=0,"
            f"afade=t=in:d=1,afade=t=out:st={PREVIEW_SECONDS - 2}:d=2[a]",
            "-map", "[a]", "-c:a", "libmp3lame", "-q:a", "2", str(PREVIEW)])
        self.preview_proc.finished.connect(self.preview_ready)
        self.preview_proc.start()

    def preview_ready(self, code, _status):
        self.preview_proc = None
        self.listen_btn.setText("🎧 Écouter un extrait")
        if code == 0:
            self.library.toggle_play(PREVIEW, self.listen_btn, "🎧 Écouter un extrait")
        else:
            self.verdict.setText("😕 Je n'ai pas réussi à préparer l'extrait.")

    # --- Ouvrir dans Mixxx ---
    def open_mixxx(self):
        import mixeur  # le pilotage de Mixxx de Bip (contrôleur virtuel)

        pair = self.pair()
        if not pair or not pair[4] or self.mixxx:
            return
        self.library.stop_playing()
        if not mixeur.mixxx_configured():
            self.verdict.setText("Mixxx n'a encore jamais été ouvert. Demande à papa de l'ouvrir une première fois !")
            return
        voice, music, _, _, result = pair
        self.mixxx = {"files": [self.songs[voice].stems["Voix"], self.songs[music].stems["Sans voix"]],
                      "shift": result["shift"], "step": "closed", "deadline": time.time() + 180,
                      "mixeur": mixeur, "timer": QTimer(self)}
        self.mixxx["timer"].timeout.connect(self.mixxx_tick)
        self.mixxx_btn.setEnabled(False)
        if mixeur.mixxx_running():
            self.verdict.setText("<b>Ferme Mixxx</b>, je le rouvre avec ton mashup dès qu'il est fermé 😉")
        self.mixxx["timer"].start(1500)
        self.mixxx_tick()

    def launch_mixxx(self):
        mixeur = self.mixxx["mixeur"]
        self.library.sync_mixxx()  # Mixxx est fermé : on donne leur grille aux pistes
        mixeur.configure_mixxx()
        if mixeur.MIXXX_LOG.exists():
            mixeur.MIXXX_LOG.replace(mixeur.MIXXX_LOG.with_suffix(".log.avant-bip"))
        proc = QProcess()
        proc.setProgram("mixxx")
        env = QProcessEnvironment.systemEnvironment()
        env.remove("QT_QPA_PLATFORM")
        proc.setProcessEnvironment(env)
        proc.setArguments(["--log-flush-level", "warning", *map(str, self.mixxx["files"])])
        proc.setStandardOutputFile(QProcess.nullDevice())
        proc.setStandardErrorFile(QProcess.nullDevice())
        proc.startDetached()
        self.verdict.setText("🎧 J'ouvre Mixxx avec ton mashup…")
        self.mixxx.update(step="received", deadline=time.time() + 120)

    def log_has(self, message):
        try:
            return f"BIP:{message}" in self.mixxx["mixeur"].MIXXX_LOG.read_text(errors="replace")
        except OSError:
            return False

    def mixxx_tick(self):
        job = self.mixxx
        mixeur = job["mixeur"]
        if time.time() > job["deadline"]:
            self.mixxx_finished("Mixxx est ouvert avec la voix sur la platine 1 et la musique sur la platine 2. "
                                "Appuie sur SYNC et lance les deux !" if job["step"] != "closed" else
                                "Mixxx est toujours ouvert. Ferme-le et réessaie !")
        elif job["step"] == "closed":
            if not mixeur.mixxx_running():
                self.launch_mixxx()
        elif job["step"] == "received":
            if self.log_has("RECU"):
                self.verdict.setText("🎚 Je cale la voix sur la musique…")
                job.update(step="ready", deadline=time.time() + 90)
            else:
                mixeur.send_midi(CC_SHIFT, 64 + job["shift"])
                mixeur.send_midi(mixeur.CC_PREPARE, MODE_MASHUP)
        elif job["step"] == "ready" and self.log_has("PRET_MASHUP"):
            self.mixxx_finished("🎉 C'est parti ! La musique joue sur la platine 2 et la voix suit sur la platine 1. "
                                "Joue avec le crossfader et les volumes. Pour faire chanter un autre moment, "
                                "clique ailleurs sur la forme d'onde de la voix : elle reste calée !")

    def mixxx_finished(self, message):
        self.mixxx["timer"].stop()
        self.mixxx = None
        self.verdict.setText(message)
        self.mixxx_btn.setEnabled(True)

    def closeEvent(self, event):
        if self.library.playing and self.library.playing[0] is self.listen_btn:
            self.library.stop_playing()
        super().closeEvent(event)
