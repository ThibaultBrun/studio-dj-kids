// Guide de calage (« Beatmatch Guide ») pour la Hercules DJControl Inpulse 200 / 200 MK2.
// Le mapping de Mixxx ne s'en sert pas : on allume les flèches comme le fait celui de l'Inpulse 300.
//  - Flèches du pitch : quelle platine accélérer ou ralentir pour avoir le même tempo.
//  - Flèches du jog : dans quel sens pousser la platine pour que les temps tombent ensemble.
// Notes MIDI : documentation Hercules « MIDI commands of DJControl Inpulse 200 » (p. 23-24).

var GuideCalage = {};

GuideCalage.TEMPO_TOLERANCE = 0.1;   // BPM : en dessous, les tempos sont égaux
GuideCalage.ALIGN_TOLERANCE = 0.02;  // fraction de temps : en dessous, les temps sont alignés
GuideCalage.PITCH_UP = 0x1E;
GuideCalage.PITCH_DOWN = 0x1F;
GuideCalage.JOG_LEFT = 0x1C;
GuideCalage.JOG_RIGHT = 0x1D;

GuideCalage.init = function() {
    ["[Channel1]", "[Channel2]"].forEach(function(group) {
        engine.makeConnection(group, "bpm", GuideCalage.update);
        engine.makeConnection(group, "play", GuideCalage.update);
        engine.makeConnection(group, "beat_distance", GuideCalage.updateAlign);
    });
    GuideCalage.update();
};

GuideCalage.shutdown = function() {
    GuideCalage.lights(GuideCalage.PITCH_UP, GuideCalage.PITCH_DOWN, 0);
    GuideCalage.lights(GuideCalage.JOG_LEFT, GuideCalage.JOG_RIGHT, 0);
};

GuideCalage.bothPlaying = function() {
    return engine.getValue("[Channel1]", "play") > 0 && engine.getValue("[Channel2]", "play") > 0;
};

// direction > 0 : la platine 1 est « devant » (plus rapide ou en avance), < 0 : la platine 2, 0 : éteint
GuideCalage.lights = function(noteUp, noteDown, direction) {
    midi.sendShortMsg(0x91, noteUp, direction > 0 ? 0x7F : 0x00);
    midi.sendShortMsg(0x91, noteDown, direction < 0 ? 0x7F : 0x00);
    midi.sendShortMsg(0x92, noteUp, direction < 0 ? 0x7F : 0x00);
    midi.sendShortMsg(0x92, noteDown, direction > 0 ? 0x7F : 0x00);
};

GuideCalage.update = function() {
    var difference = engine.getValue("[Channel1]", "bpm") - engine.getValue("[Channel2]", "bpm");
    var direction = GuideCalage.bothPlaying() && Math.abs(difference) >= GuideCalage.TEMPO_TOLERANCE ? difference : 0;
    GuideCalage.lights(GuideCalage.PITCH_UP, GuideCalage.PITCH_DOWN, direction);
    GuideCalage.updateAlign();
};

GuideCalage.updateAlign = function() {
    var deck1 = engine.getValue("[Channel1]", "beat_distance");
    var deck2 = engine.getValue("[Channel2]", "beat_distance");
    // beat_distance repart à 0 à chaque temps : 0,98 et 0,01 sont presque alignés
    var difference = deck1 - deck2;
    if (difference > 0.5) {
        difference -= 1;
    } else if (difference < -0.5) {
        difference += 1;
    }
    var direction = GuideCalage.bothPlaying() && Math.abs(difference) >= GuideCalage.ALIGN_TOLERANCE ? difference : 0;
    // Même sens des flèches que le mapping Inpulse 300 de Mixxx (testé sur le vrai matériel)
    GuideCalage.lights(GuideCalage.JOG_RIGHT, GuideCalage.JOG_LEFT, direction);
};
