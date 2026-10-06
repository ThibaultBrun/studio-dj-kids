#!/usr/bin/env python3
"""Fabrique les notes d'un défi DJ à partir de la vraie musique.

Usage : charter.py <chanson> <analyse JSON> [batterie.mp3]
Lancé avec le Python du séparateur. Écrit une ligne JSON :
{"notes": {"facile": [[temps, colonne], ...], "moyen": [...], "expert": [...]}, "start": 40.1, "end": 101.3}

Les notes tombent sur les vrais coups (grosse caisse à gauche, caisse claire au milieu, charleston à droite),
ramenés sur la grille de tempo de la chanson. Le défi dure environ une minute autour du premier refrain.
"""
import json
import sys
import warnings

import numpy as np

warnings.filterwarnings("ignore")
SR = 22050
HOP = 256
# Où peuvent tomber les notes (position dans la mesure, en doubles croches de 0 à 15) et combien à la fois
LEVELS = {
    "facile": (lambda pos, strong: pos in (0, 8), 1),             # temps 1 et 3
    "moyen": (lambda pos, strong: pos % 4 == 0, 1),               # chaque temps
    "expert": (lambda pos, strong: pos % 2 == 0 or strong, 2),    # croches, doubles croches bien marquées
}
BARS_BEFORE_CHORUS, BARS = 8, 32
# En expert (2 colonnes en même temps), la 2e colonne ne s'allume que si c'est une attaque
# vraiment forte et distincte : évite qu'un seul coup qui "bave" sur 2 bandes (ex. grosse
# caisse qui déborde dans les médiums) ne crée une double-note fantôme.
DOUBLE_LANE_MIN = 0.7


def band_onsets(audio):
    """Force des attaques dans 3 bandes : graves (grosse caisse), médiums (caisse claire), aigus (charleston)."""
    import librosa

    spectrum = np.abs(librosa.stft(audio, n_fft=1024, hop_length=HOP))
    freqs = librosa.fft_frequencies(sr=SR, n_fft=1024)
    bands = [(20, 150), (150, 2500), (5000, 11000)]
    curves = []
    for low, high in bands:
        rows = (freqs >= low) & (freqs < high)
        energy = np.log1p(spectrum[rows].sum(axis=0))
        flux = np.maximum(0, np.diff(energy, prepend=energy[0]))  # ce qui monte d'un coup = une attaque
        curves.append(flux / (np.percentile(flux, 99) + 1e-9))
    return np.array(curves)


def main():
    import librosa

    song, info = sys.argv[1], json.loads(sys.argv[2])
    drums = sys.argv[3] if len(sys.argv) > 3 else None
    period = 60 / info["bpm"]
    bar = 4 * period
    first_bar = info.get("first_bar", info.get("first_beat", 0))
    duration = info.get("duration") or librosa.get_duration(path=song)

    # Environ une minute : 8 mesures avant le premier refrain, puis la suite
    chorus = info.get("chorus") or first_bar + 16 * bar
    start = max(first_bar, chorus - BARS_BEFORE_CHORUS * bar)
    end = min(duration - 1, start + BARS * bar)
    if end - start < 16 * bar:  # chanson courte : on prend ce qu'il y a
        start, end = first_bar, min(duration - 1, first_bar + BARS * bar)

    audio, _ = librosa.load(drums or song, sr=SR, mono=True, offset=max(0, start - 1), duration=end - start + 2)
    curves = band_onsets(audio)
    frame_time = HOP / SR
    t0 = max(0, start - 1)

    def strength(lane, t):
        """Attaque la plus forte de cette bande à ±40 ms de l'instant t."""
        a = int((t - t0 - 0.04) / frame_time)
        b = int((t - t0 + 0.04) / frame_time) + 1
        segment = curves[lane, max(0, a):max(0, b)]
        return float(segment.max()) if segment.size else 0.0

    charts = {}
    sixteenth = period / 4
    for level, (allowed, per_hit) in LEVELS.items():
        notes = []
        t = start
        while t < end:
            pos = round((t - first_bar) / sixteenth) % 16
            scores = [strength(lane, t) for lane in range(3)]
            on_beat = pos % 4 == 0
            if allowed(pos, max(scores) >= 0.85):
                # Sur le temps, on est plus généreux ; entre les temps, il faut une vraie attaque
                threshold = 0.35 if on_beat else 0.6
                ranked = [int(lane) for lane in np.argsort(scores)[::-1]]
                lanes = [ranked[0]] if scores[ranked[0]] >= threshold else []
                # Colonnes supplémentaires (expert) : seuil plus haut pour ne garder que
                # les vrais coups simultanés, pas le débordement d'un même coup sur 2 bandes.
                for lane in ranked[1:per_hit]:
                    if scores[lane] >= max(threshold, DOUBLE_LANE_MIN):
                        lanes.append(lane)
                notes += [[round(t - start, 3), lane] for lane in lanes]
            t += sixteenth
        if level != "expert":  # au moins une note par mesure, même dans les passages calmes
            have = {int(round(n[0] / bar, 6)) for n in notes}
            notes += [[round(k * bar, 3), 1] for k in range(int((end - start) // bar)) if k not in have]
            notes.sort()
        charts[level] = notes

    # Zones avancées (façon DJ Hero), placées par rapport au refrain :
    #   scratch = 2 mesures de build AVANT le refrain ; fader = 1 mesure à l'ENTRÉE du refrain ;
    #   cut = 2 mesures DANS le refrain, avec un « cut » à couper sur chaque temps.
    # Le jeu les ignore si absentes (rétro-compatible).
    span = end - start
    chorus_rel = (chorus or start) - start
    zones = {"scratch": [], "fader": [], "cut": []}
    if 0 <= chorus_rel <= span:
        if chorus_rel - 2 * bar >= 0:
            zones["scratch"].append([round(chorus_rel - 2 * bar, 3), round(2 * bar, 3)])
        if chorus_rel + bar <= span:
            zones["fader"].append([round(chorus_rel, 3), round(bar, 3)])
        c0 = chorus_rel + 2 * bar
        if c0 + 2 * bar <= span:
            beats = [round(c0 + b * (bar / 4), 3) for b in range(8)]
            zones["cut"].append({"start": round(c0, 3), "dur": round(2 * bar, 3), "beats": beats})

    print(json.dumps({"notes": charts, "zones": zones, "start": round(start, 3),
                      "end": round(end, 3), "bpm": info["bpm"]}), flush=True)


if __name__ == "__main__":
    main()
