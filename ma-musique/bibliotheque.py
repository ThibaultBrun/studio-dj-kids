"""Ma bibliothèque : écouter ses musiques et les séparer en pistes (voix, batterie, basse…)."""
from pathlib import Path

from mutagen import File as AudioFile
from mutagen import MutagenError
from PyQt6.QtCore import QMimeData, QProcess, QProcessEnvironment, Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QDrag, QPixmap
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                             QProgressBar, QPushButton, QScrollArea, QVBoxLayout, QWidget)

HOME = Path.home()
MUSIC_DIR = HOME / "Musique"
STEMS_DIR = MUSIC_DIR / "Pistes séparées"
SEPARATOR_PY = HOME / ".local/share/separateur/venv/bin/python"
SEPARER = Path(__file__).resolve().parent / "separer.py"
AUDIO_EXT = {".mp3", ".m4a", ".flac", ".wav", ".ogg", ".opus"}
STEM_ICONS = {"Voix": "🎤", "Sans voix": "🎶", "Batterie": "🥁", "Basse": "🎸",
              "Guitare": "🪕", "Piano": "🎹", "Autres": "✨"}
SECONDS_PER_SECOND = 1.5  # temps de séparation mesuré sur ce PC (GTX 1050)
MAX_TRIES = 2  # le 2e essai se fait sans la carte graphique


def fmt_duration(seconds):
    seconds = int(seconds or 0)
    return f"{seconds // 60}:{seconds % 60:02d}"


def list_songs():
    songs = []
    for path in MUSIC_DIR.rglob("*"):
        rel = path.relative_to(MUSIC_DIR)
        if (path.suffix.lower() in AUDIO_EXT and path.is_file()
                and not any(p.startswith(".") for p in rel.parts) and rel.parts[0] != STEMS_DIR.name):
            songs.append(path)
    return sorted(songs, key=lambda p: p.stem.lower())


def read_song(path):
    """(artiste, titre, durée, octets de la pochette ou None)."""
    artist, _, title = path.stem.partition(" - ")
    if not title:
        artist, title = "", path.stem
    duration, cover = 0, None
    try:
        audio = AudioFile(path)
        duration = audio.info.length
        tags = audio.tags
        if tags is not None and hasattr(tags, "getall"):  # MP3 (ID3)
            pics = tags.getall("APIC")
            cover = pics[0].data if pics else None
            artist = str(tags.get("TPE1") or artist)
            title = str(tags.get("TIT2") or title)
        elif tags is not None and "covr" in tags:  # M4A
            cover = bytes(tags["covr"][0])
    except (MutagenError, AttributeError, OSError):
        pass
    return artist, title, duration, cover


def stems_of(song):
    folder = STEMS_DIR / song.stem
    found = {}
    for label in STEM_ICONS:
        f = folder / f"{song.stem} ({label}).mp3"
        if f.exists():
            found[label] = f
    return found


class DragButton(QPushButton):
    """Bouton qu'on peut aussi glisser vers Mixxx pour y charger le fichier."""

    def __init__(self, text, path):
        super().__init__(text)
        self.path = path
        self.press_pos = None
        self.setToolTip("Clique pour écouter, ou glisse-moi dans Mixxx !")

    def mousePressEvent(self, event):
        self.press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.press_pos is None or (event.position().toPoint() - self.press_pos).manhattanLength() \
                < QApplication.startDragDistance():
            return
        self.press_pos = None
        self.setDown(False)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(self.path))])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


