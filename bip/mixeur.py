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
MIDI_PORT = "hw:VirMIDI,0"
CC_PREPARE, CC_TRANSITION, CC_STOP = 0x70, 0x71, 0x72
MODE_MIX, MODE_SCRATCH = 1, 2
MAX_TRIES = 3
MAX_DURATION = 15 * 60
MAX_CHOICES = 3

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
LEADING_WORDS = re.compile(
    r"^(?:-?moi|un|une|le|la|les|de|du|des|d'|entre|avec|chansons?|musiques?|sons?|morceaux?|titres?)\s+", re.IGNORECASE)


def strip_leading_words(text):
    """« une chanson des Daft Punk » -> « Daft Punk » (YouTube trouve alors un titre connu de l'artiste)."""
    while LEADING_WORDS.match(text):
        text = LEADING_WORDS.sub("", text, count=1)
    return text
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
    # « mix » + un style connu, « avec » ou « et » suffit
    return bool(re.search(rf"\b({MIX_WORD})", q) and (find_ambiance(text) or re.search(r"\b(avec|et)\b", q)))


# « mashup », et ce que la reconnaissance vocale en fait souvent
MASHUP_REQUEST = re.compile(r"\bma[st]?c?h[ -]?up+s?\b|\bmash ?ups?\b|\bmatch ?ups?\b|\bmeshup")


def is_mashup_request(text):
    return bool(MASHUP_REQUEST.search(normalize(text)))


def open_mashup_assistant():
    """Ouvre l'assistant « Créer un mashup » de Ma Musique."""
    QProcess.startDetached("python3", [str(HOME / ".local/share/ma-musique/ma_musique.py"), "--mashup"])


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
    songs = [strip_leading_words(part.strip(" «»\"',")) for part in SONG_SEPARATOR.split(match.group(1))]
    return [song for song in songs if song][:2]


# --- Recherche dans la musique déjà téléchargée ---
def words(text):
    return {w for w in re.findall(r"\w+", normalize(text)) if len(w) > 1 and w not in STOPWORDS}


def local_matches(query, limit=MAX_CHOICES):
    """MP3 de ~/Musique qui contiennent (presque) tous les mots de la recherche, du plus proche au moins proche."""
    wanted = words(query)
    if not wanted:
        return []
    scored = []
    for path in mm.MUSIC_DIR.glob("*.mp3"):
        have = words(path.stem)
        score = len(wanted & have) / len(wanted) + len(wanted & have) / max(len(have), 1) / 10
        if score >= 0.75:
            scored.append((score, path))
    return [path for _, path in sorted(scored, reverse=True)[:limit]]


def find_local(query):
    matches = local_matches(query, 1)
    return matches[0] if matches else None


NUMBERS = [r"1|un|une|premier|premiere", r"2|deux|deuxieme|second|seconde", r"3|trois|troisieme"]


def choice_index(text, candidates):
    """Comprend « la première », « deux », « 3 » ou un bout du titre. Renvoie l'indice choisi ou None."""
    q = normalize(text)
    if len(q.split()) <= 4:
        for i, pattern in enumerate(NUMBERS[:len(candidates)]):
            if re.search(rf"\b({pattern})\b", q):
                return i
    said = words(text)
    best, best_score = None, 0.5
    for i, candidate in enumerate(candidates):
        name = words(candidate["name"])
        score = len(said & name) / max(len(name), 1)
        if score > best_score:
            best, best_score = i, score
    return best


# --- Mixxx ---
def mixxx_running():
    return subprocess.run(["pgrep", "-x", "mixxx"], capture_output=True).returncode == 0


def mixxx_configured():
    return MIXXX_CFG.exists()


def device_keys():
    """Noms du port MIDI virtuel vus par Mixxx (« VirMIDI_<carte>-0 »). Le numéro de carte change selon
    l'ordre de démarrage des cartes son : on les déclare tous, Mixxx active celui qu'il trouve."""
    keys = {"VirMIDI_0-0", "VirMIDI_1-0"}
    try:
        for line in Path("/proc/asound/cards").read_text().splitlines():
            if "VirMIDI" in line and line.split()[0].isdigit():
                keys.add(f"VirMIDI_{line.split()[0]}-0")
    except OSError:
        pass
    return sorted(keys)


