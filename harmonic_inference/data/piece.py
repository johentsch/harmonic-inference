"""A class storing a musical piece from score, midi, or audio format."""
import bisect
from collections import defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Dict, List, Tuple, Union

import music21
import numpy as np
import pandas as pd
from music21.converter import parse
from tqdm import tqdm

import harmonic_inference.utils.rhythmic_utils as ru
from harmonic_inference.data.chord import Chord
from harmonic_inference.data.data_types import NO_REDUCTION, ChordType, PieceType, PitchType
from harmonic_inference.data.key import Key
from harmonic_inference.data.note import Note


def get_reduction_mask(inputs: List[Union[Chord, Key]], kwargs: Dict = None) -> List[bool]:
    """
    Return a boolean mask that will remove repeated inputs when applied to the given inputs list
    as inputs[mask].

    Parameters
    ----------
    inputs : List[Union[Chord, Key]]
        A List of either Chord or Key objects.
    kwargs : Dict
        A Dictionary of kwargs to pass along to each given input's is_repeated() function.

    Returns
    -------
    mask : List[bool]
        A boolean mask that will remove repeated inputs when applied to the given inputs list
        as inputs = inputs[mask].
    """
    if kwargs is None:
        kwargs = {}

    mask = np.full(len(inputs), True, dtype=bool)

    for prev_index, (prev_obj, next_obj) in enumerate(zip(inputs[:-1], inputs[1:])):
        if next_obj.is_repeated(prev_obj, **kwargs):
            mask[prev_index + 1] = False

    return mask


def get_chord_note_input(
    notes: List[Note],
    measures_df: pd.DataFrame,
    chord_onset: Union[float, Tuple[int, Fraction]],
    chord_offset: Union[float, Tuple[int, Fraction]],
    chord_duration: Union[float, Fraction],
    change_index: int,
    onset_index: int,
    offset_index: int,
    window: int,
    duration_cache: np.array = None,
    chord: Chord = None,
) -> np.array:
    """
    Get an np.array or input vectors relative to a given chord.

    Parameters
    ----------
    notes : List[Note]
        A List of all of the Notes in the Piece.
    measures_df : pd.DataFrame
        The measures_df for this particular Piece.
    chord_onset : Union[float, Tuple[int, Fraction]]
        The onset location of the chord.
    chord_offset : Union[float, Tuple[int, Fraction]]
        The offset location of the chord.
    chord_duration : Union[float, Fraction]
        The duration of the chord.
    change_index : int
        The index of the note matching the onset time of the chord.
    onset_index : int
        The index of the first note of the chord.
    offset_index : int
        The index of the last note of the chord.
    window : int
        The number of notes to pad on each end of the chord's notes. If this goes past the
        bounds of the given notes list, the remaining vectors will contain only 0.
    duration_cache : np.array
        The duration from each note's onset time to the next note's onset time,
        generated by get_duration_cache(...).
    chord : Chord
        The chord the notes belong to, if not None.

    Returns
    -------
    chord_input : np.array
        The input note vectors for this chord.
    """
    # Chord aligns with duration cache
    chord_onset_aligns = chord is None or chord.onset == chord_onset
    chord_offset_aligns = chord is None or chord.offset == chord_offset

    # Add window
    window_onset_index = onset_index - window
    window_offset_index = offset_index + window

    # Shift duration_cache
    dur_from_prevs = [None] + list(duration_cache)
    dur_to_nexts = list(duration_cache)

    # Get the notes within the window
    first_note_index = max(window_onset_index, 0)
    last_note_index = min(window_offset_index, len(notes))
    chord_notes = notes[first_note_index:last_note_index]

    # Get all note vectors within the window
    pitch_list = [(note.octave, note.get_midi_note_number()) for note in chord_notes]
    min_pitch = min(pitch_list)
    max_pitch = max(pitch_list)

    if duration_cache is None or not chord_onset_aligns:
        note_onsets = np.full(len(chord_notes), None)
    else:
        note_onsets = []
        for note_index in range(first_note_index, last_note_index):
            if note_index < change_index:
                note_onset = -np.sum(duration_cache[note_index:change_index])
            elif note_index > change_index:
                note_onset = np.sum(duration_cache[change_index:note_index])
            else:
                note_onset = Fraction(0)
            note_onsets.append(note_onset)

    note_vectors = np.vstack(
        [
            note.to_vec(
                chord_onset=chord_onset if chord_onset_aligns else chord.onset,
                chord_offset=chord_offset if chord_offset_aligns else chord.offset,
                chord_duration=chord_duration,
                measures_df=measures_df,
                min_pitch=min_pitch,
                max_pitch=max_pitch,
                note_onset=note_onset,
                dur_from_prev=from_prev,
                dur_to_next=to_next,
            )
            for note, note_onset, from_prev, to_next in zip(
                chord_notes,
                note_onsets,
                dur_from_prevs[first_note_index:last_note_index],
                dur_to_nexts[first_note_index:last_note_index],
            )
        ]
    )

    # Place the note vectors within the final tensor and return
    chord_input = np.zeros((window_offset_index - window_onset_index, note_vectors.shape[1]))
    start = 0 + (first_note_index - window_onset_index)
    end = len(chord_input) - (window_offset_index - last_note_index)
    chord_input[start:end] = note_vectors
    return chord_input


