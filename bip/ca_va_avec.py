"""« Qu'est-ce qui va avec ? » : quand un morceau arrive sur une platine de Mixxx, Bip propose des morceaux
qui vont bien avec (même vitesse, tonalités qui s'accordent), dans sa musique et parmi des tubes connus.

- Le contrôleur virtuel (mixxx/Bip-scripts.js) écrit « BIP:DECK 1 bpm=120.00 key=22 duration=215.3 »
  dans le journal de Mixxx à chaque morceau chargé ; Bip lit la fin du journal toutes les 1,5 s.
- Compatibilité : vitesse à ±6 % (ou moitié / double), tonalité voisine sur la roue de Camelot.
- Les tubes viennent de bip/tubes.py (Deezer + analyse des extraits de 30 s), lancé en arrière-plan.
"""
import json
import os
import re
import sqlite3
import time
import unicodedata
from pathlib import Path

from mutagen import File as AudioFile
from mutagen import MutagenError
from PyQt6.QtCore import QMimeData, QObject, QProcess, QProcessEnvironment, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDrag, QPixmap
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea,
                             QVBoxLayout, QWidget)

import mixeur

HOME = Path.home()
APP_DIR = Path(__file__).resolve().parent
DATA_DIR = HOME / ".local/share/studio-dj-kids"
# Les variables BIP_… servent aux tests (copies des données, jamais celles de l'enfant)
MIXXX_LOG = Path(os.environ.get("BIP_MIXXX_LOG", HOME / ".mixxx/mixxx.log"))
MIXXX_DB = Path(os.environ.get("BIP_MIXXX_DB", HOME / ".mixxx/mixxxdb.sqlite"))
ANALYSES_FILE = Path(os.environ.get("BIP_ANALYSES", DATA_DIR / "analyses.json"))
TUBES_FILE = Path(os.environ.get("BIP_TUBES", DATA_DIR / "tubes.json"))
TUBES_SCRIPT = APP_DIR / "tubes.py"
SEPARATOR_PY = HOME / ".local/share/separateur/venv/bin/python"
MUSIC_DIR = mixeur.mm.MUSIC_DIR
DEEZER = "https://api.deezer.com"

LOG_POLL_MS = 1500
TEMPO_TOLERANCE = 0.06   # ±6 % : SYNC de Mixxx rattrape ça sans que ça s'entende trop
PERFECT_TEMPO = 0.03
MAX_MINE = 20
MAX_TUBES = 15
REFRESH_DAYS = 7

# --- Tonalités (numéros de Mixxx : 1 = Do majeur … 12 = Si majeur, 13 = Do mineur … 24 = Si mineur) ---
NOTES = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
SHARPS = {"C#": "Db", "D#": "Eb", "Gb": "F#", "G#": "Ab", "A#": "Bb"}
NOTES_FR = ["Do", "Ré bémol", "Ré", "Mi bémol", "Mi", "Fa", "Fa dièse", "Sol", "La bémol", "La", "Si bémol", "Si"]


def key_from_text(text):
    """« Ebm », « D#m », « A » -> numéro de tonalité de Mixxx (0 si inconnu)."""
    match = re.fullmatch(r"\s*([A-G][#b]?)\s*(m|min|minor)?\s*", text or "")
    if not match:
        return 0
    note = SHARPS.get(match.group(1), match.group(1))
    if note not in NOTES:
        return 0
    return NOTES.index(note) + (13 if match.group(2) else 1)


def key_name(key):
    """23 -> « Si bémol mineur » (en mots simples pour l'enfant)."""
    if not 1 <= key <= 24:
        return "tonalité inconnue"
    minor = key > 12
    return f"{NOTES_FR[(key - 1) % 12]} {'mineur' if minor else 'majeur'}"


def camelot(key):
    """Place sur la roue de Camelot : (1-12, « A » pour mineur ou « B » pour majeur), None si inconnu."""
    if not 1 <= key <= 24:
        return None
    pitch, minor = (key - 1) % 12, key > 12
    number = (7 * pitch + (5 if minor else 8)) % 12 or 12
    return number, "A" if minor else "B"


