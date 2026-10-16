#!/usr/bin/env python3
"""Mes Vidéos : YouTube en liste blanche, en grosses tuiles.

- « Chercher » : une vraie recherche YouTube, faite par le serveur Charly Guard (pas par Firefox).
  Les chaînes pas encore autorisées ont un badge et une miniature floutée (réglable par les parents).
  « Regarder » lit la vidéo si la chaîne est autorisée ; sinon on peut « Demander l’accès ».
- « Mes chaînes » : les dernières vidéos des chaînes autorisées.
- « Mes demandes » : en attente, oui ou non.
La lecture se fait avec mpv + yt-dlp, sans pubs ni recommandations (YouTube reste bloqué dans Firefox).
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
from PyQt6.QtCore import QByteArray, QProcess, QProcessEnvironment, Qt, QTimer, QUrl, QUrlQuery
from PyQt6.QtGui import QPixmap
from PyQt6.QtNetwork import QLocalSocket, QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                             QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget)

HOME = Path.home()
BIN = HOME / ".local/bin"
YTDLP = str(BIN / "yt-dlp")
DENO = str(BIN / "deno")
CONF = Path(os.environ.get("MES_VIDEOS_CONF", "/etc/studio-dj-kids/guard.conf"))
DATA = HOME / ".local/share/studio-dj-kids"
CACHE_FILE = DATA / "mes-videos.json"  # dernières listes reçues, pour s'ouvrir même sans Internet
OUTBOX_FILE = DATA / "mes-videos-envois.json"  # vidéos regardées et demandes pas encore envoyées
SOCKET = f"/tmp/mes-videos-mpv-{os.getuid()}.sock"
REFRESH_MS = 30 * 60 * 1000
REQUESTS_MS = 60 * 1000  # on regarde souvent si Papa a répondu
POLL_MS = 5000
COLUMNS = 3
THUMB_W, THUMB_H = 336, 189
AVATAR = 64
STATUS_TEXT = {"pending": "⏳ Papa n'a pas encore répondu", "allowed": "✅ Papa a dit oui !",
               "denied": "❌ Papa a dit non"}

STYLE = """
QWidget { font-size: 16px; }
QLineEdit { font-size: 22px; padding: 10px; border: 3px solid #4a90e2; border-radius: 14px; }
QPushButton { font-size: 18px; font-weight: bold; padding: 10px 18px; border-radius: 14px;
              background: #4a90e2; color: white; border: none; }
QPushButton:hover { background: #357abd; }
QPushButton:disabled { background: #9e9e9e; }
QPushButton#watch { background: #43a047; }
QPushButton#watch:hover { background: #2e7d32; }
QPushButton#ask { background: #e91e63; }
QPushButton#ask:hover { background: #c2185b; }
QPushButton#channel { background: #5c6bc0; font-size: 17px; }
QPushButton#channel:checked { background: #43a047; border: 4px solid #ffeb3b; }
QTabWidget::pane { border: none; }
QTabBar::tab { font-size: 20px; font-weight: bold; padding: 12px 34px; margin-right: 6px;
               border-top-left-radius: 14px; border-top-right-radius: 14px; background: #2c4f78; color: white; }
QTabBar::tab:hover { background: #3a6597; }
QTabBar::tab:selected { background: #4a90e2; color: white; }
QFrame#tile, QFrame#row { border: 3px solid #ddd; border-radius: 16px; }
QFrame#tile:hover { border-color: #4a90e2; background: rgba(74, 144, 226, 0.12); }
QLabel#title { font-size: 18px; font-weight: bold; }
QLabel#channelname { font-size: 15px; color: #888; }
QLabel#ok { font-size: 15px; font-weight: bold; color: white; background: #43a047; border-radius: 10px; padding: 3px 10px; }
QLabel#locked { font-size: 15px; font-weight: bold; color: white; background: #9e9e9e; border-radius: 10px; padding: 3px 10px; }
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


def fmt_duration(seconds):
    if not seconds:
        return ""
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}" if seconds >= 3600 \
        else f"{seconds // 60}:{seconds % 60:02d}"


def clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()


class Server:
    """Parle au serveur Charly Guard. Les envois (vidéos regardées, demandes) passent par une file sur disque."""

    def __init__(self, parent):
        self.base, self.token = read_conf()
        self.net = QNetworkAccessManager(parent)
        self.outbox = load_json(OUTBOX_FILE, [])
        self.sending = False

    def ok(self):
        return bool(self.base and self.token)

    def _request(self, path, query=None):
        url = QUrl(self.base + path)
        if query:
            q = QUrlQuery()
            for key, value in query.items():
                q.addQueryItem(key, str(value))
            url.setQuery(q)
        req = QNetworkRequest(url)
        req.setRawHeader(b"Authorization", f"Bearer {self.token}".encode())
        req.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        req.setTransferTimeout(40000)
        return req

    @staticmethod
    def _json(reply):
        try:
            return json.loads(bytes(reply.readAll()))
        except ValueError:
            return None

    def get(self, path, done, query=None):
        """done(dict) si ça marche, done(None) sinon."""
        reply = self.net.get(self._request(path, query))

        def finished():
            data = self._json(reply) if reply.error() == QNetworkReply.NetworkError.NoError else None
            reply.deleteLater()
            done(data)
        reply.finished.connect(finished)

    def post(self, path, body, done):
        """Envoi direct (sans file) : done(code HTTP, dict ou None). Code 0 = pas de réseau."""
        reply = self.net.post(self._request(path), QByteArray(json.dumps(body).encode()))

        def finished():
            code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) or 0
            data = self._json(reply)
            reply.deleteLater()
            done(code, data)
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

        def finished(code, _data):
            self.sending = False
            if code and code < 500:  # parti (ou refusé pour de bon) : on ne le renvoie plus
                if self.outbox and self.outbox[0] is item:
                    self.outbox.pop(0)
                save_json(OUTBOX_FILE, self.outbox)
                self.flush()
        self.post(item["path"], item["body"], finished)


class Images:
    """Miniatures et avatars téléchargés une seule fois, même quand les listes sont reconstruites."""

    def __init__(self, parent):
        self.net = QNetworkAccessManager(parent)
        self.pixmaps = {}
        self.waiting = {}

    def get(self, url, w, h, done, blur=False):
        if not url:
            return
        key = (url, w, h, blur)
        if key in self.pixmaps:
            done(self.pixmaps[key])
            return
        if key in self.waiting:
            self.waiting[key].append(done)
            return
        self.waiting[key] = [done]
        reply = self.net.get(QNetworkRequest(QUrl(url)))

        def finished():
            pix = QPixmap()
            if reply.error() == QNetworkReply.NetworkError.NoError and pix.loadFromData(bytes(reply.readAll())):
                if blur:  # très petite puis agrandie : on ne voit plus que des couleurs
                    pix = pix.scaled(16, 9, Qt.AspectRatioMode.IgnoreAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
                pix = pix.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                 Qt.TransformationMode.SmoothTransformation)
                self.pixmaps[key] = pix
                for callback in self.waiting.pop(key, []):
                    callback(pix)
            else:
                self.waiting.pop(key, None)
            reply.deleteLater()
        reply.finished.connect(finished)


def picture(images, url, w, h, blur=False, round_=False):
    """Un QLabel qui se remplit quand l'image arrive."""
    label = QLabel("🔒" if blur else "📺")
    label.setFixedSize(w, h)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    radius = w // 2 if round_ else 10
    label.setStyleSheet(f"background: #222; border-radius: {radius}px; font-size: {min(48, h // 2)}px;")

    def show(pix):
        if not sip.isdeleted(label):  # la liste a pu être reconstruite entre-temps
            label.setPixmap(pix)
    images.get(url, w, h, show, blur)
    return label


class Tile(QFrame):
    """Une vidéo : grosse miniature, titre, chaîne, badge et bouton « Regarder »."""

    def __init__(self, video, images, on_watch, allowed=True, blur=False, badge=True):
        super().__init__()
        self.setObjectName("tile")
        lay = QVBoxLayout(self)
        thumb = video.get("thumbnail") or f"https://i.ytimg.com/vi/{video['video_id']}/hqdefault.jpg"
        lay.addWidget(picture(images, thumb, THUMB_W, THUMB_H, blur=blur and not allowed))
        title = QLabel(video.get("title", ""))
        title.setObjectName("title")
        title.setWordWrap(True)
        title.setMaximumWidth(THUMB_W)
        title.setFixedHeight(52)
        lay.addWidget(title)
        when = fmt_age(video.get("published")) or fmt_duration(video.get("duration_s"))
        sub = QLabel(" · ".join(x for x in (video.get("channel_title", ""), when) if x))
        sub.setObjectName("channelname")
        sub.setMaximumWidth(THUMB_W)
        lay.addWidget(sub)
        bottom = QHBoxLayout()
        show_badge = badge
        badge = QLabel("✅ Autorisée" if allowed else "🔒 Pas encore autorisée")
        badge.setObjectName("ok" if allowed else "locked")
        watch = QPushButton("▶️ Regarder")
        watch.setObjectName("watch" if allowed else "")
        watch.clicked.connect(lambda: on_watch(video))
        if show_badge:
            bottom.addWidget(badge)
        bottom.addStretch()
        bottom.addWidget(watch)
        lay.addLayout(bottom)


def grid_page():
    """Zone qui défile, avec une grille de tuiles dedans."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return scroll


def fill_grid(scroll, widgets, empty_text):
    inner = QWidget()
    grid = QGridLayout(inner)
    grid.setSpacing(18)
    for i, w in enumerate(widgets):
        grid.addWidget(w, i // COLUMNS, i % COLUMNS)
    if not widgets:
        empty = QLabel(empty_text)
        empty.setObjectName("big")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(empty, 0, 0)
    grid.setRowStretch(grid.rowCount(), 1)
    scroll.setWidget(inner)


class MesVideos(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("📺 Mes Vidéos")
        self.resize(1220, 900)
        self.setStyleSheet(STYLE)
        self.server = Server(self)
        self.images = Images(self)
        cached = load_json(CACHE_FILE, {})
        self.videos = [{**v, "video_id": v.get("video_id") or v.get("id")} for v in cached.get("videos", [])]
        self.channels = cached.get("channels", [])
        self.requests = cached.get("requests", [])
        self.blur = cached.get("blur", True)
        self.results = []
        self.filter = None  # chaîne choisie dans « Mes chaînes », ou None = toutes
        self.player = None
        self.playing = None  # {"video": …, "started_at": ms, "watched": s}
        self.ipc = QLocalSocket(self)
        self.ipc.readyRead.connect(self.read_ipc)

        root = QVBoxLayout(self)
        heading = QLabel("📺 Mes Vidéos")
        heading.setStyleSheet("font-size: 30px; font-weight: bold;")
        root.addWidget(heading)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        # --- Chercher ---
        search_page = QWidget()
        lay = QVBoxLayout(search_page)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Tape le nom d'une chaîne ou d'une vidéo…")
        self.search.returnPressed.connect(self.do_search)
        self.search_btn = QPushButton("🔍 Chercher")
        self.search_btn.setMinimumHeight(56)
        self.search_btn.clicked.connect(self.do_search)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.search_btn)
        lay.addLayout(bar)
        self.search_scroll = grid_page()
        lay.addWidget(self.search_scroll, 1)
        self.tabs.addTab(search_page, "🔍 Chercher")

        # --- Mes chaînes ---
        channels_page = QWidget()
        lay = QVBoxLayout(channels_page)
        self.channel_bar = QHBoxLayout()
        row = QWidget()
        row.setLayout(self.channel_bar)
        row_scroll = QScrollArea()
        row_scroll.setWidget(row)
        row_scroll.setWidgetResizable(True)
        row_scroll.setFixedHeight(84)
        row_scroll.setFrameShape(QFrame.Shape.NoFrame)
        lay.addWidget(row_scroll)
        self.videos_scroll = grid_page()
        lay.addWidget(self.videos_scroll, 1)
        self.tabs.addTab(channels_page, "📺 Mes chaînes")

        # --- Mes demandes ---
        self.requests_scroll = grid_page()
        self.tabs.addTab(self.requests_scroll, "📨 Mes demandes")

        self.status = QLabel("")
        self.status.setObjectName("status")
        root.addWidget(self.status)

        self.poll = QTimer(self)
        self.poll.timeout.connect(self.poll_player)
        refresh = QTimer(self)
        refresh.timeout.connect(self.refresh)
        refresh.start(REFRESH_MS)
        answers = QTimer(self)
        answers.timeout.connect(self.load_requests)
        answers.start(REQUESTS_MS)

        self.build_search()
        self.build_channels()
        self.build_requests()
        if not self.server.ok():
            self.status.setText("⚠️ Mes Vidéos n'est pas encore réglée : demande à Papa.")
        else:
            self.tabs.setCurrentIndex(1 if self.channels else 0)
            self.refresh()
            self.server.flush()
            self.update_ytdlp()
        self.search.setFocus()

    def allowed_ids(self):
        return {c["channel_id"] for c in self.channels}

    def save_cache(self):
        save_json(CACHE_FILE, {"videos": self.videos, "channels": self.channels,
                               "requests": self.requests, "blur": self.blur})

    def refresh(self):
        if not self.server.ok():
            return
        self.server.get("/settings", self.got_settings)
        self.server.get("/channels", self.got_channels)
        self.server.get("/videos", self.got_videos, {"limit": 200})
        self.load_requests()

    def got_settings(self, data):
        if data and "blur_unapproved" in data and bool(data["blur_unapproved"]) != self.blur:
            self.blur = bool(data["blur_unapproved"])
            self.save_cache()
            self.build_search()

    def got_channels(self, data):
        if data and isinstance(data.get("channels"), list):
            self.channels = data["channels"]
            self.save_cache()
            self.build_channels()
            self.build_search()  # les badges changent si une chaîne vient d'être autorisée

    def got_videos(self, data):
        if data and isinstance(data.get("videos"), list):
            self.videos = [{**v, "video_id": v.get("video_id") or v.get("id")} for v in data["videos"]]
            self.save_cache()
            self.build_channels()
        elif data is None and not self.status.text():
            self.status.setText("📡 Pas de connexion au serveur : je montre les vidéos de la dernière fois.")

    # --- Chercher ---
    def do_search(self):
        text = self.search.text().strip()
        if not text or not self.server.ok():
            return
        self.search_btn.setEnabled(False)
        self.status.setText(f"⏳ Je cherche « {text} »…")
        self.server.get("/search", self.got_results, {"q": text, "limit": 30})

    def got_results(self, data):
        self.search_btn.setEnabled(True)
        if not data:
            self.status.setText("📡 La recherche ne marche pas en ce moment. Réessaie dans un petit moment.")
            return
        if "blur" in data:
            self.blur = bool(data["blur"])
        self.results = data.get("results") or []
        self.status.setText(data.get("error") or (f"🔍 {len(self.results)} vidéos trouvées" if self.results
                                                    else "🤷 Rien trouvé. Essaie avec d'autres mots."))
        self.build_search()

    def build_search(self):
        allowed = self.allowed_ids()
        tiles = [Tile(v, self.images, self.watch, allowed=v.get("channel_id") in allowed or bool(v.get("allowed")),
                      blur=self.blur) for v in self.results]
        fill_grid(self.search_scroll, tiles, "Tape ce que tu veux regarder, puis appuie sur « 🔍 Chercher ».")

    # --- Mes chaînes ---
    def build_channels(self):
        clear(self.channel_bar)
        if self.filter not in self.allowed_ids():
            self.filter = None
        for channel_id, label in [(None, "⭐ Toutes")] + [(c["channel_id"], c.get("title", "")) for c in self.channels]:
            btn = QPushButton(label)
            btn.setObjectName("channel")
            btn.setCheckable(True)
            btn.setChecked(channel_id == self.filter)
            btn.setMinimumHeight(52)
            btn.clicked.connect(lambda _, c=channel_id: self.choose(c))
            self.channel_bar.addWidget(btn)
        self.channel_bar.addStretch()
        shown = [v for v in self.videos if self.filter is None or v.get("channel_id") == self.filter]
        fill_grid(self.videos_scroll, [Tile(v, self.images, self.watch, badge=False) for v in shown],
                  "Pas encore de chaîne.\nCherche une vidéo dans « 🔍 Chercher » et demande à Papa !")

    def choose(self, channel_id):
        self.filter = channel_id
        self.build_channels()

    # --- Mes demandes ---
    def load_requests(self):
        if self.server.ok():
            self.server.get("/requests", self.got_requests)

    def got_requests(self, data):
        if not data or not isinstance(data.get("requests"), list):
            return
        before = {r["id"]: r.get("status") for r in self.requests}
        self.requests = data["requests"]
        self.save_cache()
        news = [r for r in self.requests if before.get(r["id"]) == "pending" and r.get("status") != "pending"]
        for r in news:
            self.status.setText(f"{STATUS_TEXT.get(r['status'], '')} pour « {r.get('channel_title', '')} »")
        if any(r.get("status") == "allowed" for r in news):
            self.server.get("/channels", self.got_channels)
            self.server.get("/videos", self.got_videos, {"limit": 200})
        self.build_requests()

    def build_requests(self):
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setSpacing(12)
        for r in self.requests:
            row = QFrame()
            row.setObjectName("row")
            h = QHBoxLayout(row)
            h.addWidget(picture(self.images, r.get("channel_avatar"), AVATAR, AVATAR, round_=True))
            text = QVBoxLayout()
            name = QLabel(r.get("channel_title") or "Chaîne")
            name.setObjectName("title")
            text.addWidget(name)
            if r.get("video_title"):
                vid = QLabel(f"Pour regarder : {r['video_title']}")
                vid.setObjectName("channelname")
                text.addWidget(vid)
            h.addLayout(text, 1)
            status = QLabel(STATUS_TEXT.get(r.get("status"), r.get("status", "")))
            status.setObjectName("big")
            h.addWidget(status)
            lay.addWidget(row)
        if not self.requests:
            empty = QLabel("Tu n'as encore rien demandé.")
            empty.setObjectName("big")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(empty)
        lay.addStretch()
        self.requests_scroll.setWidget(inner)

    # --- Regarder ou demander ---
    def watch(self, video):
        if video.get("channel_id") in self.allowed_ids():
            self.play(video)
            return
        pending = next((r for r in self.requests if r.get("channel_id") == video.get("channel_id")
                        and r.get("status") == "pending"), None)
        if pending:
            QMessageBox.information(self, "Mes Vidéos", f"Tu as déjà demandé « {video.get('channel_title', '')} ».\n"
                                    "Papa n'a pas encore répondu : regarde dans « 📨 Mes demandes ».")
            return
        box = QMessageBox(self)
        box.setWindowTitle("Mes Vidéos")
        box.setText(f"La chaîne « {video.get('channel_title', '')} » n'est pas encore autorisée.\n\n"
                    "Tu veux demander l'accès ?")
        ask = box.addButton("🙋 Demander l'accès", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Non merci", QMessageBox.ButtonRole.RejectRole)
        ask.setObjectName("ask")
        box.setStyleSheet(STYLE)
        box.exec()
        if box.clickedButton() is ask:
            self.ask(video)

    def ask(self, video):
        body = {"channel_id": video.get("channel_id", ""), "channel_title": video.get("channel_title", ""),
                "channel_avatar": video.get("channel_avatar") or "", "video_id": video.get("video_id", ""),
                "video_title": video.get("title", ""), "video_thumbnail": video.get("thumbnail") or ""}
        self.status.setText("📨 J'envoie ta demande…")

        def done(code, data):
            if code == 200 and data and data.get("status") == "allowed":
                self.status.setText("✅ Bonne nouvelle : cette chaîne est déjà autorisée !")
                self.server.get("/channels", self.got_channels)
            elif code == 200:
                self.status.setText("📨 C'est envoyé ! Papa va regarder ta demande.")
                self.tabs.setCurrentIndex(2)
            elif code == 0 or code >= 500:
                self.server.send("/request", body)  # pas de réseau : la demande partira plus tard
                self.status.setText("📨 Pas d'Internet pour l'instant : ta demande partira toute seule plus tard.")
            else:
                self.status.setText((data or {}).get("error") or "😕 Cette demande n'a pas marché.")
            self.load_requests()
        self.server.post("/request", body, done)

    # --- Lecture (mpv + yt-dlp) ---
    def update_ytdlp(self):
        # YouTube change souvent : on garde yt-dlp à jour en silence (comme Ma Musique)
        self.updater = QProcess(self)
        self.updater.start(YTDLP, ["-U"])

    def play(self, video):
        if self.player and self.player.state() != QProcess.ProcessState.NotRunning:
            self.status.setText("▶️ Une vidéo est déjà en train de passer.")
            return
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
            f"https://www.youtube.com/watch?v={video['video_id']}",
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
            "video_id": v["video_id"], "title": v.get("title", ""), "channel_id": v.get("channel_id", ""),
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
