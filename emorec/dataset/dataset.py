import abc
import warnings
from pathlib import Path
from typing import Collection, Dict, List, Mapping, Optional, Set, Tuple, Type, Union

import numpy as np
from sklearn.base import TransformerMixin
from sklearn.preprocessing import StandardScaler, label_binarize

from ..utils import PathOrStr, clip_arrays, frame_arrays, pad_arrays, transpose_time
from .backend import ARFFBackend, DatasetBackend, NetCDFBackend, RawAudioBackend
from .corpora import corpora
from .utils import parse_annotations


def _make_flat(a: np.ndarray) -> Tuple[np.ndarray, List[int]]:
    """Flattens an array of variable-length sequences."""
    slices = [x.shape[0] for x in a]
    flat = np.concatenate(a)
    return flat, slices


def _make_ragged(flat: np.ndarray, slices: Union[List[int], np.ndarray]) -> np.ndarray:
    """Returns a list of variable-length sequences."""
    indices = np.cumsum(slices)
    arrs = np.split(flat, indices[:-1], axis=0)
    return arrs


_BACKENDS: Dict[str, Type[DatasetBackend]] = {
    ".nc": NetCDFBackend,
    ".txt": RawAudioBackend,
    ".arff": ARFFBackend,
}


class Dataset(abc.ABC):
    """Abstract class representing a generic dataset consisting of a set
    of features and possibly speaker information.
    """

    _speakers = ["unknown"]
    _male_speakers: List[str] = []
    _female_speakers: List[str] = []
    _speaker_groups = []
    _corpus = ""
    _names: List[str] = []
    _features: List[str] = []
    _x: np.ndarray

    def __init__(self, path: PathOrStr, speaker_path: Optional[PathOrStr] = None):
        path = Path(path)
        try:
            self.backend = _BACKENDS[path.suffix](path)
        except KeyError:
            raise NotImplementedError(f"Unknown filetype '{path.suffix}'.")

        self._corpus = self.backend.corpus
        self._names = self.backend.names
        self._features = self.backend.feature_names
        self._x = self.backend.features

        if speaker_path:
            speaker_path = Path(speaker_path)
            spk_dict = parse_annotations(speaker_path, dtype=str)
            self._speakers = sorted(set(spk_dict[n] for n in self.names))
            self._speaker_indices = np.array(
                [self.speakers.index(spk_dict[n]) for n in self.names]
            )
            # TODO: remove when unnecessary
            if self.corpus.lower() in corpora:
                self._male_speakers = corpora[self.corpus.lower()].male_speakers
                self._female_speakers = corpora[self.corpus.lower()].female_speakers
                self._speaker_groups = corpora[self.corpus.lower()].speaker_groups
        else:
            self._speaker_indices = np.zeros(len(self.names), dtype=int)

        self._speaker_counts = np.bincount(
            self.speaker_indices, minlength=len(self.speakers)
        )
        if any(x == 0 for x in self.speaker_counts):
            warnings.warn("Some speakers have no corresponding instances.")

        self._reset_male_female_indices()

        if not self.speaker_groups:
            self._speaker_groups = [{s} for s in self.speakers]
        self._reset_speaker_group_indices()

    def _reset_male_female_indices(self):
        if self.male_speakers and self.female_speakers:
            self._male_indices = np.isin(
                np.array(self.speakers)[self.speaker_indices], self.male_speakers
            ).nonzero()
            self._female_indices = np.isin(
                np.array(self.speakers)[self.speaker_indices], self.female_speakers
            ).nonzero()

    def _reset_speaker_group_indices(self):
        speaker_to_group = np.empty(len(self.speakers), dtype=int)
        for i, group in enumerate(self.speaker_groups):
            for sp in group:
                speaker_to_group[self.speakers.index(sp)] = i
        self._speaker_group_indices = speaker_to_group[self.speaker_indices]

    def remove_instances(self, keep: Collection[str]):
        """Remove instances not in keep. Recalculate speakers, groups,
        etc.
        """
        idx = [i for i, x in enumerate(self.names) if x in keep]
        self._names = [self.names[i] for i in idx]
        self._x = self.x[idx]
        new_sp = np.unique(self.speaker_indices[idx])
        if len(new_sp) < len(self.speakers):
            new_speakers = [self.speakers[x] for x in new_sp]
            self._speaker_indices = np.array(
                [
                    new_speakers.index(self.speakers[self.speaker_indices[i]])
                    for i in idx
                ]
            )
            self._speakers = new_speakers

            new_sp_grp = np.unique(self.speaker_group_indices[idx])
            self._speaker_groups = [self.speaker_groups[x] for x in new_sp_grp]
            self._reset_speaker_group_indices()

            if self.male_speakers and self.female_speakers:
                self._male_speakers = [
                    s for s in self.male_speakers if s in self.speakers
                ]
                self._female_speakers = [
                    s for s in self.female_speakers if s in self.speakers
                ]
                self._reset_male_female_indices()
        else:
            self._speaker_indices = self.speaker_indices[idx]
            self._speaker_group_indices = self.speaker_group_indices[idx]
            self._male_indices = self.male_indices[idx]
            self._female_indices = self.female_indices[idx]

    def normalise(
        self, normaliser: TransformerMixin = StandardScaler(), scheme: str = "speaker"
    ):
        """Transforms the X data matrix of this dataset using some
        normalisation method. I think in theory this should be
        idempotent.
        """
        norm_cls = normaliser.__class__
        fqn = f"{norm_cls.__module__}.{norm_cls.__name__}"
        print(f"Normalising dataset with scheme '{scheme}' using {fqn}.")

        if scheme == "all":
            if self.x.dtype == object or len(self.x.shape) == 3:
                # Non-contiguous or 3-D array
                flat, slices = _make_flat(self.x)
                flat = normaliser.fit_transform(flat)
                for i, arr in enumerate(_make_ragged(flat, slices)):
                    self._x[i] = arr
            else:
                self._x = normaliser.fit_transform(self.x)
        elif scheme == "speaker":
            for sp in range(len(self.speakers)):
                idx = np.nonzero(self.speaker_indices == sp)[0]
                if self.speaker_counts[sp] == 0:
                    continue
                if self.x.dtype == object or len(self.x.shape) == 3:
                    # Non-contiguous or 3-D array
                    flat, slices = _make_flat(self.x[idx])
                    flat = normaliser.fit_transform(flat)
                    self.x[idx] = _make_ragged(flat, slices)
                else:
                    self.x[idx] = normaliser.fit_transform(self.x[idx])

    def pad_arrays(self, pad: int = 32):
        """Pads each array to the nearest multiple of `pad` greater than
        the array size. Assumes axis 0 of x is time.
        """
        print(f"Padding array lengths to nearest multiple of {pad}.")
        pad_arrays(self.x, pad=pad)

    def clip_arrays(self, length: int):
        """Clips each array to the specified maximum length."""
        print(f"Clipping arrays to max length {length}.")
        clip_arrays(self.x, length=length)

    def frame_arrays(
        self,
        frame_size: int = 640,
        frame_shift: int = 160,
        num_frames: Optional[int] = None,
    ):
        """Create a sequence of frames from the raw signal."""
        print(f"Framing arrays with size {frame_size} and shift {frame_shift}.")
        self._x = frame_arrays(
            self._x,
            frame_size=frame_size,
            frame_shift=frame_shift,
            num_frames=num_frames,
        )

    def transpose_time(self):
        """Transpose the time and feature axis of each instance."""
        print("Transposing time and feature axis of data.")
        self._x = transpose_time(self._x)

    @property
    def corpus(self) -> str:
        """The corpus this LabelledDataset represents."""
        return self._corpus

    @property
    def n_instances(self) -> int:
        """Number of instances in this dataset."""
        return len(self.names)

    @property
    def features(self) -> List[str]:
        """List of feature names."""
        return self._features

    @property
    def n_features(self) -> int:
        """Number of features."""
        return len(self.features)

    @property
    def speakers(self) -> List[str]:
        """List of speakers in this dataset."""
        return self._speakers

    @property
    def speaker_counts(self) -> np.ndarray:
        """Number of instances for each speaker."""
        return self._speaker_counts

    @property
    def speaker_indices(self) -> np.ndarray:
        """Indices into speakers array of corresponding speaker for each
        instance.
        """
        return self._speaker_indices

    @property
    def male_speakers(self) -> List[str]:
        """List of male speakers in this dataset."""
        return self._male_speakers

    @property
    def male_indices(self) -> np.ndarray:
        """Indices of instances which have male speakers."""
        return self._male_indices

    @property
    def female_speakers(self) -> List[str]:
        """List of female speakers in this dataset."""
        return self._female_speakers

    @property
    def female_indices(self) -> np.ndarray:
        """Indices of instances which have female speakers."""
        return self._female_indices

    @property
    def speaker_groups(self) -> List[Set[str]]:
        """List of speaker groups."""
        return self._speaker_groups

    @property
    def speaker_group_indices(self) -> np.ndarray:
        """Indices into speaker groups array of corresponding speaker
        group for each instance.
        """
        return self._speaker_group_indices

    @property
    def names(self) -> List[str]:
        """List of instance names."""
        return self._names

    @property
    def x(self) -> np.ndarray:
        """The data matrix."""
        return self._x

    def __len__(self) -> int:
        return self.n_instances

    def __getitem__(self, idx) -> Tuple[np.ndarray, ...]:
        return (self.x[idx],)

    def __str__(self):
        s = f"\nCorpus: {self.corpus}\n"
        s += f"{self.n_instances} instances\n"
        s += f"{len(self.features)} features\n"
        s += f"{len(self.speakers)} speakers:\n"
        s += f"\t{dict(zip(self.speakers, self.speaker_counts))}\n"
        if self.x.dtype == object or len(self.x.shape) == 3:
            lengths = [len(x) for x in self.x]
            s += "Sequences:\n"
            s += f"min length: {np.min(lengths)}\n"
            s += f"mean length: {np.mean(lengths)}\n"
            s += f"max length: {np.max(lengths)}\n"
        return s


