#!/usr/bin/env python3
"""Des tubes connus à découvrir, avec leur tempo et leur tonalité.

La liste vient de Deezer (classement et playlists de hits faites par l'équipe de Deezer, sans compte).
Deezer ne donne pas la tonalité : on télécharge l'extrait de 30 s de chaque tube et on l'analyse avec
les mêmes outils que Ma Musique (analyser.py : beat_this pour le tempo, essentia pour la tonalité).
Chaque tube n'est analysé qu'une fois ; la liste est rafraîchie au plus une fois par semaine.
Fiabilité (mesurée le 06/10/2026 sur 17 chansons de la bibliothèque aussi présentes sur Deezer, extrait comparé
à l'analyse du morceau entier) : tempo identique (écart moyen 0,3 %, au pire 1,3 %), tonalité identique 14 fois
sur 17 et compatible sur la roue de Camelot 16 fois sur 17. Durée : environ 2,8 s par tube avec 4 cœurs,
3,9 s avec 2 cœurs (réglage de Bip), téléchargement compris, plus ~8 s de démarrage.

Usage : tubes.py [--cache FICHIER] [--max N] [--refresh]
        tubes.py --test <fichier ou adresse d'un MP3>   (analyse un seul morceau, pour vérifier)
Lancé par Bip avec le Python du séparateur. Écrit des lignes « PROGRESS fait total » pour la barre d'avancement.
"""
import argparse
import json
import os
import sys
import tempfile
import time
import urllib.request
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
HOME = Path.home()
CACHE_FILE = HOME / ".local/share/studio-dj-kids/tubes.json"
API = "https://api.deezer.com"
REFRESH_DAYS = 7
PER_LIST = 25  # les 25 premiers de chaque liste : environ 300 tubes en tout
# Listes vérifiées le 06/10/2026 (playlists éditoriales de Deezer). Les morceaux « explicites » sont écartés.
LISTS = [
    ("chart/0/tracks", "Les hits du moment"),
    ("playlist/1363560485", "Deezer Hits"),
    ("playlist/2134531422", "Hits des kids"),
    ("playlist/687945565", "Hits Dance"),
    ("playlist/867825522", "Années 80"),
    ("playlist/878989033", "Années 90"),
    ("playlist/248297032", "Années 2000"),
    ("playlist/715215865", "Années 2010"),
    ("playlist/13650084141", "Années 2020"),
    ("playlist/791349661", "Années 80 en français"),
    ("playlist/1051470831", "Années 90 en français"),
    ("playlist/713806955", "Années 2000 en français"),
    ("playlist/2015058202", "Disco"),
    ("playlist/3798795702", "Funk"),
]


def get_json(path):
    with urllib.request.urlopen(f"{API}/{path}", timeout=20) as reply:
        data = json.load(reply)
    if "error" in data:
        raise OSError(data["error"])
    return data


def fetch_list():
    """Les tubes de toutes les listes, sans doublon, dans l'ordre des listes."""
    tubes, seen = [], set()
    for path, name in LISTS:
        try:
            data = get_json(f"{path}?limit=100" if path.startswith("chart") else path)
        except (OSError, ValueError) as error:
            print(f"Liste « {name} » indisponible : {error}", file=sys.stderr, flush=True)
            continue
        tracks = data.get("tracks", data).get("data", [])
        kept = 0
        for track in tracks:
            if kept >= PER_LIST:
                break
            if track.get("explicit_lyrics") or not track.get("preview") or not track.get("readable", True):
                continue
            kept += 1
            if track["id"] in seen:
                continue
            seen.add(track["id"])
            tubes.append({
                "id": track["id"],
                "title": track.get("title_short") or track["title"],
                "artist": track["artist"]["name"],
                "duration": track.get("duration", 0),
                "cover": (track.get("album") or {}).get("cover_medium", ""),
                "list": name,
            })
    return tubes


def load_cache(path):
    try:
        cache = json.loads(path.read_text())
        if isinstance(cache, dict) and cache.get("version") == 1:
            return cache
    except (OSError, ValueError):
        pass
    return {"version": 1, "refreshed": 0, "tubes": [], "analyses": {}}


def save_cache(path, cache):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
    os.replace(tmp, path)  # Bip ne lit jamais un fichier à moitié écrit


