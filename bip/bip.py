#!/usr/bin/env python3
"""Bip : petit assistant IA local (Ollama) pour aider un enfant à utiliser ses logiciels."""
import html
import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QByteArray, QTimer, QUrl
from PyQt6.QtGui import QFont
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                             QTextBrowser, QVBoxLayout, QWidget)

import mixeur
from voix import Listener, Speaker, complete_sentences

APP_DIR = Path(__file__).resolve().parent
FICHES_FILE = APP_DIR / "fiches.md"
DATA_DIR = Path.home() / ".local/share/studio-dj-kids"  # données de l'enfant, hors du code
LOG_DIR = DATA_DIR / "conversations"
OLLAMA = "http://127.0.0.1:11434"
MODEL = "gemma3:4b"
KEEP_ALIVE = "60m"
MAX_HISTORY = 6  # nombre de messages précédents gardés en mémoire

SUGGESTIONS = ["Comment on mixe dans Mixxx ?", "Fais-moi un mix hip-hop pour scratcher", "Je veux faire un jeu vidéo"]

STYLE = """
QWidget { font-size: 16px; }
QLabel#header { font-size: 24px; font-weight: bold; color: #4a90e2; }
QLabel#sub { color: #777; }
QTextBrowser { border: 2px solid #ddd; border-radius: 14px; padding: 6px; background: white; }
QLineEdit { font-size: 18px; padding: 8px; border: 3px solid #4a90e2; border-radius: 14px; }
QPushButton { font-size: 16px; font-weight: bold; padding: 8px 14px; border-radius: 14px;
              background: #4a90e2; color: white; border: none; }
QPushButton:hover { background: #357abd; }
QPushButton:disabled { background: #9e9e9e; }
QPushButton#chip { background: #eef4fc; color: #1a3d66; border: 2px solid #4a90e2; font-weight: normal; padding: 6px 10px; }
QPushButton#chip:hover { background: #d6e6fa; }
QPushButton#reset { background: #ff9800; }
QPushButton#mic { background: #43a047; font-size: 19px; min-height: 52px; }
QPushButton#mic:hover { background: #388e3c; }
QPushButton#recording { background: #e53935; font-size: 19px; min-height: 52px; }
QPushButton#mic:disabled { background: #9e9e9e; }
QPushButton#yes { background: #43a047; font-size: 18px; min-height: 48px; }
QPushButton#no { background: #e53935; font-size: 18px; min-height: 48px; }
QPushButton#voice { background: transparent; font-size: 26px; padding: 2px 6px; }
"""


def normalize(text):
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def load_fiches():
    fiches = []
    for block in re.split(r"^## ", FICHES_FILE.read_text(), flags=re.MULTILINE)[1:]:
        head, _, body = block.partition("\n")
        name, place, keywords = (head.split("|") + ["", ""])[:3]
        fiches.append({
            "name": name.strip(),
            "place": place.strip(),
            "keywords": [normalize(k.strip()) for k in keywords.split(",") if k.strip()],
            "body": body.strip(),
        })
    return fiches


def system_prompt(fiches):
    desktop = ", ".join(f["name"] for f in fiches if f["place"] == "bureau" and f["name"] != "Ordinateur")
    folder = ", ".join(f["name"] for f in fiches if f["place"] == "Apprendre")
    return f"""Tu es Bip, un petit robot assistant gentil et joyeux. Tu aides un enfant de 9 ans à utiliser son ordinateur et ses logiciels.

Règles :
- Réponds toujours en français, avec des phrases courtes et des mots simples. 6 lignes maximum.
- Pour expliquer comment faire, donne des étapes numérotées très simples.
- Quand une « fiche pratique » est fournie, base-toi UNIQUEMENT sur elle pour les boutons et les menus. N'invente jamais un bouton ou un menu.
- Si l'enfant dit qu'il n'y arrive pas, ne répète pas la même explication : propose une AUTRE façon de faire qui est dans la fiche.
- Si tu ne sais pas, dis-le simplement et propose de demander à papa ou à un adulte.
- Ne dis pas bonjour à chaque message : réponds directement à la question.
- Encourage l'enfant et sois patient. Tu peux utiliser un emoji de temps en temps.
- Ne demande jamais d'informations personnelles (nom complet, adresse, école, mots de passe, photos).
- Si l'enfant parle d'un sujet qui n'est pas pour son âge, réponds gentiment que tu ne peux pas en parler et propose une activité.
- Si l'enfant dit qu'il est triste, qu'il a peur ou que quelqu'un lui fait du mal, dis-lui avec douceur d'en parler tout de suite à un adulte de confiance.

Tu sais aussi préparer un mix dans Mixxx : l'enfant peut te dire « fais un mix avec telle chanson et telle chanson » ou « fais-moi un mix hip-hop », « électro » ou « disco ».
Sur le bureau de l'enfant : {desktop}, et le dossier « Apprendre ».
Dans le dossier « Apprendre » : {folder}."""


