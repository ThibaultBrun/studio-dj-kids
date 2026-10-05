#!/usr/bin/env python3
"""Trouve le tempo, les mesures, la tonalité et le refrain d'une chanson.

Usage : analyser.py <chanson> [artiste] [titre]
Lancé par Ma Musique avec le Python du séparateur. Écrit une ligne JSON :
{"version": 2, "bpm": 92.3, "first_beat": 0.347, "first_bar": 2.43, "key": "Em",
 "duration": 245.1, "chorus": 61.2, "chorus_source": "paroles"}
"""
import json
import re
import sys
import unicodedata
import warnings

import numpy as np

warnings.filterwarnings("ignore")
VERSION = 2
LRCLIB = "https://lrclib.net/api"


def fit_grid(times, rough_period):
    """Grille régulière (période, phase) qui tombe le mieux sur ces instants, autour de rough_period.
    beat_this donne les temps à 20 ms près : on essaie des périodes très proches et on garde la meilleure."""
    periods = np.linspace(rough_period * 0.96, rough_period * 1.04, 4001)
    scores = np.abs(np.exp(2j * np.pi * times[None, :] / periods[:, None]).sum(axis=1))
    period = periods[scores.argmax()]
    phase = (np.angle(np.exp(2j * np.pi * times / period).sum()) / (2 * np.pi)) * period
    return period, phase % period


def beats(path):
    """Grille à tempo constant (comme Mixxx) : BPM, premier temps et premier début de mesure (s)."""
    import torch
    from beat_this.inference import File2Beats

    device = "cuda" if torch.cuda.is_available() else "cpu"
    try:
        found, downbeats = File2Beats(device=device, dbn=False)(path)
    except RuntimeError:  # carte graphique pleine : on passe au processeur
        found, downbeats = File2Beats(device="cpu", dbn=False)(path)
    found, downbeats = np.asarray(found), np.asarray(downbeats)
    if len(found) < 8:
        return None, None, None
    period, first = fit_grid(found, float(np.median(np.diff(found))))
    bpm = 60 / period
    while bpm > 150:  # Mixxx compte aussi entre 70 et 150 BPM environ
        bpm /= 2
    while bpm < 70:
        bpm *= 2
    period = 60 / bpm
    if abs(bpm - round(bpm)) < 0.05:
        bpm = round(bpm)
    first %= period
    # Début de mesure : parmi les 4 temps possibles, celui où tombent le plus de « temps 1 » détectés
    first_bar = first
    if len(downbeats) >= 4:
        bar = 4 * period
        candidates = [first + k * period for k in range(4)]
        first_bar = min(candidates, key=lambda c: np.abs(((downbeats - c + bar / 2) % bar) - bar / 2).mean())
    return round(bpm, 3), round(first, 4), round(first_bar % (4 * period), 4)


def key(audio):
    import essentia.standard as es

    tonic, scale, _ = es.KeyExtractor(profileType="bgate")(audio)
    names = {"C#": "Db", "Db": "Db", "D#": "Eb", "Eb": "Eb", "F#": "F#", "Gb": "F#",
             "G#": "Ab", "Ab": "Ab", "A#": "Bb", "Bb": "Bb"}
    tonic = names.get(tonic, tonic)
    return tonic + ("m" if scale == "minor" else "")


# --- Le refrain ---
def simplify(text):
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]+", "", text).strip()


