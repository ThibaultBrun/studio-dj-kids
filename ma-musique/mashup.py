"""Assistant « Créer un mashup ».

Platine 1 puis platine 2 : une chanson (de la bibliothèque ou d'Internet), en entier, juste la voix ou juste
la musique. Ensuite l'assistant fait tout : téléchargement, séparation, tempo et tonalité, puis il ouvre
Mixxx avec les deux platines calées (même tempo, même tonalité, la voix qui démarre là où elle chante).
"""
import json
import re
import subprocess
import sys
import time

from PyQt6.QtCore import QProcess, QProcessEnvironment, QSize, Qt, QTimer
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (QButtonGroup, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                             QProgressBar, QPushButton, QStackedWidget, QVBoxLayout, QWidget)

import mixxx_db
from bibliotheque import HOME, MUSIC_DIR, SECONDS_PER_SECOND, music_text, read_song, stems_of

sys.path.insert(0, str(HOME / ".local/share/bip"))

NOTES = mixxx_db.NOTES
SMILEYS = ["😬", "😐", "😀"]
MODES = {"complete": "🎵 Chanson entière", "voix": "🎤 Juste la voix", "musique": "🎶 Juste la musique"}
STEM_OF_MODE = {"voix": "Voix", "musique": "Sans voix"}
# Contrôleur virtuel de Bip (mixxx/Bip-scripts.js)
CC_PREPARE, CC_SHIFT, CC_LEADER, CC_START1, CC_START2 = 0x70, 0x73, 0x74, 0x75, 0x77
MODE_MASHUP = 3
# Durée estimée des étapes, en secondes (mesurée sur ce PC) ; la séparation dépend de la chanson
ESTIMATES = {"telechargement": 30, "analyse": 30, "mixxx": 30}
MP3_SECONDS_PER_SECOND = 0.35  # fabrication du MP3 (étirement de la voix compris), mesurée sur ce PC
LEAD_IN_BARS = 8  # dans Mixxx, on démarre 8 mesures avant les refrains
SEPARATION_EXTRA = 20  # chargement du modèle


def fmt_wait(seconds):
    """« environ 4 min » / « moins d'une minute » (pour un enfant, pas besoin des secondes)."""
    if seconds < 15:
        return "presque fini !"
    if seconds < 60:
        return "moins d'une minute"
    minutes = round(seconds / 60)
    return f"environ {minutes} minute{'s' if minutes > 1 else ''}"


def parse_key(text):
    """« F#m » -> (6, True) ; None si inconnu."""
    if not text:
        return None
    minor = text.endswith("m")
    note = text[:-1] if minor else text
    return (NOTES.index(note), minor) if note in NOTES else None


def match(follower, leader):
    """Est-ce que `follower` ira bien sur `leader` (qui donne le tempo) ? Note (0-2), réglages et explications."""
    # Tempo : on accepte aussi le double ou la moitié (même pulsation)
    ratio = min((leader["bpm"] / (follower["bpm"] * k) for k in (0.5, 1, 2)), key=lambda r: abs(r - 1))
    change = ratio - 1
    tempo_score = 2 if abs(change) <= 0.04 else 1 if abs(change) <= 0.10 else 0
    if abs(change) < 0.005:
        tempo_text = f"🥁 Rythme : {follower['bpm']:.0f} et {leader['bpm']:.0f} BPM, le même tempo, parfait !"
    else:
        tempo_text = (f"🥁 Rythme : {follower['bpm']:.0f} et {leader['bpm']:.0f} BPM : la platine qui suit ira "
                      f"{abs(change) * 100:.0f} % plus {'vite' if change > 0 else 'lentement'}.")
    # Tonalité : on compare les gammes (une gamme mineure = la majeure qui a les mêmes notes)
    shift, key_score = 0, 1
    kf, kl = parse_key(follower.get("key")), parse_key(leader.get("key"))
    if kf and kl:
        diff = ((kl[0] + 3 * kl[1]) - (kf[0] + 3 * kf[1])) % 12
        diff = diff - 12 if diff > 6 else diff
        names = f"{follower['key']} et {leader['key']}"
        if diff == 0:
            key_score, key_text = 2, f"🎼 Tonalité : {names}, les mêmes notes, parfait !"
        elif abs(diff) == 5:
            key_score, key_text = 2, f"🎼 Tonalité : {names}, des gammes amies, ça sonne bien !"
        else:
            shift = diff
            key_score = 1 if abs(diff) <= 2 else 0
            key_text = (f"🎼 Tonalité : {names} : je {'monte' if diff > 0 else 'descends'} la platine qui suit de "
                        f"{abs(diff)} demi-ton{'s' if abs(diff) > 1 else ''}"
                        + (" (ça sera un peu bizarre)." if key_score == 0 else "."))
    else:
        key_text = "🎼 Tonalité : je ne la connais pas, écoute bien si ça sonne juste !"
    return {"score": min(tempo_score, key_score), "ratio": ratio, "shift": shift, "texts": [tempo_text, key_text]}


