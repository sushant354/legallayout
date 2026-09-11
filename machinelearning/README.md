# Font classification from extracted text

Many pdfs embed a legacy indic font under a name that says nothing about its
encoding (`TT572t00`), so there is no way to pick the right
`-fc/--font-conv` converter from the font name alone. The text extracted from
such a font is, however, unmistakable: chanakya comes out as `fnYyh fodkl`,
kruti-dev as `jk"Vªh; jkt/kkuh`, a real unicode font as actual words. That is
learnable, and this is the pipeline that learns it.

    corpus            features             model
    FontSurvey -tc -> 1..5 word phrases -> Orange3 classifier
    samples.csv       top 10,000

## 1. Build the corpus

`source/FontSurvey.py` writes it. Both sides of the corpus are named by
regexps, matched (anywhere, case insensitively) against the font name:

- `-tf/--training-font LABEL=REGEX` - a class of fonts that **needs** a
  decoder. The text drawn in a matching font is written with `LABEL` as its
  class.
- `-nf/--not-required-font REGEX` - fonts that need **no** decoder. Their
  text is the negative class, `not_required`. Repeat the option to list the
  families a few at a time.

For Hindi+English model
```bash
python -m source.FontSurvey -i ~/public/gzdl/raw/bihar  ~/public/gzdl/raw/central_extraordinary ~/public/gzdl/raw/central_weekly  ~/public/gzdl/raw/uttarpradesh_extraordinary  ~/public/gzdl/raw/uttarpradesh_ordinary -r -tc union_bihar.csv -o central_ext.txt -tf nirmala="nirmala\s*ui" -tf arialuni="arial\s*unicode" -tf krutidev="chanakya|kruti\s*dev"  -nf "times|arial|calibri|cambria|courier|helvetica|verdana|tahoma|garamond" -nf "book antiqua|bookman|liberation|nimbus|myriadpro|minionpro" -nf "segoe|malgun|yu gothic" -tf mangal="mangal"
```

For Hindi+Kannada model
```bash
python -m source.FontSurvey -r -tc kannada_samples.csv -o kar_corpus_report.txt -i /home/sushant/public/gzdl/raw/karnataka /home/sushant/public/gzdl/raw/karnataka_daily /home/sushant/public/gzdl/raw/karnataka_weekly /home/sushant/public/gzdl/raw/karnataka_extraordinary -tf nudiuni='nudi\s*uni' -tf nudi='nudi(?!\s*uni)' -tf tunga='tunga' -tf aklite='aklite' -tf nirmala='nirmala' -tf arialuni='arial[\s_-]*unicode' -nf 'times|arial|calibri|cambria|courier|helvetica|verdana|tahoma|garamond' -nf 'book[\s_-]*antiqua|bookman|liberation|nimbus|myriad|minion|segoe|malgun|yu[\s_-]*gothic' -nf 'century|georgia|trebuchet|consolas|candara|corbel|constantia|franklin|gill[\s_-]*sans|lucida|palatino|sylfaen|impact|comic|dejavu|noto|roboto'
```


For Hindi+Tamil model
```bash
python -m source.FontSurvey -i ~/public/gzdl/raw/tamilnadu -r -tc training_data/tamil_samples.csv -o tamil_corpus_report.txt -tf tamelango='tam[\s_-]*elango|tam[\s_-]*vanavil|reginet' -tf vanavil='(?<![a-z])(?<![a-z][\s_-])vanavil' -tf tommy='tommy' -tf tauelango='tau[\s_-]*elango[\s_-]*panchali(?![\s_-]*sc7)' -tf ilasundaram='uni[\s_.-]*ila[\s_.-]*sundaram' -tf marutham='tau[\s_-]*marutham' -nf 'times|arial(?![\s_-]*unicode)|calibri|cambria|courier|helvetica|verdana|tahoma|garamond' -nf 'book[\s_-]*antiqua|bookman|liberation|nimbus|myriad[\s_-]*pro|minion[\s_-]*pro' -nf 'segoe|malgun|yu[\s_-]*gothic|trebuchet|century[\s_-]*gothic|palatino|lucida|georgia|microsoft[\s_-]*sans|dejavu|aptos|rockwell|cgtimes|calisto|clarendon' -nf 'vijaya|tau[\s_-]*elango[\s_-]*valluvan|tau[\s_-]*elango[\s_-]*panchali[\s_-]*sc7'
```

