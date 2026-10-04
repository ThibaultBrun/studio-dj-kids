"""Mix automatique : Bip trouve ou télécharge deux morceaux, ouvre Mixxx et les prépare sur les platines."""
import json
import random
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, pyqtSignal

HOME = Path.home()
APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(HOME / ".local/share/ma-musique"))
import ma_musique as mm  # noqa: E402  (on réutilise la recherche et le téléchargement de Ma Musique)

AMBIANCES_FILE = APP_DIR / "ambiances.md"
MIXXX_DIR = HOME / ".mixxx"
MIXXX_CFG = MIXXX_DIR / "mixxx.cfg"
MIXXX_LOG = MIXXX_DIR / "mixxx.log"
MAPPING = MIXXX_DIR / "controllers" / "Bip.midi.xml"
DEVICE_KEY = "VirMIDI_1-0"          # nom du port MIDI virtuel vu par Mixxx, espaces remplacés par _
MIDI_PORT = "hw:VirMIDI,0"
CC_PREPARE, CC_TRANSITION, CC_STOP = 0x70, 0x71, 0x72
MODE_MIX, MODE_SCRATCH = 1, 2
MAX_TRIES = 3
MAX_DURATION = 15 * 60

MIX_WORD = r"(?:re)?mix\w*|melang\w*"
MIX_VERB = r"(?:re)?mix(?:e|er|ez)|melang\w*"  # « mixe… » mais pas « Mixxx… » (le logiciel)
MIX_REQUEST = re.compile(
    r"\b(fai[st]?|faire|lance[rz]?|commence[rz]?|on commence|prepare[rz]?|veux|voudrais|demarre[rz]?|on fait|cree[rz]?)\b"
    rf".*\b({MIX_WORD})|^\s*(bip\W*)?({MIX_VERB})\b", re.IGNORECASE)
TRANSITION_REQUEST = re.compile(r"\b(enchaine|enchainer|transition|passe a la (deuxieme|2)|lance la (deuxieme|platine 2))\b")
STOP_REQUEST = re.compile(r"\b(arrete|stop|stoppe|coupe)\b.*\b(musique|mix|tout|son)\b|^stop\b")
YES = re.compile(r"^\s*(oui|ouais|ok|okay|vas[- ]y|d'?accord|go|c'?est bon|yes|super)\b")
NO = re.compile(r"^\s*(non|nan|pas ca|annule|stop)\b")
SONGS_AFTER_MIX = re.compile(r"\b(?:(?:re)?mix\w*|m[ée]lang\w*)\s+(.+)$", re.IGNORECASE)
LEADING_WORDS = re.compile(r"^(?:-?moi|un|une|le|la|les|de|du|des|d'|entre|avec)\s+", re.IGNORECASE)
SONG_SEPARATOR = re.compile(r"\s+(?:et|puis|avec)\s+", re.IGNORECASE)
STOPWORDS = {"de", "des", "du", "la", "le", "les", "the", "et", "un", "une", "chanson", "musique", "son", "official", "video", "clip"}


def normalize(text):
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


QUESTION = re.compile(r"\b(comment|c'?est quoi|pourquoi|ou est|ou se trouve|quel|quelle|explique|ca veut dire)\b")


def is_mix_request(text):
    q = normalize(text).replace("-", " ")
    if QUESTION.search(q):  # « comment on mixe ? » est une question, pas une demande
        return False
    if MIX_REQUEST.search(q):
        return True
    # La reconnaissance vocale abîme souvent le verbe (« je vous réactivé par un Mixxx hip-hop ») :
    # « mix » + un style connu ou « avec » suffit
    return bool(re.search(rf"\b({MIX_WORD})", q) and (find_ambiance(text) or re.search(r"\bavec\b", q)))


def is_transition_request(text):
    return bool(TRANSITION_REQUEST.search(normalize(text)))


def is_stop_request(text):
    return bool(STOP_REQUEST.search(normalize(text)))


