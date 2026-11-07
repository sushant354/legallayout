import logging
import math


class WatermarkDetector:

    DIAGONAL_MIN_CHARS = 3
    DIAGONAL_MIN_ANGLE = 10
    DIAGONAL_MAX_ANGLE = 80
    DIAGONAL_STEP_RATIO = 0.9
    RECURRENCE_PAGE_RATIO = 0.5
    RECURRENCE_MIN_PAGES = 2
    POSITION_TOLERANCE = 0.01
    OVERLAP_RATIO = 0.5

    def __init__(self, pages):
        self.logger = logging.getLogger(__name__)
        self.pages = list(pages)

    def mark(self):
        try:
            entries = self.collect_entries()
            sightings = self.index_sightings(entries)
            for entry in entries:
                if self.is_watermark(entry, sightings):
                    entry["tb"].set("watermark", "1")
        except Exception as e:
            self.logger.error("Failed watermark detection: %s", e)

    def collect_entries(self):
        entries = []
        for page_idx, page in enumerate(self.pages):
            try:
                page_w, page_h = self.get_size(page.attrib["bbox"])
            except (KeyError, ValueError):
                continue
            if page_w <= 0 or page_h <= 0:
                continue
            page_entries = []
            for tb in page.findall(".//textbox"):
                if not tb.findall("textline"):
                    continue
                try:
                    x0, y0, x1, y1 = map(float, tb.attrib["bbox"].split(","))
                except (KeyError, ValueError):
                    continue
                page_entries.append({
                    "tb": tb,
                    "page": page_idx,
                    "box": (x0, y0, x1, y1),
                    "cx": (x0 + x1) / 2 / page_w,
                    "cy": (y0 + y1) / 2 / page_h,
                    "sig": self.get_signature(tb),
                    "siblings": page_entries,
                })
            entries.extend(page_entries)
        return entries

    @staticmethod
    def get_size(bbox):
        x0, y0, x1, y1 = map(float, bbox.split(","))
        return abs(x1 - x0), abs(y1 - y0)

    @staticmethod
    def get_signature(tb):
        text = "".join(t.text for t in tb.findall(".//text") if t.text)
        return "".join(c.lower() for c in text if c.isalnum())

    @staticmethod
    def index_sightings(entries):
        sightings = {}
        for entry in entries:
            if entry["sig"]:
                sightings.setdefault(entry["sig"], []).append(entry)
        return sightings

    def is_watermark(self, entry, sightings):
        if not entry["sig"]:
            return False
        diagonal = self.is_diagonal(entry["tb"])
        if len(self.pages) < self.RECURRENCE_MIN_PAGES:
            return diagonal
        if not self.recurs(entry, sightings):
            return False
        return diagonal or self.is_overlapped(entry)

    def recurs(self, entry, sightings):
        pages_seen = {
            other["page"] for other in sightings[entry["sig"]]
            if abs(other["cx"] - entry["cx"]) <= self.POSITION_TOLERANCE
            and abs(other["cy"] - entry["cy"]) <= self.POSITION_TOLERANCE
        }
        needed = max(
            self.RECURRENCE_MIN_PAGES,
            math.ceil(self.RECURRENCE_PAGE_RATIO * len(self.pages))
        )
        return len(pages_seen) >= needed

    def is_overlapped(self, entry):
        x0, y0, x1, y1 = entry["box"]
        area = (x1 - x0) * (y1 - y0)
        if area <= 0:
            return False
        for other in entry["siblings"]:
            if other is entry or other["sig"] == entry["sig"]:
                continue
            ox0, oy0, ox1, oy1 = other["box"]
            width = min(x1, ox1) - max(x0, ox0)
            height = min(y1, oy1) - max(y0, oy0)
            if width > 0 and height > 0 and width * height >= self.OVERLAP_RATIO * area:
                return True
        return False

    def is_diagonal(self, tb):
        qualifying = 0
        for textline in tb.findall(".//textline"):
            centres = []
            for text in textline.findall(".//text"):
                if not text.text or not text.text.strip() or "bbox" not in text.attrib:
                    continue
                try:
                    x0, y0, x1, y1 = map(float, text.attrib["bbox"].split(","))
                except ValueError:
                    return False
                centres.append(((x0 + x1) / 2, (y0 + y1) / 2))
            if len(centres) < 2:
                continue
            if len(centres) < self.DIAGONAL_MIN_CHARS or not self.is_diagonal_run(centres):
                return False
            qualifying += 1
        return qualifying > 0

    def is_diagonal_run(self, centres):
        steps = list(zip(centres, centres[1:]))
        total_dx = centres[-1][0] - centres[0][0]
        total_dy = centres[-1][1] - centres[0][1]
        angle = math.degrees(math.atan2(abs(total_dy), abs(total_dx)))
        if not self.DIAGONAL_MIN_ANGLE < angle < self.DIAGONAL_MAX_ANGLE:
            return False
        same_direction = sum(
            1 for a, b in steps
            if (b[0] - a[0]) * total_dx > 0 and (b[1] - a[1]) * total_dy > 0
        )
        return same_direction >= self.DIAGONAL_STEP_RATIO * len(steps)