def chorus_from_lyrics(artist, title, duration):
    """Le refrain d'après les paroles synchronisées : le groupe de lignes qui revient le plus."""
    import difflib

    import requests

    try:
        params = {"artist_name": artist, "track_name": title, "duration": round(duration)}
        reply = requests.get(f"{LRCLIB}/get", params=params, timeout=10)
        candidates = [reply.json()] if reply.ok else []
        candidates += requests.get(f"{LRCLIB}/search", params={"q": f"{artist} {title}"}, timeout=10).json()
    except (OSError, ValueError):
        return None
    # La même version que notre fichier (sinon les moments ne correspondent pas)
    candidates = [d for d in candidates if isinstance(d, dict) and d.get("syncedLyrics")
                  and abs((d.get("duration") or 0) - duration) <= 4]
    if not candidates:
        return None
    data = min(candidates, key=lambda d: abs(d["duration"] - duration))
    lines = []
    for line in data["syncedLyrics"].splitlines():
        match = re.match(r"\[(\d+):(\d+(?:\.\d+)?)\]\s*(.*)", line)
        text = simplify(re.sub(r"\(.*?\)", "", match.group(3))) if match else ""
        if text:
            lines.append((int(match.group(1)) * 60 + float(match.group(2)), text))
    # Les lignes « presque pareilles » (« come on (let's celebrate) » / « come on ») comptent ensemble
    groups, ids = [], []
    for _, text in lines:
        same = next((g for g, ref in enumerate(groups)
                     if difflib.SequenceMatcher(None, ref, text).ratio() >= 0.8), None)
        if same is None:
            groups.append(text)
            same = len(groups) - 1
        ids.append(same)
    counts = [ids.count(g) for g in range(len(groups))]
    # Le refrain : la première suite de 2 lignes (presque) répétées 3 fois ou plus (sinon 2 fois)
    for minimum in (3, 2):
        for i in range(len(lines) - 1):
            if lines[i][0] > 8 and counts[ids[i]] >= minimum and counts[ids[i + 1]] >= minimum:
                return lines[i][0]
    return None


def chorus_from_sound(audio_22k, bpm, first_bar, duration):
    """Le refrain d'après le son : le passage de 4 mesures qui revient le plus souvent, parmi les plus forts."""
    import librosa

    hop = 512
    chroma = librosa.feature.chroma_cqt(y=audio_22k, sr=22050, hop_length=hop)
    rms = librosa.feature.rms(y=audio_22k, hop_length=hop)[0]
    frame_time = hop / 22050
    bar = 4 * 60 / bpm
    starts = np.arange(first_bar, duration - 4 * bar, bar)
    if len(starts) < 12:
        return None

    features, energy = [], []
    for t in starts:
        a, b = int(t / frame_time), int((t + 4 * bar) / frame_time)
        # 4 mesures découpées en 16 morceaux : l'ordre des accords compte
        parts = np.array_split(chroma[:, a:b], 16, axis=1)
        vector = np.concatenate([p.mean(axis=1) if p.size else np.zeros(12) for p in parts])
        features.append(vector / (np.linalg.norm(vector) + 1e-9))
        energy.append(rms[a:b].mean() if b > a else 0)
    features, energy = np.array(features), np.array(energy)
    similarity = features @ features.T
    n = len(starts)
    repeats = np.zeros(n)
    for i in range(n):
        others = [similarity[i, j] for j in range(n) if abs(i - j) >= 8]
        repeats[i] = np.sort(others)[-3:].mean() if len(others) >= 3 else 0
    loud = (energy - energy.min()) / (np.ptp(energy) + 1e-9)
    score = 0.6 * (repeats - repeats.min()) / (np.ptp(repeats) + 1e-9) + 0.4 * loud
    score[starts > duration * 0.75] = -1
    best = int(score.argmax())
    # Le premier passage qui ressemble au meilleur : c'est le premier refrain
    for j in range(n):
        if similarity[best, j] >= similarity[best].max() * 0.97 - 0.03 and loud[j] >= loud[best] * 0.8:
            return float(starts[j])
    return float(starts[best])


def snap_to_bar(t, bpm, first_bar):
    bar = 4 * 60 / bpm
    return round(first_bar + round((t - first_bar) / bar) * bar, 3)


def main():
    path = sys.argv[1]
    artist = sys.argv[2] if len(sys.argv) > 2 else ""
    title = sys.argv[3] if len(sys.argv) > 3 else ""
    import essentia.standard as es

    audio = es.MonoLoader(filename=path, sampleRate=44100)()
    duration = len(audio) / 44100
    bpm, first, first_bar = beats(path)
    result = {"version": VERSION, "bpm": bpm, "first_beat": first, "first_bar": first_bar,
              "key": key(audio), "duration": round(duration, 2), "chorus": None, "chorus_source": None}
    if bpm:
        chorus = chorus_from_lyrics(artist, title, duration) if artist and title else None
        source = "paroles"
        if chorus is None:
            audio_22k = es.MonoLoader(filename=path, sampleRate=22050)()
            chorus, source = chorus_from_sound(audio_22k, bpm, first_bar, duration), "son"
        if chorus is not None:
            result["chorus"] = snap_to_bar(chorus, bpm, first_bar)
            result["chorus_source"] = source
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
