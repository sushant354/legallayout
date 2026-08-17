"""Survey the fonts used by every pdf in a directory.

Walks a directory (optionally recursively), collects every font each pdf
declares, and records a sample of the words that are actually drawn in it -
so a font whose text comes out as garbage (a legacy indic encoding with no
ToUnicode map, the case -fc/--font-conv exists for) is visible from its
sample alone.

    python -m source.FontSurvey -i <directory> [-r] [-mw 100] [-j fonts.json]
    python -m source.FontSurvey -i "pdfs/**/sebi*.pdf"

It also doubles as the corpus builder for the font classifier in
machinelearning/: -tf/--training-font names a class of fonts needing a
decoder by regexp and every run of text drawn in a font matching it is
written, one sample per line, to <label>.txt in -td/--training-dir;
-nf/--not-required-font names the fonts needing no decoder, which make up
not_required.txt. Text drawn in a font matched by neither is dropped. The
runs of one font are stitched together in drawing order until a sample is
-tw/--training-words long, one line of a pdf being far too little evidence
to classify an encoding from.

    python -m source.FontSurvey -i pdfs/ -r -td training_data \\
        -tf nirmala='nirmala\\s*ui' -tf krutidev='kruti\\s*dev' \\
        -nf 'times|arial|calibri'
"""

import os
import re
import glob
import json
import codecs
import logging
import argparse
import textwrap
from pathlib import Path

import pymupdf


# fonts are usually embedded as subsets, named 'ABCDEF+RealName'. the subset
# tag is per-file, so it is stripped for identity and kept only as a detail
SUBSET_PREFIX_RE = re.compile(r'^[A-Z]{6}\+')

# an input containing any of these is a wildcard pattern to expand ourselves
# rather than a path to open - the shell does it for an unquoted argument, but
# not for a quoted one, and not for one that matched nothing
GLOB_MAGIC_RE = re.compile(r'[*?\[]')


def strip_subset_prefix(name):
    return SUBSET_PREFIX_RE.sub('', name or '')


def get_words(text):
    for token in text.split():
        # punctuation-only tokens ('---', '(1)', bullets) are not words
        if any(c.isalnum() for c in token):
            yield token


