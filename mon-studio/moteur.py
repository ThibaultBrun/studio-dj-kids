"""Mon Studio : le moteur audio (FluidSynth), pour jouer en direct et exporter en WAV."""
import ctypes
import wave

SOUNDFONT = b"/usr/share/sounds/sf2/FluidR3_GM.sf2"
SAMPLE_RATE = 44100

_lib = ctypes.CDLL("libfluidsynth.so.3")
_p = ctypes.c_void_p
for name, restype, argtypes in [
    ("new_fluid_settings", _p, []),
    ("delete_fluid_settings", None, [_p]),
    ("fluid_settings_setstr", ctypes.c_int, [_p, ctypes.c_char_p, ctypes.c_char_p]),
    ("fluid_settings_setnum", ctypes.c_int, [_p, ctypes.c_char_p, ctypes.c_double]),
    ("fluid_settings_setint", ctypes.c_int, [_p, ctypes.c_char_p, ctypes.c_int]),
    ("new_fluid_synth", _p, [_p]),
    ("delete_fluid_synth", None, [_p]),
    ("fluid_synth_sfload", ctypes.c_int, [_p, ctypes.c_char_p, ctypes.c_int]),
    ("fluid_synth_program_select", ctypes.c_int, [_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]),
    ("fluid_synth_cc", ctypes.c_int, [_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]),
    ("fluid_synth_noteon", ctypes.c_int, [_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]),
    ("fluid_synth_noteoff", ctypes.c_int, [_p, ctypes.c_int, ctypes.c_int]),
    ("fluid_synth_all_notes_off", ctypes.c_int, [_p, ctypes.c_int]),
    ("fluid_synth_write_s16", ctypes.c_int, [_p, ctypes.c_int, _p, ctypes.c_int, ctypes.c_int, _p, ctypes.c_int, ctypes.c_int]),
    ("new_fluid_audio_driver", _p, [_p, _p]),
    ("delete_fluid_audio_driver", None, [_p]),
    ("new_fluid_sequencer2", _p, [ctypes.c_int]),
    ("delete_fluid_sequencer", None, [_p]),
    ("fluid_sequencer_register_fluidsynth", ctypes.c_short, [_p, _p]),
    ("fluid_sequencer_get_tick", ctypes.c_uint, [_p]),
    ("fluid_sequencer_send_at", ctypes.c_int, [_p, _p, ctypes.c_uint, ctypes.c_int]),
    ("fluid_sequencer_remove_events", None, [_p, ctypes.c_short, ctypes.c_short, ctypes.c_int]),
    ("new_fluid_event", _p, []),
    ("delete_fluid_event", None, [_p]),
    ("fluid_event_set_source", None, [_p, ctypes.c_short]),
    ("fluid_event_set_dest", None, [_p, ctypes.c_short]),
    ("fluid_event_note", None, [_p, ctypes.c_int, ctypes.c_short, ctypes.c_short, ctypes.c_uint]),
]:
    function = getattr(_lib, name)
    function.restype = restype
    function.argtypes = argtypes


def _new_synth(realtime):
    settings = _lib.new_fluid_settings()
    _lib.fluid_settings_setnum(settings, b"synth.sample-rate", float(SAMPLE_RATE))
    _lib.fluid_settings_setnum(settings, b"synth.gain", 0.5)
    _lib.fluid_settings_setint(settings, b"synth.reverb.active", 1)
    _lib.fluid_settings_setint(settings, b"synth.chorus.active", 0)
    if realtime:
        _lib.fluid_settings_setstr(settings, b"audio.driver", b"pulseaudio")
        _lib.fluid_settings_setint(settings, b"audio.period-size", 512)
    synth = _lib.new_fluid_synth(settings)
    sfont = _lib.fluid_synth_sfload(synth, SOUNDFONT, 1)
    if sfont < 0:
        raise RuntimeError("Impossible de charger la banque de sons")
    return settings, synth


def setup_channels(synth, channels):
    """channels : {canal: (banque, programme, volume 0-127)}"""
    for channel, (bank, program, volume) in channels.items():
        _lib.fluid_synth_program_select(synth, channel, 1, bank, program)
        _lib.fluid_synth_cc(synth, channel, 7, volume)


class Engine:
    """Synthé en direct : les notes sont programmées à l'avance au milliseconde près par le séquenceur."""

    def __init__(self):
        self.settings, self.synth = _new_synth(realtime=True)
        self.driver = _lib.new_fluid_audio_driver(self.settings, self.synth)
        self.sequencer = _lib.new_fluid_sequencer2(0)
        self.dest = _lib.fluid_sequencer_register_fluidsynth(self.sequencer, self.synth)

    def setup(self, channels):
        setup_channels(self.synth, channels)

    def now(self):
        return _lib.fluid_sequencer_get_tick(self.sequencer)

    def note_at(self, time_ms, channel, note, velocity, duration_ms):
        event = _lib.new_fluid_event()
        _lib.fluid_event_set_source(event, -1)
        _lib.fluid_event_set_dest(event, self.dest)
        _lib.fluid_event_note(event, channel, note, velocity, max(1, int(duration_ms)))
        _lib.fluid_sequencer_send_at(self.sequencer, event, int(time_ms), 1)
        _lib.delete_fluid_event(event)

    def preview(self, channel, note, velocity=100, duration_ms=300):
        """Joue une note tout de suite (quand on clique une case)."""
        self.note_at(self.now() + 5, channel, note, velocity, duration_ms)

    def stop(self):
        _lib.fluid_sequencer_remove_events(self.sequencer, -1, self.dest, -1)
        _lib.fluid_synth_all_notes_off(self.synth, -1)

    def close(self):
        self.stop()
        _lib.delete_fluid_sequencer(self.sequencer)
        _lib.delete_fluid_audio_driver(self.driver)
        _lib.delete_fluid_synth(self.synth)
        _lib.delete_fluid_settings(self.settings)


def render_wav(path, events, total_steps, tempo, channels, tail_seconds=2.5):
    """Fabrique le fichier WAV du morceau. events : {pas: [(canal, note, force, durée en pas)]}."""
    settings, synth = _new_synth(realtime=False)
    setup_channels(synth, channels)
    step_frames = SAMPLE_RATE * 60 / tempo / 4
    timeline = []  # (image, 1 = début / 0 = fin, canal, note, force)
    for step, notes in events.items():
        for channel, note, velocity, length in notes:
            start = int(step * step_frames)
            timeline.append((start, 1, channel, note, velocity))
            timeline.append((start + int(length * step_frames * 0.95), 0, channel, note, 0))
    timeline.sort(key=lambda e: (e[0], e[1]))  # les fins avant les débuts au même instant
    end_frame = int(total_steps * step_frames + tail_seconds * SAMPLE_RATE)

    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        position = 0

        def write_until(frame):
            nonlocal position
            while position < frame:
                count = min(4096, frame - position)
                buffer = (ctypes.c_int16 * (2 * count))()
                _lib.fluid_synth_write_s16(synth, count, buffer, 0, 2, buffer, 1, 2)
                out.writeframes(bytes(buffer))
                position += count

        for frame, is_on, channel, note, velocity in timeline:
            write_until(frame)
            if is_on:
                _lib.fluid_synth_noteon(synth, channel, note, velocity)
            else:
                _lib.fluid_synth_noteoff(synth, channel, note)
        write_until(end_frame)
    _lib.delete_fluid_synth(synth)
    _lib.delete_fluid_settings(settings)
