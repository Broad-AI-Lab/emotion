"""Process the raw URDU dataset.

This assumes the file structure from the original compressed file:
/.../
    Angry/
        *.wav
    ...
"""

from pathlib import Path

import click
from emorec.dataset import resample_audio, write_filelist, write_annotations
from emorec.utils import PathlibPath

emotion_map = {"A": "anger", "S": "sadness", "H": "happiness", "N": "neutral"}


@click.command()
@click.argument("input_dir", type=PathlibPath(exists=True, file_okay=False))
def main(input_dir: Path):
    """Process the URDU dataset at location INPUT_DIR and resample audio
    to 16 kHz 16-bit WAV audio.
    """

    paths = list(input_dir.glob("*/*.wav"))
    resample_dir = Path("resampled")
    resample_audio(paths, resample_dir)

    write_filelist(resample_dir.glob("*.wav"))
    write_annotations(
        {p.stem: emotion_map[p.stem[p.stem.rfind("_") + 1]] for p in paths}
    )
    write_annotations({p.stem: p.stem[: p.stem.index("_")] for p in paths}, "speaker")


if __name__ == "__main__":
    main()
