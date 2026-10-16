#!/usr/bin/env python3
"""Mes Vidéos : les vidéos YouTube des chaînes choisies par les parents, en grosses tuiles.

Pas de pubs, pas de recommandations : la liste vient du serveur Charly Guard (chaînes autorisées),
la lecture se fait avec mpv + yt-dlp (YouTube reste bloqué dans Firefox).
Chaque vidéo regardée est envoyée aux parents (titre, chaîne, heure, durée regardée).
Si Internet ou le serveur ne répond pas, les envois attendent dans une file et repartent plus tard.

Réglages (lisibles par l'enfant, modifiables seulement par un admin) : /etc/studio-dj-kids/guard.conf
    CG_BASE=https://guard.tbrun.dev
    CG_AGENT_TOKEN=…
"""
import json
import os
import sys
import time
from pathlib import Path

from PyQt6 import sip
from PyQt6.QtCore import QByteArray, QProcess, QProcessEnvironment, Qt, QTimer, QUrl
from PyQt6.QtGui import QPixmap
from PyQt6.QtNetwork import QLocalSocket, QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import (QApplication, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QScrollArea, QVBoxLayout, QWidget)

HOME = Path.home()
BIN = HOME / ".local/bin"
YTDLP = str(BIN / "yt-dlp")
DENO = str(BIN / "deno")
CONF = Path(os.environ.get("MES_VIDEOS_CONF", "/etc/studio-dj-kids/guard.conf"))
DATA = HOME / ".local/share/studio-dj-kids"
CACHE_FILE = DATA / "mes-videos.json"  # dernière liste reçue, pour s'ouvrir même sans Internet
OUTBOX_FILE = DATA / "mes-videos-envois.json"  # vidéos regardées et demandes pas encore envoyées
SOCKET = f"/tmp/mes-videos-mpv-{os.getuid()}.sock"
REFRESH_MS = 30 * 60 * 1000
POLL_MS = 5000
COLUMNS = 3
THUMB_W, THUMB_H = 352, 198

STYLE = """
QWidget { font-size: 16px; }
QLineEdit { font-size: 22px; padding: 10px; border: 3px solid #4a90e2; border-radius: 14px; }
QPushButton { font-size: 18px; font-weight: bold; padding: 10px 18px; border-radius: 14px;
              background: #4a90e2; color: white; border: none; }
QPushButton:hover { background: #357abd; }
QPushButton:disabled { background: #9e9e9e; }
QPushButton#ask { background: #e91e63; }
QPushButton#ask:hover { background: #c2185b; }
QPushButton#channel { background: #5c6bc0; font-size: 17px; }
QPushButton#channel:checked { background: #43a047; border: 4px solid #ffeb3b; }
QFrame#tile { border: 3px solid #ddd; border-radius: 16px; }
QFrame#tile:hover { border-color: #4a90e2; background: rgba(74, 144, 226, 0.12); }
QLabel#title { font-size: 18px; font-weight: bold; }
QLabel#channelname { font-size: 15px; color: #888; }
QLabel#status { font-size: 18px; }
QLabel#big { font-size: 22px; }
"""


def read_conf():
    conf = {}
    try:
        for line in CONF.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                conf[key.strip()] = value.strip()
    except OSError:
        pass
    return conf.get("CG_BASE", "").rstrip("/"), conf.get("CG_AGENT_TOKEN", "")


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    tmp.replace(path)


def fmt_age(iso):
    try:
        days = int((time.time() - time.mktime(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))) // 86400)
    except (TypeError, ValueError):
        return ""
    if days <= 0:
        return "aujourd'hui"
    if days == 1:
        return "hier"
    return f"il y a {days} jours" if days < 30 else f"il y a {days // 30} mois"


