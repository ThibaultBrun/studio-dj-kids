// Boutons 🎤 et ♪ de la Hercules DJControl Inpulse 200 Mk3 (là où la MK2 avait Loop In / Loop Out) :
// avec un fichier stems (Mixxx 2.6 et plus), 🎤 coupe ou remet la voix, ♪ coupe ou remet la musique
// (batterie + basse + le reste). Le bouton est allumé quand ce qu'il commande s'entend.
// Ordre des pistes d'un fichier stems : 1 batterie, 2 basse, 3 le reste, 4 voix.

var PistesStems = {};

PistesStems.MIC = 0x0A;
PistesStems.MUSIC = 0x09;
PistesStems.STATUS = {"[Channel1]": 0x91, "[Channel2]": 0x92};
PistesStems.VOICE = [4];
PistesStems.INSTRUMENTS = [1, 2, 3];

PistesStems.stemGroup = function(group, n) {
    return group.replace("]", "_Stem" + n + "]");
};

PistesStems.toggle = function(group, stems) {
    // Si au moins une piste s'entend, on coupe tout ; sinon on remet tout.
    var anyOn = stems.some(function(n) {
        return !engine.getValue(PistesStems.stemGroup(group, n), "mute");
    });
    stems.forEach(function(n) {
        engine.setValue(PistesStems.stemGroup(group, n), "mute", anyOn ? 1 : 0);
    });
};

PistesStems.mic = function(channel, control, value, status, group) {
    if (value > 0) {
        PistesStems.toggle(group, PistesStems.VOICE);
    }
};

PistesStems.music = function(channel, control, value, status, group) {
    if (value > 0) {
        PistesStems.toggle(group, PistesStems.INSTRUMENTS);
    }
};

PistesStems.lights = function(group) {
    var status = PistesStems.STATUS[group];
    var hasStems = engine.getValue(group, "stem_count") > 0;
    var on = function(stems) {
        return hasStems && stems.some(function(n) {
            return !engine.getValue(PistesStems.stemGroup(group, n), "mute");
        });
    };
    midi.sendShortMsg(status, PistesStems.MIC, on(PistesStems.VOICE) ? 0x7F : 0x00);
    midi.sendShortMsg(status, PistesStems.MUSIC, on(PistesStems.INSTRUMENTS) ? 0x7F : 0x00);
};

PistesStems.init = function() {
    Object.keys(PistesStems.STATUS).forEach(function(group) {
        var update = function() { PistesStems.lights(group); };
        engine.makeConnection(group, "stem_count", update);
        for (var n = 1; n <= 4; n++) {
            engine.makeConnection(PistesStems.stemGroup(group, n), "mute", update);
        }
        update();
    });
};

PistesStems.shutdown = function() {
    Object.keys(PistesStems.STATUS).forEach(function(group) {
        midi.sendShortMsg(PistesStems.STATUS[group], PistesStems.MIC, 0x00);
        midi.sendShortMsg(PistesStems.STATUS[group], PistesStems.MUSIC, 0x00);
    });
};
