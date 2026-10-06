#!/usr/bin/env python3
"""Génère le son de scratch des zones (synthétisés = libres de droits) dans dj-defi/sounds/.
Lancer une fois : python3 make_sounds.py
"""
import math
import struct
import wave
from pathlib import Path

SR = 44100
OUT = Path(__file__).resolve().parent / "sounds"
OUT.mkdir(exist_ok=True)


def save(name, samples):
    peak = max(1e-6, max(abs(s) for s in samples))
    data = b"".join(struct.pack("<h", int(max(-1, min(1, s / peak)) * 32000)) for s in samples)
    with wave.open(str(OUT / name), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data)
    print("écrit", name, f"({len(samples)/SR:.2f}s)")


def env(i, n, attack=0.005, release=0.25):
    t = i / SR
    total = n / SR
    a = min(1.0, t / attack)
    r = min(1.0, (total - t) / release)
    return max(0.0, a) * max(0.0, r)


def scratch(dur=0.45):
    import random
    random.seed(1)
    n = int(SR * dur)
    out = []
    prev = 0.0
    for i in range(n):
        t = i / SR
        # bruit filtré (passe-bas qui bouge) + balayage de hauteur = « vzzz » de scratch
        cutoff = 0.05 + 0.4 * abs(math.sin(2 * math.pi * 3 * t))
        prev = prev + cutoff * (random.uniform(-1, 1) - prev)
        out.append(prev * env(i, n, 0.005, 0.1))
    return out


if __name__ == "__main__":
    save("scratch.wav", scratch())
    print("OK ->", OUT)
