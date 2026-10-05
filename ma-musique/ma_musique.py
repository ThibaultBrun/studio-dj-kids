#!/usr/bin/env python3
"""Ma Musique : télécharger des chansons en MP3 « Artiste - Titre », les écouter et les séparer en pistes."""
import json
import os
import re
import shutil
import sys
from pathlib import Path

from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3NoHeaderError
from PyQt6.QtCore import QProcess, QProcessEnvironment, Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QFont, QPixmap
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PyQt6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                             QProgressBar, QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget)

from bibliotheque import Bibliotheque

HOME = Path.home()
BIN = HOME / ".local/bin"
YTDLP = str(BIN / "yt-dlp")
DENO = str(BIN / "deno")
MUSIC_DIR = HOME / "Musique"
TMP_DIR = MUSIC_DIR / ".en-cours"
HISTORY_FILE = HOME / ".local/share/studio-dj-kids/historique.json"  # données de l'enfant, hors du code
NB_RESULTS = 12
MAX_DURATION = 20 * 60  # on ignore les mix et les lives trop longs
MAX_TRIES = 3

JUNK = re.compile(
    r"\s*[\(\[][^\)\]]*(official|officiel|video|vidéo|clip|audio|lyric|parole|visuali[sz]er|"
    r"\bhd\b|\bhq\b|\b4k\b|remaster|music|color coded|tiktok|sped up|slowed)[^\)\]]*[\)\]]", re.IGNORECASE)

STYLE = """
QWidget { font-size: 16px; }
QLineEdit { font-size: 22px; padding: 10px; border: 3px solid #4a90e2; border-radius: 14px; }
QPushButton { font-size: 18px; font-weight: bold; padding: 10px 18px; border-radius: 14px;
              background: #4a90e2; color: white; border: none; }
QPushButton:hover { background: #357abd; }
QPushButton:disabled { background: #9e9e9e; }
QPushButton#done { background: #43a047; }
QPushButton#error { background: #e53935; }
QPushButton#folder { background: #ff9800; }
QPushButton#play { background: #8e24aa; }
QPushButton#mashup { background: #e91e63; }
QPushButton#mode { background: #5c6bc0; }
QPushButton#mode:checked { background: #43a047; border: 4px solid #ffeb3b; }
QPushButton#mashup:hover { background: #c2185b; }
QPushButton#play:hover { background: #6a1b9a; }
QPushButton#stem { background: #26a69a; font-size: 17px; }
QPushButton#stem:hover { background: #00897b; }
QFrame#stems { background: rgba(38, 166, 154, 0.18); border-radius: 12px; }
QLabel#help { font-size: 15px; font-style: italic; }
QFrame#player { background: rgba(74, 144, 226, 0.18); border-radius: 14px; }
QPushButton#round { font-size: 24px; padding: 0; border-radius: 30px; }
QSlider::groove:horizontal { height: 12px; background: rgba(128, 128, 128, 0.35); border-radius: 6px; }
QSlider::sub-page:horizontal { background: #4a90e2; border-radius: 6px; }
QSlider::handle:horizontal { background: white; border: 3px solid #4a90e2; width: 20px; margin: -7px 0;
                             border-radius: 13px; }
QSlider::handle:horizontal:disabled { border-color: #9e9e9e; }
QTabWidget::pane { border: none; }
QTabBar::tab { font-size: 20px; font-weight: bold; padding: 12px 34px; margin-right: 6px;
               border-top-left-radius: 14px; border-top-right-radius: 14px; background: #2c4f78; color: white; }
QTabBar::tab:hover { background: #3a6597; }
QTabBar::tab:selected { background: #4a90e2; color: white; }
QFrame#row { border: 2px solid #ddd; border-radius: 14px; }
QLabel#title { font-size: 18px; font-weight: bold; }
QLabel#status { font-size: 18px; }
QProgressBar { border: 2px solid #4a90e2; border-radius: 10px; text-align: center;
               font-weight: bold; min-height: 24px; background: #eef4fc; color: #1a3d66; }
QProgressBar::chunk { border-radius: 8px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4a90e2, stop:1 #43a047); }
"""


def clean(text):
    text = re.sub(r"\s+", " ", text or "")
    text = re.sub(r"([\(\[]) | ([\)\]])", r"\1\2", text)
    return text.strip(" -–—|\"'")


