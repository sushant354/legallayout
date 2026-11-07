import re
import logging
from statistics import median

import pandas as pd

from .SentenceEndDetector import INDIC_SENTENCE_END_CHARS, INDIC_SEMICOLON_CHARS
from .Table import COMBINING_MARK_START_RE

CELL_EDGE_TOLERANCE = 0.5
LINE_CLUSTER_RATIO = 0.5
WORD_GAP_RATIO = 0.25
ALIGN_TOLERANCE_CHARS = 2.0
NARROW_LINE_RATIO = 0.5
PARA_GAP_RATIO = 1.4
BLANK_LINE_RATIO = 1.7
MAX_BLANK_LINES = 2
MIN_FULL_LINES = 2
MIN_EDGE_SUPPORT = 2
MIN_PRE_BODY_CHARS = 20
VERTICAL_TEXT_RATIO = 0.5
TEXT_SHORTFALL_RATIO = 0.9
HEADER_SIZE_RATIO = 0.15
HEADER_BOLD_RATIO = 0.8
HEADER_MAX_CELL_CHARS = 200
OVERPRINT_BUCKET = 4.0
OVERPRINT_OVERLAP = 0.7
OVERPRINT_MARK_TOLERANCE = 0.15
OVERPRINT_LINE_RATIO = 0.5

SENTENCE_END_RE = re.compile(
    r'[.?!:;' + INDIC_SENTENCE_END_CHARS + INDIC_SEMICOLON_CHARS + r'][\)\]"\'”’»]*$')
LINE_MARKER_RE = re.compile(
    r'^(?:\d{1,3}(?:\.\d{1,3}){0,3}[.)]|\(\d{1,3}\)|\([^\W\d_]{1,5}\)|[ivxlcdm]{1,5}[.)]|[•●▪◦])\s+\S')
NUMBER_MARKER_RE = re.compile(r'^(\d{1,3})[.)]\s+\S')
TAG_RE = re.compile(r'<[^>]+>')
WORD_CHAR_RE = re.compile(r'\w')
PARA_OPEN = '<div>'
PARA_CLOSE = '</div>'
BLOCK_RE = re.compile(r'<div>.*?</div>|<pre>.*?</pre>', re.DOTALL)
HEADER_ROW_RE = re.compile(r'<tbody>\s*(<tr>.*?</tr>)\s*', re.DOTALL)


