import bisect
import logging
import re
from fractions import Fraction
from pathlib import Path
from typing import List, Tuple, Union

import pandas as pd
from ms3 import Score

from harmonic_inference.data.data_types import ChordType, KeyMode, PitchType
from harmonic_inference.data.piece import ScorePiece
from harmonic_inference.utils.harmonic_utils import (
    decode_relative_keys,
    get_chord_inversion,
    get_chord_one_hot_index,
    get_key_from_one_hot_index,
    get_key_one_hot_index,
    get_pitch_from_string,
)

NO_CHORD_CHANGE_REGEX = r"^\=C"
NO_KEY_CHANGE_REGEX = r"\=K"
CHORD_CHANGE_REGEX = r"\!C"
KEY_CHANGE_REGEX = r"\!K"

ACCIDENTAL_REGEX_STRING = "(#{1,2}|b{1,2})?"
ABS_PITCH_REGEX_STRING = f"[A-Ga-g]{ACCIDENTAL_REGEX_STRING}"
REL_PITCH_REGEX_STRING = (
    f"{ACCIDENTAL_REGEX_STRING}(I|II|III|IV|V|VI|VII|i|ii|iii|iv|v|vi|vii|Ger|It|Fr)"
)

CHORD_REGEX_STRING = (
    f"({ABS_PITCH_REGEX_STRING}|{REL_PITCH_REGEX_STRING})"  # Root
    r"(%|o|M|\+|\+M)?"  # Chord type
    r"(7|65|43|42|2|64|6)?"  # Fig bass (inversion)
    r"(\((((\+|-|\^|v)?(#{1,2}|b{1,2})?\d)+)\))?"  # Chord pitches
    r"(/((((#{1,2}|b{1,2})?)(I|II|III|IV|V|VI|VII|i|ii|iii|iv|v|vi|vii)/?)*))?"  # Applied root
)
CHORD_REGEX = re.compile(CHORD_REGEX_STRING)
KEY_REGEX = re.compile(f"Key: ({ABS_PITCH_REGEX_STRING}|{REL_PITCH_REGEX_STRING})")

DCML_LABEL_REGEX = re.compile(
    f"(({ABS_PITCH_REGEX_STRING}|{REL_PITCH_REGEX_STRING}).)?{CHORD_REGEX_STRING}"
)


def convert_score_positions_to_note_indexes(
    forces: Union[List[Tuple[int, Fraction]], List[Tuple[int, Fraction, int]]],
    piece: ScorePiece,
) -> Union[List[int], List[Tuple[int, int]]]:
    """
    Convert a list of forces whose positions are encoded as (mc, mn_onset) into
    one with positions encoded as note_indexes into the given piece.

    Parameters
    ----------
    forces : Union[List[Tuple[int, Fraction]], List[Tuple[int, Fraction, int]]]
        A list of forces, either (mc, mn_onset) tuples, or (mc, mn_onset, id) tuples.

    piece : ScorePiece
        A score in which to extract note indexes.

    Returns
    -------
    forces : Union[List[int], List[Tuple[int, int]]]
        A list of forces, where the (mc, mn_onset) position is converted into a note index.
    """
    note_positions = [note.onset for note in piece.get_inputs()]

    new_forces = [0] * len(forces)
    for i, force in enumerate(forces):
        index = bisect.bisect_left(note_positions, force[:2])

        if note_positions[index] != force[:2]:
            raise ValueError(
                f"Position {force[:2]} is not a note onset. Closest is {note_positions[index]}"
            )

        new_forces[i] = index if len(force) == 2 else (index, force[-1])

    return new_forces