def get_range_start(onset: Union[float, Tuple[int, Fraction]], notes: List[Note]) -> int:
    """
    Get the index of the first note whose offset is after the given range onset.

    Parameters
    ----------
    onset : Union[float, Tuple[int, Fraction]]
        The onset time of a range.
    notes : List[Note]
        A List of the Notes of a piece.

    Returns
    -------
    start : int
        The index of the first note whose offset is after the given range's onset.
    """
    for note_id, note in enumerate(notes):
        if note.onset >= onset or note.offset > onset:
            return note_id

    return len(notes)


class Piece:
    """
    A single musical piece, which can be from score, midi, or audio.
    """

    def __init__(self, data_type: PieceType, name: str = None):
        """
        Create a new musical Piece object of the given data type.

        Parameters
        ----------
        data_type : PieceType
            The data type of the piece.
        name : str
            The name of the piece, an optional identifier.
        """
        self.DATA_TYPE = data_type
        self.name = name

    def get_inputs(self) -> List[Note]:
        """
        Get a list of the inputs for this Piece.

        Returns
        -------
        inputs : np.array
            A List of the inputs for this musical piece.
        """
        raise NotImplementedError

    def get_chord_change_indices(self) -> List[int]:
        """
        Get a List of the indexes (into the input list) at which there are chord changes.

        Returns
        -------
        chord_change_indices : np.array[int]
            The indices (into the inputs list) at which there is a chord change.
        """
        raise NotImplementedError

    def get_chord_ranges(self) -> List[Tuple[int, int]]:
        """
        Get a List of the indexes (into the input list) that contain inputs for each chord.

        Returns
        -------
        ranges : List[Tuple[int, int]]
            The indexes (into the input list) that contain inputs for each chord.
        """
        raise NotImplementedError

    def get_chords(self) -> List[Chord]:
        """
        Get a List of the chords in this piece.

        Returns
        -------
        chords : List[Chord]
            The chords present in this piece. The ith chord occurs for the inputs between
            chord_change_index i (inclusive) and i+1 (exclusive).
        """
        raise NotImplementedError

    def get_chords_within_range(self, start: int = 0, stop: int = None) -> List[Chord]:
        """
        Get a List of the chords in this piece between the given bounds.

        Parameters
        ----------
        start : int
            Return chords starting at this input index. The first chord returned will be
            the chord that is sounding during this input vector.

        stop : int
            Return chords up to this index. The last chord returned will be the chord that is
            sounding during index stop - 1. If None, all chords until the end of the list
            are returned.

        Returns
        -------
        chords : List[Chord]
            The chords present in this piece between the given bounds.
        """
        assert stop is None or stop >= start, "stop must be None or >= start"

        chords = self.get_chords()
        chord_change_indices = self.get_chord_change_indices()

        start_index = bisect.bisect_left(chord_change_indices, start)
        if start_index == len(chord_change_indices) or chord_change_indices[start_index] != start:
            # Subtract 1 to get end of partial chord if exact match is not found
            start_index -= 1

        if stop is None:
            return chords[start_index:]

        end_index = bisect.bisect_left(chord_change_indices, stop, lo=max(start_index, 0))

        return chords[start_index:end_index]

    def get_chord_note_inputs(
        self,
        window: int = 2,
        ranges: List[Tuple[int, int]] = None,
        change_indices: List[int] = None,
    ) -> np.array:
        """
        Get a list of the note input vectors for each chord in this piece, using an optional
        window on both sides. The ith element in the returned array will be an nd-array of
        size (2 * window + num_notes, note_vector_length).

        Parameters
        ----------
        window : int
            Add this many neighboring notes to each side of each input tensor. Fill with 0s if
            this goes beyond the bounds of all notes.
        ranges : List[Tuple[int, int]]
            A List of chord ranges to use to get the inputs, if not using the ground truth
            chord symbols themselves.
        change_indices : List[int]
            A List of the note whose onset is the onset of each chord range.

        Returns
        -------
        chord_inputs : np.array
            The input note tensor for each chord in this piece.
        """
        raise NotImplementedError

    def get_duration_cache(self) -> List[Fraction]:
        """
        Get a List of the distance from the onset of each input of this Piece to the
        following input. The last value will be the distance from the onset of the last
        input to the offset of the last chord.

        Returns
        -------
        duration_cache : np.array[Fraction]
            A list of the distance from the onset of each input to the onset of the
            following input.
        """
        raise NotImplementedError

    def get_key_change_indices(self) -> List[int]:
        """
        Get a List of the indexes (into the chord list) at which there are key changes.

        Returns
        -------
        key_change_indices : np.array[int]
            The indices (into the chords list) at which there is a key change.
        """
        raise NotImplementedError

    def get_key_change_input_indices(self) -> List[int]:
        """
        Get a List of the indexes (into the input list) at which there are key changes.

        Returns
        -------
        key_change_indices : List[int]
            The indices (into the input list) at which there is a key change.
        """
        chord_changes = self.get_chord_change_indices()
        return [chord_changes[i] for i in self.get_key_change_indices()]

    def get_keys(self) -> List[Key]:
        """
        Get a List of the keys in this piece.

        Returns
        -------
        keys : np.array[Key]
            The keys present in this piece. The ith key occurs for the chords between
            key_change_index i (inclusive) and i+1 (exclusive).
        """
        raise NotImplementedError


