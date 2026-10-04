#!/usr/bin/env python3
"""Mon Studio : composer un morceau avec 4 lignes (batterie, basse, accords, mélodie) et des motifs à placer
sur une ligne de temps. Pensé pour un enfant : gros boutons, notes toujours dans la gamme, modèles prêts."""
import copy
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PyQt6.QtCore import QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (QApplication, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMenu,
                             QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QVBoxLayout,
                             QWidget)

import moteur
import musique as m

CREATIONS = Path.home() / "Musique" / "Mes créations"
PROJECTS = CREATIONS / "Projets"
LOOKAHEAD_MS = 200
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

STYLE = """
QWidget { font-size: 15px; }
QPushButton { font-size: 15px; font-weight: bold; padding: 8px 14px; border-radius: 12px;
              background: #4a90e2; color: white; border: none; }
QPushButton:hover { background: #357abd; }
QPushButton:checked { background: #1a3d66; }
QPushButton#play { background: #43a047; font-size: 20px; min-width: 130px; }
QPushButton#play:checked { background: #e53935; }
QPushButton#light { background: #eef4fc; color: #1a3d66; border: 2px solid #4a90e2; }
QPushButton#light:hover { background: #d6e6fa; }
QPushButton#mute { background: #ddd; color: #333; padding: 4px 8px; }
QPushButton#mute:checked { background: #e53935; color: white; }
QPushButton#chip { background: white; color: #1a1a1a; border: 2px solid #bbb; padding: 6px 12px; }
QPushButton#chip:checked { border: 3px solid #1a3d66; background: #fff8d6; }
QLabel#title { font-size: 22px; font-weight: bold; color: #4a90e2; }
QLabel#lane { font-size: 17px; font-weight: bold; }
QLabel#help { color: #555; font-size: 14px; }
QComboBox, QSpinBox { font-size: 15px; padding: 4px 8px; }
"""


def lane_color(lane, index=0):
    """Couleur d'un motif : la couleur de la ligne, plus ou moins claire selon la lettre."""
    color = QColor(m.LANE_INFO[lane][2])
    return color.lighter(100 + (index % 4) * 18)


def short_name(pattern):
    return pattern["name"].split(" · ")[0]


# --- Ligne de temps (une rangée par instrument) ---
class TimelineRow(QWidget):
    clicked = pyqtSignal(str, int, bool)  # ligne, mesure, clic droit

    def __init__(self, studio, lane):
        super().__init__()
        self.studio, self.lane = studio, lane
        self.setMinimumHeight(58)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.last_bar = None

    def bar_at(self, x):
        return max(0, min(m.SONG_BARS - 1, int(x / (self.width() / m.SONG_BARS))))

    def mousePressEvent(self, event):
        self.last_bar = self.bar_at(event.position().x())
        self.clicked.emit(self.lane, self.last_bar, event.button() == Qt.MouseButton.RightButton)

    def mouseMoveEvent(self, event):  # glisser pour peindre plusieurs mesures
        bar = self.bar_at(event.position().x())
        if bar != self.last_bar and event.buttons() & Qt.MouseButton.LeftButton:
            self.last_bar = bar
            self.studio.paint_bar(self.lane, bar)

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        lane_data = self.studio.project["lanes"][self.lane]
        w = self.width() / m.SONG_BARS
        selected = self.lane == self.studio.lane
        painter.fillRect(self.rect(), QColor("#fffbe8" if selected else "#f4f4f4"))
        for bar in range(m.SONG_BARS):
            painter.setPen(QPen(QColor("#ccc"), 1))
            painter.drawLine(int(bar * w), 0, int(bar * w), self.height())
            cell = lane_data["song"][bar]
            if not cell or cell[0] >= len(lane_data["patterns"]):
                continue
            pattern = lane_data["patterns"][cell[0]]
            rect = QRectF(bar * w + 2, 6, w - 4, self.height() - 12)
            painter.setBrush(lane_color(self.lane, cell[0]).darker(130 if lane_data["muted"] else 100))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, 8, 8)
            if cell[1] == 0:  # nom du motif sur sa première mesure
                painter.setPen(QColor("white"))
                painter.setFont(QFont(self.font().family(), 13, QFont.Weight.Bold))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, short_name(pattern))
        self.studio.draw_playhead(painter, self.width(), self.height())


