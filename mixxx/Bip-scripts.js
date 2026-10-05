// Contrôleur virtuel « Bip » : l'assistant envoie des messages MIDI, ce script agit dans Mixxx.
// Bip lit les messages « BIP:... » dans le journal de Mixxx pour savoir où on en est.

var Bip = {};

Bip.MODE_MIX = 1;
Bip.MODE_SCRATCH = 2;
Bip.MODE_MASHUP = 3;
Bip.WAIT_STEP_MS = 500;
Bip.WAIT_MAX_MS = 45000;      // temps max pour charger et analyser les deux morceaux
Bip.TRANSITION_MS = 8000;     // durée du fondu enchaîné automatique
Bip.TRANSITION_STEPS = 40;

Bip.busy = false;
// Réglages du mashup, envoyés par Ma Musique juste avant l'ordre « prépare »
Bip.mashup = {shift: 0, leader: 2, startHigh: [0, 0, 0], start: [0, 0, 0]};

Bip.init = function() {
    Bip.busy = false;
};

Bip.shutdown = function() {};

Bip.say = function(message) {
    console.warn("BIP:" + message);
};

Bip.loaded = function(deck) {
    return engine.getValue("[Channel" + deck + "]", "track_loaded") > 0;
};

Bip.ready = function(deck) {
    return Bip.loaded(deck) && engine.getValue("[Channel" + deck + "]", "bpm") > 0;
};

Bip.prepare = function(channel, control, value) {
    if (Bip.busy || value === 0) {
        return;
    }
    Bip.busy = true;
    Bip.say("RECU");
    var waited = 0;
    var check = function() {
        // En scratch, le sample est trop court pour avoir un tempo : il suffit qu'il soit chargé
        var loaded = value === Bip.MODE_SCRATCH ? Bip.loaded(1) && Bip.loaded(2) : Bip.ready(1) && Bip.ready(2);
        if (!loaded && waited < Bip.WAIT_MAX_MS) {
            waited += Bip.WAIT_STEP_MS;
            engine.beginTimer(Bip.WAIT_STEP_MS, check, true);
            return;
        }
        if (value === Bip.MODE_SCRATCH) {
            Bip.setupScratch();
        } else if (value === Bip.MODE_MASHUP) {
            Bip.setupMashup(loaded);
        } else {
            Bip.setupMix(loaded);
        }
        Bip.busy = false;
    };
    check();
};

Bip.setupMix = function(loaded) {
    // Platine 1 au départ, platine 2 calée au même tempo et prête à partir
    engine.setValue("[Channel1]", "quantize", 1);
    engine.setValue("[Channel2]", "quantize", 1);
    engine.setValue("[Channel1]", "playposition", 0);
    engine.setValue("[Channel2]", "playposition", 0);
    if (loaded) {
        engine.setValue("[Channel1]", "sync_enabled", 1);
        engine.setValue("[Channel2]", "sync_enabled", 1);
    }
    engine.setValue("[Master]", "crossfader", -1);
    engine.setValue("[Channel1]", "play", 1);
    Bip.say(loaded ? "PRET" : "PRET_SANS_SYNC");
};

Bip.setShift = function(channel, control, value) {
    Bip.mashup.shift = value - 64;  // demi-tons pour la platine qui suit
};

Bip.setLeader = function(channel, control, value) {
    Bip.mashup.leader = value === 1 ? 1 : 2;  // la platine qui donne le tempo
};

// Point de départ de chaque platine, en dixièmes de seconde sur 14 bits (poids fort puis poids faible)
Bip.setStartHigh = function(channel, control, value) {
    Bip.mashup.startHigh[control === 0x75 ? 1 : 2] = value;
};

Bip.setStartLow = function(channel, control, value) {
    var deck = control === 0x76 ? 1 : 2;
    Bip.mashup.start[deck] = (Bip.mashup.startHigh[deck] * 128 + value) / 10;
};

Bip.setupMashup = function(loaded) {
    // La platine qui suit (souvent une voix) prend le tempo de la meneuse, sans changer de hauteur (keylock),
    // décalée du nombre de demi-tons qui accorde les deux tonalités. Ma Musique donne les points de départ :
    // quelques mesures avant le refrain de chaque chanson.
    var leader = "[Channel" + Bip.mashup.leader + "]";
    var follower = "[Channel" + (3 - Bip.mashup.leader) + "]";
    [1, 2].forEach(function(deck) {
        var group = "[Channel" + deck + "]";
        var duration = engine.getValue(group, "duration");
        engine.setValue(group, "quantize", 1);
        engine.setValue(group, "keylock", 1);
        engine.setValue(group, "playposition", duration > 0 ? Math.min(Bip.mashup.start[deck] / duration, 0.95) : 0);
    });
    engine.setValue(leader, "pitch_adjust", 0);
    engine.setValue(follower, "pitch_adjust", Bip.mashup.shift);
    engine.setValue("[Master]", "crossfader", 0);
    if (loaded) {
        engine.setValue(leader, "sync_leader", 1);
        engine.setValue(leader, "sync_enabled", 1);
        engine.setValue(follower, "sync_enabled", 1);
    }
    // Les deux platines partent ensemble, chacune au début d'une mesure : les refrains tomberont ensemble
    engine.setValue(leader, "play", 1);
    engine.setValue(follower, "play", 1);
    Bip.say(loaded ? "PRET_MASHUP" : "PRET_SANS_SYNC");
};

Bip.setupScratch = function() {
    // Le beat tourne sur la platine 1, le sample attend sur la platine 2 pour être scratché
    engine.setValue("[Channel1]", "playposition", 0);
    engine.setValue("[Channel2]", "playposition", 0);
    engine.setValue("[Master]", "crossfader", 0);
    engine.setValue("[Channel1]", "play", 1);
    Bip.say("PRET_SCRATCH");
};

Bip.transition = function(channel, control, value) {
    if (Bip.busy || value === 0) {
        return;
    }
    Bip.busy = true;
    engine.setValue("[Channel2]", "play", 1);
    var step = 0;
    var start = engine.getValue("[Master]", "crossfader");
    var fade = function() {
        step++;
        engine.setValue("[Master]", "crossfader", start + (1 - start) * step / Bip.TRANSITION_STEPS);
        if (step < Bip.TRANSITION_STEPS) {
            engine.beginTimer(Bip.TRANSITION_MS / Bip.TRANSITION_STEPS, fade, true);
            return;
        }
        engine.setValue("[Channel1]", "play", 0);
        Bip.busy = false;
        Bip.say("TRANSITION_FINIE");
    };
    fade();
};

Bip.stopAll = function(channel, control, value) {
    if (value === 0) {
        return;
    }
    engine.setValue("[Channel1]", "play", 0);
    engine.setValue("[Channel2]", "play", 0);
    Bip.say("STOP");
};
