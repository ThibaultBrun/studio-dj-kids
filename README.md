# Studio DJ Kids

Un PC sous Ubuntu Studio pensé pour un enfant de 9 ans qui veut apprendre à mixer.

## Ce qu'il y a dedans

- **Ma Musique** (`ma-musique/`) : chercher une chanson et la télécharger en MP3 « Artiste - Titre », avec les tags et la pochette, en quelques clics.
  - Onglet **Ma bibliothèque** : écouter ses chansons et les **séparer en pistes** d'un clic (voix, sans voix, batterie, basse, guitare, piano, autres), rangées dans `~/Musique/Pistes séparées/`. Chaque piste se glisse directement dans Mixxx.
  - Séparation : [audio-separator](https://github.com/nomadkaraoke/python-audio-separator) avec le modèle **BS-RoFormer SW** (bien meilleur que Spleeter ou Demucs), sur la carte graphique s'il y en a une (environ 1,5 × la durée du morceau sur une GTX 1050), sinon sur le processeur.
- **Bip** (`bip/`) : un petit robot assistant, 100 % local, qui répond aux questions sur les logiciels de l'ordinateur, à l'écrit ou à la voix.
  - IA : [Ollama](https://ollama.com) + Gemma 3 (4B), guidée par des fiches pratiques (`bip/fiches.md`) pour ne pas inventer de boutons.
  - Voix : [Piper](https://github.com/rhasspy/piper) (voix Tom, effet robot) ; écoute : [whisper.cpp](https://github.com/ggml-org/whisper.cpp) (modèle small).
  - **Mix automatique** : « fais un mix avec *Alors on danse* et *One More Time* » ou « fais-moi un mix hip-hop pour scratcher ». Bip trouve ou télécharge les morceaux, ouvre Mixxx, les charge sur les platines et les synchronise. Ensuite : « enchaîne » ou « arrête la musique ».
  - Les styles de mix sont dans `bip/ambiances.md` (beats instrumentaux pour le scratch : pas de paroles inadaptées).
- **Contrôleur virtuel Mixxx** (`mixxx/`) : Bip pilote Mixxx par un port MIDI virtuel (`snd-virmidi`), comme une console DJ invisible. Il fonctionne en même temps qu'une vraie console (Hercules…).
- **[Mon Studio](https://github.com/ThibaultBrun/mon-studio)** (dépôt séparé, installé dans `/opt/mon-studio`) : composer un morceau sur 4 lignes (batterie, basse, accords, mélodie) façon Ableton Learning Music, en appli de bureau. Motifs éditables sur une grille, notes toujours dans la gamme, basse qui suit les accords, motifs et morceaux prêts, lecture en direct (FluidSynth) et export MP3 vers « Mes créations » pour le mixer dans Mixxx.
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
