#!/usr/bin/env python3
"""Bac « Scratch » dans Mixxx : les samples de scratch (Serato & co) et les classiques à scratcher, au même endroit.

Usage (depuis le compte de l'enfant, Mixxx fermé) :
    bac_scratch.py [dossier de samples à importer]
- copie les sons du dossier (sous-dossiers compris) dans ~/Musique/Samples Scratch/ ;
- crée le bac « Scratch » (ou le complète) avec ces samples et les morceaux de ~/Musique dont le nom
  fait penser au scratch (scratch, sample, break, Apache, The Champ…).
Relancer le script ne crée pas de doublons. Une sauvegarde de la base est faite avant d'écrire.
"""
import re
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ma-musique"))
import mixxx_db  # noqa: E402

MUSIC = Path.home() / "Musique"
SAMPLES = MUSIC / "Samples Scratch"
CRATE = "Scratch"
AUDIO_EXT = {".mp3", ".wav", ".aif", ".aiff", ".flac", ".m4a", ".ogg", ".opus"}
SCRATCHY = re.compile(r"scratch|sample|break|fresh|apache|the champ|ahh+|aaa+h|bboy|b-boy|turntabl", re.I)
BACKUP = mixxx_db.DB_FILE.with_name("mixxxdb.sqlite.avant-bac-scratch")


def import_samples(source):
    copied = 0
    for f in sorted(source.rglob("*")):
        if f.suffix.lower() in AUDIO_EXT and f.is_file() and not f.name.startswith("._"):
            target = SAMPLES / f.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(f, target)
                copied += 1
    return copied


def main():
    if mixxx_db.mixxx_running():
        sys.exit("Ferme Mixxx d'abord, puis relance.")
    if len(sys.argv) > 1:
        print(f"{import_samples(Path(sys.argv[1]))} samples copiés dans {SAMPLES}")
    files = [f for f in sorted(SAMPLES.rglob("*")) if f.suffix.lower() in AUDIO_EXT] if SAMPLES.exists() else []
    files += [f for f in sorted(MUSIC.glob("*")) if f.suffix.lower() in AUDIO_EXT and SCRATCHY.search(f.stem)]
    shutil.copy2(mixxx_db.DB_FILE, BACKUP)
    added = 0
    with sqlite3.connect(mixxx_db.DB_FILE, timeout=5) as db:
        db.execute("INSERT OR IGNORE INTO crates (name, show, locked) VALUES (?, 1, 0)", (CRATE,))
        crate_id = db.execute("SELECT id FROM crates WHERE name = ?", (CRATE,)).fetchone()[0]
        for f in files:
            try:
                track_id = mixxx_db._ensure_track(db, f)
            except Exception as e:  # fichier illisible : on passe au suivant
                print(f"ignoré : {f.name} ({e})")
                continue
            added += db.execute("INSERT OR IGNORE INTO crate_tracks (crate_id, track_id) VALUES (?, ?)",
                                (crate_id, track_id)).rowcount
        db.execute("UPDATE crates SET count = (SELECT COUNT(*) FROM crate_tracks WHERE crate_id = ?) WHERE id = ?",
                   (crate_id, crate_id))
        total = db.execute("SELECT count FROM crates WHERE id = ?", (crate_id,)).fetchone()[0]
    print(f"Bac « {CRATE} » : {added} ajoutés, {total} sons en tout")


if __name__ == "__main__":
    main()