class Server:
    """Parle au serveur Charly Guard. Les envois (vidéos regardées, demandes) passent par une file sur disque."""

    def __init__(self, parent):
        self.base, self.token = read_conf()
        self.net = QNetworkAccessManager(parent)
        self.outbox = load_json(OUTBOX_FILE, [])
        self.sending = False

    def ok(self):
        return bool(self.base and self.token)

    def _request(self, path):
        req = QNetworkRequest(QUrl(self.base + path))
        req.setRawHeader(b"Authorization", f"Bearer {self.token}".encode())
        req.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        req.setTransferTimeout(20000)
        return req

    def get(self, path, done):
        """done(dict) si ça marche, done(None) sinon."""
        reply = self.net.get(self._request(path))

        def finished():
            data = None
            if reply.error() == QNetworkReply.NetworkError.NoError:
                try:
                    data = json.loads(bytes(reply.readAll()))
                except ValueError:
                    pass
            reply.deleteLater()
            done(data)
        reply.finished.connect(finished)

    def send(self, path, body):
        """Met l'envoi dans la file puis vide la file. Une vidéo regardée (même video_id + started_at)
        remplace son envoi précédent pas encore parti : on garde seulement la durée la plus récente."""
        key = (body.get("video_id"), body.get("started_at")) if path == "/watch" else None
        if key:
            self.outbox = [o for o in self.outbox
                           if not (o["path"] == "/watch" and (o["body"].get("video_id"), o["body"].get("started_at")) == key)]
        self.outbox.append({"path": path, "body": body})
        save_json(OUTBOX_FILE, self.outbox)
        self.flush()

    def flush(self):
        if self.sending or not self.outbox or not self.ok():
            return
        self.sending = True
        item = self.outbox[0]
        reply = self.net.post(self._request(item["path"]), QByteArray(json.dumps(item["body"]).encode()))

        def finished():
            self.sending = False
            sent = reply.error() == QNetworkReply.NetworkError.NoError
            reply.deleteLater()
            if sent:
                if self.outbox and self.outbox[0] is item:
                    self.outbox.pop(0)
                save_json(OUTBOX_FILE, self.outbox)
                self.flush()
        reply.finished.connect(finished)


class Thumbs:
    """Miniatures téléchargées une seule fois, même quand la liste est reconstruite."""

    def __init__(self, parent):
        self.net = QNetworkAccessManager(parent)
        self.pixmaps = {}
        self.waiting = {}

    def get(self, url, done):
        if url in self.pixmaps:
            done(self.pixmaps[url])
            return
        if url in self.waiting:
            self.waiting[url].append(done)
            return
        self.waiting[url] = [done]
        reply = self.net.get(QNetworkRequest(QUrl(url)))

        def finished():
            pix = QPixmap()
            if reply.error() == QNetworkReply.NetworkError.NoError and pix.loadFromData(bytes(reply.readAll())):
                pix = pix.scaled(THUMB_W, THUMB_H, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                 Qt.TransformationMode.SmoothTransformation)
                self.pixmaps[url] = pix
                for callback in self.waiting.pop(url, []):
                    callback(pix)
            else:
                self.waiting.pop(url, None)
            reply.deleteLater()
        reply.finished.connect(finished)


