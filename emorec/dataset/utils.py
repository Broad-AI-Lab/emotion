import subprocess
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional, Sequence, Type, Union

import pandas as pd
from joblib import Parallel, delayed

from ..utils import PathOrStr


def parse_annotations(
    filename: PathOrStr, dtype: Optional[Type] = None
) -> Dict[str, str]:
    """Returns a dict of the form {name: annotation}."""
    # Need index_col to be False or None due to
    # https://github.com/pandas-dev/pandas/issues/9435
    df = pd.read_csv(filename, index_col=False, header=0, converters={0: str, 1: dtype})
    type_ = df.columns[1]
    annotations = df.set_index("name")[type_].to_dict()
    return annotations


def get_audio_paths(file: PathOrStr) -> Sequence[Path]:
    """Given a path to a file containing a list of audio files, returns
    a sequence of absolute paths to the audio files.

    Args:
    -----
    file: pathlike or str
        Path to a file containing a list of paths to audio clips.

    Returns:
    --------
        Sequence of paths to audio files.
    """
    file = Path(file)
    paths = []
    with open(file) as fid:
        for line in fid:
            p = Path(line.strip())
            paths.append(p if p.is_absolute() else (file.parent / p).resolve())
    return paths


def resample_audio(paths: Iterable[Path], dir: PathOrStr):
    """Resample given audio clips to 16 kHz 16-bit WAV, and place in
    direcotory given by `dir`.
    """
    paths = list(paths)
    if len(paths) == 0:
        raise FileNotFoundError("No audio files found.")

    dir = Path(dir)
    dir.mkdir(exist_ok=True, parents=True)
    print(f"Resampling {len(paths)} audio files to {dir}")

    opts = ["-nostdin", "-ar", "16000", "-sample_fmt", "s16", "-ac", "1", "-y"]
    print(f"Using FFmpeg options: {' '.join(opts)}")
    Parallel(n_jobs=-1, verbose=1)(
        delayed(subprocess.run)(
            ["ffmpeg", "-i", str(path), *opts, str(dir / (path.stem + ".wav"))],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        for path in paths
    )


def write_filelist(paths: Iterable[Path], out: PathOrStr = "files.txt"):
    """Write sorted file list."""
    paths = sorted(paths, key=lambda p: p.stem)
    with open(out, "w") as fid:
        fid.write("\n".join(list(map(str, paths))) + "\n")
    print("Wrote file list to files.txt")


def write_annotations(
    annotations: Mapping[str, str],
    name: str = "label",
    path: Union[PathOrStr, None] = None,
):
    """Write sorted annotations CSV.

    Args:
    -----
    annotations: mapping
        A mapping of the form {name: annotation}.
    name: str
        Name of the annotation.
    path: pathlike or str, optional
        Path to write CSV. If None, filename is name.csv
    """
    df = pd.DataFrame.from_dict(annotations, orient="index", columns=[name])
    df.index.name = "name"
    path = path or f"{name}.csv"
    df.sort_index().to_csv(path, header=True, index=True)
    print(f"Wrote CSV to {path}")
