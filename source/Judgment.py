import re
import logging
import bisect
from pathlib import Path

from .Table import TableBuilder, TOC_PLACEHOLDER
from .NormalizeText import NormalizeText
from .SentenceEndDetector import LEGAL_ABBREVIATIONS, EXTENDED_LEGAL_ABBREVIATIONS, is_abbreviation_like_token


TAG_FOR_LABEL = {
    "pre": "pre",
    "pre_header": "pre",
    "blockquote": "blockquote",
    "title": "title",
}

SENTENCE_END = ('.', '?', '!', ';', ':', '."', ".'", ';"', ";'", ':-', '—', '...', '…')

ABBREVIATIONS = {abbr.lower() for abbr in LEGAL_ABBREVIATIONS} | EXTENDED_LEGAL_ABBREVIATIONS

LAST_TOKEN_RE = re.compile(r'(\S+?)([.?!:;]+)\s*$')

BULLET_TOKEN_RE = re.compile(r'^\s*(\()?([A-Za-z0-9]{1,4})(?(1)\)|[.\):-])\s+\S')
NUMERIC_PARA_MARKER_RE = re.compile(r'^\s*\d{1,3}(?:\.\d{1,3}){0,4}\.?\s+\S')
STRICT_ROMAN_RE = re.compile(r'^M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$', re.IGNORECASE)

PARA_GAP_FACTOR = 1.15
FULL_LINE_WIDTH_RATIO = 0.92
PARA_END_RE = re.compile(r'[.?!।॥][\)\'"”’\]›»】」』]*\s*$')
MERGE_BOUNDARY_RE = re.compile(r'[.?!।॥:;][\)\'"”’\]›»】」』]*\s*$')
QUOTE_OPEN_RE = re.compile(r'^["\'“‘«‹「『]')
LEADING_WRAP_RE = re.compile(r'^[\(\[\{"\'“‘«‹「『]+')
TITLE_ENUM_RE = re.compile(r'^(?:[IVXLC]{1,5}|[A-Z])\.\s+\S|^Re:\s+\S')
QUOTE_ANNOTATION_RE = re.compile(r'^[\(\[（［][^\(\[\)\]（）［］]+[\)\]）］]$')
BLOCK_CLOSE_RE = re.compile(r'</(?:p|blockquote|table|section|ol|ul|li|h4|center|div|pre)>')

FOOTNOTE_MARKER_RE = re.compile(r'\{\{\^\{\{FOOTNOTE\s+(\d+)\}\}\}\}')
FOOTNOTE_ABBREVIATION_RE = re.compile(
    r'(?:\b[a-z]\.){2,}$|\b(?:no|ref)\.$',
    re.IGNORECASE
)