def make_name(info):
    """Construit (artiste, titre) le plus propre possible à partir des infos YouTube."""
    artist = info.get("artist") or (info.get("artists") or [None])[0]
    track = info.get("track")
    if artist and track:
        return clean(artist), clean(track)

    title = JUNK.sub("", info.get("title") or "")
    parts = re.split(r"\s+[-–—]\s+", title, maxsplit=1)
    if len(parts) != 2:
        parts = title.split("|")[:2]
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return clean(parts[0]), clean(parts[1].split("|")[0])
    title = title.split("|")[0]

    channel = info.get("channel") or info.get("uploader") or "Inconnu"
    channel = re.sub(r"\s*-\s*Topic$|VEVO$|\s*Official$", "", channel, flags=re.IGNORECASE)
    return clean(channel), clean(title)


def safe_filename(text):
    return clean(re.sub(r'[/\\:*?"<>|]', " ", text))[:180]


def fmt_duration(seconds):
    seconds = int(seconds or 0)
    return f"{seconds // 60}:{seconds % 60:02d}"


def ytdlp_process(args):
    proc = QProcess()
    env = QProcessEnvironment.systemEnvironment()
    env.insert("PATH", f"{BIN}:{env.value('PATH')}")
    proc.setProcessEnvironment(env)
    proc.setProgram(YTDLP)
    proc.setArguments(["--js-runtimes", f"deno:{DENO}", "--no-warnings", *args])
    return proc


def load_history():
    try:
        return set(json.loads(HISTORY_FILE.read_text()))
    except (OSError, ValueError):
        return set()


def add_to_history(video_id):
    history = load_history() | {video_id}
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(sorted(history)))


def download_process(video_id):
    """Prépare (sans le lancer) le téléchargement en MP3 d'une vidéo dans le dossier temporaire."""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    for old in TMP_DIR.glob(f"{video_id}.*"):
        old.unlink()
    return ytdlp_process([
        "-x", "--audio-format", "mp3", "--audio-quality", "0",
        "--embed-thumbnail", "--convert-thumbnails", "jpg",
        "--ppa", "ThumbnailsConvertor+FFmpeg_o:-c:v mjpeg -vf crop=\"'min(iw,ih)':'min(iw,ih)'\"",
        "--write-info-json", "--retries", "10", "--no-playlist",
        "--newline", "--progress", "--progress-template",
        "download:PROGRESS %(progress.downloaded_bytes)s %(progress.total_bytes)s %(progress.total_bytes_estimate)s",
        "-o", str(TMP_DIR / "%(id)s.%(ext)s"),
        f"https://www.youtube.com/watch?v={video_id}",
    ])


def parse_progress(line):
    """Traduit une ligne de yt-dlp en (pourcentage global, texte), ou None."""
    if line.startswith("PROGRESS "):
        done, total, estimate = (line.split() + ["NA"] * 3)[1:4]
        size = total if total != "NA" else estimate
        try:
            percent = min(100, float(done) / float(size) * 100)
        except (ValueError, ZeroDivisionError):
            return None
        return percent * 0.8, f"⬇ Téléchargement… {percent:.0f} %"
    if line.startswith("[ExtractAudio]"):
        return 85, "🎛 Transformation en MP3…"
    if line.startswith("[EmbedThumbnail]"):
        return 95, "🖼 Ajout de la pochette…"
    return None


def finalize_download(video_id):
    """Tague le MP3 téléchargé, le range en « Artiste - Titre.mp3 » et renvoie son chemin (None si raté)."""
    mp3 = TMP_DIR / f"{video_id}.mp3"
    info_file = TMP_DIR / f"{video_id}.info.json"
    if not mp3.exists() or not info_file.exists():
        return None
    info = json.loads(info_file.read_text())
    artist, title = make_name(info)
    try:
        tags = EasyID3(mp3)
    except ID3NoHeaderError:
        tags = EasyID3()
    tags["artist"] = artist
    tags["title"] = title
    tags.save(mp3)
    final = MUSIC_DIR / f"{safe_filename(artist)} - {safe_filename(title)}.mp3"
    shutil.move(mp3, final)
    info_file.unlink()
    add_to_history(video_id)
    return final


