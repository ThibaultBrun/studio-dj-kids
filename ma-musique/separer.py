#!/usr/bin/env python3
"""Sépare une chanson en pistes (voix, batterie, basse…) avec le modèle BS-RoFormer SW.

Usage : separer.py <chanson> <dossier des pistes>
Lancé par Ma Musique avec le Python du séparateur (~/.local/share/separateur/venv).
Écrit sur la sortie standard des lignes « ETAPE <nom> » et « PROGRESS <fait> <total> ».
"""
import json
import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import audio_separator.separator.architectures.mdxc_separator as mdxc
from audio_separator.separator import Separator

MODEL = "BS-Roformer-SW.ckpt"
MODEL_DIR = Path.home() / ".local/share/separateur/modeles"
# Piste du modèle -> nom affiché, dans l'ordre où on les range
STEMS = {"vocals": "Voix", "drums": "Batterie", "bass": "Basse",
         "guitar": "Guitare", "piano": "Piano", "other": "Autres"}
INSTRU = "Sans voix"


def say(*words):
    print(*words, flush=True)


def counting(iterable, *args, **kwargs):
    """Remplace la barre tqdm du modèle pour que Ma Musique suive l'avancement."""
    items = list(iterable)
    for i, item in enumerate(items):
        say("PROGRESS", i, len(items))
        yield item
    say("PROGRESS", len(items), len(items))


def probe(song):
    """(tags, fréquence d'échantillonnage) de la chanson."""
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                          "-select_streams", "a:0", str(song)], capture_output=True, text=True).stdout
    try:
        info = json.loads(out)
        tags = {k.lower(): v for k, v in info["format"].get("tags", {}).items()}
        return tags, int(info["streams"][0]["sample_rate"])
    except (ValueError, KeyError, IndexError):
        return {}, 44100


def to_mp3(wavs, song, title, rate, dest):
    """Encode une ou plusieurs pistes (mélangées) en MP3, avec les tags et la pochette de la chanson.

    Même fréquence que l'original : la grille de tempo de Mixxx (en échantillons) reste alors identique.
    """
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for wav in wavs:
        cmd += ["-i", str(wav)]
    cmd += ["-i", str(song)]
    n = len(wavs)
    if n > 1:
        cmd += ["-filter_complex", "".join(f"[{i}:a]" for i in range(n)) + f"amix=inputs={n}:normalize=0[a]",
                "-map", "[a]"]
    else:
        cmd += ["-map", "0:a"]
    cmd += ["-map", f"{n}:v?", "-c:v", "copy", "-map_metadata", str(n),
            "-c:a", "libmp3lame", "-b:a", "320k", "-ar", str(rate), "-id3v2_version", "3",
            "-metadata", f"title={title}", str(dest)]
    subprocess.run(cmd, check=True)


def main():
    song, dest = Path(sys.argv[1]), Path(sys.argv[2])
    tags, rate = probe(song)
    title = tags.get("title") or song.stem
    mdxc.tqdm = counting

    with tempfile.TemporaryDirectory(prefix=".separation-", dir=dest.parent) as tmp:
        tmp = Path(tmp)
        say("ETAPE modele")
        separator = Separator(log_level=logging.WARNING, model_file_dir=str(MODEL_DIR), output_dir=str(tmp))
        separator.load_model(MODEL)
        say("ETAPE separation")
        # Nom d'entrée simple : le modèle n'aime pas toujours les caractères spéciaux
        source = tmp / f"chanson{song.suffix.lower()}"
        shutil.copy(song, source)
        files = [tmp / f for f in separator.separate(str(source))]
        wavs = {}
        for f in files:
            stem = f.name.split("_(")[1].split(")")[0].lower()
            wavs[stem] = f

        say("ETAPE mp3")
        out = tmp / "pistes"
        out.mkdir()
        name = dest.name
        jobs = [([wavs[s]], STEMS[s]) for s in STEMS if s in wavs]
        jobs.insert(1, ([w for s, w in wavs.items() if s != "vocals"], INSTRU))
        for i, (sources, label) in enumerate(jobs):
            say("PROGRESS", i, len(jobs))
            to_mp3(sources, song, f"{title} ({label})", rate, out / f"{name} ({label}).mp3")
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(out, dest)
    say("FINI")


if __name__ == "__main__":
    main()
