from pathlib import Path

import click

from emorec.dataset import read_features
from emorec.utils import PathlibPath


@click.command()
@click.argument("input", type=PathlibPath(exists=True, dir_okay=False))
@click.argument("output", type=Path)
@click.option("--corpus", type=str, help="Corpus attribute to set, if required.")
@click.option(
    "--header",
    is_flag=True,
    help="Input CSV has header/Write output CSV header.",
)
@click.option(
    "--inlabel",
    is_flag=True,
    help="Input has label column.",
)
def main(input: Path, output: Path, corpus: str, header: bool, inlabel: bool):
    """Convert INPUT dataset format to OUTPUT format. Note that no label
    information is written to OUTPUT.
    """

    if input.suffix == output.suffix:
        raise ValueError("Input format must be different to output.")

    print(f"Reading {input}")
    data = read_features(input, header=header, label=inlabel)
    if corpus:
        data._corpus = corpus
    output.parent.mkdir(parents=True, exist_ok=True)
    data.write(output, header=header)
    print(f"Wrote dataset to {output}")


if __name__ == "__main__":
    main()