def key_match(a, b):
    """Comment deux tonalités s'accordent : "same", "relative", "neighbour", "unknown" ou None (ça sonne faux)."""
    ca, cb = camelot(a), camelot(b)
    if not ca or not cb:
        return "unknown"
    if ca == cb:
        return "same"
    if ca[0] == cb[0]:
        return "relative"   # La mineur et Do majeur : mêmes notes
    if ca[1] == cb[1] and (ca[0] - cb[0]) % 12 in (1, 11):
        return "neighbour"  # une case à côté sur la roue
    return None


def tempo_gap(target, bpm):
    """Écart de vitesse (0.02 = 2 %), en comptant aussi la moitié ou le double. (écart, facteur)."""
    return min((abs(bpm * factor / target - 1), factor) for factor in (1, 2, 0.5))


KEY_POINTS = {"same": 1.0, "relative": 0.85, "neighbour": 0.75, "unknown": 0.5}


def compatibility(target_bpm, target_key, bpm, key):
    """None si ça ne va pas ensemble, sinon {"score" (0-200, pour trier), "badge", "gap", "factor", "match"}."""
    match = key_match(target_key, key)
    if match is None:
        return None
    if target_bpm and bpm:
        gap, factor = tempo_gap(target_bpm, bpm)
        if gap > TEMPO_TOLERANCE:
            return None
        tempo_points = 1 - gap / TEMPO_TOLERANCE
    elif target_bpm:  # morceau sans tempo connu : on ne peut pas dire
        return None
    else:
        gap, factor, tempo_points = 0, 1, 0.5
    score = round(100 * (0.6 * KEY_POINTS[match] + 0.4 * tempo_points))
    perfect = bool(match in ("same", "relative") and gap <= PERFECT_TEMPO and target_bpm)
    # Les « Parfait » passent toujours avant les « Ça va bien »
    return {"score": score + (100 if perfect else 0), "badge": "💚 Parfait" if perfect else "👍 Ça va bien",
            "gap": gap, "factor": factor, "match": match}


# --- Le journal de Mixxx ---
DECK_LINE = re.compile(r"BIP:DECK (\d) bpm=([\d.]+) key=(\d+) duration=([\d.]+)")


def parse_deck_line(line):
    """« … BIP:DECK 1 bpm=120.00 key=22 duration=215.3 » -> {"deck", "bpm", "key", "duration"} ou None."""
    match = DECK_LINE.search(line)
    if not match:
        return None
    return {"deck": int(match.group(1)), "bpm": float(match.group(2)), "key": int(match.group(3)),
            "duration": float(match.group(4))}


class LogWatcher(QObject):
    """Lit les nouvelles lignes du journal de Mixxx toutes les 1,5 s et signale chaque morceau chargé."""
    loaded = pyqtSignal(dict)

    def __init__(self, path=None, parent=None):
        super().__init__(parent)
        self.path = Path(path or MIXXX_LOG)
        self.inode = None
        self.offset = 0
        self.rest = b""
        self.decks = {}  # dernier morceau connu de chaque platine
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)

    def start(self):
        """Ne réagit qu'aux nouvelles lignes ; ce qui est déjà dans le journal sert juste à connaître les platines."""
        try:
            stat = self.path.stat()
            self.inode, self.offset = stat.st_ino, stat.st_size
            with self.path.open("rb") as f:
                f.seek(max(0, stat.st_size - 512 * 1024))
                for line in f.read().decode(errors="replace").splitlines():
                    info = parse_deck_line(line)
                    if info:
                        self.decks[info["deck"]] = info
        except OSError:
            pass
        self.timer.start(LOG_POLL_MS)

    def poll(self):
        try:
            stat = self.path.stat()
        except OSError:
            return
        if stat.st_ino != self.inode or stat.st_size < self.offset:
            # Mixxx (ou Bip) a commencé un nouveau journal : on le lit depuis le début
            self.inode, self.offset, self.rest = stat.st_ino, 0, b""
            self.decks = {}
        if stat.st_size == self.offset:
            return
        try:
            with self.path.open("rb") as f:
                f.seek(self.offset)
                data = f.read()
        except OSError:
            return
        self.offset += len(data)
        *lines, self.rest = (self.rest + data).split(b"\n")
        for line in lines:
            info = parse_deck_line(line.decode(errors="replace"))
            if info:
                self.decks[info["deck"]] = info
                self.loaded.emit(info)


