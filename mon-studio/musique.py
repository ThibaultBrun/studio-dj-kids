"""Mon Studio : la musique (gammes, accords, motifs, modèles prêts) et la conversion d'un morceau en notes."""
import copy
import json

STEPS_PER_BAR = 16
BEATS_PER_BAR = 4
SONG_BARS = 16

NOTE_NAMES = ["Do", "Do#", "Ré", "Ré#", "Mi", "Fa", "Fa#", "Sol", "Sol#", "La", "La#", "Si"]
NOTE_NAMES_FLAT = ["Do", "Ré♭", "Ré", "Mi♭", "Mi", "Fa", "Sol♭", "Sol", "La♭", "La", "Si♭", "Si"]
FLAT_KEYS = {(5, "majeur"), (2, "mineur")}  # Fa majeur et ré mineur s'écrivent avec des bémols
SCALES = {"majeur": [0, 2, 4, 5, 7, 9, 11], "mineur": [0, 2, 3, 5, 7, 8, 10]}
# Gammes proposées : (nom affiché, note de départ 0-11, mode)
KEYS = [("La mineur", 9, "mineur"), ("Mi mineur", 4, "mineur"), ("Ré mineur", 2, "mineur"), ("Si mineur", 11, "mineur"),
        ("Do majeur", 0, "majeur"), ("Sol majeur", 7, "majeur"), ("Ré majeur", 2, "majeur"), ("Fa majeur", 5, "majeur")]

LANES = ["batterie", "basse", "accords", "melodie"]
LANE_INFO = {  # titre, canal MIDI, couleur
    "batterie": ("🥁 Batterie", 9, "#ff8a3d"),
    "basse": ("🎸 Basse", 0, "#9c5cf2"),
    "accords": ("🎹 Accords", 1, "#3d8bff"),
    "melodie": ("🎵 Mélodie", 2, "#2fbf71"),
}
# Instruments : (nom affiché, banque, programme General MIDI)
INSTRUMENTS = {
    "batterie": [("Batterie standard", 128, 0), ("Batterie rock", 128, 16), ("Batterie électro", 128, 24),
                 ("Boîte à rythmes 808", 128, 25), ("Batterie jazz", 128, 32)],
    "basse": [("Basse électrique", 0, 33), ("Basse slap", 0, 36), ("Basse synthé", 0, 38), ("Basse synthé 2", 0, 39),
              ("Contrebasse", 0, 32)],
    "accords": [("Piano", 0, 0), ("Piano électrique", 0, 4), ("Nappe douce", 0, 89), ("Cordes", 0, 48),
                ("Orgue", 0, 16), ("Guitare folk", 0, 25)],
    "melodie": [("Synthé carré", 0, 80), ("Synthé scie", 0, 81), ("Piano", 0, 0), ("Vibraphone", 0, 11),
                ("Flûte", 0, 73), ("Boîte à musique", 0, 10), ("Trompette", 0, 56), ("Sifflet", 0, 78)],
}

# Batterie, de la ligne du bas (0) à celle du haut : (nom, note General MIDI, force)
DRUM_ROWS = [("Grosse caisse", 36, 115), ("Caisse claire", 38, 105), ("Clap", 39, 100), ("Charleston", 42, 75),
             ("Charleston ouvert", 46, 85), ("Tom grave", 45, 100), ("Tom aigu", 50, 100), ("Cymbale", 49, 100)]
BASS_ROWS = 8      # notes de la gamme au-dessus de la base de l'accord : 1 (base) … 8 (octave)
MELODY_ROWS = 15   # deux octaves de la gamme
CHORD_STYLES = {"tenu": "Tenu", "rythme": "Rythmé", "arpege": "Arpège"}
LENGTHS = [1, 2, 4]  # longueur d'un motif en mesures


