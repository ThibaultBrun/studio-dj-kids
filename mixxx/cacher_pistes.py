#!/usr/bin/env python3
"""Dans Mixxx, cache les pistes séparées (Voix, Sans voix, Batterie…) des chansons qui ont une version « (Stems) » :
la version stems les contient toutes (boutons 🎤 / ♪ de la platine). La chanson normale reste visible.
Les pistes cachées restent sur le disque et se chargent toujours par glisser-déposer ou par Ma Musique.
À lancer Mixxx fermé (le lanceur mixxx-kids le fait avant chaque démarrage)."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ma-musique"))
import mixxx_db  # noqa: E402

MUSIC = Path.home() / "Musique"
PIECES = MUSIC / "Pistes séparées"
STEMS = MUSIC / "Stems"


def main():
    if not mixxx_db.DB_FILE.exists() or mixxx_db.mixxx_running() or not STEMS.exists():
        return
    names = {f.name[:-len(" (Stems).stem.mp4")] for f in STEMS.glob("* (Stems).stem.mp4")}
    folders = [str(PIECES / n) for n in names if (PIECES / n).is_dir()]
    if not folders:
        return
    with sqlite3.connect(mixxx_db.DB_FILE, timeout=5) as db:
        hidden = 0
        for folder in folders:
            hidden += db.execute(
                "UPDATE library SET mixxx_deleted = 1 WHERE mixxx_deleted = 0 AND location IN "
                "(SELECT id FROM track_locations WHERE directory = ?)", (folder,)).rowcount
    if hidden:
        print(f"{hidden} pistes séparées cachées dans Mixxx (leur version Stems existe)")


if __name__ == "__main__":
    main()
