"""Voix de Bip : synthèse vocale (Piper + effet robot) et reconnaissance vocale (whisper.cpp), 100 % locales."""
import os
import re
import tempfile
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, pyqtSignal

HOME = Path.home()
VOICE_DIR = Path(__file__).resolve().parent / "voix"
PIPER = VOICE_DIR / "piper" / "piper"
VOICE = VOICE_DIR / "fr_FR-tom-medium.onnx"
VOICE_RATE = 44100
ROBOT_FX = (f"asetrate={VOICE_RATE}*1.12,aresample={VOICE_RATE},atempo=1/1.12,"
            "flanger=delay=2:depth=2:speed=0.8,aecho=0.8:0.7:12:0.25")

WHISPER_DIR = HOME / ".local/share/whisper.cpp"
WHISPER = WHISPER_DIR / "build/bin/whisper-cli"
WHISPER_MODEL = WHISPER_DIR / "models/ggml-small.bin"
# Aide Whisper à reconnaître les noms des logiciels
BASE_PROMPT = ("Bip, Mixxx, Ma Musique, Scratch, Tux Paint, TuxMath, BeepBox, Song Maker, Music Lab, "
                  "GCompris, KTouch, Stellarium, KGeography, Marble, KTurtle, Blinken, Vikidia, Lumni, "
                  "platine, crossfader, BPM, sync.")
KNOWN_ARTISTS = ["Daft Punk", "Red Hot Chili Peppers", "Stromae", "Orelsan", "Angèle", "Aya Nakamura", "Michael Jackson",
                 "Bee Gees", "Kool & The Gang", "Earth, Wind & Fire", "David Guetta", "Bigflo et Oli", "Soprano", "Vianney"]
MUSIC_DIR = HOME / "Musique"


def whisper_prompt():
    """Vocabulaire donné à Whisper : logiciels + artistes connus + artistes déjà dans sa musique."""
    artists = list(dict.fromkeys(KNOWN_ARTISTS + [p.stem.split(" - ")[0] for p in MUSIC_DIR.glob("*.mp3")]))
    return BASE_PROMPT + " " + ", ".join(artists[:40]) + "."


# Phrases que Whisper invente parfois quand il n'entend que du bruit
HALLUCINATIONS = re.compile(r"sous-titr|amara|merci d'avoir regard|abonnez-vous|radio-canada", re.IGNORECASE)
MIN_RECORD_SECONDS = 0.6

EMOJI = re.compile("[\U0001F000-\U0001FFFF☀-➿️‍]")
SENTENCE_END = re.compile(r"(?<!\d)[.!?…:]+(?=\s)|\n")