# --- Théorie ---
def scale_note(key, degree, base):
    """Note MIDI du degré (0 = tonique, peut dépasser 6) de la gamme, à partir de la note MIDI base."""
    _, root, mode = key
    scale = SCALES[mode]
    return base + root + 12 * (degree // 7) + scale[degree % 7]


def note_name(key, midi_note):
    names = NOTE_NAMES_FLAT if (key[1], key[2]) in FLAT_KEYS else NOTE_NAMES
    return names[midi_note % 12]


def chord_name(key, degree):
    notes = [scale_note(key, degree + i, 0) for i in (0, 2, 4)]
    third, fifth = (notes[1] - notes[0]) % 12, (notes[2] - notes[0]) % 12
    quality = " dim" if fifth == 6 else (" m" if third == 3 else "")
    return note_name(key, notes[0]) + quality


def chord_voicing(key, degree):
    """Accord de 3 notes, ramené autour du do du milieu pour qu'il ne monte ni ne descende trop."""
    notes = []
    for i in (0, 2, 4):
        note = scale_note(key, degree + i, 48)
        while note < 55:
            note += 12
        while note > 72:
            note -= 12
        notes.append(note)
    return sorted(notes)


# --- Motifs ---
def new_pattern(lane, name, bars=1):
    pattern = {"name": name, "bars": bars}
    if lane == "accords":
        pattern.update(chords=[None] * (BEATS_PER_BAR * bars), style="tenu")
    else:
        pattern["cells"] = []  # [ligne, pas] allumés
    return pattern


def cells_from(rows):
    """{ligne: [pas…]} -> liste de cases."""
    return [[row, step] for row, steps in rows.items() for step in steps]


def drum(name, bars=1, **rows):
    index = {"grosse": 0, "caisse": 1, "clap": 2, "charleston": 3, "ouvert": 4, "tom_grave": 5, "tom_aigu": 6, "cymbale": 7}
    pattern = new_pattern("batterie", name, bars)
    pattern["cells"] = cells_from({index[k]: v for k, v in rows.items()})
    return pattern


def tone(lane, name, notes, bars=1):
    """notes : [(ligne, premier pas, longueur)] ; les cases consécutives d'une ligne font une note tenue."""
    pattern = new_pattern(lane, name, bars)
    pattern["cells"] = [[row, step] for row, start, length in notes for step in range(start, start + length)]
    return pattern


def progression(name, degrees, style="tenu"):
    """Un accord par mesure, sur autant de mesures que d'accords."""
    pattern = new_pattern("accords", name, len(degrees))
    pattern["chords"] = [d for d in degrees for _ in range(BEATS_PER_BAR)]
    pattern["style"] = style
    return pattern


EVERY_2 = list(range(0, 16, 2))
ALL_16 = list(range(16))
TEMPLATES = {
    "batterie": [
        drum("Boom bap", grosse=[0, 7, 10], caisse=[4, 12], charleston=EVERY_2),
        drum("Boom bap + break", grosse=[0, 7, 10], caisse=[4, 12, 14, 15], charleston=EVERY_2[:6], ouvert=[12]),
        drum("Pop rock", grosse=[0, 8, 10], caisse=[4, 12], charleston=EVERY_2),
        drum("Électro 4/4", grosse=[0, 4, 8, 12], clap=[4, 12], ouvert=[2, 6, 10, 14], charleston=[1, 3, 5, 7, 9, 11, 13, 15]),
        drum("Funk", grosse=[0, 3, 8, 10], caisse=[4, 12], charleston=ALL_16),
        drum("Reggaeton", grosse=[0, 4, 8, 12], caisse=[3, 6, 11, 14], charleston=EVERY_2),
        drum("Trap", grosse=[0, 6, 10], clap=[8], charleston=ALL_16),
        drum("Roulement", grosse=[0], caisse=[8, 9, 10, 11], tom_aigu=[12, 13], tom_grave=[14, 15]),
        drum("Coup de cymbale", grosse=[0], cymbale=[0], charleston=EVERY_2),
    ],
    "basse": [
        tone("basse", "Note tenue", [(0, 0, 16)]),
        tone("basse", "Noires", [(0, s, 2) for s in (0, 4, 8, 12)]),
        tone("basse", "Octaves disco", [(0, 0, 1), (7, 2, 1), (0, 4, 1), (7, 6, 1), (0, 8, 1), (7, 10, 1), (0, 12, 1), (7, 14, 1)]),
        tone("basse", "Funk", [(0, 0, 2), (7, 3, 1), (0, 6, 1), (4, 8, 2), (0, 10, 1), (7, 11, 1), (5, 12, 1), (4, 14, 2)]),
        tone("basse", "Reggaeton", [(0, 0, 2), (0, 3, 2), (4, 6, 2), (0, 8, 2), (0, 11, 2), (4, 14, 2)]),
        tone("basse", "Promenade", [(0, 0, 3), (2, 4, 3), (4, 8, 3), (5, 12, 3)]),
        tone("basse", "Électro", [(0, s, 1) for s in (2, 6, 10, 14)]),
    ],
    "accords": [
        progression("Épique (1-6-3-7)", [0, 5, 2, 6]),
        progression("Pop (1-5-6-4)", [0, 4, 5, 3]),
        progression("Émotion (6-4-1-5)", [5, 3, 0, 4]),
        progression("Ballade (1-4-5-1)", [0, 3, 4, 0]),
        progression("Mystère (1-4)", [0, 3, 0, 3]),
        progression("Jazz (2-5-1)", [1, 4, 0, 0]),
    ],
    "melodie": [
        tone("melodie", "Petite boucle", [(0, 0, 2), (2, 2, 2), (4, 4, 2), (2, 6, 2), (5, 8, 2), (4, 10, 2), (2, 12, 2), (1, 14, 2)]),
        tone("melodie", "Refrain", [(4, 0, 4), (2, 4, 2), (0, 6, 2), (1, 8, 6), (4, 16, 4), (5, 20, 2), (4, 22, 2), (2, 24, 8)], bars=2),
        tone("melodie", "Montée", [(r, s, 2) for r, s in zip((0, 1, 2, 3, 4, 5, 6, 7), range(0, 16, 2))]),
        tone("melodie", "Notes longues", [(4, 0, 8), (5, 8, 8), (2, 16, 8), (4, 24, 8)], bars=2),
        tone("melodie", "Écho", [(7, 0, 1), (7, 3, 1), (4, 6, 2), (9, 8, 1), (9, 11, 1), (7, 14, 2)]),
    ],
}


def template(lane, name):
    return copy.deepcopy(next(t for t in TEMPLATES[lane] if t["name"] == name))


# --- Morceau ---
def new_project():
    return {
        "name": "Mon morceau", "tempo": 95, "key": 0,
        "lanes": {lane: {"instrument": 0, "volume": 100, "muted": False, "patterns": [], "song": [None] * SONG_BARS}
                  for lane in LANES},
    }


def place(project, lane, pattern_index, bar):
    """Place un motif dans la ligne de temps à partir de la mesure bar (il occupe sa longueur)."""
    lane_data = project["lanes"][lane]
    bars = lane_data["patterns"][pattern_index]["bars"]
    for i in range(bars):
        if bar + i < SONG_BARS:
            lane_data["song"][bar + i] = [pattern_index, i]


def song_style(name, tempo, key, instruments, patterns, arrangement, chord_style="tenu"):
    """patterns : {ligne: [noms de modèles]} ; arrangement : {ligne: chaîne de 16 caractères, un par mesure :
    'A', 'B'… = début du motif, '.' = rien, '-' = suite du motif précédent}."""
    project = new_project()
    project.update(name=name, tempo=tempo, key=key)
    for lane in LANES:
        lane_data = project["lanes"][lane]
        lane_data["instrument"] = instruments[lane]
        lane_data["patterns"] = [template(lane, n) for n in patterns.get(lane, [])]
        if lane == "accords":
            for p in lane_data["patterns"]:
                p["style"] = chord_style
        for letter, p in zip("ABCDEFGH", lane_data["patterns"]):
            p["name"] = f"{letter} · {p['name']}"
        for bar, char in enumerate(arrangement.get(lane, "")):
            if char in "ABCDEFGH":
                place(project, lane, "ABCDEFGH".index(char), bar)
    return project


STYLES = {
    "Hip-hop chill": lambda: song_style(
        "Hip-hop chill", 86, 0, {"batterie": 0, "basse": 0, "accords": 1, "melodie": 3},
        {"batterie": ["Boom bap", "Boom bap + break"], "basse": ["Note tenue", "Funk"],
         "accords": ["Épique (1-6-3-7)"], "melodie": ["Petite boucle"]},
        {"accords": "A---A---A---A---", "batterie": "....AAABAAABAAAB", "basse": "....A---B---B---",
         "melodie": "........AAAAAAAA"}),
    "Pop joyeuse": lambda: song_style(
        "Pop joyeuse", 104, 4, {"batterie": 1, "basse": 0, "accords": 0, "melodie": 3},
        {"batterie": ["Pop rock", "Roulement", "Coup de cymbale"], "basse": ["Noires", "Octaves disco"],
         "accords": ["Pop (1-5-6-4)"], "melodie": ["Refrain"]},
        {"accords": "A---A---A---A---", "batterie": "AAABCAABCAABCAAB", "basse": "A-------B---B---",
         "melodie": "....A-A-A-A-A-A-"}),
    "Électro": lambda: song_style(
        "Électro", 124, 0, {"batterie": 2, "basse": 2, "accords": 2, "melodie": 1},
        {"batterie": ["Électro 4/4", "Roulement"], "basse": ["Électro", "Octaves disco"],
         "accords": ["Épique (1-6-3-7)"], "melodie": ["Écho", "Montée"]},
        {"accords": "A---A---A---A---", "batterie": "AAAAAAABAAAAAAAB", "basse": "....AAAABBBBBBBB",
         "melodie": "....AAAAAAABAAAB"}, chord_style="arpege"),
    "Reggaeton": lambda: song_style(
        "Reggaeton", 94, 2, {"batterie": 2, "basse": 2, "accords": 1, "melodie": 0},
        {"batterie": ["Reggaeton", "Roulement"], "basse": ["Reggaeton"],
         "accords": ["Épique (1-6-3-7)"], "melodie": ["Notes longues"]},
        {"accords": "A---A---A---A---", "batterie": "AAAAAAABAAAAAAAB", "basse": "A---------------",
         "melodie": "........A-A-A-A-"}),
}
# --- Conversion en notes ---
def chord_at(project, bar, beat):
    """Degré de l'accord qui joue à ce moment du morceau (0 = tonique si aucun accord)."""
    lane = project["lanes"]["accords"]
    cell = lane["song"][bar]
    if not cell or lane["muted"]:
        return 0
    pattern = lane["patterns"][cell[0]]
    degree = pattern["chords"][cell[1] * BEATS_PER_BAR + beat]
    return 0 if degree is None else degree


def runs(cells, total_steps):
    """Cases allumées -> notes (ligne, début, longueur) : les cases qui se suivent sur une ligne sont liées."""
    lit = {(r, s) for r, s in cells if s < total_steps}
    notes = []
    for row, step in sorted(lit):
        if (row, step - 1) in lit:
            continue
        length = 1
        while (row, step + length) in lit:
            length += 1
        notes.append((row, step, length))
    return notes


def pattern_notes(project, lane, pattern, chord_for_step):
    """Notes d'un motif : [(pas relatif, note MIDI, force, durée en pas)]. chord_for_step(pas) -> degré d'accord."""
    key = KEYS[project["key"]]
    total = pattern["bars"] * STEPS_PER_BAR
    notes = []
    if lane == "batterie":
        # chaque case de batterie est un coup séparé, même si elles se suivent
        lit = {(r, s) for r, s in pattern["cells"] if s < total}
        notes = [(s, DRUM_ROWS[r][1], DRUM_ROWS[r][2], 1) for r, s in sorted(lit, key=lambda c: c[1])]
    elif lane == "basse":
        for row, step, length in runs(pattern["cells"], total):
            degree = chord_for_step(step) + row
            notes.append((step, scale_note(key, degree, 36), 105, length))
    elif lane == "melodie":
        for row, step, length in runs(pattern["cells"], total):
            notes.append((step, scale_note(key, row, 60 if key[1] < 5 else 48), 100, length))
    elif lane == "accords":
        chords = pattern["chords"]
        beat = 0
        while beat < len(chords):
            degree, length = chords[beat], 1
            while beat + length < len(chords) and chords[beat + length] == degree and (beat + length) % BEATS_PER_BAR:
                length += 1
            if degree is not None:
                voicing = chord_voicing(key, degree)
                start, steps = beat * 4, length * 4
                if pattern["style"] == "tenu":
                    notes += [(start, n, 80, steps) for n in voicing]
                elif pattern["style"] == "rythme":
                    notes += [(s, n, 85 if s % 4 == 0 else 70, 1) for s in range(start, start + steps, 2) for n in voicing]
                else:  # arpège
                    order = voicing + [voicing[0] + 12, voicing[2], voicing[1]]
                    notes += [(s, order[(s - start) % len(order)], 80, 1) for s in range(start, start + steps)]
            beat += length
    return notes


def compile_song(project):
    """Toutes les notes du morceau : {pas absolu: [(canal, note, force, durée en pas)]}."""
    events = {}
    for lane in LANES:
        lane_data = project["lanes"][lane]
        if lane_data["muted"]:
            continue
        channel = LANE_INFO[lane][1]
        bar = 0
        while bar < SONG_BARS:
            cell = lane_data["song"][bar]
            if not cell:
                bar += 1
                continue
            pattern = lane_data["patterns"][cell[0]]
            # Le motif joue à partir de l'endroit où il a été coupé (cell[1]) jusqu'à la fin de sa série de mesures
            span = 1
            while (bar + span < SONG_BARS and lane_data["song"][bar + span]
                   and lane_data["song"][bar + span][0] == cell[0]
                   and lane_data["song"][bar + span][1] == cell[1] + span):
                span += 1
            start_step = cell[1] * STEPS_PER_BAR
            origin = bar * STEPS_PER_BAR - start_step

            def chord_for_step(step, origin=origin):
                absolute = origin + step
                return chord_at(project, absolute // STEPS_PER_BAR, (absolute % STEPS_PER_BAR) // 4)

            for step, note, velocity, length in pattern_notes(project, lane, pattern, chord_for_step):
                if start_step <= step < start_step + span * STEPS_PER_BAR:
                    events.setdefault(origin + step, []).append((channel, note, velocity, length))
            bar += span
    return events


def compile_pattern(project, lane, pattern):
    """Notes d'un motif seul, pour l'écouter en boucle pendant qu'on l'édite."""
    channel = LANE_INFO[lane][1]
    events = {}
    for step, note, velocity, length in pattern_notes(project, lane, pattern, lambda step: 0):
        events.setdefault(step, []).append((channel, note, velocity, length))
    return events


def save(project, path):
    with open(path, "w") as f:
        json.dump(project, f, ensure_ascii=False, indent=1)


def load(path):
    with open(path) as f:
        return json.load(f)
