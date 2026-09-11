import pandas as pd
import re
import numpy as np
import html as html_lib
from difflib import SequenceMatcher
import logging

TABLE_FOOTNOTE_MARKER_RE = re.compile(r'\{\{\^\{\{FOOTNOTE\s+(\d+)\}\}\}\}')
BOLD_FONT_RE = re.compile(r'bold', re.IGNORECASE)


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
    r'^[\s"“”\'.\-–—]*(?:\(?[a-z0-9]{1,4}\)?[.\):])+\s*',
    re.IGNORECASE
)
TOC_MATCH_THRESHOLD = 0.6
TOC_MIN_CONTAINED_TARGET_LEN = 8


def toc_block_text(fragment):
    text = TOC_TAG_STRIP_RE.sub(' ', fragment)
    text = html_lib.unescape(text)
    return re.sub(r'\s+', ' ', text).strip()


def toc_normalize(text):
    text = text.lower()
    text = TOC_LEADING_ENUM_RE.sub('', text)
    text = re.sub(r'[^a-z0-9]+', ' ', text)
    return text.strip()


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


class TableBuilder:
    def __init__(self):
        self.pending_table = None
        self.min_word_threshold_tableRows = 2
        self.table_terminators = {".", "!", "?"}
        self.logger = logging.getLogger(__name__)
        
        self.serial_patterns = [
            r"^(\(?[1-9]\d*[\)\)]?[\.\)]?)$",  # Numbers with brackets/dots: (1), 1., 1)
            r"^([a-zA-Z][\)\.]?)$",           # Single letters with brackets/dots: a), A.
            r"^([ivxlcdmIVXLCDM]+[\)\.]?)$",   # Roman numerals with brackets/dots: i), ii.
            r"^(\(?[1-9]\d*\)[\.\:]?)",       # (1). or (1):
            r"^([a-zA-Z]\d+(\.\d+)?)$",       # Alphanumeric: a1, a2.1
            r"^(\d+(\.\d+)?[a-zA-Z])$",       # Numeric-letter: 1a, 2.1b
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

        pieces = []
        last = 0
        for pos, snippet in sorted(insertions, key=lambda item: item[0]):
            pieces.append(html[last:pos])
            pieces.append(snippet)
            last = pos
        pieces.append(html[last:])
        html = ''.join(pieces)

        title_text = getattr(self, 'toc_title', None) or 'Table of Contents'
        out = [
            '<nav class="toc">',
            f'<p class="toc-title">{html_lib.escape(title_text)}</p>',
            '<table class="toc-table">',
        ]
        for entry, anchor in zip(entries, entry_anchors):
            level = entry['level']
            indent = f' style="padding-left: {(level - 1) * 1.5}em;"' if level > 1 else ''
            title = html_lib.escape(entry['text'])
            body = f'<a href="#{anchor}">{title}</a>' if anchor else title
            out.append(
                f'<tr class="toc-level-{level}">'
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
        if re.fullmatch(r"\d+(\.\d+)?(\s?(kg|g|mg|cm|mm|m|km|%|hrs?|days?|years?))?", text):
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

    def is_table_continuation(self, table2, table2_width):
        if self.pending_table is None:
            return False
            
        table1, table1_width = self.pending_table
        
        if table1.empty or table2.empty:
            return False

        try:
            # 1. Width similarity check (more flexible threshold)
            if max(table1_width, table2_width) > 0:
                width_ratio = min(table1_width, table2_width) / max(table1_width, table2_width)
                if width_ratio < 0.85:  # More flexible than 0.95
                    self.logger.debug(f"Width ratio too low: {width_ratio}")
                    return False

            # 2. Column count check with flexibility
            col_diff = abs(table1.shape[1] - table2.shape[1])
            if col_diff > 1:  # Allow 1 column difference
                self.logger.debug(f"Column count difference too high: {col_diff}")
                return False

            # 3. NEW: Header similarity check - if headers are identical, remove duplicate
            header_sim = self._calculate_header_similarity(table1, table2)
            if header_sim > 0.9:
                self.logger.debug("Identical headers detected, treating as continuation")
                # Remove the header row from table2 before merging
                if len(table2) > 1:
                    table2 = table2.iloc[1:].reset_index(drop=True)
                return True

            # 4. Check for obvious new table patterns
            if not self._is_new_table_start(table1, table2):
                return True

            # 5. ENHANCED: Last row first column check for numeric vs non-numeric
            last_row_first_col = str(table1.iloc[-1, 0]).strip()
            first_row_first_col = str(table2.iloc[0, 0]).strip()
            
            # If last row's first cell is numeric AND current row's first cell is also numeric
            # They should be separate rows (continuation)
            if (self._is_numeric_content(last_row_first_col) and 
                self._is_numeric_content(first_row_first_col)):
                self.logger.debug(f"Both numeric first columns: {last_row_first_col} and {first_row_first_col}")
                return True

            # 6. NEW: Check if last row of table1 ends with sentence terminators
            last_row_text = self._get_last_row_text(table1)
            if self._ends_with_sentence_terminator(last_row_text):
                self.logger.debug("Last row ends with sentence terminator, treating as continuation")
                return True

            # 7. NEW: Sparse table with mostly empty cells - treat as continuation
            if self._is_very_sparse_table(table2):
                self.logger.debug("Very sparse table detected, likely continuation")
                return True

            # 8. Sequential numbering check (for non-numeric cases)
            if first_row_first_col and last_row_first_col:
                if self.is_sequential(first_row_first_col, last_row_first_col):
                    self.logger.debug(f"Sequential numbering: {last_row_first_col} → {first_row_first_col}")
                    return True

            # 9. Content similarity check
            if self._has_similar_structure(table1, table2):
                self.logger.debug("Similar structure detected")
                return True

            # Fallback: treat as new table
            self.logger.debug("Defaulting to new table")
            return False

        except Exception as e:
            self.logger.error(f"Error in is_table_continuation: {e}")
            return False

    def _calculate_header_similarity(self, table1, table2):
        try:
            if len(table1) == 0 or len(table2) == 0:
                return 0.0
                
            header1 = table1.iloc[0]
            header2 = table2.iloc[0]
            
            # Normalize headers for comparison
            normalized1 = [self._normalize_header_cell(str(cell)) for cell in header1]
            normalized2 = [self._normalize_header_cell(str(cell)) for cell in header2]
            
            # Calculate similarity ratio
            similarity = SequenceMatcher(None, normalized1, normalized2).ratio()
            return similarity
            
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
        normalized = re.sub(r'^(s\.no|sl\.?no|serial|item|part)\s*\.?\s*', '', normalized)
        normalized = re.sub(r'\s*(no|num|number)\s*\.?\s*$', '', normalized)
        
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
        if re.fullmatch(r'[a-z]\d+|[a-z]+\d+', clean_text):
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
        
        # Common new table patterns
        new_table_patterns = [
            r"^(\(?[1aAiI]\)?[\.\)]?)$",           # (1), 1., 1), a), A., (i)
            r"^[\[\(]?(table|tbl|chart)\s*[\d\.]*",  # Table 1, Tbl. 2
            r"^(schedule|annexure|appendix)\s*[A-Z0-9]*",  # Schedule A, Annexure 1
            r"^part\s+[A-Z\d]+",                     # Part I, Part 1
            r"^section\s+\d+",                      # Section 1
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


    def merge_tables(self, table2, table2_width):
        if self.pending_table is None:
            self.pending_table = [table2, table2_width]
            return
            
        table1, table1_width = self.pending_table
        
        if table1.empty or table2.empty:
            return

        try:
            # Step 1: ENHANCED: Check if table2 has duplicate header
            table2_adjusted = self._handle_duplicate_headers(table1, table2)
            
            # Step 2: Intelligent column alignment
            table2_aligned = self._align_columns(table1, table2_adjusted)
            if table2_aligned is None:
                self.logger.warning("Failed to align columns, skipping merge")
                return

            # Step 3: ENHANCED: Check for broken rows between tables (last row of table1 + first row of table2)
            merged_table = self._merge_table_boundaries(table1, table2_aligned)

            # Step 4: Update average width
            avg_width = (table1_width + table2_width) / 2.0
            self.pending_table = [merged_table, avg_width]
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
            # Handle different length rows
            min_length = min(len(row1), len(row2))
            if min_length == 0:
                return 0.0
            
            # Normalize cells for comparison
            normalized1 = [self._normalize_header_cell(str(cell)) for cell in row1[:min_length]]
            normalized2 = [self._normalize_header_cell(str(cell)) for cell in row2[:min_length]]
            
            # Calculate similarity ratio
            similarity = SequenceMatcher(None, normalized1, normalized2).ratio()
            return similarity
            
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
            col_diff = table2.shape[1] - table1.shape[1]
            
            if col_diff == 0:
                return table2
            elif col_diff < 0:
                # Add padding columns to table2
                table2_copy = table2.copy()
                for i in range(abs(col_diff)):
                    table2_copy[f"_pad{i}"] = ""
                return table2_copy
            else:
                # Table2 has more columns - try to intelligently truncate or merge
                if col_diff == 1:
                    # Might be an extra column that can be merged with the last one
                    table2_copy = table2.copy()
                    # Merge last two columns if the last one looks like continuation
                    if table2_copy.shape[1] >= 2:
                        last_col = table2_copy.iloc[:, -1]
                        second_last_col = table2_copy.iloc[:, -2]
                        
                        # If last column is mostly empty, merge with second last
                        if last_col.isnull().sum() > len(last_col) * 0.7:
                            table2_copy.iloc[:, -2] = (table2_copy.iloc[:, -2].astype(str) + 
                                                      " " + table2_copy.iloc[:, -1].astype(str))
                            table2_copy = table2_copy.iloc[:, :-1]
                        else:
                            # Truncate to match table1
                            table2_copy = table2_copy.iloc[:, :table1.shape[1]]
                    
                    return table2_copy
                else:
                    # Too many columns difference, truncate to match table1
                    return table2.iloc[:, :table1.shape[1]].copy()

        except Exception as e:
            self.logger.error(f"Error aligning columns: {e}")
            return None

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
            if BOLD_FONT_RE.search(text_el.attrib.get('font', '')):
                bold += 1
        if not total:
            return False
        return (bold / total) > 0.5

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

            lines.append({
                'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1, 'text': text,
                'lead_bold': self.leading_token_is_bold(textline),
            })
        return lines

    def cluster_rows_by_position(self, items):
        rows = []
        for item in sorted(items, key=lambda it: (-it['y0'], it['x0'])):
            placed_row = None
            for row in rows:
                ref = row[0]
                row_height = max(ref['y1'] - ref['y0'], item['y1'] - item['y0'], 1.0)
                if abs(item['y0'] - ref['y0']) <= row_height * 0.4:
                    placed_row = row
                    break
            if placed_row is not None:
                placed_row.append(item)
            else:
                rows.append([item])

        rows.sort(key=lambda row: -row[0]['y0'])
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

    def render_pre_block(self, lines):
        ordered = []
        current_chunk = []
        for item in lines:
            if 'raw' in item:
                if current_chunk:
                    ordered.append(('text', current_chunk))
                    current_chunk = []
                ordered.append(('raw', item))
                continue
            current_chunk.append(item)
        if current_chunk:
            ordered.append(('text', current_chunk))

        all_text_items = [item for kind, chunk in ordered if kind == 'text' for item in chunk]
        if not all_text_items:
            return

        base_x0 = min(item['x0'] for item in all_text_items)
        total_width = sum(item['x1'] - item['x0'] for item in all_text_items)
        total_chars = sum(len(item['text']) for item in all_text_items)
        char_width = total_width / total_chars

        body_lines = []
        for kind, chunk in ordered:
            if kind == 'raw':
                body_lines.append(chunk['raw'])
                continue
            for row in self.cluster_rows_by_position(chunk):
                line = self.build_row_text(row, base_x0, char_width)
                if line.strip():
                    body_lines.append(line)

        if not body_lines:
            return

        self.builder += '<pre>\n' + '\n'.join(body_lines) + '\n</pre>\n'