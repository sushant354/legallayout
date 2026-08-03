import logging
import re
import unicodedata

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


class ClauseTracker:

    _PATTERNS = (
        ("roman", re.compile(r'^\(([ivxlcdmIVXLCDM]{2,6})\)(?=[\s.:;]|$)')),
        ("arabic_dot", re.compile(r'^(\d{1,3})[.\)](?=[\s]|$)')),
        ("paren_num", re.compile(r'^\((\d{1,3})\)(?=[\s.:;]|$)')),
        ("alpha", re.compile(r'^\(([A-Za-z])\)(?=[\s.:;]|$)')),
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
        self.bq_indent_x0 = None
        self.bq_active_run_length = 0
        self.bq_seen_row = False
        self.clause_tracker = ClauseTracker()

    # --- func to classify the textbox if it is detected with sign of amendments properties ---
    def check_for_amendment_acts(self, page): #,startPage,endPage):
        for tb in page.all_tbs.keys():
            try:
                text = tb.extract_text_from_tb().strip()
            except Exception as e:
                self.logger.warning(f"Failed to extract text from textbox on page {getattr(page, 'pg_num', '?')}: {e}")
                continue

            text = text.replace('“', '"').replace('”', '"').replace('‘‘','"').replace('’’','"').replace('‘', "'").replace('’', "'")

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

            text = text.replace('“', '"').replace('”', '"').replace('‘‘','"').replace('’’','"').replace('‘', "'").replace('’', "'")

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
    def check_for_blockquotes_judgments(self, page):
        trigger_re = re.compile(r'[:：][-‐‑‒–—―−]?\s*$')
        min_trigger_words = 3
        row_y_tolerance = 3.0
        BQ_MAX_RUN_LENGTH = 80
        annotation_re = re.compile(
            r'\s*[\(\[](emphasis (supplied|added|mine)|underlined?|sic)[\)\]]\.?\s*$',
            re.IGNORECASE,
        )

        def normalize(text):
            if not text:
                return ""
            text = unicodedata.normalize("NFKC", text)
            for invisible in ('\xad', '​', '‌', '‍', '⁠', '﻿'):
                text = text.replace(invisible, '')
            text = text.replace('“', '"').replace('”', '"').replace('„', '"').replace('‟', '"')
            text = text.replace('«', '"').replace('»', '"').replace('＂', '"')
            text = text.replace('‘', "'").replace('’', "'").replace('‚', "'").replace('‛', "'")
            text = text.replace('‹', "'").replace('›', "'").replace('＇', "'")
            text = re.sub(r'\s+', ' ', text).strip()
            return text

        def is_closing(text, quote_char):
            text = annotation_re.sub('', text)
            return (
                text.endswith(quote_char + '.') or text.endswith(quote_char + ';') or
                text.endswith('.' + quote_char) or text.endswith(';' + quote_char) or
                text.endswith('…' + quote_char) or text.endswith(quote_char)
            )

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
                "tb": tb, "text": text, "x0": x0, "y_top": y1,
                "height": y1 - y0,
            })

        if not boxes:
            return

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

        for row in rows:
            text = row["text"]
            if not text:
                continue

            try:
                double_count = text.count('"')
                single_count = text.count("'")
                marker = ClauseTracker.parse_marker(text)
                row_x0 = row["boxes"][0]["x0"]
                row_height = row["boxes"][0]["height"]
                is_first_row = not self.bq_seen_row
                self.bq_seen_row = True

                if self.bq_active:
                    self.bq_active_run_length += 1
                    if self.bq_active_run_length > BQ_MAX_RUN_LENGTH:
                        self.bq_active = False
                        self.bq_quote_stack = []
                        self.bq_return_marker = None
                        self.bq_indent_x0 = None
                        self.bq_active_run_length = 0
                        self.logger.debug(f"Page {page.pg_num}: blockquote force-closed after {BQ_MAX_RUN_LENGTH} rows before row '{text}'")

                if self.bq_active:
                    # Quote-character balance and clause-marker matching are two
                    # independent ways to notice the excerpt has ended - whichever
                    # fires first wins. The marker check matters precisely because not
                    # every PDF balances its quotation marks (OCR routinely drops one
                    # side of a curly quote, and non-English/script-mixed documents
                    # may not use a Western quote convention at all): the reappearance
                    # of the outer document's next clause is a language-agnostic
                    # signal that doesn't depend on quote punctuation being present or
                    # well-formed.
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
                    # before deactivating. A marker-close is different in kind: it
                    # fires on the row *after* the excerpt, the outer document's own
                    # next clause, which was never part of the quote - so it must
                    # NOT be marked, and processing falls through to treat it as
                    # ordinary content (including re-checking it as a fresh trigger).
                    marker_closed = (not quote_closed) and ClauseTracker.matches_with_indent(
                        marker, self.bq_return_marker, row_x0, self.bq_indent_x0, row_height
                    )

                    if marker_closed:
                        self.bq_active = False
                        self.bq_quote_stack = []
                        self.bq_return_marker = None
                        self.bq_indent_x0 = None
                        self.bq_active_run_length = 0
                        self.logger.debug(f"Page {page.pg_num}: blockquote closed by clause marker before row '{text}'")
                        # falls through: this row is ordinary outer-flow content
                    else:
                        for b in row["boxes"]:
                            page.all_tbs[b["tb"]] = "blockquote"
                        if quote_closed:
                            self.bq_active = False
                            self.bq_quote_stack = []
                            self.bq_return_marker = None
                            self.bq_indent_x0 = None
                            self.bq_active_run_length = 0
                        continue

                pending_trigger = self.bq_pending_trigger
                self.bq_pending_trigger = False
                opened_ongoing = False
                opened_self_contained = False
                if text.startswith('"') and not is_closing(text, '"') and (double_count % 2 != 0 and not is_first_row or pending_trigger):
                    self.bq_quote_stack.append('"')
                    opened_ongoing = True
                elif text.startswith("'") and not is_closing(text, "'") and (single_count % 2 != 0 and not is_first_row or pending_trigger):
                    self.bq_quote_stack.append("'")
                    opened_ongoing = True
                elif (text.startswith('"') and is_closing(text, '"')) or \
                     (text.startswith("'") and is_closing(text, "'")):
                    opened_self_contained = True
                elif pending_trigger and not (marker is not None and ClauseTracker.matches_with_indent(
                    marker, self.clause_tracker.snapshot_next_sibling(), row_x0, None, row_height
                )):
                    opened_ongoing = True

                if opened_ongoing:
                    return_marker = self.clause_tracker.snapshot_next_sibling()
                    if not self.bq_quote_stack and return_marker is None:
                        self.bq_quote_stack = []
                    else:
                        self.bq_active = True
                        self.bq_return_marker = return_marker
                        self.bq_indent_x0 = row_x0
                        self.bq_active_run_length = 0
                        for b in row["boxes"]:
                            page.all_tbs[b["tb"]] = "blockquote"
                        self.logger.debug(f"Page {page.pg_num}: blockquote opened on row '{text}'")
                        continue
                elif opened_self_contained:
                    for b in row["boxes"]:
                        page.all_tbs[b["tb"]] = "blockquote"
                    self.logger.debug(f"Page {page.pg_num}: self-contained blockquote on row '{text}'")
                    continue

                self.clause_tracker.observe(marker, row_x0)

                # A short line ending in ':' is only rejected as a standalone label
                # (e.g. "CORAM:", "Present:") when it also *looks* like one - starting
                # with a capital, as institutional headers do. A short fragment like
                # "follows:-" that line-wrapped off the end of a longer lead-in sentence
                # starts lowercase and must still be allowed to open a quote.
                words = text.split()
                looks_like_standalone_label = len(words) < min_trigger_words and text[0].isupper()
                if trigger_re.search(text) and not looks_like_standalone_label:
                    self.bq_pending_trigger = True
                    self.logger.debug(f"Page {page.pg_num}: blockquote trigger detected in row '{text}'")

            except Exception as e:
                self.logger.error(f"Error while processing judgment blockquote logic on page {getattr(page, 'pg_num', '?')}: {e}")