# --- Sa musique ---
def simplify(text):
    """Pour comparer des noms : minuscules, sans accents, sans « (Remastered) » ni « feat. »."""
    text = unicodedata.normalize("NFD", (text or "").lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[\(\[].*?([\)\]]|$)", " ", text)
    text = re.sub(r"\s+-\s+.*$|\b(feat|ft|featuring|with)\b.*$", " ", text)
    return " ".join(re.findall(r"[a-z0-9]+", text))


def same_song(artist_a, title_a, artist_b, title_b):
    title_a, title_b = simplify(title_a), simplify(title_b)
    if not title_a or title_a != title_b:
        return False
    words_a = set(simplify(artist_a).split()) - {"the", "les", "le", "la"}
    words_b = set(simplify(artist_b).split()) - {"the", "les", "le", "la"}
    return bool(words_a & words_b) or not words_a or not words_b


def name_from_path(path):
    artist, _, title = Path(path).stem.partition(" - ")
    return (artist, title) if title else ("", artist)


def is_stem(path):
    return "Pistes séparées" in str(path)


def load_tubes_cache():
    try:
        cache = json.loads(TUBES_FILE.read_text())
        return cache if isinstance(cache, dict) else {}
    except (OSError, ValueError):
        return {}


def analysed_tubes(cache=None):
    """Les tubes dont on connaît la vitesse : [{"id", "title", "artist", "cover", "bpm", "key"}]."""
    cache = load_tubes_cache() if cache is None else cache
    analyses = cache.get("analyses", {})
    tubes = []
    for tube in cache.get("tubes", []):
        found = analyses.get(str(tube["id"]))
        if found and found.get("bpm"):
            tubes.append({**tube, "bpm": found["bpm"], "key": key_from_text(found.get("key", ""))})
    return tubes


def load_library():
    """Les morceaux de sa musique avec leur vitesse et leur tonalité : d'abord ce que Mixxx en sait,
    sinon l'analyse de Ma Musique, sinon (morceau tout juste téléchargé) celle de l'extrait du tube."""
    songs = {}
    if MIXXX_DB.exists():
        try:
            db = sqlite3.connect(f"file:{MIXXX_DB}?mode=ro", uri=True, timeout=2)
            rows = db.execute("SELECT t.location, l.artist, l.title, l.duration, l.bpm, l.key_id FROM library l "
                              "JOIN track_locations t ON l.location = t.id "
                              "WHERE l.mixxx_deleted = 0 AND t.fs_deleted = 0").fetchall()
            db.close()
        except sqlite3.Error:
            rows = []
        for location, artist, title, duration, bpm, key in rows:
            songs[location] = {"path": location, "artist": artist or "", "title": title or Path(location).stem,
                               "duration": duration or 0, "bpm": bpm or 0, "key": key or 0}
    try:
        analyses = json.loads(ANALYSES_FILE.read_text())
    except (OSError, ValueError):
        analyses = {}
    for location, info in analyses.items():
        artist, title = name_from_path(location)
        song = songs.setdefault(location, {"path": location, "artist": artist, "title": title,
                                           "duration": info.get("duration") or 0, "bpm": 0, "key": 0})
        if not song["bpm"] or not song["key"]:
            song["bpm"] = song["bpm"] or info.get("bpm") or 0
            song["key"] = song["key"] or key_from_text(info.get("key", ""))
        song["duration"] = song["duration"] or info.get("duration") or 0
    # Morceaux téléchargés depuis « Des tubes à découvrir » et pas encore analysés
    missing = [p for p in MUSIC_DIR.glob("*.mp3") if str(p) not in songs or not songs[str(p)]["bpm"]]
    if missing:
        tubes = analysed_tubes()
        for path in missing:
            artist, title = name_from_path(path)
            tube = next((t for t in tubes if same_song(t["artist"], t["title"], artist, title)), None)
            if tube:
                songs[str(path)] = {"path": str(path), "artist": artist, "title": title, "bpm": tube["bpm"],
                                    "key": tube["key"], "duration": tube.get("duration", 0)}
    return [s for s in songs.values() if Path(s["path"]).exists()]


def identify(info, library):
    """Le morceau de sa musique qui a la même durée (±1 s) et la même vitesse (±0,5) : None si on ne sait pas."""
    found = [s for s in library if abs(s["duration"] - info["duration"]) <= 1
             and abs((s["bpm"] or 0) - info["bpm"]) <= 0.5]
    found.sort(key=lambda s: (is_stem(s["path"]), abs(s["duration"] - info["duration"])))  # l'original d'abord
    return found[0] if found else None


def suggestions(info, library, tubes, current=None):
    """(liste A « dans ta musique », liste B « tubes à découvrir »), du plus au moins compatible."""
    mine = []
    for song in library:
        if is_stem(song["path"]) or song["duration"] < 30 or not song["bpm"]:
            continue  # pas les pistes séparées ni les petits sons à scratcher
        if current and (song["path"] == current["path"]
                        or same_song(song["artist"], song["title"], current["artist"], current["title"])):
            continue
        match = compatibility(info["bpm"], info["key"], song["bpm"], song["key"])
        if match:
            mine.append({**song, **match})
    mine.sort(key=lambda s: -s["score"])
    found = []
    for rank, tube in enumerate(tubes):
        if any(same_song(s["artist"], s["title"], tube["artist"], tube["title"]) for s in library):
            continue  # déjà dans sa musique
        match = compatibility(info["bpm"], info["key"], tube["bpm"], tube["key"])
        if match:
            found.append({**tube, **match, "rank": rank})
    found.sort(key=lambda t: (-t["score"], t["rank"]))
    return mine[:MAX_MINE], found[:MAX_TUBES]


def describe(bpm, key):
    speed = f"🏃 vitesse {round(bpm)}" if bpm else "🏃 vitesse inconnue"
    return f"{speed} · 🎵 {key_name(key)}"


# --- Construction du cache des tubes, en arrière-plan ---
def tubes_need_work():
    cache = load_tubes_cache()
    if not cache.get("tubes") or time.time() - cache.get("refreshed", 0) > REFRESH_DAYS * 86400:
        return True
    analyses = cache.get("analyses", {})
    return any(str(t["id"]) not in analyses for t in cache["tubes"])


class TubesBuilder(QObject):
    """Lance bip/tubes.py tout doucement (jamais pendant que Mixxx joue, pour ne pas faire craquer le son)."""
    progress = pyqtSignal(int, int)
    changed = pyqtSignal()  # de nouveaux tubes sont prêts

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc = None
        self.done, self.total = 0, 0
        self.last_reload = 0

    @staticmethod
    def available():
        return SEPARATOR_PY.exists() and TUBES_SCRIPT.exists()

    def running(self):
        return self.proc is not None

    def start(self):
        if self.proc or not self.available() or not tubes_need_work():
            return
        self.proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("CUDA_VISIBLE_DEVICES", "")  # la carte graphique reste pour l'IA et la séparation
        env.insert("OMP_NUM_THREADS", "2")
        env.insert("BIP_TUBES", str(TUBES_FILE))
        self.proc.setProcessEnvironment(env)
        self.proc.setProgram("nice")
        self.proc.setArguments(["-n", "19", str(SEPARATOR_PY), "-I", str(TUBES_SCRIPT)])
        self.proc.setStandardErrorFile(QProcess.nullDevice())
        self.proc.readyReadStandardOutput.connect(self.output)
        self.proc.finished.connect(self.finished)
        self.proc.start()

    def stop(self):
        if self.proc:
            self.proc.finished.disconnect(self.finished)
            self.proc.kill()  # chaque tube est enregistré dès qu'il est analysé : rien n'est perdu
            self.proc.waitForFinished(2000)
            self.proc = None
            self.changed.emit()

    def output(self):
        for line in bytes(self.proc.readAllStandardOutput()).decode(errors="replace").splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[0] == "PROGRESS":
                self.done, self.total = int(parts[1]), int(parts[2])
                self.progress.emit(self.done, self.total)
                if time.time() - self.last_reload > 60:  # la page se met à jour de temps en temps
                    self.last_reload = time.time()
                    self.changed.emit()

    def finished(self):
        self.proc.deleteLater()
        self.proc = None
        self.changed.emit()


# --- La page ---
STYLE = """
QLabel#track { font-size: 21px; font-weight: bold; color: #1a1a1a; }
QLabel#trackinfo { font-size: 17px; color: #1a3d66; }
QLabel#section { font-size: 20px; font-weight: bold; color: #1a3d66; margin-top: 10px; }
QLabel#hint { font-size: 15px; color: #555; font-style: italic; }
QFrame#song { background: white; border: 2px solid #ddd; border-radius: 14px; }
QFrame#song:hover { border-color: #4a90e2; background: #f5f9ff; }
QLabel#songtitle { font-size: 17px; font-weight: bold; color: #1a1a1a; }
QLabel#songinfo { font-size: 14px; color: #555; }
QLabel#perfect { background: #43a047; color: white; font-weight: bold; border-radius: 9px; padding: 2px 8px; }
QLabel#good { background: #eef4fc; color: #1a3d66; font-weight: bold; border: 2px solid #4a90e2;
              border-radius: 9px; padding: 1px 7px; }
QLabel#grip { font-size: 26px; color: #4a90e2; }
QLabel#cover { background: #eef4fc; border-radius: 8px; font-size: 28px; }
QPushButton#listen { background: #8e24aa; font-size: 15px; padding: 6px 10px; }
QPushButton#listen:hover { background: #6a1b9a; }
QPushButton#get { background: #43a047; font-size: 15px; padding: 6px 10px; }
QPushButton#get:hover { background: #388e3c; }
QPushButton#deck { background: #eef4fc; color: #1a3d66; border: 2px solid #4a90e2; font-size: 15px; padding: 4px 10px; }
QPushButton#deck:checked { background: #4a90e2; color: white; }
QProgressBar { border: 2px solid #4a90e2; border-radius: 8px; text-align: center; font-weight: bold;
               min-height: 20px; background: #eef4fc; color: #1a3d66; }
QProgressBar::chunk { border-radius: 6px; background: #43a047; }
"""


def badge(text):
    label = QLabel(text)
    label.setObjectName("perfect" if "Parfait" in text else "good")
    return label


class SongRow(QFrame):
    """Une ligne de morceau : pochette, badge, titre, artiste, vitesse et tonalité."""

    def __init__(self, song):
        super().__init__()
        self.setObjectName("song")
        self.song = song
        self.outer = QHBoxLayout(self)
        self.outer.setContentsMargins(8, 8, 8, 8)
        self.cover = QLabel()
        self.cover.setObjectName("cover")
        self.cover.setFixedSize(56, 56)
        self.cover.setScaledContents(True)
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setText("🎵")  # en attendant la pochette (ou s'il n'y en a pas)
        self.outer.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignTop)
        self.texts = QVBoxLayout()
        self.texts.setSpacing(2)
        top = QHBoxLayout()
        top.addWidget(badge(song["badge"]))
        top.addStretch()
        self.texts.addLayout(top)
        title = QLabel(song["title"])
        title.setObjectName("songtitle")
        title.setWordWrap(True)
        self.texts.addWidget(title)
        info = QLabel(f"{song['artist']}\n{describe(song['bpm'], song['key'])}" if song["artist"]
                      else describe(song["bpm"], song["key"]))
        info.setObjectName("songinfo")
        info.setWordWrap(True)
        self.texts.addWidget(info)
        self.outer.addLayout(self.texts, 1)

    def set_cover(self, data):
        pixmap = QPixmap()
        if data and pixmap.loadFromData(data):
            self.cover.setPixmap(pixmap)
            self.cover.setStyleSheet("background: transparent;")


