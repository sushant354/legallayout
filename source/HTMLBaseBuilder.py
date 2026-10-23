import re
import logging
import unicodedata
import bisect
from pathlib import Path

from .Table import TableBuilder, TOC_PLACEHOLDER
from .TableCell import TableCellArranger
from .NormalizeText import NormalizeText
from .SentenceEndDetector import (
    LEGAL_ABBREVIATIONS,
    EXTENDED_LEGAL_ABBREVIATIONS,
    is_abbreviation_like_token,
    INDIC_SENTENCE_END_CHARS,
    INDIC_SEMICOLON_CHARS,
)
from .Utils import INDIC_WORD_CHARS, INDIC_DIGIT_CHARS, INDIC_LETTER_CHARS, is_separator_line, starts_with_bullet_glyph, \
    follows_clause_break
from .Amendment import Amendment


TAG_FOR_LABEL = {
    "pre": "pre",
    "pre_header": "pre",
    "blockquote": "blockquote",
    "title": "title",
}

SENTENCE_END = ('.', '?', '!', ';', ':', '."', ".'", ';"', ";'", ':-', '—', '...', '…') \
    + tuple(INDIC_SENTENCE_END_CHARS) + tuple(INDIC_SEMICOLON_CHARS)

ABBREVIATIONS = {abbr.lower() for abbr in LEGAL_ABBREVIATIONS} | EXTENDED_LEGAL_ABBREVIATIONS

LAST_TOKEN_RE = re.compile(r'(\S+?)([.?!:;' + INDIC_SENTENCE_END_CHARS + INDIC_SEMICOLON_CHARS + r']+)\s*$')

BULLET_TOKEN_RE = re.compile(r'^\s*(\()?([A-Za-z0-9' + INDIC_WORD_CHARS + INDIC_DIGIT_CHARS + r']{1,4})(?(1)\)|[.\):-])\s+\S')
NUMERIC_PARA_MARKER_RE = re.compile(r'^\s*\d{1,3}(?:\.\d{1,3}){0,4}\.?\s+\S')
STRICT_ROMAN_RE = re.compile(r'^M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$', re.IGNORECASE)

PARA_GAP_FACTOR = 1.15
BLANK_LINE_RATIO = 0.5
FULL_LINE_WIDTH_RATIO = 0.92
PARA_END_RE = re.compile(r'[.?!' + INDIC_SENTENCE_END_CHARS + r'][\)\'"”’\]›»】」』]*\s*$')
MERGE_BOUNDARY_RE = re.compile(r'[.?!:;' + INDIC_SENTENCE_END_CHARS + INDIC_SEMICOLON_CHARS + r'][\)\'"”’\]›»】」』]*\s*$')
STRICT_MARKER_RE = re.compile(r'^(?:\d{1,3}(?:\.\d{1,3}){0,4}[.)]|\(\d{1,3}\)|\([^\W\d_]{1,5}\)|(?:[ivxlcdmIVXLCDM]{1,5}|[^\W\d_])[.)]|[•●▪◦\-–—*])\s+\S')
IGNORABLE_TAIL_RE = re.compile(r'(?:\s|<img [^>]*>)*')
QUOTE_OPEN_RE = re.compile(r'^["\'“‘«‹「『]')
LEADING_WRAP_RE = re.compile(r'^[\(\[\{"\'“‘«‹「『]+')
TITLE_ENUM_RE = re.compile(r'^(?:[IVXLC]{1,5}|[A-Z' + INDIC_WORD_CHARS + r'])\.\s+\S|^Re:\s+\S')
QUOTE_ANNOTATION_RE = re.compile(r'^[\(\[（［][^\(\[\)\]（）［］]+[\)\]）］]$')
INDIC_TEXT_RE = re.compile('[' + INDIC_LETTER_CHARS + ']')
INDIC_TERMINAL_RE = re.compile('[' + INDIC_SENTENCE_END_CHARS + r'][\)\'"”’\]›»】」』]*\s*$')
INDIC_INDENT_RATIO = 0.6
JUSTIFIED_BLOCK_RATIO = 0.6
ENUM_MARKER_RE = re.compile(r'^\s*(?:\((?P<p>[ivxlcdm]{1,8}|[IVXLCDM]{1,8}|[A-Za-z]|\d{1,3})\)'
                            r'|(?P<c>[ivxlcdm]{1,8}|[a-z]|\d{1,3})\)'
                            r'|(?P<d>[ivxlcdm]{1,8}|[IVXLCDM]{1,8}|[a-z]|\d{1,3})\.(?!\d))\s*\S')
ROMAN_VALUES = {'i': 1, 'v': 5, 'x': 10, 'l': 50, 'c': 100, 'd': 500, 'm': 1000}
ENUM_ALIGN_RATIO = 0.5
TRIGGER_END_RE = re.compile(r'[:：][\s\-‐‑‒–—―−:：]*$')
CLOSING_TOKEN_START_RE = re.compile(r'^\s*[^\s()\[\]]{1,8}[)\]]')
BARE_NUMBER_START_RE = re.compile(r'^\s*\d{1,4}\s+\S')
PARENTHETICAL_PERIOD_RE = re.compile(r'[.' + ''.join(INDIC_SENTENCE_END_CHARS) + r']$')
SENTENCE_CLOSE_RE = re.compile(r'[.?!' + ''.join(INDIC_SENTENCE_END_CHARS) + r'][\)\]"\'”’»]*$')
QUOTE_SEQUENCE_MARKER_RE = re.compile(r'(?:(?<=\s)|(?<=>))(?:<[biu]>)*(\([^\W_]{1,6}\)|[^\W_]{1,6}\))(?=\s)')
STYLE_TAG_RE = re.compile(r'<[^>]+>')
BLOCK_CLOSE_RE = re.compile(r'</(?:p|blockquote|table|section|ol|ul|li|h4|center|div|pre)>')

WIDE_SPACE_RE = re.compile(r'[ \t]{2,}')
STYLE_TOKEN_RE = re.compile(r'<(/?)([biu])>')

FOOTNOTE_MARKER_RE = re.compile(r'\{\{\^\{\{FOOTNOTE\s+(\d+)\}\}\}\}')
FOOTNOTE_ABBREVIATION_RE = re.compile(
    r'(?:\b[a-z]\.){2,}$|\b(?:no|ref)\.$',
    re.IGNORECASE
)