class ResultRow(QFrame):
    def __init__(self, app, entry):
        super().__init__()
        self.setObjectName("row")
        self.entry = entry
        layout = QHBoxLayout(self)

        self.thumb = QLabel()
        self.thumb.setFixedSize(192, 108)
        self.thumb.setStyleSheet("background: #222; border-radius: 8px;")
        layout.addWidget(self.thumb)
        app.load_thumbnail(entry["id"], self.thumb)

        texts = QVBoxLayout()
        title = QLabel(entry.get("title") or "")
        title.setObjectName("title")
        title.setWordWrap(True)
        info = QLabel(f"🎤 {entry.get('channel') or entry.get('uploader') or ''}    ⏱ {fmt_duration(entry.get('duration'))}")
        texts.addWidget(title)
        texts.addWidget(info)
        texts.addStretch()
        self.progress = QProgressBar()
        self.progress.hide()
        texts.addWidget(self.progress)
        layout.addLayout(texts, 1)

        self.button = QPushButton()
        self.button.setMinimumWidth(210)
        self.button.setMinimumHeight(60)
        self.button.clicked.connect(lambda: app.enqueue(self))
        layout.addWidget(self.button)
        if entry["id"] in app.history:
            self.set_state("done", "✅ Déjà là")
        else:
            self.set_state(None, "⬇ Télécharger")

    def set_progress(self, percent, text):
        """percent=None : barre animée (on ne connaît pas la durée)."""
        self.progress.show()
        if percent is None:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(int(percent))
        self.progress.setFormat(text)

    def set_state(self, name, text, enabled=None):
        self.button.setObjectName(name or "")
        self.button.setText(text)
        self.button.setEnabled(enabled if enabled is not None else name != "busy")
        self.button.style().unpolish(self.button)
        self.button.style().polish(self.button)


