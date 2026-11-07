import math

import cv2
import numpy as np

MIN_COMPONENT_AREA = 4
MIN_BOLD_CHARS = 3
MIN_WORD_CHARS = 2
MIN_ITALIC_CHARS = 3
MIN_ITALIC_COMPONENTS = 2
BOLD_REFERENCE_PERCENTILE = 25
BOLD_LINE_FACTOR = 1.2
BOLD_WORD_FACTOR = 1.3
ITALIC_ANGLES = np.arange(-6.0, 30.1, 2.0)
ITALIC_MIN_EXCESS_DEG = 6.0
ITALIC_MIN_GAIN = 1.02
UNDERLINE_ZONE_ABOVE = 0.25
UNDERLINE_ZONE_BELOW = 0.45
UNDERLINE_RUN_RATIO = 0.9
UNDERLINE_MAX_THICKNESS = 0.25
UNDERLINE_BREAK_GAP = 1
UNDERLINE_RUN_GAP = 2
UNDERLINE_OVERRUN = 1.5
HEIGHT_PERCENTILE = 90
LINE_HEIGHT_TOLERANCE = 0.25


class ScanStyleDetector:

    def __init__(self, gray, scale):
        _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        self.ink = ink > 0
        self.image_height, self.image_width = self.ink.shape
        self.scale = scale
        self.cross = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))

    def annotate(self, words):
        lines = {}
        for word in words:
            lines.setdefault((word['block'], word['par'], word['line']), []).append(word)

        measured_lines = []
        for line_words in lines.values():
            line_records = [rec for rec in (self.measure_word(w) for w in line_words) if rec]
            if not line_records:
                continue
            heights = [h for rec in line_records for h in rec['heights']]
            measured_lines.append((line_records, float(np.percentile(heights, HEIGHT_PERCENTILE))))

        records = []
        if measured_lines:
            page_height = float(np.median([height for line_records, height in measured_lines]))
            for line_records, line_height in measured_lines:
                if page_height > 0 and abs(line_height / page_height - 1.0) <= LINE_HEIGHT_TOLERANCE:
                    line_height = page_height
                extent = (min(rec['box'][0] for rec in line_records),
                          max(rec['box'][2] for rec in line_records))
                for rec in line_records:
                    rec['line_height'] = max(line_height, 1.0)
                    rec['extent'] = extent
                    rec['line'] = line_records
                records.extend(line_records)

        if not records:
            return

        bold = self.find_bold(records)
        italic = self.find_italic(records)
        underlined = {id(rec) for rec in records if self.is_underlined(rec)}

        for line_records in lines_of(records):
            self.fill_line(line_records, bold, True)
            self.fill_line(line_records, italic, False)
            self.fill_line(line_records, underlined, True)

        for rec in records:
            style = ''
            if id(rec) in bold:
                style += 'b'
            if id(rec) in italic:
                style += 'i'
            if id(rec) in underlined:
                style += 'u'
            if style:
                rec['word']['fstyle'] = style

    @staticmethod
    def fill_line(line_records, flagged, by_majority):
        ordered = sorted(line_records, key=lambda rec: rec['box'][0])
        marks = [id(rec) in flagged for rec in ordered]
        if not any(marks):
            return
        first = marks.index(True)
        last = len(marks) - 1 - marks[::-1].index(True)
        for index in range(first, last + 1):
            flagged.add(id(ordered[index]))
        if by_majority:
            words = [rec for rec in ordered if len(rec['word']['text']) >= MIN_WORD_CHARS]
            hits = sum(1 for rec in words if id(rec) in flagged)
            if hits >= 2 and hits * 2 > len(words):
                for rec in ordered:
                    if has_letter(rec):
                        flagged.add(id(rec))

    def pixel_box(self, word):
        x0 = max(int(word['left'] / self.scale), 0)
        y0 = max(int(word['top'] / self.scale), 0)
        x1 = min(int(math.ceil(word['right'] / self.scale)), self.image_width)
        y1 = min(int(math.ceil(word['bottom'] / self.scale)), self.image_height)
        return x0, y0, x1, y1

    def measure_word(self, word):
        x0, y0, x1, y1 = self.pixel_box(word)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return None
        crop = self.ink[y0:y1, x0:x1].astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(crop, connectivity=8)
        keep = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= MIN_COMPONENT_AREA]
        if not keep:
            return None
        if len(keep) < count - 1:
            crop = np.isin(labels, keep).astype(np.uint8)
        return {
            'word': word,
            'box': (x0, y0, x1, y1),
            'crop': crop,
            'heights': [int(stats[i, cv2.CC_STAT_HEIGHT]) for i in keep],
            'components': len(keep),
        }

    def stroke_width(self, crop):
        area = int(crop.sum())
        edge = area - int(cv2.erode(crop, self.cross).sum())
        if edge <= 0:
            return None
        return 2.0 * area / edge

    def find_bold(self, records):
        ratios = {}
        for rec in records:
            if alnum_count(rec) < MIN_BOLD_CHARS:
                continue
            width = self.stroke_width(rec['crop'])
            if width:
                ratios[id(rec)] = width / rec['line_height']
        if not ratios:
            return set()
        reference = float(np.percentile(list(ratios.values()), BOLD_REFERENCE_PERCENTILE))
        line_floor = reference * BOLD_LINE_FACTOR
        word_floor = reference * BOLD_WORD_FACTOR

        bold = set()
        for line_records in lines_of(records):
            ordered = sorted((rec for rec in line_records if id(rec) in ratios),
                             key=lambda rec: rec['box'][0])
            if not ordered:
                continue
            values = [ratios[id(rec)] for rec in ordered]
            if float(np.median(values)) >= line_floor:
                bold.update(id(rec) for rec in line_records if has_letter(rec))
                continue
            for index, rec in enumerate(ordered):
                if values[index] < word_floor:
                    continue
                near = values[max(index - 1, 0):index] + values[index + 1:index + 2]
                if any(value >= line_floor for value in near):
                    bold.add(id(rec))
        return bold

    def slant_scores(self, crop):
        height, width = crop.shape
        pad = int(height * math.tan(math.radians(float(ITALIC_ANGLES[-1])))) + 1
        canvas = np.zeros((height, width + 2 * pad), np.float32)
        canvas[:, pad:pad + width] = crop
        scores = []
        for angle in ITALIC_ANGLES:
            shear = math.tan(math.radians(float(angle)))
            matrix = np.float32([[1.0, shear, -shear * height / 2.0], [0.0, 1.0, 0.0]])
            sheared = cv2.warpAffine(canvas, matrix, (canvas.shape[1], height))
            columns = sheared.sum(axis=0)
            scores.append(float((columns * columns).sum()))
        return scores

    def find_italic(self, records):
        measured = []
        for rec in records:
            if len(rec['word']['text']) < MIN_ITALIC_CHARS or rec['components'] < MIN_ITALIC_COMPONENTS:
                continue
            scores = self.slant_scores(rec['crop'])
            best = int(np.argmax(scores))
            measured.append((rec, scores, best))
        if not measured:
            return set()

        page_angle = float(np.median([ITALIC_ANGLES[best] for rec, scores, best in measured]))
        reference = int(np.argmin(np.abs(ITALIC_ANGLES - page_angle)))

        flagged = set()
        for rec, scores, best in measured:
            if float(ITALIC_ANGLES[best]) - page_angle < ITALIC_MIN_EXCESS_DEG:
                continue
            if scores[reference] <= 0 or scores[best] / scores[reference] < ITALIC_MIN_GAIN:
                continue
            flagged.add(id(rec))

        italic = set()
        for rec, scores, best in measured:
            if id(rec) not in flagged:
                continue
            ordered = sorted(rec['line'], key=lambda other: other['box'][0])
            index = next(i for i, other in enumerate(ordered) if other is rec)
            neighbours = ordered[max(index - 1, 0):index] + ordered[index + 1:index + 2]
            if any(id(other) in flagged for other in neighbours):
                italic.add(id(rec))
        return italic

    def is_underlined(self, rec):
        text = rec['word']['text']
        if len(text) < MIN_WORD_CHARS or not any(ch.isalnum() for ch in text):
            return False
        x0, y0, x1, y1 = rec['box']
        line_height = rec['line_height']
        top = max(int(y1 - UNDERLINE_ZONE_ABOVE * line_height), 0)
        bottom = min(int(y1 + UNDERLINE_ZONE_BELOW * line_height) + 1, self.image_height)
        if bottom - top < 1:
            return False

        zone = self.ink[top:bottom, x0:x1]
        grown = zone.copy()
        grown[1:] |= zone[:-1]
        grown[:-1] |= zone[1:]
        width = x1 - x0
        best_row = None
        best_run = 0
        thick = 0
        for offset in range(grown.shape[0]):
            run = longest_run(grown[offset], UNDERLINE_BREAK_GAP)
            if run >= UNDERLINE_RUN_RATIO * width:
                thick += 1
                if run > best_run:
                    best_run = run
                    best_row = offset
        if best_row is None:
            return False
        if thick > max(UNDERLINE_MAX_THICKNESS * line_height, 3.0):
            return False

        row = top + best_row
        return not self.runs_past_line(row, (x0 + x1) // 2, rec['extent'], line_height)

    def runs_past_line(self, row, centre, extent, line_height):
        band = self.ink[max(row - 1, 0):row + 2, :].any(axis=0)
        columns = np.where(band)[0]
        if not len(columns):
            return False
        breaks = np.where(np.diff(columns) > UNDERLINE_RUN_GAP + 1)[0]
        starts = np.concatenate(([0], breaks + 1))
        ends = np.concatenate((breaks, [len(columns) - 1]))
        for start, end in zip(starts, ends):
            run_left, run_right = int(columns[start]), int(columns[end])
            if run_left <= centre <= run_right:
                margin = UNDERLINE_OVERRUN * line_height
                return run_left < extent[0] - margin or run_right > extent[1] + margin
        return False


def alnum_count(rec):
    return sum(1 for ch in rec['word']['text'] if ch.isalnum())


def has_letter(rec):
    return any(ch.isalnum() for ch in rec['word']['text'])


def lines_of(records):
    seen = set()
    for rec in records:
        key = id(rec['line'])
        if key in seen:
            continue
        seen.add(key)
        yield rec['line']


def longest_run(row, gap):
    columns = np.where(row)[0]
    if not len(columns):
        return 0
    breaks = np.where(np.diff(columns) > gap + 1)[0]
    starts = np.concatenate(([0], breaks + 1))
    ends = np.concatenate((breaks, [len(columns) - 1]))
    return int((columns[ends] - columns[starts]).max()) + 1
