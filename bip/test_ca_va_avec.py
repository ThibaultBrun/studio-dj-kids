"""Tests de « Qu'est-ce qui va avec ? » : python3 -m unittest bip/test_ca_va_avec.py (sans toucher aux données)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from PyQt6.QtCore import QCoreApplication  # noqa: E402

import ca_va_avec as cva  # noqa: E402

APP = QCoreApplication.instance() or QCoreApplication([])


class Tonalites(unittest.TestCase):
    def test_noms(self):
        self.assertEqual(cva.key_from_text("Ebm"), 16)
        self.assertEqual(cva.key_from_text("D#m"), 16)
        self.assertEqual(cva.key_from_text("A"), 10)
        self.assertEqual(cva.key_from_text("4A"), 0)
        self.assertEqual(cva.key_name(22), "La mineur")
        self.assertEqual(cva.key_name(1), "Do majeur")
        self.assertEqual(cva.key_name(23), "Si bémol mineur")
        self.assertEqual(cva.key_name(0), "tonalité inconnue")

    def test_roue_de_camelot(self):
        self.assertEqual(cva.camelot(22), (8, "A"))   # La mineur
        self.assertEqual(cva.camelot(1), (8, "B"))    # Do majeur
        self.assertEqual(cva.camelot(21), (1, "A"))   # Sol dièse mineur
        self.assertEqual(cva.camelot(12), (1, "B"))   # Si majeur
        self.assertEqual(cva.camelot(14), (12, "A"))  # Do dièse mineur
        self.assertEqual(cva.camelot(5), (12, "B"))   # Mi majeur
        self.assertEqual(len({cva.camelot(k) for k in range(1, 25)}), 24)

    def test_accords(self):
        self.assertEqual(cva.key_match(22, 22), "same")
        self.assertEqual(cva.key_match(22, 1), "relative")     # La mineur / Do majeur
        self.assertEqual(cva.key_match(22, 17), "neighbour")   # 8A / 9A (Mi mineur)
        self.assertEqual(cva.key_match(22, 15), "neighbour")   # 8A / 7A (Ré mineur)
        self.assertEqual(cva.key_match(21, 14), "neighbour")   # 1A / 12A : la roue tourne
        self.assertIsNone(cva.key_match(22, 2))                # La mineur / Ré bémol majeur
        self.assertIsNone(cva.key_match(22, 8))                # 8A / 9B : pas proposé
        self.assertEqual(cva.key_match(0, 22), "unknown")


class Vitesse(unittest.TestCase):
    def test_ecart(self):
        self.assertAlmostEqual(cva.tempo_gap(120, 126)[0], 0.05)
        self.assertEqual(cva.tempo_gap(140, 70), (0, 2))     # moitié moins vite : ça marche aussi
        self.assertEqual(cva.tempo_gap(70, 140), (0, 0.5))

    def test_compatibilite(self):
        perfect = cva.compatibility(120, 22, 121, 22)
        self.assertEqual(perfect["badge"], "💚 Parfait")
        self.assertEqual(cva.compatibility(120, 22, 121, 1)["badge"], "💚 Parfait")       # relative
        self.assertEqual(cva.compatibility(120, 22, 120, 17)["badge"], "👍 Ça va bien")   # voisine
        self.assertEqual(cva.compatibility(120, 22, 125, 22)["badge"], "👍 Ça va bien")   # 4 % plus vite
        self.assertIsNone(cva.compatibility(120, 22, 128, 22))                           # 6,7 % : trop
        self.assertIsNone(cva.compatibility(120, 22, 120, 2))                            # tonalité qui sonne faux
        self.assertIsNotNone(cva.compatibility(140, 16, 70.5, 16))                       # moitié
        self.assertIsNone(cva.compatibility(120, 22, 0, 22))                             # vitesse inconnue
        scores = [cva.compatibility(120, 22, bpm, key)["score"] for bpm, key in
                  [(120, 22), (120, 1), (123, 22), (120, 17), (123, 17), (126, 17)]]
        self.assertEqual(scores, sorted(scores, reverse=True))


class Journal(unittest.TestCase):
    LINE = "21:05:31.550 Warning [Controller] BIP:DECK 2 bpm=120.75 key=22 duration=219.0"

    def test_ligne(self):
        self.assertEqual(cva.parse_deck_line(self.LINE), {"deck": 2, "bpm": 120.75, "key": 22, "duration": 219.0})
        self.assertIsNone(cva.parse_deck_line("21:05:31.550 Warning [Controller] BIP:RECU"))
        self.assertIsNone(cva.parse_deck_line("Debug [Main] DECK 1 bpm=3"))

    def test_surveillance(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "mixxx.log"
            log.write_text("old\n12:00:00.000 Warning [Controller] BIP:DECK 1 bpm=97.00 key=20 duration=214.2\n")
            watcher = cva.LogWatcher(log)
            seen = []
            watcher.loaded.connect(seen.append)
            watcher.start()
            self.assertEqual(watcher.decks[1]["bpm"], 97.0)   # déjà connu, mais pas annoncé
            watcher.poll()
            self.assertEqual(seen, [])
            with log.open("a") as f:
                f.write("Debug [Main] bla\n" + self.LINE[:40])  # ligne pas encore finie
            watcher.poll()
            self.assertEqual(seen, [])
            with log.open("a") as f:
                f.write(self.LINE[40:] + "\n")
            watcher.poll()
            self.assertEqual([s["deck"] for s in seen], [2])
            watcher.poll()
            self.assertEqual(len(seen), 1)                    # pas deux fois la même ligne
            # Mixxx redémarre : nouveau journal, plus court
            log.unlink()
            log.write_text("12:00:00.000 Warning [Controller] BIP:DECK 1 bpm=90.00 key=13 duration=245.8\n")
            watcher.poll()
            self.assertEqual([s["bpm"] for s in seen], [120.75, 90.0])
            watcher.timer.stop()


class Bibliotheque(unittest.TestCase):
    LIB = [
        {"path": "/m/CHIC - Le Freak.mp3", "artist": "CHIC", "title": "Le Freak", "duration": 219.0, "bpm": 120.75, "key": 22},
        {"path": "/m/Pistes séparées/CHIC - Le Freak/CHIC - Le Freak (Voix).mp3", "artist": "CHIC",
         "title": "Le Freak (Voix)", "duration": 219.0, "bpm": 120.75, "key": 22},
        {"path": "/m/Apache.mp3", "artist": "Incredible Bongo Band", "title": "Apache", "duration": 293.4, "bpm": 118.53, "key": 22},
        {"path": "/m/Celebration.mp3", "artist": "Kool & The Gang", "title": "Celebration", "duration": 257.4, "bpm": 119.67, "key": 2},
        {"path": "/m/sample.mp3", "artist": "x", "title": "sample", "duration": 9.0, "bpm": 120, "key": 22},
    ]
    TUBES = [
        {"id": 1, "artist": "Chic", "title": "Le Freak (Edit)", "bpm": 120, "key": 22},
        {"id": 2, "artist": "Daft Punk", "title": "Get Lucky", "bpm": 116, "key": 13},   # Do mineur : faux
        {"id": 3, "artist": "Boney M.", "title": "Daddy Cool", "bpm": 119, "key": 1},    # Do majeur : relative
        {"id": 4, "artist": "Toto", "title": "Africa", "bpm": 92, "key": 22},            # trop lent
    ]

    def test_reconnaitre(self):
        found = cva.identify({"deck": 1, "bpm": 120.75, "key": 22, "duration": 219.4}, self.LIB)
        self.assertEqual(found["path"], "/m/CHIC - Le Freak.mp3")   # l'original, pas la piste séparée
        self.assertIsNone(cva.identify({"deck": 1, "bpm": 100, "key": 22, "duration": 219.0}, self.LIB))
        self.assertIsNone(cva.identify({"deck": 1, "bpm": 120.75, "key": 22, "duration": 221.0}, self.LIB))

    def test_suggestions(self):
        info = {"deck": 1, "bpm": 120.75, "key": 22, "duration": 219.0}
        mine, tubes = cva.suggestions(info, self.LIB, self.TUBES, self.LIB[0])
        self.assertEqual([s["title"] for s in mine], ["Apache"])   # ni lui-même, ni ses pistes, ni les samples
        self.assertEqual([t["id"] for t in tubes], [3])            # Le Freak est déjà dans sa musique

    def test_memes_chansons(self):
        self.assertTrue(cva.same_song("The Jackson 5", "ABC", "Jackson 5", "ABC (Remastered)"))
        self.assertTrue(cva.same_song("Anaïs Delva", 'Libérée, Délivrée (De "La Reine des Neiges")',
                                      "Anais Delva", "Libérée, délivrée"))
        self.assertTrue(cva.same_song("Shakira", "Hips Don't Lie (feat. Wyclef Jean)", "Shakira", "Hips Don't Lie"))
        self.assertFalse(cva.same_song("Red Hot Chili Peppers", "Can't Stop", "Red Hot Chili Peppers", "Give It Away"))


if __name__ == "__main__":
    unittest.main()
