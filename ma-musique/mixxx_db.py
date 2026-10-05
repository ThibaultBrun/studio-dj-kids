"""Lien avec la bibliothèque de Mixxx (~/.mixxx/mixxxdb.sqlite).

Les pistes séparées sont calées au millième sur la chanson d'origine : on leur donne donc la même grille
de tempo et la même tonalité que l'original. Sans ça, Mixxx analyse mal une voix seule (pas de batterie)
et SYNC ne marche pas. On n'écrit dans la base que quand Mixxx est fermé.
"""
import shutil
import sqlite3
import struct
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from mutagen import File as AudioFile
from mutagen import MutagenError

DB_FILE = Path.home() / ".mixxx/mixxxdb.sqlite"
BACKUP = DB_FILE.with_name("mixxxdb.sqlite.avant-ma-musique")
NOTES = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
# Versions des analyseurs de Mixxx 2.5 : avec les mêmes, Mixxx ne refait pas l'analyse
BEATS_VERSION = ("BeatGrid-2.0", "rounding=V4|vamp_plugin_id=qm-tempotracker:0")
KEYS_VERSION = ("KeyMap-1.0", "vamp_plugin_id=qm-keydetector:2")


def mixxx_running():
    return subprocess.run(["pgrep", "-x", "mixxx"], capture_output=True).returncode == 0


# --- Petit encodage protobuf (le format des grilles et des tonalités de Mixxx) ---
def _varint(value):
    out = b""
    while True:
        byte = value & 0x7F
        value >>= 7
        out += bytes([byte | (0x80 if value else 0)])
        if not value:
            return out


def _read_varint(data, i):
    value = shift = 0
    while True:
        byte = data[i]
        i += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return value, i


def _fields(data):
    """Champs de premier niveau d'un message protobuf : {numéro: valeur}."""
    fields, i = {}, 0
    while i < len(data):
        tag, i = _read_varint(data, i)
        number, kind = tag >> 3, tag & 7
        if kind == 0:
            fields[number], i = _read_varint(data, i)
        elif kind == 1:
            fields[number] = data[i:i + 8]
            i += 8
        elif kind == 2:
            size, i = _read_varint(data, i)
            fields[number] = data[i:i + size]
            i += size
        elif kind == 5:
            fields[number] = data[i:i + 4]
            i += 4
        else:
            break
    return fields


def encode_beatgrid(bpm, first_frame):
    bpm_msg = b"\x09" + struct.pack("<d", bpm)
    beat_msg = b"\x08" + _varint(int(first_frame))
    return b"\x0a" + _varint(len(bpm_msg)) + bpm_msg + b"\x12" + _varint(len(beat_msg)) + beat_msg


def decode_beatgrid(blob):
    """(bpm, premier temps en frames) d'une grille Mixxx, ou None."""
    try:
        fields = _fields(blob)
        bpm = struct.unpack("<d", _fields(fields[1])[1])[0]
        first = _fields(fields[2]).get(1, 0) if 2 in fields else 0
        return bpm, first
    except (KeyError, IndexError, struct.error):
        return None


def key_id(text):
    """« Em » -> numéro de tonalité de Mixxx (1-12 majeur, 13-24 mineur), 0 si inconnu."""
    minor = text.endswith("m")
    note = text[:-1] if minor else text
    if note not in NOTES:
        return 0
    return NOTES.index(note) + (13 if minor else 1)


def encode_keymap(text):
    kid = key_id(text)
    change = b"\x08\x00\x10" + _varint(kid)
    return b"\x08" + _varint(kid) + b"\x12" + _varint(len(text)) + text.encode() + b"\x1a" + _varint(len(change)) + change


# --- Lecture ---
def _connect(readonly=True):
    if readonly:
        return sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True, timeout=2)
    return sqlite3.connect(DB_FILE, timeout=5)


def track_analysis(path):
    """Ce que Mixxx sait d'un morceau : {"bpm", "first_beat" (s), "key"} s'il l'a analysé, sinon None."""
    if not DB_FILE.exists():
        return None
    try:
        with _connect() as db:
            row = db.execute("SELECT l.beats, l.samplerate, l.key FROM library l JOIN track_locations t "
                             "ON l.location = t.id WHERE t.location = ?", (str(path),)).fetchone()
    except sqlite3.Error:
        return None
    if not row or not row[0] or not row[1]:
        return None
    grid = decode_beatgrid(row[0])
    if not grid or grid[0] <= 0:
        return None
    return {"bpm": grid[0], "first_beat": grid[1] / row[1], "key": row[2] or ""}