def extract_forces_from_musescore(
    score_path: Union[str, Path]
) -> Tuple[
    Tuple[int, Fraction],
    Tuple[int, Fraction],
    Tuple[int, Fraction],
    Tuple[int, Fraction],
    Tuple[int, Fraction, Union[Tuple[int, str], Tuple[str, ChordType, int, str]], str],
    Tuple[int, Fraction, Union[int, str], str],
]:
    """
    Extract forced labels, changes, and non-changes from a Musescore3 file.

    Parameters
    ----------
    score_path : Union[str, Path]
        The path to the Musescore3 file which contains the labels.

    Returns
    -------
    chord_changes : Tuple[int, Fraction]
        Tuples of (mc, mn_onset) indicating positions at which there must be a chord change.

    chord_non_changes : Tuple[int, Fraction]
        Tuples of (mc, mn_onset) indicating positions at which there must NOT be a chord change.

    key_changes : Tuple[int, Fraction]
        Tuples of (mc, mn_onset) indicating positions at which there must be a key change.

    key_non_changes : Tuple[int, Fraction]
        Tuples of (mc, mn_onset) indicating positions at which there must NOT be a key change.

    chords : Tuple[int, Fraction, Union[Tuple[int, str], Tuple[str, ChordType, int, str]], str]
        Tuples of (mc, mn_onset, chord_id, type) indicating positions at which a given chord label
        is forced. Type may be either "abs" or "rel", denoting the type of chord_id used
        If abs, chord_id is a tuple containing the one-hot chord id and a string of the changes.
        If rel, chord_id is a tuple containing the (string) relative root, the chord type,
        the inversion, and a string of the changes.

    keys : Tuple[int, Fraction, Union[int, str], str]
        Tuples of (mc, mn_onset, key_id, type) indicating positions at which a given key label
        is forced. Type may be either "abs" or "rel", denoting the type of key_id used.
        If abs, the key_id is a one-hot key id. If rel, a label string is given in that slot
        instead (since RN label intervals are dependant on the local mode). Such label strings
        are formatted like relativeroots (slash-separated Roman numerals).
    """
    score = Score(score_path)

    labels: pd.DataFrame = score.annotations.get_labels()

    chord_changes = pd.concat(
        [labels.loc[labels["label"].str.fullmatch(CHORD_CHANGE_REGEX), ["mc", "mn_onset"]]]
    )
    chord_changes = [
        (mc, mn_onset) for mc, mn_onset in zip(chord_changes["mc"], chord_changes["mn_onset"])
    ]

    chord_non_changes = pd.concat(
        [labels.loc[labels["label"].str.fullmatch(NO_CHORD_CHANGE_REGEX), ["mc", "mn_onset"]]]
    )
    chord_non_changes = [
        (mc, mn_onset)
        for mc, mn_onset in zip(chord_non_changes["mc"], chord_non_changes["mn_onset"])
    ]

    key_changes = pd.concat(
        [labels.loc[labels["label"].str.fullmatch(KEY_CHANGE_REGEX), ["mc", "mn_onset"]]]
    )
    key_changes = [
        (mc, mn_onset) for mc, mn_onset in zip(key_changes["mc"], key_changes["mn_onset"])
    ]

    key_non_changes = pd.concat(
        [labels.loc[labels["label"].str.fullmatch(NO_KEY_CHANGE_REGEX), ["mc", "mn_onset"]]]
    )
    key_non_changes = [
        (mc, mn_onset) for mc, mn_onset in zip(key_non_changes["mc"], key_non_changes["mn_onset"])
    ]

    chord_ids = []
    key_ids = []

    keys = pd.concat(
        [labels.loc[labels["label"].str.fullmatch(KEY_REGEX), ["mc", "mn_onset", "label"]]]
    )
    for mc, mn_onset, label in zip(keys["mc"], keys["mn_onset"], keys["label"]):
        tonic_str = KEY_REGEX.match(label).group(1)
        mode = KeyMode.MINOR if tonic_str.islower() else KeyMode.MAJOR

        if any([numeral in tonic_str for numeral in ["v", "i"]]):
            id_type = "rel"
            key_id = tonic_str

        else:
            id_type = "abs"
            key_id = get_key_one_hot_index(
                mode, get_pitch_from_string(tonic_str, PitchType.TPC), PitchType.TPC
            )

        key_ids.append(mc, mn_onset, key_id, id_type)

    # Can include key and chord, plus relative roots (also modeled as key changes)
    dcml_labels = pd.concat(
        [labels.loc[labels["label"].str.fullmatch(DCML_LABEL_REGEX), ["mc", "mn_onset", "label"]]]
    )
    for mc, mn_onset, label in zip(
        dcml_labels["mc"], dcml_labels["mn_onset"], dcml_labels["label"]
    ):
        if "." in label:
            # Label has chord and key: handle the key here and save only the chord label
            idx = label.index(".")
            tonic_str = label[:idx]
            label = label[idx + 1 :]

            # Handle key label
            if any([numeral in tonic_str for numeral in ["v", "i"]]):
                id_type = "rel"
                key_id = tonic_str

            else:
                id_type = "abs"
                key_id = get_key_one_hot_index(
                    mode, get_pitch_from_string(tonic_str, PitchType.TPC), PitchType.TPC
                )

            key_ids.append((mc, mn_onset, key_id, id_type))

        # Label is now only a chord label. We can match it to get groups.
        chord_match = CHORD_REGEX.match(label)
        root_string = chord_match.group(1)
        type_string = chord_match.group(5)
        figbass_string = chord_match.group(6)
        changes_string = chord_match.group(7)
        relroot_string = chord_match.group(13)

        # Get chord features
        is_minor = root_string.islower()
        inversion = get_chord_inversion(figbass_string)
        if figbass_string in ["7", "65", "43", "2"]:
            # 7th chord
            chord_type = {
                "o": ChordType.DIM7,
                "%": ChordType.HALF_DIM7,
                "+": ChordType.AUG_MIN7,
                "+M": ChordType.AUG_MAJ7,
                "M": ChordType.MIN_MAJ7 if is_minor else ChordType.MAJ_MAJ7,
                "": ChordType.MIN_MIN7 if is_minor else ChordType.MAJ_MIN7,
            }[type_string]
        else:
            # Triad
            chord_type = {
                "o": ChordType.DIMINISHED,
                "+": ChordType.AUGMENTED,
                "": ChordType.MINOR if is_minor else ChordType.MAJOR,
            }[type_string]

        if any([numeral in root_string for numeral in ["v", "i"]]):
            id_type = "rel"
            chord_id = (root_string, chord_type, inversion, changes_string)

        else:
            id_type = "abs"
            chord_id = (
                get_chord_one_hot_index(
                    chord_type,
                    get_pitch_from_string(root_string, PitchType.TPC),
                    PitchType.TPC,
                    inversion=inversion,
                ),
                changes_string,
            )

        chord_ids.append(mc, mn_onset, chord_id, id_type)

        # Handle relroot_string (add to existing key force)
        found = False
        for i, (key_mc, key_mn_onset, key_id, key_id_type) in enumerate(key_ids):
            if key_mc == mc and key_mn_onset == mn_onset:
                found = True
                if key_id_type == "abs":
                    tonic, mode = get_key_from_one_hot_index(key_id, PitchType.TPC)
                    tonic, mode = decode_relative_keys(relroot_string, tonic, mode, PitchType.TPC)
                    key_ids[i] = (
                        key_mc,
                        key_mn_onset,
                        get_key_one_hot_index(mode, tonic, PitchType.TPC),
                        key_id_type,
                    )

                else:
                    # Relative: Just append relroot to previous relative key
                    key_ids[i] = (key_mc, key_mn_onset, f"{relroot_string}/{key_id}", key_id_type)

        if not found:
            # Here, there is no real way to represent this during the search, so it is ignored
            logging.warning(
                "Ignoring relative root of forced %s (relative roots are ignored for forces)",
                label,
            )

    return (
        chord_changes,
        chord_non_changes,
        key_changes,
        key_non_changes,
        chord_ids,
        key_ids,
    )
