"""Append the lines of a text file to a font-classifier training corpus.

The corpus is the csv `machinelearning/features.py` trains from - a row per
sample, `label,font,pdf,text` (`source.FontSurvey.TrainingWriter.CORPUS_FIELDS`).
This adds one row per non-empty line of a text file, which is the shape
`indic2unicode`'s `testdata/` fixtures already have: one line of text drawn in
one font, per line.

    python add_corpus_samples.py -i ~/indic2unicode/testdata/surekh \
        -c union_bihar.csv -l surekh

A line carrying no letter or digit (a rule of dashes, a row of punctuation) is
dropped as carrying no signal, the way `TrainingWriter` drops one; every other
line is written as it is, with its internal whitespace collapsed so that one
row stays one line.
"""

import argparse
import csv
import os
import sys
import unicodedata

CORPUS_FIELDS = ['label', 'font', 'pdf', 'text']
LABEL_CHARS = set(
    'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-')


def is_useful(text):
    """True if the text carries at least one letter or digit."""
    return any(unicodedata.category(c)[0] in ('L', 'N') for c in text)


def get_lines(path, words):
    """Yield the samples of a text file: one per line, or `words` words each."""
    buf = []
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            text = ' '.join(line.split())
            if not text or not is_useful(text):
                continue
            if not words:
                yield text
                continue
            buf.extend(text.split())
            while len(buf) >= words:
                yield ' '.join(buf[:words])
                buf = buf[words:]
    if buf:
        yield ' '.join(buf)


def check_header(corpus):
    """Fail loudly on a corpus whose columns aren't the ones we write."""
    with open(corpus, encoding='utf-8', newline='') as f:
        header = next(csv.reader(f), None)
    if header is None:
        return False
    if header != CORPUS_FIELDS:
        raise ValueError('%s has columns %s, expected %s'
                         % (corpus, header, CORPUS_FIELDS))
    return True


def ends_with_newline(path):
    with open(path, 'rb') as f:
        if f.seek(0, os.SEEK_END) == 0:
            return True
        f.seek(-1, os.SEEK_END)
        return f.read(1) == b'\n'


def append_samples(corpus, label, font, pdf, samples, dry_run=False):
    exists = os.path.exists(corpus) and os.path.getsize(corpus) > 0
    has_header = check_header(corpus) if exists else False

    if dry_run:
        n = 0
        for text in samples:
            if n < 3:
                print('%s,%s,%s,%s' % (label, font, pdf, text[:120]))
            n += 1
        return n

    if exists and not ends_with_newline(corpus):
        with open(corpus, 'a', encoding='utf-8') as f:
            f.write('\n')

    n = 0
    with open(corpus, 'a', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        if not has_header:
            writer.writerow(CORPUS_FIELDS)
        for text in samples:
            writer.writerow([label, font, pdf, text])
            n += 1
    return n


def main():
    parser = argparse.ArgumentParser(
        description='Append a text file to a font-classifier training corpus')
    parser.add_argument('-i', '--input', required=True,
                        help='text file, one line of one font per line')
    parser.add_argument('-c', '--corpus', default='union_bihar.csv',
                        help='corpus csv to append to (default: %(default)s)')
    parser.add_argument('-l', '--label', required=True,
                        help='class the samples belong to, e.g. surekh')
    parser.add_argument('-f', '--font',
                        help='font column (default: the label)')
    parser.add_argument('-p', '--pdf',
                        help='pdf column, the source of the text '
                             '(default: the input path)')
    parser.add_argument('-w', '--words', type=int, default=0,
                        help='pack the text into samples of this many words '
                             'instead of one sample per line')
    parser.add_argument('-n', '--dry-run', action='store_true',
                        help='count and show the rows without writing them')
    args = parser.parse_args()

    bad = set(args.label) - LABEL_CHARS
    if bad:
        raise ValueError('label %r carries %s, allowed are [A-Za-z0-9_-]'
                         % (args.label, sorted(bad)))
    if args.words < 0:
        raise ValueError('--words cannot be negative')

    font = args.font or args.label
    pdf = args.pdf or args.input

    samples = get_lines(args.input, args.words)
    n = append_samples(args.corpus, args.label, font, pdf, samples,
                       dry_run=args.dry_run)
    print('%s %d %s rows %s %s'
          % ('Would add' if args.dry_run else 'Added', n, args.label,
             'from' if args.dry_run else 'from', args.input),
          file=sys.stderr)


if __name__ == '__main__':
    main()