class SongRow(QFrame):
    def __init__(self, library, path):
        super().__init__()
        self.setObjectName("row")
        self.library = library
        self.path = path
        artist, title, self.duration, cover = read_song(path)
        self.search_text = f"{artist} {title} {path.stem}".lower()

        outer = QVBoxLayout(self)
        layout = QHBoxLayout()
        outer.addLayout(layout)

        thumb = QLabel("🎵")
        thumb.setFixedSize(90, 90)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setStyleSheet("background: #222; border-radius: 8px; font-size: 40px;")
        pixmap = QPixmap()
        if cover and pixmap.loadFromData(cover):
            thumb.setPixmap(pixmap.scaled(90, 90, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))
        layout.addWidget(thumb)

        texts = QVBoxLayout()
        title_label = QLabel(title)
        title_label.setObjectName("title")
        title_label.setWordWrap(True)
        info = f"🎤 {artist}    ⏱ {fmt_duration(self.duration)}" if artist else f"⏱ {fmt_duration(self.duration)}"
        if path.parent != MUSIC_DIR:
            info += f"    📁 {path.parent.name}"
        texts.addWidget(title_label)
        texts.addWidget(QLabel(info))
        texts.addStretch()
        self.progress = QProgressBar()
        self.progress.hide()
        texts.addWidget(self.progress)
        layout.addLayout(texts, 1)

        self.play_btn = DragButton("▶ Écouter", path)
        self.play_btn.setObjectName("play")
        self.play_btn.setMinimumSize(150, 60)
        self.play_btn.clicked.connect(lambda: library.toggle_play(path, self.play_btn, "▶ Écouter"))
        layout.addWidget(self.play_btn)

        self.split_btn = QPushButton()
        self.split_btn.setMinimumSize(200, 60)
        self.split_btn.clicked.connect(self.split_clicked)
        layout.addWidget(self.split_btn)

        self.stems_box = QFrame()
        self.stems_box.setObjectName("stems")
        self.stems_box.hide()
        outer.addWidget(self.stems_box)
        self.refresh_stems()

    # --- Pistes séparées ---
    def refresh_stems(self):
        self.stems = stems_of(self.path)
        if self.library.busy_row(self):
            return
        if self.stems:
            self.set_state("done", "🎚 Mes pistes")
        else:
            self.set_state(None, "✂️ Séparer", enabled=self.library.separator_ok)
        old = self.stems_box.layout()
        if old:
            QWidget().setLayout(old)  # se débarrasse de l'ancienne grille
        grid = QGridLayout(self.stems_box)
        for i, (label, f) in enumerate(self.stems.items()):
            text = f"▶ {STEM_ICONS[label]} {label}"
            btn = DragButton(text, f)
            btn.setObjectName("stem")
            btn.setMinimumHeight(50)
            btn.clicked.connect(lambda _, f=f, b=btn, t=text: self.library.toggle_play(f, b, t))
            grid.addWidget(btn, i // 4, i % 4)
        folder = QPushButton("📂 Ouvrir le dossier")
        folder.setObjectName("folder")
        folder.setMinimumHeight(50)
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(STEMS_DIR / self.path.stem))))
        grid.addWidget(folder, len(self.stems) // 4, len(self.stems) % 4)
        if not self.stems:
            self.stems_box.hide()

    def split_clicked(self):
        if self.stems:
            self.stems_box.setVisible(not self.stems_box.isVisible())
        else:
            self.library.enqueue(self)

    def set_progress(self, percent, text):
        self.progress.show()
        if percent is None:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(int(percent))
        self.progress.setFormat(text)

    def set_state(self, name, text, enabled=None):
        self.split_btn.setObjectName(name or "")
        self.split_btn.setText(text)
        self.split_btn.setEnabled(enabled if enabled is not None else name != "busy")
        self.split_btn.style().unpolish(self.split_btn)
        self.split_btn.style().polish(self.split_btn)


class Bibliotheque(QWidget):
    def __init__(self, status):
        super().__init__()
        self.status = status  # la barre d'état de la fenêtre
        self.separator_ok = SEPARATOR_PY.exists()
        self.rows = {}
        self.queue = []
        self.current = None
        self.playing = None  # (bouton, texte d'origine)
        self.audio = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.player.playbackStateChanged.connect(self.playback_changed)

        root = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("🔎 Retrouver une chanson…")
        self.filter.textChanged.connect(self.apply_filter)
        bar.addWidget(self.filter, 1)
        root.addLayout(bar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.list = QWidget()
        self.list_layout = QVBoxLayout(self.list)
        self.list_layout.addStretch()
        self.scroll.setWidget(self.list)
        root.addWidget(self.scroll, 1)

        help_text = QLabel("✂️ Séparer = découper une chanson en pistes : la voix toute seule, la musique sans la voix, "
                           "la batterie, la basse… Ensuite, glisse une piste dans Mixxx pour faire un remix !"
                           if self.separator_ok else
                           "✂️ Pour séparer les chansons, demande à papa d'installer le séparateur.")
        help_text.setWordWrap(True)
        help_text.setObjectName("help")
        root.addWidget(help_text)

    # --- Liste des chansons ---
    def refresh(self):
        songs = list_songs()
        if list(self.rows) == songs:
            for row in self.rows.values():
                row.refresh_stems()
            return
        while self.list_layout.count() > 1:
            widget = self.list_layout.takeAt(0).widget()
            if widget:
                widget.setParent(None)
        old = self.rows
        self.rows = {}
        for path in songs:
            row = old.pop(path, None) or SongRow(self, path)
            row.refresh_stems()
            self.rows[path] = row
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)
        for row in old.values():
            if not self.busy_row(row):
                row.deleteLater()
        self.apply_filter()
        if not self.current:
            self.status.setText(f"📚 Tu as {len(songs)} chansons" if songs
                                else "📚 Pas encore de chansons : va dans « Chercher » pour en télécharger !")

    def apply_filter(self):
        words = self.filter.text().lower().split()
        for row in self.rows.values():
            row.setVisible(all(w in row.search_text for w in words))

    # --- Écouter ---
    def toggle_play(self, path, button, text):
        previous = self.playing
        self.player.stop()
        if previous and previous[0] is button:
            return
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()
        self.playing = (button, text)
        button.setText("⏹ Stop")

    def playback_changed(self, state):
        if state == QMediaPlayer.PlaybackState.StoppedState and self.playing:
            button, text = self.playing
            self.playing = None
            try:
                button.setText(text)
            except RuntimeError:  # bouton effacé entre-temps
                pass

    def stop_playing(self):
        self.player.stop()

    # --- Séparation (une chanson à la fois) ---
    def busy_row(self, row):
        return (self.current and self.current["row"] is row) or any(j["row"] is row for j in self.queue)

    def enqueue(self, row):
        row.set_state("busy", "⏳ En attente…")
        self.queue.append({"row": row, "tries": 0})
        self.next_split()

    def next_split(self):
        if self.current or not self.queue:
            return
        self.current = self.queue.pop(0)
        self.start_split()

    def start_split(self):
        job = self.current
        job["tries"] += 1
        job["step"] = "modele"
        row = job["row"]
        row.set_state("busy", "✂️ Séparation…")
        row.set_progress(None, "Je prépare les ciseaux… ✂️" if job["tries"] == 1 else "🔁 Nouvel essai, plus lent…")
        minutes = max(1, round(row.duration * SECONDS_PER_SECOND / 60))
        self.status.setText(f"✂️ Je découpe « {row.path.stem} »… ça prend environ {minutes} min, "
                            "tu peux faire autre chose en attendant !")
        STEMS_DIR.mkdir(parents=True, exist_ok=True)
        proc = QProcess(self)
        if job["tries"] > 1:  # si la carte graphique a calé, on réessaie avec le processeur seul
            env = QProcessEnvironment.systemEnvironment()
            env.insert("CUDA_VISIBLE_DEVICES", "")
            proc.setProcessEnvironment(env)
        proc.setProgram(str(SEPARATOR_PY))
        proc.setArguments([str(SEPARER), str(row.path), str(STEMS_DIR / row.path.stem)])
        proc.readyReadStandardOutput.connect(self.split_output)
        proc.finished.connect(self.split_done)
        job["proc"] = proc
        job["ok"] = False
        proc.start()

    def split_output(self):
        job = self.current
        row = job["row"]
        for line in bytes(job["proc"].readAllStandardOutput()).decode(errors="replace").splitlines():
            words = line.split()
            if not words:
                continue
            if words[0] == "ETAPE":
                job["step"] = words[1]
                if words[1] == "separation":
                    row.set_progress(0, "✂️ Je découpe… 0 %")
            elif words[0] == "PROGRESS" and len(words) == 3:
                done, total = int(words[1]), max(1, int(words[2]))
                if job["step"] == "separation":
                    percent = done / total * 90
                    row.set_progress(percent, f"✂️ Je découpe… {percent:.0f} %")
                else:
                    row.set_progress(90 + done / total * 10, "💾 Je range les pistes…")
            elif words[0] == "FINI":
                job["ok"] = True

    def split_done(self):
        job = self.current
        row = job["row"]
        self.current = None
        if not job["ok"] and job["tries"] < MAX_TRIES:
            self.current = job
            self.start_split()
            return
        row.refresh_stems()
        if job["ok"]:
            row.set_progress(100, "🎉 Fini !")
            row.stems_box.show()
            self.status.setText(f"🎉 « {row.path.stem} » est découpée ! Écoute les pistes ou glisse-les dans Mixxx.")
        else:
            row.progress.hide()
            row.set_state("error", "❌ Réessayer", enabled=True)
            self.status.setText("😕 Oups, la séparation n'a pas marché. Réessaie, ou demande à papa.")
        self.next_split()

    def busy(self):
        return bool(self.current or self.queue)