class HTMLBaseBuilder(TableCellArranger, TableBuilder):

    track_text_styles = True

    def __init__(self, unique_images, all_footnote_text, sentence_completion_punctuation=tuple(), pdf_type=None,
                 table_extract=False):
        TableBuilder.__init__(self)
        self.table_extract = table_extract
        self.logger = logging.getLogger(__name__)
        self.pdf_type = pdf_type
        self.unique_images = unique_images
        self.all_footnote_text = all_footnote_text
        self.footnote_refs_used = []
        self.current_page_num = None
        self.toc_entries = None
        self.toc_title = None
        self.toc_rendered = False
        self.doc_line_gap = None
        self.doc_max_line_width = None
        self._doc_line_width_xs = None
        self._doc_line_width_ws = None
        self.sentence_completion_punctuation = sentence_completion_punctuation
        self._base_normalize_text = NormalizeText().normalize_text
        self.normalize_text = self._normalize_and_linkify_footnotes
        self.builder = ""
        self.pending_header_footer = []
        self.current_tag = None
        self.current_lines = []
        self._merge_anchor = None
        self._enum_history = []
        self.main_builder = '''<!DOCTYPE HTML>
<html>
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {
    line-height: 1.6;
  }

  p, pre, blockquote {
    white-space: pre-wrap;
  }

  ul {
    list-style-type: none;
  }

  p.figure-text {
    display: none;
  }

  span.header-text, span.footer-text, span.watermark-text {
    display: none;
  }

  span.table-header-text, span.table-footer-text {
    display: none;
  }

  .footnotes {
    display: block;
    font-size: 0.9em;
    border-top: 1px solid #999;
    margin-top: 1em;
  }

  sup a {
    text-decoration: none;
  }

  table {
    border-collapse: collapse;
    width: 100%;
    font-size: 0.95em;
  }

  table, td, th {
    border: 1px solid #333;
  }

  td {
    white-space: pre-wrap;
  }

  nav.toc .toc-title {
    font-weight: bold;
  }

  nav.toc .toc-table {
    border-collapse: collapse;
    width: 100%;
  }

  nav.toc .toc-table td {
    border: none;
    padding: 0.15em 0.4em;
    white-space: pre-wrap;
  }

  nav.toc .toc-page {
    text-align: right;
    color: #555;
    white-space: nowrap;
  }

  img {
    display: block;
    max-width: 100%;
    height: auto;
  }
</style>
</head>
<body>
'''

    def club_lines_into_rows(self, lines):
        units = []
        current_row = []

        def flush_row():
            if current_row:
                units.append({
                    'kind': 'text',
                    'value': ' '.join(l['text'] for l in current_row),
                    'page_num': current_row[0].get('page_num'),
                    'y0': min(l['y0'] for l in current_row),
                    'y1': max(l['y1'] for l in current_row),
                    'x0': min(l['x0'] for l in current_row),
                    'x1': max(l['x1'] for l in current_row),
                    'lead_bold': current_row[0].get('lead_bold', False),
                    'styled': self.join_styled_parts([l.get('styled', l['text']) for l in current_row]),
                    'list_depth': current_row[0].get('list_depth', 0),
                    'list_start': current_row[0].get('bq2_start') == '1',
                    'list_inline': [token for l in current_row if l.get('bq2_inline')
                                    for token in l['bq2_inline'].split('|')],
                })

        for item in lines:
            if 'raw' in item:
                flush_row()
                current_row = []
                units.append({'kind': 'raw', 'value': item['raw']})
                continue

            if current_row:
                prev = current_row[-1]
                row_height = max(prev['y1'] - prev['y0'], item['y1'] - item['y0'], 1.0)
                same_row = abs(prev['y0'] - item['y0']) <= row_height * 0.4
                continues_rightward = item['x0'] >= prev['x1'] - row_height * 0.5
                if same_row and continues_rightward:
                    current_row.append(item)
                    continue
                flush_row()
                current_row = []
            current_row = [item]

        flush_row()
        return units

    def join_styled_parts(self, parts):
        joined = ''
        for part in parts:
            joined = self.join_styled(joined, ' ' + part) if joined else part
        return joined

    def normalize_units(self, units):
        result = []
        for unit in units:
            if unit['kind'] == 'raw':
                result.append(unit)
                continue
            text = self.normalize_text(unit['value'], unit.get('page_num'))
            if text.strip():
                new_unit = dict(unit)
                new_unit['value'] = text
                new_unit['styled'] = self.normalize_text(unit.get('styled', unit['value']), unit.get('page_num'))
                result.append(new_unit)
        return result

    def ends_with_abbreviation(self, text):
        match = LAST_TOKEN_RE.search(text)
        if not match:
            return False
        return is_abbreviation_like_token(match.group(1))

    def strip_trailing_footnote_markers(self, text):
        stripped = text.rstrip()
        while True:
            match = FOOTNOTE_MARKER_RE.search(stripped)
            if not match or match.end() != len(stripped):
                break
            stripped = stripped[:match.start()].rstrip()
        return stripped

    def line_completes_sentence(self, text):
        stripped = self.strip_trailing_footnote_markers(text.strip())
        if not PARA_END_RE.search(stripped):
            return False
        return not self.ends_with_abbreviation(stripped)

    def is_merge_boundary(self, text):
        stripped = self.strip_trailing_footnote_markers(text.strip())
        if not MERGE_BOUNDARY_RE.search(stripped):
            return False
        return not self.ends_with_abbreviation(stripped)

    def ends_with_punct_loose(self, text):
        stripped = self.strip_trailing_footnote_markers(text.strip())
        return bool(MERGE_BOUNDARY_RE.search(stripped))

    @staticmethod
    def is_glyph_bullet(text):
        return starts_with_bullet_glyph(text)

    def is_block_starter(self, text, allow_bullet=True, roman_ok=False):
        stripped = text.strip()
        if not stripped:
            return True
        if self.is_glyph_bullet(stripped):
            return True
        if QUOTE_OPEN_RE.match(stripped):
            return True
        if NUMERIC_PARA_MARKER_RE.match(stripped):
            return True
        if self.is_title_row(stripped):
            return True
        return allow_bullet and self.is_bullet_row(stripped, roman_ok=roman_ok)

    def local_line_width_entries(self):
        if self._doc_line_width_xs is None:
            entries = self.doc_max_line_width or []
            self._doc_line_width_xs = [e[0] for e in entries]
            self._doc_line_width_ws = [e[1] for e in entries]
        return self._doc_line_width_xs, self._doc_line_width_ws

    def is_anchor_line_full(self, units):
        if not self.doc_max_line_width:
            return False
        last = None
        for unit in units:
            if unit.get('kind') == 'text' and 'x0' in unit and 'x1' in unit:
                last = unit
        if last is None:
            return False
        width = last['x1'] - last['x0']
        row_height = last.get('y1', 0) - last.get('y0', 0) or 8.0
        x0_tol = max(10.0, 1.5 * row_height)
        xs, ws = self.local_line_width_entries()
        lo = bisect.bisect_left(xs, last['x0'] - x0_tol)
        hi = bisect.bisect_right(xs, last['x0'] + x0_tol)
        nearby = sorted(ws[lo:hi])
        if len(nearby) < 5:
            nearby = sorted(ws)
        if len(nearby) < 5:
            return False
        idx = min(len(nearby) - 1, int(len(nearby) * 0.9))
        local_max = nearby[idx]
        return width >= local_max * FULL_LINE_WIDTH_RATIO

    def has_continuation_evidence(self, text):
        stripped = text.strip()
        if not stripped:
            return False
        core = LEADING_WRAP_RE.sub('', stripped)
        first = core[0] if core else stripped[0]
        if first.isalpha() and not first.isupper():
            return True
        return self.line_completes_sentence(stripped)

    def group_rows_into_sentences(self, units):
        result = []
        current = []
        pending_raw = []

        def flush_current():
            if current:
                result.append({'kind': 'text', 'value': ' '.join(current)})
                current.clear()
            result.extend(pending_raw)
            pending_raw.clear()

        for unit in units:
            if unit['kind'] == 'raw':
                if current:
                    pending_raw.append(unit)
                else:
                    result.append(unit)
                continue

            current.append(unit['value'])
            stripped = unit['value'].strip()
            if stripped.endswith(SENTENCE_END) and not self.ends_with_abbreviation(stripped):
                flush_current()

        flush_current()
        return result

    def is_roman_numeral(self, token):
        return bool(token) and bool(STRICT_ROMAN_RE.match(token))

    def is_bullet_row(self, row, roman_ok=False):
        match = BULLET_TOKEN_RE.match(row)
        if not match:
            return False

        had_paren = bool(match.group(1))
        token = match.group(2)

        if token.isdigit():
            return len(token) <= 3
        if had_paren:
            return True

        low_token = token.lower()
        if low_token in ABBREVIATIONS or (low_token + '.') in ABBREVIATIONS:
            return roman_ok and self.is_roman_numeral(token)

        if len(token) == 1 and token.isalpha() and token.islower():
            return True

        return len(token) >= 2 and self.is_roman_numeral(token)

    def is_bold_numbered_marker_row(self, unit):
        if not unit.get('lead_bold'):
            return False
        return bool(NUMERIC_PARA_MARKER_RE.match(unit['value']))

    def split_rows_into_bullet_items(self, units):
        items = []
        current = []
        for unit in units:
            if current and unit['kind'] == 'text' and self.is_bullet_row(unit['value']):
                items.append(current)
                current = []
            current.append(unit)
        if current:
            items.append(current)
        return items

    def is_title_row(self, value):
        text = value.strip()
        if not text or text[0].isdigit():
            return False
        return bool(TITLE_ENUM_RE.match(text))

    @staticmethod
    def local_gap_breaks(units, normal):
        if not normal:
            return set()
        texts = [(idx, u) for idx, u in enumerate(units) if u['kind'] == 'text' and 'y0' in u]
        gaps = []
        for (_, upper), (idx, lower) in zip(texts, texts[1:]):
            height = max(lower.get('y1', lower['y0']) - lower['y0'], upper.get('y1', upper['y0']) - upper['y0'])
            gap = upper['y0'] - lower['y0']
            if upper.get('page_num') != lower.get('page_num') or gap < height:
                gaps.append((idx, None, height))
            else:
                gaps.append((idx, gap, height))
        breaks = set()
        for position, (idx, gap, height) in enumerate(gaps):
            if gap is None:
                continue
            neighbours = [g for _, g, _ in gaps[max(0, position - 1):position] + gaps[position + 1:position + 2] if g is not None]
            if not neighbours:
                continue
            smaller = min(neighbours)
            if (smaller * PARA_GAP_FACTOR < normal and gap - smaller >= BLANK_LINE_RATIO * height
                    and gap + BLANK_LINE_RATIO * height > max(neighbours)):
                breaks.add(idx)
        return breaks

    def median_line_gap(self, units):
        if self.doc_line_gap:
            return self.doc_line_gap
        gaps = []
        prev = None
        for unit in units:
            if unit['kind'] != 'text' or 'y0' not in unit:
                continue
            if prev is not None and prev.get('page_num') == unit.get('page_num'):
                gap = prev['y0'] - unit['y0']
                if gap > 0:
                    gaps.append(gap)
            prev = unit
        if not gaps:
            return None
        ordered = sorted(gaps)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[mid]
        return (ordered[mid - 1] + ordered[mid]) / 2.0

    def unit_style_signature(self, unit):
        styled = unit.get('styled')
        if not styled:
            return 'n'
        open_tags = set()
        total = 0
        bold = 0
        italic = 0
        pos = 0
        for match in STYLE_TOKEN_RE.finditer(styled):
            chunk = styled[pos:match.start()]
            count = len(chunk.replace(' ', '').strip())
            total += count
            if 'b' in open_tags:
                bold += count
            if 'i' in open_tags:
                italic += count
            if match.group(1):
                open_tags.discard(match.group(2))
            else:
                open_tags.add(match.group(2))
            pos = match.end()
        chunk = styled[pos:]
        count = len(chunk.replace(' ', '').strip())
        total += count
        if 'b' in open_tags:
            bold += count
        if 'i' in open_tags:
            italic += count
        if not total:
            return 'n'
        if bold == total:
            return 'b'
        if italic == total:
            return 'i'
        return 'n'

    def reaches_block_right(self, unit):
        edge = getattr(self, '_block_right', None)
        if edge is None or 'x1' not in unit:
            return False
        row_height = unit.get('y1', 0) - unit.get('y0', 0) or 8.0
        return unit['x1'] >= edge - 2.5 * row_height

    def style_breaks_paragraph(self, prev_unit, unit, normal):
        prev_sig = self.unit_style_signature(prev_unit)
        sig = self.unit_style_signature(unit)
        if prev_sig == 'n' and sig == 'n':
            return False
        large_gap = bool(
            normal and prev_unit.get('page_num') == unit.get('page_num')
            and 'y0' in prev_unit and 'y0' in unit
            and prev_unit['y0'] - unit['y0'] > normal * PARA_GAP_FACTOR
        )
        plain_next = STYLE_TOKEN_RE.sub('', unit['value']).lstrip()
        if (plain_next[:1].islower() and not self.ends_with_punct_loose(prev_unit['value'])
                and not self.is_bullet_row(plain_next)):
            return False
        prev_full = self.is_anchor_line_full([prev_unit]) or self.reaches_block_right(prev_unit)
        if large_gap and (self.ends_with_punct_loose(prev_unit['value']) or not prev_full):
            return True
        if prev_sig == sig:
            return False
        continues = (prev_full
                     and not self.ends_with_punct_loose(prev_unit['value']))
        return not continues

    def ends_indic_sentence(self, text):
        stripped = self.strip_trailing_footnote_markers(text.strip())
        if INDIC_TERMINAL_RE.search(stripped):
            return True
        return self.line_completes_sentence(stripped)

    def unit_reaches_right(self, unit):
        return self.is_anchor_line_full([unit]) or self.reaches_block_right(unit)

    def block_is_justified(self, units):
        texts = [u for u in units if u['kind'] == 'text' and 'x1' in u]
        if len(texts) < 4 or self._block_right is None:
            return False
        body = [u for u in texts[:-1] if not self.ends_with_punct_loose(u['value'])]
        if len(body) < 3:
            return False
        full = sum(1 for u in body if self.unit_reaches_right(u))
        return full >= len(body) * JUSTIFIED_BLOCK_RATIO

    def indic_layout_starts_paragraph(self, prev_unit, unit, block_left, justified):
        if 'x0' not in unit or 'x0' not in prev_unit:
            return False
        if not (INDIC_TEXT_RE.search(prev_unit['value']) or INDIC_TEXT_RE.search(unit['value'])):
            return False
        if not self.ends_indic_sentence(prev_unit['value']):
            return False
        prev_full = self.unit_reaches_right(prev_unit)
        if block_left is not None:
            row_height = unit.get('y1', 0) - unit.get('y0', 0) or 8.0
            indent = row_height * INDIC_INDENT_RATIO
            if unit['x0'] - block_left >= indent:
                prev_indented = prev_unit['x0'] - block_left >= indent
                return not (prev_indented and prev_full)
        return justified and not prev_full

    def roman_value(self, token):
        if not self.is_roman_numeral(token):
            return None
        total = 0
        values = [ROMAN_VALUES[ch] for ch in token.lower()]
        for idx, value in enumerate(values):
            if idx + 1 < len(values) and values[idx + 1] > value:
                total -= value
            else:
                total += value
        return total or None

    def enum_marker(self, text):
        match = ENUM_MARKER_RE.match(STYLE_TOKEN_RE.sub('', text))
        if not match:
            return None
        style = next(name for name in ('p', 'c', 'd') if match.group(name))
        token = match.group(style)
        readings = set()
        if token.isdigit():
            readings.add(('num', style, None, int(token)))
            return readings
        case = 'u' if token.isupper() else 'l'
        roman = self.roman_value(token)
        if roman:
            readings.add(('roman', style, case, roman))
        if len(token) == 1:
            readings.add(('alpha', style, case, ord(token.lower()) - ord('a') + 1))
        return readings or None

    def enum_follows(self, prev_readings, readings):
        for kind, style, case, value in readings:
            if (kind, style, case, value - 1) in prev_readings:
                return True
        return False

    def enum_aligned(self, prev_x0, unit):
        row_height = unit.get('y1', 0) - unit.get('y0', 0) or 8.0
        return abs(prev_x0 - unit['x0']) <= max(3.0, row_height * ENUM_ALIGN_RATIO)

    def enum_same_list(self, prev_readings, readings):
        kinds = {(kind, style, case) for kind, style, case, _ in prev_readings}
        return any((kind, style, case) in kinds for kind, style, case, _ in readings)

    def enum_follows_history(self, readings, unit):
        for prev_readings, prev_x0 in reversed(self._enum_history):
            if not self.enum_aligned(prev_x0, unit) or not self.enum_same_list(prev_readings, readings):
                continue
            return self.enum_follows(prev_readings, readings)
        return False

    def enum_item_starts(self, units):
        markers = []
        starts = set()
        for idx, unit in enumerate(units):
            if unit['kind'] != 'text' or 'x0' not in unit:
                continue
            readings = self.enum_marker(unit['value'])
            if not readings:
                continue
            for prev_idx, prev_readings, prev_x0 in reversed(markers):
                if self.enum_aligned(prev_x0, unit) and self.enum_follows(prev_readings, readings):
                    starts.add(prev_idx)
                    starts.add(idx)
                    break
            else:
                if self.enum_follows_history(readings, unit):
                    starts.add(idx)
            markers.append((idx, readings, unit['x0']))
        self._enum_history.extend((readings, x0) for _, readings, x0 in markers)
        return starts

    def page_break_ends_paragraph(self, prev_unit, unit, justified):
        if prev_unit.get('page_num') is None or prev_unit.get('page_num') == unit.get('page_num'):
            return False
        if 'x0' not in prev_unit or 'x0' not in unit:
            return False
        if not self.is_merge_boundary(prev_unit['value']) or self.unit_reaches_right(prev_unit):
            return False
        row_height = unit.get('y1', 0) - unit.get('y0', 0) or 8.0
        if abs(prev_unit['x0'] - unit['x0']) >= row_height:
            return True
        return justified and self.line_completes_sentence(prev_unit['value'])

    def hanging_continuation(self, prev_unit, unit):
        if 'x0' not in prev_unit or 'x0' not in unit:
            return False
        row_height = unit.get('y1', 0) - unit.get('y0', 0) or 8.0
        if unit['x0'] - prev_unit['x0'] < row_height:
            return False
        if self.ends_with_punct_loose(prev_unit['value']):
            return False
        return self.unit_reaches_right(prev_unit)

    def split_rows_into_paragraphs(self, units):
        normal = self.median_line_gap(units)
        rights = sorted(u['x1'] for u in units if u['kind'] == 'text' and 'x1' in u)
        self._block_right = rights[int(0.9 * (len(rights) - 1))] if len(rights) >= 3 else None
        lefts = sorted(u['x0'] for u in units if u['kind'] == 'text' and 'x0' in u)
        block_left = lefts[int(0.1 * (len(lefts) - 1))] if len(lefts) >= 3 else None
        justified = self.block_is_justified(units)
        item_starts = self.enum_item_starts(units)
        local_breaks = self.local_gap_breaks(units, normal)
        tight_block = bool(local_breaks)
        groups = []
        current = []
        prev_text = None
        prev_was_marker = True
        prev_was_separator = False
        gap_breaks_seen = 0

        for idx, unit in enumerate(units):
            if unit['kind'] != 'text':
                current.append(unit)
                continue

            marker_boundary = prev_was_marker and not (
                prev_text is not None and self.hanging_continuation(prev_text, unit))
            prev_at_boundary = (prev_text is None or marker_boundary
                                 or self.ends_with_punct_loose(prev_text['value']))
            is_marker_row = self.is_bold_numbered_marker_row(unit) or self.is_glyph_bullet(unit['value']) or (
                prev_at_boundary and (
                    bool(NUMERIC_PARA_MARKER_RE.match(unit['value'].strip()))
                    or self.is_title_row(unit['value'])
                    or self.is_bullet_row(unit['value'], roman_ok=True)
                )
            )
            if is_marker_row and prev_text is not None and (
                    self.parenthetical_continuation(prev_text['value'], unit['value'])
                    or self.closes_open_bracket(prev_text['value'], unit['value'])):
                is_marker_row = False

            starts_para = False
            if idx in item_starts:
                unit['enum_item_start'] = True
            if current and any(u['kind'] == 'text' for u in current):
                if is_marker_row or idx in item_starts:
                    starts_para = True
                elif prev_text is not None and self.style_breaks_paragraph(prev_text, unit, normal):
                    starts_para = True
                elif prev_text is not None and self.indic_layout_starts_paragraph(prev_text, unit, block_left, justified):
                    starts_para = True
                elif prev_text is not None and self.page_break_ends_paragraph(prev_text, unit, justified):
                    starts_para = True
                elif (normal and prev_text is not None
                        and prev_text.get('page_num') == unit.get('page_num')
                        and 'y0' in prev_text and 'y0' in unit):
                    gap = prev_text['y0'] - unit['y0']
                    if (gap > normal * PARA_GAP_FACTOR
                            and self.is_merge_boundary(prev_text['value'])):
                        starts_para = True
                    elif idx in local_breaks:
                        starts_para = True
                if (tight_block and gap_breaks_seen and prev_text is not None
                        and prev_text.get('page_num') is not None
                        and prev_text.get('page_num') != unit.get('page_num')
                        and (not self.unit_reaches_right(prev_text) or self.is_merge_boundary(prev_text['value']))):
                    starts_para = True
                    unit['gap_break'] = True
                if starts_para and idx in local_breaks:
                    unit['gap_break'] = True
                    gap_breaks_seen += 1

            is_separator = self.is_separator_text(unit['value'])
            if current and any(u['kind'] == 'text' for u in current) and (is_separator or prev_was_separator):
                starts_para = True
            prev_was_separator = is_separator

            prev_was_marker = is_marker_row
            if starts_para:
                groups.append(current)
                current = []
            current.append(unit)
            prev_text = unit

        if current:
            groups.append(current)
        return groups

    @staticmethod
    def is_separator_text(text):
        return is_separator_line(text)

    def last_block_is_blockquote(self):
        idx = self.builder.rfind('</blockquote>')
        if idx == -1:
            return -1
        after = self.builder[idx + len('</blockquote>'):]
        if BLOCK_CLOSE_RE.search(after):
            return -1
        return idx

    def emit_paragraph(self, tag, units, is_heading=False):
        text_parts = [u['value'].strip() for u in units if u['kind'] == 'text' and u['value'].strip()]
        styled_parts = [u.get('styled', u['value']).strip() for u in units if u['kind'] == 'text' and u['value'].strip()]
        raws = [u['value'] for u in units if u['kind'] == 'raw']
        if not text_parts:
            if raws:
                self.builder += ''.join(raws) + '\n'
            return
        body_text = WIDE_SPACE_RE.sub(' ', ' '.join(text_parts))
        styled_text = WIDE_SPACE_RE.sub(' ', self.join_styled_parts(styled_parts))
        if is_heading:
            anchor = self._merge_anchor
            if self.line_completes_sentence(body_text):
                is_heading = False
            elif (anchor is not None
                    and not anchor['boundary']
                    and not self.is_block_starter(body_text, allow_bullet=not anchor['line_full'] or anchor['boundary'], roman_ok=anchor['boundary'])
                    and (self.has_continuation_evidence(body_text)
                         or (not STRICT_MARKER_RE.match(body_text.strip())
                             and self.anchor_wrapped(anchor)))):
                is_heading = False
                anchor['is_heading'] = False
        if self.is_separator_text(body_text):
            separator_tag = tag if tag in ("p", "blockquote") else "p"
            self.builder += f"<{separator_tag}>{styled_text}{''.join(raws)}</{separator_tag}>\n"
            self._merge_anchor = None
            return
        if tag == "p" and QUOTE_ANNOTATION_RE.match(body_text):
            idx = self.last_block_is_blockquote()
            if idx != -1:
                self.builder = (self.join_styled(self.builder[:idx], ' ' + styled_text)
                                + self.builder[idx:])
                if raws:
                    self.builder += ''.join(raws) + '\n'
                self._merge_anchor = None
                return
        if self.try_continuation_merge(tag, body_text, styled_text, raws, is_heading, units):
            return
        pieces = self.split_quote_sequence(styled_text) if tag == "blockquote" else None
        if pieces:
            for piece in pieces[:-1]:
                self.builder += f"<blockquote>{piece}</blockquote>\n"
            styled_text = pieces[-1]
            body_text = STYLE_TAG_RE.sub('', styled_text)
        body = styled_text + ''.join(raws)
        start = len(self.builder)
        self.builder += f"<{tag}>{body}</{tag}>\n"
        if tag in ("p", "blockquote"):
            self._merge_anchor = {
                'close_idx': start + len(f"<{tag}>") + len(body),
                'tag': tag,
                'complete': self.line_completes_sentence(body_text),
                'boundary': self.is_merge_boundary(body_text),
                'is_heading': is_heading,
                'ends_with_trigger': bool(TRIGGER_END_RE.search(body_text.strip())),
                'line_full': self.is_anchor_line_full(units),
                'geometry': self.last_text_geometry(units),
                'enum': self.anchor_enum(units),
                'text': body_text,
            }
        else:
            self._merge_anchor = None

    @staticmethod
    def after_clause_break(before, allow_word):
        return follows_clause_break(before, allow_word)

    def split_quote_sequence(self, styled):
        cuts = []
        level = None
        for match in QUOTE_SEQUENCE_MARKER_RE.finditer(styled):
            readings = Amendment.parse_list_marker(match.group(1) + ' x')
            if not readings:
                continue
            before = STYLE_TAG_RE.sub('', styled[:match.start()]).rstrip()
            starts = {r for r in readings if r[3] == 1}
            follows = Amendment.list_follows(level, readings) if level else set()
            if follows and self.after_clause_break(before, True):
                level = follows
                cuts.append(match.start())
            elif starts and len(cuts) < 2 and self.after_clause_break(before, False):
                level = starts
                cuts = [match.start()]
        if len(cuts) < 2:
            return None
        pieces = []
        previous = 0
        carried = ''
        for cut in cuts + [len(styled)]:
            if cut == 0:
                continue
            piece = carried + styled[previous:cut]
            if cut < len(styled):
                piece = re.sub(r'(?:\s|<[biu]>)+$', '', piece)
            open_tags = []
            for tag_match in re.finditer(r'<(/?)([biu])>', piece):
                if tag_match.group(1):
                    if open_tags and open_tags[-1] == tag_match.group(2):
                        open_tags.pop()
                else:
                    open_tags.append(tag_match.group(2))
            if cut < len(styled):
                piece += ''.join(f'</{t}>' for t in reversed(open_tags))
                carried = ''.join(f'<{t}>' for t in open_tags)
            if piece.strip():
                pieces.append(piece.strip())
            previous = cut
        return pieces if len(pieces) > 1 else None

    def last_text_geometry(self, units):
        last = None
        ys = []
        for unit in units:
            if unit.get('kind') == 'text' and 'y0' in unit:
                last = unit
                ys.append((unit.get('page_num'), unit['y0']))
        if last is None:
            return None
        pitches = sorted(a[1] - b[1] for a, b in zip(ys, ys[1:]) if a[0] == b[0] and a[1] > b[1])
        pitch = pitches[len(pitches) // 2] if pitches else 0
        height = last.get('y1', 0) - last['y0']
        texts = [u for u in units if u.get('kind') == 'text' and 'x1' in u]
        block_right = max(u['x1'] for u in texts) if len(texts) >= 2 else None
        return (last.get('page_num'), last['y0'], pitch, last.get('x1'), height, last.get('x0'), block_right)

    def first_text_geometry(self, units):
        for unit in units:
            if unit.get('kind') == 'text' and 'y0' in unit:
                return (unit.get('page_num'), unit['y0'])
        return None

    def gap_separates(self, anchor, units):
        before = anchor.get('geometry')
        after = self.first_text_geometry(units)
        normal = self.doc_line_gap
        if not (before and after and normal) or before[0] != after[0]:
            return False
        own = self.last_text_geometry(units)
        local = max(before[2], own[2] if own else 0)
        return before[1] - after[1] > max(normal, local) * PARA_GAP_FACTOR

    def doc_right_edge(self):
        if getattr(self, '_doc_right_edge', None) is None:
            entries = self.doc_max_line_width or []
            rights = sorted(e[0] + e[1] for e in entries)
            self._doc_right_edge = rights[int(0.9 * (len(rights) - 1))] if rights else False
        return self._doc_right_edge or None

    def anchor_wrapped(self, anchor):
        if anchor['line_full']:
            return True
        geometry = anchor.get('geometry')
        if not geometry or geometry[3] is None:
            return False
        x1, height, x0, block_right = geometry[3], geometry[4] or 0, geometry[5], geometry[6]
        tolerance = 2 * height
        if block_right is not None and x1 >= block_right - tolerance:
            return True
        xs, ws = self.local_line_width_entries()
        if xs and x0 is not None:
            x0_tol = max(10.0, 1.5 * height)
            lo = bisect.bisect_left(xs, x0 - x0_tol)
            hi = bisect.bisect_right(xs, x0 + x0_tol)
            rights = sorted(xs[n] + ws[n] for n in range(lo, hi))
            if len(rights) >= 3:
                return x1 >= rights[int(0.9 * (len(rights) - 1))] - tolerance
        edge = self.doc_right_edge()
        return edge is not None and x1 >= edge - tolerance

    def first_text_unit(self, units):
        for unit in units:
            if unit.get('kind') == 'text':
                return unit
        return None

    def continues_enum_list(self, anchor, units):
        first = self.first_text_unit(units)
        if first is None:
            return False
        if first.get('enum_item_start'):
            return True
        enum = anchor.get('enum')
        if not enum or 'x0' not in first:
            return False
        readings = self.enum_marker(first['value'])
        return bool(readings) and self.enum_aligned(enum[1], first) and self.enum_follows(enum[0], readings)

    def anchor_enum(self, units):
        first = self.first_text_unit(units)
        if first is None or 'x0' not in first:
            return None
        readings = self.enum_marker(first['value'])
        return (readings, first['x0']) if readings else None

    def try_continuation_merge(self, tag, body_text, styled_text, raws, is_heading, units):
        anchor = self._merge_anchor
        if anchor is None or is_heading or anchor['is_heading']:
            return False
        if self.continues_enum_list(anchor, units):
            return False
        if any(u.get('gap_break') for u in units if u.get('kind') == 'text'):
            return False
        if tag in ("p", "blockquote") and anchor['tag'] == tag \
                and self.parenthetical_continuation(anchor.get('text', ''), body_text) \
                and not self.gap_separates(anchor, units):
            return self.append_to_anchor(anchor, body_text, styled_text, raws, units)
        if anchor['complete'] or tag not in ("p", "blockquote"):
            return False
        if tag == "blockquote" and anchor['tag'] != "blockquote" and anchor.get('ends_with_trigger'):
            return False
        allow_bullet = not anchor['line_full'] or anchor['boundary']
        if self.gap_separates(anchor, units):
            return False
        mid_sentence = not anchor['boundary']
        weak_start = mid_sentence and not STRICT_MARKER_RE.match(body_text.strip())
        wrapped_line = self.anchor_wrapped(anchor)
        lowercase_start = body_text.strip()[:1].islower()
        if weak_start and not lowercase_start and not wrapped_line:
            return False
        if not (weak_start and (not body_text.strip()[:1].isupper() or wrapped_line)):
            if (self.is_block_starter(body_text, allow_bullet=allow_bullet, roman_ok=anchor['boundary'])
                    or not self.has_continuation_evidence(body_text)):
                return False
        lead = LEADING_WRAP_RE.sub('', body_text.strip())[:1]
        if lead.isupper() and not wrapped_line:
            return False
        return self.append_to_anchor(anchor, body_text, styled_text, raws, units)

    def append_to_anchor(self, anchor, body_text, styled_text, raws, units):
        close = f"</{anchor['tag']}>"
        idx = anchor['close_idx']
        if (self.builder[idx:idx + len(close)] != close
                or not IGNORABLE_TAIL_RE.fullmatch(self.builder[idx + len(close):])):
            self._merge_anchor = None
            return False
        head = self.join_styled(self.builder[:idx], ' ' + styled_text + ''.join(raws))
        self.builder = head + self.builder[idx:]
        anchor['close_idx'] = len(head)
        anchor['complete'] = self.line_completes_sentence(body_text)
        anchor['boundary'] = self.is_merge_boundary(body_text)
        anchor['line_full'] = self.is_anchor_line_full(units)
        anchor['geometry'] = self.last_text_geometry(units)
        anchor['text'] = body_text
        return True

    @staticmethod
    def closes_open_bracket(previous, following):
        if not CLOSING_TOKEN_START_RE.match(following or ''):
            return False
        text = previous or ''
        return text.count('(') > text.count(')') or text.count('[') > text.count(']')

    @staticmethod
    def parenthetical_continuation(previous, following):
        if not BARE_NUMBER_START_RE.match(following or ''):
            return False
        text = (previous or '').rstrip()
        if not text or text[-1] not in ')]':
            return False
        depth = 0
        opening = None
        for index in range(len(text) - 1, -1, -1):
            if text[index] in ')]':
                depth += 1
            elif text[index] in '([':
                depth -= 1
                if depth == 0:
                    opening = index
                    break
        if not opening:
            return False
        inner = text[opening + 1:-1].rstrip()
        before = text[:opening].rstrip()
        if not inner or not before or not PARENTHETICAL_PERIOD_RE.search(inner):
            return False
        return not SENTENCE_CLOSE_RE.search(before)

    def group_units_for_render(self, units):
        groups = []
        leading_raw = []
        current = None
        for unit in units:
            if unit['kind'] == 'text':
                current = {'text': unit['value'], 'raw': list(leading_raw)}
                leading_raw = []
                groups.append(current)
            elif current is not None:
                current['raw'].append(unit['value'])
            else:
                leading_raw.append(unit['value'])
        if leading_raw:
            groups.append({'text': None, 'raw': leading_raw})
        return groups

    def emit_tag(self, tag, units):
        if not units:
            return

        text_count = sum(1 for unit in units if unit['kind'] == 'text')

        if len(units) == 1 and text_count == 1:
            self.builder += f"<{tag}>{units[0]['value']}</{tag}>\n"
            return

        wrap = text_count > 1
        pieces = []
        for group in self.group_units_for_render(units):
            if group['text'] is None:
                pieces.append(''.join(group['raw']))
            else:
                text_html = f"<span>{group['text']}</span>" if wrap else group['text']
                pieces.append(text_html + ''.join(group['raw']))

        joiner = "<br>\n" if wrap else "\n"
        body = joiner.join(pieces)
        self.builder += f"<{tag}>\n{body}\n</{tag}>\n"

    def emit_each_group_as_own_tag(self, tag, units):
        for group in self.group_units_for_render(units):
            item_units = []
            if group['text'] is not None:
                item_units.append({'kind': 'text', 'value': group['text']})
            item_units.extend({'kind': 'raw', 'value': r} for r in group['raw'])
            self.emit_tag(tag, item_units)

    def render_block(self, tag, lines):
        if tag == "pre":
            self.render_pre_block(lines)
            self._merge_anchor = None
            return

        units = self.normalize_units(self.club_lines_into_rows(lines))
        if not units:
            return

        if tag == "ul":
            self.render_list(units)
            self._merge_anchor = None
            return

        if tag == "title":
            for group in self.split_rows_into_paragraphs(units):
                self.emit_paragraph("p", group, is_heading=True)
            return

        if tag != "p":
            for group in self.split_rows_into_paragraphs(units):
                self.emit_paragraph(tag, group)
            return

        for group in self.split_rows_into_paragraphs(units):
            self.emit_paragraph("p", group)

    def render_list(self, units):
        items = []
        after_separator = False
        leading_raw = ''
        trailing_raw = ''
        for unit in units:
            if unit['kind'] == 'raw':
                if items:
                    trailing_raw += unit['value']
                else:
                    leading_raw += unit['value']
                continue
            if trailing_raw:
                items[-1][1] += trailing_raw
                trailing_raw = ''
            text = WIDE_SPACE_RE.sub(' ', unit.get('styled', unit['value']).strip())
            if not text:
                continue
            depth = unit.get('list_depth', 0)
            pieces = self.split_at_inline_markers(text, unit.get('list_inline', []))
            separator = self.is_separator_text(unit['value'])
            if not items or depth != items[-1][0] or unit.get('list_start') or separator or after_separator:
                items.append([depth, leading_raw + pieces[0]])
                leading_raw = ''
            else:
                items[-1][1] = self.join_styled(items[-1][1], ' ' + pieces[0])
            for piece in pieces[1:]:
                items.append([depth, piece])
            after_separator = separator
        if not items:
            if leading_raw:
                self.builder += leading_raw + '\n'
            return
        html = ''
        depths = []
        for depth, text in items:
            if not depths:
                html += "<ul>\n<li>" + text
                depths.append(depth)
            elif depth > depths[-1]:
                html += "\n<ul>\n<li>" + text
                depths.append(depth)
            else:
                while len(depths) > 1 and depth < depths[-1]:
                    html += "</li>\n</ul>\n"
                    depths.pop()
                html += "</li>\n<li>" + text
        html += "</li>\n</ul>\n" * len(depths)
        self.builder += html + trailing_raw

    @staticmethod
    def split_at_inline_markers(styled, tokens):
        pieces = []
        rest = styled
        for token in tokens:
            match = re.search(r'(?:(?<=\s)|(?<=>)|(?<=\[))' + re.escape(token) + r'(?=\s)', rest)
            if not match or match.start() == 0:
                continue
            head = rest[:match.start()]
            open_tags = []
            for tag_match in re.finditer(r'<(/?)([biu])>', head):
                if tag_match.group(1):
                    if open_tags and open_tags[-1] == tag_match.group(2):
                        open_tags.pop()
                else:
                    open_tags.append(tag_match.group(2))
            head = re.sub(r'(?:\s|<[biu]>)+$', '', head)
            closing = ''.join(f'</{t}>' for t in reversed(open_tags))
            if head.endswith(closing) and closing:
                closing = ''
            pieces.append(head + closing)
            rest = ''.join(f'<{t}>' for t in open_tags) + rest[match.start():]
        pieces.append(rest)
        return pieces

    def flush_block(self):
        if self.current_tag and self.current_lines:
            self.render_block(self.current_tag, self.current_lines)
        self.current_tag = None
        self.current_lines = []

    def addTable(self, table):
        try:
            has_header = self.has_header_row(table)
            table = self.flatten_table_cells(table)
            table_html = (
                table.replace('\n', '&#10;', regex=True)
                    .to_html(escape=False, index=False, header=False)
                    .replace("<table", "<table style='white-space: pre-wrap;'")
            )
            if has_header:
                table_html = self.promote_header_row(table_html)
            self.builder += self.normalize_text(table_html)
            self.builder += "\n"
            self._merge_anchor = None
        except Exception as e:
            self.logger.exception("Error while adding table in html - %s", e)

    def flushTables(self):
        if self.pending_table is not None and len(self.pending_table) <= 2:
            self.addTable(self.pending_table[0])
            self.pending_table = None

    def extract_img_path(self, full_path):
        try:
            p = Path(full_path)
            parts = p.parts
            if 'manifest' in parts:
                idx = parts.index('manifest')
                return str(Path(*parts[idx:]))
            return None
        except Exception as e:
            self.logger.warning(f'Extracting img path while building judgment html {e}')
            return None

    def addFigure(self, tb, page):
        try:
            if tb.figname in self.unique_images:
                img_data = self.unique_images[tb.figname]
                img_path = self.extract_img_path(img_data.get("path", ""))
                width = img_data.get("width")
                height = img_data.get("height")

                size_attrs = ""
                if width:
                    size_attrs += f' width="{width}"'
                if height:
                    size_attrs += f' height="{height}"'

                self.builder += f'<img src="{img_path}"{size_attrs} loading="lazy">\n'

                text_content = img_data.get("text", "")
                if text_content:
                    self.builder += f'<p class="figure-text">{text_content}</p>\n'
                    self._merge_anchor = None
        except Exception as e:
            self.logger.warning(f'While adding figure to judgment html, {e}')

    def add_header(self, text):
        self.add_hidden_span(f'<span class="header-text">{text}</span>')

    def add_footer(self, text):
        self.add_hidden_span(f'<span class="footer-text">{text}</span>')

    def add_watermark(self, text):
        self.add_hidden_span(f'<span class="watermark-text">{text}</span>')

    def add_table_boilerplate(self, table_obj, position):
        text_html = self.render_table_boilerplate_text(table_obj, position)
        if text_html:
            self.add_hidden_span(text_html)

    def add_hidden_span(self, span):
        if self.pending_table:
            self.pending_header_footer.append(span)
        elif self.current_tag:
            self.current_lines.append({'raw': span})
        else:
            self.builder += span + '\n'

    def flush_pending_header_footer(self):
        if self.pending_header_footer:
            self.builder += '\n' + '\n'.join(self.pending_header_footer) + '\n'
            self.pending_header_footer = []

    def _normalize_and_linkify_footnotes(self, text, page_num=None):
        text = self._base_normalize_text(text)
        resolved_page_num = page_num if page_num is not None else self.current_page_num
        if not text or resolved_page_num is None:
            return text

        def replace(match):
            footnote_num = match.group(1)
            ref_key = (resolved_page_num, footnote_num)
            if ref_key not in self.footnote_refs_used:
                self.footnote_refs_used.append(ref_key)
            anchor = f"fn-{resolved_page_num}-{footnote_num}"
            ref = f"fnref-{resolved_page_num}-{footnote_num}"
            return f'<sup id="{ref}"><a href="#{anchor}">{footnote_num}</a></sup>'

        return FOOTNOTE_MARKER_RE.sub(replace, text)

    def arrange_footnote_sentences(self, raw_text):
        rawlines = raw_text.split('\n')
        arranged_text = []
        current_sentence = ""

        for line in rawlines:
            if current_sentence:
                current_sentence += " " + line
            else:
                current_sentence = line

            is_sentence_completed = (current_sentence.endswith(
                                    self.sentence_completion_punctuation)
                                    and
                                    not FOOTNOTE_ABBREVIATION_RE.search(current_sentence)
                                    )

            if is_sentence_completed:
                arranged_text.append(current_sentence.strip())
                current_sentence = ""

        if current_sentence:
            arranged_text.append(current_sentence.strip())

        return " ".join(arranged_text)

    def render_footnote_section(self):
        if not self.footnote_refs_used:
            return

        items = []
        for page_num, footnote_num in sorted(
            self.footnote_refs_used, key=lambda pair: (pair[0], int(pair[1]))
        ):
            page_footnote_text = self.all_footnote_text.get(page_num, {})
            if footnote_num not in page_footnote_text:
                continue

            body = self.arrange_footnote_sentences(page_footnote_text[footnote_num])
            anchor = f"fn-{page_num}-{footnote_num}"
            ref = f"fnref-{page_num}-{footnote_num}"
            items.append(f'<li id="{anchor}" value="{footnote_num}">{body} <a href="#{ref}">↩</a></li>\n')

        if items:
            self.builder += '<section class="footnotes">\n<hr>\n<ol>\n'
            for item in items:
                self.builder += item
            self.builder += '</ol>\n</section>\n'
            self._merge_anchor = None

        self.footnote_refs_used = []

    def build(self, page, has_side_notes):
        self.current_page_num = int(page.pg_num)
        self.page_underlines = self.collect_page_underlines(page)
        visited_for_table = set()

        for watermark in page.watermark_texts:
            self.add_watermark(self.normalize_text(watermark))

        all_items = list(page.all_tbs.items())
        if not self.table_extract:
            all_items = self.apply_pre_grid_labels(all_items, page.is_multicolumn)
        for tb, label in all_items:
            if label == "figure" and getattr(tb, 'figname', None) not in self.unique_images:
                continue
            is_table_label = isinstance(label, tuple) and label[0] in ("table", "borderless_table")
            is_table_boilerplate_label = isinstance(label, tuple) and label[0] in (
                "table_boilerplate", "borderless_table_boilerplate"
            )
            is_header_footer_label = label in ("header", "footer", "footnote")

            if not is_table_label and not is_table_boilerplate_label and not is_header_footer_label \
                    and self.pending_table is not None and len(self.pending_table) <= 2:
                self.addTable(self.pending_table[0])
                self.pending_table = None
                self.flush_pending_header_footer()

            if is_table_boilerplate_label:
                table_id = label[1]
                position = label[2]
                if table_id not in visited_for_table:
                    source_tables = (page.tabular_datas if label[0] == "table_boilerplate"
                                      else page.borderless_tabular_datas).tables
                    table_obj = source_tables.get(table_id)
                    if table_obj is not None:
                        self.add_table_boilerplate(table_obj, position)
                    visited_for_table.add(table_id)
                continue

            if label == "header":
                self.add_header(self.normalize_text(tb.extract_text_from_tb()))
                continue

            if label == "footer":
                self.add_footer(self.normalize_text(tb.extract_text_from_tb()))
                continue

            if label == "footnote":
                continue

            if label == "toc":
                if self.toc_entries and not self.toc_rendered:
                    self.flush_block()
                    self.builder += TOC_PLACEHOLDER
                    self._merge_anchor = None
                    self.toc_rendered = True
                continue

            if label == "figure":
                if tb.figname in self.unique_images:
                    if self.current_tag == "ul" and self.current_lines:
                        mark = len(self.builder)
                        self.addFigure(tb, page)
                        figure_html = self.builder[mark:]
                        self.builder = self.builder[:mark]
                        if figure_html:
                            self.current_lines.append({'raw': figure_html})
                    else:
                        self.flush_block()
                        self.addFigure(tb, page)
                continue

            if is_table_label:
                table_id = label[1]
                if table_id in visited_for_table:
                    continue

                tables_source = page.tabular_datas if label[0] == "table" else page.borderless_tabular_datas
                table_obj = tables_source.tables.get(table_id)
                if table_obj is not None and label[0] == "table":
                    table_obj = self.arrange_table_cells(page, table_id, table_obj)

                if table_obj is not None:
                    self.flush_block()
                    table_width = tables_source.get_table_width(table_id)
                    table_bbox = tables_source.table_bbox.get(table_id) if label[0] == "table" else None
                    table_page = self.current_page_num if table_bbox is not None else None
                    if self.pending_table is None:
                        self.pending_table = [table_obj, table_width]
                        self.pending_table_bbox = table_bbox
                        self.pending_table_page = table_page
                    elif self.is_table_continuation(table_obj, table_width, table_bbox,
                                                    table_page, page.pg_height):
                        self.merge_tables(table_obj, table_width, table_bbox, table_page)
                    else:
                        self.addTable(self.pending_table[0])
                        self.pending_table = [table_obj, table_width]
                        self.pending_table_bbox = table_bbox
                        self.pending_table_page = table_page

                visited_for_table.add(table_id)
                continue

            base_tag, base_depth = self.tag_for_label(label)
            per_line = label is None or label == "blockquote" or (
                isinstance(label, str) and label.startswith("list:"))
            if base_tag != self.current_tag and not per_line:
                self.flush_block()
                self.current_tag = base_tag
            lines = self.extract_textlines(tb)
            for line in lines:
                line['page_num'] = self.current_page_num
                tag, depth = base_tag, base_depth
                if per_line and line.get('bq2'):
                    tag, depth = self.tag_for_label(line['bq2'])
                if tag != self.current_tag:
                    self.flush_block()
                    self.current_tag = tag
                if depth is not None:
                    line['list_depth'] = depth
                self.current_lines.append(line)
            if per_line and not lines and base_tag != self.current_tag:
                self.flush_block()
                self.current_tag = base_tag

    @staticmethod
    def tag_for_label(label):
        if isinstance(label, str) and label.startswith("list:"):
            return "ul", int(label.split(":", 1)[1])
        return TAG_FOR_LABEL.get(label, "p"), None

    def close_html(self):
        if not self.builder:
            return None
        return self.main_builder + self.builder + "\n</body>\n</html>"

    def get_html(self):
        self.flush_block()
        self.flushTables()
        self.flush_pending_header_footer()
        self.render_footnote_section()
        html = self.close_html()
        if html:
            html = self.finalize_toc(html)
        return html
