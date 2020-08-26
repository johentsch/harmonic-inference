"""Tests for piece.py"""
from fractions import Fraction

import pandas as pd

from harmonic_inference.data.data_types import KeyMode, PitchType, ChordType
from harmonic_inference.data.piece import *
from harmonic_inference.utils import harmonic_constants as hc


def test_note_from_series():
    def check_equals(note_dict, note, pitch_type):
        assert pitch_type == note.pitch_type
        if pitch_type == PitchType.MIDI:
            assert (note_dict['midi'] % hc.NUM_PITCHES[PitchType.MIDI]) == note.pitch_class
        else:
            assert note.pitch_class == note_dict['tpc'] + hc.TPC_C
        assert note.octave == note_dict['midi'] // hc.NUM_PITCHES[PitchType.MIDI]
        assert note.onset == (note_dict['mc'], note_dict['onset'])
        assert note.offset == (note_dict['offset_mc'], note_dict['offset_beat'])
        assert note.duration == note_dict['duration']

    note_dict = {
        'midi': 50,
        'tpc': 5,
        'mc': 1,
        'onset': Fraction(1, 2),
        'offset_mc': 2,
        'offset_beat': Fraction(3, 4),
        'duration': Fraction(5, 6),
    }

    key_values = {
        'midi': range(127),
        'tpc': range(-hc.TPC_C, hc.TPC_C),
        'mc': range(3),
        'onset': [i * Fraction(1, 2) for i in range(3)],
        'offset_mc': range(3),
        'offset_beat': [i * Fraction(1, 2) for i in range(3)],
        'duration': [i * Fraction(1, 2) for i in range(3)],
    }

    for key, values in key_values.items():
        for value in values:
            note_dict[key] = value
            note_series = pd.Series(note_dict)
            note = Note.from_series(note_series, PitchType.MIDI)
            check_equals(note_dict, note, PitchType.MIDI)
            note = Note.from_series(note_series, PitchType.TPC)
            check_equals(note_dict, note, PitchType.TPC)

    note_dict['tpc'] = hc.NUM_PITCHES[PitchType.TPC] - hc.TPC_C
    assert Note.from_series(pd.Series(note_dict), PitchType.TPC) is None
    note_dict['tpc'] = 0 - hc.TPC_C - 1
    assert Note.from_series(pd.Series(note_dict), PitchType.TPC) is None


def test_chord_from_series():
    def check_equals(chord_dict, chord, pitch_type, local_key):
        assert chord.pitch_type == pitch_type
        assert chord.chord_type == hu.get_chord_type_from_string(chord_dict['chord_type'])
        assert chord.inversion == hu.get_chord_inversion(chord_dict['figbass'])
        assert chord.onset == (chord_dict['mc'], chord_dict['onset'])
        assert chord.offset == (chord_dict['mc_next'], chord_dict['onset_next'])
        assert chord.duration == chord_dict['duration']

        root = chord_dict['root']
        bass = chord_dict['bass_note']
        if pitch_type == PitchType.MIDI:
            root = hu.tpc_interval_to_midi_interval(root)
            bass = hu.tpc_interval_to_midi_interval(bass)
        assert chord.root == hu.transpose_pitch(local_key.tonic, root, pitch_type)
        assert chord.bass == hu.transpose_pitch(local_key.tonic, bass, pitch_type)

    chord_dict = {
        'numeral': 'III',
        'root': 5,
        'bass_note': 5,
        'chord_type': 'M',
        'figbass': '',
        'globalkey': 'A',
        'globalkey_is_minor': False,
        'localkey': 'iii',
        'localkey_is_minor': True,
        'relativeroot': pd.NA,
        'offset_mc': 2,
        'offset_beat': Fraction(3, 4),
        'duration': Fraction(5, 6),
        'mc': 1,
        'onset': Fraction(1, 2),
        'mc_next': 2,
        'onset_next': Fraction(3, 4),
        'duration': Fraction(5, 6),
    }

    key_values = {
        'root': range(-7, 7),
        'bass_note': range(-7, 7),
        'chord_type': hc.STRING_TO_CHORD_TYPE.keys(),
        'figbass': hc.FIGBASS_INVERSIONS.keys(),
        'mc': range(3),
        'onset': [i * Fraction(1, 2) for i in range(3)],
        'mc_next': range(3),
        'onset_next': [i * Fraction(1, 2) for i in range(3)],
        'duration': [i * Fraction(1, 2) for i in range(3)],
    }

    for key, values in key_values.items():
        for value in values:
            chord_dict[key] = value
            chord_series = pd.Series(chord_dict)
            for pitch_type in PitchType:
                chord = Chord.from_series(chord_series, pitch_type)
                local_key = Key.from_series(chord_series, pitch_type, do_relative=False)
                check_equals(chord_dict, chord, pitch_type, local_key)

    # @none returns None
    for numeral in ['@none', pd.NA]:
        chord_dict['numeral'] = numeral
        chord_series = pd.Series(chord_dict)
        for pitch_type in PitchType:
            assert Chord.from_series(chord_series, pitch_type) is None
    chord_dict['numeral'] = 'III'

    # Bad key returns None
    chord_dict['localkey'] = 'Error'
    chord_series = pd.Series(chord_dict)
    for pitch_type in PitchType:
        assert Chord.from_series(chord_series, pitch_type) is None
    chord_dict['localkey'] = 'iii'

    # Bad relativeroot is ok
    chord_dict['relativeroot'] = 'Error'
    chord_series = pd.Series(chord_dict)
    for pitch_type in PitchType:
        assert Chord.from_series(chord_series, pitch_type) is not None