class LabelledDataset(Dataset):
    """Class representing a dataset containing discrete labels for each
    instance.
    """

    _classes: List[str]
    _class_counts: np.ndarray
    _y: np.ndarray

    def __init__(
        self,
        path: PathOrStr,
        label_path: PathOrStr,
        speaker_path: Optional[PathOrStr] = None,
    ):
        super().__init__(path, speaker_path=speaker_path)
        label_dict = parse_annotations(label_path)
        labels = [label_dict[n] for n in self.names]
        self._classes = sorted(set(labels))
        self._y = np.array([self.class_to_int(x) for x in labels])
        self._class_counts = np.bincount(self.y)
        self._labels = {"all": self.y}

    def remove_instances(self, keep: Collection[str]):
        idx = [i for i, x in enumerate(self.names) if x in keep]
        new_cl = np.unique(self.y[idx])
        if len(new_cl) < len(self.classes):
            new_classes = [self.classes[x] for x in new_cl]
            self._y = np.array(
                [new_classes.index(self.classes[self.y[i]]) for i in idx]
            )
            self._classes = new_classes
        else:
            self._y = self.y[idx]
        self._class_counts = np.bincount(self.y)
        super().remove_instances(keep)

    def binarise(self, pos_val: List[str] = [], pos_aro: List[str] = []):
        """Creates a N x C array of binary values B, where B[i, j] is 1
        if instance i belongs to class j, and 0 otherwise.
        """
        self.binary_y = label_binarize(self.y, np.arange(self.n_classes))
        self._labels.update(
            {c: self.binary_y[:, i] for i, c in enumerate(self.classes)}
        )

        if pos_aro and pos_val:
            print("Binarising arousal and valence")
            arousal_map = np.array([int(c in pos_aro) for c in self.classes])
            valence_map = np.array([int(c in pos_val) for c in self.classes])
            self._labels["arousal"] = arousal_map[self.y]
            self._labels["valence"] = valence_map[self.y]

    def map_classes(self, map: Mapping[str, str]):
        """Modifies classses based on the mapping in map. Keys not
        corresponding to classes are ignored. The new classes will be
        sorted lexicographically.
        """
        new_classes = sorted(set([map.get(x, x) for x in self.classes]))
        arr_map = np.array([new_classes.index(map.get(k, k)) for k in self.classes])
        self._y = arr_map[self.y]
        self._class_counts = np.bincount(self.y)
        self._classes = new_classes

    def remove_classes(self, keep: Collection[str]):
        """Remove instances with labels not in `keep`."""
        keep = set(keep)
        str_labels = [self.classes[int(i)] for i in self.y]
        keep_idx = [i for i, x in enumerate(str_labels) if x in keep]
        self._x = self._x[keep_idx]
        self._names = [self.names[i] for i in keep_idx]
        self._speaker_indices = self._speaker_indices[keep_idx]
        self._speaker_counts = np.bincount(
            self.speaker_indices, minlength=len(self.speakers)
        )
        self._speaker_group_indices = self._speaker_indices

        self._classes = sorted(keep.intersection(self.classes))
        str_labels = [x for x in str_labels if x in keep]
        self._y = np.array([self._classes.index(y) for y in str_labels])
        self._class_counts = np.bincount(self.y)

    @property
    def classes(self) -> List[str]:
        """A list of emotion class labels."""
        return self._classes

    @property
    def n_classes(self) -> int:
        """Total number of emotion classes."""
        return len(self.classes)

    @property
    def class_counts(self) -> np.ndarray:
        """Number of instances for each class."""
        return self._class_counts

    @property
    def labels(self) -> Dict[str, np.ndarray]:
        """Mapping from label set to array of numeric labels. The keys
        of the dictionary are {'all', 'arousal', 'valence', 'class1',
        ...}
        """
        return self._labels

    @property
    def y(self) -> np.ndarray:
        """The class label array; one label per instance."""
        return self._y

    def class_to_int(self, c: str) -> int:
        """Returns the index of the given class label."""
        return self.classes.index(c)

    def __str__(self):
        s = super().__str__()
        s += f"{self.n_classes} classes:\n"
        s += f"\t{dict(zip(self.classes, self.class_counts))}\n"
        return s

    def __getitem__(self, idx) -> Tuple[np.ndarray, ...]:
        y = (self.y[idx],) if self.y is not None else ()
        return super().__getitem__(idx) + y