def singing_start(voice_file, info):
    """Le moment où la voix commence à chanter, ramené au début de la mesure (en secondes)."""
    out = subprocess.run(["ffmpeg", "-v", "info", "-nostats", "-i", str(voice_file),
                          "-af", "silencedetect=n=-35dB:d=1.5", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", out)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", out)]
    t = ends[0] if starts and starts[0] < 0.5 and ends else 0.0
    period = 4 * 60 / info["bpm"]
    bars = int((t - info["first_beat"]) // period)
    return max(0.0, info["first_beat"] + bars * period)


def alignment(follower, leader, ratio):
    """Comment caler la platine qui suit sur la meneuse pour que les refrains tombent ensemble.

    follower/leader : analyses (bpm, first_bar, chorus en s). ratio : tempo meneuse / suiveuse.
    Renvoie les points de départ dans Mixxx (quelques mesures avant les refrains) et, pour le MP3,
    le décalage de la suiveuse (s, après étirement) ; None si on ne connaît pas les refrains."""
    if not follower.get("chorus") or not leader.get("chorus"):
        return None
    bar_leader = 4 * 60 / leader["bpm"]
    bar_follower = 4 * 60 / leader["bpm"] * ratio  # une mesure de la meneuse, dans le temps de la suiveuse
    room = min(int((leader["chorus"] - leader.get("first_bar", 0)) // bar_leader),
               int((follower["chorus"] - follower.get("first_bar", 0)) // bar_follower))
    lead_in = max(0, min(LEAD_IN_BARS, room))
    return {"follower_start": follower["chorus"] - lead_in * bar_follower,
            "leader_start": leader["chorus"] - lead_in * bar_leader,
            "mp3_delay": leader["chorus"] - follower["chorus"] / ratio}


class Choice:
    """Ce que l'enfant a choisi pour une platine."""

    def __init__(self):
        self.path = None      # chanson déjà dans la bibliothèque
        self.online = None    # ou résultat Internet {"id", "title", ...} à télécharger
        self.mode = None

    @property
    def duration(self):
        if self.online:
            return self.online.get("duration") or 240
        return read_song(self.path)[2] if self.path else 240

    @property
    def name(self):
        return self.path.stem if self.path else (self.online or {}).get("title", "")

    def ready(self):
        return bool((self.path or self.online) and self.mode)


class SongPage(QWidget):
    """Choisir une chanson et ce qu'on en garde, pour une platine."""

    def __init__(self, wizard, deck):
        super().__init__()
        self.wizard = wizard
        self.choice = Choice()
        self.search_proc = None
        root = QVBoxLayout(self)
        title = QLabel(f"🎚 Platine {deck} : choisis {'ta première' if deck == 1 else 'ta deuxième'} chanson")
        title.setObjectName("title")
        root.addWidget(title)

        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔎 Tape le nom d'une chanson…")
        self.search.textChanged.connect(self.filter)
        self.search.returnPressed.connect(self.search_online)
        self.online_btn = QPushButton("🌍 Chercher sur Internet")
        self.online_btn.setMinimumHeight(50)
        self.online_btn.clicked.connect(self.search_online)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.online_btn)
        root.addLayout(bar)

        self.list = QListWidget()
        self.list.setIconSize(QSize(48, 48))
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setStyleSheet("QListWidget { font-size: 17px; } QListWidget::item { padding: 5px; }")
        self.list.currentItemChanged.connect(self.song_chosen)
        root.addWidget(self.list, 1)

        root.addWidget(QLabel("Et tu veux garder…"))
        modes = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        for mode, text in MODES.items():
            button = QPushButton(text)
            button.setObjectName("mode")
            button.setCheckable(True)
            button.setMinimumHeight(60)
            button.setProperty("mode", mode)
            if mode != "complete" and not wizard.library.separator_ok:
                button.setEnabled(False)
                button.setToolTip("Il faut le séparateur de pistes : demande à papa.")
            self.mode_group.addButton(button)
            modes.addWidget(button)
        self.mode_group.buttonClicked.connect(self.mode_chosen)
        root.addLayout(modes)
        self.fill_library()

    def fill_library(self):
        self.list.clear()
        for path, row in self.wizard.library.rows.items():
            info = self.wizard.library.song_info(path)
            extra = music_text(info)
            if row.stems:
                extra += "    ✂️ déjà séparée"
            item = QListWidgetItem(f"{path.stem}\n{extra}".strip())
            item.setData(Qt.ItemDataRole.UserRole, ("local", path))
            cover = read_song(path)[3]
            pixmap = QPixmap()
            if cover and pixmap.loadFromData(cover):
                item.setIcon(QIcon(pixmap))
            self.list.addItem(item)
        self.filter()

    def filter(self):
        words = self.search.text().lower().split()
        for i in range(self.list.count()):
            item = self.list.item(i)
            data = item.data(Qt.ItemDataRole.UserRole)
            if data and data[0] == "local":
                item.setHidden(not all(w in item.text().lower() for w in words))

    def search_online(self):
        import ma_musique as mm  # la recherche YouTube de l'onglet « Chercher »

        query = self.search.text().strip()
        if not query or self.search_proc:
            return
        self.online_btn.setEnabled(False)
        self.online_btn.setText("⏳ Je cherche…")
        self.search_proc = mm.ytdlp_process(["--flat-playlist", "--dump-json", f"ytsearch8:{query}"])
        self.search_proc.finished.connect(lambda *_: self.online_results(mm))
        self.search_proc.start()

    def online_results(self, mm):
        out = bytes(self.search_proc.readAllStandardOutput()).decode(errors="replace")
        self.search_proc = None
        self.online_btn.setEnabled(True)
        self.online_btn.setText("🌍 Chercher sur Internet")
        for i in reversed(range(self.list.count())):  # on remplace les anciens résultats Internet
            data = self.list.item(i).data(Qt.ItemDataRole.UserRole)
            if not data or data[0] != "local":
                self.list.takeItem(i)
        found = 0
        for line in out.splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not entry.get("id") or not entry.get("duration") or entry["duration"] > mm.MAX_DURATION:
                continue
            item = QListWidgetItem(f"🌍 {entry.get('title')}\n⬇ à télécharger    ⏱ {mm.fmt_duration(entry['duration'])}")
            item.setData(Qt.ItemDataRole.UserRole, ("online", entry))
            self.list.insertItem(found, item)
            found += 1
        if not found:
            self.list.insertItem(0, "😕 Rien trouvé sur Internet… essaie d'écrire autrement !")
        self.list.scrollToTop()

    def song_chosen(self, item, _previous=None):
        data = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.choice.path = data[1] if data and data[0] == "local" else None
        self.choice.online = data[1] if data and data[0] == "online" else None
        self.wizard.update_buttons()

    def mode_chosen(self, button):
        self.choice.mode = button.property("mode")
        self.wizard.update_buttons()


class MashupWizard(QDialog):
    def __init__(self, library):
        super().__init__(library)
        self.library = library
        self.setWindowTitle("🎤 + 🎶 Créer un mashup")
        self.resize(1000, 760)
        self.steps = []
        self.current = -1
        self.mixxx = None
        self.running = False

        root = QVBoxLayout(self)
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.pages = [SongPage(self, 1), SongPage(self, 2)]
        for page in self.pages:
            self.stack.addWidget(page)
        self.stack.addWidget(self.make_summary())
        self.stack.addWidget(self.make_work())

        nav = QHBoxLayout()
        self.back_btn = QPushButton("⬅ Retour")
        self.back_btn.setObjectName("folder")
        self.next_btn = QPushButton("Suivant ➡")
        for button in (self.back_btn, self.next_btn):
            button.setMinimumHeight(60)
        self.back_btn.clicked.connect(self.back)
        self.next_btn.clicked.connect(self.next)
        nav.addWidget(self.back_btn)
        nav.addStretch()
        nav.addWidget(self.next_btn)
        root.addLayout(nav)
        library.job_progress.connect(self.job_progress)
        library.job_done.connect(self.job_done)
        self.update_buttons()

    # --- Pages ---
    def make_summary(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("🎛 Ton mashup")
        title.setObjectName("title")
        layout.addWidget(title)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setObjectName("status")
        layout.addWidget(self.summary)
        verdict = QHBoxLayout()
        self.smiley = QLabel()
        self.smiley.setStyleSheet("font-size: 64px;")
        self.verdict = QLabel()
        self.verdict.setWordWrap(True)
        self.verdict.setObjectName("status")
        verdict.addWidget(self.smiley)
        verdict.addWidget(self.verdict, 1)
        layout.addLayout(verdict)
        layout.addWidget(QLabel("Et je fais quoi avec ?"))
        outputs = QHBoxLayout()
        self.to_mixxx = QPushButton("🎚 Le jouer dans Mixxx")
        self.to_mp3 = QPushButton("💾 Fabriquer le MP3")
        for button in (self.to_mixxx, self.to_mp3):
            button.setObjectName("mode")
            button.setCheckable(True)
            button.setMinimumHeight(60)
            button.toggled.connect(self.outputs_changed)
            outputs.addWidget(button)
        # Par défaut : seulement « jouer dans Mixxx ». Le MP3 n'est PAS coché d'office —
        # l'enfant le fabrique lui-même s'il le veut (évite un long rendu MP3 à chaque mashup).
        self.to_mixxx.setChecked(True)
        self.to_mp3.setChecked(False)
        layout.addLayout(outputs)
        self.summary_time = QLabel()
        self.summary_time.setObjectName("status")
        layout.addWidget(self.summary_time)
        layout.addStretch()
        return page

    def outputs_changed(self):
        self.update_buttons()
        if self.stack.currentIndex() == 2:
            self.show_summary()

    def make_work(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("🛠 Je prépare ton mashup…")
        title.setObjectName("title")
        layout.addWidget(title)
        self.steps_label = QLabel()
        self.steps_label.setObjectName("status")
        self.steps_label.setWordWrap(True)
        layout.addWidget(self.steps_label)
        layout.addStretch()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setMinimumHeight(40)
        layout.addWidget(self.progress)
        self.time_label = QLabel()
        self.time_label.setObjectName("title")
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.time_label)
        self.work_status = QLabel()
        self.work_status.setObjectName("status")
        self.work_status.setWordWrap(True)
        layout.addWidget(self.work_status)
        self.close_mixxx_btn = QPushButton("🛑 Fermer Mixxx pour moi")
        self.close_mixxx_btn.setObjectName("error")
        self.close_mixxx_btn.setMinimumHeight(60)
        self.close_mixxx_btn.hide()
        self.close_mixxx_btn.clicked.connect(self.close_mixxx)
        layout.addWidget(self.close_mixxx_btn)
        self.listen_btn = QPushButton("▶ Écouter mon mashup")
        self.listen_btn.setObjectName("play")
        self.listen_btn.setMinimumHeight(60)
        self.listen_btn.hide()
        self.listen_btn.clicked.connect(lambda: self.library.toggle_play(self.mp3, self.listen_btn, "▶ Écouter mon mashup"))
        layout.addWidget(self.listen_btn)
        return page

    def update_buttons(self):
        index = self.stack.currentIndex()
        self.back_btn.setVisible(index in (1, 2) or (index == 3 and not self.running))
        self.next_btn.setVisible(index < 3 or not self.running)
        if index < 2:
            self.next_btn.setText("Suivant ➡")
            self.next_btn.setEnabled(self.pages[index].choice.ready())
            self.next_btn.setObjectName("")
        elif index == 2:
            self.next_btn.setText("🚀 Lancer le mashup !")
            self.next_btn.setEnabled(self.to_mixxx.isChecked() or self.to_mp3.isChecked())
            self.next_btn.setObjectName("done")
        else:
            self.next_btn.setText("Fermer")
            self.next_btn.setObjectName("folder")
        self.next_btn.style().unpolish(self.next_btn)
        self.next_btn.style().polish(self.next_btn)

    def back(self):
        index = self.stack.currentIndex()
        self.stack.setCurrentIndex(1 if index == 3 else max(0, index - 1))
        self.update_buttons()

    def next(self):
        index = self.stack.currentIndex()
        if index == 3:
            self.close()
            return
        self.stack.setCurrentIndex(index + 1)
        if index + 1 == 2:
            self.show_summary()
        elif index + 1 == 3:
            self.start()
        self.update_buttons()

    # --- Récapitulatif ---
    def roles(self):
        """(platine qui suit, platine qui mène) : une voix suit toujours la musique de l'autre platine."""
        modes = [page.choice.mode for page in self.pages]
        if modes[1] == "voix" and modes[0] != "voix":
            return 1, 0
        return 0, 1

    def show_summary(self):
        lines = [f"<b>Platine {i + 1}</b> : {MODES[p.choice.mode]} de « {p.choice.name} »"
                 + (" <i>(je vais la télécharger)</i>" if p.choice.online else "") for i, p in enumerate(self.pages)]
        self.summary.setText("<br>".join(lines))
        follower, leader = self.roles()
        infos = [self.library.song_info(p.choice.path) if p.choice.path else None for p in self.pages]
        same = self.pages[0].choice.name == self.pages[1].choice.name
        if same and self.pages[0].choice.mode == self.pages[1].choice.mode:
            self.smiley.setText("🙃")
            self.verdict.setText("C'est deux fois la même chose ! Tu peux quand même essayer…")
        elif infos[0] and infos[1]:
            result = match(infos[follower], infos[leader])
            advice = ["Ça risque de sonner bizarre… mais tu peux essayer !", "Ça peut marcher !",
                      "Ça va super bien ensemble !"][result["score"]]
            self.smiley.setText(SMILEYS[result["score"]])
            self.verdict.setText(f"<b>{advice}</b><br>" + "<br>".join(result["texts"]))
        else:
            self.smiley.setText("🤔")
            self.verdict.setText("Je ne connais pas encore le rythme de ces chansons : je vais l'écouter, "
                                 "puis je calerai tout dans Mixxx.")
        total = sum(step["estimate"] for step in self.plan())
        self.summary_time.setText(f"⏱ Ça va prendre {fmt_wait(total)}.")

    # --- Le travail ---
    def plan(self):
        """Les étapes à faire, avec leur durée estimée (celles déjà faites sont marquées « skip »)."""
        steps = []
        for i, page in enumerate(self.pages):
            choice = page.choice
            if choice.online:
                steps.append({"kind": "telechargement", "deck": i, "text": f"⬇ Télécharger « {choice.name} »",
                              "estimate": ESTIMATES["telechargement"]})
            if choice.mode != "complete":
                done = bool(choice.path and stems_of(choice.path))
                steps.append({"kind": "separation", "deck": i, "text": f"✂️ Séparer « {choice.name} »",
                              "estimate": 0 if done else choice.duration * SECONDS_PER_SECOND + SEPARATION_EXTRA})
            done = bool(choice.path and self.library.structure(choice.path))
            steps.append({"kind": "analyse", "deck": i, "text": f"🥁 Trouver le rythme et le refrain de « {choice.name} »",
                          "estimate": 0 if done else ESTIMATES["analyse"]})
        if self.to_mp3.isChecked():
            _, leader = self.roles()
            steps.append({"kind": "mp3", "text": "💾 Fabriquer le MP3 (refrains calés)",
                          "estimate": self.pages[leader].choice.duration * MP3_SECONDS_PER_SECOND})
        if self.to_mixxx.isChecked():
            steps.append({"kind": "mixxx", "text": "🎚 Préparer Mixxx et caler les platines",
                          "estimate": ESTIMATES["mixxx"]})
        for step in steps:
            step.update(state="todo", fraction=0.0, started=None)
        return steps

    def start(self):
        self.final_message = ""
        self.mp3 = None
        self.listen_btn.hide()
        self.running = True
        self.steps = self.plan()
        self.current = -1
        self.clock = QTimer(self)
        self.clock.timeout.connect(self.show_steps)
        self.clock.start(1000)
        self.next_step()

    def remaining(self, step):
        """Temps restant estimé pour une étape : d'après sa vitesse réelle dès qu'on la connaît."""
        if step["state"] in ("done", "skip"):
            return 0
        if step["state"] != "busy" or not step["started"]:
            return step["estimate"]
        elapsed = time.time() - step["started"]
        if step["fraction"] > 0.1 and elapsed > 10:
            return elapsed / step["fraction"] * (1 - step["fraction"])
        return max(step["estimate"] * (1 - step["fraction"]), step["estimate"] - elapsed, 5)

    def show_steps(self):
        if not self.steps:
            return
        icons = {"todo": "⬜", "busy": "⏳", "done": "✅", "skip": "✅", "fail": "❌"}
        self.steps_label.setText("<br>".join(f"{icons[s['state']]} {s['text']}" for s in self.steps
                                             if s["estimate"] or s["state"] not in ("skip", "todo")))
        # La barre avance avec le temps : chaque étape compte pour sa durée (estimée, puis réelle)
        left = sum(self.remaining(s) for s in self.steps)
        spent = sum(time.time() - s["started"] for s in self.steps if s["started"] and s["state"] == "busy") + \
            sum(s.get("took", 0) for s in self.steps)
        fraction = spent / (spent + left) if spent + left else 1
        if any(s["state"] == "fail" for s in self.steps):
            self.time_label.setText("")
        elif all(s["state"] in ("done", "skip") for s in self.steps):
            fraction = 1
            self.time_label.setText("🎉 Fini !")
        else:
            self.time_label.setText(f"⏱ Encore {fmt_wait(left)}")
        self.progress.setValue(int(fraction * 1000))
        self.progress.setFormat(f"{fraction * 100:.0f} %")

    def step(self):
        return self.steps[self.current]

    def set_fraction(self, fraction):
        self.step()["fraction"] = max(0.0, min(1.0, fraction))
        self.show_steps()

    def finish_step(self, ok=True, message=None):
        step = self.step()
        step["state"] = "done" if ok else "fail"
        step["took"] = time.time() - step["started"] if step["started"] else 0
        self.show_steps()
        if not ok:
            self.running = False
            self.clock.stop()
            self.work_status.setText(message or "😕 Oups, ça n'a pas marché. Réessaie, ou demande à papa.")
            self.update_buttons()
            return
        self.next_step()

    def next_step(self):
        while True:
            self.current += 1
            if self.current >= len(self.steps):
                self.all_done()
                return
            step = self.step()
            choice = self.pages[step["deck"]].choice if "deck" in step else None
            # Déjà fait ? (chanson déjà séparée, rythme déjà connu)
            if (step["kind"] == "separation" and stems_of(choice.path)) or \
                    (step["kind"] == "analyse" and self.library.structure(choice.path)):
                step["state"] = "skip"
                continue
            break
        step["state"] = "busy"
        step["started"] = time.time()
        self.show_steps()
        if step["kind"] == "telechargement":
            self.download(choice)
        elif step["kind"] == "separation":
            self.library.refresh()
            self.work_status.setText(f"✂️ Je découpe « {choice.name} » en pistes… "
                                     "Tu peux faire autre chose en attendant !")
            row = self.library.rows[choice.path]
            if not self.library.busy_row(row):
                self.library.enqueue(row)
        elif step["kind"] == "analyse":
            self.work_status.setText(f"🥁 J'écoute le rythme, la tonalité et le refrain de « {choice.name} »…")
            self.set_fraction(0.3)
            self.library.request_analysis(choice.path)
        elif step["kind"] == "mp3":
            self.make_mp3()
        else:
            self.prepare_mixxx()

    def all_done(self):
        self.running = False
        self.clock.stop()
        self.show_steps()
        if self.final_message:
            self.work_status.setText(self.final_message)
        self.update_buttons()

    # Téléchargement (comme l'onglet « Chercher »)
    def download(self, choice):
        import ma_musique as mm

        self.work_status.setText(f"⬇ Je télécharge « {choice.name} »…")
        proc = mm.download_process(choice.online["id"])

        def output():
            for line in bytes(proc.readAllStandardOutput()).decode(errors="replace").splitlines():
                progress = mm.parse_progress(line)
                if progress:
                    self.set_fraction(progress[0] / 100)

        def done(*_):
            final = mm.finalize_download(choice.online["id"])
            if not final:
                self.finish_step(False, "😕 Le téléchargement n'a pas marché. Réessaie dans un moment !")
                return
            choice.path, choice.online = final, None
            self.library.refresh()
            self.finish_step()

        proc.readyReadStandardOutput.connect(output)
        proc.finished.connect(done)
        self.download_proc = proc
        proc.start()

    # Séparation et analyse : faites par la bibliothèque, qui nous prévient
    def job_progress(self, path, fraction):
        if self.running and 0 <= self.current < len(self.steps) and self.step()["kind"] == "separation" \
                and self.pages[self.step()["deck"]].choice.path == path:
            self.set_fraction(fraction)

    def job_done(self, kind, path, ok):
        if not self.running or not 0 <= self.current < len(self.steps):
            return
        step = self.step()
        if step["kind"] == kind and self.pages[step["deck"]].choice.path == path:
            self.finish_step(ok, None if ok else ("😕 La séparation n'a pas marché." if kind == "separation"
                                                  else "😕 Je n'ai pas trouvé le rythme de cette chanson."))

    # --- Réglages communs au MP3 et à Mixxx ---
    def settings(self):
        """Qui mène, qui suit, de combien étirer et transposer, et où caler les refrains."""
        follower, leader = self.roles()
        infos = [self.library.song_info(p.choice.path) for p in self.pages]
        structures = [self.library.structure(p.choice.path) or {} for p in self.pages]
        # Tempo de Mixxx s'il l'a analysé (c'est lui qui joue), mesures et refrain de notre analyse
        merged = [{**structures[i], **infos[i], "first_bar": structures[i].get("first_bar", infos[i].get("first_beat", 0))}
                  for i in range(2)]
        result = match(infos[follower], infos[leader])
        timing = alignment(merged[follower], merged[leader], result["ratio"])
        return follower, leader, infos, result, timing

    def make_mp3(self):
        follower, leader, infos, result, timing = self.settings()
        files = [self.deck_file(0), self.deck_file(1)]
        if timing:
            delay = timing["mp3_delay"]
            self.work_status.setText("💾 Je fabrique ton mashup, avec les deux refrains en même temps…")
        else:  # refrain inconnu : on cale le début de la voix sur une mesure de la musique
            start = singing_start(files[follower], infos[follower]) if self.pages[follower].choice.mode == "voix" else 0
            delay = 0 - start / result["ratio"]
            self.work_status.setText("💾 Je fabrique ton mashup…")
        ratio, pitch = result["ratio"], 2 ** (result["shift"] / 12)
        trim = max(0.0, -delay) * ratio  # la suiveuse commence trop tôt : on coupe son début
        wait_ms = int(max(0.0, delay) * 1000)
        leader_duration = self.pages[leader].choice.duration
        names = [self.pages[i].choice.name for i in (follower, leader)]
        creations = MUSIC_DIR / "Mes créations"
        creations.mkdir(parents=True, exist_ok=True)
        self.mp3 = creations / f"Mashup - {names[0]} x {names[1]}.mp3"
        follower_chain = (f"[0:a]atrim=start={trim:.3f},asetpts=PTS-STARTPTS,"
                          f"rubberband=tempo={ratio:.5f}:pitch={pitch:.5f}:pitchq=quality,"
                          f"adelay={wait_ms}|{wait_ms},volume=1.15[f]")
        mix = (f"[1:a][f]amix=inputs=2:duration=first:normalize=0,afade=t=in:d=1,"
               f"afade=t=out:st={max(0, leader_duration - 4):.2f}:d=4[a]")
        self.mp3_proc = QProcess(self)
        self.mp3_proc.setProgram("ffmpeg")
        self.mp3_proc.setArguments([
            "-v", "error", "-y", "-progress", "pipe:1", "-nostats", "-i", str(files[follower]), "-i", str(files[leader]),
            "-filter_complex", f"{follower_chain};{mix}", "-map", "[a]", "-c:a", "libmp3lame", "-q:a", "2",
            "-metadata", "artist=Mashup", "-metadata", f"title={names[0]} x {names[1]}", str(self.mp3)])

        def output():
            for line in bytes(self.mp3_proc.readAllStandardOutput()).decode(errors="replace").splitlines():
                if line.startswith("out_time_us=") and line[12:].isdigit():
                    self.set_fraction(int(line[12:]) / 1e6 / max(1, leader_duration))

        def done(code, _status):
            if code != 0:
                self.finish_step(False, "😕 Je n'ai pas réussi à fabriquer le MP3.")
                return
            self.listen_btn.show()
            self.final_message = (f"🎉 Ton mashup est prêt : « {self.mp3.stem} » ! Il est dans « Mes créations ».")
            self.library.refresh()
            self.finish_step()

        self.mp3_proc.readyReadStandardOutput.connect(output)
        self.mp3_proc.finished.connect(done)
        self.mp3_proc.start()

    # --- Mixxx ---
    def deck_file(self, i):
        choice = self.pages[i].choice
        return choice.path if choice.mode == "complete" else stems_of(choice.path)[STEM_OF_MODE[choice.mode]]

    def prepare_mixxx(self):
        import mixeur  # le pilotage de Mixxx de Bip

        if not mixeur.mixxx_configured():
            self.finish_step(False, "Mixxx n'a encore jamais été ouvert. Demande à papa de l'ouvrir une première fois !")
            return
        follower, leader, infos, result, timing = self.settings()
        if timing:  # quelques mesures avant les refrains : ils tombent ensemble
            starts = [0.0, 0.0]
            starts[follower], starts[leader] = timing["follower_start"], timing["leader_start"]
        else:
            starts = [singing_start(self.deck_file(i), infos[i]) if page.choice.mode == "voix" else 0.0
                      for i, page in enumerate(self.pages)]
        self.mixxx = {"mixeur": mixeur, "shift": result["shift"], "leader": leader + 1, "starts": starts,
                      "step": "closed", "deadline": time.time() + 600, "timer": QTimer(self)}
        self.mixxx["timer"].timeout.connect(self.mixxx_tick)
        self.mixxx["timer"].start(1000)
        self.mixxx_tick()

    def close_mixxx(self):
        subprocess.run(["pkill", "-TERM", "-x", "mixxx"])
        self.close_mixxx_btn.setEnabled(False)
        self.close_mixxx_btn.setText("⏳ Je ferme Mixxx…")

    def launch_mixxx(self):
        mixeur = self.mixxx["mixeur"]
        self.close_mixxx_btn.hide()
        self.library.sync_mixxx()  # Mixxx est fermé : les pistes séparées reçoivent la grille de leur chanson
        mixeur.configure_mixxx()
        if mixeur.MIXXX_LOG.exists():
            mixeur.MIXXX_LOG.replace(mixeur.MIXXX_LOG.with_suffix(".log.avant-bip"))
        proc = QProcess()
        proc.setProgram("mixxx")
        env = QProcessEnvironment.systemEnvironment()
        env.remove("QT_QPA_PLATFORM")
        proc.setProcessEnvironment(env)
        proc.setArguments(["--log-flush-level", "warning", str(self.deck_file(0)), str(self.deck_file(1))])
        proc.setStandardOutputFile(QProcess.nullDevice())
        proc.setStandardErrorFile(QProcess.nullDevice())
        proc.startDetached()
        self.work_status.setText("🎧 J'ouvre Mixxx avec tes deux chansons…")
        self.set_fraction(0.4)
        self.mixxx.update(step="received", deadline=time.time() + 120)

    def send_setup(self):
        """Envoie tous les réglages puis l'ordre « prépare », en un seul message (donc dans l'ordre)."""
        job = self.mixxx
        data = [CC_SHIFT, 64 + job["shift"], CC_LEADER, job["leader"]]
        for cc, seconds in ((CC_START1, job["starts"][0]), (CC_START2, job["starts"][1])):
            tenths = min(16383, int(seconds * 10))
            data += [cc, tenths >> 7, cc + 1, tenths & 0x7F]
        data += [CC_PREPARE, MODE_MASHUP]
        message = " ".join(f"B0 {data[i]:02X} {data[i + 1]:02X}" for i in range(0, len(data), 2))
        QProcess.startDetached("amidi", ["-p", job["mixeur"].MIDI_PORT, "-S", message])

    def log_has(self, message):
        try:
            return f"BIP:{message}" in self.mixxx["mixeur"].MIXXX_LOG.read_text(errors="replace")
        except OSError:
            return False

    def mixxx_tick(self):
        job = self.mixxx
        mixeur = job["mixeur"]
        if time.time() > job["deadline"]:
            if job["step"] == "closed":
                self.mixxx_finished(False, "Mixxx est toujours ouvert. Ferme-le et relance le mashup !")
            else:
                self.mixxx_finished(True, "Mixxx est ouvert avec tes deux chansons. Je n'ai pas pu les caler "
                                          "tout seul : allume SYNC sur les deux platines et lance-les !")
        elif job["step"] == "closed":
            if mixeur.mixxx_running():
                self.work_status.setText("Mixxx est ouvert : je dois le fermer pour y mettre ton mashup.")
                self.close_mixxx_btn.show()
            else:
                self.launch_mixxx()
        elif job["step"] == "received":
            if self.log_has("RECU"):
                self.work_status.setText("🎚 Je cale les deux platines…")
                self.set_fraction(0.8)
                job.update(step="ready", deadline=time.time() + 90)
            else:
                self.send_setup()
        elif job["step"] == "ready":
            if self.log_has("PRET_MASHUP"):
                self.mixxx_finished(True, "🎉 C'est parti ! Les deux platines jouent, calées sur le même tempo. "
                                          "Joue avec le crossfader et les volumes. Pour changer de moment, "
                                          "clique ailleurs sur une forme d'onde : ça reste calé !")
            elif self.log_has("PRET_SANS_SYNC"):
                self.mixxx_finished(True, "Les deux platines jouent, mais je n'ai pas trouvé le rythme d'une des "
                                          "deux : cale-les à l'oreille avec SYNC !")

    def mixxx_finished(self, ok, message):
        self.mixxx["timer"].stop()
        self.mixxx = None
        self.close_mixxx_btn.hide()
        if ok:
            self.final_message = message + (f"\n\n{self.final_message}" if self.final_message else "")
        self.finish_step(ok, message)

    def closeEvent(self, event):
        if getattr(self, "clock", None):
            self.clock.stop()
        if self.mixxx:
            self.mixxx["timer"].stop()
        super().closeEvent(event)