class JudgmentBuilder(TableBuilder):

    def __init__(self, unique_images, all_footnote_text, sentence_completion_punctuation=tuple(), pdf_type=None):
        TableBuilder.__init__(self)
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

  p.figure-text {
    display: none;
  }

  span.header-text, span.footer-text {
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

    def is_block_starter(self, text, allow_bullet=True):
        stripped = text.strip()
        if not stripped:
            return True
        if QUOTE_OPEN_RE.match(stripped):
            return True
        if NUMERIC_PARA_MARKER_RE.match(stripped):
            return True
        if self.is_title_row(stripped):
            return True
        return allow_bullet and self.is_bullet_row(stripped)

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

    def is_bullet_row(self, row):
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
            return False

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

    def split_rows_into_paragraphs(self, units):
        normal = self.median_line_gap(units)
        groups = []
        current = []
        prev_text = None
        prev_was_marker = True

        for unit in units:
            if unit['kind'] != 'text':
                current.append(unit)
                continue

            prev_at_boundary = (prev_text is None or prev_was_marker
                                 or self.ends_with_punct_loose(prev_text['value']))
            is_marker_row = self.is_bold_numbered_marker_row(unit) or (
                prev_at_boundary and (
                    bool(NUMERIC_PARA_MARKER_RE.match(unit['value'].strip()))
                    or self.is_title_row(unit['value'])
                    or self.is_bullet_row(unit['value'])
                )
            )

            starts_para = False
            if current and any(u['kind'] == 'text' for u in current):
                if is_marker_row:
                    starts_para = True
                elif (normal and prev_text is not None
                        and prev_text.get('page_num') == unit.get('page_num')
                        and 'y0' in prev_text and 'y0' in unit):
                    gap = prev_text['y0'] - unit['y0']
                    if (gap > normal * PARA_GAP_FACTOR
                            and self.is_merge_boundary(prev_text['value'])):
                        starts_para = True

            prev_was_marker = is_marker_row
            if starts_para:
                groups.append(current)
                current = []
            current.append(unit)
            prev_text = unit

        if current:
            groups.append(current)
        return groups

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
        raws = [u['value'] for u in units if u['kind'] == 'raw']
        if not text_parts:
            if raws:
                self.builder += ''.join(raws) + '\n'
            return
        body_text = ' '.join(text_parts)
        if is_heading:
            anchor = self._merge_anchor
            if self.line_completes_sentence(body_text):
                is_heading = False
            elif (anchor is not None
                    and not anchor['boundary']
                    and not self.is_block_starter(body_text, allow_bullet=not anchor['line_full'])
                    and self.has_continuation_evidence(body_text)):
                is_heading = False
                anchor['is_heading'] = False
        if tag == "p" and QUOTE_ANNOTATION_RE.match(body_text):
            idx = self.last_block_is_blockquote()
            if idx != -1:
                self.builder = (self.builder[:idx]
                                + ' ' + body_text
                                + self.builder[idx:])
                if raws:
                    self.builder += ''.join(raws) + '\n'
                self._merge_anchor = None
                return
        if self.try_continuation_merge(tag, body_text, raws, is_heading, units):
            return
        body = body_text + ''.join(raws)
        start = len(self.builder)
        self.builder += f"<{tag}>{body}</{tag}>\n"
        if tag in ("p", "blockquote"):
            self._merge_anchor = {
                'close_idx': start + len(f"<{tag}>") + len(body),
                'tag': tag,
                'complete': self.line_completes_sentence(body_text),
                'boundary': self.is_merge_boundary(body_text),
                'is_heading': is_heading,
                'line_full': self.is_anchor_line_full(units),
            }
        else:
            self._merge_anchor = None

    def try_continuation_merge(self, tag, body_text, raws, is_heading, units):
        anchor = self._merge_anchor
        if anchor is None or is_heading or anchor['is_heading']:
            return False
        if anchor['complete'] or tag not in ("p", "blockquote"):
            return False
        allow_bullet = not anchor['line_full']
        if self.is_block_starter(body_text, allow_bullet=allow_bullet) or not self.has_continuation_evidence(body_text):
            return False
        close = f"</{anchor['tag']}>"
        idx = anchor['close_idx']
        if (len(self.builder) != idx + len(close)
                or self.builder[idx:idx + len(close)] != close):
            self._merge_anchor = None
            return False
        insert = ' ' + body_text + ''.join(raws)
        self.builder = self.builder[:idx] + insert + self.builder[idx:]
        anchor['close_idx'] = idx + len(insert)
        anchor['complete'] = self.line_completes_sentence(body_text)
        anchor['boundary'] = self.is_merge_boundary(body_text)
        anchor['line_full'] = self.is_anchor_line_full(units)
        return True

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

    def flush_block(self):
        if self.current_tag and self.current_lines:
            self.render_block(self.current_tag, self.current_lines)
        self.current_tag = None
        self.current_lines = []

    def addTable(self, table):
        try:
            table_html = (
                table.replace('\n', '&#10;', regex=True)
                    .to_html(escape=False, index=False, header=False)
                    .replace("<table", "<table style='white-space: pre-wrap;'")
            )
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
        visited_for_table = set()

        for tb, label in page.all_tbs.items():
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
                    self.flush_block()
                    self.addFigure(tb, page)
                continue

            if is_table_label:
                table_id = label[1]
                if table_id in visited_for_table:
                    continue

                tables_source = page.tabular_datas if label[0] == "table" else page.borderless_tabular_datas
                table_obj = tables_source.tables.get(table_id)

                if table_obj is not None:
                    self.flush_block()
                    table_width = tables_source.get_table_width(table_id)
                    if self.pending_table is None:
                        self.pending_table = [table_obj, table_width]
                    elif self.is_table_continuation(table_obj, table_width):
                        self.merge_tables(table_obj, table_width)
                    else:
                        self.addTable(self.pending_table[0])
                        self.pending_table = [table_obj, table_width]

                visited_for_table.add(table_id)
                continue

            tag = TAG_FOR_LABEL.get(label, "p")
            if tag != self.current_tag:
                self.flush_block()
                self.current_tag = tag
            lines = self.extract_textlines(tb)
            for line in lines:
                line['page_num'] = self.current_page_num
            self.current_lines.extend(lines)

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
