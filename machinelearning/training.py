"""Cross validate and train the font classifier with Orange3.

    python -m machinelearning.training -d training_data -m model/eng_hin_fonts.pkl

Reads the per-class corpus FontSurvey's -td/--training-dir wrote, builds the
top 10,000 phrase features (see features.py), runs a stratified k fold cross
validation over every requested learner, and trains the chosen one on the
whole corpus and pickles it together with its vocabulary so predict.py can
classify the text of a font whose name says nothing about its encoding.
"""

import time
import pickle
import codecs
import logging
import argparse
import warnings

import numpy as np

from Orange.evaluation import CrossValidation, CA, F1, Precision, Recall
from Orange.classification import LogisticRegressionLearner, \
                                  RandomForestLearner, \
                                  SGDClassificationLearner, TreeLearner

from machinelearning import features


logger = logging.getLogger('fontml.training')

# name -> a learner that can be trained on a large sparse count matrix
LEARNERS = {
    'logistic': lambda: LogisticRegressionLearner(),
    # sgd stops after 5 passes by default, far too few to converge on a
    # sparse phrase matrix this wide
    'sgd':      lambda: SGDClassificationLearner(max_iter = 1000),
    'tree':     lambda: TreeLearner(),
    'forest':   lambda: RandomForestLearner(),
}

DEFAULT_LEARNERS = ['logistic', 'sgd']


def get_learners(names):
    learners = []
    for name in names:
        if name not in LEARNERS:
            raise ValueError(f'unknown learner {name}, '
                             f'pick from {", ".join(sorted(LEARNERS))}')
        learner = LEARNERS[name]()
        learner.name = name
        learners.append(learner)
    return learners


def format_scores(names, results):
    """One line per learner: the scores cross validation measured for it."""
    scores = [
        ('CA',        CA(results)),
        ('F1',        F1(results, average = 'macro')),
        ('precision', Precision(results, average = 'macro')),
        ('recall',    Recall(results, average = 'macro')),
    ]
    lines = ['', f'{"learner":<12} ' + \
                 ' '.join(f'{label:>10}' for label, _v in scores)]
    lines.append('-' * len(lines[-1]))
    for i, name in enumerate(names):
        lines.append(f'{name:<12} ' + \
                     ' '.join(f'{value[i]:>10.4f}' for _l, value in scores))
    return lines


def get_confusion(results, index, num_labels):
    """Counts of (true class, predicted class) over every held out fold.

    Orange only ships one of these inside a Qt widget module, which is not
    importable without a display, so it is counted here from the predictions
    cross validation already recorded.
    """
    actual    = results.actual.astype(int)
    predicted = results.predicted[index].astype(int)
    flat      = np.bincount(actual * num_labels + predicted, \
                            minlength = num_labels ** 2)
    return flat.reshape(num_labels, num_labels)


def format_confusion(table, results, names):
    """The confusion matrix of each learner, in samples not proportions."""
    labels = list(table.domain.class_var.values)
    width  = max(len(l) for l in labels + ['predicted ->']) + 2
    lines  = []
    for i, name in enumerate(names):
        matrix = get_confusion(results, i, len(labels))
        lines.append('')
        lines.append(f'{name}: rows are the true font, columns the predicted')
        lines.append(' ' * width + ''.join(f'{l:>{width}}' for l in labels))
        for row, label in enumerate(labels):
            counts = ''.join(f'{int(matrix[row][col]):>{width}}' \
                             for col in range(len(labels)))
            lines.append(f'{label:<{width}}{counts}')
    return lines


def format_class_counts(table):
    labels = list(table.domain.class_var.values)
    counts = np.bincount(table.Y.astype(int), minlength = len(labels))
    lines  = ['', f'{len(table)} sample(s), '
                  f'{len(table.domain.attributes)} feature(s)']
    lines.extend(f'    {label:<20} {int(count)}' \
                 for label, count in zip(labels, counts))
    return lines


def cross_validate(table, learners, k = 10):
    names = [l.name for l in learners]
    logger.info(f'{k} fold cross validation of {", ".join(names)}')
    start   = time.time()
    results = CrossValidation(k = k, stratified = True, random_state = 42)(\
                  table, learners)
    logger.info(f'cross validation took {time.time() - start:.1f}s')

    lines = format_class_counts(table)
    lines.extend(format_scores(names, results))
    lines.extend(format_confusion(table, results, names))
    return results, '\n'.join(lines)


def train(table, learner):
    logger.info(f'training {learner.name} on all {len(table)} sample(s)')
    start = time.time()
    model = learner(table)
    logger.info(f'training took {time.time() - start:.1f}s')
    return model


def save_model(path, model, vocab, params):
    with open(path, 'wb') as f:
        pickle.dump({
            'model':  model,
            'vocab':  vocab,
            'params': params,
            'labels': list(model.domain.class_var.values),
        }, f)
    logger.info(f'wrote {path}')


def load_model(path):
    with open(path, 'rb') as f:
        return pickle.load(f)


