#!/usr/bin/env python3
"""Défis DJ : un jeu de rythme façon DJ Hero, sur les chansons de l'enfant.

Les notes tombent sur les vrais coups de la musique (fabriquées par charter.py) : on tape en rythme avec
les flèches ← ↓ →. Score, combos, étoiles et records. Le défi dure environ une minute autour du refrain.
"""
import json
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QElapsedTimer, QPointF, QProcess, QRectF, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter, QPen, QPixmap, QPolygonF
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer, QSoundEffect
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QProgressBar,
                             QPushButton, QStackedWidget, QVBoxLayout, QWidget)

HOME = Path.home()
sys.path.insert(0, str(HOME / ".local/share/ma-musique"))
from bibliotheque import (ANALYSES_FILE, ANALYSIS_VERSION, SEPARATOR_PY, list_songs, load_analyses,  # noqa: E402
                          read_song, stems_of)

APP_DIR = Path(__file__).resolve().parent
CHARTER = APP_DIR / "charter.py"
ANALYSER = HOME / ".local/share/ma-musique/analyser.py"
DATA = HOME / ".local/share/studio-dj-kids"
CHARTS_DIR = DATA / "defis-dj"
SCORES_FILE = DATA / "defis-dj-scores.json"

LEVELS = {"facile": "⭐ Facile", "moyen": "⭐⭐ Moyen", "expert": "⭐⭐⭐ Expert"}
SPEED = {"facile": 2.2, "moyen": 1.8, "expert": 1.4}  # secondes pendant lesquelles on voit une note arriver
LANE_COLORS = [QColor("#2ecc40"), QColor("#ff4136"), QColor("#2f8bff")]  # vert, rouge, bleu, comme DJ Hero
LANE_KEYS = [{Qt.Key.Key_Left, Qt.Key.Key_1, Qt.Key.Key_D}, {Qt.Key.Key_Down, Qt.Key.Key_2, Qt.Key.Key_F},
             {Qt.Key.Key_Right, Qt.Key.Key_3, Qt.Key.Key_J}]
LANE_HINTS = ["←", "↓", "→"]
# Pads « sons à envoyer » (bonus, jamais de punition) : touches A S E R
PAD_KEYS = [Qt.Key.Key_A, Qt.Key.Key_S, Qt.Key.Key_E, Qt.Key.Key_R]
PAD_NAMES = ["airhorn", "scratch", "zap", "boom"]
PAD_LABELS = ["A", "S", "E", "R"]
PAD_COLORS = [QColor("#ffb300"), QColor("#8e44ff"), QColor("#00d0c0"), QColor("#ff4fd8")]
SOUNDS_DIR = APP_DIR / "sounds"
PERFECT, GOOD, MISS = 0.06, 0.12, 0.15  # fenêtres de tir (s)
AUDIO_LATENCY = 0.06  # le son sort un peu après ce que dit le lecteur
LEAD_IN = 2.5  # secondes avant la première note

STYLE = """
QWidget { background: #120b2e; color: white; font-size: 18px; }
QLabel#title { font-size: 40px; font-weight: bold; color: #ff4fd8; }
QLabel#big { font-size: 28px; font-weight: bold; }
QListWidget { background: #1d1248; border: 3px solid #6c3cff; border-radius: 14px; font-size: 19px; }
QListWidget::item { padding: 8px; }
QListWidget::item:selected { background: #6c3cff; }
QPushButton { font-size: 22px; font-weight: bold; padding: 12px 22px; border-radius: 16px;
              background: #6c3cff; color: white; border: none; }
QPushButton:hover { background: #8a5cff; }
QPushButton:disabled { background: #4a4466; color: #999; }
QPushButton#level:checked { background: #ff4fd8; border: 4px solid #ffe14f; }
QPushButton#play { background: #2ecc40; font-size: 28px; }
QProgressBar { border: 2px solid #6c3cff; border-radius: 10px; text-align: center; background: #1d1248;
               min-height: 26px; }
QProgressBar::chunk { border-radius: 8px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                      stop:0 #6c3cff, stop:1 #ff4fd8); }
"""