def speakable(text):
    """Nettoie un texte pour qu'il soit lu correctement à voix haute."""
    text = EMOJI.sub("", text)
    text = re.sub(r"[*_#`•]", "", text)
    text = re.sub(r"\bMixxx\b", "Mix", text)
    text = re.sub(r"\bBPM\b", "B P M", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if re.search(r"\w", text) else ""


def complete_sentences(text, start):
    """Renvoie l'index de fin de la dernière phrase complète dans text[start:]."""
    end = start
    for match in SENTENCE_END.finditer(text, start):
        end = match.end()
    return end


class Speaker(QObject):
    """Lit les phrases à voix haute, dans l'ordre, en préparant la suivante pendant la lecture."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.enabled = PIPER.exists() and VOICE.exists()
        self.tmp = Path(tempfile.mkdtemp(prefix="bip-voix-"))
        self.pending = []      # textes à synthétiser
        self.ready = []        # fichiers prêts à jouer
        self.synth_proc = None
        self.play_proc = None
        self.counter = 0
        self.generation = 0    # change à chaque stop() pour ignorer les anciens résultats

    def say(self, text):
        text = speakable(text)
        if not self.enabled or not text:
            return
        self.pending.append(text)
        self.next_synth()

    def next_synth(self):
        if self.synth_proc or not self.pending:
            return
        text = self.pending.pop(0)
        self.counter += 1
        out = self.tmp / f"{self.counter}.wav"
        proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PIPER", str(PIPER))
        env.insert("VOICE", str(VOICE))
        env.insert("FX", ROBOT_FX)
        env.insert("OUT", str(out))
        proc.setProcessEnvironment(env)
        generation = self.generation
        proc.finished.connect(lambda *_: self.synth_done(proc, out, generation))
        proc.start("bash", ["-c", '"$PIPER" -m "$VOICE" --output_raw 2>/dev/null | '
                                  f'ffmpeg -v error -y -f s16le -ar {VOICE_RATE} -ac 1 -i - -af "$FX" "$OUT"'])
        proc.write(text.encode() + b"\n")
        proc.closeWriteChannel()
        self.synth_proc = proc

    def synth_done(self, proc, out, generation):
        proc.deleteLater()
        if generation != self.generation:
            out.unlink(missing_ok=True)
            return
        self.synth_proc = None
        if out.exists():
            self.ready.append(out)
        self.next_play()
        self.next_synth()

    def next_play(self):
        if self.play_proc or not self.ready:
            return
        path = self.ready.pop(0)
        proc = QProcess(self)
        generation = self.generation
        proc.finished.connect(lambda *_: self.play_done(proc, path, generation))
        proc.start("pw-play", [str(path)])
        self.play_proc = proc

    def play_done(self, proc, path, generation):
        proc.deleteLater()
        path.unlink(missing_ok=True)
        if generation != self.generation:
            return
        self.play_proc = None
        self.next_play()

    def stop(self):
        self.generation += 1
        self.pending.clear()
        for path in self.ready:
            path.unlink(missing_ok=True)
        self.ready.clear()
        for proc in (self.synth_proc, self.play_proc):
            if proc:
                proc.kill()
        self.synth_proc = self.play_proc = None


class Listener(QObject):
    """Enregistre le micro tant que le bouton est appuyé, puis transcrit avec Whisper."""
    heard = pyqtSignal(str)      # texte compris ("" si rien compris)
    too_short = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.available = WHISPER.exists() and WHISPER_MODEL.exists()
        self.wav = Path(tempfile.mkdtemp(prefix="bip-micro-")) / "question.wav"
        self.rec_proc = None
        self.stt_proc = None

    def start(self):
        if self.rec_proc or self.stt_proc:
            return
        self.wav.unlink(missing_ok=True)
        self.rec_proc = QProcess(self)
        self.rec_proc.finished.connect(self.recorded)
        self.rec_proc.start("pw-record", ["--rate", "16000", "--channels", "1", "--format", "s16", str(self.wav)])

    def stop(self):
        if self.rec_proc:
            self.rec_proc.terminate()

    def recorded(self):
        self.rec_proc.deleteLater()
        self.rec_proc = None
        seconds = (self.wav.stat().st_size - 44) / 32000 if self.wav.exists() else 0
        if seconds < MIN_RECORD_SECONDS:
            self.too_short.emit()
            return
        self.stt_proc = QProcess(self)
        self.stt_proc.finished.connect(self.transcribed)
        self.stt_proc.start(str(WHISPER), [
            "-m", str(WHISPER_MODEL), "-l", "fr", "-t", str(os.cpu_count() or 4),
            "-nt", "-np", "--prompt", whisper_prompt(), "-f", str(self.wav),
            # Whisper analyse 30 s d'audio par défaut (1500) : on réduit à la durée réelle, 3x plus rapide
            "-ac", str(min(1500, max(448, int((seconds + 4) * 50 / 64 + 1) * 64))),
        ])

    def transcribed(self):
        text = bytes(self.stt_proc.readAllStandardOutput()).decode(errors="replace")
        self.stt_proc.deleteLater()
        self.stt_proc = None
        text = re.sub(r"\[[^\]]*\]|\([^)]*\)", "", text)  # [Musique], (rires)…
        text = re.sub(r"\s+", " ", text).strip()
        if HALLUCINATIONS.search(text) or not re.search(r"\w", text):
            text = ""
        self.heard.emit(text)