# --- Écriture (Mixxx fermé) ---
def _ensure_track(db, path):
    """Id de la piste dans la bibliothèque de Mixxx, en l'ajoutant si besoin."""
    row = db.execute("SELECT l.id FROM library l JOIN track_locations t ON l.location = t.id "
                     "WHERE t.location = ?", (str(path),)).fetchone()
    if row:
        return row[0]
    audio = AudioFile(path, easy=True)
    tags = audio.tags or {}
    stat = path.stat()
    db.execute("INSERT OR IGNORE INTO track_locations (location, filename, directory, filesize, fs_deleted, "
               "needs_verification) VALUES (?, ?, ?, ?, 0, 0)", (str(path), path.name, str(path.parent), stat.st_size))
    location_id = db.execute("SELECT id FROM track_locations WHERE location = ?", (str(path),)).fetchone()[0]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    cursor = db.execute(
        "INSERT INTO library (artist, title, location, duration, bitrate, samplerate, channels, datetime_added, "
        "mixxx_deleted, played, header_parsed, filetype, source_synchronized_ms) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 1, ?, ?)",
        ((tags.get("artist") or [""])[0], (tags.get("title") or [path.stem])[0], location_id, audio.info.length,
         int(getattr(audio.info, "bitrate", 0) / 1000), audio.info.sample_rate, getattr(audio.info, "channels", 2),
         now, path.suffix.lstrip(".").lower(), int(stat.st_mtime * 1000)))
    return cursor.lastrowid


def _write_analysis(db, track_id, grid, key, keys, keys_version):
    """grid = (bpm, premier temps en secondes) ; la position est convertie à la fréquence de ce fichier."""
    rate = db.execute("SELECT samplerate FROM library WHERE id = ?", (track_id,)).fetchone()[0] or 44100
    bpm, first = grid
    db.execute("UPDATE library SET bpm = ?, beats = ?, beats_version = ?, beats_sub_version = ?, bpm_lock = 1, "
               "key = ?, key_id = ?, keys = ?, keys_version = ?, keys_sub_version = ? WHERE id = ?",
               (bpm, encode_beatgrid(bpm, round(first * rate)), *BEATS_VERSION,
                key, key_id(key), keys, *keys_version, track_id))


def give_grid_to_stems(song, stems, fallback):
    """Copie la grille et la tonalité de la chanson vers ses pistes séparées.

    On prend l'analyse de Mixxx si elle existe ; sinon `fallback` ({"bpm", "first_beat", "key"}, notre analyse),
    qu'on donne aussi à la chanson d'origine pour que tout soit cohérent.
    Renvoie True si c'est fait. Mixxx doit être fermé.
    """
    if mixxx_running() or not DB_FILE.exists():
        return False
    if not BACKUP.exists():
        shutil.copy2(DB_FILE, BACKUP)
    try:
        with _connect(readonly=False) as db:
            row = db.execute("SELECT l.beats, l.samplerate, l.key, l.keys, l.keys_version, l.keys_sub_version "
                             "FROM library l JOIN track_locations t ON l.location = t.id WHERE t.location = ?",
                             (str(song),)).fetchone()
            grid = decode_beatgrid(row[0]) if row and row[0] and row[1] else None
            if grid and grid[0] > 0:
                grid = (grid[0], grid[1] / row[1])
                key = row[2] or (fallback or {}).get("key", "")
                keys, keys_version = (row[3], (row[4], row[5])) if row[3] else (encode_keymap(key), KEYS_VERSION)
            elif fallback and fallback.get("bpm"):
                grid, key = (fallback["bpm"], fallback["first_beat"]), fallback["key"]
                keys, keys_version = encode_keymap(key), KEYS_VERSION
                _write_analysis(db, _ensure_track(db, song), grid, key, keys, keys_version)
            else:
                return False
            for stem in stems:
                _write_analysis(db, _ensure_track(db, stem), grid, key, keys, keys_version)
        return True
    except (sqlite3.Error, MutagenError, OSError):
        return False


def stems_have_grid(song, stems):
    """True si toutes les pistes ont déjà, verrouillée, la même grille que la chanson dans Mixxx."""
    if not DB_FILE.exists():
        return False
    try:
        with _connect() as db:
            rows = {loc: (beats, rate, lock) for loc, beats, rate, lock in db.execute(
                "SELECT t.location, l.beats, l.samplerate, l.bpm_lock FROM library l JOIN track_locations t "
                "ON l.location = t.id WHERE t.location IN (%s)" % ",".join("?" * (len(stems) + 1)),
                [str(song), *map(str, stems)])}
    except sqlite3.Error:
        return False

    def grid(location):
        beats, rate, lock = rows.get(str(location), (None, 0, 0))
        decoded = decode_beatgrid(beats) if beats and rate else None
        return (round(decoded[0], 3), round(decoded[1] / rate, 3), lock) if decoded else None

    reference = grid(song)
    return reference is not None and all(
        (g := grid(s)) is not None and g[:2] == reference[:2] and g[2] == 1 for s in stems)
