"""Turn the FontSurvey training corpus into a feature table for Orange3.

The corpus is one csv, training_data/samples.csv, written by

    python -m source.FontSurvey -i pdfs/ -r -td training_data \\
        -tf nirmala='nirmala\\s*ui' -tf krutidev='kruti\\s*dev'

with a row per sample: the class it belongs to (nirmala, krutidev, and
not_required for the fonts needing no decoding), the font it was drawn in,
the pdf it came from, and the text itself. Only the label and the text are
learned from; the font and pdf are there to say where a sample came from when
a class looks polluted, which the label alone cannot.

A directory of per-class <label>.txt files - the corpus layout FontSurvey
wrote before the csv - is still read when there is no samples.csv in it, so a
corpus built by an older run does not have to be surveyed again.

Features are phrases: every 1 to 5 word sequence in the corpus is counted and
the most frequent 10,000 become the feature set. A sample is then the count
of each of those phrases in it. Text drawn in a legacy indic font extracts as
latin gibberish with its own very distinctive vocabulary ('fnYyh', 'ds', 'ls'
for chanakya/kruti-dev), which is exactly what a phrase feature set captures.
"""

import csv
import json
import codecs
import logging
from pathlib import Path
from collections import Counter

import numpy as np
import scipy.sparse as sp

from Orange.data import Table, Domain, DiscreteVariable, ContinuousVariable


DEFAULT_MIN_N = 1
DEFAULT_MAX_N = 5
DEFAULT_TOP_K = 10000

# the corpus: one row per sample, its class in the 'label' column
CORPUS_CSV    = 'samples.csv'
LABEL_FIELD   = 'label'
TEXT_FIELD    = 'text'

# the older layout, one file per class, the class being the file name
CORPUS_GLOB   = '*.txt'

# a sample is one field of one row, and -tw/--training-words has no ceiling,
# so the 128k default is not necessarily enough
CSV_FIELD_LIMIT = 64 * 1024 * 1024

logger = logging.getLogger('fontml.features')


def tokenize(text, lowercase = False):
    """Words of a sample.

    Whitespace is the only separator: the whole point of the corpus is text
    that is *not* real language, so nothing may be assumed about what its
    punctuation means - in kruti-dev '{ks=' is a word, not three of them.
    """
    if lowercase:
        text = text.lower()
    return text.split()


def iter_phrases(tokens, min_n = DEFAULT_MIN_N, max_n = DEFAULT_MAX_N):
    """Every min_n..max_n word phrase in one sample, in order."""
    num = len(tokens)
    for size in range(min_n, max_n + 1):
        for start in range(num - size + 1):
            yield ' '.join(tokens[start:start + size])


def get_class_labels(data_dir):
    """The legacy per-class files: (label, path), the label being the name."""
    paths = sorted(Path(data_dir).glob(CORPUS_GLOB))
    if not paths:
        raise ValueError(f'no {CORPUS_GLOB} corpus files in {data_dir}')
    return [(p.stem, p) for p in paths]


def read_corpus_csv(path, max_per_class = 0, lowercase = False):
    """[(label, [token, ...]), ...] for every row of the corpus csv.

    The cap is per class and the classes are interleaved (the rows are in the
    order the pdfs drew them), so a class that has filled its quota is skipped
    over rather than stopping the read.
    """
    csv.field_size_limit(CSV_FIELD_LIMIT)
    samples = []
    counts  = Counter()
    with codecs.open(str(path), 'r', encoding = 'utf8') as f:
        reader  = csv.DictReader(f)
        missing = {LABEL_FIELD, TEXT_FIELD}.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f'{path} has no {", ".join(sorted(missing))} '
                             f'column - is it a FontSurvey -td corpus?')
        for row in reader:
            label = (row.get(LABEL_FIELD) or '').strip()
            if not label:
                continue
            if max_per_class and counts[label] >= max_per_class:
                continue
            tokens = tokenize((row.get(TEXT_FIELD) or '').strip(), lowercase)
            if not tokens:
                continue
            samples.append((label, tokens))
            counts[label] += 1

    for label in sorted(counts):
        logger.info(f'{label}: {counts[label]} sample(s)')
    return samples, counts


def read_corpus_files(data_dir, max_per_class = 0, lowercase = False):
    """The same, from the older one-file-per-class layout."""
    samples = []
    counts  = Counter()
    for label, path in get_class_labels(data_dir):
        taken = 0
        with codecs.open(str(path), 'r', encoding = 'utf8') as f:
            for line in f:
                if max_per_class and taken >= max_per_class:
                    break
                tokens = tokenize(line.strip(), lowercase)
                if not tokens:
                    continue
                samples.append((label, tokens))
                taken += 1
        counts[label] = taken
        logger.info(f'{path.name}: {taken} sample(s)')
    return samples, counts


def read_corpus(data_dir, max_per_class = 0, lowercase = False):
    """[(label, [token, ...]), ...] for every sample of the corpus."""
    data_dir = Path(data_dir)
    path     = data_dir.joinpath(CORPUS_CSV)
    if path.is_file():
        samples, counts = read_corpus_csv(path, max_per_class, lowercase)
        source = path
    else:
        # nothing to convert an old corpus with, so it is simply still read
        logger.warning(f'no {CORPUS_CSV} in {data_dir}, reading the older '
                       f'per-class {CORPUS_GLOB} files instead')
        samples, counts = read_corpus_files(data_dir, max_per_class, lowercase)
        source = data_dir

    if not samples:
        raise ValueError(f'no samples in {source}')
    return samples, counts