class DragRow(SongRow):
    """Un morceau de sa musique, à glisser sur une platine de Mixxx."""

    def __init__(self, song):
        super().__init__(song)
        self.press_pos = None
        grip = QLabel("✋")
        grip.setObjectName("grip")
        self.outer.addWidget(grip, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Glisse-moi sur une platine de Mixxx !")
        try:
            tags = AudioFile(song["path"]).tags
            pics = tags.getall("APIC") if tags is not None and hasattr(tags, "getall") else []
            self.set_cover(pics[0].data if pics else None)
        except (MutagenError, OSError):
            pass

    def mousePressEvent(self, event):
        self.press_pos = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if self.press_pos is None or (event.position().toPoint() - self.press_pos).manhattanLength() \
                < QApplication.startDragDistance():
            return
        self.press_pos = None
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(self.song["path"]))])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(self.grab().scaledToWidth(220, Qt.TransformationMode.SmoothTransformation))
        drag.exec(Qt.DropAction.CopyAction)

    def mouseReleaseEvent(self, event):
        self.press_pos = None


class TubeRow(SongRow):
    """Un tube à découvrir : l'écouter (extrait de 30 s) ou le télécharger dans sa musique."""

    def __init__(self, song, page):
        super().__init__(song)
        buttons = QHBoxLayout()
        self.listen = QPushButton("▶ Écouter")
        self.listen.setObjectName("listen")
        self.listen.clicked.connect(lambda: page.toggle_preview(self))
        self.get = QPushButton("⬇ Télécharger")
        self.get.setObjectName("get")
        self.get.clicked.connect(lambda: page.download(self))
        buttons.addWidget(self.listen)
        buttons.addWidget(self.get)
        buttons.addStretch()
        self.texts.addLayout(buttons)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        self.texts.addWidget(self.progress)

    def show_progress(self, percent, text):
        self.progress.show()
        self.progress.setValue(int(percent))
        self.progress.setFormat(text.replace("%", "%%"))