class MaMusique(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("🎵 Ma Musique")
        self.resize(1100, 800)
        self.setStyleSheet(STYLE)
        self.net = QNetworkAccessManager(self)
        self.history = load_history()
        self.queue = []
        self.current = None
        self.search_proc = None
        TMP_DIR.mkdir(parents=True, exist_ok=True)

        outer = QVBoxLayout(self)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        search_page = QWidget()
        root = QVBoxLayout(search_page)
        root.setContentsMargins(0, 8, 0, 0)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Tape le nom d'une chanson ou d'un artiste…")
        self.search.returnPressed.connect(self.do_search)
        self.search_btn = QPushButton("🔍 Chercher")
        self.search_btn.setMinimumHeight(56)
        self.search_btn.clicked.connect(self.do_search)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.search_btn)
        root.addLayout(bar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.results = QWidget()
        self.results_layout = QVBoxLayout(self.results)
        self.results_layout.addStretch()
        self.scroll.setWidget(self.results)
        root.addWidget(self.scroll, 1)

        bottom = QHBoxLayout()
        self.status = QLabel("Salut ! Cherche une chanson pour commencer 😀")
        self.status.setObjectName("status")
        folder_btn = QPushButton("📂 Mes musiques")
        folder_btn.setObjectName("folder")
        folder_btn.setMinimumHeight(56)
        folder_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(MUSIC_DIR))))
        self.search_bar = QProgressBar()
        self.search_bar.setRange(0, 0)
        self.search_bar.setFixedWidth(160)
        self.search_bar.setTextVisible(False)
        self.search_bar.hide()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.search_bar)
        bottom.addWidget(folder_btn)
        outer.addLayout(bottom)

        self.library = Bibliotheque(self.status)
        self.tabs.addTab(search_page, "🔍 Chercher")
        self.tabs.addTab(self.library, "📚 Ma bibliothèque")
        self.tabs.currentChanged.connect(self.tab_changed)

        self.search.setFocus()
        self.self_update()

    def tab_changed(self, index):
        if self.tabs.widget(index) is self.library:
            self.library.refresh()
        else:
            self.library.stop_playing()

    def closeEvent(self, event):
        if self.library.busy() and QMessageBox.question(
                self, "Ma Musique", "✂️ Une chanson est en train d'être découpée.\nTu veux vraiment fermer ?") \
                != QMessageBox.StandardButton.Yes:
            event.ignore()
            return
        event.accept()

    # --- yt-dlp ---
    def self_update(self):
        # YouTube change souvent : on garde yt-dlp à jour en silence
        self.updater = ytdlp_process(["-U"])
        self.updater.start()

    # --- Recherche ---
    def do_search(self):
        query = self.search.text().strip()
        if not query or self.search_proc:
            return
        self.search_btn.setEnabled(False)
        self.status.setText(f"🔎 Je cherche « {query} »…")
        self.search_bar.show()
        self.search_proc = ytdlp_process(["--flat-playlist", "--dump-json", f"ytsearch{NB_RESULTS}:{query}"])
        self.search_proc.finished.connect(self.search_done)
        self.search_proc.start()

    def search_done(self):
        out = bytes(self.search_proc.readAllStandardOutput()).decode(errors="replace")
        self.search_proc = None
        self.search_btn.setEnabled(True)
        self.search_bar.hide()

        while self.results_layout.count() > 1:
            widget = self.results_layout.takeAt(0).widget()
            if widget and widget is not getattr(self.current, "row", None) and not any(j["row"] is widget for j in self.queue):
                widget.deleteLater()
            elif widget:
                widget.setParent(None)

        entries = []
        for line in out.splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            duration = entry.get("duration")
            if entry.get("id") and duration and duration <= MAX_DURATION:
                entries.append(entry)

        for entry in entries:
            self.results_layout.insertWidget(self.results_layout.count() - 1, ResultRow(self, entry))
        self.scroll.verticalScrollBar().setValue(0)
        self.status.setText(f"J'ai trouvé {len(entries)} chansons 🎶" if entries
                            else "😕 Rien trouvé… essaie d'écrire autrement !")

    def load_thumbnail(self, video_id, label):
        reply = self.net.get(QNetworkRequest(QUrl(f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg")))

        def done():
            pixmap = QPixmap()
            if pixmap.loadFromData(reply.readAll()):
                try:
                    label.setPixmap(pixmap.scaled(label.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                                  Qt.TransformationMode.SmoothTransformation))
                except RuntimeError:  # la ligne a été effacée entre-temps
                    pass
            reply.deleteLater()
        reply.finished.connect(done)

    # --- Téléchargements (un par un) ---
    def enqueue(self, row):
        row.set_state("busy", "⏳ En attente…")
        self.queue.append({"row": row, "tries": 0})
        self.next_download()

    def next_download(self):
        if self.current or not self.queue:
            return
        self.current = self.queue.pop(0)
        self.start_download()

    def start_download(self):
        job = self.current
        job["tries"] += 1
        row = job["row"]
        row.set_state("busy", "⏳ Téléchargement…")
        row.set_progress(0, "C'est parti… 0 %" if job["tries"] == 1 else "🔁 Nouvel essai…")
        self.status.setText(f"⬇ Je télécharge « {row.entry.get('title')} »…")
        proc = download_process(row.entry["id"])
        proc.readyReadStandardOutput.connect(self.download_output)
        proc.finished.connect(self.download_done)
        job["proc"] = proc
        proc.start()

    def download_output(self):
        job = self.current
        row = job["row"]
        for line in bytes(job["proc"].readAllStandardOutput()).decode(errors="replace").splitlines():
            progress = parse_progress(line)
            if progress:
                row.set_progress(*progress)

    def download_done(self):
        job = self.current
        row = job["row"]
        video_id = row.entry["id"]
        final = finalize_download(video_id)

        if not final:
            if job["tries"] < MAX_TRIES:
                self.start_download()
                return
            row.set_state("error", "❌ Réessayer", enabled=True)
            row.progress.hide()
            self.status.setText("😕 Oups, ça n'a pas marché. Réessaie dans un moment !")
        else:
            self.history.add(video_id)
            row.set_state("done", "✅ Téléchargé")
            row.set_progress(100, "🎉 Fini !")
            self.status.setText(f"🎉 « {final.stem} » est dans tes musiques !")

        self.current = None
        self.next_download()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("Ma Musique")
    app.setDesktopFileName("ma-musique")
    app.setFont(QFont(app.font().family(), 12))
    window = MaMusique()
    window.show()
    if "--mashup" in sys.argv:  # raccourci « Créer un mashup » : on ouvre directement l'assistant
        window.tabs.setCurrentWidget(window.library)
        QTimer.singleShot(300, window.library.open_mashup)
    elif "--bibliotheque" in sys.argv:  # bouton « Ma bibliothèque » de Bip
        window.tabs.setCurrentWidget(window.library)
    sys.exit(app.exec())