For Hindi+Marathi model
```bash
  nohup python -m source.FontSurvey -i ~/public/gzdl/raw/maharashtra -r \
      -tc training_data/marathi_samples.csv -o marathi_corpus_report.txt \
      -tf yogesh='dvb[\s_-]*w?[\s_-]*tt' \
      -tf surekh='dvttsurekh' \
      -tf dvotsurekh='dvot[-]?surekh' \
      -tf abhishek='abhishek' \
      -tf sakal='sakal[\s_-]*marathi' \
      -tf arialuni='arial[\s_-]*unicode' \
      -tf nirmala='nirmala[\s_-]*ui' \
      -tf mangal='mangal' \
      -tf type3='^type3' \
      -nf 'times|arial(?![\s_-]*unicode)|calibri|cambria|courier|helvetica|verdana|tahoma|garamond' \
      -nf 'newcentury|century[\s_-]*schoolbook|century[\s_-]*gothic|bookman|book[\s_-]*antiqua|calisto|minion[\s_-]*pro|myriad[\s_-]*pro' \
      -nf 'segoe|malgun|yu[\s_-]*gothic|trebuchet|palatino|lucida|georgia|microsoft[\s_-]*sans|dejavu|liberation|nimbus|symbol|aptos|consolas|candara|corbel'  &
```

```bash
nohup python -m source.FontSurvey -r \
      -i ~/public/gzdl/raw/andhra ~/public/gzdl/raw/andhra_extraordinary \
         ~/public/gzdl/raw/andhra_weekly ~/public/gzdl/raw/telangana \
      -tc training_data/telugu_samples.csv -o telugu_corpus_report.txt \
      -tf priyaanka='priyaanka|anupama|pallavi|gowthami' \
      -tf nirmala='nirmala[\s_-]*ui' \
      -tf gautami='gautami' \
      -tf nats='(?<![a-z])nats' \
      -nf 'times|arial(?![\s_-]*unicode)|calibri|cambria|courier|helvetica|verdana|tahoma|garamond' \
      -nf 'book[\s_-]*antiqua|bookman|liberation|nimbus|myriad[\s_-]*pro|minion[\s_-]*pro|calisto|carlito|caladea' \
      -nf 'segoe|malgun|yu[\s_-]*gothic|trebuchet|century[\s_-]*gothic|palatino|lucida|georgia|microsoft[\s_-]*sans|dejavu|aptos|rockwell' \
      -nf 'consolas|constantia|corbel|candara|franklin|agency[\s_-]*fb|maiandra|algerian|batang|mingliu|simsun|ms[\s_-]*mincho|msgothic|sylfaen|symbol|wingdings|zdingbats|arimo|rupeeforadian|arabictransparent' &
```

The whole corpus is the one csv `-tc/--training-csv` names, a row per sample:

```
label,font,pdf,text
chanakya,TT572t00,union_hindi.pdf,"ubZ fnYyh] 3 tqykbZ] 2018 …"
not_required,TimesNewRomanPSMT,act1.pdf,"The following Act of Parliament …"
```

Only `label` and `text` are learned from. `font` and `pdf` are there because
a class that looks wrong is a question the label cannot answer: a regexp like
`chanakya='chanakya|TT[0-9A-F]+t[0-9]+'` matches a *shape*, not an encoding,
so any unrelated `TT…t…` font lands in that class and only the font name says
which. Group the csv to see what actually fed a class:

```bash
python -c "
import csv, collections
rows = [r for r in csv.DictReader(open('training_data/samples.csv'))
        if r['label'] == 'chanakya']
for k, v in sorted(collections.Counter((r['font'], r['pdf']) for r in rows).items()):
    print(v, k)"
```

