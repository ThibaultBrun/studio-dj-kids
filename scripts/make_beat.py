"""Construit « Mon premier beat » (LMMS) à partir du modèle TR808 : un beat hip-hop à 90 BPM
et une suite d'accords en la mineur (La m - Fa - Do - Sol), prêts à modifier."""
import sys
import xml.etree.ElementTree as ET

TEMPLATE = "/usr/share/lmms/projects/templates/TR808.mpt"
BPM = 90
STEP = 12  # 16 pas par mesure de 192 ticks
BEAT = {  # piste du modèle : (nouveau nom, pas allumés sur 16)
    "Kick.ds": ("Grosse caisse", [0, 7, 10]),
    "Snare.ds": ("Caisse claire", [4, 12]),
    "Hat_c.ds": ("Charleston", [0, 2, 4, 6, 8, 10, 12, 14]),
    "Handclap.ds": ("Clap", [12]),
}

BAR = 192
# Notes LMMS : 57 = la (A4). Un accord par mesure, joué sur la piste synthé du modèle.
CHORDS = [("La mineur", [45, 48, 52]), ("Fa", [41, 45, 48]), ("Do", [48, 52, 55]), ("Sol", [43, 47, 50])]

tree = ET.parse(TEMPLATE)
root = tree.getroot()
root.find("head").set("bpm", str(BPM))
for track in root.iter("track"):
    if track.get("name") in BEAT:
        name, steps = BEAT[track.get("name")]
        track.set("name", name)
        pattern = track.find("pattern")
        pattern.set("name", name)
        for step in steps:
            ET.SubElement(pattern, "note", pan="0", key="57", vol="100", pos=str(step * STEP), len="-192")
    if track.get("name") == "Default":
        track.set("name", "Accords (la mineur)")
        track.find("instrumenttrack").set("vol", "12")  # 3 notes à la fois : plus bas que la batterie
        pattern = ET.SubElement(track, "pattern", type="1", muted="0", steps="16", name="La m - Fa - Do - Sol",
                                pos="0", len=str(len(CHORDS) * BAR))
        for bar, (_, keys) in enumerate(CHORDS):
            for key in keys:
                ET.SubElement(pattern, "note", pan="0", key=str(key), vol="70", pos=str(bar * BAR), len=str(BAR))
    if track.get("type") == "1":
        track.set("name", "Mon beat")
        # Le rythme joue sur 4 mesures dans l'éditeur de morceau
        ET.SubElement(track, "bbtco", usestyle="1", name="", muted="0", pos="0", len=str(4 * 192))

with open(sys.argv[1], "w") as f:
    f.write('<?xml version="1.0"?>\n<!DOCTYPE lmms-project>\n')
    f.write(ET.tostring(root, encoding="unicode"))
