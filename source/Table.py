import pandas as pd
import re
import math
import numpy as np
import html as html_lib
from difflib import SequenceMatcher
import logging

from .NormalizeText import NormalizeText
from .Utils import INDIC_WORD_CHARS, INDIC_DIGIT_CHARS, INDIC_NONZERO_DIGIT_CHARS, INDIC_COMBINING_MARK_CHARS, \
    is_schedule, is_annexure, is_appendix, is_part, _term_alternation, \
    indic_nil_alternation, indic_unit_time_alternation, indic_serial_no_alternation, \
    indic_number_word_alternation, indic_item_alternation

COMBINING_MARK_START_RE = re.compile(r'^[' + INDIC_COMBINING_MARK_CHARS + r']')
TABLE_FOOTNOTE_MARKER_RE = re.compile(r'\{\{\^\{\{FOOTNOTE\s+(\d+)\}\}\}\}')
BOLD_FONT_RE = re.compile(r'bold', re.IGNORECASE)
STYLE_BOLD_FONT_RE = re.compile(r'bold|demi|heavy|black', re.IGNORECASE)
STYLE_ITALIC_FONT_RE = re.compile(r'italic|oblique', re.IGNORECASE)
STYLE_TAGS = ('b', 'i', 'u')
UNDERLINE_BELOW_TOLERANCE = 2.5
UNDERLINE_ABOVE_RATIO = 0.4
UNDERLINE_X_TOLERANCE = 0.5
UNDERLINE_MAX_WIDTH_RATIO = 1.3
UNDERLINE_WIDTH_SLACK = 4.0
STYLE_SEAM_CLOSE_RE = re.compile(r'((?:</[biu]>)+)$')
STYLE_SEAM_OPEN_RE = re.compile(r'^(\s*)((?:<[biu]>)+)')
PRE_KEEP_SPAN_RE = re.compile(
    r'<span[^>]*class="(?:header-text|footer-text|table-header-text|table-footer-text)"')

PRE_GRID_COLUMN_GAP_FACTOR = 2.0
PRE_GRID_ALIGN_FACTOR = 0.5
PRE_GRID_MIN_ROWS = 3
PRE_GRID_MIN_MULTICOL_ROWS = 2
PRE_GRID_MIN_INTERIOR_COLUMNS = 2
PRE_GRID_MIN_TOTAL_COLUMNS = 3
PRE_GRID_MIN_COLUMN_ROWS = 2
PRE_GRID_COLUMN_SUPPORT_FRAC = 0.05
PRE_GRID_GUTTER_FACTOR = 2.0
PRE_GRID_SPINE_FRAC = 0.75
PRE_GRID_MIN_SPINE_ROWS = 3
PRE_GRID_MAX_ROW_SKIP = 1
PRE_GRID_INDEX_FRACTION = 0.5
PRE_GRID_MIN_DATA_FRAC = 0.5
PRE_GRID_DUPLICATE_OVERLAP_RATIO = 0.5
PRE_GRID_RAGGED_MAX_COLUMNS = PRE_GRID_MIN_TOTAL_COLUMNS + 1
PRE_GRID_WIDE_COLUMN_FRAC = 0.4


def table_dataframe_signature(df):
    try:
        cells = [
            str(value).strip()
            for row in df.itertuples(index=False, name=None)
            for value in row
        ]
    except Exception:
        return ""
    text = ' '.join(cell for cell in cells if cell and cell.lower() != 'nan')
    return re.sub(r'\s+', ' ', text).strip()


DIGIT_RUN_RE = re.compile(r'\d+')

# a cell holding real data (a count, a serial number, a date) is exactly the
# thing a templated table varies row to row and page to page - and it can sit
# inside an otherwise-static sentence, where a plain text-similarity ratio
# barely moves ("Total: 5 seats" vs "Total: 8 seats" still scores ~0.9). \d is
# unicode-aware, so this reads a digit run in any script's own digits (Latin,
# Devanagari, Malayalam, Tamil, ...) without needing to know which script it is
def _cell_similarity(a, b):
    if a == b:
        return 1.0
    digits_a = DIGIT_RUN_RE.findall(a)
    digits_b = DIGIT_RUN_RE.findall(b)
    if (digits_a or digits_b) and digits_a != digits_b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def table_dataframe_cell_match_ratio(df1, df2, min_cell_ratio=0.6):
    if df1 is None or df2 is None or df1.shape != df2.shape:
        return 0.0
    try:
        cells1 = [str(v).strip().lower() for row in df1.itertuples(index=False, name=None) for v in row]
        cells2 = [str(v).strip().lower() for row in df2.itertuples(index=False, name=None) for v in row]
    except Exception:
        return 0.0
    if not cells1:
        return 0.0
    ratios = [_cell_similarity(a, b) for a, b in zip(cells1, cells2)]
    # one cell reading as wildly different must never be smoothed away by
    # every other cell in a large table matching perfectly - a single genuine
    # content difference, however small a fraction of the table it is, means
    # this is not the same boilerplate repeated
    if min(ratios) < min_cell_ratio:
        return 0.0
    return sum(ratios) / len(ratios)


def table_dataframe_flattened_text(df, col_sep='    ', row_sep='&#10;'):
    try:
        rows = list(df.itertuples(index=False, name=None))
    except Exception:
        return ""
    lines = []
    for row in rows:
        cells = [str(value).strip() for value in row]
        cells = [cell for cell in cells if cell and cell.lower() != 'nan']
        if cells:
            lines.append(col_sep.join(cells))
    return row_sep.join(lines)

TOC_PLACEHOLDER = '{{__TOC_ANCHOR_PLACEHOLDER__}}'
TOC_TAG_NAMES = ('h4', 'p', 'li', 'blockquote')
TOC_TAG_OPEN_ANY_RE = re.compile(
    r'<(' + '|'.join(TOC_TAG_NAMES) + r')(?![a-zA-Z])([^>]*)>'
)
TOC_TAG_OPEN_RES = {t: re.compile(r'<' + t + r'(?![a-zA-Z])[^>]*>') for t in TOC_TAG_NAMES}
TOC_TAG_CLOSE_RES = {t: re.compile(r'</' + t + r'>') for t in TOC_TAG_NAMES}
TOC_TAG_STRIP_RE = re.compile(r'<[^>]+>')
TOC_LEADING_ENUM_RE = re.compile(
    r'^[\s"“”\'.\-–—]*(?:\(?[a-z0-9' + INDIC_WORD_CHARS + INDIC_DIGIT_CHARS + r']{1,4}\)?[.\):])+\s*',
    re.IGNORECASE
)
TOC_LINK_BREAK_RE = re.compile(r'<(?:ul|ol|table|pre|blockquote|p|div|h4|li|nav|section)(?![a-zA-Z])', re.IGNORECASE)
TOC_STYLE_TOKEN_RE = re.compile(r'<(/?)([biu])>')
TOC_MATCH_THRESHOLD = 0.6
TOC_MIN_CONTAINED_TARGET_LEN = 8


def toc_block_text(fragment):
    text = TOC_TAG_STRIP_RE.sub(' ', fragment)
    text = html_lib.unescape(text)
    return re.sub(r'\s+', ' ', text).strip()


def toc_normalize(text):
    text = text.lower()
    text = TOC_LEADING_ENUM_RE.sub('', text)
    text = re.sub(r'[^a-z0-9' + INDIC_WORD_CHARS + INDIC_DIGIT_CHARS + r']+', ' ', text)
    return text.strip()


def toc_link_length(body):
    found = TOC_LINK_BREAK_RE.search(body)
    end = found.start() if found else len(body)
    segment = body[:end].rstrip()
    if not segment.strip():
        return 0
    open_tags = []
    for token in TOC_STYLE_TOKEN_RE.finditer(segment):
        if token.group(1):
            if not open_tags or open_tags[-1] != token.group(2):
                return 0
            open_tags.pop()
        else:
            open_tags.append(token.group(2))
    if open_tags:
        return 0
    return len(segment)


def toc_best_containment_match(candidates, target, start, end):
    best_idx = None
    best_len_diff = None
    for idx in range(start, end):
        cand_norm = candidates[idx]['norm']
        if cand_norm in target:
            contained = True
        elif target in cand_norm:
            contained = len(target) >= TOC_MIN_CONTAINED_TARGET_LEN
        else:
            contained = False
        if not contained:
            continue

        len_diff = abs(len(cand_norm) - len(target))
        if best_idx is None or len_diff < best_len_diff:
            best_idx = idx
            best_len_diff = len_diff
            if len_diff == 0:
                break
    return best_idx


