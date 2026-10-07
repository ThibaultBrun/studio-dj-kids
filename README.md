# Studio DJ Kids

Un PC sous Ubuntu Studio pensé pour un enfant de 9 ans qui veut apprendre à mixer.

## Ce qu'il y a dedans

- **Ma Musique** (`ma-musique/`) : chercher une chanson et la télécharger en MP3 « Artiste - Titre », avec les tags et la pochette, en quelques clics.
  - Onglet **Ma bibliothèque** : écouter ses chansons et les **séparer en pistes** d'un clic (voix, sans voix, batterie, basse, guitare, piano, autres), rangées dans `~/Musique/Pistes séparées/`. Chaque piste se glisse directement dans Mixxx.
  - Séparation : [audio-separator](https://github.com/nomadkaraoke/python-audio-separator) avec le modèle **BS-RoFormer SW** (bien meilleur que Spleeter ou Demucs), sur la carte graphique s'il y en a une (environ 1,5 × la durée du morceau sur une GTX 1050), sinon sur le processeur.
  - **Créer un mashup** (bouton dans « Ma bibliothèque », ou raccourci sur le bureau) : un assistant pas à pas. Pour chaque platine, on choisit une chanson (de la bibliothèque ou d'Internet) et ce qu'on en garde (entière, juste la voix, juste la musique). L'assistant télécharge, sépare, trouve tempo et tonalité ([beat_this](https://github.com/CPJKU/beat_this), [Essentia](https://essentia.upf.edu) ou l'analyse de Mixxx), avec une barre de progression, puis, au choix, **fabrique le MP3** ou **ouvre Mixxx** avec les deux platines calées : même tempo (SYNC), même tonalité (keylock + demi-tons), et **les refrains qui tombent ensemble**. Le refrain est trouvé grâce aux paroles synchronisées ([LRCLIB](https://lrclib.net) : les lignes qui reviennent le plus), sinon d'après le son (le passage qui se répète le plus).
  - **Grille de tempo dans Mixxx** : une voix seule n'a pas de batterie, Mixxx la cale mal. Comme les pistes séparées sont alignées sur la chanson d'origine, Ma Musique leur recopie sa grille et sa tonalité dans la bibliothèque de Mixxx (quand Mixxx est fermé ; sauvegarde de la base dans `~/.mixxx/mixxxdb.sqlite.avant-ma-musique`).
- **Bip** (`bip/`) : le copain musical, 100 % local. D'abord de **gros boutons** : créer un mashup, lancer un mix, télécharger, ouvrir sa bibliothèque, Mon Studio ou LMMS, enchaîner/arrêter dans Mixxx, et des **fiches d'aide** affichées telles quelles (pas d'IA, donc pas d'erreur). En option, « Parler à Bip » : une discussion avec une petite IA, à l'écrit ou à la voix (l'IA ne se charge qu'à ce moment-là, pour laisser la carte graphique libre).
  - IA : [Ollama](https://ollama.com) + Gemma 3 (4B), guidée par des fiches pratiques (`bip/fiches.md`) pour ne pas inventer de boutons.
  - Voix : [Piper](https://github.com/rhasspy/piper) (voix Tom, effet robot) ; écoute : [whisper.cpp](https://github.com/ggml-org/whisper.cpp) (modèle small).
  - **Mix automatique** : « fais un mix avec *Alors on danse* et *One More Time* » ou « fais-moi un mix hip-hop pour scratcher ». Bip trouve ou télécharge les morceaux, ouvre Mixxx, les charge sur les platines et les synchronise. Ensuite : « enchaîne » ou « arrête la musique ».
  - **Qu'est-ce qui va avec ?** (`bip/ca_va_avec.py`) : dès qu'un morceau arrive sur une platine de Mixxx, Bip ouvre une page avec des morceaux qui vont bien avec (vitesse à ±6 %, moitié ou double comprises ; tonalités voisines sur la roue de Camelot), avec un badge « 💚 Parfait » ou « 👍 Ça va bien ». D'abord **dans sa musique** (à glisser sur l'autre platine), puis **des tubes à découvrir** (classement et playlists de hits de [Deezer](https://developers.deezer.com/api), sans les titres « explicites ») : écouter l'extrait de 30 s, ou le télécharger comme pour un mix. Deezer ne donne pas la tonalité : `bip/tubes.py` analyse chaque extrait une fois pour toutes (tempo et tonalité avec `analyser.py`, environ 3 à 4 s par tube sur le processeur, jamais pendant que Mixxx est ouvert), dans `~/.local/share/studio-dj-kids/tubes.json`. Sur 17 chansons de la bibliothèque, l'extrait donne le même tempo (écart moyen 0,3 %, au pire 1,3 %) et la même tonalité 14 fois sur 17 (16 sur 17 compatibles sur la roue de Camelot). Le contrôleur virtuel écrit `BIP:DECK …` dans le journal de Mixxx : il faut lancer Mixxx avec `--log-flush-level warning` (le raccourci installé le fait).
  - Les styles de mix sont dans `bip/ambiances.md` (beats instrumentaux pour le scratch : pas de paroles inadaptées).
- **Contrôleur virtuel Mixxx** (`mixxx/`) : Bip pilote Mixxx par un port MIDI virtuel (`snd-virmidi`), comme une console DJ invisible. Il fonctionne en même temps qu'une vraie console (Hercules…).
- **Hercules DJControl Inpulse 200 MK2** (`mixxx/Hercules-Inpulse-200-MK2-StudioDJKids.midi.xml`) : le mapping Inpulse 200 de Mixxx, plus le **guide de calage** que Mixxx n'utilise pas sur ce modèle (flèches du pitch : quelle platine accélérer ; flèches du jog : dans quel sens pousser pour caler les temps). À choisir dans Mixxx → Préférences → Contrôleurs.
- **[Mon Studio](https://github.com/ThibaultBrun/mon-studio)** (dépôt séparé, installé dans `/opt/mon-studio`) : composer un morceau sur 4 lignes (batterie, basse, accords, mélodie) façon Ableton Learning Music, en appli de bureau. Motifs éditables sur une grille, notes toujours dans la gamme, basse qui suit les accords, motifs et morceaux prêts, lecture en direct (FluidSynth) et export MP3 vers « Mes créations » pour le mixer dans Mixxx.
- **Défis DJ** (`dj-defi/`) : un jeu de rythme façon DJ Hero sur ses propres chansons. Les notes (vert ← / rouge ↓ / bleu →) tombent sur les vrais coups de batterie (grosse caisse, caisse claire, charleston, repérés sur la piste « Batterie » séparée si elle existe), calés sur la grille de tempo, pendant une minute autour du refrain. Trois niveaux, combos ×2/×3/×4, étoiles et records.
- **Faire des beats** (`lmms/`) : LMMS s'ouvre sur « Mon premier beat », un beat hip-hop 808 à 90 BPM prêt à modifier (généré par `scripts/make_beat.py`), plus Hydrogen pour débuter.
- Des raccourcis vers des sites de création musicale et des sites éducatifs (`desktop/`, `icons/`).

## Installation

Depuis un compte administrateur, le dépôt placé dans un dossier lisible par l'enfant :

```bash
sudo git clone https://github.com/<compte>/studio-dj-kids.git /opt/studio-dj-kids
cd /opt/studio-dj-kids
sudo ./scripts/install.sh <compte-enfant>
```

Puis ouvrir Mixxx une première fois depuis le compte de l'enfant.

## Vie privée

Tout tourne en local : l'IA, la voix et l'écoute. Les conversations avec Bip sont enregistrées pour les parents dans `~/.local/share/studio-dj-kids/conversations/` du compte de l'enfant, et ne sont jamais versionnées.