def is_yes(text):
    return bool(YES.search(normalize(text)))


def is_no(text):
    return bool(NO.search(normalize(text)))


# --- Ambiances (listes de morceaux prêtes à mixer) ---
def load_ambiances():
    ambiances = []
    for block in re.split(r"^## ", AMBIANCES_FILE.read_text(), flags=re.MULTILINE)[1:]:
        head, _, body = block.partition("\n")
        name, mode, keywords = (head.split("|") + ["", ""])[:3]
        lines = [line.strip() for line in body.splitlines()]
        ambiances.append({
            "name": name.strip(),
            "mode": mode.strip(),
            "keywords": [normalize(k.strip()).replace("-", " ") for k in keywords.split(",") if k.strip()],
            "tracks": [line[2:].strip() for line in lines if line.startswith("- ")],
            "beats": [line[5:].strip() for line in lines if line.startswith("beat:")],
            "scratches": [line[8:].strip() for line in lines if line.startswith("scratch:")],
        })
    return ambiances


def find_ambiance(text):
    q = normalize(text).replace("-", " ")  # « hip-hop » = « hip hop »
    if re.search(r"\bavec\b", q):  # « un mix avec telle et telle chanson » : chansons précises, pas une ambiance
        return None
    for ambiance in load_ambiances():
        if any(re.search(rf"\b{re.escape(k)}\b", q) for k in ambiance["keywords"]):
            return ambiance
    return None


def pick_from_ambiance(ambiance):
    """Renvoie (mode, [recherche platine 1, recherche platine 2])."""
    if ambiance["mode"] == "scratch":
        return MODE_SCRATCH, [random.choice(ambiance["beats"]), random.choice(ambiance["scratches"])]
    return MODE_MIX, random.sample(ambiance["tracks"], 2)


def ambiance_names():
    return ", ".join(a["name"] for a in load_ambiances())


def songs_in_request(text):
    """« fais un mix avec X et Y », « le remix de X avec Y » -> ["X", "Y"] (sans IA : plus fiable qu'un petit modèle)."""
    match = SONGS_AFTER_MIX.search(text.strip().rstrip(" !?."))
    if not match:
        return []
    rest = match.group(1)
    while LEADING_WORDS.match(rest):
        rest = LEADING_WORDS.sub("", rest, count=1)
    songs = [part.strip(" «»\"',") for part in SONG_SEPARATOR.split(rest)]
    return [song for song in songs if song][:2]


# --- Recherche dans la musique déjà téléchargée ---
def words(text):
    return {w for w in re.findall(r"\w+", normalize(text)) if len(w) > 1 and w not in STOPWORDS}


def find_local(query):
    """Cherche dans ~/Musique un MP3 qui contient (presque) tous les mots de la recherche."""
    wanted = words(query)
    if not wanted:
        return None
    best, best_score = None, 0.0
    for path in mm.MUSIC_DIR.glob("*.mp3"):
        have = words(path.stem)
        score = len(wanted & have) / len(wanted) + len(wanted & have) / max(len(have), 1) / 10
        if score > best_score:
            best, best_score = path, score
    return best if best_score >= 0.75 else None


# --- Mixxx ---
def mixxx_running():
    return subprocess.run(["pgrep", "-x", "mixxx"], capture_output=True).returncode == 0


def mixxx_configured():
    return MIXXX_CFG.exists()


def configure_mixxx():
    """Active le contrôleur virtuel « Bip » dans la configuration de Mixxx (Mixxx doit être fermé)."""
    lines = MIXXX_CFG.read_text().splitlines()
    wanted = [
        ("[Controller]", f"{DEVICE_KEY} 1"),
        ("[ControllerPreset]", f"{DEVICE_KEY} {MAPPING}"),
        # Sinon Mixxx pose une question au démarrage, qui bloque l'activation du contrôleur
        ("[Config]", "show_menubar_hint 0"),
        ("[Config]", "hide_menubar 0"),
    ]
    for group, entry in wanted:
        key = entry.split()[0]
        if group not in lines:
            lines += ["", group]
        start = lines.index(group) + 1
        end = next((i for i in range(start, len(lines)) if lines[i].startswith("[")), len(lines))
        lines[start:end] = [line for line in lines[start:end] if line.split(" ", 1)[0] != key]
        lines.insert(start, entry)
    MIXXX_CFG.write_text("\n".join(lines) + "\n")