class Tile(QFrame):
    """Une vidéo : grosse miniature, titre, chaîne. Un clic la lance."""

    def __init__(self, video, thumbs, on_play):
        super().__init__()
        self.setObjectName("tile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.video = video
        self.on_play = on_play
        lay = QVBoxLayout(self)
        self.thumb = QLabel("📺")
        self.thumb.setFixedSize(THUMB_W, THUMB_H)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setStyleSheet("background: #222; border-radius: 10px; font-size: 48px;")
        title = QLabel(video.get("title", ""))
        title.setObjectName("title")
        title.setWordWrap(True)
        title.setMaximumWidth(THUMB_W)
        title.setFixedHeight(52)
        sub = QLabel(f"{video.get('channel_title', '')} · {fmt_age(video.get('published'))}")
        sub.setObjectName("channelname")
        lay.addWidget(self.thumb)
        lay.addWidget(title)
        lay.addWidget(sub)
        thumbs.get(video.get("thumbnail") or f"https://i.ytimg.com/vi/{video['id']}/hqdefault.jpg", self.show_thumb)

    def show_thumb(self, pix):
        if not sip.isdeleted(self.thumb):  # la liste a pu être reconstruite entre-temps
            self.thumb.setPixmap(pix)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.on_play(self.video)


class AskDialog(QDialog):
    """« Demander une chaîne » : Charly tape le nom ou colle le lien, papa reçoit la demande sur Discord."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Demander une chaîne")
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(640)
        lay = QVBoxLayout(self)
        info = QLabel("Quelle chaîne YouTube veux-tu regarder ?\nTape son nom (ou colle son lien). "
                      "Papa recevra ta demande et te répondra.")
        info.setObjectName("big")
        info.setWordWrap(True)
        self.field = QLineEdit()
        self.field.setPlaceholderText("Par exemple : Dr Nozman")
        self.field.returnPressed.connect(self.accept_if_filled)
        buttons = QHBoxLayout()
        cancel = QPushButton("Annuler")
        cancel.setStyleSheet("background: #9e9e9e;")
        cancel.clicked.connect(self.reject)
        send = QPushButton("📨 Envoyer la demande")
        send.setObjectName("ask")
        send.clicked.connect(self.accept_if_filled)
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(send)
        lay.addWidget(info)
        lay.addWidget(self.field)
        lay.addLayout(buttons)

    def accept_if_filled(self):
        if self.field.text().strip():
            self.accept()


class MesVideos(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("📺 Mes Vidéos")
        self.resize(1220, 860)
        self.setStyleSheet(STYLE)
        self.server = Server(self)
        self.thumbs = Thumbs(self)
        cached = load_json(CACHE_FILE, {})
        self.videos = cached.get("videos", [])
        self.channels = cached.get("channels", [])
        self.filter = None  # channel_id choisi, ou None = toutes
        self.player = None
        self.playing = None  # {"video": …, "started_at": ms, "watched": s}
        self.ipc = QLocalSocket(self)
        self.ipc.readyRead.connect(self.read_ipc)

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        heading = QLabel("📺 Mes Vidéos")
        heading.setStyleSheet("font-size: 30px; font-weight: bold;")
        ask = QPushButton("🙋 Demander une chaîne")
        ask.setObjectName("ask")
        ask.setMinimumHeight(56)
        ask.clicked.connect(self.ask_channel)
        top.addWidget(heading, 1)
        top.addWidget(ask)
        root.addLayout(top)

        self.channel_bar = QHBoxLayout()
        channel_row = QWidget()
        channel_row.setLayout(self.channel_bar)
        channel_scroll = QScrollArea()
        channel_scroll.setWidget(channel_row)
        channel_scroll.setWidgetResizable(True)
        channel_scroll.setFixedHeight(84)
        channel_scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(channel_scroll)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(self.scroll, 1)

        self.status = QLabel("")
        self.status.setObjectName("status")
        root.addWidget(self.status)

        self.poll = QTimer(self)
        self.poll.timeout.connect(self.poll_player)
        refresh = QTimer(self)
        refresh.timeout.connect(self.refresh)
        refresh.start(REFRESH_MS)

        self.build()
        if not self.server.ok():
            self.status.setText("⚠️ Mes Vidéos n'est pas encore réglée : demande à Papa.")
        else:
            self.refresh()
            self.server.flush()
            self.update_ytdlp()

    # --- Liste des vidéos ---
    def refresh(self):
        if not self.server.ok():
            return
        self.status.setText("⏳ Je regarde s'il y a de nouvelles vidéos…")
        self.server.get("/channels", self.got_channels)
        self.server.get("/videos?limit=200", self.got_videos)

    def got_channels(self, data):
        if data and isinstance(data.get("channels"), list):
            self.channels = data["channels"]
            self.save_cache()
            self.build()

    def got_videos(self, data):
        if data and isinstance(data.get("videos"), list):
            self.videos = data["videos"]
            self.save_cache()
            self.build()
            self.status.setText(f"✅ {len(self.videos)} vidéos" if self.videos else "")
        else:
            self.status.setText("📡 Pas de connexion au serveur : je montre les vidéos de la dernière fois.")

    def save_cache(self):
        save_json(CACHE_FILE, {"videos": self.videos, "channels": self.channels})

    def build(self):
        while self.channel_bar.count():
            item = self.channel_bar.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for channel_id, label in [(None, "⭐ Toutes")] + [(c["channel_id"], c.get("title", "")) for c in self.channels]:
            btn = QPushButton(label)
            btn.setObjectName("channel")
            btn.setCheckable(True)
            btn.setChecked(channel_id == self.filter)
            btn.setMinimumHeight(52)
            btn.clicked.connect(lambda _, c=channel_id: self.choose(c))
            self.channel_bar.addWidget(btn)
        self.channel_bar.addStretch()

        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setSpacing(18)
        shown = [v for v in self.videos if self.filter is None or v.get("channel_id") == self.filter]
        for i, video in enumerate(shown):
            grid.addWidget(Tile(video, self.thumbs, self.play), i // COLUMNS, i % COLUMNS)
        if not shown:
            empty = QLabel("Pas encore de vidéos ici.\nAppuie sur « 🙋 Demander une chaîne » !")
            empty.setObjectName("big")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(empty, 0, 0)
        grid.setRowStretch(grid.rowCount(), 1)
        self.scroll.setWidget(grid_widget)

    def choose(self, channel_id):
        self.filter = channel_id
        self.build()

    # --- Demande de chaîne ---
    def ask_channel(self):
        dialog = AskDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.server.send("/request", {"kind": "channel", "value": dialog.field.text().strip(), "label": ""})
        self.status.setText("📨 C'est envoyé ! Papa va regarder ta demande.")

    # --- Lecture (mpv + yt-dlp) ---
    def update_ytdlp(self):
        # YouTube change souvent : on garde yt-dlp à jour en silence (comme Ma Musique)
        self.updater = QProcess(self)
        self.updater.start(YTDLP, ["-U"])

    def play(self, video):
        if self.player and self.player.state() != QProcess.ProcessState.NotRunning:
            self.status.setText("▶️ Une vidéo est déjà en train de passer.")
            return
        allowed = {c["channel_id"] for c in self.channels}
        if allowed and video.get("channel_id") not in allowed:
            return  # chaîne retirée depuis : on ne la lit plus
        self.playing = {"video": video, "started_at": int(time.time() * 1000), "watched": 0}
        self.report()
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PATH", f"{BIN}:{env.value('PATH')}")
        self.player = QProcess(self)
        self.player.setProcessEnvironment(env)
        self.player.finished.connect(self.stopped)
        self.player.start("mpv", [
            "--fs", "--force-window=immediate", "--no-terminal", "--keep-open=no",
            f"--title=📺 {video.get('title', '')}",
            f"--input-ipc-server={SOCKET}",
            "--ytdl-format=bestvideo[height<=720]+bestaudio/best[height<=720]/best",
            f"--script-opts=ytdl_hook-ytdl_path={YTDLP}",
            f"--ytdl-raw-options=js-runtimes=deno:{DENO},no-playlist=",
            f"https://www.youtube.com/watch?v={video['id']}",
        ])
        self.status.setText(f"▶️ {video.get('title', '')} (Échap ou Q pour arrêter)")
        self.poll.start(POLL_MS)

    def poll_player(self):
        if self.ipc.state() != QLocalSocket.LocalSocketState.ConnectedState:
            self.ipc.connectToServer(SOCKET)
            return
        self.ipc.write(b'{"command": ["get_property", "playback-time"], "request_id": 1}\n')

    def read_ipc(self):
        while self.ipc.canReadLine():
            try:
                msg = json.loads(bytes(self.ipc.readLine()))
            except ValueError:
                continue
            if msg.get("request_id") == 1 and isinstance(msg.get("data"), (int, float)) and self.playing:
                self.playing["watched"] = max(self.playing["watched"], int(msg["data"]))

    def stopped(self):
        self.poll.stop()
        self.ipc.abort()
        if self.playing:
            self.report()
            minutes = self.playing["watched"] // 60
            self.status.setText(f"⏹️ Fini ({minutes} min regardées)." if minutes else "⏹️ Fini.")
        self.playing = None

    def report(self):
        """Envoie aux parents : quelle vidéo, quand, combien de temps."""
        v = self.playing["video"]
        self.server.send("/watch", {
            "video_id": v["id"], "title": v.get("title", ""), "channel_id": v.get("channel_id", ""),
            "channel_title": v.get("channel_title", ""), "started_at": self.playing["started_at"],
            "watched_s": self.playing["watched"],
        })

    def closeEvent(self, event):
        if self.player and self.player.state() != QProcess.ProcessState.NotRunning:
            self.player.terminate()
            self.player.waitForFinished(3000)
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("mes-videos")
    app.setDesktopFileName("mes-videos")
    window = MesVideos()
    window.show()
    sys.exit(app.exec())