def test_key_from_series():
    def get_relative(global_tonic, global_mode, relative_numeral, pitch_type):
        """Get the relative key tonic of a numeral in a given global key."""
        local_interval = hu.get_interval_from_numeral(relative_numeral, global_mode, pitch_type)
        local_tonic = hu.transpose_pitch(global_tonic, local_interval, pitch_type)
        return local_tonic

    def check_equals(key_dict, key, pitch_type, do_relative):
        assert key.tonic_type == pitch_type

        # Check mode
        if do_relative and not pd.isnull(key_dict['relativeroot']):
            final_root = key_dict['relativeroot'].split('/')[0]
            assert key.mode == KeyMode.MINOR if final_root[-1].islower() else KeyMode.MAJOR
        else:
            assert key.mode == KeyMode.MINOR if key_dict['localkey_is_minor'] else KeyMode.MAJOR

        # Check tonic
        if do_relative and not pd.isnull(key_dict['relativeroot']):
            # We can rely on this non-relative local key. It is checked below
            local_key = Key.from_series(pd.Series(key_dict), pitch_type, do_relative=False)
            key_tonic = local_key.tonic
            key_mode = local_key.mode
            for relative_numeral in reversed(key_dict['relativeroot'].split('/')):
                key_tonic = get_relative(key_mode, relative_numeral, pitch_type)
                key_mode = KeyMode.MINOR if relative_numeral[-1].islower() else KeyMode.MAJOR
        else:
            global_key_tonic = hu.get_pitch_from_string(key_dict['globalkey'], pitch_type)
            global_mode = KeyMode.MINOR if key_dict['globalkey_is_minor'] else KeyMode.MAJOR
            local_key_tonic = get_relative(
                global_key_tonic, global_mode, key_dict['localkey'], pitch_type
            )
            local_key_mode = KeyMode.MINOR if key_dict['localkey_is_minor'] else KeyMode.MAJOR
            assert key.tonic == local_key_tonic
            assert key.mode == local_key_mode

    key_dict = {
        'globalkey': 'A',
        'globalkey_is_minor': False,
        'localkey': 'iii',
        'localkey_is_minor': True,
        'relativeroot': pd.NA,
    }

    # A few ad-hoc
    key_tpc = Key.from_series(pd.Series(key_dict), PitchType.TPC)
    key_midi = Key.from_series(pd.Series(key_dict), PitchType.MIDI)
    assert key_tpc.mode == KeyMode.MINOR == key_midi.mode
    assert key_tpc.tonic == hc.TPC_C + hc.ACCIDENTAL_ADJUSTMENT[PitchType.TPC]
    assert key_midi.tonic == 1

    key_dict['globalkey_is_minor'] = True
    key_tpc = Key.from_series(pd.Series(key_dict), PitchType.TPC)
    key_midi = Key.from_series(pd.Series(key_dict), PitchType.MIDI)
    assert key_tpc.mode == KeyMode.MINOR == key_midi.mode
    assert key_tpc.tonic == hc.TPC_C
    assert key_midi.tonic == 0

    key_dict['localkey_is_minor'] = False
    key_tpc = Key.from_series(pd.Series(key_dict), PitchType.TPC)
    key_midi = Key.from_series(pd.Series(key_dict), PitchType.MIDI)
    assert key_tpc.mode == KeyMode.MAJOR == key_midi.mode
    assert key_tpc.tonic == hc.TPC_C
    assert key_midi.tonic == 0

    key_dict['localkey'] = 'ii'
    key_tpc = Key.from_series(pd.Series(key_dict), PitchType.TPC)
    key_midi = Key.from_series(pd.Series(key_dict), PitchType.MIDI)
    assert key_tpc.mode == KeyMode.MAJOR == key_midi.mode
    assert key_tpc.tonic == hc.TPC_C + 5
    assert key_midi.tonic == 11

    key_dict['globalkey'] = 'C'
    key_tpc = Key.from_series(pd.Series(key_dict), PitchType.TPC)
    key_midi = Key.from_series(pd.Series(key_dict), PitchType.MIDI)
    assert key_tpc.mode == KeyMode.MAJOR == key_midi.mode
    assert key_tpc.tonic == hc.TPC_C + 2
    assert key_midi.tonic == 2

    key_values = {
        'globalkey': ['A', 'B#', 'Bb', 'C'],
        'globalkey_is_minor': [False, True],
        'localkey': ['iii', 'i' ,'bV'],
        'localkey_is_minor': [False, True],
    }

    for key, values in key_values.items():
        for value in values:
            key_dict[key] = value
            key_series = pd.Series(key_dict)
            for pitch_type in PitchType:
                for do_relative in [False, True]:
                    key = Key.from_series(key_series, pitch_type, do_relative=do_relative)
                    check_equals(key_dict, key, pitch_type, do_relative)

    # Try with localkey minor, relatives major
    key_dict['localkey_is_minor'] = True
    for root_symbol in ['I', 'bII', '#III', 'V', 'bVI', 'bVII']:
        initial_interval_tpc = hu.get_interval_from_numeral(
            root_symbol, KeyMode.MINOR, PitchType.TPC
        )
        initial_interval_midi = hu.get_interval_from_numeral(
            root_symbol, KeyMode.MINOR, PitchType.MIDI
        )
        relative_interval_tpc = hu.get_interval_from_numeral(
            root_symbol, KeyMode.MAJOR, PitchType.TPC
        )
        relative_interval_midi = hu.get_interval_from_numeral(
            root_symbol, KeyMode.MAJOR, PitchType.MIDI
        )
        for repeats in range(1, 4):
            if repeats == 1:
                relative_root = root_symbol
            else:
                relative_root = '/'.join([root_symbol] * repeats)
            interval_tpc = initial_interval_tpc + (repeats - 1) * relative_interval_tpc
            interval_midi = initial_interval_midi + (repeats - 1) * relative_interval_midi
            key_series = pd.Series(key_dict)
            old_key_tpc = Key.from_series(key_series, PitchType.TPC, do_relative=True)
            old_key_midi = Key.from_series(key_series, PitchType.MIDI, do_relative=True)
            key_series['relativeroot'] = relative_root
            key_tpc = Key.from_series(key_series, PitchType.TPC, do_relative=True)
            key_midi = Key.from_series(key_series, PitchType.MIDI, do_relative=True)
            target_tpc = old_key_tpc.tonic + interval_tpc
            if target_tpc < 0 or target_tpc >= hc.NUM_PITCHES[PitchType.TPC]:
                assert key_tpc is None
            else:
                assert key_tpc.tonic == old_key_tpc.tonic + interval_tpc
            target_midi = (old_key_midi.tonic + interval_midi) % hc.NUM_PITCHES[PitchType.MIDI]
            assert key_midi.tonic == target_midi

    # Try with localkey major, relatives minor
    key_dict['localkey_is_minor'] = False
    for root_symbol in ['i', 'bii', '#iii', 'v', 'bvi', 'bvii']:
        initial_interval_tpc = hu.get_interval_from_numeral(
            root_symbol, KeyMode.MAJOR, PitchType.TPC
        )
        initial_interval_midi = hu.get_interval_from_numeral(
            root_symbol, KeyMode.MAJOR, PitchType.MIDI
        )
        relative_interval_tpc = hu.get_interval_from_numeral(
            root_symbol, KeyMode.MINOR, PitchType.TPC
        )
        relative_interval_midi = hu.get_interval_from_numeral(
            root_symbol, KeyMode.MINOR, PitchType.MIDI
        )
        for repeats in range(1, 4):
            if repeats == 1:
                relative_root = root_symbol
            else:
                relative_root = '/'.join([root_symbol] * repeats)
            interval_tpc = initial_interval_tpc + (repeats - 1) * relative_interval_tpc
            interval_midi = initial_interval_midi + (repeats - 1) * relative_interval_midi
            key_series = pd.Series(key_dict)
            old_key_tpc = Key.from_series(key_series, PitchType.TPC, do_relative=True)
            old_key_midi = Key.from_series(key_series, PitchType.MIDI, do_relative=True)
            key_series['relativeroot'] = relative_root
            key_tpc = Key.from_series(key_series, PitchType.TPC, do_relative=True)
            key_midi = Key.from_series(key_series, PitchType.MIDI, do_relative=True)
            target_tpc = old_key_tpc.tonic + interval_tpc
            if target_tpc < 0 or target_tpc >= hc.NUM_PITCHES[PitchType.TPC]:
                assert key_tpc is None
            else:
                assert key_tpc.tonic == old_key_tpc.tonic + interval_tpc
            target_midi = (old_key_midi.tonic + interval_midi) % hc.NUM_PITCHES[PitchType.MIDI]
            assert key_midi.tonic == target_midi


def test_score_piece():
    pass