def markdown_to_html(text):
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"^\s*[-*] ", "• ", text, flags=re.MULTILINE)
    return text.replace("\n", "<br>")


class Bip(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Bip, ton assistant")
        self.resize(420, 1000)
        self.setStyleSheet(STYLE)
        self.net = QNetworkAccessManager(self)
        self.fiches = load_fiches()
        self.system = system_prompt(self.fiches)
        self.history = []          # messages envoyés à l'IA (sans les fiches)
        self.bubbles = []          # (qui, texte) affichés
        self.last_fiches = []
        self.reply = None
        self.buffer = b""
        self.spoken = 0            # partie de la réponse déjà envoyée à la voix
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.speaker = Speaker(self)
        self.listener = Listener(self)
        self.listener.heard.connect(self.on_heard)
        self.listener.too_short.connect(self.on_too_short)

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        header = QLabel("🤖 Bip")
        header.setObjectName("header")
        self.voice_btn = QPushButton()
        self.voice_btn.setObjectName("voice")
        self.voice_btn.setToolTip("Allumer ou couper la voix de Bip")
        self.voice_btn.clicked.connect(self.toggle_voice)
        self.voice_btn.setVisible(self.speaker.enabled)
        self.voice_btn.setText("🔊")
        top.addWidget(header, 1)
        top.addWidget(self.voice_btn)
        sub = QLabel("Pose-moi une question sur tes logiciels !")
        sub.setObjectName("sub")
        sub.setWordWrap(True)
        root.addLayout(top)
        root.addWidget(sub)

        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        root.addWidget(self.view, 1)

        # Boutons pour confirmer un mix
        confirm = QHBoxLayout()
        self.yes_btn = QPushButton("✅ Oui, on mixe !")
        self.yes_btn.setObjectName("yes")
        self.yes_btn.clicked.connect(lambda: self.answer_mix(True))
        self.no_btn = QPushButton("❌ Non")
        self.no_btn.setObjectName("no")
        self.no_btn.clicked.connect(lambda: self.answer_mix(False))
        confirm.addWidget(self.yes_btn, 2)
        confirm.addWidget(self.no_btn, 1)
        root.addLayout(confirm)
        self.pending_mix = None
        self.mix_job = None
        self.show_confirm(False)

        self.chips = []
        for text in SUGGESTIONS:
            chip = QPushButton(text)
            chip.setObjectName("chip")
            chip.clicked.connect(lambda _, t=text: self.ask(t))
            root.addWidget(chip)
            self.chips.append(chip)

        self.mic_btn = QPushButton("🎤 Appuie et parle")
        self.mic_btn.setObjectName("mic")
        self.mic_btn.pressed.connect(self.start_listening)
        self.mic_btn.released.connect(self.stop_listening)
        self.mic_btn.setVisible(self.listener.available)
        root.addWidget(self.mic_btn)

        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Écris ta question ici…")
        self.input.returnPressed.connect(lambda: self.ask(self.input.text()))
        self.send_btn = QPushButton("Envoyer")
        self.send_btn.clicked.connect(lambda: self.ask(self.input.text()))
        row.addWidget(self.input, 1)
        row.addWidget(self.send_btn)
        root.addLayout(row)

        reset = QPushButton("🧹 Nouvelle discussion")
        reset.setObjectName("reset")
        reset.clicked.connect(self.reset)
        root.addWidget(reset)

        self.bubbles.append(("bip", "Salut ! Je suis Bip 🤖\nJe me réveille, ça prend quelques secondes…"))
        self.render()
        self.set_busy(True)
        self.warm_up()

    # --- Affichage ---
    def render(self):
        parts = []
        for who, text in self.bubbles:
            if who == "me":
                parts.append(f'<table width="100%" cellpadding="10"><tr><td width="15%"></td>'
                             f'<td bgcolor="#4a90e2" style="color:white">{markdown_to_html(text)}</td></tr></table>')
            else:
                parts.append(f'<table width="100%" cellpadding="10"><tr>'
                             f'<td bgcolor="#eef4fc" style="color:#1a1a1a">{markdown_to_html(text)}</td>'
                             f'<td width="15%"></td></tr></table>')
        self.view.setHtml("".join(parts))
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def bip_says(self, text, replace=False):
        if replace:
            self.bubbles[-1] = ("bip", text)
        else:
            self.bubbles.append(("bip", text))
        self.render()
        self.speaker.say(text)

    def set_mic_look(self, name, text):
        self.mic_btn.setObjectName(name)
        self.mic_btn.setText(text)
        self.mic_btn.style().unpolish(self.mic_btn)
        self.mic_btn.style().polish(self.mic_btn)

    def set_busy(self, busy):
        for w in [self.send_btn, self.mic_btn, *self.chips]:
            w.setEnabled(not busy)
        self.input.setEnabled(not busy)
        if not busy:
            self.input.setFocus()

    def reset(self):
        if self.reply or self.mix_job:
            return
        self.pending_mix = None
        self.show_confirm(False)
        self.speaker.stop()
        self.history.clear()
        self.last_fiches = []
        self.bubbles = []
        self.bip_says("C'est reparti ! Pose-moi ta question 😀")

    # --- Voix ---
    def toggle_voice(self):
        self.speaker.enabled = not self.speaker.enabled
        self.voice_btn.setText("🔊" if self.speaker.enabled else "🔇")
        if not self.speaker.enabled:
            self.speaker.stop()

    def start_listening(self):
        self.speaker.stop()
        self.set_mic_look("recording", "🔴 Je t'écoute… lâche quand tu as fini")
        self.listener.start()

    def stop_listening(self):
        if self.mic_btn.objectName() != "recording":
            return
        self.set_mic_look("mic", "👂 Je comprends ce que tu as dit…")
        self.set_busy(True)
        self.listener.stop()

    def on_too_short(self):
        self.set_mic_look("mic", "🎤 Appuie et parle")
        self.set_busy(False)
        self.bip_says("Garde le bouton vert appuyé pendant que tu parles, puis lâche-le 😉")

    def on_heard(self, text):
        self.set_mic_look("mic", "🎤 Appuie et parle")
        self.set_busy(False)
        if text:
            self.ask(text)
        else:
            self.bip_says("Je n'ai pas bien entendu 🙉 Tu peux répéter un peu plus fort ?")

    # --- IA ---
    def post(self, path, payload):
        request = QNetworkRequest(QUrl(OLLAMA + path))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        request.setTransferTimeout(0)
        return self.net.post(request, QByteArray(json.dumps(payload).encode()))

    def warm_up(self):
        # Charge le modèle et prépare les consignes en mémoire pour que la 1re réponse soit plus rapide
        reply = self.post("/api/chat", {
            "model": MODEL, "stream": False, "keep_alive": KEEP_ALIVE, "options": {"num_predict": 1},
            "messages": [{"role": "system", "content": self.system}, {"role": "user", "content": "Bonjour"}],
        })

        def done():
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.bip_says("Oups, je n'arrive pas à me réveiller 😴\nDemande à papa de vérifier que l'IA (Ollama) est bien lancée.", replace=True)
            else:
                self.bip_says("Salut ! Je suis Bip 🤖\nJe peux t'aider à utiliser tes logiciels : Mixxx, Ma Musique, Scratch, Tux Paint…\n"
                              + ("Appuie sur le bouton vert pour me parler, ou écris ta question !" if self.listener.available
                                 else "Pose-moi ta question !"), replace=True)
            reply.deleteLater()
            self.set_busy(False)
        reply.finished.connect(done)

    def pick_fiches(self, question):
        q = normalize(question)
        scored = []
        for fiche in self.fiches:
            score = sum(1 for k in fiche["keywords"] if re.search(rf"\b{re.escape(k)}\b", q))
            if score:
                scored.append((score, fiche))
        scored.sort(key=lambda s: -s[0])
        found = [f for _, f in scored[:2]]
        if found:
            self.last_fiches = found
            return found
        return self.last_fiches  # question de suite (« et après ? »)

    # --- Mix automatique dans Mixxx ---
    def show_confirm(self, visible):
        self.yes_btn.setVisible(visible)
        self.no_btn.setVisible(visible)

    def user_says(self, text):
        self.bubbles.append(("me", text))
        self.render()
        self.log("Enfant", text)

    def handle_mix_commands(self, question):
        """Gère les demandes liées au mix. Renvoie True si la question est traitée ici."""
        if self.pending_mix:
            if mixeur.is_yes(question) or mixeur.is_no(question):
                self.input.clear()
                self.user_says(question)
                self.answer_mix(mixeur.is_yes(question), echo=False)
                return True
            self.pending_mix = None
            self.show_confirm(False)
        if mixeur.mixxx_running() and mixeur.is_transition_request(question):
            self.input.clear()
            self.user_says(question)
            mixeur.send_midi(mixeur.CC_TRANSITION)
            self.bip_says("C'est parti, j'enchaîne doucement vers la platine de droite ! 🎚")
            return True
        if mixeur.mixxx_running() and mixeur.is_stop_request(question):
            self.input.clear()
            self.user_says(question)
            mixeur.send_midi(mixeur.CC_STOP)
            self.bip_says("J'arrête la musique ⏹")
            return True
        if mixeur.is_mix_request(question):
            self.input.clear()
            self.speaker.stop()
            self.user_says(question)
            self.prepare_mix(question)
            return True
        return False

    def prepare_mix(self, question):
        ambiance = mixeur.find_ambiance(question)
        if ambiance:
            mode, queries = mixeur.pick_from_ambiance(ambiance)
        else:
            mode, queries = mixeur.MODE_MIX, mixeur.songs_in_request(question)
        if len(queries) < 2:
            self.bip_says("Pour un mix, il me faut deux chansons ! Dis-moi par exemple : "
                          "« fais un mix avec Alors on danse et One More Time ». "
                          f"Je connais aussi ces styles : {mixeur.ambiance_names()}.")
            return
        self.set_busy(True)
        self.bubbles.append(("bip", "🔎 Je cherche tes chansons…"))
        self.render()
        self.resolver = mixeur.Resolver(queries, self)
        self.resolver.resolved.connect(lambda items: self.on_resolved(mode, items))
        self.resolver.failed.connect(self.on_resolve_failed)
        self.resolver.start()

    def on_resolved(self, mode, items):
        self.resolver.deleteLater()
        self.set_busy(False)
        self.propose_mix(mode, items)

    def on_resolve_failed(self, message):
        self.resolver.deleteLater()
        self.set_busy(False)
        self.bip_says(message, replace=True)
        self.log("Bip", message)

    def propose_mix(self, mode, items):
        self.pending_mix = (mode, items)
        if mode == mixeur.MODE_SCRATCH:
            text = (f"Je mets le beat « {items[0]['name']} » sur la platine de gauche, "
                    "et un son à scratcher sur celle de droite. On y va ?")
        else:
            text = (f"Je mets « {items[0]['name']} » sur la platine de gauche "
                    f"et « {items[1]['name']} » sur la platine de droite. C'est bon ?")
        self.bip_says(text, replace=True)
        self.log("Bip", text)
        self.show_confirm(True)

    def answer_mix(self, yes, echo=True):
        if not self.pending_mix:
            return
        mode, items = self.pending_mix
        self.pending_mix = None
        self.show_confirm(False)
        if echo:
            self.user_says("Oui !" if yes else "Non")
        if not yes:
            self.bip_says("D'accord ! Tu peux m'écrire les noms des chansons dans la case en bas, "
                          "je comprendrai mieux qu'à l'oral 😉 Par exemple : fais un mix avec Get Lucky et Californication.")
            return
        self.set_busy(True)
        self.bip_says("🎛 C'est parti, je prépare ton mix !")
        self.bubbles.append(("bip", "…"))
        self.mix_job = mixeur.MixJob(mode, items, self)
        self.mix_job.status.connect(self.on_mix_status)
        self.mix_job.finished.connect(self.on_mix_finished)
        self.mix_job.start()

    def on_mix_status(self, text, speak):
        self.bubbles[-1] = ("bip", text)
        self.render()
        if speak:
            self.speaker.say(text)

    def on_mix_finished(self, ok, message):
        self.mix_job.deleteLater()
        self.mix_job = None
        self.set_busy(False)
        self.bip_says(message, replace=True)
        self.log("Bip", message)

    def ask(self, question):
        question = question.strip()
        if not question or self.reply or self.mix_job:
            return
        if self.handle_mix_commands(question):
            return
        self.input.clear()
        self.speaker.stop()
        self.set_busy(True)
        self.bubbles.append(("me", question))
        self.bubbles.append(("bip", "🤔 Je réfléchis…"))
        self.render()

        fiches = self.pick_fiches(question)
        content = question
        if fiches:
            context = "\n\n".join(f"Fiche pratique « {f['name']} » :\n{f['body']}" for f in fiches)
            content = (f"{context}\n\n(Si la fiche ne donne pas la réponse, dis honnêtement que tu n'es pas sûr "
                       f"au lieu d'inventer.)\n\nQuestion de l'enfant : {question}")
        messages = [{"role": "system", "content": self.system}, *self.history[-MAX_HISTORY:],
                    {"role": "user", "content": content}]
        self.history.append({"role": "user", "content": question})
        self.answer = ""
        self.spoken = 0
        self.buffer = b""
        self.reply = self.post("/api/chat", {
            "model": MODEL, "stream": True, "keep_alive": KEEP_ALIVE, "messages": messages,
            "options": {"temperature": 0.4, "num_predict": 300},
        })
        self.reply.readyRead.connect(self.on_chunk)
        self.reply.finished.connect(self.on_finished)
        self.log("Enfant", question)

    def on_chunk(self):
        self.buffer += bytes(self.reply.readAll())
        *lines, self.buffer = self.buffer.split(b"\n")
        for line in lines:
            try:
                data = json.loads(line)
            except ValueError:
                continue
            self.answer += data.get("message", {}).get("content", "")
        if self.answer.strip():
            self.bubbles[-1] = ("bip", self.answer.strip())
            self.render()
        # Bip lit chaque phrase dès qu'elle est terminée, sans attendre la fin de la réponse
        end = complete_sentences(self.answer, self.spoken)
        if end > self.spoken:
            self.speaker.say(self.answer[self.spoken:end])
            self.spoken = end

    def on_finished(self):
        self.on_chunk()
        if self.reply.error() != QNetworkReply.NetworkError.NoError or not self.answer.strip():
            self.bip_says("Oups, j'ai eu un petit bug 🙃 Tu peux reposer ta question ?", replace=True)
            self.history.pop()
        else:
            self.speaker.say(self.answer[self.spoken:])
            self.history.append({"role": "assistant", "content": self.answer.strip()})
            self.log("Bip", self.answer.strip())
        self.reply.deleteLater()
        self.reply = None
        self.render()
        self.set_busy(False)

    def log(self, who, text):
        # Historique lisible par les parents
        path = LOG_DIR / f"{datetime.now():%Y-%m-%d}.txt"
        with path.open("a") as f:
            f.write(f"[{datetime.now():%H:%M}] {who} : {text}\n\n")

    def closeEvent(self, event):
        # Libère la mémoire du PC quand Bip est fermé
        self.speaker.stop()
        self.listener.stop()
        reply = self.post("/api/generate", {"model": MODEL, "keep_alive": 0})
        reply.finished.connect(QApplication.quit)
        QTimer.singleShot(3000, QApplication.quit)
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("Bip")
    app.setDesktopFileName("bip")
    app.setFont(QFont(app.font().family(), 12))
    app.setQuitOnLastWindowClosed(False)
    window = Bip()
    window.show()
    sys.exit(app.exec())
