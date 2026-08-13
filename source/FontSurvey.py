"""Survey the fonts used by every pdf in a directory.

Walks a directory (optionally recursively), collects every font each pdf
declares, and records a sample of the words that are actually drawn in it -
so a font whose text comes out as garbage (a legacy indic encoding with no
ToUnicode map, the case -fc/--font-conv exists for) is visible from its
sample alone.

    python -m source.FontSurvey -i <directory> [-r] [-mw 100] [-j fonts.json]
"""

import os
import re
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


def strip_subset_prefix(name):
    return SUBSET_PREFIX_RE.sub('', name or '')


def get_words(text):
    for token in text.split():
        # punctuation-only tokens ('---', '(1)', bullets) are not words
        if any(c.isalnum() for c in token):
            yield token


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
    def __init__(self, max_words = 100):
        self.max_words = max_words
        self.fonts     = {}
        self.pdfs      = []
        self.failed    = []
        self.logger    = logging.getLogger('fontsurvey')

    def get_pdf_paths(self, inpath, recursive):
        inpath = Path(inpath)
        if inpath.is_file():
            return [inpath]

        if not inpath.is_dir():
            raise ValueError(f'not a file or directory: {inpath}')

        paths = inpath.rglob('*') if recursive else inpath.glob('*')
        return sorted(p for p in paths \
                      if p.is_file() and p.suffix.lower() == '.pdf')

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

    def survey_drawn_text(self, page, filepath):
        """The text actually drawn on a page, attributed to its span's font."""
        for block in page.get_text('dict').get('blocks', []):
            for line in block.get('lines', []):
                for span in line.get('spans', []):
                    text = span.get('text', '')
                    if not text.strip():
                        continue
                    name = strip_subset_prefix(span.get('font', ''))
                    self.get_record(name).add_text(filepath, page.number, text)

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

    def survey(self, inpath, recursive = False):
        paths = self.get_pdf_paths(inpath, recursive)
        self.logger.info(f'surveying {len(paths)} pdf file(s) under {inpath}')

        relative_to = Path(inpath) if Path(inpath).is_dir() else None
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

        return '\n'.join(lines)


def get_arg_parser():
    parser = argparse.ArgumentParser(\
        description = 'Find the unique fonts used across a directory of pdfs, '
                      'with a sample of the words drawn in each one.')
    parser.add_argument('-i', '--input-path', dest = 'input_path', \
                        action = 'store', required = True, \
                        help = 'directory of pdf files (or a single pdf)')
    parser.add_argument('-r', '--recursive', dest = 'recursive', \
                        action = 'store_true', \
                        help = 'also read pdfs in subdirectories')
    parser.add_argument('-mw', '--max-words', dest = 'max_words', \
                        action = 'store', type = int, default = 100, \
                        help = 'distinct words to keep per font (default 100)')
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

    survey = FontSurvey(max_words = args.max_words)
    try:
        survey.survey(args.input_path, recursive = args.recursive)
    except ValueError as e:
        raise SystemExit(f'error: {e}')

    report = survey.get_report()
    if args.output_file:
        with codecs.open(args.output_file, 'w', encoding = 'utf8') as f:
            f.write(report + '\n')
    else:
        print(report)

    if args.json_file:
        with codecs.open(args.json_file, 'w', encoding = 'utf8') as f:
            f.write(survey.to_json() + '\n')