class ScorePiece(Piece):
    """
    A single musical piece, in score format.
    """

    def __init__(
        self,
        measures_df: pd.DataFrame,
        notes: List[Note],
        chords: List[Chord],
        keys: List[Key],
        chord_changes: List[int],
        chord_ranges: List[Tuple[int, int]],
        key_changes: List[int],
        name: str = None,
    ):
        """
        Create a new ScorePiece.

        Parameters
        ----------
        measures_df : pd.DataFrame
            A DataFrame containing information about the measures in the piece.
        notes : List[Note]
            A list of the notes of the piece.
        chords : List[Chord]
            A list of the chords of the piece.
        keys : List[Key]
            A list of the keys of the piece.
        chord_changes : List[int]
            A list of the indexes at which there are chord changes in the piece.
        chord_ranges : List[Tuple[int, int]]
            A list of the [start, end) indexes of the chords of the piece.
        key_changes : List[int]
            A list of the indexes at which there are key changes in the piece.
        name : str
            A string identifier for this piece.
        """
        super().__init__(PieceType.SCORE, name=name)
        self.duration_cache = None

        self.measures_df = measures_df

        self.notes = np.array(notes)
        self.chords = np.array(chords)
        self.keys = np.array(keys)
        self.chord_changes = np.array(chord_changes)
        self.chord_ranges = np.array(chord_ranges)
        self.key_changes = np.array(key_changes)

    def get_duration_cache(self):
        if self.duration_cache is None:
            fake_last_note = Note(
                0, 0, self.chords[-1].offset, 0, Fraction(0), (0, Fraction(0)), 0, PitchType.TPC
            )

            self.duration_cache = np.array(
                [
                    ru.get_range_length(prev_note.onset, next_note.onset, self.measures_df)
                    for prev_note, next_note in zip(
                        self.notes, list(self.notes[1:]) + [fake_last_note]
                    )
                ]
            )

        return self.duration_cache

    def get_inputs(self) -> List[Note]:
        return self.notes

    def get_chord_change_indices(self) -> List[int]:
        return self.chord_changes

    def get_chord_ranges(self) -> List[Tuple[int, int]]:
        return self.chord_ranges

    def get_chords(self) -> List[Chord]:
        return self.chords

    def get_chord_note_inputs(
        self,
        window: int = 2,
        ranges: List[Tuple[int, int]] = None,
        change_indices: List[int] = None,
    ):
        use_real_chords = False

        if ranges is None:
            use_real_chords = True
            ranges = self.get_chord_ranges()
        if change_indices is None:
            use_real_chords = True
            change_indices = self.get_chord_change_indices()

        chords = self.get_chords() if use_real_chords else [None] * len(ranges)

        last_offset = self.chords[-1].offset
        duration_cache = self.get_duration_cache()

        chord_note_inputs = []
        for (onset_index, offset_index), change_index, chord in tqdm(
            zip(ranges, change_indices, chords),
            desc="Generating chord classification inputs",
            total=len(ranges),
        ):
            duration = (
                np.sum(duration_cache[change_index:offset_index])
                if chord is None
                else chord.duration
            )
            onset = self.notes[change_index].onset
            try:
                offset = self.notes[offset_index].onset
            except IndexError:
                offset = last_offset

            chord_note_inputs.append(
                get_chord_note_input(
                    self.notes,
                    self.measures_df,
                    onset,
                    offset,
                    duration,
                    change_index,
                    onset_index,
                    offset_index,
                    window,
                    duration_cache=duration_cache,
                    chord=chord,
                )
            )

        return chord_note_inputs

    def get_key_change_indices(self) -> List[int]:
        return self.key_changes

    def get_keys(self) -> List[Key]:
        return self.keys

    def to_dict(self) -> Dict[str, List]:
        """
        Return a dictionary of this ScorePiece, that can be used to load it quickly
        from json, for example.

        Returns
        -------
        piece : Dict[str, List]
            A dictionary of this Piece.
        """
        return {
            "notes": [note.to_dict() for note in self.get_inputs()],
            "chords": [chord.to_dict() for chord in self.get_chords()],
            "keys": [key.to_dict() for key in self.get_keys()],
            "chord_changes": self.get_chord_change_indices(),
            "chord_ranges": self.get_chord_ranges(),
            "key_changes": self.get_key_change_indices(),
        }


