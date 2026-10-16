// Hercules DJControl Inpulse 200 Mk3 : ce qui change par rapport à la MK2 (mapping de base de Mixxx).
// Notes relevées sur la vraie platine (platine gauche 0x91 / pads 0x96, droite 0x92 / pads 0x97) :
//   Shift = 0x04, Hot Cue = 0x0F, Loop = 0x03 (là où la MK2 avait Vinyl), FX = 0x10.
// Les boutons de mode choisissent ce que font les pads ; la lumière du mode choisi est allumée.
//   Hot Cue : pads 1-4 = points de repère (Shift + pad = effacer le point)
//   Loop    : pads 1-4 = boucle de 1 / 2 / 4 / 8 temps (rappuyer pour sortir)
//   FX      : pads 1-3 = effets 1 à 3 de l'unité d'effets de la platine, pad 4 = l'unité on/off
// Le scratch au jog reste toujours actif (la MK2 le coupait avec Vinyl).

var Mk3 = {};

Mk3.HOTCUE = 0x0F;
Mk3.LOOP = 0x03;
Mk3.FX = 0x10;
Mk3.MODES = [Mk3.HOTCUE, Mk3.LOOP, Mk3.FX];
Mk3.LOOP_BEATS = [1, 2, 4, 8];
Mk3.DECK = {"[Channel1]": {status: 0x91, number: 1}, "[Channel2]": {status: 0x92, number: 2}};

Mk3.init = function() {
    Mk3.mode = {"[Channel1]": Mk3.HOTCUE, "[Channel2]": Mk3.HOTCUE};
    Mk3.shifted = {"[Channel1]": false, "[Channel2]": false};
    DJCi200.scratchButtonState = true;  // scratch toujours actif
    Object.keys(Mk3.DECK).forEach(Mk3.lights);
};

Mk3.lights = function(group) {
    Mk3.MODES.forEach(function(note) {
        midi.sendShortMsg(Mk3.DECK[group].status, note, Mk3.mode[group] === note ? 0x7F : 0x00);
    });
};

Mk3.modeButton = function(channel, control, value, status, group) {
    if (value > 0 && Mk3.MODES.indexOf(control) >= 0) {
        Mk3.mode[group] = control;
        Mk3.lights(group);
    }
};

Mk3.shift = function(channel, control, value, status, group) {
    Mk3.shifted[group] = value > 0;
};

Mk3.pad = function(channel, control, value, status, group) {
    var n = control % 8;  // le numéro du pad, quel que soit le mode que la platine croit avoir
    if (n > 3) {
        return;
    }
    var pressed = value > 0;
    var deck = Mk3.DECK[group].number;
    var mode = Mk3.mode[group];
    if (mode === Mk3.HOTCUE) {
        if (Mk3.shifted[group]) {
            if (pressed) {
                engine.setValue(group, "hotcue_" + (n + 1) + "_clear", 1);
            }
        } else {
            engine.setValue(group, "hotcue_" + (n + 1) + "_activate", pressed ? 1 : 0);
        }
    } else if (mode === Mk3.LOOP) {
        if (pressed) {
            engine.setValue(group, "beatloop_" + Mk3.LOOP_BEATS[n] + "_toggle", 1);
        }
    } else if (mode === Mk3.FX && pressed) {
        var unit = "[EffectRack1_EffectUnit" + deck + "]";
        if (n < 3) {
            var effect = "[EffectRack1_EffectUnit" + deck + "_Effect" + (n + 1) + "]";
            engine.setValue(effect, "enabled", !engine.getValue(effect, "enabled"));
            engine.setValue(unit, "group_" + group + "_enable", 1);
        } else {
            var key = "group_" + group + "_enable";
            engine.setValue(unit, key, !engine.getValue(unit, key));
        }
    }
};

Mk3.shutdown = function() {
    Object.keys(Mk3.DECK).forEach(function(group) {
        Mk3.MODES.forEach(function(note) {
            midi.sendShortMsg(Mk3.DECK[group].status, note, 0x00);
        });
    });
};
