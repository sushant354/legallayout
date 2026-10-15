import logging
import re
import unicodedata

from .SentenceEndDetector import SENTENCE_END_CHAR_CLASS, INDIC_SENTENCE_END_CHARS
from .Utils import INDIC_WORD_CHARS, INDIC_DIGIT_CHARS, indic_annotation_alternation, is_separator_line

SECTION_HEADING_ENUM_RE = re.compile(r'^(?:[IVXLC]{1,5}|[A-Z' + INDIC_WORD_CHARS + r'])\.\s+\S|^Re:\s+\S')
LIST_ITEM_RE = re.compile(
    r'^\s*(?:\([A-Za-z0-9' + INDIC_WORD_CHARS + INDIC_DIGIT_CHARS + r']{1,4}\)'
    r'|\d{1,3}(?:\.\d{1,3})+\.?'
    r'|[A-Za-z0-9' + INDIC_WORD_CHARS + INDIC_DIGIT_CHARS + r']{1,3}[.\)])(?=\s|$)'
)

_ROMAN_VALUES = {'i': 1, 'v': 5, 'x': 10, 'l': 50, 'c': 100, 'd': 500, 'm': 1000}


def _roman_to_int(text):
    text = text.lower()
    if not text or any(ch not in _ROMAN_VALUES for ch in text):
        return None
    total = 0
    prev = 0
    for ch in reversed(text):
        value = _ROMAN_VALUES[ch]
        total += value if value >= prev else -value
        prev = value
    return total


SUBITEM_MARKER_RE = re.compile(r'^\(?(?:[ivxlcdm]{1,5}|[^\W\d_])[\.\)]$', re.IGNORECASE)


class ClauseTracker:

    _PATTERNS = (
        ("roman", re.compile(r'^\(([ivxlcdmIVXLCDM]{2,6})\)(?=[\s.:;]|$)')),
        ("arabic_dot", re.compile(r'^(\d{1,3})[.\)]?(?=[\s]|$|[A-Z' + INDIC_WORD_CHARS + r'])')),
        ("paren_num", re.compile(r'^\((\d{1,3})\)(?=[\s.:;]|$)')),
        ("alpha", re.compile(r'^\(([A-Za-z' + INDIC_WORD_CHARS + r'])\)(?=[\s.:;]|$)')),
    )

    def __init__(self):
        self.stack = []  # innermost-last list of {"kind": str, "value": int, "x0": float|None}

    @classmethod
    def parse_marker(cls, text):
        for kind, rgx in cls._PATTERNS:
            match = rgx.match(text)
            if not match:
                continue
            raw = match.group(1)
            if kind == "roman":
                value = _roman_to_int(raw)
                if value is None:
                    continue
            elif kind == "alpha":
                value = ord(raw.lower()) - ord('a') + 1
            else:
                value = int(raw)
            return {"kind": kind, "value": value}
        return None

    @staticmethod
    def matches(marker, expected):
        return (
            marker is not None and expected is not None and
            marker["kind"] == expected["kind"] and marker["value"] == expected["value"]
        )

    @staticmethod
    def matches_with_indent(marker, expected, row_x0, indent_x0, row_height):
        if not ClauseTracker.matches(marker, expected):
            return False
        expected_x0 = expected.get("x0")
        if expected_x0 is None or row_x0 is None:
            # no coordinate baseline recorded - fall back to the old,
            # value-only check rather than refuse to ever close/skip.
            return True
        unit = row_height or 8.0
        tight = max(4.0, 0.4 * unit)
        loose = max(10.0, 1.2 * unit)
        delta_outer = abs(row_x0 - expected_x0)
        if delta_outer <= tight:
            return True
        if delta_outer > loose or indent_x0 is None:
            return False
        delta_indent = abs(row_x0 - indent_x0)
        return delta_outer < delta_indent

    def snapshot_next_siblings(self):
        return [
            {"kind": level["kind"], "value": level["value"] + 1, "x0": level.get("x0")}
            for level in reversed(self.stack)
        ]

    @staticmethod
    def matches_any_with_indent(marker, expected_list, row_x0, indent_x0, row_height):
        return any(
            ClauseTracker.matches_with_indent(marker, expected, row_x0, indent_x0, row_height)
            for expected in (expected_list or [])
        )

    def snapshot_next_sibling(self):
        if not self.stack:
            return None
        top = self.stack[-1]
        return {"kind": top["kind"], "value": top["value"] + 1, "x0": top.get("x0")}

    def observe(self, marker, x0=None):
        if marker is None:
            return
        marker = dict(marker, x0=x0)
        for depth in range(len(self.stack) - 1, -1, -1):
            level = self.stack[depth]
            if level["kind"] == marker["kind"] and marker["value"] == level["value"] + 1:
                del self.stack[depth + 1:]
                self.stack[depth] = marker
                return
        if marker["value"] == 1:
            self.stack.append(marker)
        elif not self.stack:
            self.stack.append(marker)