def get_score_piece_from_dict(
    measures_df: pd.DataFrame,
    piece_dict: Dict,
    name: str = None,
) -> ScorePiece:
    """
    Create and return a ScorePiece from a dictionary, create by ScorePiece.to_dict().

    Parameters
    ----------
    measures_df : pd.DataFrame
        A measures_df is required for metrical information when getting chord note inputs.
    piece_dict : Dict
        The dictionary created by ScorePiece.to_dict().
    name : str
        A string identifier for this piece.

    Returns
    -------
    piece : ScorePiece
        The ScorePiece, loaded from the dict.
    """
    return ScorePiece(
        measures_df,
        [Note(**note) for note in piece_dict["notes"]],
        [Chord(**chord) for chord in piece_dict["chords"]],
        [Key(**key) for key in piece_dict["keys"]],
        piece_dict["chord_changes"],
        piece_dict["chord_ranges"],
        piece_dict["key_changes"],
        name=name,
    )


def get_score_piece_from_data_frames(
    notes_df: pd.DataFrame,
    chords_df: pd.DataFrame,
    measures_df: pd.DataFrame,
    chord_reduction: Dict[ChordType, ChordType] = NO_REDUCTION,
    use_inversions: bool = True,
    use_relative: bool = True,
    name: str = None,
) -> ScorePiece:
    """
    Create a ScorePiece object from the given 3 pandas DataFrames.

    Parameters
    ----------
    notes_df : pd.DataFrame
        A DataFrame containing information about the notes contained in the piece.
    chords_df : pd.DataFrame
        A DataFrame containing information about the chords contained in the piece.
    measures_df : pd.DataFrame
        A DataFrame containing information about the measures in the piece.
    chord_reduction : Dict[ChordType, ChordType]
        A mapping from every possible ChordType to a reduced ChordType: the type that chord
        should be stored as. This can be used, for example, to store each chord as its triad.
    use_inversions : bool
        True to store inversions in the chord symbols. False to ignore them.
    use_relative : bool
        True to treat relative roots as new local keys. False to treat them as chord symbols
        within the annotated local key.
    name : str
        A string identifier for this piece.

    Returns
    -------
    score_piece : ScorePiece
        The ScorePiece, loaded from the dataframes.
    """
    levels_cache = defaultdict(dict)
    notes_list = np.array(
        [
            [note, note_id]
            for note_id, note in enumerate(
                notes_df.apply(
                    Note.from_series,
                    axis="columns",
                    measures_df=measures_df,
                    pitch_type=PitchType.TPC,
                    levels_cache=levels_cache,
                )
            )
            if note is not None
        ]
    )
    notes, note_ilocs = np.hsplit(notes_list, 2)
    notes = np.squeeze(notes)
    note_ilocs = np.squeeze(note_ilocs).astype(int)

    chords_list = np.array(
        [
            [chord, chord_id]
            for chord_id, chord in enumerate(
                chords_df.apply(
                    Chord.from_series,
                    axis="columns",
                    measures_df=measures_df,
                    pitch_type=PitchType.TPC,
                    levels_cache=levels_cache,
                    reduction=chord_reduction,
                    use_inversion=use_inversions,
                    use_relative=use_relative,
                )
            )
            if chord is not None
        ]
    )
    chords_list, chord_ilocs = np.hsplit(chords_list, 2)
    chords_list = np.squeeze(chords_list)
    chord_ilocs = np.squeeze(chord_ilocs).astype(int)

    # Remove accidentally repeated chords
    non_repeated_mask = get_reduction_mask(chords_list, kwargs={"use_inversion": use_inversions})
    chords = []
    for chord, mask in zip(chords_list, non_repeated_mask):
        if mask:
            chords.append(chord)
        else:
            chords[-1].merge_with(chord)
    chords = np.array(chords)
    chord_ilocs = chord_ilocs[non_repeated_mask]

    # The index of the notes where there is a chord change
    chord_changes = np.zeros(len(chords), dtype=int)
    note_index = 0
    for chord_index, chord in enumerate(chords):
        while note_index + 1 < len(notes) and notes[note_index].onset < chord.onset:
            note_index += 1
        chord_changes[chord_index] = note_index

    # The note input ranges for each chord
    chord_ranges = [
        (get_range_start(chord.onset, notes), end)
        for chord, end in zip(chords, list(chord_changes[1:]) + [len(notes)])
    ]

    key_cols = chords_df.loc[
        chords_df.index[chord_ilocs],
        [
            "globalkey",
            "globalkey_is_minor",
            "localkey_is_minor",
            "localkey",
            "relativeroot",
        ],
    ]
    key_cols = key_cols.fillna("-1")
    changes = key_cols.ne(key_cols.shift()).fillna(True)

    key_changes = np.arange(len(changes))[changes.any(axis=1)]
    keys_list = np.array(
        [
            key
            for key in chords_df.loc[chords_df.index[chord_ilocs[key_changes]]].apply(
                Key.from_series, axis="columns", tonic_type=PitchType.TPC
            )
            if key is not None
        ]
    )

    # Remove accidentally repeated keys
    non_repeated_mask = get_reduction_mask(keys_list, kwargs={"use_relative": use_relative})
    keys = keys_list[non_repeated_mask]
    key_changes = key_changes[non_repeated_mask]

    return ScorePiece(
        measures_df,
        notes,
        chords,
        keys,
        chord_changes,
        chord_ranges,
        key_changes,
        name=name,
    )