def configure_mixxx():
    """Active le contrôleur virtuel « Bip » dans la configuration de Mixxx (Mixxx doit être fermé)."""
    lines = MIXXX_CFG.read_text().splitlines()
    wanted = [entry for key in device_keys()
              for entry in (("[Controller]", f"{key} 1"), ("[ControllerPreset]", f"{key} {MAPPING}"))]
    wanted += [
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
    """Trouve jusqu'à 3 candidats par morceau demandé : d'abord sa musique, puis YouTube."""
    resolved = pyqtSignal(list)   # une liste de candidats {"name", "path" ou "video_id", "thumb"} par morceau
    failed = pyqtSignal(str)

    def __init__(self, queries, min_duration=0, parent=None):
        super().__init__(parent)
        self.queries = queries
        self.min_duration = min_duration  # 60 s pour écarter les « shorts » quand l'enfant choisit
        self.items = []
        self.proc = None

    def start(self):
        self.next()

    def next(self):
        if len(self.items) == len(self.queries):
            self.resolved.emit(self.items)
            return
        query = self.queries[len(self.items)]
        self.proc = mm.ytdlp_process(["--flat-playlist", "--dump-json", f"ytsearch8:{query}"])
        self.proc.finished.connect(lambda *_: self.search_done(query))
        self.proc.start()

    def search_done(self, query):
        out = bytes(self.proc.readAllStandardOutput()).decode(errors="replace")
        self.proc.deleteLater()
        candidates = [{"name": path.stem, "path": path, "thumb": None} for path in local_matches(query)]
        seen = {normalize(c["name"]) for c in candidates}
        for line in out.splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            duration = entry.get("duration") or 0
            if not entry.get("id") or not self.min_duration <= duration <= MAX_DURATION:
                continue
            name = " - ".join(mm.make_name(entry))
            if normalize(name) in seen:
                continue
            seen.add(normalize(name))
            candidates.append({"name": name, "video_id": entry["id"],
                               "thumb": f"https://i.ytimg.com/vi/{entry['id']}/mqdefault.jpg"})
        if candidates:
            self.items.append(candidates[:MAX_CHOICES])
            self.next()
            return
        self.failed.emit(f"Je n'ai pas trouvé « {query} » 😕 Essaie avec le nom de l'artiste en plus !")


class SongDownload(QObject):
    """Cherche une chanson (sa musique d'abord, puis YouTube) et la télécharge dans sa musique, comme pour un mix."""
    progress = pyqtSignal(float, str)   # pourcentage, texte
    finished = pyqtSignal(object, str)  # chemin du MP3 (None si raté), message

    def __init__(self, query, parent=None):
        super().__init__(parent)
        self.query = query
        self.item = None
        self.tries = 0
        self.proc = None

    def start(self):
        self.progress.emit(0, "🔎 Je cherche la chanson…")
        self.resolver = Resolver([self.query], min_duration=60, parent=self)
        self.resolver.resolved.connect(self.resolved)
        self.resolver.failed.connect(lambda message: self.finished.emit(None, message))
        self.resolver.start()

    def resolved(self, found):
        self.item = found[0][0]
        if "path" in self.item:  # elle était déjà là
            self.finished.emit(self.item["path"], "Elle est déjà dans ta musique ! 😉")
            return
        self.download()

    def download(self):
        self.tries += 1
        self.proc = mm.download_process(self.item["video_id"])
        self.proc.readyReadStandardOutput.connect(self.download_output)
        self.proc.finished.connect(self.download_done)
        self.proc.start()

    def download_output(self):
        for line in bytes(self.proc.readAllStandardOutput()).decode(errors="replace").splitlines():
            progress = mm.parse_progress(line)
            if progress:
                self.progress.emit(*progress)

    def download_done(self):
        final = mm.finalize_download(self.item["video_id"])
        if final:
            self.finished.emit(final, "C'est dans ta musique ! 🎉")
        elif self.tries < MAX_TRIES:
            self.progress.emit(0, "🔁 Nouvel essai…")
            self.download()
        else:
            self.finished.emit(None, "Le téléchargement n'a pas marché 😕 Réessaie dans un moment.")


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
