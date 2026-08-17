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

`source/FontSurvey.py` writes it. Both sides of the corpus are named by
regexps, matched (anywhere, case insensitively) against the font name:

- `-tf/--training-font LABEL=REGEX` - a class of fonts that **needs** a
  decoder. Every run of text drawn in a matching font becomes one line of
  `<label>.txt`.
- `-nf/--not-required-font REGEX` - fonts that need **no** decoder. Their
  text is the negative class, `not_required.txt`. Repeat the option to list
  the families a few at a time.

```bash
python -m source.FontSurvey -i pdfs/ -r -td training_data \
    -tf nirmala='nirmala\s*ui' \
    -tf arialuni='arial\s*unicode' \
    -tf krutidev='kruti\s*dev' \
    -tf chanakya='chanakya|TT\d+t\d+' \
    -nf 'times|arial|calibri|cambria|courier|helvetica' \
    -nf 'liberation|dejavu|nimbus|century|tahoma'
```

Text drawn in a font matched by neither is **dropped**. That is the whole
point of naming both sides: a font that in fact needs decoding but is swept
into the negative class by default teaches the model the exact opposite of
the truth, and there is no way to tell from the font name alone which side an
unnamed font belongs on - that is the problem this model exists to solve.

The report lists every dropped font with how many samples went with it, so it
is the worklist for widening the regexps:

```
dropped: 66843 sample(s) in 37 font(s) matched by neither --training-font nor --not-required-font
    CIDFont+F1: 57622
    TAUElangoPanchali: 746
    Devnagari-ChanakyaNormal: 14
```

The survey's own report (`-o`) lists every font with a sample of the words
drawn in it - read it first and write the regexps from it.

Bootstrapping a class from fonts whose names *do* identify them (Nirmala UI,
Kruti Dev, Times) is the point: once trained, the model labels the text of the
fonts whose names identify nothing - which is exactly the text `-tf`/`-nf`
could not have labelled either, and why it was dropped rather than guessed at.

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