def get_measures_df_from_music21_score(m21_score: music21.stream.Score) -> pd.DataFrame:
    """
    Compute and return a measures_df (that can be used to create a ScorePiece) from a
    parsed music21 Score.

    Parameters
    ----------
    m21_score : music21.stream.Score
        A music21 Score that has been parsed already.

    Returns
    -------
    measures_df : pd.DataFrame
        A measures_df with the following columns:
            'mc' (int): The measure index.
            'timesig' (str): The time signature of each measure.
            'start' (Fraction): The "offset" position at the start of each measure, in
                                whole notes since the beginning of the piece.
            'act_dur' (Fraction): The duration of the measure, in whole notes.
            'offset' (Fraction): The starting position of this measure, in whole notes
                                 after the most recent downbeat.
            'next' (int): The measure index of the measure that follows each one.
    """
    # Lists to compute and add to measures_df
    time_signatures = []
    starts = []
    lengths = []
    df_offsets = []
    mns = []

    # Lists that we can pre-compute
    mcs = list(range(len(m21_score.measureOffsetMap())))
    nexts = mcs[1:] + [pd.NA]

    # The start of the 2nd measure (the first full measure in the case of an anacrusis)
    ts_epoch = Fraction(list(m21_score.measureOffsetMap().keys())[1] / 4)

    # Default time signature
    time_signature = "4/4"
    ts_duration = Fraction(time_signature)

    # Go through the measures and add them to the tracking lists
    for mc, (offset, measures_list) in enumerate(m21_score.measureOffsetMap().items()):
        offset = Fraction(offset) / 4
        measure = measures_list[0]

        if measure.timeSignature is not None:
            if measure.timeSignature.ratioString != time_signature:
                # Time Signature change
                time_signature = measure.timeSignature.ratioString
                ts_duration = Fraction(time_signature)

                # Reset the ts_epoch to this location
                if mc != 0:
                    ts_epoch = offset

        if lengths:
            # Set the length of each bar to the difference between consecutive measure offsets
            lengths[-1] = offset - starts[-1]
        # Default (used only for the last measure)
        lengths.append(Fraction(measure.duration.quarterLength) / 4)

        starts.append(offset)
        time_signatures.append(time_signature)
        df_offsets.append((offset - ts_epoch) % ts_duration)
        mns.append(measure.measureNumber)

    return pd.DataFrame(
        {
            "mc": mcs,
            "mn": mns,
            "timesig": time_signatures,
            "start": starts,
            "act_dur": lengths,
            "offset": df_offsets,
            "next": nexts,
        }
    )