def toc_iter_blocks(html, start=0, end=None):
    if end is None:
        end = len(html)
    pos = start
    while pos < end:
        match = TOC_TAG_OPEN_ANY_RE.search(html, pos, end)
        if not match:
            return

        tag = match.group(1)
        open_re = TOC_TAG_OPEN_RES[tag]
        close_re = TOC_TAG_CLOSE_RES[tag]

        depth = 1
        scan_pos = match.end()
        body_start = match.end()
        body_end = None
        close_end = None
        while depth > 0:
            next_open = open_re.search(html, scan_pos, end)
            next_close = close_re.search(html, scan_pos, end)
            if not next_close:
                break
            if next_open and next_open.start() < next_close.start():
                depth += 1
                scan_pos = next_open.end()
            else:
                depth -= 1
                scan_pos = next_close.end()
                if depth == 0:
                    body_end = next_close.start()
                    close_end = next_close.end()

        if body_end is None:
            pos = match.end()
            continue

        yield {
            'insert_pos': match.end(1),
            'attrs': match.group(2),
            'body': html[body_start:body_end],
            'body_start': body_start,
        }
        yield from toc_iter_blocks(html, body_start, body_end)
        pos = close_end


TABLE_MERGE_ROW_GAP_MULTIPLIER = 3
TABLE_MERGE_BOTTOM_MARGIN_RATIO = 0.15
TABLE_MERGE_TOP_BAND_RATIO = 0.30
TABLE_MERGE_WIDTH_RATIO = 0.85
TABLE_MERGE_X_OVERLAP_RATIO = 0.6