class CombinedDataset(LabelledDataset):
    """A dataset that joins individual corpus datasets together and
    handles labelling differences.
    """

    def __init__(
        self, *datasets: LabelledDataset, labels: Optional[Collection[str]] = None
    ):
        self._corpus = "combined"
        self._corpora = [x.corpus for x in datasets]
        sizes = [len(x.x) for x in datasets]
        self._corpus_indices = np.repeat(np.arange(len(datasets)), sizes)
        self._corpus_counts = np.bincount(self.corpus_indices)

        self._names = [d.corpus + "_" + n for d in datasets for n in d.names]
        self._features = datasets[0].features

        self._speakers = []
        speaker_indices = []
        self._speaker_groups = []
        speaker_group_indices = []
        for d in datasets:
            speaker_indices.append(d.speaker_indices + len(self.speakers))
            self._speakers.extend([d.corpus + "_" + s for s in d.speakers])

            speaker_group_indices.append(
                d.speaker_group_indices + len(self.speaker_groups)
            )
            new_group = [{d.corpus + "_" + s for s in g} for g in d.speaker_groups]
            self._speaker_groups.extend(new_group)
        self._speaker_indices = np.concatenate(speaker_indices)
        self._speaker_group_indices = np.concatenate(speaker_group_indices)

        self._x = np.concatenate([x.x for x in datasets])

        all_labels = set(c for d in datasets for c in d.classes)
        self._classes = sorted(all_labels)
        str_labels = [d.classes[int(i)] for d in datasets for i in d.y]
        if labels is not None:
            drop_labels = all_labels - set(labels)
            keep_idx = [i for i, x in enumerate(str_labels) if x not in drop_labels]
            self._x = self._x[keep_idx]
            self._speaker_indices = self._speaker_indices[keep_idx]
            self._classes = sorted(set(labels))
            str_labels = [x for x in str_labels if x not in drop_labels]
        self._y = np.array([self._classes.index(y) for y in str_labels])
        self._speaker_group_indices = self._speaker_indices

    @property
    def corpora(self) -> List[str]:
        """List of corpora in this CombinedDataset."""
        return self._corpora

    @property
    def corpus_indices(self) -> np.ndarray:
        """Indices into corpora list of corresponding corpus for each
        instance.
        """
        return self._corpus_indices

    @property
    def corpus_counts(self) -> List[int]:
        return self._corpus_counts

    def corpus_to_idx(self, corpus: str) -> int:
        return self.corpora.index(corpus)

    def get_corpus_split(self, corpus: str) -> Tuple[np.ndarray, np.ndarray]:
        """Returns a tuple (corpus_idx, other_idx) containing the
        indices of x and y for the specified corpus and all other
        corpora.
        """
        cond = self.corpus_indices == self.corpus_to_idx(corpus)
        corpus_idx = np.nonzero(cond)[0]
        other_idx = np.nonzero(~cond)[0]
        return corpus_idx, other_idx

    def normalise(
        self, normaliser: TransformerMixin = StandardScaler(), scheme: str = "speaker"
    ):

        if scheme == "corpus":
            norm_cls = normaliser.__class__
            fqn = f"{norm_cls.__module__}.{norm_cls.__name__}"
            print(f"Normalising dataset with scheme 'corpus' using {fqn}.")

            for corpus in range(len(self.corpora)):
                idx = np.nonzero(self.corpus_indices == corpus)[0]
                if self.x.dtype == object or len(self.x.shape) == 3:
                    flat, slices = _make_flat(self.x[idx])
                    flat = normaliser.fit_transform(flat)
                    self.x[idx] = _make_ragged(flat, slices)
                else:
                    self.x[idx] = normaliser.fit_transform(self.x[idx])
        else:
            super().normalise(normaliser, scheme)

    def __str__(self) -> str:
        s = super().__str__()
        s += f"{len(self.corpora)} corpora:\n"
        s += f"\t{dict(zip(self.corpora, self.corpus_counts))}\n"
        return s