def get_notes_from_music_xml(
    m21_score: music21.stream.Score,
    measures_df: pd.DataFrame,
) -> List[Note]:
    """
    Get a list of Note objects from a music21 score that has already been parsed.
    This function will flatten and remove ties.

    Parameters
    ----------
    m21_score : music21.stream.Score
        A music21 Score that has been parsed already.
    measures_df : pd.DataFrame
        A measures_df with the following columns (from get_measures_df_from_music21_score):
            'mc' (int): The measure index.
            'timesig' (str): The time signature of each measure.
            'start' (Fraction): The "offset" position at the start of each measure, in
                                whole notes since the beginning of the piece.
            'act_dur' (Fraction): The duration of the measure, in whole notes.
            'offset' (Fraction): The starting position of this measure, in whole notes
                                 after the most recent downbeat.
            'next' (int): The measure index of the measure that follows each one.

    Returns
    -------
    notes : List[Note]
        A List of the Notes present in the given music21 score.
    """
    m21_score = m21_score.flattenParts()
    m21_score = m21_score.stripTies()

    levels_cache = defaultdict(dict)
    notes = []

    for measure_mc, measures_list in enumerate(m21_score.measureOffsetMap().values()):
        measure = measures_list[0]

        for note in measure.recurse().notes:
            if note.isChord:
                chord = note
                for chord_note in chord.notes:
                    notes.append(
                        Note.from_music21(
                            chord_note,
                            measures_df,
                            measure_mc,
                            pitch_type=PitchType.TPC,
                            m21_chord=chord,
                            levels_cache=levels_cache,
                        )
                    )
            else:
                notes.append(
                    Note.from_music21(
                        note,
                        measures_df,
                        measure_mc,
                        pitch_type=PitchType.TPC,
                        levels_cache=levels_cache,
                    )
                )

    return notes


