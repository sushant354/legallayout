import re
import logging
from pathlib import Path

from .Table import TableBuilder
from .NormalizeText import NormalizeText
from .SentenceEndDetector import LEGAL_ABBREVIATIONS, EXTENDED_LEGAL_ABBREVIATIONS, is_abbreviation_like_token


TAG_FOR_LABEL = {
    "pre": "pre",
    "pre_header": "pre",
    "blockquote": "blockquote",
}

SENTENCE_END = ('.', '?', '!', ';', ':', '."', ".'", ';"', ";'", ':-', '—', '...', '…')

ABBREVIATIONS = {abbr.lower() for abbr in LEGAL_ABBREVIATIONS} | EXTENDED_LEGAL_ABBREVIATIONS

LAST_TOKEN_RE = re.compile(r'(\S+?)([.?!:;]+)\s*$')

BULLET_TOKEN_RE = re.compile(r'^\s*(\()?([A-Za-z0-9]{1,4})(?(1)\)|[.\):-])\s+\S')
STRICT_ROMAN_RE = re.compile(r'^M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$', re.IGNORECASE)

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
        self.toc_html = None
        self.toc_rendered = False
        self.sentence_completion_punctuation = sentence_completion_punctuation
        self._base_normalize_text = NormalizeText().normalize_text
        self.normalize_text = self._normalize_and_linkify_footnotes
        self.builder = ""
        self.pending_header_footer = []
        self.current_tag = None
        self.current_lines = []
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
                result.append({'kind': 'text', 'value': text})
        return result

    def ends_with_abbreviation(self, text):
        match = LAST_TOKEN_RE.search(text)
        if not match:
            return False
        return is_abbreviation_like_token(match.group(1))

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

        return len(token) >= 2 and self.is_roman_numeral(token)

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
            return

        units = self.normalize_units(self.club_lines_into_rows(lines))
        if not units:
            return

        if tag != "p":
            self.emit_each_group_as_own_tag(tag, self.group_rows_into_sentences(units))
            return

        for item_units in self.split_rows_into_bullet_items(units):
            self.emit_each_group_as_own_tag(tag, self.group_rows_into_sentences(item_units))

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
        except Exception as e:
            self.logger.warning(f'While adding figure to judgment html, {e}')

    def add_header(self, text):
        self.add_hidden_span(f'<span class="header-text">{text}</span>')

    def add_footer(self, text):
        self.add_hidden_span(f'<span class="footer-text">{text}</span>')

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

        self.footnote_refs_used = []

    def build(self, page, has_side_notes):
        self.current_page_num = int(page.pg_num)
        visited_for_table = set()

        for tb, label in page.all_tbs.items():
            is_table_label = isinstance(label, tuple) and label[0] in ("table", "borderless_table")
            is_header_footer_label = label in ("header", "footer", "footnote")

            if not is_table_label and not is_header_footer_label \
                    and self.pending_table is not None and len(self.pending_table) <= 2:
                self.addTable(self.pending_table[0])
                self.pending_table = None
                self.flush_pending_header_footer()

            if label == "header":
                self.add_header(self.normalize_text(tb.extract_text_from_tb()))
                continue

            if label == "footer":
                self.add_footer(self.normalize_text(tb.extract_text_from_tb()))
                continue

            if label == "footnote":
                continue

            if label == "toc":
                if self.toc_html and not self.toc_rendered:
                    self.flush_block()
                    self.builder += self.toc_html
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
        return self.close_html()