`type3` is a class like any other: the Type3 fonts of a distilled gazette
carry a broken `ToUnicode` map that `ToUnicodeFixer.fix_type3_fonts` repairs,
and their text extracts as devanagari with holes and stray latin in it
(`रा% &पित क( अनुमित`) - as learnable a signature as latin gibberish is.
pymupdf names an unnamed Type3 font after its xref (`Type3 (314 0 R)`), which
differs per file, so the regexp has to be the bare `^type3` and never the
whole name.

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

### Sample size

One row of the csv is one sample, and one sample is `-tw/--training-words`
words (default 50) of text drawn in one font. A single line of a pdf is about
six words, which is nowhere near enough to tell one encoding from another, so
the runs of a font are stitched together in the order they are drawn until
the sample is that long. Stitching stops at the end of each document, so a
sample never mixes two pdfs, and the tail end of a document is written out
short rather than dropped - a pdf that draws a font only a few times is
exactly the pdf whose text is most worth having.

It costs roughly seven eighths of the sample count and is worth it. On
`test/test_pdfs`, the same five class problem:

| sample          | samples | CA     | macro F1 |
|-----------------|---------|--------|----------|
| one run (`-tw 1`) | 6680  | 0.9379 | 0.9044   |
| 50 words        | 869     | 0.9931 | 0.9690   |

The report prints the mean words per sample per class. A mean well under
`-tw` means that class is made of documents that each draw the font only a
little, so most of its samples are end-of-document leftovers - more pdfs, not
a bigger `-tw`, is what fixes that.

Bootstrapping a class from fonts whose names *do* identify them (Nirmala UI,
Kruti Dev, Times) is the point: once trained, the model labels the text of the
fonts whose names identify nothing - which is exactly the text `-tf`/`-nf`
could not have labelled either, and why it was dropped rather than guessed at.

## 2. Cross validate and train

```bash
python -m machinelearning.training -c training_data/samples.csv \
    -m model/eng_hin_fonts.pkl -k 10 -mc 20000 -vf vocab.json
```

`-c/--corpus` is the csv `FontSurvey -tc` wrote; both options default to
`training_data/samples.csv`, so the pair of commands lines up with neither
given. `features.py` reads it, counts every 1 to 5 word phrase in the corpus,
keeps the
`-tk/--top-k` most frequent (10,000 by default) as the feature set, and
builds a sparse Orange `Table` of per-sample phrase counts with the class
name as the target. `training.py` then runs a stratified `-k` fold
`CrossValidation` over the requested learners, prints accuracy, macro
F1/precision/recall and a confusion matrix per learner, trains the final
model on the whole corpus and pickles it with its vocabulary.

`model/eng_hin_fonts.pkl` is where `source/Main.py` looks for the model by
default (`Main.FONT_MODEL_PATH`); write it anywhere else and the parser needs
`-fm/--font-model` pointed at it. `*.pkl` is tracked with Git LFS, so a clone
needs `git lfs pull` before the checked-in model is anything but a pointer.

Options worth knowing:

- `-mc/--max-per-class` caps the samples read from each class. Real corpora
  are wildly imbalanced (a hundred thousand `not_required` rows against a
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
python -m machinelearning.predict -m model/eng_hin_fonts.pkl -t "fnYyh fodkl izkf/kdj.k"
python -m machinelearning.predict -m model/eng_hin_fonts.pkl -f page_text.txt -w
```

or from python:

```python
from machinelearning.predict import FontClassifier

classifier   = FontClassifier('model/eng_hin_fonts.pkl')
label, prob  = classifier.classify(text_drawn_in_one_font)
```

Hand it about as much text as a training sample holds - `-tw` words of one
font, 50 by default - or more. A single short line carries little evidence,
and it is not what the model was trained on; `-w` treats a whole file as one
sample.

## Files

- `features.py` - corpus reading, phrase counting, vocabulary selection and
  the sparse Orange `Table`
- `training.py` - cross validation, training, model pickling
- `predict.py` - `FontClassifier`, loads a pickled model and classifies text

Requires `Orange3` (in `requirements.txt`).
