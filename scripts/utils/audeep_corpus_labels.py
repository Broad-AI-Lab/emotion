"""Resets the filenames and adds correct label information to a dataset
generated by the auDeep spectrogram generation script.
"""

import argparse
from pathlib import Path

import netCDF4
import numpy as np
from emorec.dataset import parse_classification_annotations


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('input')
    parser.add_argument('--labels', required=True)
    args = parser.parse_args()

    labels = parse_classification_annotations(args.labels)

    dataset = netCDF4.Dataset(args.input, 'a')
    names = [Path(x).stem for x in dataset.variables['filename']]
    label_arr = [labels[x] for x in names]
    dataset.variables['label_nominal'] = np.array(label_arr)
    dataset.variables['filename'] = np.array(names)
    dataset.close()
    print("Changed corpus to {} in {}".format(args.corpus, args.input))


if __name__ == "__main__":
    main()
