#!/usr/bin/env python3
"""Trouve le tempo, le premier temps et la tonalité d'une chanson.

Usage : analyser.py <chanson>
Lancé par Ma Musique avec le Python du séparateur. Écrit une ligne JSON :
{"bpm": 92.3, "first_beat": 0.347, "key": "Em"}
"""
import json
import sys
import warnings

import numpy as np

warnings.filterwarnings("ignore")


def beats(path):
    """Grille à tempo constant (comme Mixxx) : BPM et position du premier temps, en secondes."""
    import torch
    from beat_this.inference import File2Beats

    device = "cuda" if torch.cuda.is_available() else "cpu"
    try:
        found, _ = File2Beats(device=device, dbn=False)(path)
    except RuntimeError:  # carte graphique pleine : on passe au processeur
        found, _ = File2Beats(device="cpu", dbn=False)(path)
    found = np.asarray(found)
    if len(found) < 8:
        return None, None
    # beat_this donne les temps à 20 ms près : on cherche la grille régulière (temps n = phase + n × période)
    # qui tombe le mieux sur tous les temps de la chanson, en essayant des périodes très proches.
    rough = float(np.median(np.diff(found)))
    periods = np.linspace(rough * 0.96, rough * 1.04, 4001)
    angles = 2j * np.pi * found[None, :] / periods[:, None]
    scores = np.abs(np.exp(angles).sum(axis=1))
    period = periods[scores.argmax()]
    phase = (np.angle(np.exp(2j * np.pi * found / period).sum()) / (2 * np.pi)) * period
    bpm = 60 / period
    while bpm > 150:  # Mixxx compte aussi entre 70 et 150 BPM environ
        bpm /= 2
    while bpm < 70:
        bpm *= 2
    period = 60 / bpm
    if abs(bpm - round(bpm)) < 0.05:
        bpm = round(bpm)
    first = phase % period
    return round(bpm, 3), round(first, 4)


def key(path):
    import essentia.standard as es

    audio = es.MonoLoader(filename=path, sampleRate=44100)()
    tonic, scale, _ = es.KeyExtractor(profileType="bgate")(audio)
    names = {"C#": "Db", "Db": "Db", "D#": "Eb", "Eb": "Eb", "F#": "F#", "Gb": "F#",
             "G#": "Ab", "Ab": "Ab", "A#": "Bb", "Bb": "Bb"}
    tonic = names.get(tonic, tonic)
    return tonic + ("m" if scale == "minor" else "")


def main():
    path = sys.argv[1]
    bpm, first = beats(path)
    print(json.dumps({"bpm": bpm, "first_beat": first, "key": key(path)}), flush=True)


if __name__ == "__main__":
    main()