def get_arg_parser():
    parser = argparse.ArgumentParser(\
        description = 'Cross validate and train an Orange3 model that tells '
                      'which decoder a font needs from the text drawn in it.')
    parser.add_argument('-d', '--data-dir', dest = 'data_dir', \
                        action = 'store', default = 'training_data', \
                        help = 'directory of per-class corpus files written '
                               'by FontSurvey -td (default training_data)')
    parser.add_argument('-m', '--model-file', dest = 'model_file', \
                        action = 'store', default = None, \
                        help = 'pickle the trained model and its vocabulary '
                               'here; without it nothing is saved and only '
                               'the cross validation runs')
    parser.add_argument('-vf', '--vocab-file', dest = 'vocab_file', \
                        action = 'store', default = None, \
                        help = 'also write the selected phrases as json here')
    parser.add_argument('-o', '--output-file', dest = 'output_file', \
                        action = 'store', default = None, \
                        help = 'write the evaluation report here instead of '
                               'stdout')
    parser.add_argument('-k', '--folds', dest = 'folds', action = 'store', \
                        type = int, default = 10, \
                        help = 'cross validation folds (default 10), 0 to '
                               'skip the evaluation and only train')
    parser.add_argument('-le', '--learner', dest = 'learners', \
                        action = 'append', default = None, \
                        choices = sorted(LEARNERS), \
                        help = f'learner to evaluate, repeatable (default '
                               f'{" and ".join(DEFAULT_LEARNERS)})')
    parser.add_argument('-fl', '--final-learner', dest = 'final_learner', \
                        action = 'store', default = None, \
                        choices = sorted(LEARNERS), \
                        help = 'learner to train the saved model with '
                               '(default the first evaluated one)')
    parser.add_argument('-tk', '--top-k', dest = 'top_k', action = 'store', \
                        type = int, default = features.DEFAULT_TOP_K, \
                        help = f'phrases to keep as features (default '
                               f'{features.DEFAULT_TOP_K})')
    parser.add_argument('-nm', '--min-ngram', dest = 'min_n', \
                        action = 'store', type = int, \
                        default = features.DEFAULT_MIN_N, \
                        help = f'shortest phrase in words (default '
                               f'{features.DEFAULT_MIN_N})')
    parser.add_argument('-nx', '--max-ngram', dest = 'max_n', \
                        action = 'store', type = int, \
                        default = features.DEFAULT_MAX_N, \
                        help = f'longest phrase in words (default '
                               f'{features.DEFAULT_MAX_N})')
    parser.add_argument('-mc', '--max-per-class', dest = 'max_per_class', \
                        action = 'store', type = int, default = 0, \
                        help = 'cap the samples read from each class, to stop '
                               'not_required swamping the rest (default 0, no '
                               'cap)')
    parser.add_argument('-ms', '--min-samples', dest = 'min_samples', \
                        action = 'store', type = int, default = 10, \
                        help = 'drop a class with fewer samples than this, it '
                               'cannot be split across folds (default 10)')
    parser.add_argument('-lc', '--lowercase', dest = 'lowercase', \
                        action = 'store_true', \
                        help = 'lowercase the corpus; off by default because '
                               'the case pattern of legacy indic gibberish is '
                               'itself a signal')
    parser.add_argument('-l', '--loglevel', dest = 'loglevel', \
                        action = 'store', default = 'info', \
                        choices = ['critical', 'error', 'warning', 'info', \
                                   'debug'], \
                        help = 'log level (default info)')
    parser.add_argument('-g', '--logfile', dest = 'logfile', action = 'store', \
                        default = None, help = 'log file path')
    return parser


if __name__ == '__main__':
    args = get_arg_parser().parse_args()

    logging.basicConfig(\
        level    = getattr(logging, args.loglevel.upper()), \
        format   = '%(asctime)s: %(name)s: %(levelname)s  %(message)s', \
        datefmt  = '%Y-%m-%d %H:%M:%S', \
        stream   = codecs.open(args.logfile, 'w', encoding = 'utf8') \
                       if args.logfile else None)
    # orange passes sklearn a few arguments sklearn has since deprecated;
    # nothing here can act on that, and it buries the report
    warnings.filterwarnings('ignore', category = FutureWarning)

    names = args.learners or DEFAULT_LEARNERS
    try:
        learners = get_learners(names)
        final    = get_learners([args.final_learner or names[0]])[0]
        table, vocab = features.build_dataset(\
            args.data_dir, top_k = args.top_k, min_n = args.min_n, \
            max_n = args.max_n, max_per_class = args.max_per_class, \
            min_samples = args.min_samples, lowercase = args.lowercase)
    except ValueError as e:
        raise SystemExit(f'error: {e}')

    report = '\n'.join(format_class_counts(table))
    if args.folds:
        _results, report = cross_validate(table, learners, args.folds)

    if args.output_file:
        with codecs.open(args.output_file, 'w', encoding = 'utf8') as f:
            f.write(report + '\n')
    else:
        print(report)

    if args.vocab_file:
        features.save_vocabulary(vocab, args.vocab_file)

    if args.model_file:
        model = train(table, final)
        save_model(args.model_file, model, vocab, {
            'min_n':     args.min_n,
            'max_n':     args.max_n,
            'lowercase': args.lowercase,
            'learner':   final.name,
            'data_dir':  args.data_dir,
        })
    else:
        logger.info('no -m/--model-file given, nothing was saved')