def get_score_piece_from_music_xml(
    music_xml_path: Union[str, Path],
    label_csv_path: Union[str, Path],
    name: str = None,
) -> ScorePiece:
    """
    Create a ScorePiece object from the given 3 pandas DataFrames.

    Parameters
    ----------
    music_xml_path : Union[str, Path]
        The path to a music XML score file.
    label_csv_path : Union[str, Path]
        The path to the csv label file for the given XML score.
    name : str
        A string identifier for this piece.

    Returns
    -------
    score_piece : ScorePiece
        The ScorePiece, loaded from the xml and label csv.
    """
    # Turn all paths into Path objects
    if isinstance(music_xml_path, str):
        music_xml_path = Path(music_xml_path)
    if isinstance(label_csv_path, str):
        label_csv_path = Path(label_csv_path)

    # Parse the score
    m21_score = parse(music_xml_path)
    measures_df = get_measures_df_from_music21_score(m21_score)
    notes = get_notes_from_music_xml(m21_score, measures_df)

    # Parse the labels csv
    labels_df = pd.read_csv(
        label_csv_path,
        header=None,
        names=["on", "off", "key", "degree", "type", "inv"],
        dtype={"degree": str},
        converters={"on": Fraction, "off": Fraction},
    )

    # Labels are in quarter notes, but the rest of the code uses whole notes
    labels_df["on"] /= 4
    labels_df["off"] /= 4

    # Bugfix for some pieces that start with negative numbers
    labels_df = labels_df.loc[(labels_df["on"] >= 0) & (labels_df["off"] > 0)]

    # Bugfix for duration 0 symbols
    labels_df = labels_df.loc[labels_df["off"] > labels_df["on"]]

    # Bugfix for some augmented chords adding "+" to scale degree
    for degree in range(1, 6):
        labels_df.loc[labels_df["degree"] == f"{degree}+", ["degree"]] = str(degree)

    levels_cache = defaultdict(dict)
    chords = np.array(
        [
            Chord.from_labels_csv_row(row, measures_df, PitchType.TPC, levels_cache=levels_cache)
            for _, row in labels_df.iterrows()
        ]
    )

    chord_changes = np.zeros(len(chords), dtype=int)
    note_index = 0
    for chord_index, chord in enumerate(chords):
        while note_index + 1 < len(notes) and notes[note_index].onset < chord.onset:
            note_index += 1
        chord_changes[chord_index] = note_index

    # The note input ranges for each chord
    chord_ranges = [
        (get_range_start(chord.onset, notes), end)
        for chord, end in zip(chords, list(chord_changes[1:]) + [len(notes)])
    ]

    global_key = Key.from_labels_csv_row(labels_df.iloc[0], PitchType.TPC)
    keys = np.array(
        [
            Key.from_labels_csv_row(row, PitchType.TPC, global_key=global_key)
            for _, row in labels_df.iterrows()
        ]
    )
    key_changes = np.arange(len(keys))

    # Remove accidentally repeated keys
    non_repeated_mask = get_reduction_mask(keys, kwargs={"use_relative": True})
    keys = keys[non_repeated_mask]
    key_changes = key_changes[non_repeated_mask]

    return ScorePiece(
        measures_df,
        notes,
        chords,
        keys,
        chord_changes,
        chord_ranges,
        key_changes,
        name=name,
    )