class TrainingWriter:
    """Writes the text drawn in each font into a per-class training file.

    Every class is named by a regexp matched against the font name the same
    way -fc/--font-conv matches its font names - anywhere in the name, case
    insensitively - so one 'nirmala\\s*ui' catches 'NirmalaUI', 'Nirmala UI'
    and 'Nirmala UI,Bold' alike. The first regexp that matches wins, and the
    classes needing a decoder (-tf) are tried before the ones needing none
    (-nf), so a font matching both is treated as needing one.

    Both sides of the corpus are named explicitly: -tf says which fonts need
    decoding, -nf says which need none and make up not_required.txt. Text
    drawn in a font that matches neither is *dropped*, because there is no
    way to tell which side it belongs on - and a font that in fact needs
    decoding, silently swept into the negative class, would teach the
    classifier the exact opposite of the truth. The dropped fonts are
    reported so the regexps can be widened to cover them.

    One line is one sample and one sample is min_words words of text drawn in
    one font: a single line of a pdf is far too little evidence to classify
    an encoding from, so the runs of a font are stitched together in the
    order they are drawn until the sample is that long and only then written
    out. Newlines can never appear inside a sample. Stitching stops at the
    end of each document, so a sample never mixes two pdfs, and whatever a
    document ends with is written out short rather than dropped.
    """

    NOT_REQUIRED = 'not_required'
    # the label is used as a filename, so keep it to something that is one
    LABEL_RE     = re.compile(r'^[A-Za-z0-9_-]+$')
    # words per sample. enough to carry a distribution of words and phrases,
    # while still cutting a page of one font into several samples
    MIN_WORDS    = 50

    def __init__(self, outdir, label_res, min_words = MIN_WORDS):
        self.outdir    = Path(outdir)
        self.label_res = label_res
        self.min_words = min_words
        self.files     = {}
        self.counts    = {label: 0 for label, _re in label_res}
        self.words     = {label: 0 for label, _re in label_res}
        self.fonts     = {}
        # text of a font seen so far but not yet long enough to be a sample,
        # per font rather than per class, so two fonts of one class drawn
        # alternately do not interleave inside a sample
        self.pending   = {}
        # fonts no regexp claimed, and how much text was dropped with them
        self.dropped   = {}
        self.logger    = logging.getLogger('fontsurvey.training')
        self.outdir.mkdir(parents = True, exist_ok = True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def get_label(self, fontname):
        """The class this font belongs to, or None to drop its text."""
        for label, regexp in self.label_res:
            if regexp.search(fontname or ''):
                return label
        return None

    def get_file(self, label):
        if label not in self.files:
            path = self.outdir.joinpath(f'{label}.txt')
            self.files[label] = codecs.open(str(path), 'w', encoding = 'utf8')
        return self.files[label]

    def write_sample(self, label, fontname, words):
        self.get_file(label).write(' '.join(words) + '\n')
        self.counts[label] += 1
        self.words[label]  += len(words)
        self.fonts.setdefault(label, set()).add(fontname)

    def add_text(self, fontname, text):
        # a sample is a line, so nothing inside it may be one; the source
        # spans do not contain newlines, but a pdf can draw anything
        words = text.split()
        if not any(c.isalnum() for c in ''.join(words)):
            # rules, bullets and stray punctuation carry no signal at all
            return

        label = self.get_label(fontname)
        if label is None:
            self.dropped[fontname] = self.dropped.get(fontname, 0) + 1
            return

        pending = self.pending.setdefault(fontname, [])
        pending.extend(words)
        if len(pending) >= self.min_words:
            self.write_sample(label, fontname, pending)
            self.pending[fontname] = []

    def flush(self):
        """End of a document: write out every part-built sample as it is.

        A pdf that draws a font only a few times is exactly the pdf whose
        text is most worth having, so a short sample is written rather than
        held back or discarded.
        """
        for fontname, pending in self.pending.items():
            if not pending:
                continue
            label = self.get_label(fontname)
            if label is not None:
                self.write_sample(label, fontname, pending)
        self.pending = {}

    def close(self):
        self.flush()
        for f in self.files.values():
            f.close()
        self.files = {}

    def get_dropped_report(self, max_fonts = 25):
        if not self.dropped:
            return []

        total = sum(self.dropped.values())
        lines = ['', f'dropped: {total} text run(s) in {len(self.dropped)} '
                     f'font(s) matched by neither --training-font nor '
                     f'--not-required-font']
        ranked = sorted(self.dropped.items(), key = lambda kv: (-kv[1], kv[0]))
        for fontname, count in ranked[:max_fonts]:
            lines.append(f'    {fontname or "(unnamed)"}: {count}')
        if len(ranked) > max_fonts:
            lines.append(f'    +{len(ranked) - max_fonts} more font(s)')
        return lines

    def get_report(self):
        lines = ['', '=' * 78, f'training corpus in {self.outdir}', '=' * 78]
        for label in self.counts:
            fonts = sorted(self.fonts.get(label, ()))
            shown = ', '.join(fonts[:10])
            if len(fonts) > 10:
                shown += f', +{len(fonts) - 10} more'
            lines.append('')
            count = self.counts[label]
            words = self.words[label]
            lines.append(f'{label}.txt')
            lines.append(f'    samples : {count}')
            # a mean well under --training-words means the class is made of
            # documents that each draw the font only a little, so most of its
            # samples are end-of-document leftovers rather than full ones
            lines.append(f'    words   : {words}' + \
                         (f' ({words / count:.1f} per sample)' if count else ''))
            lines.append(f'    fonts   : {len(fonts)} ({shown or "none"})')
            if not count:
                lines.append('    | (no text matched - check the regexp)')

        lines.extend(self.get_dropped_report())
        return '\n'.join(lines)


def compile_font_re(label, pattern):
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        raise ValueError(f'bad regex for {label}: {pattern}: {e}')


def parse_training_fonts(specs, not_required_specs = None):
    """The classes to write, in the order a font name is tried against them.

    '-tf nirmala=nirmala\\s*ui -nf times' -> [('nirmala', re), ('not_required',
    re)]. Every -nf regexp feeds the one not_required class, so the fonts
    needing no decoder can be listed a family at a time.
    """
    label_res = []
    for spec in specs or []:
        label, sep, pattern = spec.partition('=')
        label, pattern = label.strip(), pattern.strip()
        if not sep or not label or not pattern:
            raise ValueError(f'training font must be LABEL=REGEX: {spec}')
        if not TrainingWriter.LABEL_RE.match(label):
            raise ValueError(\
                f'training label must be a plain filename [A-Za-z0-9_-]: {label}')
        if label == TrainingWriter.NOT_REQUIRED:
            raise ValueError(\
                f'{TrainingWriter.NOT_REQUIRED} is the class of the fonts '
                f'that need no decoding - name those with '
                f'--not-required-font instead')
        if label in [l for l, _r in label_res]:
            raise ValueError(f'duplicate training label: {label}')
        label_res.append((label, compile_font_re(label, pattern)))

    # the fonts needing a decoder are matched first, so a font caught by both
    # a -tf and a -nf pattern is decoded rather than used as a counterexample
    for pattern in not_required_specs or []:
        pattern = pattern.strip()
        if not pattern:
            raise ValueError('--not-required-font needs a regex')
        label_res.append((TrainingWriter.NOT_REQUIRED, \
                          compile_font_re(TrainingWriter.NOT_REQUIRED, pattern)))
    return label_res


class FontRecord:
    """Everything seen about one font, across all the pdfs it appears in."""

    def __init__(self, name, max_words):
        self.name       = name
        self.max_words  = max_words
        self.subsets    = set()
        self.types      = set()
        self.exts       = set()
        self.encodings  = set()
        self.embedded   = False
        self.tounicode  = False
        # every document the font appears in at all, and the subset of those
        # where it genuinely draws text - a font can be declared in a page's
        # resources and never used
        self.files      = set()
        self.drawn_files= set()
        self.pages      = set()
        self.num_words  = 0
        self.words      = []
        self.word_set   = set()

    def add_declaration(self, basefont, ext, ftype, encoding, has_tounicode):
        if basefont != self.name:
            self.subsets.add(basefont)
        if ftype:
            self.types.add(ftype)
        # get_fonts reports 'n/a' as the extension of a font that is only
        # referenced by name and not embedded in the file
        if ext and ext != 'n/a':
            self.exts.add(ext)
            self.embedded = True
        if encoding:
            self.encodings.add(encoding)
        if has_tounicode:
            self.tounicode = True

    def add_text(self, filepath, pageno, text):
        for word in get_words(text):
            self.num_words += 1
            if word not in self.word_set and len(self.words) < self.max_words:
                self.word_set.add(word)
                self.words.append(word)
        self.files.add(filepath)
        self.drawn_files.add(filepath)
        self.pages.add((filepath, pageno))

    def to_dict(self):
        return {
            'name':          self.name,
            'subset_names':  sorted(self.subsets),
            'types':         sorted(self.types),
            'formats':       sorted(self.exts),
            'embedded':      self.embedded,
            'encodings':     sorted(self.encodings),
            'has_tounicode': self.tounicode,
            'num_drawn_files': len(self.drawn_files),
            'drawn_files':   sorted(self.drawn_files),
            'num_files':     len(self.files),
            'files':         sorted(self.files),
            'num_pages':     len(self.pages),
            'num_words':     self.num_words,
            'words':         self.words,
        }


class FontSurvey:
    def __init__(self, max_words = 100, training = None):
        self.max_words = max_words
        self.fonts     = {}
        self.pdfs      = []
        self.failed    = []
        # an open TrainingWriter, or None to just survey and not write a corpus
        self.training  = training
        self.logger    = logging.getLogger('fontsurvey')

    def get_dir_pdfs(self, directory, recursive):
        paths = directory.rglob('*') if recursive else directory.glob('*')
        return [p for p in paths \
                if p.is_file() and p.suffix.lower() == '.pdf']

    def expand_target(self, path, recursive, from_glob = False):
        """One already-expanded path: a directory of pdfs, or a pdf itself."""
        if path.is_dir():
            return self.get_dir_pdfs(path, recursive)

        if path.is_file():
            # a pattern like '*' legitimately sweeps in non-pdfs, so those are
            # dropped quietly; a file named outright is taken at its word and
            # left to fail loudly at open() if it isn't really a pdf
            if from_glob and path.suffix.lower() != '.pdf':
                return []
            return [path]

        if from_glob:
            return []
        raise ValueError(f'not a file or directory: {path}')

    def get_pdf_paths(self, inpaths, recursive):
        if isinstance(inpaths, (str, Path)):
            inpaths = [inpaths]

        paths = []
        for inpath in inpaths:
            # ~ and any wildcard survive if the shell was not the one that
            # expanded them (a quoted pattern, or one that matched nothing)
            expanded = os.path.expanduser(str(inpath))
            if GLOB_MAGIC_RE.search(expanded):
                matches = glob.glob(expanded, recursive = True)
                if not matches:
                    self.logger.warning(f'pattern matched nothing: {inpath}')
                for match in sorted(matches):
                    paths.extend(self.expand_target(Path(match), recursive, \
                                                    from_glob = True))
            else:
                paths.extend(self.expand_target(Path(expanded), recursive))

        # the same file can be reached by more than one pattern or by both a
        # pattern and a directory, so identity is the resolved path
        unique = {p.resolve(): p for p in paths}
        if not unique:
            raise ValueError(\
                f'no pdf files found: {", ".join(str(p) for p in inpaths)}')
        return sorted(unique.keys())

    def get_display_base(self, paths):
        """The deepest directory all the pdfs sit under, to name them by."""
        try:
            return Path(os.path.commonpath([str(p.parent) for p in paths]))
        except ValueError:
            # different drives, or nothing to compare
            return None

    def get_record(self, name):
        if name not in self.fonts:
            self.fonts[name] = FontRecord(name, self.max_words)
        return self.fonts[name]

    def has_tounicode(self, doc, xref):
        try:
            key, _value = doc.xref_get_key(xref, 'ToUnicode')
        except Exception:
            return False
        return key not in (None, 'null')

    def survey_declared_fonts(self, doc, page, filepath):
        """The fonts in a page's resources, whether or not they draw text."""
        for xref, ext, ftype, basefont, _refname, encoding in \
                [f[:6] for f in page.get_fonts(full = True)]:
            name   = strip_subset_prefix(basefont)
            record = self.get_record(name)
            record.add_declaration(basefont, ext, ftype, encoding, \
                                   self.has_tounicode(doc, xref))
            record.files.add(filepath)

    def get_line_runs(self, line):
        """One line's spans, consecutive same-font ones joined into one run.

        A single word is routinely split across spans, so a run - not a span -
        is the smallest piece of text that is certain to be whole and to be
        drawn entirely in one font.
        """
        runs = []
        for span in line.get('spans', []):
            text = span.get('text', '')
            if not text:
                continue
            name = strip_subset_prefix(span.get('font', ''))
            if runs and runs[-1][0] == name:
                runs[-1][1] += text
            else:
                runs.append([name, text])
        return runs

    def survey_drawn_text(self, page, filepath):
        """The text actually drawn on a page, attributed to its span's font."""
        for block in page.get_text('dict').get('blocks', []):
            for line in block.get('lines', []):
                for name, text in self.get_line_runs(line):
                    if not text.strip():
                        continue
                    self.get_record(name).add_text(filepath, page.number, text)
                    if self.training:
                        self.training.add_text(name, text)

    def survey_pdf(self, path, relative_to = None):
        filepath = str(path.relative_to(relative_to) if relative_to else path)
        try:
            doc = pymupdf.open(path)
        except Exception as e:
            self.logger.warning(f'could not open {filepath}: {e}')
            self.failed.append((filepath, str(e)))
            return

        with doc:
            if doc.needs_pass and not doc.authenticate(''):
                self.logger.warning(f'{filepath} is password protected, skipping')
                self.failed.append((filepath, 'password protected'))
                return

            self.pdfs.append(filepath)
            for page in doc:
                try:
                    self.survey_declared_fonts(doc, page, filepath)
                    self.survey_drawn_text(page, filepath)
                except Exception as e:
                    self.logger.warning(\
                        f'{filepath} page {page.number + 1}: {e}')

        if self.training:
            # samples are stitched across the pages of a document but never
            # across two of them
            self.training.flush()

    def survey(self, inpaths, recursive = False):
        paths = self.get_pdf_paths(inpaths, recursive)
        self.logger.info(f'surveying {len(paths)} pdf file(s)')

        relative_to = self.get_display_base(paths)
        for path in paths:
            self.logger.debug(f'reading {path}')
            self.survey_pdf(path, relative_to)

        self.logger.info(f'found {len(self.fonts)} unique font(s)')
        return self.fonts

    def get_records(self):
        # the fonts used across the most documents first, so the report opens
        # with what the corpus as a whole is set in; only documents where the
        # font genuinely draws text count, and word count breaks ties
        return sorted(self.fonts.values(), \
                      key = lambda r: (-len(r.drawn_files), -r.num_words, \
                                       r.name.lower()))

    def to_json(self):
        return json.dumps({
            'num_pdfs':     len(self.pdfs),
            'pdfs':         self.pdfs,
            'unreadable':   [{'file': f, 'error': e} for f, e in self.failed],
            'max_words':    self.max_words,
            'num_fonts':    len(self.fonts),
            'fonts':        [r.to_dict() for r in self.get_records()],
        }, indent = 2, ensure_ascii = False)

    def get_report(self, max_files = 10):
        records = self.get_records()
        lines   = [
            '=' * 78,
            f'{len(records)} unique font(s) in {len(self.pdfs)} pdf file(s)',
            '=' * 78,
        ]

        for record in records:
            details = record.types and '/'.join(sorted(record.types)) or 'unknown'
            if record.exts:
                details += ' ' + '/'.join(sorted(record.exts))
            details += ', embedded' if record.embedded else ', not embedded'
            if not record.tounicode:
                details += ', no tounicode'

            # the documents the font is drawn in are what it is ranked on, so
            # they are what gets listed; a font never drawn anywhere falls
            # back to listing where it was merely declared
            files = sorted(record.drawn_files) or sorted(record.files)
            shown = ', '.join(files[:max_files])
            if len(files) > max_files:
                shown += f', +{len(files) - max_files} more'

            declared_only = len(record.files) - len(record.drawn_files)
            if record.drawn_files and declared_only:
                shown += f' [+{declared_only} declared only]'

            lines.append('')
            lines.append(record.name or '(unnamed)')
            lines.append(f'    {details}')
            if record.encodings:
                lines.append(f'    encoding : {", ".join(sorted(record.encodings))}')
            lines.append(f'    files    : {len(record.drawn_files)} ({shown})')
            lines.append(f'    pages    : {len(record.pages)}')
            lines.append(f'    words    : {record.num_words} '
                         f'({len(record.words)} distinct shown)')
            if record.words:
                sample = textwrap.wrap(' '.join(record.words), width = 74)
                lines.extend(f'    | {line}' for line in sample)
            elif record.drawn_files:
                # drawn, but every token it draws is punctuation or a symbol
                # glyph (bullets, rules) - no words to sample
                lines.append('    | (draws only punctuation or symbols)')
            else:
                lines.append('    | (declared but never drawn)')

        if self.failed:
            lines.append('')
            lines.append(f'{len(self.failed)} file(s) could not be read:')
            lines.extend(f'    {f}: {e}' for f, e in self.failed)

        if self.training:
            lines.append(self.training.get_report())

        return '\n'.join(lines)


def get_arg_parser():
    parser = argparse.ArgumentParser(\
        description = 'Find the unique fonts used across a directory of pdfs, '
                      'with a sample of the words drawn in each one.')
    parser.add_argument('-i', '--input-path', dest = 'input_paths', \
                        action = 'store', required = True, nargs = '+', \
                        metavar = 'PATH', \
                        help = 'directories of pdf files, single pdfs, or '
                               'wildcard patterns - any mix of them. A '
                               'pattern works whether the shell expands it '
                               '(pdfs/*.pdf) or it is quoted and expanded '
                               'here ("pdfs/*.pdf"); quote it to use ** for '
                               'recursive matching ("pdfs/**/*.pdf")')
    parser.add_argument('-r', '--recursive', dest = 'recursive', \
                        action = 'store_true', \
                        help = 'also read pdfs in subdirectories of any '
                               'directory given')
    parser.add_argument('-mw', '--max-words', dest = 'max_words', \
                        action = 'store', type = int, default = 100, \
                        help = 'distinct words to keep per font (default 100)')
    parser.add_argument('-tf', '--training-font', dest = 'training_fonts', \
                        action = 'append', default = None, \
                        metavar = 'LABEL=REGEX', \
                        help = 'a class of fonts that needs decoding, named '
                               'by a regexp matched anywhere in the font name, '
                               'case insensitively (-tf nirmala="nirmala\\s*ui"). '
                               'Every run of text drawn in a matching font is '
                               'written as one line of LABEL.txt in '
                               '--training-dir. Repeatable, first match wins')
    parser.add_argument('-nf', '--not-required-font', \
                        dest = 'not_required_fonts', action = 'append', \
                        default = None, metavar = 'REGEX', \
                        help = 'fonts that need no decoding, matched the same '
                               'way; their text is the negative class and '
                               'goes to not_required.txt. Repeatable. Text '
                               'drawn in a font matched by neither --training-'
                               'font nor this is dropped, not guessed at')
    parser.add_argument('-tw', '--training-words', dest = 'training_words', \
                        action = 'store', type = int, \
                        default = TrainingWriter.MIN_WORDS, \
                        help = f'words per training sample (default '
                               f'{TrainingWriter.MIN_WORDS}): the runs of text '
                               f'drawn in one font are stitched together in '
                               f'drawing order until the sample is this long. '
                               f'1 writes every run as its own sample, which '
                               f'is usually far too little text to classify '
                               f'an encoding from')
    parser.add_argument('-td', '--training-dir', dest = 'training_dir', \
                        action = 'store', default = None, \
                        help = 'directory to write the per-class training '
                               'files into (default training_data when '
                               '--training-font is given)')
    parser.add_argument('-o', '--output-file', dest = 'output_file', \
                        action = 'store', default = None, \
                        help = 'write the report here instead of stdout')
    parser.add_argument('-j', '--json', dest = 'json_file', action = 'store', \
                        default = None, \
                        help = 'also write the full survey as json here')
    parser.add_argument('-l', '--loglevel', dest = 'loglevel', \
                        action = 'store', default = 'warning', \
                        choices = ['critical', 'error', 'warning', 'info', \
                                   'debug'], \
                        help = 'log level (default warning)')
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

    try:
        label_res = parse_training_fonts(args.training_fonts, \
                                         args.not_required_fonts)
    except ValueError as e:
        raise SystemExit(f'error: {e}')

    if args.training_dir and not label_res:
        raise SystemExit('error: --training-dir needs at least one '
                         '--training-font to say which fonts need decoding')
    if args.training_fonts and not args.not_required_fonts:
        raise SystemExit('error: --training-font needs at least one '
                         '--not-required-font to say which fonts need no '
                         'decoding - without them the corpus has no negative '
                         'class to learn against')
    if args.not_required_fonts and not args.training_fonts:
        raise SystemExit('error: --not-required-font needs at least one '
                         '--training-font to say which fonts need decoding')

    if args.training_words < 1:
        raise SystemExit('error: --training-words must be at least 1')

    training = TrainingWriter(args.training_dir or 'training_data', label_res, \
                              min_words = args.training_words) \
                   if label_res else None
    survey   = FontSurvey(max_words = args.max_words, training = training)
    try:
        survey.survey(args.input_paths, recursive = args.recursive)
    except ValueError as e:
        raise SystemExit(f'error: {e}')
    finally:
        if training:
            training.close()

    report = survey.get_report()
    if args.output_file:
        with codecs.open(args.output_file, 'w', encoding = 'utf8') as f:
            f.write(report + '\n')
    else:
        print(report)

    if args.json_file:
        with codecs.open(args.json_file, 'w', encoding = 'utf8') as f:
            f.write(survey.to_json() + '\n')
