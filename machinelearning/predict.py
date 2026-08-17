"""Classify the text drawn in a font with the model training.py saved.

    python -m machinelearning.predict -m fontmodel.pkl -t "fnYyh fodkl"
    python -m machinelearning.predict -m fontmodel.pkl -f page_text.txt

As a library:

    classifier = FontClassifier('fontmodel.pkl')
    label, prob = classifier.classify(text)
"""

import codecs
import argparse

import numpy as np

from Orange.data import Table

from machinelearning import features
from machinelearning.training import load_model


class FontClassifier:
    """The trained model plus everything needed to featurize new text."""

    def __init__(self, model_path):
        saved        = load_model(model_path)
        self.model   = saved['model']
        self.vocab   = saved['vocab']
        self.params  = saved.get('params', {})
        self.labels  = saved['labels']
        self.min_n   = self.params.get('min_n', features.DEFAULT_MIN_N)
        self.max_n   = self.params.get('max_n', features.DEFAULT_MAX_N)
        self.lower   = self.params.get('lowercase', False)

    def get_table(self, texts):
        samples = [(None, features.tokenize(t, self.lower)) for t in texts]
        X = features.vectorize(samples, self.vocab, self.min_n, self.max_n)
        # the domain has a class variable and from_numpy insists on a column
        # for it; unknown is exactly what it is, that is what is being asked
        y = np.full(len(samples), np.nan)
        return Table.from_numpy(self.model.domain, X, y)

    def classify_all(self, texts):
        """[(label, probability), ...], one per text."""
        if not texts:
            return []
        probs = self.model(self.get_table(texts), self.model.Probs)
        best  = np.argmax(probs, axis = 1)
        return [(self.labels[i], float(probs[row][i])) \
                for row, i in enumerate(best)]

    def classify(self, text):
        return self.classify_all([text])[0]


def get_arg_parser():
    parser = argparse.ArgumentParser(\
        description = 'Say which font class - and so which decoder - a piece '
                      'of extracted text belongs to.')
    parser.add_argument('-m', '--model-file', dest = 'model_file', \
                        action = 'store', required = True, \
                        help = 'model pickled by training.py')
    parser.add_argument('-t', '--text', dest = 'texts', action = 'append', \
                        default = None, help = 'text to classify, repeatable')
    parser.add_argument('-f', '--file', dest = 'files', action = 'append', \
                        default = None, \
                        help = 'classify every line of this file, repeatable')
    parser.add_argument('-w', '--whole-file', dest = 'whole_file', \
                        action = 'store_true', \
                        help = 'classify each --file as one sample instead of '
                               'line by line')
    return parser


if __name__ == '__main__':
    args = get_arg_parser().parse_args()

    texts = list(args.texts or [])
    for path in args.files or []:
        with codecs.open(path, 'r', encoding = 'utf8') as f:
            content = f.read()
        if args.whole_file:
            texts.append(content)
        else:
            texts.extend(l for l in content.splitlines() if l.strip())

    if not texts:
        raise SystemExit('error: give something to classify with -t or -f')

    classifier = FontClassifier(args.model_file)
    for text, (label, prob) in zip(texts, classifier.classify_all(texts)):
        # -w hands a whole file over as one sample, so the echo of it has to
        # stay on the one line its verdict is on
        sample = ' '.join(text.split())
        if len(sample) > 60:
            sample = sample[:57] + '...'
        print(f'{label:<16} {prob:.4f}  {sample}')