class BarNumbers(QWidget):
    clicked = pyqtSignal(int)

    def __init__(self, studio):
        super().__init__()
        self.studio = studio
        self.setFixedHeight(26)

    def mousePressEvent(self, event):
        self.clicked.emit(int(event.position().x() / (self.width() / m.SONG_BARS)))

    def paintEvent(self, _):
        painter = QPainter(self)
        w = self.width() / m.SONG_BARS
        painter.setPen(QColor("#666"))
        for bar in range(m.SONG_BARS):
            painter.drawText(QRectF(bar * w, 0, w, self.height()), Qt.AlignmentFlag.AlignCenter, str(bar + 1))
        self.studio.draw_playhead(painter, self.width(), self.height())


# --- Éditeur de motif (grille) ---
class GridEditor(QWidget):
    LABEL_W = 150

    def __init__(self, studio):
        super().__init__()
        self.studio = studio
        self.drag_value = None

    def layout_info(self):
        lane, pattern = self.studio.lane, self.studio.current_pattern()
        if lane == "accords":
            columns, rows = m.BEATS_PER_BAR * pattern["bars"], 7
        else:
            columns = m.STEPS_PER_BAR * pattern["bars"]
            rows = {"batterie": len(m.DRUM_ROWS), "basse": m.BASS_ROWS, "melodie": m.MELODY_ROWS}[lane]
        return columns, rows

    def row_labels(self):
        lane, key = self.studio.lane, m.KEYS[self.studio.project["key"]]
        if lane == "batterie":
            return [name for name, _, _ in reversed(m.DRUM_ROWS)]
        if lane == "accords":
            return [m.chord_name(key, d) for d in range(6, -1, -1)]
        if lane == "basse":
            return [f"Note {r + 1}" + (" (base)" if r == 0 else " (octave)" if r == 7 else "") for r in range(m.BASS_ROWS - 1, -1, -1)]
        base = 60 if key[1] < 5 else 48
        return [m.note_name(key, m.scale_note(key, r, base)) + ("  ↑" if r >= 7 else "") for r in range(m.MELODY_ROWS - 1, -1, -1)]

    def sizeHint(self):
        if not self.studio.current_pattern():
            return QSize(600, 200)
        columns, rows = self.layout_info()
        col_w = 64 if self.studio.lane == "accords" else 34
        return QSize(self.LABEL_W + columns * col_w + 2, rows * self.row_height() + 2)

    def row_height(self):
        return 26 if self.studio.lane == "melodie" else 34

    def cell_at(self, pos):
        columns, rows = self.layout_info()
        col_w = (self.width() - self.LABEL_W) / columns
        col, row_from_top = int((pos.x() - self.LABEL_W) / col_w), int(pos.y() / self.row_height())
        if pos.x() < self.LABEL_W or not (0 <= col < columns and 0 <= row_from_top < rows):
            return None
        return rows - 1 - row_from_top, col

    def mousePressEvent(self, event):
        cell = self.cell_at(event.position())
        if cell:
            self.drag_value = self.studio.toggle_cell(*cell)

    def mouseMoveEvent(self, event):
        cell = self.cell_at(event.position())
        if cell and event.buttons() & Qt.MouseButton.LeftButton and self.drag_value is not None:
            self.studio.toggle_cell(*cell, self.drag_value)

    def paintEvent(self, _):
        pattern = self.studio.current_pattern()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("white"))
        if not pattern:
            painter.setPen(QColor("#888"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Choisis un motif tout prêt avec « ✨ Motifs prêts », ou crée-en un avec « ➕ Nouveau ».")
            return
        lane = self.studio.lane
        columns, rows = self.layout_info()
        rh = self.row_height()
        col_w = (self.width() - self.LABEL_W) / columns
        color = lane_color(lane, self.studio.selected[lane])
        labels = self.row_labels()
        for r in range(rows):
            y = r * rh
            painter.fillRect(QRectF(0, y, self.LABEL_W, rh), QColor("#f0f0f0" if r % 2 else "#e6e6e6"))
            painter.setPen(QColor("#333"))
            painter.drawText(QRectF(8, y, self.LABEL_W - 10, rh), Qt.AlignmentFlag.AlignVCenter, labels[r])
        if lane == "accords":
            lit = {(6 - d, beat) for beat, d in enumerate(pattern["chords"]) if d is not None}
        else:
            lit = {(rows - 1 - r, s) for r, s in pattern["cells"]}
        per_beat = 1 if lane == "accords" else 4
        for c in range(columns):
            x = self.LABEL_W + c * col_w
            for r in range(rows):
                rect = QRectF(x + 1, r * rh + 1, col_w - 2, rh - 2)
                if (r, c) in lit:
                    painter.fillRect(rect, color)
                else:
                    shade = "#fafafa" if (c // per_beat) % 2 == 0 else "#efefef"
                    painter.fillRect(rect, QColor(shade))
            bar_line = c % (m.BEATS_PER_BAR if lane == "accords" else m.STEPS_PER_BAR) == 0
            painter.setPen(QPen(QColor("#555" if bar_line else "#ccc"), 2 if bar_line else 1))
            painter.drawLine(int(x), 0, int(x), rows * rh)
        # tête de lecture dans le motif
        step = self.studio.pattern_play_step()
        if step is not None:
            x = self.LABEL_W + (step / (4 if lane == "accords" else 1)) * col_w
            painter.setPen(QPen(QColor("#e53935"), 3))
            painter.drawLine(int(x), 0, int(x), rows * rh)


# --- Fenêtre principale ---
class Studio(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("🎛 Mon Studio")
        self.setStyleSheet(STYLE)
        self.resize(1500, 920)
        self.engine = moteur.Engine()
        self.project = m.STYLES["Hip-hop chill"]()
        self.lane = "batterie"
        self.selected = {lane: 0 if self.project["lanes"][lane]["patterns"] else None for lane in m.LANES}
        self.playing = None      # None, "song" ou "pattern"
        self.events = {}
        self.loop_steps = 0
        self.dirty = True
        self.start_tick = 0
        self.next_step = 0
        self.first_step = 0
        self.save_path = None

        root = QVBoxLayout(self)
        root.addLayout(self.build_top_bar())
        self.help = QLabel()
        self.help.setObjectName("help")
        root.addWidget(self.help)
        root.addLayout(self.build_timeline())
        root.addLayout(self.build_editor_bar())
        self.grid = GridEditor(self)
        self.grid_scroll = QScrollArea()
        self.grid_scroll.setWidget(self.grid)
        self.grid_scroll.setWidgetResizable(True)  # la grille remplit la largeur, et défile si elle est plus grande
        root.addWidget(self.grid_scroll, 1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(20)
        self.apply_instruments()
        self.refresh_all()

    # ---------- construction ----------
    def build_top_bar(self):
        bar = QHBoxLayout()
        title = QLabel("🎛 Mon Studio")
        title.setObjectName("title")
        bar.addWidget(title)
        self.play_btn = QPushButton("▶ Lecture")
        self.play_btn.setObjectName("play")
        self.play_btn.setCheckable(True)
        self.play_btn.clicked.connect(lambda: self.toggle_play("song"))
        bar.addWidget(self.play_btn)
        bar.addWidget(QLabel("Tempo"))
        self.tempo = QSpinBox()
        self.tempo.setRange(60, 180)
        self.tempo.setSuffix(" BPM")
        self.tempo.valueChanged.connect(self.set_tempo)
        bar.addWidget(self.tempo)
        bar.addWidget(QLabel("Gamme"))
        self.key = QComboBox()
        self.key.addItems([k[0] for k in m.KEYS])
        self.key.currentIndexChanged.connect(self.set_key)
        bar.addWidget(self.key)
        bar.addStretch()
        styles = QPushButton("🎁 Morceaux prêts")
        styles.setObjectName("light")
        menu = QMenu(styles)
        for name in m.STYLES:
            menu.addAction(name, lambda n=name: self.load_style(n))
        styles.setMenu(menu)
        bar.addWidget(styles)
        for text, slot in [("🆕 Nouveau", self.new_song), ("📂 Ouvrir", self.open_song), ("💾 Enregistrer", self.save_song)]:
            button = QPushButton(text)
            button.setObjectName("light")
            button.clicked.connect(slot)
            bar.addWidget(button)
        export = QPushButton("🎧 Exporter vers Mixxx")
        export.clicked.connect(self.export_song)
        bar.addWidget(export)
        return bar

    def build_timeline(self):
        grid = QVBoxLayout()
        numbers_row = QHBoxLayout()
        spacer = QWidget()
        spacer.setFixedWidth(400)
        numbers_row.addWidget(spacer)
        self.numbers = BarNumbers(self)
        self.numbers.clicked.connect(self.play_from_bar)
        numbers_row.addWidget(self.numbers, 1)
        grid.addLayout(numbers_row)
        self.rows, self.instrument_boxes, self.mute_buttons, self.volume_sliders, self.lane_labels = {}, {}, {}, {}, {}
        for lane in m.LANES:
            row = QHBoxLayout()
            header = QWidget()
            header.setFixedWidth(400)
            h = QHBoxLayout(header)
            h.setContentsMargins(0, 0, 0, 0)
            label = QPushButton(m.LANE_INFO[lane][0])
            label.setObjectName("chip")
            label.setCheckable(True)
            label.setFixedWidth(130)
            label.clicked.connect(lambda _, l=lane: self.select_lane(l))
            self.lane_labels[lane] = label
            h.addWidget(label)
            box = QComboBox()
            box.addItems([name for name, _, _ in m.INSTRUMENTS[lane]])
            box.currentIndexChanged.connect(lambda i, l=lane: self.set_instrument(l, i))
            self.instrument_boxes[lane] = box
            h.addWidget(box, 1)
            mute = QPushButton("🔇")
            mute.setObjectName("mute")
            mute.setCheckable(True)
            mute.setToolTip("Couper cette ligne")
            mute.clicked.connect(lambda checked, l=lane: self.set_muted(l, checked))
            self.mute_buttons[lane] = mute
            h.addWidget(mute)
            volume = QSlider(Qt.Orientation.Horizontal)
            volume.setRange(0, 127)
            volume.setFixedWidth(60)
            volume.setToolTip("Volume")
            volume.valueChanged.connect(lambda v, l=lane: self.set_volume(l, v))
            self.volume_sliders[lane] = volume
            h.addWidget(volume)
            row.addWidget(header)
            timeline_row = TimelineRow(self, lane)
            timeline_row.clicked.connect(self.timeline_clicked)
            self.rows[lane] = timeline_row
            row.addWidget(timeline_row, 1)
            grid.addLayout(row)
        return grid

    def build_editor_bar(self):
        bar = QHBoxLayout()
        self.editor_title = QLabel()
        self.editor_title.setObjectName("lane")
        bar.addWidget(self.editor_title)
        self.chips_box = QHBoxLayout()
        bar.addLayout(self.chips_box)
        new = QPushButton("➕ Nouveau")
        new.clicked.connect(self.new_pattern)
        bar.addWidget(new)
        self.templates_btn = QPushButton("✨ Motifs prêts")
        bar.addWidget(self.templates_btn)
        dup = QPushButton("📋 Copier")
        dup.setObjectName("light")
        dup.clicked.connect(self.duplicate_pattern)
        bar.addWidget(dup)
        delete = QPushButton("🗑")
        delete.setObjectName("light")
        delete.setToolTip("Supprimer ce motif")
        delete.clicked.connect(self.delete_pattern)
        bar.addWidget(delete)
        bar.addStretch()
        bar.addWidget(QLabel("Longueur"))
        self.length = QComboBox()
        self.length.addItems([f"{n} mesure" + ("s" if n > 1 else "") for n in m.LENGTHS])
        self.length.activated.connect(self.set_length)
        bar.addWidget(self.length)
        self.chord_style_label = QLabel("Jeu")
        bar.addWidget(self.chord_style_label)
        self.chord_style = QComboBox()
        self.chord_style.addItems(m.CHORD_STYLES.values())
        self.chord_style.activated.connect(self.set_chord_style)
        bar.addWidget(self.chord_style)
        self.loop_btn = QPushButton("🔁 Écouter ce motif")
        self.loop_btn.setCheckable(True)
        self.loop_btn.clicked.connect(lambda: self.toggle_play("pattern"))
        bar.addWidget(self.loop_btn)
        return bar

    # ---------- affichage ----------
    def refresh_all(self):
        for widget in (self.tempo, self.key):
            widget.blockSignals(True)
        self.tempo.setValue(self.project["tempo"])
        self.key.setCurrentIndex(self.project["key"])
        for widget in (self.tempo, self.key):
            widget.blockSignals(False)
        for lane in m.LANES:
            data = self.project["lanes"][lane]
            for widget, setter, value in [(self.instrument_boxes[lane], "setCurrentIndex", data["instrument"]),
                                          (self.mute_buttons[lane], "setChecked", data["muted"]),
                                          (self.volume_sliders[lane], "setValue", data["volume"])]:
                widget.blockSignals(True)
                getattr(widget, setter)(value)
                widget.blockSignals(False)
        self.refresh_editor()

    def refresh_editor(self):
        lane = self.lane
        for l, label in self.lane_labels.items():
            label.setChecked(l == lane)
        self.editor_title.setText(f"{m.LANE_INFO[lane][0]} :")
        while self.chips_box.count():
            self.chips_box.takeAt(0).widget().deleteLater()
        for i, pattern in enumerate(self.project["lanes"][lane]["patterns"]):
            chip = QPushButton(pattern["name"])
            chip.setObjectName("chip")
            chip.setCheckable(True)
            chip.setChecked(i == self.selected[lane])
            chip.setStyleSheet(f"QPushButton {{ border-left: 10px solid {lane_color(lane, i).name()}; }}")
            chip.clicked.connect(lambda _, i=i: self.select_pattern(i))
            self.chips_box.addWidget(chip)
        menu = QMenu(self.templates_btn)
        for t in m.TEMPLATES[lane]:
            menu.addAction(t["name"], lambda n=t["name"]: self.add_template(n))
        self.templates_btn.setMenu(menu)
        pattern = self.current_pattern()
        self.length.setEnabled(pattern is not None)
        if pattern:
            self.length.setCurrentIndex(m.LENGTHS.index(pattern["bars"]))
        is_chords = lane == "accords"
        self.chord_style.setVisible(is_chords)
        self.chord_style_label.setVisible(is_chords)
        if is_chords and pattern:
            self.chord_style.setCurrentIndex(list(m.CHORD_STYLES).index(pattern["style"]))
        tips = {
            "batterie": "Clique dans la grille pour poser un coup de batterie. Chaque case est un petit moment du rythme.",
            "basse": "La basse suit les accords tout seule : « Note 1 » est toujours la base de l'accord. Plusieurs cases à la suite = une note tenue.",
            "accords": "Choisis un accord par temps. Toutes ces notes vont ensemble dans la gamme !",
            "melodie": "Toutes les notes de la grille sont dans la gamme : impossible de jouer faux ! Plusieurs cases à la suite = une note tenue.",
        }
        self.help.setText(f"💡 {tips[lane]}   Pour construire le morceau : choisis un motif, puis clique dans la ligne "
                          "de temps pour le placer (clic droit pour l'enlever).")
        self.fit_grid()
        self.update_views()

    def fit_grid(self):
        self.grid.setMinimumSize(self.grid.sizeHint())

    def update_views(self):
        for row in self.rows.values():
            row.update()
        self.numbers.update()
        self.grid.update()

    def draw_playhead(self, painter, width, height):
        if self.playing != "song":
            return
        step = self.current_step()
        x = width * step / (m.SONG_BARS * m.STEPS_PER_BAR)
        painter.setPen(QPen(QColor("#e53935"), 3))
        painter.drawLine(int(x), 0, int(x), height)

    # ---------- motifs ----------
    def lane_data(self, lane=None):
        return self.project["lanes"][lane or self.lane]

    def current_pattern(self):
        index = self.selected[self.lane]
        patterns = self.lane_data()["patterns"]
        return patterns[index] if index is not None and index < len(patterns) else None

    def next_letter(self):
        used = {short_name(p) for p in self.lane_data()["patterns"]}
        return next(letter for letter in LETTERS if letter not in used)

    def add_pattern(self, pattern):
        patterns = self.lane_data()["patterns"]
        patterns.append(pattern)
        self.selected[self.lane] = len(patterns) - 1
        self.changed()
        self.refresh_editor()

    def new_pattern(self):
        bars = 4 if self.lane == "accords" else 1
        self.add_pattern(m.new_pattern(self.lane, self.next_letter(), bars))

    def add_template(self, name):
        pattern = m.template(self.lane, name)
        pattern["name"] = f"{self.next_letter()} · {name}"
        self.add_pattern(pattern)

    def duplicate_pattern(self):
        pattern = self.current_pattern()
        if pattern:
            clone = copy.deepcopy(pattern)
            clone["name"] = self.next_letter() + (" · " + pattern["name"].split(" · ")[1] if " · " in pattern["name"] else "")
            self.add_pattern(clone)

    def delete_pattern(self):
        index = self.selected[self.lane]
        if index is None:
            return
        data = self.lane_data()
        data["patterns"].pop(index)
        data["song"] = [None if c and c[0] == index else ([c[0] - 1, c[1]] if c and c[0] > index else c) for c in data["song"]]
        self.selected[self.lane] = (min(index, len(data["patterns"]) - 1) if data["patterns"] else None)
        self.changed()
        self.refresh_editor()

    def select_pattern(self, index):
        self.selected[self.lane] = index
        self.refresh_editor()

    def select_lane(self, lane):
        self.lane = lane
        if self.playing == "pattern":
            self.stop()
        self.refresh_editor()

    def set_length(self, index):
        pattern = self.current_pattern()
        if not pattern:
            return
        bars = m.LENGTHS[index]
        pattern["bars"] = bars
        if self.lane == "accords":
            chords = pattern["chords"][:bars * m.BEATS_PER_BAR]
            pattern["chords"] = chords + [None] * (bars * m.BEATS_PER_BAR - len(chords))
        else:
            pattern["cells"] = [c for c in pattern["cells"] if c[1] < bars * m.STEPS_PER_BAR]
        # replace chaque utilisation du motif dans le morceau avec sa nouvelle longueur
        data, idx = self.lane_data(), self.selected[self.lane]
        starts = [b for b, c in enumerate(data["song"]) if c and c[0] == idx and c[1] == 0]
        data["song"] = [None if c and c[0] == idx else c for c in data["song"]]
        for bar in starts:
            m.place(self.project, self.lane, idx, bar)
        self.changed()
        self.refresh_editor()

    def set_chord_style(self, index):
        pattern = self.current_pattern()
        if pattern:
            pattern["style"] = list(m.CHORD_STYLES)[index]
            self.changed()

    def toggle_cell(self, row, col, value=None):
        """Allume ou éteint une case. value : forcer (glisser) ; renvoie l'état appliqué."""
        pattern = self.current_pattern()
        if self.lane == "accords":
            degree = row
            new = (None if pattern["chords"][col] == degree else degree) if value is None else (degree if value else None)
            pattern["chords"][col] = new
            if new is not None:
                key = m.KEYS[self.project["key"]]
                for note in m.chord_voicing(key, degree):
                    self.engine.preview(1, note, 90, 500)
            result = new is not None
        else:
            cell = [row, col]
            on = cell in pattern["cells"]
            result = (not on) if value is None else value
            if result and not on:
                pattern["cells"].append(cell)
                self.preview_cell(row)
            elif not result and on:
                pattern["cells"].remove(cell)
        self.changed()
        self.update_views()
        return result

    def preview_cell(self, row):
        key = m.KEYS[self.project["key"]]
        if self.lane == "batterie":
            _, note, velocity = m.DRUM_ROWS[row]
            self.engine.preview(9, note, velocity, 200)
        elif self.lane == "basse":
            self.engine.preview(0, m.scale_note(key, row, 36), 105, 300)
        else:
            self.engine.preview(2, m.scale_note(key, row, 60 if key[1] < 5 else 48), 100, 300)

    # ---------- ligne de temps ----------
    def timeline_clicked(self, lane, bar, right):
        if lane != self.lane:
            self.lane = lane
            self.refresh_editor()
        data = self.lane_data(lane)
        cell = data["song"][bar]
        index = self.selected[lane]
        if right or (cell and index is not None and cell[0] == index and cell[1] == 0) or index is None:
            self.clear_instance(lane, bar)
        else:
            m.place(self.project, lane, index, bar)
        self.changed()
        self.update_views()

    def paint_bar(self, lane, bar):
        index = self.selected[lane]
        if index is not None:
            m.place(self.project, lane, index, bar)
            self.changed()
            self.update_views()

    def clear_instance(self, lane, bar):
        data = self.lane_data(lane)
        cell = data["song"][bar]
        if not cell:
            return
        start = bar - cell[1]
        for i in range(data["patterns"][cell[0]]["bars"]):
            if 0 <= start + i < m.SONG_BARS and data["song"][start + i] == [cell[0], i]:
                data["song"][start + i] = None

    # ---------- réglages ----------
    def set_tempo(self, value):
        position = self.current_step_float() if self.playing else 0
        self.project["tempo"] = value
        if self.playing:  # garde la position actuelle avec le nouveau tempo
            now = self.engine.now()
            self.engine.stop()
            self.start_tick = now - position * self.step_ms()
            self.next_step = int(position) + 1

    def set_key(self, index):
        self.project["key"] = index
        self.changed()
        self.refresh_editor()

    def set_instrument(self, lane, index):
        self.lane_data(lane)["instrument"] = index
        self.apply_instruments()

    def set_volume(self, lane, value):
        self.lane_data(lane)["volume"] = value
        self.apply_instruments()

    def set_muted(self, lane, muted):
        self.lane_data(lane)["muted"] = muted
        self.changed()
        self.update_views()

    def channels(self):
        return {m.LANE_INFO[lane][1]: m.INSTRUMENTS[lane][self.lane_data(lane)["instrument"]][1:] + (self.lane_data(lane)["volume"],)
                for lane in m.LANES}

    def apply_instruments(self):
        self.engine.setup(self.channels())

    # ---------- lecture ----------
    def changed(self):
        self.dirty = True

    def step_ms(self):
        return 60000 / self.project["tempo"] / 4

    def current_step_float(self):
        return max(0.0, (self.engine.now() - self.start_tick) / self.step_ms())

    def current_step(self):
        return int(self.current_step_float()) % max(1, self.loop_steps)

    def pattern_play_step(self):
        """Position de lecture dans le motif affiché (en pas), ou None."""
        if self.playing == "pattern":
            return self.current_step()
        if self.playing == "song":
            step = self.current_step()
            cell = self.lane_data()["song"][step // m.STEPS_PER_BAR]
            if cell and cell[0] == self.selected[self.lane]:
                return cell[1] * m.STEPS_PER_BAR + step % m.STEPS_PER_BAR
        return None

    def compile(self):
        if self.playing == "pattern":
            pattern = self.current_pattern()
            self.events = m.compile_pattern(self.project, self.lane, pattern) if pattern else {}
            self.loop_steps = (pattern["bars"] if pattern else 1) * m.STEPS_PER_BAR
        else:
            self.events = m.compile_song(self.project)
            self.loop_steps = m.SONG_BARS * m.STEPS_PER_BAR
        self.dirty = False

    def toggle_play(self, mode, from_step=0):
        was = self.playing
        self.stop()
        if was == mode and from_step == 0:
            return
        if mode == "pattern" and not self.current_pattern():
            return
        self.playing = mode
        self.compile()
        self.start_tick = self.engine.now() + 60 - from_step * self.step_ms()
        self.next_step = from_step
        self.play_btn.setChecked(mode == "song")
        self.play_btn.setText("⏹ Stop" if mode == "song" else "▶ Lecture")
        self.loop_btn.setChecked(mode == "pattern")

    def play_from_bar(self, bar):
        self.toggle_play("song", bar * m.STEPS_PER_BAR)

    def stop(self):
        self.playing = None
        self.engine.stop()
        self.play_btn.setChecked(False)
        self.play_btn.setText("▶ Lecture")
        self.loop_btn.setChecked(False)
        self.update_views()

    def tick(self):
        if not self.playing:
            return
        if self.dirty:
            self.compile()
        now = self.engine.now()
        step_ms = self.step_ms()
        while self.start_tick + self.next_step * step_ms < now + LOOKAHEAD_MS:
            time = self.start_tick + self.next_step * step_ms
            for channel, note, velocity, length in self.events.get(self.next_step % self.loop_steps, []):
                self.engine.note_at(time, channel, note, velocity, length * step_ms * 0.95)
            self.next_step += 1
        self.update_views()

    # ---------- fichiers ----------
    def confirm_replace(self):
        answer = QMessageBox.question(self, "Mon Studio", "Remplacer le morceau en cours ?\n(Pense à l'enregistrer avant !)")
        return answer == QMessageBox.StandardButton.Yes

    def load_project(self, project, path=None):
        self.stop()
        self.project = project
        self.save_path = path
        self.selected = {lane: 0 if project["lanes"][lane]["patterns"] else None for lane in m.LANES}
        self.changed()
        self.apply_instruments()
        self.refresh_all()

    def load_style(self, name):
        if self.confirm_replace():
            self.load_project(m.STYLES[name]())

    def new_song(self):
        if self.confirm_replace():
            self.load_project(m.new_project())

    def open_song(self):
        PROJECTS.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getOpenFileName(self, "Ouvrir un morceau", str(PROJECTS), "Morceaux (*.json)")
        if path:
            self.load_project(m.load(path), Path(path))

    def ask_name(self, question):
        name, ok = QInputDialog.getText(self, "Mon Studio", question, text=self.project.get("name", "Mon morceau"))
        name = re.sub(r'[/\\:*?"<>|]', " ", name).strip()
        if ok and name:
            self.project["name"] = name
            return name
        return None

    def save_song(self):
        name = self.ask_name("Comment s'appelle ton morceau ?")
        if not name:
            return
        PROJECTS.mkdir(parents=True, exist_ok=True)
        self.save_path = PROJECTS / f"{name}.json"
        m.save(self.project, self.save_path)
        QMessageBox.information(self, "Mon Studio", f"💾 « {name} » est enregistré !")

    def export_song(self):
        name = self.ask_name("Comment s'appelle ton morceau ? Il ira dans Mixxx !")
        if not name:
            return
        self.stop()
        CREATIONS.mkdir(parents=True, exist_ok=True)
        PROJECTS.mkdir(parents=True, exist_ok=True)
        m.save(self.project, PROJECTS / f"{name}.json")
        events = m.compile_song(self.project)
        last_bar = max((b for lane in m.LANES for b, c in enumerate(self.lane_data(lane)["song"]) if c), default=0)
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "morceau.wav"
            moteur.render_wav(wav, events, (last_bar + 1) * m.STEPS_PER_BAR, self.project["tempo"], self.channels())
            mp3 = CREATIONS / f"Mes créations - {name}.mp3"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-af", "loudnorm=I=-14:TP=-1",
                            "-codec:a", "libmp3lame", "-q:a", "2", "-metadata", "artist=Mes créations",
                            "-metadata", f"title={name}", str(mp3)], check=True)
        QMessageBox.information(self, "Mon Studio", f"🎧 « {name} » est dans tes musiques !\n"
                                                    "Ouvre Mixxx : il est dans le dossier « Mes créations ».")

    def closeEvent(self, event):
        self.timer.stop()
        self.engine.close()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("Mon Studio")
    app.setDesktopFileName("mon-studio")
    app.setFont(QFont(app.font().family(), 11))
    window = Studio()
    window.showMaximized()
    sys.exit(app.exec())