def load_scores():
    try:
        return json.loads(SCORES_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    tmp.replace(path)


def stars_for(accuracy):
    return 5 if accuracy >= 0.95 else 4 if accuracy >= 0.85 else 3 if accuracy >= 0.7 else 2 if accuracy >= 0.5 else 1


class Highway(QWidget):
    """La piste : les notes arrivent du fond vers la ligne de frappe, en perspective."""

    def __init__(self, game):
        super().__init__()
        self.game = game
        self.setMinimumSize(700, 500)

    def lane_x(self, lane, depth):
        """Position horizontale du centre d'une colonne ; depth 0 = ligne de frappe, 1 = tout au fond."""
        w = self.width()
        half = (0.42 - 0.27 * depth) * w  # la piste rétrécit au loin
        return w / 2 + (lane - 1) * (2 * half / 3)

    def y_of(self, depth):
        top, hit = self.height() * 0.06, self.height() * 0.84
        return hit - (hit - top) * (1 - (1 - depth) ** 1.6)

    def paintEvent(self, _event):
        game = self.game
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        painter.fillRect(self.rect(), QColor("#120b2e"))
        # La piste
        corners = [QPointF(self.lane_x(-0.5, 1), self.y_of(1)), QPointF(self.lane_x(2.5, 1), self.y_of(1)),
                   QPointF(self.lane_x(2.5, 0) + 30, h), QPointF(self.lane_x(-0.5, 0) - 30, h)]
        gradient = QLinearGradient(0, 0, 0, h)
        gradient.setColorAt(0, QColor("#1d1248"))
        gradient.setColorAt(1, QColor("#3b1f8c"))
        painter.setBrush(QBrush(gradient))
        painter.setPen(QPen(QColor("#6c3cff"), 3))
        painter.drawPolygon(QPolygonF(corners))
        now = game.now()
        window = SPEED[game.level]
        # Les barres de mesure qui défilent
        painter.setPen(QPen(QColor(255, 255, 255, 40), 2))
        bar = 4 * 60 / game.chart["bpm"]
        first = int(now // bar) + 1
        for k in range(first, first + 6):
            depth = (k * bar - now) / window
            if 0 <= depth <= 1:
                y = self.y_of(depth)
                painter.drawLine(QPointF(self.lane_x(-0.5, depth), y), QPointF(self.lane_x(2.5, depth), y))
        # Ligne de frappe et boutons
        hit_y = self.y_of(0)
        for lane in range(3):
            x = self.lane_x(lane, 0)
            color = QColor(LANE_COLORS[lane])
            pressed = time.monotonic() - game.pressed_at[lane] < 0.12
            color.setAlpha(255 if pressed else 110)
            painter.setBrush(color)
            painter.setPen(QPen(QColor("white"), 4 if pressed else 2))
            painter.drawEllipse(QPointF(x, hit_y), 46, 24)
            painter.setPen(QColor("white"))
            painter.setFont(QFont(self.font().family(), 22, QFont.Weight.Bold))
            painter.drawText(QRectF(x - 40, hit_y + 26, 80, 40), Qt.AlignmentFlag.AlignCenter, LANE_HINTS[lane])
        # Les notes
        for note in game.visible_notes(now, window):
            depth = (note["t"] - now) / window
            x, y = self.lane_x(note["lane"], depth), self.y_of(depth)
            size = 1 - 0.6 * depth
            painter.setBrush(LANE_COLORS[note["lane"]])
            painter.setPen(QPen(QColor("white"), 3))
            painter.drawEllipse(QPointF(x, y), 40 * size, 20 * size)
        # Pads (sons à envoyer) : rangée de 4 en bas + marqueur qui tombe quand un cue approche
        pads = getattr(game, "pads", [])
        if pads:
            pad_w = w * 0.6 / 4
            x0, pad_y, top = w * 0.2, h - 48, self.y_of(1)
            for i in range(4):
                cx = x0 + pad_w * (i + 0.5)
                upcoming = [p for p in pads if not p["done"] and p["pad"] == i and -0.1 <= p["t"] - now <= window]
                glow = 0.22
                if upcoming:
                    frac = max(0.0, min(1.0, 1 - min(p["t"] - now for p in upcoming) / window))
                    glow = 0.22 + 0.78 * frac
                    painter.setBrush(PAD_COLORS[i])
                    painter.setPen(QPen(QColor("white"), 2))
                    painter.drawEllipse(QPointF(cx, top + frac * (pad_y - top)), 15, 15)
                pressed = time.monotonic() - game.pad_pressed_at[i] < 0.12
                fill = QColor("white") if pressed else QColor(PAD_COLORS[i])
                if not pressed:
                    fill.setAlphaF(glow)
                painter.setBrush(fill)
                painter.setPen(QPen(PAD_COLORS[i], 3))
                painter.drawRoundedRect(QRectF(cx - pad_w * 0.4, pad_y - 18, pad_w * 0.8, 36), 10, 10)
                painter.setPen(QColor("white"))
                painter.setFont(QFont(self.font().family(), 16, QFont.Weight.Bold))
                painter.drawText(QRectF(cx - pad_w * 0.4, pad_y - 18, pad_w * 0.8, 36),
                                 Qt.AlignmentFlag.AlignCenter, PAD_LABELS[i])
        # Message (Parfait / Bien / Raté)
        if game.flash and time.monotonic() - game.flash[2] < 0.5:
            text, color, _ = game.flash
            painter.setPen(QColor(color))
            painter.setFont(QFont(self.font().family(), 34, QFont.Weight.Black))
            painter.drawText(QRectF(0, h * 0.42, w, 60), Qt.AlignmentFlag.AlignCenter, text)
        if game.countdown:
            painter.setPen(QColor("#ffe14f"))
            painter.setFont(QFont(self.font().family(), 90, QFont.Weight.Black))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, game.countdown)
        painter.end()


class Game(QWidget):
    """Une partie : la musique, les notes, le score."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.audio = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.player.positionChanged.connect(self.position_changed)
        self.clock = QElapsedTimer()
        self.last_position = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.frame)
        self.chart, self.level, self.notes = None, "facile", []
        self.pressed_at = [0.0, 0.0, 0.0]
        self.flash = None
        self.countdown = ""
        # Pads (sons à envoyer) : état + chargement des one-shots
        self.pads = []
        self.pad_pressed_at = [0.0, 0.0, 0.0, 0.0]
        self.pad_sounds = []
        for name in PAD_NAMES:
            effect = QSoundEffect(self)
            effect.setSource(QUrl.fromLocalFile(str(SOUNDS_DIR / f"{name}.wav")))
            effect.setVolume(0.9)
            self.pad_sounds.append(effect)

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        self.score_label = QLabel()
        self.score_label.setObjectName("big")
        self.combo_label = QLabel()
        self.combo_label.setObjectName("big")
        self.combo_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        top.addWidget(self.score_label, 1)
        top.addWidget(self.combo_label, 1)
        root.addLayout(top)
        self.highway = Highway(self)
        root.addWidget(self.highway, 1)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        root.addWidget(self.progress)
        hint = QLabel("Tape ← ↓ → sur les notes • A S E R = envoie des sons (pads) • Échap : pause")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(hint)

    def start(self, song, chart, level):
        self.song, self.chart, self.level = song, chart, level
        self.notes = [{"t": t, "lane": lane, "done": None} for t, lane in chart["notes"][level]]
        self.pads = [{"t": t, "pad": pad, "done": None} for t, pad in chart.get("pads", [])]
        self.score = self.combo = self.best_combo = 0
        self.flash = None
        self.length = chart["end"] - chart["start"]
        self.update_labels()
        self.player.setSource(QUrl.fromLocalFile(str(song)))
        self.player.setPosition(int((chart["start"] - LEAD_IN) * 1000))
        self.last_position = -LEAD_IN
        self.paused = False
        self.countdown_left = 3
        self.countdown = "3"
        self.timer.start(16)
        QTimer.singleShot(700, self.count)
        self.setFocus()

    def count(self):
        self.countdown_left -= 1
        if self.countdown_left > 0:
            self.countdown = str(self.countdown_left)
            QTimer.singleShot(700, self.count)
        else:
            self.countdown = ""
            self.player.play()
            self.clock.restart()

    def position_changed(self, ms):
        self.last_position = ms / 1000 - self.chart["start"] if self.chart else 0
        self.clock.restart()

    def now(self):
        """Temps du défi (s), entre deux nouvelles du lecteur on avance avec l'horloge."""
        if not self.chart:
            return 0.0
        moving = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        return self.last_position + (self.clock.elapsed() / 1000 if moving and self.clock.isValid() else 0) - AUDIO_LATENCY

    def visible_notes(self, now, window):
        return [n for n in self.notes if not n["done"] and -0.2 <= n["t"] - now <= window]

    def frame(self):
        now = self.now()
        for note in self.notes:
            if not note["done"] and note["t"] < now - MISS:
                note["done"] = "raté"
                self.combo = 0
                self.flash = ("RATÉ", "#ff4136", time.monotonic())
        for pad in self.pads:  # pad manqué : on l'efface, mais c'est un bonus -> aucune punition
            if not pad["done"] and pad["t"] < now - MISS:
                pad["done"] = "loupé"
        self.progress.setValue(int(max(0, min(1, now / self.length)) * 100))
        self.update_labels()
        self.highway.update()
        if now > self.length + 1.5 and not self.countdown:
            self.finish()

    def multiplier(self):
        return 4 if self.combo >= 24 else 3 if self.combo >= 16 else 2 if self.combo >= 8 else 1

    def update_labels(self):
        self.score_label.setText(f"🏆 {self.score}")
        self.combo_label.setText(f"🔥 {self.combo}  ×{self.multiplier()}" if self.combo else "")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.toggle_pause()
            return
        if event.isAutoRepeat() or self.countdown or getattr(self, "paused", False):
            return
        pad = next((i for i, key in enumerate(PAD_KEYS) if event.key() == key), None)
        if pad is not None:
            self.pad_pressed_at[pad] = time.monotonic()
            self.pad_sounds[pad].play()  # le son part dès qu'on tape (satisfaisant, hit ou pas)
            now = self.now()
            hits = [n for n in self.pads if not n["done"] and n["pad"] == pad and abs(n["t"] - now) <= GOOD]
            if hits:  # pile sur le cue = bonus (jamais de punition si on tape à côté)
                note = min(hits, key=lambda n: abs(n["t"] - now))
                note["done"] = "pad"
                self.combo += 1
                self.best_combo = max(self.best_combo, self.combo)
                self.score += 75 * self.multiplier()
                self.flash = ("PAD !", PAD_COLORS[pad].name(), time.monotonic())
            return
        lane = next((i for i, keys in enumerate(LANE_KEYS) if event.key() in keys), None)
        if lane is None:
            return
        self.pressed_at[lane] = time.monotonic()
        now = self.now()
        candidates = [n for n in self.notes if not n["done"] and n["lane"] == lane and abs(n["t"] - now) <= GOOD]
        if not candidates:
            return  # appui dans le vide : pas de punition
        note = min(candidates, key=lambda n: abs(n["t"] - now))
        perfect = abs(note["t"] - now) <= PERFECT
        note["done"] = "parfait" if perfect else "bien"
        self.combo += 1
        self.best_combo = max(self.best_combo, self.combo)
        self.score += (100 if perfect else 50) * self.multiplier()
        self.flash = ("PARFAIT !", "#2ecc40", time.monotonic()) if perfect else ("BIEN", "#ffe14f", time.monotonic())

    def toggle_pause(self):
        if self.countdown:
            return
        self.paused = not self.paused
        if self.paused:
            self.player.pause()
            self.timer.stop()
            self.app.show_pause()
        else:
            self.player.play()
            self.clock.restart()
            self.timer.start(16)
            self.setFocus()

    def stop(self):
        self.timer.stop()
        self.player.stop()

    def finish(self):
        self.stop()
        counts = {kind: sum(1 for n in self.notes if n["done"] == kind) for kind in ("parfait", "bien", "raté")}
        total = max(1, len(self.notes))
        accuracy = (counts["parfait"] + 0.6 * counts["bien"]) / total
        self.app.show_results(self.song, self.level, self.score, stars_for(accuracy), counts, self.best_combo)


class DefisDJ(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("🎮 Défis DJ")
        self.setStyleSheet(STYLE)
        self.resize(1200, 860)
        self.analyses = load_analyses()
        self.scores = load_scores()
        self.level = "facile"
        self.proc = None
        root = QVBoxLayout(self)
        self.stack = QStackedWidget()
        root.addWidget(self.stack)
        self.stack.addWidget(self.make_menu())
        self.stack.addWidget(self.make_wait())
        self.game = Game(self)
        self.stack.addWidget(self.game)
        self.stack.addWidget(self.make_results())
        self.stack.addWidget(self.make_pause())
        self.fill_songs()

    # --- Menu ---
    def make_menu(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("🎮 Défis DJ")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        hint = QLabel("Choisis une chanson et ton niveau, puis tape en rythme avec les flèches ← ↓ → !")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)
        self.songs = QListWidget()
        self.songs.setIconSize(QSize(64, 64))
        self.songs.itemDoubleClicked.connect(lambda _: self.play())
        layout.addWidget(self.songs, 1)
        levels = QHBoxLayout()
        self.level_buttons = {}
        for level, text in LEVELS.items():
            button = QPushButton(text)
            button.setObjectName("level")
            button.setCheckable(True)
            button.setChecked(level == self.level)
            button.clicked.connect(lambda _, lv=level: self.choose_level(lv))
            levels.addWidget(button)
            self.level_buttons[level] = button
        layout.addLayout(levels)
        play = QPushButton("▶ Jouer !")
        play.setObjectName("play")
        play.setMinimumHeight(80)
        play.clicked.connect(self.play)
        layout.addWidget(play)
        return page

    def choose_level(self, level):
        self.level = level
        for lv, button in self.level_buttons.items():
            button.setChecked(lv == level)
        self.fill_songs()

    def fill_songs(self):
        current = self.songs.currentRow()
        self.songs.clear()
        for path in list_songs():
            best = self.scores.get(f"{path.stem}|{self.level}")
            record = f"    {'⭐' * best['stars']}  🏆 {best['score']}" if best else ""
            item = QListWidgetItem(f"{path.stem}{record}")
            item.setData(Qt.ItemDataRole.UserRole, path)
            cover = read_song(path)[3]
            pixmap = QPixmap()
            if cover and pixmap.loadFromData(cover):
                item.setIcon(QIcon(pixmap))
            self.songs.addItem(item)
        self.songs.setCurrentRow(max(0, current))

    # --- Préparation des notes ---
    def make_wait(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addStretch()
        self.wait_label = QLabel()
        self.wait_label.setObjectName("big")
        self.wait_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.wait_label.setWordWrap(True)
        layout.addWidget(self.wait_label)
        bar = QProgressBar()
        bar.setRange(0, 0)
        layout.addWidget(bar)
        layout.addStretch()
        return page

    def play(self):
        item = self.songs.currentItem()
        if not item or self.proc:
            return
        self.song = item.data(Qt.ItemDataRole.UserRole)
        self.stack.setCurrentIndex(1)
        info = self.analysis(self.song)
        if info:
            self.make_chart(info)
        elif not SEPARATOR_PY.exists():
            self.wait_label.setText("😕 Il manque l'outil d'analyse : demande à papa.")
        else:
            self.wait_label.setText("🎧 J'écoute la chanson pour trouver son rythme…")
            artist, title, _, _ = read_song(self.song)
            self.run([ANALYSER, self.song, artist, title], self.analysis_done)

    def analysis(self, path):
        entry = self.analyses.get(str(path))
        if entry and entry.get("version", 1) >= ANALYSIS_VERSION and entry.get("mtime") == int(path.stat().st_mtime):
            return entry
        return None

    def run(self, args, done):
        self.proc = QProcess(self)
        self.proc.setProgram(str(SEPARATOR_PY))
        self.proc.setArguments([str(a) for a in args])
        self.proc.finished.connect(lambda *_: done(bytes(self.proc.readAllStandardOutput()).decode(errors="replace")))
        self.proc.start()

    def analysis_done(self, out):
        self.proc = None
        try:
            info = json.loads(out.strip().splitlines()[-1])
            info["mtime"] = int(self.song.stat().st_mtime)
        except (ValueError, IndexError, OSError):
            info = None
        if not info or not info.get("bpm"):
            self.wait_label.setText("😕 Je n'ai pas trouvé le rythme de cette chanson. Choisis-en une autre !")
            QTimer.singleShot(2500, lambda: self.stack.setCurrentIndex(0))
            return
        analyses = load_analyses()  # Ma Musique a pu en ajouter entre-temps
        analyses[str(self.song)] = info
        save_json(ANALYSES_FILE, analyses)
        self.analyses = analyses
        self.make_chart(info)

    def chart_file(self, path):
        return CHARTS_DIR / f"{path.stem}.json"

    def make_chart(self, info):
        try:
            chart = json.loads(self.chart_file(self.song).read_text())
            if chart.get("mtime") == info["mtime"]:
                return self.start_game(chart)
        except (OSError, ValueError):
            pass
        self.wait_label.setText("🎛 Je prépare tes notes sur les vrais coups de batterie…")
        drums = stems_of(self.song).get("Batterie")
        args = [CHARTER, self.song, json.dumps(info)] + ([drums] if drums else [])
        self.run(args, lambda out: self.chart_done(out, info))

    def chart_done(self, out, info):
        self.proc = None
        try:
            chart = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            self.wait_label.setText("😕 Oups, je n'ai pas réussi à préparer les notes.")
            QTimer.singleShot(2500, lambda: self.stack.setCurrentIndex(0))
            return
        chart["mtime"] = info["mtime"]
        save_json(self.chart_file(self.song), chart)
        self.start_game(chart)

    def start_game(self, chart):
        self.stack.setCurrentWidget(self.game)
        self.game.start(self.song, chart, self.level)

    # --- Pause ---
    def make_pause(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addStretch()
        label = QLabel("⏸ Pause")
        label.setObjectName("title")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        resume = QPushButton("▶ Reprendre")
        resume.clicked.connect(self.resume)
        leave = QPushButton("🏠 Arrêter")
        leave.clicked.connect(self.leave_game)
        layout.addWidget(resume)
        layout.addWidget(leave)
        layout.addStretch()
        return page

    def show_pause(self):
        self.stack.setCurrentIndex(4)

    def resume(self):
        self.stack.setCurrentWidget(self.game)
        self.game.toggle_pause()

    def leave_game(self):
        self.game.stop()
        self.stack.setCurrentIndex(0)

    # --- Résultats ---
    def make_results(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addStretch()
        self.result_title = QLabel()
        self.result_title.setObjectName("title")
        self.result_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.result_title)
        self.result_text = QLabel()
        self.result_text.setObjectName("big")
        self.result_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result_text.setWordWrap(True)
        layout.addWidget(self.result_text)
        buttons = QHBoxLayout()
        again = QPushButton("🔁 Rejouer")
        again.clicked.connect(self.play)
        menu = QPushButton("🎵 Autre chanson")
        menu.clicked.connect(lambda: (self.fill_songs(), self.stack.setCurrentIndex(0)))
        buttons.addWidget(again)
        buttons.addWidget(menu)
        layout.addLayout(buttons)
        layout.addStretch()
        return page

    def show_results(self, song, level, score, stars, counts, best_combo):
        key = f"{song.stem}|{level}"
        best = self.scores.get(key)
        record = not best or score > best["score"]
        if record:
            self.scores[key] = {"score": score, "stars": max(stars, best["stars"] if best else 0)}
            save_json(SCORES_FILE, self.scores)
        cheers = {5: "INCROYABLE ! 🤩", 4: "Super DJ ! 😎", 3: "Bien joué ! 👏", 2: "Pas mal ! 💪", 1: "Continue ! 🙂"}
        self.result_title.setText("⭐" * stars + "☆" * (5 - stars))
        self.result_text.setText(f"{cheers[stars]}\n\n🏆 {score} points" + ("   🎉 NOUVEAU RECORD !" if record else "")
                                 + f"\n✅ {counts['parfait']} parfaits   👍 {counts['bien']} biens   "
                                   f"❌ {counts['raté']} ratés\n🔥 Meilleur combo : {best_combo}")
        self.stack.setCurrentIndex(3)

    def closeEvent(self, event):
        self.game.stop()
        if self.proc:
            self.proc.kill()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("Défis DJ")
    app.setDesktopFileName("dj-defi")
    window = DefisDJ()
    window.showMaximized()
    sys.exit(app.exec())