class TableCellArranger:

    def arrange_table_cells(self, page, table_id, table_obj):
        logger = logging.getLogger(__name__)
        try:
            grid = getattr(page.tabular_datas, 'cell_grids', {}).get(table_id)
            if not grid or table_obj.shape != (len(grid), len(grid[0])):
                return table_obj
            cell_chars = self.collect_cell_chars(page, table_id, grid)
            if not cell_chars:
                return table_obj

            results = {}
            for (row, col), segments in cell_chars.items():
                if grid[row][col][4]:
                    continue
                result = self.arrange_cell(segments)
                if result is not None:
                    results[(row, col)] = result

            word_chars = lambda text: len(WORD_CHAR_RE.findall(text))
            arranged_total = sum(word_chars(r[1]) for r in results.values())
            original_total = sum(word_chars(str(table_obj.iat[row, col])) for row, col in results)
            table_intact = arranged_total >= TEXT_SHORTFALL_RATIO * original_total

            arranged = table_obj.copy()
            stats = {}
            for (row, col), (html, plain, bold_ratio, height) in results.items():
                if not table_intact and \
                        word_chars(plain) < TEXT_SHORTFALL_RATIO * word_chars(str(table_obj.iat[row, col])):
                    continue
                arranged.iat[row, col] = html
                stats[(row, col)] = (bold_ratio, height, len(plain))

            self.register_header_row(arranged, stats)
            return arranged
        except Exception as e:
            logger.warning("Could not arrange the cells of table %s on page %s: %s", table_id, page.pg_num, e)
            return table_obj

    def collect_cell_chars(self, page, table_id, grid):
        columns = []
        rows = []
        for row_idx, row in enumerate(grid):
            rows.append((row_idx, min(c[1] for c in row), max(c[3] for c in row)))
        for col_idx, cell in enumerate(grid[0]):
            columns.append((col_idx, cell[0], cell[2]))

        segments = {}
        seen = {}
        for tb, label in page.all_tbs.items():
            if label != ("table", table_id):
                continue
            if tb.footnotes_superscript:
                return None
            for line_idx, textline in enumerate(tb.tbox.findall('.//textline')):
                try:
                    line_bbox = tuple(map(float, textline.attrib['bbox'].split(',')))
                except (KeyError, ValueError):
                    continue
                underlines = self.get_line_underlines(line_bbox)
                for text_el in textline.findall('.//text'):
                    raw = text_el.text
                    if not raw or 'bbox' not in text_el.attrib:
                        continue
                    try:
                        x0, y0, x1, y1 = map(float, text_el.attrib['bbox'].split(','))
                    except ValueError:
                        continue
                    cx = (x0 + x1) / 2
                    cy = (y0 + y1) / 2
                    row_idx = next((r for r, lo, hi in rows
                                    if lo - CELL_EDGE_TOLERANCE <= cy <= hi + CELL_EDGE_TOLERANCE), None)
                    col_idx = next((c for c, lo, hi in columns
                                    if lo - CELL_EDGE_TOLERANCE <= cx <= hi + CELL_EDGE_TOLERANCE), None)
                    if row_idx is None or col_idx is None:
                        continue
                    key = (row_idx, col_idx, id(tb), line_idx)
                    segments.setdefault(key, []).append({
                        'ch': raw.replace('\n', ' '), 'x0': x0, 'x1': x1, 'y0': y0, 'y1': y1, 'cy': cy,
                        'space': raw.isspace(), 'style': self.get_char_style(text_el, underlines),
                        'twin': self.is_overprinted(seen, (row_idx, col_idx), raw, x0, y0, x1, y1),
                    })

        for key, chars in segments.items():
            visible = [c for c in chars if not c['space']]
            if visible and sum(1 for c in visible if c['twin']) >= OVERPRINT_LINE_RATIO * len(visible):
                segments[key] = [c for c in chars if not c['twin']]

        cells = {}
        for (row_idx, col_idx, _, _), chars in segments.items():
            visible = [c for c in chars if not c['space']]
            if not visible:
                continue
            cells.setdefault((row_idx, col_idx), []).append({
                'chars': chars,
                'x0': min(c['x0'] for c in visible),
                'x1': max(c['x1'] for c in visible),
                'cy': median(c['cy'] for c in visible),
                'height': median(c['y1'] - c['y0'] for c in visible),
            })
        return cells

    def is_overprinted(self, seen, cell, ch, x0, y0, x1, y1):
        if ch.isspace():
            return False
        width = x1 - x0
        height = y1 - y0
        bucket_x = int(((x0 + x1) / 2) // OVERPRINT_BUCKET)
        bucket_y = int(((y0 + y1) / 2) // OVERPRINT_BUCKET)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for other, ox0, oy0, ox1, oy1 in seen.get((cell, bucket_x + dx, bucket_y + dy), ()):
                    if other != ch:
                        continue
                    if width <= 0 or ox1 - ox0 <= 0:
                        tolerance = OVERPRINT_MARK_TOLERANCE * max(height, oy1 - oy0)
                        if abs(x0 - ox0) <= tolerance and abs((y0 + y1) / 2 - (oy0 + oy1) / 2) <= tolerance:
                            return True
                        continue
                    overlap_w = min(x1, ox1) - max(x0, ox0)
                    overlap_h = min(y1, oy1) - max(y0, oy0)
                    if overlap_w > 0 and overlap_h > 0 and \
                            overlap_w * overlap_h >= OVERPRINT_OVERLAP * min(width * height, (ox1 - ox0) * (oy1 - oy0)):
                        return True
        seen.setdefault((cell, bucket_x, bucket_y), []).append((ch, x0, y0, x1, y1))
        return False

    def arrange_cell(self, segments):
        visible = [c for seg in segments for c in seg['chars'] if not c['space']]
        if not visible:
            return None
        widths = [c['x1'] - c['x0'] for c in visible
                  if c['x1'] > c['x0'] and not COMBINING_MARK_START_RE.match(c['ch'])]
        char_w = median(widths) if widths else 1.0
        line_h = median(seg['height'] for seg in segments)

        lines = self.group_segments_into_lines(segments, line_h, char_w)
        if len(lines) > 1 and len(lines) > VERTICAL_TEXT_RATIO * len(visible):
            return None

        html = self.layout_lines(lines, char_w)
        plain = TAG_RE.sub('', html.replace('&#10;', ' '))
        bold = sum(1 for c in visible if c['style'][0]) / len(visible)
        return html, plain, bold, median(c['y1'] - c['y0'] for c in visible)

    def group_segments_into_lines(self, segments, line_h, char_w):
        ordered = sorted(segments, key=lambda s: -s['cy'])
        groups = []
        for seg in ordered:
            if groups and abs(groups[-1]['cy'] - seg['cy']) <= LINE_CLUSTER_RATIO * line_h:
                groups[-1]['segments'].append(seg)
                groups[-1]['cy'] = sum(s['cy'] for s in groups[-1]['segments']) / len(groups[-1]['segments'])
            else:
                groups.append({'cy': seg['cy'], 'segments': [seg]})

        lines = []
        for group in groups:
            seq = []
            previous = None
            for seg in sorted(group['segments'], key=lambda s: s['x0']):
                if previous is not None and seg['x0'] - previous['x1'] > WORD_GAP_RATIO * char_w \
                        and seq and not seq[-1][0].isspace():
                    seq.append((' ', seq[-1][1]))
                for c in seg['chars']:
                    for ch in c['ch']:
                        if ch.isspace():
                            if seq and not seq[-1][0].isspace():
                                seq.append((' ', c['style']))
                        else:
                            seq.append((ch, c['style']))
                previous = seg
            while seq and seq[-1][0].isspace():
                seq.pop()
            if not seq:
                continue
            lines.append({
                'x0': min(s['x0'] for s in group['segments']),
                'x1': max(s['x1'] for s in group['segments']),
                'cy': group['cy'],
                'html': self.styled_chars_to_html(seq),
                'plain': ''.join(ch for ch, _ in seq),
            })
        return lines

    def dominant_edge(self, values, tol, prefer_high):
        ordered = sorted(values)
        clusters = [[ordered[0]]]
        for value in ordered[1:]:
            if value - clusters[-1][0] <= tol:
                clusters[-1].append(value)
            else:
                clusters.append([value])
        best = max(clusters, key=lambda c: (len(c), max(c) if prefer_high else -min(c)))
        if len(best) < MIN_EDGE_SUPPORT:
            return None
        return max(best) if prefer_high else min(best)

    def is_single_axis_block(self, lines, tol):
        if len(lines) < 2:
            return False
        centers = [(l['x0'] + l['x1']) / 2 for l in lines]
        lefts = [l['x0'] for l in lines]
        rights = [l['x1'] for l in lines]
        if max(lefts) - min(lefts) > tol and max(centers) - min(centers) <= tol:
            return True
        return max(lefts) - min(lefts) > tol and max(rights) - min(rights) <= tol

    def layout_lines(self, lines, char_w):
        if len(lines) == 1:
            return lines[0]['html']

        tol = ALIGN_TOLERANCE_CHARS * char_w
        if self.is_single_axis_block(lines, tol):
            text = lines[0]['html']
            for l in lines[1:]:
                text = self.join_styled(text, ' ' + l['html'])
            return text
        left = self.dominant_edge([l['x0'] for l in lines], tol, False)
        right = self.dominant_edge([l['x1'] for l in lines], tol, True)
        gaps = [lines[i - 1]['cy'] - lines[i]['cy'] for i in range(1, len(lines))]
        pitch = median(gaps) if gaps else 0.0

        justified = False
        kinds = ['flow'] * len(lines)
        if left is not None and right is not None and right > left:
            mid = (left + right) / 2
            body = right - left
            for idx, l in enumerate(lines):
                at_left = l['x0'] <= left + tol
                reaches = l['x1'] >= right - tol
                width = l['x1'] - l['x0']
                if at_left and reaches:
                    kinds[idx] = 'full'
                elif at_left:
                    kinds[idx] = 'short'
                elif reaches:
                    kinds[idx] = 'right' if width < NARROW_LINE_RATIO * body else 'indent'
                elif abs((l['x0'] + l['x1']) / 2 - mid) <= tol and l['x0'] - left > tol and right - l['x1'] > tol:
                    kinds[idx] = 'center'
                else:
                    kinds[idx] = 'short'
            justified = sum(1 for k in kinds if k == 'full') >= MIN_FULL_LINES
        if not justified:
            kinds = ['flow'] * len(lines)

        members = [False] * len(lines)
        if justified and right - left >= MIN_PRE_BODY_CHARS * char_w:
            for idx, l in enumerate(lines):
                kind = kinds[idx]
                if kind in ('center', 'right'):
                    members[idx] = True
                elif kind == 'short' and not LINE_MARKER_RE.match(l['plain']):
                    gap_before = gaps[idx - 1] if idx else 0.0
                    members[idx] = idx == 0 or members[idx - 1] or (pitch and gap_before > PARA_GAP_RATIO * pitch)
            idx = 0
            while idx < len(lines):
                if not members[idx]:
                    idx += 1
                    continue
                end = idx
                while end < len(lines) and members[end]:
                    end += 1
                if not any(kinds[k] in ('center', 'right') for k in range(idx, end)):
                    for k in range(idx, end):
                        members[k] = False
                idx = end

        blocks = []
        paragraph = None
        last_number = None
        for idx, l in enumerate(lines):
            if members[idx]:
                if blocks and blocks[-1][0] == 'pre':
                    blocks[-1][1].append(idx)
                else:
                    blocks.append(('pre', [idx]))
                paragraph = None
                continue
            gap_before = gaps[idx - 1] if idx else 0.0
            starts = paragraph is None or bool(LINE_MARKER_RE.match(l['plain']))
            number = NUMBER_MARKER_RE.match(l['plain'])
            if starts and number and paragraph is not None and justified and kinds[idx - 1] == 'full' \
                    and not SENTENCE_END_RE.search(lines[idx - 1]['plain'].rstrip()) \
                    and int(number.group(1)) != (last_number + 1 if last_number is not None else 1):
                starts = False
            if starts and number:
                last_number = int(number.group(1))
            if not starts and pitch and gap_before > PARA_GAP_RATIO * pitch:
                starts = True
            if not starts and justified:
                starts = kinds[idx - 1] == 'short'
            if starts:
                paragraph = ('para', [idx])
                blocks.append(paragraph)
            else:
                paragraph[1].append(idx)

        rendered = []
        for kind, indexes in blocks:
            if kind == 'pre':
                rendered.append(self.render_pre(lines, indexes, left if left is not None else lines[indexes[0]]['x0'],
                                                pitch))
            else:
                text = lines[indexes[0]]['html']
                for idx in indexes[1:]:
                    text = self.join_styled(text, ' ' + lines[idx]['html'])
                rendered.append(('para', text))

        if len(rendered) == 1 and rendered[0][0] == 'para':
            return rendered[0][1]
        return ''.join(PARA_OPEN + text + PARA_CLOSE if kind == 'para' else text for kind, text in rendered)

    def render_pre(self, lines, indexes, left, pitch):
        chosen = [lines[i] for i in indexes]
        total_width = sum(l['x1'] - l['x0'] for l in chosen)
        total_chars = sum(len(l['plain']) for l in chosen) or 1
        unit = total_width / total_chars or 1.0
        out = []
        for pos, idx in enumerate(indexes):
            l = lines[idx]
            if pos and pitch:
                distance = lines[indexes[pos - 1]]['cy'] - l['cy']
                if distance > BLANK_LINE_RATIO * pitch:
                    out.extend([''] * min(int(round(distance / pitch)) - 1, MAX_BLANK_LINES))
            pad = max(int(round((l['x0'] - left) / unit)), 0)
            out.append(' ' * pad + l['html'])
        return ('pre', '<pre>' + '\n'.join(out) + '</pre>')

    def register_header_row(self, arranged, stats):
        rows, cols = arranged.shape
        if rows < 2 or cols < 2:
            return
        header = [(c, stats[(0, c)]) for c in range(cols) if (0, c) in stats]
        body = [stats[k] for k in stats if k[0] > 0]
        if len(header) < 2 or not body:
            return
        texts = [str(arranged.iat[0, c]) for c, _ in header]
        plain = [TAG_RE.sub('', t).strip() for t in texts]
        if any(len(t) > HEADER_MAX_CELL_CHARS for t in plain):
            return
        if all(t.replace('.', '').replace(',', '').isdigit() for t in plain):
            return
        all_bold = all(s[0] >= HEADER_BOLD_RATIO for _, s in header)
        header_h = median(s[1] for _, s in header)
        body_h = median(s[1] for s in body)
        different = body_h > 0 and abs(header_h - body_h) / body_h >= HEADER_SIZE_RATIO
        body_bold = median(s[0] for s in body) >= HEADER_BOLD_RATIO
        if (all_bold and not body_bold) or different:
            self.__dict__.setdefault('table_header_rows', set()).add(tuple(str(v) for v in arranged.iloc[0]))

    def has_header_row(self, table):
        known = self.__dict__.get('table_header_rows')
        if not known or table.shape[0] < 2:
            return False
        return tuple(str(v) for v in table.iloc[0]) in known

    def promote_header_row(self, table_html):
        match = HEADER_ROW_RE.search(table_html)
        if not match:
            return table_html
        row = match.group(1).replace('<td>', '<th>').replace('</td>', '</th>')
        return table_html[:match.start()] + '<thead>\n' + row + '\n</thead>\n<tbody>\n' + table_html[match.end():]

    def _table_adjacency(self, bbox1, page1, bbox2, page2, page_height, table1_rows):
        adjacency = super()._table_adjacency(bbox1, page1, bbox2, page2, page_height, table1_rows)
        self.__dict__['table_cut_by_page'] = adjacency == 'cross_page'
        return adjacency

    def merge_tables(self, table2, table2_width, table2_bbox=None, table2_page=None):
        pending = self.pending_table
        before = None
        column = None
        glue = True
        cut = self.__dict__.get('table_cut_by_page', False)
        self.__dict__['table_cut_by_page'] = False
        if pending is not None and table2_bbox is not None and not pending[0].empty and not table2.empty \
                and pending[0].shape[1] == table2.shape[1]:
            before = pending[0]
            found = self.continued_column(before.iloc[-1], table2.iloc[0], cut)
            if found is not None:
                column, glue = found
        super().merge_tables(table2, table2_width, table2_bbox, table2_page)
        if column is None or self.pending_table is None:
            return
        merged = self.pending_table[0]
        boundary = before.shape[0]
        if merged.shape[0] != boundary + table2.shape[0]:
            return
        joined = self.join_cell_html(str(merged.iat[boundary - 1, column]), str(merged.iat[boundary, column]), glue)
        if joined is None:
            return
        merged = merged.copy()
        merged.iat[boundary - 1, column] = joined
        self.pending_table[0] = merged.drop(merged.index[boundary]).reset_index(drop=True)

    def continued_column(self, last_row, first_row, cut=False):
        filled_last = [i for i, v in enumerate(last_row) if not self._is_blank_cell(v)]
        filled_first = [i for i, v in enumerate(first_row) if not self._is_blank_cell(v)]
        if len(filled_last) != 1 or filled_last != filled_first:
            return None
        column = filled_last[0]
        previous = str(last_row.iloc[column])
        following = str(first_row.iloc[column])
        previous_blocks = BLOCK_RE.findall(previous)
        tail = TAG_RE.sub('', previous_blocks[-1] if previous_blocks else previous).strip()
        head = TAG_RE.sub('', following).strip()
        if not tail or not head:
            return None
        if not SENTENCE_END_RE.search(tail) and not LINE_MARKER_RE.match(head):
            return column, True
        if cut and len(last_row) >= 2:
            return column, False
        return None

    def flatten_cell(self, value):
        if not isinstance(value, str) or (PARA_OPEN not in value and '<pre>' not in value):
            return value
        out = ''
        position = 0
        for match in BLOCK_RE.finditer(value):
            loose = value[position:match.start()].strip()
            if loose:
                out = out + ' ' + loose if out else loose
            block = match.group(0)[len(PARA_OPEN):-len(PARA_CLOSE)]
            out = out + '\n' + block if out else block
            position = match.end()
        loose = value[position:].strip()
        if loose:
            out = out + ' ' + loose if out else loose
        return out

    def flatten_table_cells(self, table):
        if table.empty:
            return table
        rows = [[self.flatten_cell(v) for v in row] for row in table.itertuples(index=False, name=None)]
        return pd.DataFrame(rows, columns=table.columns)

    def join_cell_html(self, previous, following, glue=True):
        def to_blocks(html):
            if html.startswith(PARA_OPEN) or html.startswith('<pre>'):
                blocks = BLOCK_RE.findall(html)
                return blocks if ''.join(blocks) == html else None
            return [PARA_OPEN + html + PARA_CLOSE]

        def inner(block):
            return block[len(PARA_OPEN):-len(PARA_CLOSE)]

        before = to_blocks(previous)
        after = to_blocks(following)
        if not before or not after:
            return None
        if glue and before[-1].startswith(PARA_OPEN) and after[0].startswith(PARA_OPEN):
            text = self.join_styled(inner(before[-1]), ' ' + inner(after[0]))
            blocks = before[:-1] + [PARA_OPEN + text + PARA_CLOSE] + after[1:]
        else:
            blocks = before + after
        if len(blocks) == 1 and blocks[0].startswith(PARA_OPEN):
            return inner(blocks[0])
        return ''.join(blocks)
