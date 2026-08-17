# Font classification from extracted text

Many pdfs embed a legacy indic font under a name that says nothing about its
encoding (`TT572t00`, `Vivek-NormalA`), so there is no way to pick the right
`-fc/--font-conv` converter from the font name alone. The text extracted from
such a font is, however, unmistakable: chanakya comes out as `fnYyh fodkl`,
kruti-dev as `jk"Vªh; jkt/kkuh`, a real unicode font as actual words. That is
learnable, and this is the pipeline that learns it.

    corpus            features             model
    FontSurvey -td -> 1..5 word phrases -> Orange3 classifier
    <label>.txt       top 10,000

## 1. Build the corpus

`source/FontSurvey.py` writes it. Each `-tf/--training-font` gives a class a
name and a regexp matched (anywhere, case insensitively) against the font
name; every run of text drawn in a matching font becomes one line of
`<label>.txt`. Text in every other font goes to `not_required.txt` - the
negative class, the fonts that need no decoding at all.

```bash
python -m source.FontSurvey -i pdfs/ -r -td training_data \
    -tf nirmala='nirmala\s*ui' \
    -tf arialuni='arial\s*unicode' \
    -tf krutidev='kruti\s*dev' \
    -tf chanakya='chanakya|TT\d+t\d+'
```

The classes are only as clean as the regexps: any font that needs decoding
but is not named by a `-tf` lands in `not_required.txt` and teaches the model
the opposite of the truth. The survey report (`-o`) lists every font and a
sample of its words for exactly this - read it first and write the regexps
from it.

Bootstrapping a class from fonts whose names *do* identify them (Nirmala UI,
Kruti Dev) is the point: once trained, the model labels the text of the fonts
whose names identify nothing.

## 2. Cross validate and train

```bash
python -m machinelearning.training -d training_data -m fontmodel.pkl \
    -k 10 -mc 20000 -vf vocab.json
```

`features.py` counts every 1 to 5 word phrase in the corpus, keeps the
`-tk/--top-k` most frequent (10,000 by default) as the feature set, and
builds a sparse Orange `Table` of per-sample phrase counts with the class
name as the target. `training.py` then runs a stratified `-k` fold
`CrossValidation` over the requested learners, prints accuracy, macro
F1/precision/recall and a confusion matrix per learner, trains the final
model on the whole corpus and pickles it with its vocabulary.

Options worth knowing:

- `-mc/--max-per-class` caps the samples read from each class. Real corpora
  are wildly imbalanced (a hundred thousand `not_required` lines against a
  few hundred nirmala ones) and the cap is the cheapest fix. The confusion
  matrix is printed in samples so the imbalance stays visible either way.
- `-ms/--min-samples` drops a class with fewer samples than the fold count
  can split, with a warning, rather than failing the run.
- `-le/--learner` (repeatable) picks what to evaluate: `logistic`, `sgd`,
  `tree`, `forest`. `-fl/--final-learner` picks what to save; it defaults to
  the first one evaluated.
- `-nm/--min-ngram` / `-nx/--max-ngram` change the phrase lengths. Dropping
  to `-nx 1` makes it a plain bag of words, which trains much faster and is a
  useful baseline to compare against.
- `-k 0` skips the evaluation and only trains.

## 3. Classify

```bash
python -m machinelearning.predict -m fontmodel.pkl -t "fnYyh fodkl izkf/kdj.k"
python -m machinelearning.predict -m fontmodel.pkl -f page_text.txt -w
```

or from python:

```python
from machinelearning.predict import FontClassifier

classifier   = FontClassifier('fontmodel.pkl')
label, prob  = classifier.classify(text_drawn_in_one_font)
```

A single short line carries little evidence, so classify as much text of one
font as is available at once (`-w` treats a whole file as one sample) rather
than line by line.

## Files

- `features.py` - corpus reading, phrase counting, vocabulary selection and
  the sparse Orange `Table`
- `training.py` - cross validation, training, model pickling
- `predict.py` - `FontClassifier`, loads a pickled model and classifies text

Requires `Orange3` (in `requirements.txt`).