def send_midi(cc, value=1):
    QProcess.startDetached("amidi", ["-p", MIDI_PORT, "-S", f"B0 {cc:02X} {value:02X}"])


class Resolver(QObject):
    """Identifie chaque morceau demandé : fichier déjà téléchargé, sinon 1er bon résultat YouTube."""
    resolved = pyqtSignal(list)   # [{"name", "path" ou "video_id"}, …]
    failed = pyqtSignal(str)

    def __init__(self, queries, parent=None):
        super().__init__(parent)
        self.queries = queries
        self.items = []
        self.proc = None

    def start(self):
        self.next()

    def next(self):
        if len(self.items) == len(self.queries):
            self.resolved.emit(self.items)
            return
        query = self.queries[len(self.items)]
        local = find_local(query)
        if local:
            self.items.append({"name": local.stem, "path": local})
            self.next()
            return
        self.proc = mm.ytdlp_process(["--flat-playlist", "--dump-json", f"ytsearch5:{query}"])
        self.proc.finished.connect(lambda *_: self.search_done(query))
        self.proc.start()

    def search_done(self, query):
        out = bytes(self.proc.readAllStandardOutput()).decode(errors="replace")
        self.proc.deleteLater()
        for line in out.splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("id") and entry.get("duration") and entry["duration"] <= MAX_DURATION:
                artist, title = mm.make_name(entry)
                self.items.append({"name": f"{artist} - {title}", "video_id": entry["id"]})
                self.next()
                return
        self.failed.emit(f"Je n'ai pas trouvé « {query} » 😕 Essaie avec le nom de l'artiste en plus !")