class CaVaAvecPage(QWidget):
    """La page « Ça va avec … » de Bip."""

    def __init__(self, back_button, parent=None):
        super().__init__(parent)
        self.setStyleSheet(STYLE)
        self.net = QNetworkAccessManager(self)
        self.decks = {}
        self.deck = None
        self.library = []
        self.tube_rows = {}
        self.playing = None
        self.job = None          # téléchargement en cours : (tube, SongDownload)
        self.downloaded = {}     # chemin du MP3 -> tube, pour les morceaux téléchargés ici
        self.builder_text = ""
        self.audio = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.player.playbackStateChanged.connect(self.playback_changed)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(back_button)
        question = QLabel("🎧 Qu'est-ce qui va avec ?")
        question.setObjectName("question")
        root.addWidget(question)
        self.deck_row = QHBoxLayout()
        self.deck_buttons = {}
        for deck in (1, 2):
            button = QPushButton(f"Platine {deck} ({'gauche' if deck == 1 else 'droite'})")
            button.setObjectName("deck")
            button.setCheckable(True)
            button.clicked.connect(lambda _, d=deck: self.show_deck(d))
            self.deck_buttons[deck] = button
            self.deck_row.addWidget(button)
        root.addLayout(self.deck_row)
        self.track = QLabel()
        self.track.setObjectName("track")
        self.track.setWordWrap(True)
        root.addWidget(self.track)
        self.track_info = QLabel()
        self.track_info.setObjectName("trackinfo")
        self.track_info.setWordWrap(True)
        root.addWidget(self.track_info)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(0, 0, 4, 0)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        mine_title = QLabel("🎵 Dans ta musique")
        mine_title.setObjectName("section")
        self.mine_hint = QLabel()
        self.mine_hint.setObjectName("hint")
        self.mine_hint.setWordWrap(True)
        self.mine_box = QVBoxLayout()
        tubes_title = QLabel("🌟 Des tubes à découvrir")
        tubes_title.setObjectName("section")
        self.tubes_hint = QLabel()
        self.tubes_hint.setObjectName("hint")
        self.tubes_hint.setWordWrap(True)
        self.tubes_box = QVBoxLayout()
        for item in (mine_title, self.mine_hint, self.mine_box, tubes_title, self.tubes_hint, self.tubes_box):
            (self.body.addLayout if isinstance(item, QVBoxLayout) else self.body.addWidget)(item)
        self.body.addStretch()
        self.show_deck(None)

    # --- Platines ---
    def set_decks(self, decks):
        """Ce qu'on sait déjà des platines (au démarrage de Bip), sans changer de page."""
        self.decks = dict(decks)
        if self.decks and self.deck not in self.decks:
            self.show_deck(max(self.decks))

    def deck_loaded(self, info):
        self.decks[info["deck"]] = info
        self.show_deck(info["deck"])

    def show_deck(self, deck):
        self.deck = deck
        for d, button in self.deck_buttons.items():
            button.setVisible(len(self.decks) > 1)
            button.setChecked(d == deck)
        self.refresh()

    def refresh(self):
        self.stop_preview()
        info = self.decks.get(self.deck)
        if not info:
            self.track.setText("Charge un morceau sur une platine de Mixxx 🎛")
            self.track_info.setText("Je te dirai ce qui va bien avec !")
            self.fill([], [])
            self.mine_hint.setText("")
            self.update_tubes_hint()
            return
        self.library = load_library()
        analysed = {s["path"] for s in self.library if s["bpm"]}
        for path, tube in self.downloaded.items():
            if path not in analysed and Path(path).exists():  # en attendant l'analyse de Ma Musique
                artist, title = name_from_path(path)
                self.library = [s for s in self.library if s["path"] != path] + [
                    {"path": path, "artist": artist, "title": title, "bpm": tube["bpm"], "key": tube["key"],
                     "duration": tube.get("duration", 0)}]
        current = identify(info, self.library)
        if current:
            self.track.setText(f"« {current['title']} »" + (f"\nde {current['artist']}" if current["artist"] else ""))
        else:
            speed = f"{round(info['bpm'])} BPM" if info["bpm"] else "vitesse inconnue"
            self.track.setText(f"Ton morceau ({speed}, {key_name(info['key'])})")
        side = "gauche" if info["deck"] == 1 else "droite"
        self.track_info.setText(f"Platine de {side} · {describe(info['bpm'], info['key'])}")
        if not info["bpm"]:  # un petit son à scratcher, ou Mixxx n'a pas trouvé le rythme
            self.mine_hint.setText("Mixxx ne connaît pas la vitesse de ce morceau, alors je ne peux pas t'aider 🤷")
            self.fill([], [])
            self.tubes_hint.hide()
            return
        fetched = {t["id"] for t in self.downloaded.values()}
        tubes = [t for t in analysed_tubes() if t["id"] not in fetched]
        mine, tubes = suggestions(info, self.library, tubes, current)
        other = "droite 👉" if info["deck"] == 1 else "gauche 👈"
        self.mine_hint.setText(f"✋ Glisse-le sur l'autre platine (celle de {other})" if mine else
                               "Rien dans ta musique ne va vraiment avec celui-là… Regarde les tubes en dessous ! 👇")
        self.fill(mine, tubes)

    def fill(self, mine, tubes):
        for box in (self.mine_box, self.tubes_box):
            while box.count():
                widget = box.takeAt(0).widget()
                if widget:
                    widget.deleteLater()
        self.tube_rows = {}
        for song in mine:
            self.mine_box.addWidget(DragRow(song))
        for tube in tubes:
            row = TubeRow(tube, self)
            self.tube_rows[tube["id"]] = row
            self.tubes_box.addWidget(row)
            self.load_cover(row, tube.get("cover"))
            if self.job:
                row.get.setEnabled(False)
                if self.job[0]["id"] == tube["id"]:
                    row.show_progress(0, "⬇ Téléchargement…")
        self.update_tubes_hint(bool(tubes))

    def update_tubes_hint(self, any_tube=None):
        if any_tube is None:
            any_tube = bool(self.tube_rows)
        text = self.builder_text
        if not any_tube and self.deck in self.decks:
            text = text or ("Je n'ai pas encore trouvé de tube qui va avec 🤔" if analysed_tubes()
                            else "Je n'ai pas encore découvert de tubes. Il faut Internet 🌍")
        self.tubes_hint.setText(text)
        self.tubes_hint.setVisible(bool(text))

    def set_builder_text(self, text):
        self.builder_text = text
        self.update_tubes_hint()

    def load_cover(self, row, url):
        if not url:
            return
        reply = self.net.get(QNetworkRequest(QUrl(url)))

        def done():
            try:
                row.set_cover(bytes(reply.readAll()))
            except RuntimeError:  # la ligne a déjà disparu
                pass
            reply.deleteLater()
        reply.finished.connect(done)

    # --- Écouter un extrait (un seul à la fois) ---
    def toggle_preview(self, row):
        previous = self.playing
        self.stop_preview()
        if previous is row:
            return
        self.playing = row
        row.listen.setText("⏹ Stop")
        # L'adresse de l'extrait expire vite : on la redemande à Deezer juste avant d'écouter
        reply = self.net.get(QNetworkRequest(QUrl(f"{DEEZER}/track/{row.song['id']}")))

        def done():
            reply.deleteLater()
            if self.playing is not row:
                return
            try:
                url = json.loads(bytes(reply.readAll())).get("preview") \
                    if reply.error() == QNetworkReply.NetworkError.NoError else None
            except ValueError:
                url = None
            if not url:
                self.stop_preview()
                row.listen.setText("😕 Réessaie")
                return
            self.player.setSource(QUrl(url))
            self.player.play()
        reply.finished.connect(done)

    def stop_preview(self):
        row, self.playing = self.playing, None
        self.player.stop()
        if row:
            try:
                row.listen.setText("▶ Écouter")
            except RuntimeError:
                pass

    def playback_changed(self, state):
        if state == QMediaPlayer.PlaybackState.StoppedState and self.playing \
                and self.player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia:
            self.stop_preview()

    def hideEvent(self, event):
        self.stop_preview()
        super().hideEvent(event)

    # --- Télécharger un tube dans sa musique (comme pour un mix : recherche YouTube de Ma Musique) ---
    def download(self, row):
        if self.job:
            return
        tube = row.song
        job = mixeur.SongDownload(f"{tube['artist']} {tube['title']}", self)
        self.job = (tube, job)
        for other in self.tube_rows.values():
            other.get.setEnabled(False)
        job.progress.connect(lambda percent, text: self.download_progress(tube["id"], percent, text))
        job.finished.connect(self.download_done)
        row.show_progress(0, "🔎 Je cherche la chanson…")
        job.start()

    def download_progress(self, tube_id, percent, text):
        row = self.tube_rows.get(tube_id)
        if row:
            row.show_progress(percent, text)

    def download_done(self, path, message):
        tube, job = self.job
        job.deleteLater()
        self.job = None
        if path:
            self.downloaded[str(path)] = tube
            self.refresh()  # le morceau passe dans « Dans ta musique »
            self.mine_hint.setText(f"🎉 « {tube['title']} » est dans ta musique !\n" + self.mine_hint.text())
            return
        row = self.tube_rows.get(tube["id"])
        for other in self.tube_rows.values():
            other.get.setEnabled(True)
        if row:
            row.show_progress(0, message)