def keep_beat_model():
    """analyser.beats() recharge le modèle de beat_this à chaque appel : on le garde en mémoire (3x plus rapide)."""
    import beat_this.inference as inference

    original, models = inference.File2Beats, {}

    def cached(**options):
        key = tuple(sorted(options.items()))
        if key not in models:
            models[key] = original(**options)
        return models[key]
    inference.File2Beats = cached


def analyse_file(path):
    """{"bpm", "key"} d'un extrait, avec les fonctions de Ma Musique."""
    import essentia.standard as es

    import analyser
    audio = es.MonoLoader(filename=str(path), sampleRate=44100)()
    bpm, _, _ = analyser.beats(str(path))
    return {"bpm": bpm, "key": analyser.key(audio) if len(audio) > 44100 * 5 else ""}


def analyse_preview(track_id, folder):
    """Télécharge l'extrait de 30 s (l'adresse change souvent : on la redemande à Deezer) et l'analyse."""
    url = get_json(f"track/{track_id}").get("preview")
    if not url:
        return {"bpm": None, "key": "", "error": "pas d'extrait"}
    mp3 = Path(folder) / f"{track_id}.mp3"
    with urllib.request.urlopen(url, timeout=30) as reply:
        mp3.write_bytes(reply.read())
    try:
        return analyse_file(mp3)
    finally:
        mp3.unlink(missing_ok=True)


def setup_imports():
    for folder in (Path(__file__).resolve().parent.parent / "ma-musique", HOME / ".local/share/ma-musique"):
        if (folder / "analyser.py").exists():
            sys.path.insert(0, str(folder))
            break
    keep_beat_model()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, default=Path(os.environ.get("BIP_TUBES", CACHE_FILE)))
    parser.add_argument("--max", type=int, default=0, help="analyser au plus N tubes (tests)")
    parser.add_argument("--refresh", action="store_true", help="redemander la liste à Deezer tout de suite")
    parser.add_argument("--test", help="analyser un seul fichier ou une adresse")
    args = parser.parse_args()
    setup_imports()

    if args.test:
        start = time.time()
        if args.test.startswith("http"):
            with tempfile.TemporaryDirectory() as folder:
                mp3 = Path(folder) / "test.mp3"
                with urllib.request.urlopen(args.test, timeout=30) as reply:
                    mp3.write_bytes(reply.read())
                result = analyse_file(mp3)
        else:
            result = analyse_file(args.test)
        result["seconds"] = round(time.time() - start, 2)
        print(json.dumps(result), flush=True)
        return

    cache = load_cache(args.cache)
    if args.refresh or not cache["tubes"] or time.time() - cache["refreshed"] > REFRESH_DAYS * 86400:
        try:
            tubes = fetch_list()
        except OSError:
            tubes = []
        if tubes:
            cache["tubes"], cache["refreshed"] = tubes, int(time.time())
            save_cache(args.cache, cache)
    analyses = cache["analyses"]
    todo = [t for t in cache["tubes"] if str(t["id"]) not in analyses]
    if args.max:
        todo = todo[:args.max]
    total = len(cache["tubes"])
    done = total - len([t for t in cache["tubes"] if str(t["id"]) not in analyses])
    print(f"PROGRESS {done} {total}", flush=True)
    with tempfile.TemporaryDirectory() as folder:
        for tube in todo:
            start = time.time()
            try:
                result = analyse_preview(tube["id"], folder)
            except OSError as error:  # pas d'Internet : on réessaiera la prochaine fois
                print(f"{tube['artist']} - {tube['title']} : {error}", file=sys.stderr, flush=True)
                continue
            except Exception as error:  # extrait illisible : on ne le réessaie pas
                result = {"bpm": None, "key": "", "error": str(error)[:200]}
            result["seconds"] = round(time.time() - start, 2)
            analyses[str(tube["id"])] = result
            save_cache(args.cache, cache)
            done += 1
            print(f"PROGRESS {done} {total}", flush=True)
            print(f"TUBE {json.dumps({'artist': tube['artist'], 'title': tube['title'], **result}, ensure_ascii=False)}",
                  flush=True)


if __name__ == "__main__":
    main()