class MixJob(QObject):
    """Prépare un mix : télécharge ce qui manque, ouvre Mixxx, synchronise via le contrôleur virtuel."""
    status = pyqtSignal(str, bool)   # texte, à dire à voix haute ?
    finished = pyqtSignal(bool, str)  # réussi ?, message final

    def __init__(self, mode, items, parent=None):
        super().__init__(parent)
        self.mode = mode
        self.items = items
        self.files = []
        self.tries = 0
        self.proc = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.step = None
        self.deadline = 0

    def start(self):
        self.next_track()

    # Étape 1 : télécharger les morceaux qui ne sont pas encore là
    def next_track(self):
        if len(self.files) == len(self.items):
            self.wait_for_mixxx_closed()
            return
        item = self.items[len(self.files)]
        if "path" in item:
            self.files.append(item["path"])
            self.next_track()
            return
        self.tries = 0
        self.status.emit(f"⬇ Je télécharge « {item['name']} »…", True)
        self.download()

    def download(self):
        self.tries += 1
        self.proc = mm.download_process(self.items[len(self.files)]["video_id"])
        self.proc.readyReadStandardOutput.connect(self.download_output)
        self.proc.finished.connect(self.download_done)
        self.proc.start()

    def download_output(self):
        name = self.items[len(self.files)]["name"]
        for line in bytes(self.proc.readAllStandardOutput()).decode(errors="replace").splitlines():
            progress = mm.parse_progress(line)
            if progress:
                self.status.emit(f"« {name} »\n{progress[1]}", False)

    def download_done(self):
        final = mm.finalize_download(self.items[len(self.files)]["video_id"])
        if final:
            self.files.append(final)
            self.next_track()
        elif self.tries < MAX_TRIES:
            self.status.emit("🔁 Nouvel essai…", False)
            self.download()
        else:
            self.fail("Le téléchargement n'a pas marché 😕 Réessaie dans un moment.")

    # Étape 2 : Mixxx doit être fermé pour charger les nouveaux morceaux
    def wait_for_mixxx_closed(self):
        if not mixxx_configured():
            self.fail("Mixxx n'a encore jamais été ouvert. Demande à papa de l'ouvrir une première fois !")
            return
        if mixxx_running():
            self.status.emit("Ferme Mixxx, je l'ouvre avec tes chansons dès qu'il est fermé 😉", True)
            self.wait("mixxx_closed", 180)
        else:
            self.launch_mixxx()

    # Étape 3 : ouvrir Mixxx avec les morceaux sur les platines 1 et 2
    def launch_mixxx(self):
        configure_mixxx()
        if MIXXX_LOG.exists():
            MIXXX_LOG.replace(MIXXX_LOG.with_suffix(".log.avant-bip"))  # pour ne lire que les nouveaux messages
        mixxx = QProcess()
        mixxx.setProgram("mixxx")
        env = QProcessEnvironment.systemEnvironment()
        env.remove("QT_QPA_PLATFORM")  # Mixxx doit toujours s'afficher, même si Bip a été lancé sans écran
        mixxx.setProcessEnvironment(env)
        mixxx.setArguments(["--log-flush-level", "warning", *map(str, self.files)])
        mixxx.setStandardOutputFile(QProcess.nullDevice())
        mixxx.setStandardErrorFile(QProcess.nullDevice())
        mixxx.startDetached()  # Mixxx continue de tourner même si Bip est fermé
        self.status.emit("🎧 J'ouvre Mixxx avec tes chansons…", True)
        self.wait("received", 120)

    # Étape 4 : envoyer l'ordre « prépare » jusqu'à ce que le contrôleur virtuel réponde
    def wait(self, step, seconds):
        self.step = step
        self.deadline = time.time() + seconds
        self.timer.start(1500)

    def log_has(self, *messages):
        try:
            log = MIXXX_LOG.read_text(errors="replace")
        except OSError:
            return None
        return next((m for m in messages if f"BIP:{m}" in log), None)

    def tick(self):
        if time.time() > self.deadline:
            self.timer.stop()
            if self.step == "mixxx_closed":
                self.fail("Mixxx est toujours ouvert. Ferme-le et redemande-moi le mix !")
            else:
                self.finished.emit(True, "Mixxx est ouvert avec tes chansons sur les platines, "
                                         "mais je n'ai pas réussi à les régler tout seul. À toi de jouer !")
            return
        if self.step == "mixxx_closed":
            if not mixxx_running():
                self.timer.stop()
                self.launch_mixxx()
        elif self.step == "received":
            if self.log_has("RECU"):
                self.status.emit("🎚 Je règle les platines…", True)
                self.step = "ready"
                self.deadline = time.time() + 90
            else:
                send_midi(CC_PREPARE, self.mode)
        elif self.step == "ready":
            result = self.log_has("PRET_SCRATCH", "PRET_SANS_SYNC", "PRET")
            if result:
                self.timer.stop()
                self.finished.emit(True, self.final_message(result))

    def final_message(self, result):
        if result == "PRET_SCRATCH":
            return ("C'est prêt ! Le beat tourne sur la platine de gauche. 🎶\n"
                    "Sur la platine de droite, il y a le son à scratcher : "
                    "fais tourner la grande roue d'avant en arrière en rythme !")
        if result == "PRET_SANS_SYNC":
            return ("La platine de gauche joue ! 🎶 Je n'ai pas trouvé le rythme d'une des chansons, "
                    "alors cale-les à l'oreille. Quand tu veux, lance la platine de droite et glisse le crossfader vers la droite.")
        return ("C'est prêt ! La platine de gauche joue, et celle de droite est calée au même rythme. 🎶\n"
                "Quand tu veux, lance la platine de droite et glisse doucement le crossfader vers la droite. "
                "Ou dis-moi « enchaîne » et je le fais pour toi !")

    def fail(self, message):
        self.timer.stop()
        self.finished.emit(False, message)