def drop_small_classes(samples, min_samples):
    """Classes too small to be split across folds are unusable, not fatal."""
    counts  = Counter(label for label, _tokens in samples)
    dropped = {l for l, c in counts.items() if c < min_samples}
    for label in sorted(dropped):
        logger.warning(f'dropping class {label}: only {counts[label]} '
                       f'sample(s), fewer than the {min_samples} needed')
    if len(counts) - len(dropped) < 2:
        raise ValueError(\
            f'only {len(counts) - len(dropped)} class(es) left with at least '
            f'{min_samples} samples - nothing to learn from')
    return [s for s in samples if s[0] not in dropped]


def count_phrases(samples, min_n = DEFAULT_MIN_N, max_n = DEFAULT_MAX_N, \
                  prune_at = 2000000):
    """How often every phrase occurs in the whole corpus.

    A corpus of a few hundred thousand lines has millions of distinct 5 word
    phrases, nearly all of them seen once. Once the table grows past prune_at
    keys the singletons are dropped: a phrase that is still unique after that
    much text cannot reach the top of the ranking, and holding all of them
    costs gigabytes.
    """
    counter = Counter()
    for _label, tokens in samples:
        counter.update(iter_phrases(tokens, min_n, max_n))
        if prune_at and len(counter) > prune_at:
            singles = [p for p, c in counter.items() if c == 1]
            for phrase in singles:
                del counter[phrase]
            logger.debug(f'pruned {len(singles)} phrase(s) seen once, '
                         f'{len(counter)} left')
    return counter


def build_vocabulary(samples, top_k = DEFAULT_TOP_K, min_n = DEFAULT_MIN_N, \
                     max_n = DEFAULT_MAX_N, prune_at = 2000000):
    """The top_k most frequent phrases, most frequent first."""
    counter = count_phrases(samples, min_n, max_n, prune_at)
    logger.info(f'{len(counter)} distinct phrase(s) of {min_n}-{max_n} words')
    # the phrase itself breaks ties, so the same corpus always gives the same
    # feature set whatever order Counter happens to hold it in
    ranked = sorted(counter.items(), key = lambda kv: (-kv[1], kv[0]))
    return [phrase for phrase, _count in ranked[:top_k]]


def vectorize(samples, vocab, min_n = DEFAULT_MIN_N, max_n = DEFAULT_MAX_N):
    """Samples as a sparse count matrix over the vocabulary."""
    index   = {phrase: i for i, phrase in enumerate(vocab)}
    indptr  = [0]
    indices = []
    data    = []
    for _label, tokens in samples:
        counts = Counter()
        for phrase in iter_phrases(tokens, min_n, max_n):
            col = index.get(phrase)
            if col is not None:
                counts[col] += 1
        indices.extend(counts.keys())
        data.extend(counts.values())
        indptr.append(len(indices))

    return sp.csr_matrix(\
        (np.array(data, dtype = np.float64), np.array(indices, dtype = np.int32), \
         np.array(indptr, dtype = np.int32)), \
        shape = (len(samples), len(vocab)))


def get_domain(vocab, labels):
    # a phrase can contain anything the pdf drew, and Orange takes the name
    # verbatim, so only the feature index is guaranteed unique - the phrase
    # itself rides along in the attribute's metadata for readability
    features = []
    for i, phrase in enumerate(vocab):
        var = ContinuousVariable(f'f{i}: {phrase}')
        var.attributes['phrase'] = phrase
        features.append(var)
    return Domain(features, DiscreteVariable('font', values = tuple(labels)))


def build_table(samples, vocab, min_n = DEFAULT_MIN_N, max_n = DEFAULT_MAX_N):
    """An Orange Table of phrase counts, sparse, with the font as the class."""
    labels = sorted({label for label, _tokens in samples})
    domain = get_domain(vocab, labels)
    index  = {label: i for i, label in enumerate(labels)}

    X = vectorize(samples, vocab, min_n, max_n)
    y = np.array([index[label] for label, _tokens in samples], dtype = np.float64)
    return Table.from_numpy(domain, X, y)


def build_dataset(data_dir, top_k = DEFAULT_TOP_K, min_n = DEFAULT_MIN_N, \
                  max_n = DEFAULT_MAX_N, max_per_class = 0, \
                  min_samples = 10, lowercase = False, prune_at = 2000000):
    """Corpus directory -> (Orange Table, vocabulary)."""
    samples, _counts = read_corpus(data_dir, max_per_class, lowercase)
    samples = drop_small_classes(samples, min_samples)
    vocab   = build_vocabulary(samples, top_k, min_n, max_n, prune_at)
    logger.info(f'{len(samples)} sample(s), {len(vocab)} feature(s)')

    table = build_table(samples, vocab, min_n, max_n)
    empty = int((table.X.getnnz(axis = 1) == 0).sum()) if sp.issparse(table.X) \
                else int((table.X.sum(axis = 1) == 0).sum())
    if empty:
        # samples made only of phrases that missed the top_k cut - they carry
        # no evidence either way and just pull every metric towards the
        # majority class, so it is worth knowing how many there are
        logger.warning(f'{empty} sample(s) have no feature in the vocabulary')
    return table, vocab


def save_vocabulary(vocab, path):
    with codecs.open(str(path), 'w', encoding = 'utf8') as f:
        json.dump(vocab, f, indent = 2, ensure_ascii = False)


def load_vocabulary(path):
    with codecs.open(str(path), 'r', encoding = 'utf8') as f:
        return json.load(f)
