#!/usr/bin/env python3
"""Fabrique des fichiers « stems » (format Native Instruments .stem.mp4) pour Mixxx 2.6 et plus,
à partir des pistes déjà séparées par Ma Musique : pas besoin de refaire la séparation.

Un fichier stems contient la chanson complète + 4 pistes : batterie, basse, le reste, voix.
Dans Mixxx, chaque piste se coupe ou se remet d'un bouton (voix / musique sur la platine).

Usage : stems_mixxx.py [chanson ...]   (sans argument : toutes les chansons séparées)
Les fichiers vont dans ~/Musique/Stems/ ; ceux qui existent déjà ne sont pas refaits.
"""
import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

MUSIC = Path.home() / "Musique"
STEMS_DIR = MUSIC / "Pistes séparées"
OUT_DIR = MUSIC / "Stems"
AUDIO_EXT = {".mp3", ".m4a", ".flac", ".wav", ".ogg", ".opus"}
# Ordre imposé par le format : batterie, basse, autres, voix
LAYOUT = [("Batterie", ["Batterie"], "#E53935"), ("Basse", ["Basse"], "#FB8C00"),
          ("Musique", ["Autres", "Guitare", "Piano"], "#43A047"), ("Voix", ["Voix"], "#1E88E5")]
MANIFEST = {
    "version": 1,
    "stems": [{"name": name, "color": color} for name, _, color in LAYOUT],
    "mastering_dsp": {
        "compressor": {"enabled": False, "ratio": 3, "output_gain": 0.5, "release": 0.3, "attack": 0.003,
                       "input_gain": 0.5, "threshold": 0, "hp_cutoff": 300, "dry_wet": 50},
        "limiter": {"enabled": False, "release": 0.05, "threshold": 0, "ceiling": -0.35},
    },
}


def original_of(folder):
    for ext in AUDIO_EXT:
        f = MUSIC / f"{folder.name}{ext}"
        if f.exists():
            return f
    return None


def title_of(original, folder):
    try:
        from mutagen import File as AudioFile
        tags = AudioFile(original, easy=True).tags or {}
        return (tags.get("title") or [folder.name])[0]
    except Exception:
        return folder.name


def stem_file(folder, part):
    for ext in AUDIO_EXT:
        f = folder / f"{folder.name} ({part}){ext}"
        if f.exists():
            return f
    return None


def box(kind, payload):
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def add_manifest(path):
    """Ajoute moov/udta/stem (le JSON qui dit « ceci est un fichier stems »). Le moov est en fin de fichier
    (pas de faststart), donc l'agrandir ne décale pas les données audio."""
    data = bytearray(path.read_bytes())
    stem = box(b"stem", json.dumps(MANIFEST).encode())
    i = 0
    while i < len(data):
        size, kind = struct.unpack(">I4s", data[i:i + 8])
        if kind == b"moov":
            if i + size != len(data):
                raise RuntimeError("moov pas en fin de fichier")
            j, end = i + 8, i + size
            while j < end:
                sub_size, sub_kind = struct.unpack(">I4s", data[j:j + 8])
                if sub_kind == b"udta":
                    data[j + sub_size:j + sub_size] = stem
                    struct.pack_into(">I", data, j, sub_size + len(stem))
                    break
                j += sub_size
            else:
                data += box(b"udta", stem)
                stem = box(b"udta", stem)
            struct.pack_into(">I", data, i, size + len(stem))
            path.write_bytes(bytes(data))
            return
        i += size
    raise RuntimeError("pas de moov")


def build(folder):
    original = original_of(folder)
    out = OUT_DIR / f"{folder.name} (Stems).stem.mp4"  # un autre nom que la chanson : on les distingue dans Mixxx
    if out.exists():
        return "déjà fait"
    if not original:
        return "chanson d'origine introuvable"
    parts = {part: stem_file(folder, part) for _, sources, _ in LAYOUT for part in sources}
    if not parts["Voix"] or not parts["Batterie"] or not parts["Basse"]:
        return "pistes manquantes"
    inputs, filters, maps = ["-i", str(original)], [], ["-map", "0:a"]
    n = 1
    for k, (_, sources, _) in enumerate(LAYOUT):
        files = [parts[s] for s in sources if parts.get(s)]
        first = n
        for f in files:
            inputs += ["-i", str(f)]
            n += 1
        if len(files) == 1:
            maps += ["-map", f"{first}:a"]
        else:
            labels = "".join(f"[{first + m}:a]" for m in range(len(files)))
            filters.append(f"{labels}amix=inputs={len(files)}:normalize=0[s{k}]")
            maps += ["-map", f"[s{k}]"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=OUT_DIR, prefix=".en-cours-") as tmp:  # même disque que la musique
        tmp_out = Path(tmp) / "stems.mp4"
        cmd = ["ffmpeg", "-v", "error", "-y", *inputs]
        if filters:
            cmd += ["-filter_complex", ";".join(filters)]
        cmd += [*maps, "-map_metadata", "0", "-metadata", f"title={title_of(original, folder)} (Stems)", "-c:a", "aac", "-b:a", "256k", "-ar", "44100", "-ac", "2",
                *[x for k in range(1, 5) for x in (f"-disposition:a:{k}", "0")], "-f", "mp4", str(tmp_out)]
        subprocess.run(cmd, check=True)
        add_manifest(tmp_out)
        tmp_out.replace(out)
    return "ok"


def main():
    names = sys.argv[1:]
    folders = [STEMS_DIR / n for n in names] if names else sorted(p for p in STEMS_DIR.iterdir() if p.is_dir())
    for folder in folders:
        try:
            result = build(folder)
        except (subprocess.CalledProcessError, RuntimeError, OSError) as e:
            result = f"erreur ({e})"
        print(f"{folder.name} : {result}", flush=True)


if __name__ == "__main__":
    main()