class TableBuilder:
    track_text_styles = False
    page_underlines = ()

    def __init__(self):
        self.pending_table = None
        self.pending_table_bbox = None
        self.pending_table_page = None
        self.min_word_threshold_tableRows = 2
        self._pre_grid_spine = None
        self.table_terminators = {".", "!", "?"}
        self.logger = logging.getLogger(__name__)
        
        self.serial_patterns = [
            r"^(\(?[1-9" + INDIC_NONZERO_DIGIT_CHARS + r"]\d*[\)\)]?[\.\)]?)$",  # Numbers with brackets/dots: (1), 1., 1)
            r"^([a-zA-Z" + INDIC_WORD_CHARS + r"][\)\.]?)$",           # Single letters with brackets/dots: a), A.
            r"^([ivxlcdmIVXLCDM]+[\)\.]?)$",   # Roman numerals with brackets/dots: i), ii.
            r"^(\(?[1-9" + INDIC_NONZERO_DIGIT_CHARS + r"]\d*\)[\.\:]?)",       # (1). or (1):
            r"^([a-zA-Z" + INDIC_WORD_CHARS + r"]\d+(\.\d+)?)$",       # Alphanumeric: a1, a2.1
            r"^(\d+(\.\d+)?[a-zA-Z" + INDIC_WORD_CHARS + r"])$",       # Numeric-letter: 1a, 2.1b
            r"^(sec|art|clause|section)\s*[\-\:]?\s*\d+",  # Legal references
            r"^(\d+\s*of\s*\d+)$",            # "1 of 10" pattern
        ]
    
    def render_table_boilerplate_text(self, table_obj, position):
        if table_obj is None:
            return ""
        flattened = table_dataframe_flattened_text(table_obj)
        if not flattened:
            return ""
        return f'<span class="table-{position}-text">{self.normalize_text(flattened)}</span>'

    def apply_table_footnote_markers(self, page, table_id, table_obj):
        marker_tbs = [
            tb for tb, label in page.all_tbs.items()
            if label == ("table", table_id) and tb.footnotes_superscript
        ]
        if not marker_tbs:
            return table_obj

        df = table_obj.copy()

        for tb in marker_tbs:
            text = tb.extract_text_from_tb()
            for match in TABLE_FOOTNOTE_MARKER_RE.finditer(text):
                footnote_num = match.group(1)
                preceding = text[:match.start()].rstrip()
                word_match = re.search(r'(\S+)$', preceding)
                if not word_match:
                    continue

                preceding_word = word_match.group(1)
                needle = preceding_word + footnote_num
                replacement = (preceding_word + '{{^{{FOOTNOTE ' + footnote_num
                              + '@' + str(page.pg_num) + '}}}}')

                for row_idx in range(df.shape[0]):
                    for col_idx in range(df.shape[1]):
                        cell = df.iat[row_idx, col_idx]
                        if isinstance(cell, str) and needle in cell:
                            df.iat[row_idx, col_idx] = cell.replace(needle, replacement, 1)
                            break
                    else:
                        continue
                    break

        return df

    def finalize_toc(self, html):
        if TOC_PLACEHOLDER not in html:
            return html

        entries = getattr(self, 'toc_entries', None) or []
        if not entries:
            return html.replace(TOC_PLACEHOLDER, '', 1)

        toc_normalizer = NormalizeText().normalize_text
        entries = [dict(entry, text=toc_normalizer(entry['text'])) for entry in entries]

        candidates = []
        for block in toc_iter_blocks(html):
            if 'id=' in block['attrs']:
                continue
            norm = toc_normalize(toc_block_text(block['body']))
            if len(norm) < 3:
                continue
            candidates.append({
                'insert_pos': block['insert_pos'],
                'body_start': block['body_start'],
                'body': block['body'],
                'norm': norm,
            })

        anchor_counter = [0]
        block_anchor = {}
        anchor_block = {}
        insertions = []
        used_positions = set()

        def next_anchor():
            anchor_counter[0] += 1
            return f'toc-anchor-{anchor_counter[0]}'

        def inline_position(cand, entry_text):
            words = [w for w in re.split(r'\s+', entry_text.strip()) if w]
            if not words:
                return None
            pattern = r'\s*'.join(re.escape(w) for w in words[:6])
            found = re.search(pattern, cand['body'])
            if not found:
                return None
            return cand['body_start'] + found.start()

        def assign_anchor(idx, entry_text):
            cand = candidates[idx]
            pos = cand['insert_pos']
            if pos not in block_anchor:
                anchor = next_anchor()
                block_anchor[pos] = anchor
                anchor_block[anchor] = cand
                insertions.append((pos, f' id="{anchor}"'))
                used_positions.add(pos)
                return anchor
            inline_pos = inline_position(cand, entry_text)
            if inline_pos is not None and inline_pos not in used_positions:
                anchor = next_anchor()
                insertions.append((inline_pos, f'<span id="{anchor}"></span>'))
                used_positions.add(inline_pos)
                return anchor
            return block_anchor[pos]

        cursor = 0
        entry_anchors = []
        for entry in entries:
            target = toc_normalize(entry['text'])
            match_idx = None

            if len(target) >= 3:
                match_idx = toc_best_containment_match(candidates, target, cursor, len(candidates))
                if match_idx is None:
                    match_idx = toc_best_containment_match(candidates, target, 0, len(candidates))

                if match_idx is None:
                    best_score = TOC_MATCH_THRESHOLD
                    for idx, cand in enumerate(candidates):
                        score = SequenceMatcher(None, target, cand['norm']).ratio()
                        if score > best_score:
                            best_score = score
                            match_idx = idx

            if match_idx is not None:
                entry_anchors.append(assign_anchor(match_idx, entry['text']))
                cursor = match_idx
            else:
                entry_anchors.append(None)

        linked = set()
        for number, anchor in enumerate(entry_anchors, start=1):
            cand = anchor_block.get(anchor)
            if anchor is None or cand is None or anchor in linked:
                continue
            length = toc_link_length(cand['body'])
            if not length:
                continue
            linked.add(anchor)
            insertions.append((cand['body_start'] + length,
                               f' <a class="toc-backlink" href="#toc-entry-{number}">↩</a>'))

        pieces = []
        last = 0
        for pos, snippet in sorted(insertions, key=lambda item: item[0]):
            pieces.append(html[last:pos])
            pieces.append(snippet)
            last = pos
        pieces.append(html[last:])
        html = ''.join(pieces)

        title_text = toc_normalizer(getattr(self, 'toc_title', None) or 'Table of Contents')
        out = [
            '<nav class="toc">',
            f'<p class="toc-title">{html_lib.escape(title_text)}</p>',
            '<table class="toc-table">',
        ]
        for number, (entry, anchor) in enumerate(zip(entries, entry_anchors), start=1):
            level = entry['level']
            indent = f' style="padding-left: {(level - 1) * 1.5}em;"' if level > 1 else ''
            title = html_lib.escape(entry['text'])
            body = f'<a href="#{anchor}">{title}</a>' if anchor else title
            row_id = f' id="toc-entry-{number}"' if anchor in linked else ''
            out.append(
                f'<tr{row_id} class="toc-level-{level}">'
                f'<td class="toc-entry"{indent}>{body}</td></tr>'
            )
        out.append('</table>')
        out.append('</nav>')
        toc_html = '\n'.join(out) + '\n'

        return html.replace(TOC_PLACEHOLDER, toc_html, 1)

    def is_sequential(self, text1, text2):
        try:
            s1, s2 = str(text1).strip(), str(text2).strip()
            if s1.isdigit() and s2.isdigit():
                return int(s2) == int(s1) + 1
            n1, n2 = re.findall(r"\d+", s1), re.findall(r"\d+", s2)
            if n1 and n2:
                return int(n2[0]) == int(n1[0]) + 1
            return False
        except:
            return False

    def row_similarity(self, row1, row2):
        s1, s2 = " ".join(str(x) for x in row1), " ".join(str(x) for x in row2)
        return SequenceMatcher(None, s1, s2).ratio()

    def _has_serial_number(self, cell):
        text = str(cell).strip()
        if not text or text.lower() in ["nan", ""]:
            return False

        # Check against improved serial patterns
        for pattern in self.serial_patterns:
            if re.fullmatch(pattern, text, re.IGNORECASE):
                return True

        # Fallback checks for edge cases
        # Simple digits (catch all)
        if text.isdigit():
            return True

        # Simple Roman numerals
        if re.fullmatch(r"[ivxlcdmIVXLCDM]+", text):
            return True

        return False

    def _is_numeric_or_symbolic(self, cell):
        text = str(cell).strip().lower()
        if not text or text in ["-", "—", "–", "na", "n/a", "nil", "none", "✓", "x"]:
            return True
        if re.fullmatch(indic_nil_alternation(), text):
            return True
        if re.fullmatch(r"\d+(\.\d+)?(\s?(kg|g|mg|cm|mm|m|km|%|hrs?|days?|years?|" + indic_unit_time_alternation() + r"))?", text):
            return True
        return False

    def _looks_like_continuation(self, prev_text, curr_text, curr_row):
        prev_text, curr_text = str(prev_text).strip(), str(curr_text).strip()

        # Guard: numeric/measurement-only rows should not merge
        numeric_like = sum(self._is_numeric_or_symbolic(c) for c in curr_row[1:])
        if numeric_like >= len(curr_row) - 2:  # all except maybe one col
            return False

        # Rule 1: prev doesn't end with punctuation + curr starts lowercase
        if prev_text and prev_text[-1] not in self.table_terminators and curr_text and curr_text[0].islower():
            return True

        # Rule 2: curr row is sparse (only 1 column filled beyond first col)
        non_empty_cols = sum(bool(str(c).strip()) for c in curr_row[1:])
        if non_empty_cols == 1:
            return True

        # Rule 3: curr row very short (few words)
        if len(curr_text.split()) < self.min_word_threshold_tableRows:
            return True

        # Rule 4: Text indentation pattern (continuation often indented)
        if curr_text and (curr_text.startswith('    ') or curr_text.startswith('\t')):
            return True

        # Rule 5: Continuation markers like hyphens or em-dashes
        if curr_text and curr_text[:3] in ['...', '— ', '- ']:
            return True

        return False


    def _is_sparse_row(self, row_list):
        if len(row_list) <= 1:
            return False
            
        empty_count = 0
        for cell in row_list[1:]:  # Skip first column for sparse check
            cell_str = str(cell).strip()
            if not cell_str or cell_str.lower() in ['nan', '']:
                empty_count += 1
        
        empty_ratio = empty_count / (len(row_list) - 1)
        return empty_ratio > 0.7  # More than 70% empty

    def _smart_concatenate(self, prev_text, curr_text):
        if not prev_text:
            return curr_text
        if not curr_text:
            return prev_text
            
        prev_text = prev_text.rstrip()
        curr_text = curr_text.lstrip()
        
        # If previous text ends with sentence terminator, add space
        if prev_text[-1] in self.table_terminators:
            return prev_text + " " + curr_text
        
        # If current text starts with lowercase, likely continuation - just space
        if curr_text[0].islower():
            return prev_text + " " + curr_text
            
        # If current text starts with punctuation (like continuation), join without space
        if curr_text[0] in [',', ';', '-', '—']:
            return prev_text + curr_text
            
        # Default: add space
        return prev_text + " " + curr_text

    def is_table_continuation(self, table2, table2_width, table2_bbox=None, table2_page=None, page_height=None):
        if self.pending_table is None:
            return False

        table1, table1_width = self.pending_table

        if table1.empty or table2.empty:
            return False

        try:
            have_coords = (table2_bbox is not None and table2_page is not None and page_height
                           and self.pending_table_bbox is not None and self.pending_table_page is not None)
            if have_coords:
                return self._is_geometric_continuation(
                    table1, table1_width, table2, table2_width,
                    self.pending_table_bbox, self.pending_table_page,
                    table2_bbox, table2_page, page_height)
            return self._is_legacy_continuation(table1, table1_width, table2, table2_width)
        except Exception as e:
            self.logger.error(f"Error in is_table_continuation: {e}")
            return False

    def _is_geometric_continuation(self, table1, table1_width, table2, table2_width,
                                   bbox1, page1, bbox2, page2, page_height):
        if not self._columns_match(table1, table1_width, table2, table2_width):
            return False

        adjacency = self._table_adjacency(bbox1, page1, bbox2, page2, page_height, len(table1))
        if adjacency is None:
            return False

        if adjacency == 'same_page' and self._looks_like_own_header(table2):
            self.logger.debug("Same-page table starts with its own header, treating as new table")
            return False

        if adjacency == 'cross_page_header' and not self._headers_repeat(table1, table2):
            self.logger.debug("Cross-page table ends mid-page without a repeated header, treating as new table")
            return False

        self.logger.debug(f"Geometric continuation accepted ({adjacency})")
        return True

    def _headers_repeat(self, table1, table2):
        return self._calculate_header_similarity(table1, table2) > 0.9

    def _columns_match(self, table1, table1_width, table2, table2_width):
        if table1.shape[1] != table2.shape[1]:
            self.logger.debug(f"Column counts differ: {table1.shape[1]} vs {table2.shape[1]}")
            return False
        if max(table1_width, table2_width) > 0:
            width_ratio = min(table1_width, table2_width) / max(table1_width, table2_width)
            if width_ratio < TABLE_MERGE_WIDTH_RATIO:
                self.logger.debug(f"Width ratio too low: {width_ratio}")
                return False
        return True

    def _table_adjacency(self, bbox1, page1, bbox2, page2, page_height, table1_rows):
        try:
            page1 = int(page1)
            page2 = int(page2)
        except (TypeError, ValueError):
            return None

        x1_0, y1_0, x1_1, y1_1 = bbox1
        x2_0, y2_0, x2_1, y2_1 = bbox2

        if not self._x_ranges_align(x1_0, x1_1, x2_0, x2_1):
            return None

        if page1 == page2:
            row_height = abs(y1_1 - y1_0) / max(table1_rows, 1)
            gap = y1_0 - y2_1
            if -row_height <= gap <= TABLE_MERGE_ROW_GAP_MULTIPLIER * max(row_height, 1.0):
                return 'same_page'
            return None

        if page2 - page1 == 1 and page_height:
            at_top_of_next_page = y2_1 >= page_height * (1.0 - TABLE_MERGE_TOP_BAND_RATIO)
            if not at_top_of_next_page:
                return None
            at_bottom_of_prev_page = y1_0 <= page_height * TABLE_MERGE_BOTTOM_MARGIN_RATIO
            if at_bottom_of_prev_page:
                return 'cross_page'
            return 'cross_page_header'

        return None

    def _x_ranges_align(self, a0, a1, b0, b1):
        width_a = a1 - a0
        width_b = b1 - b0
        if width_a <= 0 or width_b <= 0:
            return False
        overlap = min(a1, b1) - max(a0, b0)
        if overlap <= 0:
            return False
        return overlap >= TABLE_MERGE_X_OVERLAP_RATIO * min(width_a, width_b)

    def _looks_like_own_header(self, table):
        if table.empty or table.shape[1] == 0:
            return False
        first_row = table.iloc[0]
        cells = [str(cell).strip() for cell in first_row]
        non_empty = [cell for cell in cells if cell and cell.lower() != 'nan']
        if len(non_empty) < table.shape[1]:
            return False
        return not any(self._is_numeric_content(cell) for cell in non_empty)

    def _is_legacy_continuation(self, table1, table1_width, table2, table2_width):
        if max(table1_width, table2_width) > 0:
            width_ratio = min(table1_width, table2_width) / max(table1_width, table2_width)
            if width_ratio < 0.85:
                return False

        col_diff = abs(table1.shape[1] - table2.shape[1])
        if col_diff > 1:
            return False

        header_sim = self._calculate_header_similarity(table1, table2)
        if header_sim > 0.9:
            return True

        if not self._is_new_table_start(table1, table2):
            return True

        last_row_first_col = str(table1.iloc[-1, 0]).strip()
        first_row_first_col = str(table2.iloc[0, 0]).strip()

        if (self._is_numeric_content(last_row_first_col) and
                self._is_numeric_content(first_row_first_col)):
            return True

        last_row_text = self._get_last_row_text(table1)
        if self._ends_with_sentence_terminator(last_row_text):
            return True

        if self._is_very_sparse_table(table2):
            return True

        if first_row_first_col and last_row_first_col:
            if self.is_sequential(first_row_first_col, last_row_first_col):
                return True

        if self._has_similar_structure(table1, table2):
            return True

        return False

    def _calculate_header_similarity(self, table1, table2):
        try:
            if len(table1) == 0 or len(table2) == 0:
                return 0.0
            return self._calculate_row_similarity(table1.iloc[0], table2.iloc[0])
        except Exception as e:
            self.logger.error(f"Error calculating header similarity: {e}")
            return 0.0

    def _normalize_header_cell(self, cell_text):
        if not cell_text or cell_text.lower() in ['nan', '']:
            return ""
        
        # Remove extra spaces, convert to lowercase, remove special formatting
        normalized = ' '.join(cell_text.split())
        normalized = normalized.lower()
        # Remove common table header prefixes/suffixes
        normalized = re.sub(r'^(s\.no|sl\.?no|serial|item|part|' + indic_serial_no_alternation() + r'|'
                            + indic_item_alternation() + r'|' + _term_alternation("part") + r')\s*\.?\s*', '', normalized)
        normalized = re.sub(r'\s*(no|num|number|' + indic_number_word_alternation() + r')\s*\.?\s*$', '', normalized)
        
        return normalized.strip()

    def _is_numeric_content(self, text):
        if not text or text.lower() in ['nan', '']:
            return False
            
        # Remove common formatting
        clean_text = re.sub(r'[^\w]', '', text.lower())
        
        # Check for pure numbers
        if clean_text.isdigit():
            return True
            
        # Check for Roman numerals
        if re.fullmatch(r'[ivxlcdm]+', clean_text):
            return True
            
        # Check for alphanumeric serial patterns
        if re.fullmatch(r'[a-z' + INDIC_WORD_CHARS + r']\d+|[a-z' + INDIC_WORD_CHARS + r']+\d+', clean_text):
            return True
            
        return False

    def _get_last_row_text(self, table):
        if table.empty:
            return ""
            
        last_row = table.iloc[-1]
        text_parts = []
        
        for cell in last_row:
            cell_str = str(cell).strip()
            if cell_str and cell_str.lower() not in ['nan', '']:
                text_parts.append(cell_str)
        
        return ' '.join(text_parts)

    def _ends_with_sentence_terminator(self, text):
        if not text:
            return False
            
        # Remove trailing spaces and check last character
        text = text.rstrip()
        
        # Check for sentence terminators including some legal document patterns
        terminators = ['.', '!', '?', ':', ';']
        
        for char in reversed(text):
            if char in terminators:
                return True
            elif char.isspace():
                continue
            else:
                break
                
        return False

    def _is_very_sparse_table(self, table):
        if table.empty:
            return False
            
        total_cells = table.shape[0] * table.shape[1]
        empty_cells = 0
        
        for _, row in table.iterrows():
            for cell in row:
                cell_str = str(cell).strip()
                if not cell_str or cell_str.lower() in ['nan', '']:
                    empty_cells += 1
        
        empty_ratio = empty_cells / total_cells
        return empty_ratio > 0.6  # More than 60% empty

    def _is_new_table_start(self, table1, table2):
        first_cell = str(table2.iloc[0, 0]).strip()

        if is_schedule(first_cell) or is_annexure(first_cell) or is_appendix(first_cell) \
                or is_part(first_cell):
            return True

        # Common new table patterns
        new_table_patterns = [
            r"^(\(?[1aAiI]\)?[\.\)]?)$",           # (1), 1., 1), a), A., (i)
            r"^[\[\(]?(table|tbl|chart)\s*[\d" + INDIC_DIGIT_CHARS + r"\.]*",  # Table 1, Tbl. 2
            r"^(schedule|annexure|appendix)\s*[A-Z" + INDIC_WORD_CHARS + INDIC_DIGIT_CHARS + r"0-9]*",  # Schedule A, Annexure 1
            r"^part\s+[A-Z" + INDIC_WORD_CHARS + INDIC_DIGIT_CHARS + r"\d]+",                     # Part I, Part 1
            r"^section\s+[\d" + INDIC_DIGIT_CHARS + r"]+",                      # Section 1
        ]

        for pattern in new_table_patterns:
            if re.match(pattern, first_cell, re.IGNORECASE):
                return True

        return False

    def _has_similar_structure(self, table1, table2):
        try:
            # Header similarity
            header_sim = self.row_similarity(table1.iloc[0], table2.iloc[0])
            if header_sim > 0.8:
                return True
            
            # Overall content pattern similarity
            if len(table1) >= 2 and len(table2) >= 2:
                # Compare first data row patterns
                row1_pattern = self._get_row_pattern(table1.iloc[1])
                row2_pattern = self._get_row_pattern(table2.iloc[1])
                pattern_sim = SequenceMatcher(None, row1_pattern, row2_pattern).ratio()
                if pattern_sim > 0.7:
                    return True
            
            return False
        except:
            return False

    def _get_row_pattern(self, row):
        pattern = []
        for cell in row:
            cell_str = str(cell).strip().lower()
            if not cell_str or cell_str in ['nan', '']:
                pattern.append('E')  # Empty
            elif cell_str.replace('.', '').replace(',', '').isdigit():
                pattern.append('N')  # Numeric
            elif self._has_serial_number(cell):
                pattern.append('S')  # Serial
            else:
                pattern.append('T')  # Text
        return ''.join(pattern)


    def merge_tables(self, table2, table2_width, table2_bbox=None, table2_page=None):
        if self.pending_table is None:
            self.pending_table = [table2, table2_width]
            self.pending_table_bbox = table2_bbox
            self.pending_table_page = table2_page
            return

        table1, table1_width = self.pending_table

        if table1.empty or table2.empty:
            return

        try:
            # Step 1: ENHANCED: Check if table2 has duplicate header
            table2_adjusted = self._handle_duplicate_headers(table1, table2)
            
            # Step 2: Intelligent column alignment
            aligned = self._align_columns(table1, table2_adjusted)
            if aligned is None:
                self.logger.warning("Failed to align columns, skipping merge")
                return
            table1, table2_aligned = aligned

            # Step 3: ENHANCED: Check for broken rows between tables (last row of table1 + first row of table2)
            merged_table = self._merge_table_boundaries(table1, table2_aligned)

            # Step 4: Update average width
            avg_width = (table1_width + table2_width) / 2.0
            self.pending_table = [merged_table, avg_width]
            if table2_bbox is not None:
                self.pending_table_bbox = table2_bbox
                self.pending_table_page = table2_page
            self.logger.debug(f"Successfully merged tables. New shape: {merged_table.shape}")

        except Exception as e:
            self.logger.error(f"Error during table merge: {e}")

    def _handle_duplicate_headers(self, table1, table2):
        if table2.empty:
            return table2
            
        # Start with table1 header (first row)
        table1_header = table1.iloc[0] if len(table1) > 0 else None
        
        if table1_header is None:
            return table2
            
        # Compare rows positionally: table1 row 1 with table2 row 1, table1 row 2 with table2 row 2, etc.
        header_rows_to_remove = 0
        max_rows_to_check = min(len(table1), len(table2))
        
        for i in range(max_rows_to_check):
            table1_row = table1.iloc[i]
            table2_row = table2.iloc[i]
            
            # Check if this row matches positionally
            header_similarity = self._calculate_row_similarity(table1_row, table2_row)
            
            if header_similarity > 0.85:  # High similarity indicates duplicate header
                header_rows_to_remove += 1
                self.logger.debug(f"Table1 row {i+1} matches Table2 row {i+1} (similarity: {header_similarity}), marking for removal")
            else:
                # Found first non-matching row, stop checking
                self.logger.debug(f"Table1 row {i+1} doesn't match Table2 row {i+1} (similarity: {header_similarity}), stopping header detection")
                break
        
        # Remove detected header rows from the beginning of table2
        if header_rows_to_remove > 0:
            self.logger.debug(f"Removing {header_rows_to_remove} duplicate header rows from beginning of table2")
            if len(table2) > header_rows_to_remove:
                return table2.iloc[header_rows_to_remove:].reset_index(drop=True)
            else:
                # If table2 only has header rows, return empty DataFrame
                return pd.DataFrame(columns=table1.columns)
        
        return table2

    def _calculate_row_similarity(self, row1, row2):
        try:
            min_length = min(len(row1), len(row2))
            if min_length == 0:
                return 0.0

            normalized1 = [self._normalize_header_cell(str(cell)) for cell in row1[:min_length]]
            normalized2 = [self._normalize_header_cell(str(cell)) for cell in row2[:min_length]]

            compared = [(a, b) for a, b in zip(normalized1, normalized2) if a or b]
            if len(compared) < min(2, min_length):
                return 0.0
            return sum(1 for a, b in compared if a == b) / len(compared)

        except Exception as e:
            self.logger.error(f"Error calculating row similarity: {e}")
            return 0.0

    def _merge_table_boundaries(self, table1, table2):
        if table2.empty:
            return table1
            
        # Get last row of table1 and first row of table2
        if table1.empty:
            return table2
            
        last_row_table1 = table1.iloc[-1]
        first_row_table2 = table2.iloc[0]
        
        # Check if we should merge the boundary rows
        should_merge_boundary = self._should_merge_boundary_rows(last_row_table1, first_row_table2)
        
        if should_merge_boundary:
            # Merge the boundary rows
            merged_boundary = self._merge_boundary_rows(last_row_table1, first_row_table2)
            
            # Create new merged table
            # Remove last row from table1 and first row from table2, then add merged row
            table1_without_last = table1.iloc[:-1]
            table2_without_first = table2.iloc[1:] if len(table2) > 1 else pd.DataFrame(columns=table2.columns)
            
            # Combine: table1 + merged_boundary + table2_without_first
            merged_table = pd.concat([
                table1_without_last,
                pd.DataFrame([merged_boundary], columns=table1.columns),
                table2_without_first
            ], ignore_index=True)
            
            self.logger.debug("Merged boundary rows between tables")
        else:
            # No merging needed, just concatenate
            merged_table = pd.concat([table1, table2], ignore_index=True)
            self.logger.debug("No boundary merge needed, concatenated tables")
        
        return merged_table

    def _should_merge_boundary_rows(self, last_row, first_row):
        try:
            # Check if any cell in last row ends with sentence terminators
            for cell in last_row:
                cell_text = str(cell).strip()
                if self._ends_with_sentence_terminator(cell_text):
                    self.logger.debug(f"Last row cell ends with terminator: '{cell_text[-20:]}'")
                    return False  # Don't merge if sentence is complete
            
            # Check if first column of both rows has numeric content
            last_first_col = str(last_row.iloc[0] if hasattr(last_row, 'iloc') else last_row[0]).strip()
            first_first_col = str(first_row.iloc[0] if hasattr(first_row, 'iloc') else first_row[0]).strip()
            
            if (self._is_numeric_content(last_first_col) and 
                self._is_numeric_content(first_first_col)):
                self.logger.debug(f"Both first columns numeric: '{last_first_col}' and '{first_first_col}'")
                return False  # Don't merge if both have numeric serials
            
            # Check if first row of table2 is sparse (likely continuation)
            first_row_list = list(first_row)
            if self._is_sparse_row(first_row_list):
                self.logger.debug("First row of table2 is sparse, merging")
                return True
            
            # Check continuation patterns
            # Get text content from non-first columns for comparison
            last_content = self._get_content_columns(last_row)
            first_content = self._get_content_columns(first_row)
            
            return self._looks_like_continuation(last_content, first_content, list(first_row))
            
        except Exception as e:
            self.logger.error(f"Error checking boundary rows: {e}")
            return False

    def _get_content_columns(self, row):
        content_parts = []
        # Skip first column (usually serial number) and get text from other columns
        for i, cell in enumerate(row):
            if i == 0:  # Skip first column
                continue
            cell_str = str(cell).strip()
            if cell_str and cell_str.lower() not in ['nan', '']:
                content_parts.append(cell_str)
        return ' '.join(content_parts)

    def _merge_boundary_rows(self, last_row, first_row):
        merged_row = []
        
        for i in range(max(len(last_row), len(first_row))):
            last_cell = str(last_row.iloc[i] if hasattr(last_row, 'iloc') else last_row[i]).strip() if i < len(last_row) else ""
            first_cell = str(first_row.iloc[i] if hasattr(first_row, 'iloc') else first_row[i]).strip() if i < len(first_row) else ""
            
            if first_cell and first_cell.lower() not in ['nan', '']:
                if last_cell:
                    merged_text = self._smart_concatenate(last_cell, first_cell)
                    merged_row.append(merged_text)
                else:
                    merged_row.append(first_cell)
            else:
                merged_row.append(last_cell)
        
        return merged_row

    def _align_columns(self, table1, table2):
        try:
            width1, width2 = table1.shape[1], table2.shape[1]

            if width2 < width1:
                table2_out = table2.copy()
                for i in range(width1 - width2):
                    table2_out[f"_pad{i}"] = ""
                table2_out.columns = list(table1.columns)
                return table1, table2_out

            if width2 > width1:
                extra = table2.iloc[:, width1:]
                if all(self._is_blank_cell(cell) for cell in extra.to_numpy().ravel()):
                    table2_out = table2.iloc[:, :width1].copy()
                    table2_out.columns = list(table1.columns)
                    return table1, table2_out

                table1_out = table1.copy()
                for i in range(width1, width2):
                    label = table2.columns[i]
                    if label in table1_out.columns:
                        label = f"_extra{i}"
                    table1_out[label] = ""
                table2_out = table2.copy()
                table2_out.columns = list(table1_out.columns)
                return table1_out, table2_out

            table2_out = table2.copy()
            table2_out.columns = list(table1.columns)
            return table1, table2_out

        except Exception as e:
            self.logger.error(f"Error aligning columns: {e}")
            return None

    def _is_blank_cell(self, cell):
        text = str(cell).strip()
        return text == '' or text.lower() in ('nan', 'none')

    def build_line_text(self, tb, textline):
        line_parts = []
        pending_superscript = []

        for text_el in textline.findall('.//text'):
            raw = text_el.text or ''
            if not raw:
                continue

            is_super = False
            if 'bbox' in text_el.attrib:
                try:
                    char_bbox = tuple(map(float, text_el.attrib['bbox'].split(',')))
                    if char_bbox in tb.footnotes_superscript:
                        pending_superscript.append(tb.footnotes_superscript[char_bbox])
                        is_super = True
                except Exception:
                    pass

            if not is_super:
                if pending_superscript:
                    marker = ''.join(pending_superscript)
                    line_parts.append('{{^{{FOOTNOTE ' + marker + '}}}}')
                    pending_superscript = []
                line_parts.append(raw)

        if pending_superscript:
            marker = ''.join(pending_superscript)
            line_parts.append('{{^{{FOOTNOTE ' + marker + '}}}}')

        return ''.join(line_parts).replace('\n', ' ').strip()

    def leading_token_is_bold(self, textline):
        total = 0
        bold = 0
        for text_el in textline.findall('.//text'):
            raw = text_el.text or ''
            if not raw:
                continue
            if raw.isspace():
                if total:
                    break
                continue
            total += 1
            if BOLD_FONT_RE.search(text_el.attrib.get('font', '')) or 'b' in text_el.attrib.get('fstyle', ''):
                bold += 1
        if not total:
            return False
        return (bold / total) > 0.5

    def line_cells_from_chars(self, tb, textline):
        chars = []
        pending_superscript = []
        for text_el in textline.findall('.//text'):
            raw = text_el.text or ''
            if not raw:
                continue
            cx0 = cx1 = None
            char_bbox = None
            if 'bbox' in text_el.attrib:
                try:
                    char_bbox = tuple(map(float, text_el.attrib['bbox'].split(',')))
                    cx0, cx1 = char_bbox[0], char_bbox[2]
                except Exception:
                    char_bbox = None
            if char_bbox is not None and char_bbox in tb.footnotes_superscript:
                pending_superscript.append(tb.footnotes_superscript[char_bbox])
                continue
            if pending_superscript:
                marker = '{{^{{FOOTNOTE ' + ''.join(pending_superscript) + '}}}}'
                chars.append((cx0, cx1, marker, False))
                pending_superscript = []
            chars.append((cx0, cx1, raw, raw.isspace()))
        if pending_superscript:
            marker = '{{^{{FOOTNOTE ' + ''.join(pending_superscript) + '}}}}'
            chars.append((None, None, marker, False))

        widths = [c[1] - c[0] for c in chars
                  if not c[3] and c[0] is not None and c[1] is not None and c[1] > c[0]
                  and not COMBINING_MARK_START_RE.match(c[2])]
        char_width = sorted(widths)[len(widths) // 2] if widths else 1.0
        threshold = char_width * 2.0

        cells = []
        current = None
        prev_x1 = None
        saw_space = False
        for cx0, cx1, raw, is_space in chars:
            if is_space:
                saw_space = True
                if current is not None:
                    current['text'] += raw
                continue
            if (current is not None and prev_x1 is not None and cx0 is not None and saw_space
                    and cx0 - prev_x1 > threshold and not COMBINING_MARK_START_RE.match(raw)):
                if current['text'].strip():
                    cells.append(current)
                current = None
            if current is None:
                current = {'x0': cx0, 'x1': cx1, 'text': raw}
            else:
                current['text'] += raw
                if cx1 is not None:
                    current['x1'] = cx1
            if cx1 is not None:
                prev_x1 = cx1
            saw_space = False
        if current is not None and current['text'].strip():
            cells.append(current)
        for cell in cells:
            cell['text'] = cell['text'].strip()
        return cells, char_width

    def extract_textlines(self, tb):
        lines = []
        for textline in tb.tbox.findall('.//textline'):
            bbox = textline.attrib.get('bbox')
            if not bbox:
                continue
            try:
                x0, y0, x1, y1 = map(float, bbox.split(','))
            except ValueError:
                continue

            text = self.build_line_text(tb, textline)
            if not text:
                continue

            cells, char_width = self.line_cells_from_chars(tb, textline)
            for cell in cells:
                cell['y0'] = y0
                cell['y1'] = y1
                if cell['x0'] is None:
                    cell['x0'] = x0
                if cell['x1'] is None:
                    cell['x1'] = x1

            line = {
                'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1, 'text': text,
                'lead_bold': self.leading_token_is_bold(textline),
                'cells': cells, 'char_width': char_width,
            }
            if self.track_text_styles:
                line['styled'] = self.build_styled_line_text(tb, textline, (x0, y0, x1, y1))
            for key in ('bq2', 'bq2_start', 'bq2_inline'):
                if key in textline.attrib:
                    line[key] = textline.attrib[key]
            lines.append(line)
        return lines

    def get_char_style(self, text_el, underlines):
        font = text_el.attrib.get('font', '')
        fstyle = text_el.attrib.get('fstyle', '')
        bold = bool(STYLE_BOLD_FONT_RE.search(font)) or 'b' in fstyle
        italic = bool(STYLE_ITALIC_FONT_RE.search(font)) or 'i' in fstyle
        underlined = 'u' in fstyle
        if underlines and not underlined and 'bbox' in text_el.attrib:
            try:
                cx0, cy0, cx1, cy1 = map(float, text_el.attrib['bbox'].split(','))
            except ValueError:
                cx0 = None
            if cx0 is not None:
                mid = (cx0 + cx1) / 2
                height = cy1 - cy0
                low = cy0 - UNDERLINE_BELOW_TOLERANCE
                high = cy0 + UNDERLINE_ABOVE_RATIO * height
                for ux0, uy, ux1 in underlines:
                    if low <= uy <= high and ux0 - UNDERLINE_X_TOLERANCE <= mid <= ux1 + UNDERLINE_X_TOLERANCE:
                        underlined = True
                        break
        return (bold, italic, underlined)

    def collect_page_underlines(self, page):
        underlines = []
        try:
            for tag in ('rect', 'line', 'curve'):
                for el in page.page_in_xml.findall(f'.//{tag}'):
                    try:
                        bbox = tuple(map(float, el.attrib['bbox'].split(',')))
                    except (KeyError, ValueError):
                        continue
                    x0, y0, x1, y1 = bbox
                    if x1 - x0 <= 0 or x1 - x0 > 0.7 * page.pg_width:
                        continue
                    if not page.is_line_like(bbox) or page.is_table_line(bbox):
                        continue
                    underlines.append((x0, max(y0, y1), x1))
        except Exception as e:
            self.logger.warning("Could not collect underlines on page %s: %s", page.pg_num, e)
            return []
        return underlines

    def get_line_underlines(self, line_bbox):
        if not self.page_underlines:
            return []
        lx0, ly0, lx1, ly1 = line_bbox
        line_width = lx1 - lx0
        height = ly1 - ly0
        low = ly0 - UNDERLINE_BELOW_TOLERANCE
        high = ly0 + UNDERLINE_ABOVE_RATIO * height
        near = []
        for ux0, uy, ux1 in self.page_underlines:
            if not low <= uy <= high:
                continue
            if ux1 < lx0 or ux0 > lx1:
                continue
            if (ux1 - ux0) > line_width * UNDERLINE_MAX_WIDTH_RATIO + UNDERLINE_WIDTH_SLACK:
                continue
            near.append((ux0, uy, ux1))
        return near

    def build_styled_line_text(self, tb, textline, line_bbox):
        underlines = self.get_line_underlines(line_bbox)
        chars = []
        pending_superscript = []

        def add_placeholder():
            marker = '{{^{{FOOTNOTE ' + ''.join(pending_superscript) + '}}}}'
            chars.extend((ch, None) for ch in marker)

        for text_el in textline.findall('.//text'):
            raw = text_el.text or ''
            if not raw:
                continue

            is_super = False
            if 'bbox' in text_el.attrib:
                try:
                    char_bbox = tuple(map(float, text_el.attrib['bbox'].split(',')))
                    if char_bbox in tb.footnotes_superscript:
                        pending_superscript.append(tb.footnotes_superscript[char_bbox])
                        is_super = True
                except Exception:
                    pass

            if is_super:
                continue
            if pending_superscript:
                add_placeholder()
                pending_superscript = []
            style = self.get_char_style(text_el, underlines)
            for ch in raw:
                chars.append((' ' if ch == '\n' else ch, style))

        if pending_superscript:
            add_placeholder()

        start = 0
        end = len(chars)
        while start < end and chars[start][0].isspace():
            start += 1
        while end > start and chars[end - 1][0].isspace():
            end -= 1
        return self.styled_chars_to_html(chars[start:end])

    @staticmethod
    def styled_chars_to_html(chars):
        effective = []
        for idx, (ch, style) in enumerate(chars):
            if not ch.isspace():
                effective.append(style)
                continue
            prev_style = None
            for back in range(idx - 1, -1, -1):
                if not chars[back][0].isspace():
                    prev_style = chars[back][1]
                    break
            next_style = None
            for ahead in range(idx + 1, len(chars)):
                if not chars[ahead][0].isspace():
                    next_style = chars[ahead][1]
                    break
            if prev_style is None or next_style is None:
                effective.append(None)
            else:
                effective.append(tuple(p and n for p, n in zip(prev_style, next_style)))

        parts = []
        stack = []
        for (ch, _), style in zip(chars, effective):
            wanted = {name for name, on in zip(STYLE_TAGS, style) if on} if style else set()
            keep = 0
            while keep < len(stack) and stack[keep] in wanted:
                keep += 1
            for name in reversed(stack[keep:]):
                parts.append(f'</{name}>')
            stack = stack[:keep]
            for name in STYLE_TAGS:
                if name in wanted and name not in stack:
                    parts.append(f'<{name}>')
                    stack.append(name)
            parts.append(ch)
        for name in reversed(stack):
            parts.append(f'</{name}>')
        return ''.join(parts)

    @staticmethod
    def join_styled(left, right):
        close = STYLE_SEAM_CLOSE_RE.search(left)
        opened = STYLE_SEAM_OPEN_RE.match(right)
        if close and opened:
            closes = re.findall(r'</([biu])>', close.group(1))
            opens = re.findall(r'<([biu])>', opened.group(2))
            if closes[::-1] == opens:
                return left[:close.start()] + opened.group(1) + right[opened.end():]
        return left + right

    def cluster_rows_by_position(self, items):
        rows = []
        for item in sorted(items, key=lambda it: (it.get('page_num') or 0, -it['y0'], it['x0'])):
            placed_row = None
            for row in rows:
                ref = row[0]
                if ref.get('page_num') != item.get('page_num'):
                    continue
                row_height = max(ref['y1'] - ref['y0'], item['y1'] - item['y0'], 1.0)
                if abs(item['y0'] - ref['y0']) <= row_height * 0.4:
                    placed_row = row
                    break
            if placed_row is not None:
                placed_row.append(item)
            else:
                rows.append([item])

        rows.sort(key=lambda row: (row[0].get('page_num') or 0, -row[0]['y0']))
        for row in rows:
            row.sort(key=lambda it: it['x0'])
        return rows

    def build_row_text(self, row, base_x0, char_width):
        line = ""
        for item in row:
            text = self.normalize_text(item['text'])
            if not text.strip():
                continue
            target_col = max(0, round((item['x0'] - base_x0) / char_width))
            if target_col > len(line):
                line += " " * (target_col - len(line))
            elif line and not line.endswith(" "):
                line += " "
            line += text
        return line

    def pre_line_cells(self, item):
        cells = item.get('cells')
        if cells:
            page_num = item.get('page_num')
            if page_num is None:
                return cells
            return [dict(cell, page_num=page_num) for cell in cells]
        return [item]

    def render_pre_block(self, lines):
        ordered = []
        current_chunk = []
        for item in lines:
            if 'raw' in item:
                if current_chunk:
                    ordered.append(('text', self._dedupe_overlapping_lines(current_chunk)))
                    current_chunk = []
                ordered.append(('raw', item))
                continue
            current_chunk.append(item)
        if current_chunk:
            ordered.append(('text', self._dedupe_overlapping_lines(current_chunk)))

        all_text_items = [cell for kind, chunk in ordered if kind == 'text'
                          for item in chunk for cell in self.pre_line_cells(item)]
        if not all_text_items:
            return

        base_x0 = min(item['x0'] for item in all_text_items)
        total_width = sum(max(item['x1'] - item['x0'], 0.0) for item in all_text_items)
        total_chars = sum(len(item['text']) for item in all_text_items) or 1
        char_width = total_width / total_chars or 1.0

        body_lines = []
        for kind, chunk in ordered:
            if kind == 'raw':
                raw = chunk['raw']
                if not PRE_KEEP_SPAN_RE.search(raw):
                    raw = re.sub(r'</?span[^>]*>', '', raw)
                if raw.strip():
                    body_lines.append(raw)
                continue
            chunk_cells = [cell for item in chunk for cell in self.pre_line_cells(item)]
            for row in self.cluster_rows_by_position(chunk_cells):
                line = self.build_row_text(row, base_x0, char_width)
                if line.strip():
                    body_lines.append(line)

        if not body_lines:
            return

        self.builder += '<pre>\n' + '\n'.join(body_lines) + '\n</pre>\n'

    def _line_cell_starts(self, tb, textline):
        chars = []
        after_space = False
        for text_el in textline.findall('.//text'):
            raw = text_el.text or ''
            if not raw:
                continue
            if raw.isspace():
                after_space = True
                continue
            bbox = text_el.attrib.get('bbox')
            if not bbox:
                continue
            try:
                cx0, cy0, cx1, cy1 = map(float, bbox.split(','))
            except ValueError:
                continue
            chars.append((cx0, cx1, raw, after_space))
            after_space = False
        if not chars:
            return [], None, []
        chars.sort(key=lambda c: c[0])
        widths = [x1 - x0 for x0, x1, raw, gap in chars
                  if x1 > x0 and not COMBINING_MARK_START_RE.match(raw)]
        char_width = sorted(widths)[len(widths) // 2] if widths else 1.0
        threshold = char_width * PRE_GRID_COLUMN_GAP_FACTOR
        starts = [chars[0][0]]
        ink = [[chars[0][0], chars[0][1]]]
        prev_x1 = chars[0][1]
        for x0, x1, raw, after_gap in chars[1:]:
            if x0 - prev_x1 > threshold and after_gap and not COMBINING_MARK_START_RE.match(raw):
                starts.append(x0)
            if x0 - ink[-1][1] > char_width:
                ink.append([x0, x1])
            else:
                ink[-1][1] = max(ink[-1][1], x1)
            prev_x1 = max(prev_x1, x1)
        return starts, char_width, ink

    def _collect_grid_lines(self, tbs):
        lines = []
        for tb in tbs:
            for textline in tb.tbox.findall('.//textline'):
                bbox = textline.attrib.get('bbox')
                if not bbox:
                    continue
                try:
                    x0, y0, x1, y1 = map(float, bbox.split(','))
                except ValueError:
                    continue
                text = self.build_line_text(tb, textline)
                if not text.strip():
                    continue
                starts, char_width, ink = self._line_cell_starts(tb, textline)
                if not starts:
                    continue
                cells, _ = self.line_cells_from_chars(tb, textline)
                lines.append({'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1,
                              'text': text, 'starts': starts, 'char_width': char_width,
                              'ink': ink, 'cells': cells})
        return self._dedupe_overlapping_lines(lines)

    def _dedupe_overlapping_lines(self, lines):
        kept = []
        for line in lines:
            duplicate = False
            for other in kept:
                if line['text'] != other['text']:
                    continue
                if line.get('page_num') != other.get('page_num'):
                    continue
                y_span = max(line['y1'], other['y1']) - min(line['y0'], other['y0'])
                x_span = max(line['x1'], other['x1']) - min(line['x0'], other['x0'])
                if y_span <= 0 or x_span <= 0:
                    continue
                y_overlap = min(line['y1'], other['y1']) - max(line['y0'], other['y0'])
                x_overlap = min(line['x1'], other['x1']) - max(line['x0'], other['x0'])
                if y_overlap / y_span > PRE_GRID_DUPLICATE_OVERLAP_RATIO \
                        and x_overlap / x_span > PRE_GRID_DUPLICATE_OVERLAP_RATIO:
                    duplicate = True
                    break
            if not duplicate:
                kept.append(line)
        return kept

    def _cluster_values(self, values, tol):
        clusters = []
        for v in sorted(values):
            if clusters and v - clusters[-1][-1] <= tol:
                clusters[-1].append(v)
            else:
                clusters.append([v])
        return clusters

    def pre_grid_span(self, tbs):
        span = self._pre_grid_span_strict(tbs)
        if span is not None:
            return span
        return self._pre_grid_passthrough(tbs)

    def _pre_grid_passthrough(self, tbs):
        spine = self._pre_grid_spine
        if not spine:
            return None
        spine_lo, spine_hi, char_w, tol = spine
        lines = self._collect_grid_lines(tbs)
        if len(lines) < PRE_GRID_MIN_COLUMN_ROWS:
            return None
        band = max(char_w * PRE_GRID_GUTTER_FACTOR, tol)
        aligned = [l for l in lines if abs(l['x0'] - spine_lo) <= band]
        min_aligned = max(PRE_GRID_MIN_COLUMN_ROWS,
                          math.ceil(len(lines) * PRE_GRID_COLUMN_SUPPORT_FRAC))
        if len(aligned) < min_aligned or len(aligned) < len(lines) * PRE_GRID_SPINE_FRAC:
            return None
        tokens = []
        for line in aligned:
            for cell in line.get('cells', []):
                tokens.extend(cell['text'].split())
        if not tokens:
            return None
        datalike = sum(1 for t in tokens if any(ch.isdigit() for ch in t))
        if datalike < len(tokens) * PRE_GRID_MIN_DATA_FRAC:
            return None
        heights = [l['y1'] - l['y0'] for l in lines if l['y1'] > l['y0']]
        med_h = sorted(heights)[len(heights) // 2] if heights else 1.0
        y_bottom = min(l['y0'] for l in lines)
        y_top = max(l['y1'] for l in lines)
        return (y_bottom - med_h, y_top + med_h)

    def _pre_grid_span_strict(self, tbs):
        lines = self._collect_grid_lines(tbs)
        if len(lines) < PRE_GRID_MIN_ROWS:
            return None
        rows = self.cluster_rows_by_position(lines)
        if len(rows) < PRE_GRID_MIN_ROWS:
            return None

        heights = [l['y1'] - l['y0'] for l in lines if l['y1'] > l['y0']]
        med_h = sorted(heights)[len(heights) // 2] if heights else 1.0
        tol = max(med_h * PRE_GRID_ALIGN_FACTOR, 1.0)
        char_widths = [l['char_width'] for l in lines
                       if l.get('char_width') and l['char_width'] > 0]
        char_w = sorted(char_widths)[len(char_widths) // 2] if char_widths else 1.0
        gutter = max(char_w * PRE_GRID_GUTTER_FACTOR, tol)

        row_starts = [sorted({s for line in row for s in line['starts']})
                      for row in rows]
        row_ink = [sorted(iv for line in row for iv in line.get('ink', []))
                   for row in rows]
        pairs = [(s, i) for i, starts in enumerate(row_starts) for s in starts]
        if not pairs:
            return None

        gutter_min = char_w * PRE_GRID_GUTTER_FACTOR
        bin_w = max(char_w * 0.5, 1.0)
        gutter_thresh = len(rows) * (1.0 - PRE_GRID_SPINE_FRAC)

        def has_gutter_between(a, b):
            if b - a < gutter_min:
                return False
            n = int((b - a) / bin_w) + 1
            counts = [0] * n
            for iv in row_ink:
                for x0, x1 in iv:
                    if x1 <= a or x0 >= b:
                        continue
                    i0 = max(0, int((max(x0, a) - a) / bin_w))
                    i1 = min(n - 1, int((min(x1, b) - a) / bin_w))
                    for i in range(i0, i1 + 1):
                        counts[i] += 1
            run = 0
            for c in counts:
                if c <= gutter_thresh:
                    run += 1
                    if run * bin_w >= gutter_min:
                        return True
                else:
                    run = 0
            return False

        min_support = max(PRE_GRID_MIN_COLUMN_ROWS,
                          math.ceil(len(rows) * PRE_GRID_COLUMN_SUPPORT_FRAC))

        columns = []
        for cluster in self._cluster_values([s for s, _ in pairs], tol):
            lo, hi = cluster[0], cluster[-1]
            support = {i for s, i in pairs if lo - tol <= s <= hi + tol}
            if len(support) >= min_support:
                columns.append([lo, hi, len(support)])
        if len(columns) < 2:
            return None

        columns.sort(key=lambda c: c[0])
        merged = [columns[0]]
        for lo, hi, sup in columns[1:]:
            if lo - merged[-1][1] < gutter:
                merged[-1][1] = max(merged[-1][1], hi)
                merged[-1][2] = max(merged[-1][2], sup)
            else:
                merged.append([lo, hi, sup])

        kept = [merged[0]]
        for col in merged[1:]:
            if has_gutter_between(kept[-1][0], col[0]):
                kept.append(col)
            else:
                kept[-1][1] = max(kept[-1][1], col[1])
                kept[-1][2] = max(kept[-1][2], col[2])
        merged = kept
        if len(merged) < PRE_GRID_MIN_TOTAL_COLUMNS:
            return None

        left_x = (merged[0][0] + merged[0][1]) / 2.0
        interior = [k for k, c in enumerate(merged)
                    if (c[0] + c[1]) / 2.0 > left_x + gutter]
        if len(interior) < PRE_GRID_MIN_INTERIOR_COLUMNS:
            return None

        def band_of(s):
            for k, col in enumerate(merged):
                if col[0] - tol <= s <= col[1] + tol:
                    return k
            return None

        spine_k = max(interior, key=lambda k: merged[k][2])
        spine_rows = [i for i, starts in enumerate(row_starts)
                      if any(band_of(s) == spine_k for s in starts)]
        if len(spine_rows) < PRE_GRID_MIN_SPINE_ROWS:
            return None

        best_start = best_end = run_start = prev = spine_rows[0]
        for idx in spine_rows[1:]:
            if idx - prev <= PRE_GRID_MAX_ROW_SKIP + 1:
                prev = idx
                continue
            if prev - run_start > best_end - best_start:
                best_start, best_end = run_start, prev
            run_start = prev = idx
        if prev - run_start > best_end - best_start:
            best_start, best_end = run_start, prev

        block = range(best_start, best_end + 1)
        if len(block) < PRE_GRID_MIN_ROWS:
            return None

        spine_set = set(spine_rows)
        block_spine = sum(1 for i in block if i in spine_set)
        if block_spine < max(PRE_GRID_MIN_SPINE_ROWS,
                             math.ceil(len(block) * PRE_GRID_SPINE_FRAC)):
            return None

        multicol = 0
        for i in block:
            occupied = {band_of(s) for s in row_starts[i]}
            occupied.discard(None)
            if len(occupied) >= 2:
                multicol += 1
        if multicol < PRE_GRID_MIN_MULTICOL_ROWS:
            return None

        if multicol < len(block) * PRE_GRID_INDEX_FRACTION:
            spine_lo, spine_hi = merged[spine_k][0], merged[spine_k][1]
            tokens = []
            for i in block:
                for line in rows[i]:
                    for cell in line.get('cells', []):
                        if spine_lo - tol <= cell['x0'] <= spine_hi + tol:
                            tokens.extend(cell['text'].split())
            if not tokens:
                return None
            datalike = sum(1 for t in tokens if any(ch.isdigit() for ch in t))
            if datalike < len(tokens) * PRE_GRID_MIN_DATA_FRAC:
                return None
        elif not self._pre_grid_block_is_tabular(rows, block, merged, band_of, multicol):
            return None

        grid_rows = rows[best_start:best_end + 1]
        y_bottom = min(line['y0'] for row in grid_rows for line in row)
        y_top = max(line['y1'] for row in grid_rows for line in row)
        self._pre_grid_spine = (merged[spine_k][0], merged[spine_k][1], char_w, tol)
        return (y_bottom - med_h, y_top + med_h)

    def _pre_grid_block_is_tabular(self, rows, block, merged, band_of, multicol):
        cells = [cell for i in block for line in rows[i] for cell in line.get('cells', [])]
        tokens = [t for cell in cells for t in cell['text'].split()]
        if not tokens:
            return True
        datalike = sum(1 for t in tokens if any(ch.isdigit() for ch in t))
        if datalike >= len(tokens) * PRE_GRID_MIN_DATA_FRAC:
            return True
        if multicol < len(block) * PRE_GRID_SPINE_FRAC and len(merged) <= PRE_GRID_RAGGED_MAX_COLUMNS:
            return False
        left = min(cell['x0'] for cell in cells)
        total = max(cell['x1'] for cell in cells) - left
        if total <= 0:
            return True
        wide = 0
        for k, col in enumerate(merged):
            right = max((cell['x1'] for cell in cells if band_of(cell['x0']) == k), default=col[0])
            if right - col[0] >= total * PRE_GRID_WIDE_COLUMN_FRAC:
                wide += 1
        return wide < 2

    PRE_GRID_RECLAIMABLE_LABELS = {"header", "footer"}

    def apply_pre_grid_labels(self, all_items, is_multicolumn=False):
        if is_multicolumn:
            return all_items
        result = list(all_items)
        total = len(result)

        def is_open(item):
            tb, label = item
            if label is None:
                return True
            return label in self.PRE_GRID_RECLAIMABLE_LABELS and not getattr(tb, 'recurring_furniture', False)

        i = 0
        while i < total:
            if not is_open(result[i]):
                i += 1
                continue
            j = i
            while j < total and is_open(result[j]):
                j += 1
            span = self.pre_grid_span([result[k][0] for k in range(i, j)])
            if span is not None:
                y_bottom, y_top = span
                for k in range(i, j):
                    tb = result[k][0]
                    cy = (tb.coords[1] + tb.coords[3]) / 2.0
                    if y_bottom <= cy <= y_top:
                        result[k] = (tb, "pre")
            i = j
        return result