class Amendment:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.isAmendmentPDF = False
        self.quote_stack = []
        self.bq_active = False
        self.bq_quote_stack = []
        self.bq_pending_trigger = False
        self.bq_return_marker = None
        self.bq_return_markers = []
        self.bq_indent_x0 = None
        self.bq_outer_x0 = None
        self.bq_active_run_length = 0
        self.bq_has_evidence = False
        self.bq_seen_row = False
        self.bq_prev_block_boundary = True
        self.bq_style_mode = False
        self.bq_all_styled = False
        self.bq_closed_indent_x0 = None
        self.body_margins = {}
        self.doc_margin = None
        self.doc_right = None
        self.clause_tracker = ClauseTracker()
        self.bq2_pending_trigger = False
        self.bq2_mode = None
        self.bq2_list_stack = []
        self.bq2_list_parent = None
        self.bq2_main_last = None
        self.bq2_main_offset = None
        self.bq2_main_x0 = None
        self.bq2_main_fmt = None
        self.bq2_prev_text = None
        self.bq2_prev_x1 = None
        self.bq2_suspended = None
        self.bq2_prev_top = None
        self.bq2_prev_page = None
        self.bq2_prev_start = False
        self.bq2_main_plan = set()
        self.bq2_pitch = {}
        self.bq2_section_plan = set()

    @staticmethod
    def block_between_rows(rows, row_idx, block_spans):
        if not block_spans:
            return False
        row_top = max(b["y_top"] for b in rows[row_idx]["boxes"])
        if row_idx == 0:
            return any(y0 >= row_top - 1.0 for y0, y1 in block_spans)
        previous_bottom = min(b["tb"].coords[1] for b in rows[row_idx - 1]["boxes"])
        return any(y1 <= previous_bottom + 1.0 and y0 >= row_top - 1.0 for y0, y1 in block_spans)

    @staticmethod
    def get_hanging_text_x0(tb, row_height):
        try:
            textline = tb.tbox.find(".//textline")
            chars = []
            for char in textline.findall("text"):
                if char.text is None or "bbox" not in char.attrib:
                    continue
                x0, _, x1, _ = map(float, char.attrib["bbox"].split(","))
                chars.append((char.text, x0, x1))
        except Exception:
            return None
        marker_end = None
        for idx, (ch, x0, x1) in enumerate(chars):
            if ch.isspace():
                if marker_end is None and idx:
                    marker_end = chars[idx - 1][2]
                continue
            if marker_end is not None:
                gap = x0 - marker_end
                return x0 if gap >= (row_height or 8.0) else None
        return None

    # --- func to classify the textbox if it is detected with sign of amendments properties ---
    def check_for_amendment_acts(self, page): #,startPage,endPage):
        for tb in page.all_tbs.keys():
            try:
                text = tb.extract_text_from_tb().strip()
            except Exception as e:
                self.logger.warning(f"Failed to extract text from textbox on page {getattr(page, 'pg_num', '?')}: {e}")
                continue

            text = text.replace('\u201C', '"').replace('\u201D', '"').replace('\u2018\u2018','"').replace('\u2019\u2019','"') \
                .replace('\u201E', '"').replace('\u201F', '"').replace('\u00AB', '"').replace('\u00BB', '"').replace('\uFF02', '"') \
                .replace('\u2018', "'").replace('\u2019', "'").replace('\u201A', "'").replace('\u201B', "'") \
                .replace('\u2039', "'").replace('\u203A', "'").replace('\uFF07', "'")

            try:
                label = page.all_tbs[tb]
            except Exception as e:
                self.logger.warning(f"Failed to retrieve label for textbox on page {getattr(page, 'pg_num', '?')}: {e}")
                continue

            try:
                page_num = int(page.pg_num)
            except Exception as e:
                self.logger.error(f"Invalid page number: {page.pg_num}")
                continue

            if ((label is not None and isinstance(label, tuple) and (label[0] == "table" or \
                                                                     label[0] == "borderless_table")) \
              or (label is None)): #and startPage is not None and endPage is not None and  startPage <= page_num <= endPage:
                doubleQuote_count = text.count('"')
                singleQuote_count = text.count("'")
                self.logger.debug(f"Page {page.pg_num}, Text: '{text}'")
                self.logger.debug(f"Quote counts — Double: {doubleQuote_count}, Single: {singleQuote_count}")
                self.logger.debug(f"Quote Stack: {self.quote_stack}")


                try:
                    # Check for self-contained quotes
                    if ((text.startswith('"') and (text.endswith('".') or text.endswith('";') or \
                                                   text.endswith('."') or text.endswith(';"') or \
                                                   text.endswith('". and') or text.endswith('." and') or \
                                                   text.endswith(';" and') or text.endswith('"; and') or \
                                                   text.endswith('". or') or text.endswith('." or') or \
                                                   text.endswith(';" or') or text.endswith('"; or'))) or \
                        (text.startswith("'") and (text.endswith("'.") or text.endswith("';") or \
                                               text.endswith('.\'') or text.endswith(';\'') or \
                                               text.endswith('\'. and') or text.endswith('.\' and') or \
                                               text.endswith(';\' and') or text.endswith('\'; and') or \
                                               text.endswith('\'. or') or text.endswith('.\' or') or \
                                               text.endswith(';\' or') or text.endswith('\'; or')))):
                        self.isAmendmentPDF = True
                        self.logger.debug(f"Detected self-contained quote on page {page.pg_num}. Marked as amendment PDF.")
                        if label is not None and isinstance(label, tuple) and (label[0] == "table" or \
                                                                               label[0] == 'borderless_table'):
                            continue
                        page.all_tbs[tb] = ["amendment"]


                    # Check for opening quote
                    elif  (text.startswith('"')) and (doubleQuote_count%2!=0):
                        self.quote_stack.append(text[0])
                        self.logger.debug(f"Detected opening quote with imbalance on page {page.pg_num}. Pushed to quote_stack.")
                        self.isAmendmentPDF = True
                        if label is not None and isinstance(label, tuple) and (label[0] == "table" or \
                                                                               label[0] == "borderless_table"):
                            continue
                        page.all_tbs[tb] = ["amendment"]

                    elif  (text.startswith("'")) and (singleQuote_count%2!=0):
                        self.quote_stack.append(text[0])
                        self.logger.debug(f"Detected opening quote with imbalance on page {page.pg_num}. Pushed to quote_stack.")
                        self.isAmendmentPDF = True
                        if label is not None and isinstance(label, tuple) and (label[0] == "table" or \
                                                                               label[0] == "borderless_table"):
                            continue
                        page.all_tbs[tb] = ["amendment"]


                    # Check for closing quote
                    elif self.quote_stack and self.quote_stack[-1] == "'" and singleQuote_count%2!=0 and \
                        (text.endswith(self.quote_stack[-1] + ".") or text.endswith(self.quote_stack[-1] + ";") or\
                         text.endswith(self.quote_stack[-1] + ". and") or text.endswith(self.quote_stack[-1] + "; and") or \
                         text.endswith(self.quote_stack[-1] + ". or") or text.endswith(self.quote_stack[-1] + "; or") or\
                         text.endswith("."+self.quote_stack[-1]) or text.endswith(";"+self.quote_stack[-1]) or\
                         text.endswith("."+self.quote_stack[-1]+" and") or text.endswith(";"+self.quote_stack[-1]+" and") or\
                         text.endswith("."+self.quote_stack[-1]+" or") or text.endswith(";"+self.quote_stack[-1]+" or") \
                         ):
                        self.quote_stack.pop()
                        self.logger.debug(f"Detected closing quote on page {page.pg_num}. Popped from quote_stack.")
                        if label is not None and isinstance(label, tuple) and (label[0] == "table" or \
                                                                               label[0] == "borderless_table"):
                            continue
                        page.all_tbs[tb] = ["amendment"]

                    elif self.quote_stack and self.quote_stack[-1] == '"' and doubleQuote_count%2!=0 and \
                        (text.endswith(self.quote_stack[-1] + ".") or text.endswith(self.quote_stack[-1] + ";") or \
                        text.endswith(self.quote_stack[-1] + ". and") or text.endswith(self.quote_stack[-1] + "; and") or \
                        text.endswith(self.quote_stack[-1] + ". or") or text.endswith(self.quote_stack[-1] + "; or") or \
                        text.endswith("."+self.quote_stack[-1]) or text.endswith(";"+self.quote_stack[-1]) or \
                        text.endswith("."+self.quote_stack[-1]+" and") or text.endswith(";"+self.quote_stack[-1]+" and") or \
                        text.endswith("."+self.quote_stack[-1]+" or") or text.endswith(";"+self.quote_stack[-1]+" or") \
                         ):
                        self.quote_stack.pop()
                        self.logger.debug(f"Detected closing quote on page {page.pg_num}. Popped from quote_stack.")
                        if label is not None and isinstance(label, tuple) and (label[0] == "table" or \
                                                                               label[0] == "borderless_table"):
                            continue
                        page.all_tbs[tb] = ["amendment"]


                    # Inside an open quote block
                    elif self.quote_stack:
                        if label is not None and isinstance(label, tuple) and (label[0] == "table" or \
                                                                               label[0] == "borderless_table"):
                            continue
                        page.all_tbs[tb] = ["amendment"]
                        self.logger.debug(f"Inside open quote block on page {page.pg_num}.The text [{text}] marked as amendment.")

                except Exception as e:
                    self.logger.error(f"Error while processing amendment logic on page {page_num}: {e}")

    def check_for_blockquotes(self, page):
        for tb in page.all_tbs.keys():
            try:
                text = tb.extract_text_from_tb().strip()
            except Exception as e:
                self.logger.warning(f"Failed to extract text from textbox on page {getattr(page, 'pg_num', '?')}: {e}")
                continue

            text = text.replace('\u201C', '"').replace('\u201D', '"').replace('\u2018\u2018','"').replace('\u2019\u2019','"').replace('\u2018', "'").replace('\u2019', "'")

            try:
                label = page.all_tbs[tb]
            except Exception as e:
                self.logger.warning(f"Failed to retrieve label for textbox on page {getattr(page, 'pg_num', '?')}: {e}")
                continue

            try:
                page_num = int(page.pg_num)
            except Exception as e:
                self.logger.error(f"Invalid page number: {page.pg_num}")
                continue

            if label is None:
                doubleQuote_count = text.count('"')
                singleQuote_count = text.count("'")
                self.logger.debug(f"Page {page.pg_num}, Text: '{text}'")
                self.logger.debug(f"Quote counts — Double: {doubleQuote_count}, Single: {singleQuote_count}")
                self.logger.debug(f"Quote Stack: {self.quote_stack}")


                try:
                    # Check for self-contained quotes
                    if (
                            (
                                 text.startswith('"') and (
                                    text.lower().endswith('(emphasis supplied)') or
                                    text.lower().endswith('[emphasis supplied]') or
                                    text.endswith('".') or
                                    text.endswith('";') or
                                    text.endswith('…"') or
                                    text.lower().endswith('…" (emphasis supplied)') or
                                    text.lower().endswith('." (emphasis supplied)') or
                                    text.lower().endswith(';" (emphasis supplied)') or
                                    text.lower().endswith('…"(emphasis supplied)') or
                                    text.lower().endswith('."(emphasis supplied)') or
                                    text.lower().endswith(';"(emphasis supplied)') or

                                    text.lower().endswith('…" [emphasis supplied]') or
                                    text.lower().endswith('." [emphasis supplied]') or
                                    text.lower().endswith(';" [emphasis supplied]') or
                                    text.lower().endswith('…"[emphasis supplied]') or
                                    text.lower().endswith('."[emphasis supplied]') or
                                    text.lower().endswith(';"[emphasis supplied]') or

                                    text.endswith('."') or
                                    text.endswith(';"') or
                                    text.lower().endswith('". (emphasis supplied)') or
                                    text.lower().endswith('"; (emphasis supplied)') or
                                    text.lower().endswith('".(emphasis supplied)') or
                                    text.lower().endswith('";(emphasis supplied)') or

                                    text.lower().endswith('". [emphasis supplied]') or
                                    text.lower().endswith('"; [emphasis supplied]') or
                                    text.lower().endswith('".[emphasis supplied]') or
                                    text.lower().endswith('";[emphasis supplied]') or
                                    text.endswith('"')
                                )
                            )
                            or
                            (
                                text.startswith("'") and (
                                    text.lower().endswith('(emphasis supplied)') or
                                    text.lower().endswith('[emphasis supplied]') or
                                    text.endswith("'.") or
                                    text.endswith("';") or
                                    text.endswith("…'") or
                                    text.lower().endswith("…' (emphasis supplied)") or
                                    text.lower().endswith(".' (emphasis supplied)") or
                                    text.lower().endswith(";' (emphasis supplied)") or
                                    text.lower().endswith("…'(emphasis supplied)") or
                                    text.lower().endswith(".'(emphasis supplied)") or
                                    text.lower().endswith(";'(emphasis supplied)") or

                                    text.lower().endswith("…' [emphasis supplied]") or
                                    text.lower().endswith(".' [emphasis supplied]") or
                                    text.lower().endswith(";' [emphasis supplied]") or
                                    text.lower().endswith("…'[emphasis supplied]") or
                                    text.lower().endswith(".'[emphasis supplied]") or
                                    text.lower().endswith(";'[emphasis supplied]") or
                                    text.endswith(".'") or
                                    text.endswith(";'") or
                                    text.lower().endswith("'.(emphasis supplied)") or
                                    text.lower().endswith("';(emphasis supplied)") or
                                    text.lower().endswith("'.[emphasis supplied]") or
                                    text.lower().endswith("';[emphasis supplied]") or
                                    text.endswith("'")
                                )
                            )
                    ):
                        self.isAmendmentPDF = True
                        self.logger.debug(f"Detected self-contained quote on page {page.pg_num}. Marked as amendment PDF.")
                        page.all_tbs[tb] = 'blockquote'


                    # Check for opening quote
                    elif (text.startswith('"')) and (doubleQuote_count%2!=0):
                        self.quote_stack.append(text[0])
                        self.logger.debug(f"Detected opening quote with imbalance on page {page.pg_num}. Pushed to quote_stack.")
                        self.isAmendmentPDF = True
                        page.all_tbs[tb] = 'blockquote'

                    elif (text.startswith("'")) and (singleQuote_count%2!=0):
                        self.quote_stack.append(text[0])
                        self.logger.debug(f"Detected opening quote with imbalance on page {page.pg_num}. Pushed to quote_stack.")
                        self.isAmendmentPDF = True
                        page.all_tbs[tb] = 'blockquote'

                    # Check for closing quote
                    elif self.quote_stack and self.quote_stack[-1] == '"' and doubleQuote_count%2!=0  and (text.lower().endswith('(emphasis supplied)') or
                                               text.lower().endswith('[emphasis supplied]') or text.endswith(self.quote_stack[-1] + ".") or text.endswith(self.quote_stack[-1] + ";") or \
                                               text.lower().endswith(self.quote_stack[-1] + "." + " (emphasis supplied)") or text.lower().endswith(self.quote_stack[-1] + ";" + " (emphasis supplied)") or\
                                               text.lower().endswith(self.quote_stack[-1] + "." + "(emphasis supplied)") or text.lower().endswith(self.quote_stack[-1] + ";" + "(emphasis supplied)") or \

                                               text.lower().endswith(self.quote_stack[-1] + "." + " [emphasis supplied]") or text.lower().endswith(self.quote_stack[-1] + ";" + " [emphasis supplied]") or\
                                               text.lower().endswith(self.quote_stack[-1] + "." + "[emphasis supplied]") or text.lower().endswith(self.quote_stack[-1] + ";" + "[emphasis supplied]")\

                                               or  text.endswith("."+self.quote_stack[-1]) or  text.endswith(";"+self.quote_stack[-1]) or \
                                               text.lower().endswith("."+self.quote_stack[-1]+ " (emphasis supplied)") or text.lower().endswith(";"+self.quote_stack[-1]+ " (emphasis supplied)") or \
                                               text.lower().endswith("."+self.quote_stack[-1]+ "(emphasis supplied)") or text.lower().endswith(";"+self.quote_stack[-1]+ "(emphasis supplied)") or \
                                               text.lower().endswith("…"+self.quote_stack[-1]+ " (emphasis supplied)") or text.lower().endswith("…"+self.quote_stack[-1]+ "(emphasis supplied)") or text.endswith("…"+self.quote_stack[-1]) or \

                                               text.lower().endswith("."+self.quote_stack[-1]+ " [emphasis supplied]") or text.lower().endswith(";"+self.quote_stack[-1]+ " [emphasis supplied]") or \
                                               text.lower().endswith("."+self.quote_stack[-1]+ "[emphasis supplied]") or text.lower().endswith(";"+self.quote_stack[-1]+ "[emphasis supplied]") or \
                                               text.lower().endswith("…"+self.quote_stack[-1]+ " [emphasis supplied]") or text.lower().endswith("…"+self.quote_stack[-1]+ "[emphasis supplied]") or text.endswith("…"+self.quote_stack[-1]) or \
                                               text.endswith(self.quote_stack[-1])
                                               ):
                        self.quote_stack.pop()
                        self.logger.debug(f"Detected closing quote on page {page.pg_num}. Popped from quote_stack.")
                        page.all_tbs[tb] = 'blockquote'

                    elif self.quote_stack and self.quote_stack[-1] == "'" and singleQuote_count%2!=0  and (text.lower().endswith('(emphasis supplied)') or
                                               text.lower().endswith('[emphasis supplied]') or text.endswith(self.quote_stack[-1] + ".") or text.endswith(self.quote_stack[-1] + ";") or \
                                               text.lower().endswith(self.quote_stack[-1] + "." + " (emphasis supplied)") or text.lower().endswith(self.quote_stack[-1] + ";" + " (emphasis supplied)") or\
                                               text.lower().endswith(self.quote_stack[-1] + "." + "(emphasis supplied)") or text.lower().endswith(self.quote_stack[-1] + ";" + "(emphasis supplied)") or \

                                               text.lower().endswith(self.quote_stack[-1] + "." + " [emphasis supplied]") or text.lower().endswith(self.quote_stack[-1] + ";" + " [emphasis supplied]") or\
                                               text.lower().endswith(self.quote_stack[-1] + "." + "[emphasis supplied]") or text.lower().endswith(self.quote_stack[-1] + ";" + "[emphasis supplied]")\

                                               or  text.endswith("."+self.quote_stack[-1]) or  text.endswith(";"+self.quote_stack[-1]) or \
                                               text.lower().endswith("."+self.quote_stack[-1]+ " (emphasis supplied)") or text.lower().endswith(";"+self.quote_stack[-1]+ " (emphasis supplied)") or \
                                               text.lower().endswith("."+self.quote_stack[-1]+ "(emphasis supplied)") or text.lower().endswith(";"+self.quote_stack[-1]+ "(emphasis supplied)") or \
                                               text.lower().endswith("…"+self.quote_stack[-1]+ " (emphasis supplied)") or text.lower().endswith("…"+self.quote_stack[-1]+ "(emphasis supplied)") or text.endswith("…"+self.quote_stack[-1]) or \

                                               text.lower().endswith("."+self.quote_stack[-1]+ " [emphasis supplied]") or text.lower().endswith(";"+self.quote_stack[-1]+ " [emphasis supplied]") or \
                                               text.lower().endswith("."+self.quote_stack[-1]+ "[emphasis supplied]") or text.lower().endswith(";"+self.quote_stack[-1]+ "[emphasis supplied]") or \
                                               text.lower().endswith("…"+self.quote_stack[-1]+ " [emphasis supplied]") or text.lower().endswith("…"+self.quote_stack[-1]+ "[emphasis supplied]") or text.endswith("…"+self.quote_stack[-1]) or \


                                               text.endswith(self.quote_stack[-1])
                                               ):
                        self.quote_stack.pop()
                        self.logger.debug(f"Detected closing quote on page {page.pg_num}. Popped from quote_stack.")
                        page.all_tbs[tb] = 'blockquote'

                    # Inside an open quote block
                    elif self.quote_stack:
                        page.all_tbs[tb] = 'blockquote'
                        self.logger.debug(f"Inside open quote block on page {page.pg_num}.The text [{text}] marked as amendment.")

                except Exception as e:
                    self.logger.error(f"Error while processing blockquote logic on page {page_num}: {e}")


    # --- func to detect blockquotes in judgments: a lead-in line ending in ':' / ':-'
    # (e.g. "...observed as under:-", "...reads as follows:", "...states that:") is what a
    # judgment uses to introduce a quoted excerpt. A trigger line only arms the *next* row
    # to open a quote - it never itself gets labelled, and a row that merely ends in ':'
    # without anything recognisable following (e.g. "CORAM:") never opens anything.
    #
    # Two independent signals decide when the excerpt is over, because not every PDF can
    # be trusted to balance its quotation marks (OCR text routinely drops one side of a
    # curly quote) and not every quoted excerpt uses Western quote punctuation at all:
    #   1. Quote-character balance (nesting, self-contained same-row quotes, runs with no
    #      quote marks in between) - the same style already used by check_for_blockquotes/
    #      check_for_amendment_acts. Used both to open (needs an actual quote mark) and,
    #      when present, to close.
    #   2. Clause-number tracking (ClauseTracker) - language/script-agnostic, since it keys
    #      off numerals rather than punctuation conventions. The excerpt is presumed to
    #      start once the *outer* document's own clause numbering (e.g. "3.") is interrupted
    #      by a marker that isn't simply its next sibling (a bare statute clause reproduced
    #      under a trigger, restarting its own "(1)(2)(3)..."), and to end the moment that
    #      outer sibling ("4.") reappears - whether or not the quote ever closes punctually.
    # Either signal closes the excerpt on its own; state persists across a page break since
    # excerpts routinely run past one.
    def compute_document_margin(self, pages):
        lefts = []
        rights = []
        page_lefts = []
        bin_width = 6.0
        for page in pages:
            bin_width = max(4.0, 0.01 * (getattr(page, 'pg_width', 0) or 600))
            own_lefts = []
            page_lefts.append((getattr(page, 'pg_num', None), own_lefts))
            for tb, label in page.all_tbs.items():
                if label is not None:
                    continue
                try:
                    text = tb.extract_text_from_tb().strip()
                except Exception:
                    continue
                if len(text) < 30:
                    continue
                lefts.append(tb.coords[0])
                own_lefts.append(tb.coords[0])
                rights.append(tb.coords[2])
        self.doc_margin = None
        self.doc_right = None
        self.body_margins = {}
        if len(lefts) < 8:
            return
        counts = {}
        for x in lefts:
            counts[int(x // bin_width)] = counts.get(int(x // bin_width), 0) + 1
        best = max(counts, key=lambda b: counts.get(b - 1, 0) + counts[b] + counts.get(b + 1, 0))
        near = sorted(x for x in lefts if best - 1 <= int(x // bin_width) <= best + 1)
        self.doc_margin = near[len(near) // 2]
        rights.sort()
        self.doc_right = rights[int(0.9 * (len(rights) - 1))]
        self.body_margins = self.recurring_page_margins(page_lefts, bin_width)

    def leftmost_supported_left(self, lefts, bin_width):
        if not lefts:
            return None
        counts = {}
        for x in lefts:
            counts[int(x // bin_width)] = counts.get(int(x // bin_width), 0) + 1
        need = max(2, 0.2 * len(lefts))
        supported = [b for b in counts
                     if counts.get(b - 1, 0) + counts[b] + counts.get(b + 1, 0) >= need]
        if not supported:
            return None
        best = min(supported)
        near = sorted(x for x in lefts if best - 1 <= int(x // bin_width) <= best + 1)
        return near[len(near) // 2]

    def recurring_page_margins(self, page_lefts, bin_width):
        min_pages = max(3, 0.1 * len(page_lefts))
        runs = []
        for pg_num, lefts in page_lefts:
            if len(lefts) < 4:
                continue
            margin = self.leftmost_supported_left(lefts, bin_width)
            if margin is None:
                continue
            if runs and abs(margin - runs[-1]["margin"]) <= bin_width:
                runs[-1]["margins"].append(margin)
                runs[-1]["pages"].append(pg_num)
            elif runs and margin > runs[-1]["margin"]:
                runs[-1]["pages"].append(pg_num)
            else:
                runs.append({"margin": margin, "margins": [margin], "pages": [pg_num]})
        margins = {}
        for run in runs:
            if len(run["margins"]) < min_pages:
                continue
            values = sorted(run["margins"])
            for pg_num in run["pages"]:
                margins[pg_num] = values[len(values) // 2]
        return margins

    BQ2_TRIGGER_RE = re.compile(r'(?:[:：]|;\s*[\-‐‑‒–—―−])[\s\-‐‑‒–—―−:：]*["“\'‘]?\s*$')
    BQ2_ITALIC_FONT_RE = re.compile(r'italic|oblique', re.IGNORECASE)
    BQ2_ITALIC_RATIO = 0.9
    BQ2_MAIN_CLAUSE_RE = re.compile(r'^\s*(\d{1,3}(?:\.\d{1,3}){0,3})([.):]?)(?=\s)')
    BQ2_LIST_MARKER_RE = re.compile(
        r'^\s*(?:\((?P<p>[ivxlcdm]{1,6}|[IVXLCDM]{1,6}|[^\W\d_]|\d{1,3})\)\s*(?=\S)'
        r'|(?P<c>[ivxlcdm]{1,6}|[IVXLCDM]{1,6}|[^\W\d_]|\d{1,3})\)\s*(?=\S)'
        r'|(?P<d>[ivxlcdm]{1,6}|[IVXLCDM]{1,6}|[^\W\d_]|\d{1,3})\.(?!\d)\s+(?=\S))'
    )
    BQ2_SCRIPT_LETTERS = {}
    BQ2_DECIMAL_MARKER_RE = re.compile(r'^\s*\(?(\d{1,3}(?:\.\d{1,3})+)\.?\)?\s+(?=\S)')
    BQ2_QUOTE_OPEN_RE = re.compile(r'^["“‘\'«„]')
    BQ2_TERMINAL_RE = re.compile(r'[.?!' + INDIC_SENTENCE_END_CHARS + r'][\)\'"”’\]]*\s*$')
    BQ2_SECTION_RE = re.compile(r'^\s*([IVXLCDM]+|[ivxlcdm]+)\.?\s+(?=\S)')
    BQ2_HEADING_MARKER_RE = re.compile(r'^\s*(?:\(?[^\W_]{1,6}[.)]\s*)+')

    @classmethod
    def parse_main_clause(cls, text):
        match = cls.BQ2_MAIN_CLAUSE_RE.match(text)
        if not match:
            return None, None
        return tuple(int(n) for n in match.group(1).split('.')), match.group(2)

    @staticmethod
    def quote_unclosed(text):
        curly_open = text.count('“') - text.count('”')
        return curly_open > 0 or text.count('"') % 2 == 1

    @staticmethod
    def ends_inside_sentence(text):
        text = text.rstrip()
        if not text:
            return False
        last = text[-1]
        category = unicodedata.category(last)
        return last == ',' or category in ('Ll', 'Lo') or category[0] == 'M'

    @classmethod
    def is_caps_heading(cls, text):
        body = cls.BQ2_HEADING_MARKER_RE.sub('', text, count=1)
        words = [w for w in re.findall(r'[^\W\d_]{2,}', body) if any(c.isupper() or c.islower() for c in w)]
        if len(words) < 2:
            return False
        letters = [c for w in words for c in w if c.isupper() or c.islower()]
        upper = sum(1 for c in letters if c.isupper())
        return len(letters) >= 6 and upper / len(letters) >= 0.85

    @classmethod
    def marker_italic(cls, textline, text):
        match = cls.BQ2_LIST_MARKER_RE.match(text) or cls.BQ2_DECIMAL_MARKER_RE.match(text)
        if not match:
            return False
        size = len(match.group(0).strip())
        chars = [c for c in textline.findall("text") if c.text and c.text.strip()][:size]
        return bool(chars) and all(
            cls.BQ2_ITALIC_FONT_RE.search(c.attrib.get("font", "")) or 'i' in c.attrib.get("fstyle", "")
            for c in chars
        )

    @classmethod
    def parse_list_marker(cls, text):
        decimal = cls.BQ2_DECIMAL_MARKER_RE.match(text)
        if decimal:
            parts = decimal.group(1).split('.')
            return {('num', 'dec:' + '.'.join(parts[:-1]), None, int(parts[-1]))}
        match = cls.BQ2_LIST_MARKER_RE.match(text)
        if not match:
            return None
        style = next(name for name in ('p', 'c', 'd') if match.group(name))
        token = match.group(style)
        if token.isdigit():
            zero = chr(ord(token[0]) - unicodedata.digit(token[0]))
            return {('num', style, None if zero == '0' else zero, int(token))}
        if not token.isascii():
            return cls.script_letter_readings(style, token)
        case = 'u' if token.isupper() else 'l'
        readings = set()
        roman = _roman_to_int(token)
        if roman and re.fullmatch(r'm{0,4}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})', token.lower()):
            readings.add(('roman', style, case, roman))
        if len(token) == 1:
            readings.add(('alpha', style, case, ord(token.lower()) - ord('a') + 1))
        return readings or None

    @classmethod
    def script_letters(cls, char):
        block = ord(char) & ~0x7F
        if block not in cls.BQ2_SCRIPT_LETTERS:
            letters = [chr(code) for code in range(block, block + 0x80)
                       if unicodedata.category(chr(code)) == 'Lo' and not unicodedata.decomposition(chr(code))]
            anchors = {}
            for index, letter in enumerate(letters):
                name = unicodedata.name(letter, '')
                for kind, suffix in (('vowel', ' LETTER A'), ('consonant', ' LETTER KA')):
                    if name.endswith(suffix) and kind not in anchors:
                        anchors[kind] = index
            cls.BQ2_SCRIPT_LETTERS[block] = (letters, anchors)
        return cls.BQ2_SCRIPT_LETTERS[block]

    @classmethod
    def script_letter_readings(cls, style, token):
        if len(token) != 1 or unicodedata.category(token) != 'Lo':
            return None
        letters, anchors = cls.script_letters(token)
        if token not in letters:
            return None
        block = ord(token) & ~0x7F
        rank = letters.index(token)
        for kind in ('consonant', 'vowel'):
            if kind in anchors and rank >= anchors[kind]:
                return {(kind, style, block, rank - anchors[kind] + 1)}
        return None

    @staticmethod
    def list_follows(level, readings):
        follows = set()
        for r in readings:
            steps = (1, 2) if r[0] in ('consonant', 'vowel') else (1,)
            if any((r[0], r[1], r[2], r[3] - step) in level for step in steps):
                follows.add(r)
        return follows

    def next_main_clauses(self):
        last = self.bq2_main_last
        candidates = {last + (1,)}
        for depth in range(len(last)):
            candidates.add(last[:depth] + (last[depth] + 1,))
        return candidates

    def main_clause_shift(self, row_x0, margin):
        if self.bq2_main_offset is None:
            return None
        shift = abs(row_x0 - margin - self.bq2_main_offset)
        if self.bq2_main_x0 is not None:
            shift = min(shift, abs(row_x0 - self.bq2_main_x0))
        return shift

    def is_next_main_clause(self, nums, row_x0, tol, margin, in_context):
        if nums is None:
            return False
        if self.bq2_main_last is None:
            return not in_context or row_x0 <= margin + tol
        shift = self.main_clause_shift(row_x0, margin)
        tightly_aligned = shift is not None and shift <= 3.0
        if not in_context and tightly_aligned:
            last = self.bq2_main_last
            if nums == (1,) or (nums > last and nums[0] <= last[0] + 1):
                return True
        if nums not in self.next_main_clauses():
            return False
        return shift is None or shift <= tol

    def end_bq2_mode(self):
        self.bq2_mode = None
        self.bq2_list_stack = []
        self.bq2_list_parent = None

    def suspend_bq2_list(self):
        if self.bq2_list_stack:
            self.bq2_suspended = {"stack": [dict(level) for level in self.bq2_list_stack],
                                  "parent": self.bq2_list_parent}

    def resume_bq2_list(self, readings, row_x0, indent_tol):
        suspended = self.bq2_suspended
        if not suspended or not readings:
            return False
        stack = suspended["stack"]
        for depth in range(len(stack) - 1, -1, -1):
            matched = self.list_follows(stack[depth]["readings"], readings)
            if matched and abs(row_x0 - stack[depth]["x0"]) <= indent_tol:
                self.bq2_mode = "list"
                self.bq2_list_parent = suspended["parent"]
                self.bq2_list_stack = stack[:depth] + [{"readings": matched, "x0": row_x0}]
                self.bq2_suspended = None
                return True
        return False

    @staticmethod
    def int_to_roman(value):
        numerals = ((1000, 'm'), (900, 'cm'), (500, 'd'), (400, 'cd'), (100, 'c'), (90, 'xc'),
                    (50, 'l'), (40, 'xl'), (10, 'x'), (9, 'ix'), (5, 'v'), (4, 'iv'), (1, 'i'))
        out = ''
        for number, numeral in numerals:
            while value >= number:
                out += numeral
                value -= number
        return out

    def marker_token(self, reading):
        kind, style, case, value = reading
        if style.startswith('dec:'):
            return style[4:] + '.' + str(value)
        if kind == 'num':
            token = str(value)
            if case:
                token = ''.join(chr(ord(case) + int(d)) for d in token)
            case = None
        elif kind in ('consonant', 'vowel'):
            letters, anchors = self.script_letters(chr(case))
            index = value - 1 + anchors.get(kind, 0)
            token = letters[index] if 0 <= index < len(letters) else ''
            case = None
        elif kind == 'roman':
            token = self.int_to_roman(value)
        else:
            token = chr(ord('a') + value - 1)
        if case == 'u':
            token = token.upper()
        if style == 'p':
            return '(' + token + ')'
        if style == 'c':
            return token + ')'
        return token + '.'

    def find_inline_markers(self, text, level):
        found = []
        readings = level["readings"]
        pos = 1
        while True:
            best = None
            for reading in readings:
                nxt = (reading[0], reading[1], reading[2], reading[3] + 1)
                token = self.marker_token(nxt)
                match = None
                for found_match in re.finditer(r'(?<=[\s\[])' + re.escape(token) + r'\s+(?=\S)', text[pos:]):
                    following = text[pos + found_match.end()]
                    if following in '"“\'‘(' or following.isupper() or (following.isalpha() and not following.islower()):
                        match = re.compile(re.escape(token)).match(text, pos + found_match.start())
                        break
                if match and (best is None or match.start() < best[0]):
                    best = (match.start(), match.end(), token, nxt)
            if best is None:
                return found
            found.append(best[2])
            readings = {best[3]}
            level["readings"] = readings
            pos = best[1]

    @staticmethod
    def textline_text(textline):
        return " ".join("".join(t.text or "" for t in textline.findall("text")).split())

    @classmethod
    def textlines_italic(cls, textlines):
        total = 0
        italic = 0
        for textline in textlines:
            for char in textline.findall("text"):
                if not char.text or not char.text.strip():
                    continue
                total += 1
                if cls.BQ2_ITALIC_FONT_RE.search(char.attrib.get("font", "")) or 'i' in char.attrib.get("fstyle", ""):
                    italic += 1
        return total > 0 and italic / total >= cls.BQ2_ITALIC_RATIO

    def collect_bq2_rows(self, page):
        pg_num = getattr(page, 'pg_num', '?')
        lines = []
        tb_lines = {}
        for tb, label in page.all_tbs.items():
            if label is not None:
                continue
            for textline in tb.tbox.findall(".//textline"):
                try:
                    x0, y0, x1, y1 = (float(v) for v in textline.attrib["bbox"].split(","))
                except Exception:
                    continue
                text = self.textline_text(textline)
                if not text:
                    continue
                line = {"tb": tb, "el": textline, "text": text, "x0": x0, "x1": x1,
                        "y_top": y1, "height": (y1 - y0) or 8.0}
                lines.append(line)
                tb_lines.setdefault(tb, []).append(line)
        lines.sort(key=lambda l: (-l["y_top"], l["x0"]))
        rows = []
        for line in lines:
            tolerance = 0.4 * line["height"]
            if rows and abs(rows[-1]["y_top"] - line["y_top"]) <= tolerance:
                rows[-1]["lines"].append(line)
                continue
            rows.append({"y_top": line["y_top"], "lines": [line]})
        for row in rows:
            row["lines"].sort(key=lambda l: l["x0"])
            row["text"] = " ".join(l["text"] for l in row["lines"])
            row["x0"] = row["lines"][0]["x0"]
            row["x1"] = max(l["x1"] for l in row["lines"])
            row["height"] = max(l["height"] for l in row["lines"])
            row["category"] = "p"
            row["page"] = pg_num
            row["start"] = False
            row["inline"] = []
        return rows, tb_lines

    def plan_main_clauses(self, pages):
        self.bq2_main_plan = set()
        candidates = []
        page_rows = []
        for page in pages:
            pg_num = getattr(page, 'pg_num', '?')
            rows, tb_lines = self.collect_bq2_rows(page)
            if not rows:
                continue
            margin = self.body_margins.get(pg_num, self.doc_margin)
            if margin is None:
                margin = min(r["x0"] for r in rows)
            page_rows.append((pg_num, margin, rows))
            for row_idx, row in enumerate(rows):
                nums, fmt = self.parse_main_clause(row["text"])
                if nums is None or (len(nums) == 1 and not fmt):
                    continue
                if self.BQ2_QUOTE_OPEN_RE.match(row["text"]):
                    continue
                after_trigger = row_idx > 0 and bool(self.BQ2_TRIGGER_RE.search(rows[row_idx - 1]["text"]))
                candidates.append({"key": (pg_num, round(row["y_top"], 1)), "nums": nums,
                                   "after_trigger": after_trigger,
                                   "offset": row["x0"] - margin,
                                   "tol": max(6.0, 0.75 * row["height"])})
        self.bq2_pitch = self.document_pitches(page_rows)
        if not candidates:
            return
        main = max(candidates, key=lambda c: sum(
            1 for o in candidates if abs(o["offset"] - c["offset"]) <= c["tol"]))
        aligned = [c for c in candidates if abs(c["offset"] - main["offset"]) <= c["tol"]]
        last = None
        mains = []
        for idx, cand in enumerate(aligned):
            nums = cand["nums"]
            accept = last is None or nums in self.main_successors(last)
            if not accept and idx + 1 < len(aligned) and not cand["after_trigger"]:
                following = aligned[idx + 1]["nums"]
                accept = following in self.main_successors(nums) \
                    and following not in self.main_successors(last)
            if accept:
                self.bq2_main_plan.add(cand["key"])
                mains.append((cand["key"], nums))
                last = nums
            else:
                following = aligned[idx + 1]["nums"] if idx + 1 < len(aligned) else None
                self.logger.debug(f"blockquote_v2 plan skipped {nums} on page {cand['key'][0]} "
                                  f"after {last}, next {following}")
        right_edge = self.doc_right if self.doc_right is not None \
            else max(r["x1"] for _, _, rows in page_rows for r in rows)
        self.plan_section_headings(page_rows, right_edge, mains)

    @staticmethod
    def pitch_class(row, margin):
        return "margin" if abs(row["x0"] - margin) <= max(6.0, 0.75 * row["height"]) else "indent"

    def document_pitches(self, page_rows):
        gaps = {"margin": [], "indent": []}
        for pg_num, margin, rows in page_rows:
            for upper, lower in zip(rows, rows[1:]):
                gap = upper["y_top"] - lower["y_top"]
                if gap <= 0 or abs(upper["x0"] - lower["x0"]) > max(6.0, 0.75 * lower["height"]):
                    continue
                if self.pitch_class(upper, margin) != self.pitch_class(lower, margin):
                    continue
                gaps[self.pitch_class(lower, margin)].append(gap)
        pitches = {}
        for name, values in gaps.items():
            if values:
                values.sort()
                pitches[name] = values[len(values) // 2]
        return pitches

    def plan_section_headings(self, page_rows, right_edge, mains):
        self.bq2_section_plan = set()
        candidates = []
        order = {}
        for pg_idx, (pg_num, margin, rows) in enumerate(page_rows):
            for row_idx, row in enumerate(rows):
                order[(pg_num, round(row["y_top"], 1))] = (pg_idx, row_idx)
        mains = sorted((order[key], nums) for key, nums in mains if key in order)

        def restarts_after(key):
            pos = order[key]
            before = [nums for at, nums in mains if at < pos]
            after = [nums for at, nums in mains if at > pos]
            return bool(after) and (not before or after[0] <= before[-1])

        for pg_num, margin, rows in page_rows:
            for row in rows:
                match = self.BQ2_SECTION_RE.match(row["text"])
                if not match or self.BQ2_TERMINAL_RE.search(row["text"]):
                    continue
                value = _roman_to_int(match.group(1))
                if not value or row["x1"] >= right_edge - 3.0 * row["height"]:
                    continue
                candidates.append({"key": (pg_num, round(row["y_top"], 1)), "value": value,
                                   "case": match.group(1).isupper(), "offset": row["x0"] - margin,
                                   "tol": max(6.0, 0.75 * row["height"])})
        last = None
        for idx, cand in enumerate(candidates):
            same = [c for c in candidates[idx + 1:] if c["case"] == cand["case"]
                    and abs(c["offset"] - cand["offset"]) <= cand["tol"]]
            follows = last is not None and cand["case"] == last["case"] \
                and cand["value"] == last["value"] + 1 and abs(cand["offset"] - last["offset"]) <= cand["tol"]
            leads = bool(same) and same[0]["value"] == cand["value"] + 1
            if (follows or leads) and restarts_after(cand["key"]):
                self.bq2_section_plan.add(cand["key"])
                last = cand

    @staticmethod
    def main_successors(last):
        candidates = {last + (1,)}
        for depth in range(len(last)):
            candidates.add(last[:depth] + (last[depth] + 1,))
        return candidates

    def judgment_blockquote_v2(self, page):
        pg_num = getattr(page, 'pg_num', '?')
        rows, tb_lines = self.collect_bq2_rows(page)
        if not rows:
            return

        block_spans = [
            (tb.coords[1], tb.coords[3]) for tb, label in page.all_tbs.items()
            if label == "figure" or (
                isinstance(label, tuple) and label[0] in ("table", "borderless_table")
            )
        ]

        margin = self.body_margins.get(getattr(page, 'pg_num', None), self.doc_margin)
        if margin is None:
            margin = min(r["x0"] for r in rows)
        right_edge = self.doc_right if self.doc_right is not None else max(r["x1"] for r in rows)

        def spans_between(row_idx):
            if not block_spans:
                return False
            row_top = rows[row_idx]["y_top"]
            if row_idx == 0:
                return any(y0 >= row_top - 1.0 for y0, y1 in block_spans)
            previous_bottom = min(l["y_top"] - l["height"] for l in rows[row_idx - 1]["lines"])
            return any(y1 <= previous_bottom + 1.0 and y0 >= row_top - 1.0 for y0, y1 in block_spans)

        def set_list(row, start):
            row["category"] = f"list:{max(len(self.bq2_list_stack) - 1, 0)}"
            row["start"] = start
            for depth, level in enumerate(self.bq2_list_stack):
                fresh = start and depth == len(self.bq2_list_stack) - 1
                level["text"] = row["text"] if fresh else level.get("text", "") + " " + row["text"]

        gaps = sorted(
            rows[i - 1]["y_top"] - rows[i]["y_top"] for i in range(1, len(rows))
            if 0 < rows[i - 1]["y_top"] - rows[i]["y_top"] <= 3.0 * rows[i]["height"]
        )
        pitch = gaps[len(gaps) // 2] if gaps else 2.0 * max(r["height"] for r in rows)

        for row_idx, row in enumerate(rows):
            text = row["text"]
            row_x0 = row["x0"]
            unit_h = row["height"]
            tol = max(12.0, 1.5 * unit_h)
            indent_tol = max(6.0, 0.75 * unit_h)
            is_indented = row_x0 > margin + indent_tol
            prev_text = self.bq2_prev_text
            prev_trigger = prev_text is not None and bool(self.BQ2_TRIGGER_RE.search(prev_text))
            prev_open = prev_text is not None and not self.BQ2_TERMINAL_RE.search(prev_text) \
                and not prev_trigger
            prev_wrapped = prev_open and self.bq2_prev_x1 is not None \
                and self.bq2_prev_x1 >= right_edge - 3.0 * unit_h
            prev_midsentence = prev_wrapped and self.ends_inside_sentence(prev_text)
            prev_full = self.bq2_prev_x1 is not None and self.bq2_prev_x1 >= right_edge - 3.0 * unit_h
            row_pitch = self.bq2_pitch.get(self.pitch_class(row, margin), pitch)
            if self.bq2_prev_page == pg_num and self.bq2_prev_top is not None:
                tight = self.bq2_prev_top - row["y_top"] <= 1.25 * max(pitch, row_pitch)
            else:
                tight = row_idx == 0
            edges = [margin] + [level["x0"] for level in self.bq2_list_stack]
            flows_on = (prev_full or self.bq2_prev_start) and tight \
                and any(abs(row_x0 - edge) <= indent_tol for edge in edges)
            pending = self.bq2_pending_trigger
            if self.bq2_mode is not None and is_separator_line(text):
                if self.bq2_mode == "list":
                    set_list(row, False)
                else:
                    row["category"] = "blockquote"
                continue
            self.bq2_pending_trigger = bool(self.BQ2_TRIGGER_RE.search(text))

            if spans_between(row_idx):
                if self.bq2_mode == "italic":
                    self.end_bq2_mode()
                pending = False

            is_italic = self.textlines_italic([l["el"] for l in row["lines"]])
            planned = (pg_num, round(row["y_top"], 1)) in self.bq2_main_plan
            if (pg_num, round(row["y_top"], 1)) in self.bq2_section_plan:
                if self.bq2_mode is not None:
                    self.logger.debug(f"blockquote_v2 {self.bq2_mode} closed on page {pg_num} at section: {text[:60]}")
                self.end_bq2_mode()
                self.bq2_suspended = None
                self.remember_bq2_row(row)
                continue
            if self.bq2_mode == "italic":
                if is_italic and not planned:
                    row["category"] = "blockquote"
                    self.remember_bq2_row(row)
                    continue
                self.logger.debug(f"blockquote_v2 italic closed on page {pg_num}: {text[:60]}")
                self.end_bq2_mode()

            nums, main_fmt = self.parse_main_clause(text)
            if nums is not None and len(nums) == 1 and not main_fmt:
                nums = None
            readings = self.parse_list_marker(text)
            in_context = pending or self.bq2_mode is not None
            first_main = nums == (1,) and main_fmt and row_x0 <= margin + indent_tol
            if self.bq2_main_last is None and in_context and not first_main:
                is_main = False
            elif prev_midsentence:
                is_main = False
            else:
                is_main = self.is_next_main_clause(nums, row_x0, tol, margin, in_context)
            list_continues = self.bq2_mode == "list" and bool(readings) and any(
                self.list_follows(level["readings"], readings) for level in self.bq2_list_stack)
            if not is_main and not prev_midsentence and not pending and not list_continues and planned:
                is_main = True
            if nums is not None and main_fmt and not is_main:
                self.logger.debug(f"blockquote_v2 number {nums} on page {pg_num} at x0={row_x0:.1f} "
                                  f"not taken as main clause after {self.bq2_main_last}")

            if is_main and self.bq2_mode == "list" and readings:
                top_x0 = self.bq2_list_stack[-1]["x0"] if self.bq2_list_stack else None
                list_match = any(self.list_follows(level["readings"], readings) for level in self.bq2_list_stack)
                list_aligned = top_x0 is not None and abs(row_x0 - top_x0) <= indent_tol
                shift = self.main_clause_shift(row_x0, margin)
                main_aligned = shift is None or shift <= indent_tol
                if list_match and (list_aligned and not main_aligned or main_fmt != self.bq2_main_fmt):
                    is_main = False

            if is_main:
                if self.bq2_mode is not None:
                    self.logger.debug(f"blockquote_v2 {self.bq2_mode} closed on page {pg_num} at main clause {nums}")
                self.end_bq2_mode()
                self.bq2_suspended = None
                self.logger.debug(f"blockquote_v2 main clause {nums} on page {pg_num} at x0={row_x0:.1f}")
                self.bq2_main_last = nums
                self.bq2_main_offset = row_x0 - margin
                self.bq2_main_x0 = row_x0
                self.bq2_main_fmt = main_fmt
                self.remember_bq2_row(row)
                continue

            if self.bq2_mode in (None, "list") and self.is_caps_heading(text) and not prev_midsentence:
                if self.bq2_mode is not None:
                    self.logger.debug(f"blockquote_v2 list closed on page {pg_num} at heading: {text[:60]}")
                self.end_bq2_mode()
                self.bq2_suspended = None
                self.remember_bq2_row(row)
                continue

            if self.bq2_mode == "list":
                handled = False
                start = False
                if readings:
                    for depth in range(len(self.bq2_list_stack) - 1, -1, -1):
                        level = self.bq2_list_stack[depth]
                        matched = self.list_follows(level["readings"], readings)
                        if not matched:
                            continue
                        misaligned = abs(row_x0 - level["x0"]) > indent_tol
                        if misaligned and (prev_midsentence or self.quote_unclosed(level.get("text", ""))):
                            continue
                        del self.bq2_list_stack[depth + 1:]
                        self.bq2_list_stack[depth] = {"readings": matched, "x0": row_x0}
                        handled = start = True
                        break
                    if not handled and not prev_midsentence \
                            and (prev_trigger or not self.quote_unclosed(self.bq2_list_stack[-1].get("text", ""))):
                        kinds = {(r[0], r[1], r[2]) for level in self.bq2_list_stack for r in level["readings"]}
                        starts = {r for r in readings if r[3] == 1 and (r[0], r[1], r[2]) not in kinds}
                        if starts:
                            self.bq2_list_stack.append({"readings": starts, "x0": row_x0})
                            handled = start = True
                if not handled:
                    first_x0 = self.bq2_list_stack[0]["x0"]
                    indented = row_x0 > first_x0 + 3.0
                    handled = prev_open or prev_trigger or flows_on \
                        or (indented and (tight or self.quote_unclosed(self.bq2_list_stack[-1].get("text", ""))))
                if handled:
                    set_list(row, start)
                    row["inline"] = self.find_inline_markers(text, self.bq2_list_stack[-1])
                    self.remember_bq2_row(row)
                    continue
                self.logger.debug(f"blockquote_v2 list closed on page {pg_num} (x0={row_x0:.1f} margin={margin:.1f} "
                                  f"pitch={pitch:.1f} tight={tight} prev_full={prev_full}): {text[:60]}")
                resume_quote = self.bq2_list_parent == "quote"
                self.suspend_bq2_list()
                self.end_bq2_mode()
                if resume_quote:
                    self.bq2_mode = "quote"

            if self.bq2_mode == "quote":
                starts = {r for r in readings if r[3] == 1} if readings else set()
                if pending and starts:
                    self.bq2_mode = "list"
                    self.bq2_list_parent = "quote"
                    self.bq2_list_stack = [{"readings": starts, "x0": row_x0}]
                    set_list(row, True)
                    row["inline"] = self.find_inline_markers(text, self.bq2_list_stack[-1])
                    self.remember_bq2_row(row)
                    continue
                if is_indented or readings or nums is not None or prev_open:
                    row["category"] = "blockquote"
                    self.remember_bq2_row(row)
                    continue
                self.logger.debug(f"blockquote_v2 marker quote closed on page {pg_num}: {text[:60]}")
                self.end_bq2_mode()

            if self.bq2_mode is None and not prev_midsentence and self.resume_bq2_list(readings, row_x0, indent_tol):
                set_list(row, True)
                row["inline"] = self.find_inline_markers(text, self.bq2_list_stack[-1])
                self.logger.debug(f"blockquote_v2 list resumed on page {pg_num}: {text[:60]}")
                self.remember_bq2_row(row)
                continue

            if pending:
                starts = {r for r in readings if r[3] == 1} if readings else set()
                if starts and is_italic and not self.marker_italic(row["lines"][0]["el"], text):
                    is_italic = False
                if is_italic:
                    self.bq2_mode = "italic"
                    row["category"] = "blockquote"
                    self.logger.debug(f"blockquote_v2 italic opened on page {pg_num}: {text[:60]}")
                elif starts:
                    self.bq2_mode = "list"
                    self.bq2_suspended = None
                    self.bq2_list_stack = [{"readings": starts, "x0": row_x0}]
                    set_list(row, True)
                    row["inline"] = self.find_inline_markers(text, self.bq2_list_stack[-1])
                    self.logger.debug(f"blockquote_v2 list opened on page {pg_num}: {text[:60]}")
                elif readings or nums is not None:
                    self.bq2_mode = "quote"
                    row["category"] = "blockquote"
                    self.logger.debug(f"blockquote_v2 marker quote opened on page {pg_num}: {text[:60]}")
                elif is_indented and self.BQ2_QUOTE_OPEN_RE.match(text):
                    self.bq2_mode = "quote"
                    row["category"] = "blockquote"
                    self.logger.debug(f"blockquote_v2 quoted passage opened on page {pg_num}: {text[:60]}")
            self.remember_bq2_row(row)

        for row in rows:
            for idx, line in enumerate(row["lines"]):
                line["el"].set("bq2", row["category"])
                if row["start"] and idx == 0:
                    line["el"].set("bq2_start", "1")
                if row["inline"] and idx == len(row["lines"]) - 1:
                    line["el"].set("bq2_inline", "|".join(row["inline"]))
        for tb, own in tb_lines.items():
            categories = [l["el"].get("bq2") for l in own]
            marked = [c for c in categories if c and c != "p"]
            if marked:
                page.all_tbs[tb] = marked[0]

    def remember_bq2_row(self, row):
        self.bq2_prev_text = row["text"]
        self.bq2_prev_x1 = row["x1"]
        self.bq2_prev_top = row["y_top"]
        self.bq2_prev_page = row["page"]
        self.bq2_prev_start = row["start"]

    def check_for_blockquotes_judgments(self, page, judgments_mode=False):
        trigger_re = re.compile(r'[:：][\s\-‐‑‒–—―−:：]*$')
        is_scanned = bool(getattr(page, 'scanned_copy', False))
        scan_factor = 1.5 if is_scanned else 1.0
        row_y_tolerance = 3.0
        annotation_re = re.compile(
            r'\s*[\(\[](emphasis (supplied|added|mine)|underlined?|sic|'
            + indic_annotation_alternation() + r')[\)\]]\.?\s*$',
            re.IGNORECASE,
        )

        def normalize(text):
            if not text:
                return ""
            text = unicodedata.normalize("NFKC", text)
            for invisible in ('\xad', '​', '‌', '‍', '⁠', '﻿'):
                text = text.replace(invisible, '')
            text = text.replace('\u201C', '"').replace('\u201D', '"').replace('\u201E', '"').replace('\u201F', '"')
            text = text.replace('\u00AB', '"').replace('\u00BB', '"').replace('\uFF02', '"')
            text = text.replace('\u2018', "'").replace('\u2019', "'").replace('\u201A', "'").replace('\u201B', "'")
            text = text.replace('\u2039', "'").replace('\u203A', "'").replace('\uFF07', "'")
            text = re.sub(r'\s+', ' ', text).strip()
            if is_scanned:
                text = re.sub(r"^(?:''|,,)\s*", '"', text)
                text = re.sub(r"\s*(?:''|,,)$", '"', text)
            return text

        def is_closing(text, quote_char):
            text = annotation_re.sub('', text)
            return (
                text.endswith(quote_char + '.') or text.endswith(quote_char + ';') or
                text.endswith('.' + quote_char) or text.endswith(';' + quote_char) or
                text.endswith('…' + quote_char) or text.endswith(quote_char)
            )

        def line_height_of(tb, fallback):
            heights = []
            try:
                for textline in tb.tbox.findall(".//textline"):
                    coords = [float(v) for v in textline.attrib["bbox"].split(",")]
                    if coords[3] - coords[1] > 0:
                        heights.append(coords[3] - coords[1])
            except Exception:
                return fallback
            if not heights:
                return fallback
            heights.sort()
            return heights[len(heights) // 2]

        boxes = []
        for tb, label in page.all_tbs.items():
            if label is not None:
                continue
            try:
                text = normalize(tb.extract_text_from_tb())
            except Exception as e:
                self.logger.warning(f"Failed to extract text from textbox on page {getattr(page, 'pg_num', '?')}: {e}")
                continue
            if not text:
                continue
            x0, y0, x1, y1 = tb.coords
            boxes.append({
                "tb": tb, "text": text, "x0": x0, "x1": tb.coords[2], "y_top": y1,
                "height": line_height_of(tb, y1 - y0),
            })

        if not boxes:
            return

        def row_style(row):
            row_boxes = row["boxes"]
            if not row_boxes:
                return False, False
            italic = all(b["tb"].textFont_is_italic() for b in row_boxes)
            styled = all(b["tb"].textFont_is_bold() or b["tb"].textFont_is_italic() for b in row_boxes)
            return italic, styled

        block_spans = [
            (tb.coords[1], tb.coords[3]) for tb, label in page.all_tbs.items()
            if label == "figure" or (
                isinstance(label, tuple) and label[0] in ("table", "borderless_table")
            )
        ]

        boxes.sort(key=lambda b: (-b["y_top"], b["x0"]))

        # --- group textboxes that sit on the same visual line into a single row, so a
        # line that pdfminer split into several boxes is still checked as one unit ---
        rows = []
        used = set()
        for i in range(len(boxes)):
            if i in used:
                continue
            group = [boxes[i]]
            used.add(i)
            for j in range(i + 1, len(boxes)):
                if j in used:
                    continue
                if abs(boxes[i]["y_top"] - boxes[j]["y_top"]) <= row_y_tolerance:
                    group.append(boxes[j])
                    used.add(j)
            group.sort(key=lambda b: b["x0"])
            rows.append({
                "boxes": group,
                "text": normalize(" ".join(g["text"] for g in group)),
            })

        rows.sort(key=lambda r: -max(b["y_top"] for b in r["boxes"]))

        body_rows = [r for r in rows if len(r["text"]) >= 30 and r["boxes"]]
        page_margin = None
        bin_width = None
        if len(body_rows) >= 4:
            heights = sorted((r["boxes"][0]["height"] or 0) for r in body_rows)
            bin_width = max(heights[len(heights) // 2], 4.0)
            bins = {}
            for r in body_rows:
                bins.setdefault(int(r["boxes"][0]["x0"] // bin_width), []).append(r["boxes"][0]["x0"])
            best = max(bins.values(), key=len)
            if len(best) >= 0.3 * len(body_rows):
                page_margin = min(best)
        page_right = None
        if len(body_rows) >= 4:
            rights = sorted(max(b["x1"] for b in r["boxes"]) for r in body_rows)
            page_right = rights[int(0.9 * (len(rights) - 1))]
        if self.doc_margin is not None:
            run_margin = self.body_margins.get(getattr(page, 'pg_num', None))
            doc_margin_on_page = bin_width is None or any(
                abs(r["boxes"][0]["x0"] - self.doc_margin) <= bin_width for r in body_rows)
            if run_margin is not None and not doc_margin_on_page:
                page_margin = run_margin
            else:
                page_margin = self.doc_margin
        if self.doc_right is not None:
            page_right = self.doc_right

        for row_idx, row in enumerate(rows):
            text = row["text"]
            if not text:
                continue

            try:
                double_count = text.count('"')
                single_count = text.count("'")
                marker = ClauseTracker.parse_marker(text)
                row_x0 = row["boxes"][0]["x0"]
                row_height = row["boxes"][0]["height"]
                text_x0 = row_x0
                if len(row["boxes"]) > 1 and SUBITEM_MARKER_RE.match(row["boxes"][0]["text"].strip()):
                    text_x0 = row["boxes"][1]["x0"]
                open_x0 = text_x0
                if text_x0 == row_x0 and SUBITEM_MARKER_RE.match(text.split(' ')[0]):
                    hanging_x0 = self.get_hanging_text_x0(row["boxes"][0]["tb"], row_height)
                    if hanging_x0 is not None:
                        open_x0 = hanging_x0
                is_first_row = not self.bq_seen_row
                self.bq_seen_row = True

                row_is_italic, row_is_styled = row_style(row)
                has_boxes = bool(row["boxes"])
                row_is_bold = has_boxes and all(b["tb"].textFont_is_bold() for b in row["boxes"])
                row_is_bold_or_italic = has_boxes and all(
                    b["tb"].textFont_is_bold() or b["tb"].textFont_is_italic() for b in row["boxes"])
                unit_h = row_height or 8.0
                return_ref = page_margin if page_margin is not None else self.bq_outer_x0
                inside_quote_geometry = bool(
                    self.bq_active and row_x0 is not None and return_ref is not None
                    and row_x0 > return_ref + max(6.0, 0.75 * unit_h) * scan_factor
                )
                row_is_section_heading = (
                    not self.bq_quote_stack
                    and not inside_quote_geometry
                    and (
                        (row_is_bold_or_italic and bool(SECTION_HEADING_ENUM_RE.match(text)))
                        or (row_is_bold and not LIST_ITEM_RE.match(text)
                            and (text.rstrip().endswith(':')
                                 or not re.search(r'[.?!' + INDIC_SENTENCE_END_CHARS + r']["\'”’)\]]*$', text.rstrip())))
                    )
                )
                if self.bq_active and row_is_section_heading:
                    self.bq_active = False
                    self.bq_quote_stack = []
                    self.bq_return_marker = None
                    self.bq_indent_x0 = None
                    self.bq_active_run_length = 0
                    self.bq_has_evidence = False
                    self.logger.debug(f"Page {page.pg_num}: blockquote closed before section heading '{text}'")

                if self.bq_active:
                    self.bq_active_run_length += 1

                    # A trigger line's opening can still be wrong - a case-heading or
                    # bench-composition line ending in ':' with no font signal
                    # distinguishing it (font *names* vary too much across the many
                    # court/tribunal PDF generators this runs on to be a reliable
                    # heading test on their own). Rather than guess harder up front,
                    # track whether the excerpt has produced any actual evidence of
                    # being a real quote - a quote character, a parseable internal
                    # marker (even one that doesn't match the outer document's own
                    # numbering, since a reproduced schedule's "1./2./3." still counts
                    # as structure), or a left margin clearly offset from the outer
                    # narrative. A run this long with *zero* such evidence is far more
                    # likely an unrecognized false trigger silently consuming ordinary
                    # narrative than a genuine, deliberately unmarked, unindented,
                    # quote-mark-free excerpt - so it gets rolled back. The much larger
                    # absolute ceiling is a last-resort backstop only: nothing here is
                    # a substitute for real close-detection, it just guarantees a
                    # mistaken open can never consume the rest of the document.
                    if self.bq_quote_stack or marker is not None or LIST_ITEM_RE.match(text):
                        self.bq_has_evidence = True
                    elif not self.bq_has_evidence and self.bq_indent_x0 is not None \
                            and self.bq_outer_x0 is not None:
                        unit = row_height or 8.0
                        if abs(self.bq_indent_x0 - self.bq_outer_x0) > max(10.0, 1.2 * unit):
                            self.bq_has_evidence = True

                    # Three independent ways to notice the excerpt has ended - whichever
                    # fires first wins. Quote-character balance and clause-marker
                    # matching aren't enough on their own: not every PDF balances its
                    # quotation marks (OCR routinely drops one side of a curly quote,
                    # and non-English/script-mixed documents may not use a Western
                    # quote convention at all), and a reproduced excerpt that restarts
                    # its own numbering (e.g. a schedule's internal "1./2./3.") never
                    # matches the outer document's expected next clause, so marker
                    # matching alone can leave a genuinely-ended excerpt open
                    # indefinitely. Indentation return is the structural fallback for
                    # exactly that case: quoted material is conventionally set off by a
                    # left-margin indent in the source PDF regardless of language, so a
                    # row whose margin matches where the outer narrative sat right
                    # before the excerpt opened - and clearly not the excerpt's own
                    # indent - is on its own good evidence of having returned to normal
                    # flow, with no reliance on recognizing its marker/punctuation at all.
                    quote_closed = False
                    if self.bq_quote_stack:
                        quote_char = self.bq_quote_stack[-1]
                        count = double_count if quote_char == '"' else single_count
                        closing = count % 2 != 0 and is_closing(text, quote_char)
                        # a row opening with the same quote character (without also
                        # closing within itself) is a fresh nested quote, not a
                        # continuation - e.g. an excerpt that itself quotes a statute
                        # section. Tracked as another stack level so that inner
                        # quote's own close isn't mistaken for the outer excerpt's.
                        if not closing and text.startswith(quote_char) and count % 2 != 0:
                            self.bq_quote_stack.append(quote_char)
                            self.logger.debug(f"Page {page.pg_num}: nested blockquote opened on row '{text}'")
                        elif closing:
                            self.bq_quote_stack.pop()
                            self.logger.debug(f"Page {page.pg_num}: blockquote nested/outer quote closed on row '{text}'")
                            if not self.bq_quote_stack:
                                quote_closed = True

                    # A quote-close is trusted as-is - the row carrying the closing
                    # mark is still the excerpt's own last line, so it gets marked
                    # before deactivating. A marker-close or indent-return is
                    # different in kind: it fires on the row *after* the excerpt, the
                    # outer document's own next clause, which was never part of the
                    # quote - so it must NOT be marked, and processing falls through
                    # to treat it as ordinary content (including re-checking it as a
                    # fresh trigger).
                    marker_closed = (not quote_closed) and ClauseTracker.matches_any_with_indent(
                        marker, self.bq_return_markers, row_x0, self.bq_indent_x0, row_height
                    )
                    indent_returned = False
                    if not quote_closed and not marker_closed and \
                            return_ref is not None and row_x0 is not None:
                        unit = row_height or 8.0
                        tight = max(4.0, 0.4 * unit) * scan_factor
                        indent_distinct = self.bq_indent_x0 is None or \
                            abs(self.bq_indent_x0 - return_ref) > tight
                        at_quote_indent = bool(
                            self.bq_indent_x0 is not None
                            and abs(text_x0 - self.bq_indent_x0) <= tight
                            and abs(text_x0 - return_ref) > tight
                        )
                        indent_returned = (indent_distinct and text_x0 <= return_ref + tight
                                           and not at_quote_indent
                                           and not (self.bq_all_styled and row_is_styled))
                        # A margin match can be a false alarm rather than a genuine
                        # return to outer flow: a hanging-indent wrapped continuation
                        # line (a citation's overflow text, a list item's second line)
                        # can transiently sit close to the outer margin too. Peeking at
                        # the next row disambiguates - if it snaps straight back to the
                        # excerpt's own indent (e.g. the list's next lettered item),
                        # this row was the anomaly, not a real close.
                        if indent_returned and judgments_mode and self.bq_indent_x0 is not None \
                                and self.bq_indent_x0 < return_ref - tight and row_idx > 0 \
                                and marker is None and not LIST_ITEM_RE.match(text) \
                                and not re.search(r'[' + SENTENCE_END_CHAR_CLASS + r'][)\'"”’\]]*$',
                                                  rows[row_idx - 1]["text"].strip()):
                            indent_returned = False
                        if indent_returned and self.bq_indent_x0 is not None and row_idx + 1 < len(rows):
                            next_boxes = rows[row_idx + 1]["boxes"]
                            if next_boxes:
                                next_x0 = next_boxes[0]["x0"]
                                next_delta_indent = abs(next_x0 - self.bq_indent_x0)
                                next_delta_outer = abs(next_x0 - return_ref)
                                if next_delta_indent <= tight and next_delta_indent < next_delta_outer:
                                    indent_returned = False

                    safety_closed = False
                    if not quote_closed and not marker_closed and not indent_returned:
                        if not self.bq_has_evidence and self.bq_active_run_length > 15:
                            safety_closed = True
                        elif self.bq_active_run_length > 300:
                            safety_closed = True

                    style_closed = (
                        self.bq_style_mode and not self.bq_quote_stack
                        and not quote_closed and not marker_closed and not indent_returned
                        and not row_is_styled and not annotation_re.match(text)
                    )

                    if marker_closed or indent_returned or safety_closed or style_closed:
                        self.bq_active = False
                        self.bq_style_mode = False
                        self.bq_quote_stack = []
                        self.bq_return_marker = None
                        self.bq_indent_x0 = None
                        self.bq_active_run_length = 0
                        self.bq_has_evidence = False
                        if style_closed:
                            reason = "return to unstyled text"
                        elif marker_closed:
                            reason = "clause marker"
                        elif indent_returned:
                            reason = "indentation return to outer margin"
                        else:
                            reason = "no supporting evidence this was ever a real quote"
                        self.logger.debug(f"Page {page.pg_num}: blockquote closed by {reason} before row '{text}'")
                        # falls through: this row is ordinary outer-flow content
                    else:
                        self.bq_all_styled = self.bq_all_styled and row_is_styled
                        for b in row["boxes"]:
                            page.all_tbs[b["tb"]] = "blockquote"
                        if quote_closed:
                            self.bq_closed_indent_x0 = self.bq_indent_x0
                            self.bq_active = False
                            self.bq_quote_stack = []
                            self.bq_return_marker = None
                            self.bq_indent_x0 = None
                            self.bq_active_run_length = 0
                            self.bq_has_evidence = False
                        continue

                if self.bq_pending_trigger and self.block_between_rows(rows, row_idx, block_spans):
                    self.bq_pending_trigger = False

                pending_trigger = self.bq_pending_trigger
                self.bq_pending_trigger = False
                closed_indent_x0 = self.bq_closed_indent_x0
                self.bq_closed_indent_x0 = None
                opened_ongoing = False
                opened_self_contained = False
                style_opened = False
                next_row_styled = row_idx + 1 < len(rows) and row_style(rows[row_idx + 1])[1]
                unit = row_height or 8.0
                indent_step = max(6.0, 0.75 * unit) * scan_factor
                ref_candidates = [v for v in (self.bq_outer_x0, page_margin) if v is not None]
                ref_x0 = min(ref_candidates) if ref_candidates else None
                measurable_indent = ref_x0 is not None and row_x0 is not None
                row_indented = measurable_indent and row_x0 > ref_x0 + indent_step
                if row_indented and page_margin is not None:
                    body_tol = max(4.0, 0.4 * unit) * scan_factor
                    if ref_x0 < page_margin - body_tol and abs(row_x0 - page_margin) <= body_tol:
                        row_indented = False
                has_structure = marker is not None or bool(LIST_ITEM_RE.match(text))
                next_line_indented = False
                if measurable_indent and row_idx + 1 < len(rows):
                    peek_boxes = rows[row_idx + 1]["boxes"]
                    if peek_boxes:
                        next_line_indented = peek_boxes[0]["x0"] > ref_x0 + indent_step
                row_x1 = max(b["x1"] for b in row["boxes"])
                right_inset = bool(
                    page_right is not None and row_x1 < page_right - max(8.0, unit) * scan_factor
                )
                first_line_indent = bool(
                    not next_line_indented and not right_inset and row_idx + 1 < len(rows)
                    and not re.search(r'[' + SENTENCE_END_CHAR_CLASS + r'][)\'"”’\]]*$', text.strip())
                )
                indent_signal = bool(
                    row_indented
                    and (next_line_indented or right_inset
                         or (row_x0 > ref_x0 + 2 * indent_step and not first_line_indent))
                )
                outdented_sub_item = bool(
                    measurable_indent and SUBITEM_MARKER_RE.match(text.split(' ')[0])
                    and row_x0 < ref_x0 - indent_step
                )
                is_outer_sibling = marker is not None and ClauseTracker.matches_any_with_indent(
                    marker, self.clause_tracker.snapshot_next_siblings(), row_x0, None, row_height
                )
                quote_open_ok = pending_trigger or self.bq_prev_block_boundary or (
                    self.bq_outer_x0 is not None and row_x0 is not None
                    and row_x0 > self.bq_outer_x0 + (row_height or 0) * 0.5)
                if text.startswith('"') and not is_closing(text, '"') and (pending_trigger or (double_count % 2 != 0 and not is_first_row and quote_open_ok)):
                    self.bq_quote_stack.append('"')
                    opened_ongoing = True
                elif text.startswith("'") and not is_closing(text, "'") and (pending_trigger or (single_count % 2 != 0 and not is_first_row and quote_open_ok)):
                    self.bq_quote_stack.append("'")
                    opened_ongoing = True
                elif (text.startswith('"') and is_closing(text, '"')) or \
                     (text.startswith("'") and is_closing(text, "'")):
                    opened_self_contained = True
                elif pending_trigger and not is_outer_sibling and (
                        (has_structure
                         and (ref_x0 is None
                              or open_x0 > ref_x0 + (row_height or 0) * 0.5 * scan_factor
                              or (judgments_mode and outdented_sub_item)))
                        or (judgments_mode and not has_structure and indent_signal)):
                    opened_ongoing = True
                elif (judgments_mode and closed_indent_x0 is not None and has_structure
                        and not is_outer_sibling and row_indented
                        and row_x0 >= closed_indent_x0 - max(4.0, 0.4 * unit) * scan_factor):
                    opened_ongoing = True
                elif (judgments_mode and pending_trigger and not is_outer_sibling and row_is_styled
                        and (row_is_italic or row_indented or next_row_styled)):
                    opened_ongoing = True
                    style_opened = True

                if opened_ongoing:
                    return_marker = self.clause_tracker.snapshot_next_sibling()
                    return_markers = self.clause_tracker.snapshot_next_siblings()
                    if not judgments_mode and not self.bq_quote_stack and return_marker is None:
                        self.bq_quote_stack = []
                    else:
                        self.bq_active = True
                        self.bq_return_marker = return_marker
                        self.bq_return_markers = return_markers
                        self.bq_indent_x0 = row_x0
                        if ref_x0 is not None:
                            self.bq_outer_x0 = ref_x0
                        self.bq_active_run_length = 0
                        self.bq_has_evidence = bool(self.bq_quote_stack) or row_indented or style_opened
                        self.bq_style_mode = style_opened and not self.bq_quote_stack
                        self.bq_all_styled = row_is_styled
                        for b in row["boxes"]:
                            page.all_tbs[b["tb"]] = "blockquote"
                        self.logger.debug(f"Page {page.pg_num}: blockquote opened on row '{text}'")
                        continue
                elif opened_self_contained:
                    self.bq_closed_indent_x0 = row_x0
                    for b in row["boxes"]:
                        page.all_tbs[b["tb"]] = "blockquote"
                    self.logger.debug(f"Page {page.pg_num}: self-contained blockquote on row '{text}'")
                    continue

                self.clause_tracker.observe(marker, row_x0)
                # Remembers the outer document's own left margin as of the most
                # recent ordinary-flow row, frozen while a quote is active - the
                # baseline the indentation-return closing check above compares
                # against once we're back out of the quote.
                self.bq_outer_x0 = row_x0
                self.bq_prev_block_boundary = bool(re.search(r'[' + SENTENCE_END_CHAR_CLASS + r'][)\'"”’\]]*$', text.strip()))

                if trigger_re.search(text) and not row_is_styled:
                    self.bq_pending_trigger = True
                    self.logger.debug(f"Page {page.pg_num}: blockquote trigger detected in row '{text}'")

            except Exception as e:
                self.logger.error(f"Error while processing judgment blockquote logic on page {getattr(page, 'pg_num', '?')}: {e}")
