import statistics
import re
import math
from collections import OrderedDict
import numpy as np
import logging
import textwrap
import pandas as pd
from difflib import SequenceMatcher
from sklearn.cluster import DBSCAN
import os

from .Table import TableBuilder
from .NormalizeText import NormalizeText
from .SentenceEndDetector import SentenceMaker

COMBINED_RE = re.compile(
                r"""
                ^\s*
                (?:
                    (?P<bullet>\([^)]*\))          # FIRST priority: (A), (1), (i)
                    |
                    (?P<title>.*?[.:]\s*(?:-|—)?)  # SECOND priority: title.
                )
                \s*
                (?P<rest>.*)
                $
                """,
                re.VERBOSE
            )

class SebiCirculars(TableBuilder, SentenceMaker):
    def __init__(self, unique_images, all_footnote_text, sentence_completion_punctuation = tuple(), pdf_type = None, docend_symbol = False):
        TableBuilder.__init__(self)
        self.logger = logging.getLogger(__name__)
        self.pdf_type = pdf_type
        self.sentence_completion_punctuation = sentence_completion_punctuation
        self.stack_for_section = []
        self.hierarchy = []
        self.is_preamble_reached = True
        self.is_body_added = True
        self.normalize_text = NormalizeText().normalize_text
        self.builder = ""
        self.an_or_act = ""
        self.main_builder = ""
        self.is_schedule_open = False
        self.previous_sentence_end_status = True
        self.curr_tab_level = 0
        self.is_act_ended = False
        self.docend_symbol = docend_symbol
        self.table_visited_lastly = False
        self.act_end_re = r'[— _-]{3,}'
        self.roman_re  = r"(?:M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3}))"
        self.section_shorttitle_notend_status = False
        self.tab_level = {
            0 : ['PREFACE', 'PREAMBLE', 'CHAP', 'BODY', 'SCHEDULE', 'ART', 'PART',
                 'ANNEXURE', 'APPENDIX', 'ATTACHMENT'],
        }
        self.unique_image = unique_images
        self.all_footnote_text = all_footnote_text
        self.footnote_to_add = None
    
    def get_tab_level(self, category):
        for key, value in self.tab_level.items():
            if category in value :
                return key
        return None
    
    def check_preamble_start(self, text):
        pattern = re.compile(
                r'''
                ^\s*(
                    (?:A\s+)?An\s+Act\b                      # An Act / A An Act
                    (?:\s*\|\s*BE\s+it\s+enacted\s+by\b)?    # optional pipe + BE it enacted by
                    |
                    BE\s+it\s+enacted\s+by\b                 # BE it enacted by
                    |
                    preamble\b                               # preamble
                    |
                    hereby\s+it\s+is\s+enacted\s+by\b        # hereby it is enacted by
                    |
                    A\s+Bill\b                             # A Bill
                    # |
                    # Whereas\b                             # Whereas
                )
                ''',
                re.IGNORECASE | re.VERBOSE
            )
        match = re.search(pattern, text)
        return bool(match)
    
    def close_bluebell(self):
       bluebell = self.main_builder + self.builder 
       return bluebell
    
    def get_content(self):
        self.flushTables()
        return self.close_bluebell()
    
    def is_chapter(self, text):
        pattern = rf"""
            ^\s*

            \b(?:chapter|section)\b
            
            \s*

            # optional separator before chapter number
            [\-–—:.\u2013\u2014]?
            \s*

            # chapter number
            (?P<number>
                \d+
                |
                {self.roman_re}
            )

            # optional separator after number
            \s*
            [\-–—:.\u2013\u2014.]?
            \s*

            # optional title
            (?P<title>.*?)

            \s*$
        """

        match = re.match(
            pattern,
            text,
            re.IGNORECASE | re.VERBOSE
        )

        if match:
            return  True, match.group("number"),match.group("title")
            

        return False, None, None
    
    def is_part(self, text):

        pattern = rf"""
            ^\s*

            \bpart\b
            \s*

            # optional separator before part number
            [\-–—:.\u2013\u2014]?
            \s*

            # part identifier
            (
                \d+
                |
                [A-Z]+
                |
                {self.roman_re}
            )

            # optional separator after identifier
            \s*
            [\-–—:.\u2013\u2014.]?
            \s*

            # optional title
            (.*?)

            \s*$
        """

        match = re.match(
            pattern,
            text,
            re.IGNORECASE | re.VERBOSE
        )

        if match:
            return True, match.group(1), match.group(2)


        return False, None, None

    def is_article(self, text):
        pattern = rf"""
            ^\s*\barticle\b              # word 'article'
            \s*                      # optional spaces
            [\-–—:.\u2013\u2014]?    # one optional separator
            \s*                      # optional spaces
            (\d+|{self.roman_re})    # number or roman
        """
        match = re.match(pattern, text, re.IGNORECASE | re.VERBOSE)
        if match:
            return True, match.group(1)
        return False, None


    def is_schedule(self, text):

        ordinals = [
            "first", "second", "third", "fourth", "fifth", "sixth", "seventh",
            "eighth", "ninth", "tenth", "eleventh", "twelfth", "thirteenth",
            "fourteenth", "fifteenth", "sixteenth", "seventeenth", "eighteenth",
            "nineteenth", "twentieth"
        ]

        ordinals_re = r"(?:{})".format("|".join(ordinals))

        numbers_re = r"(?:[1-9][0-9]*)"

        pattern = rf"""
            ^\s*

            (?:the\s+)?                 # optional 'the'

            \bschedule\b
            \s*

            # optional separator after schedule
            [\-–—:.\u2013\u2014]?
            \s*

            # optional schedule identifier
            (
                {ordinals_re}
                |
                {numbers_re}
                |
                {self.roman_re}
            )?

            \s*

            # optional separator after identifier
            [\-–—:.\u2013\u2014.]?
            \s*

            # optional title
            (.*?)

            \s*$
        """

        return bool(
            re.match(
                pattern,
                text,
                re.IGNORECASE | re.VERBOSE
            )
        )


    def is_annexure(self, text):

        pattern = rf"""
            ^\s*

            (?:

                # ---------------------------------
                # CASE 1:
                # Annexure at beginning
                # ---------------------------------

                (?:\d+(?:\.\d+)*\s+)?     # optional numbering

                \bannexures?\b

                \s*

                [\-–—:.\u2013\u2014]?
                \s*

                (
                    \d+
                    |
                    [A-Z]+
                    |
                    {self.roman_re}
                )?

                \s*

                [\-–—:.\u2013\u2014.]?
                \s*

                (.*?)

                |

                # ---------------------------------
                # CASE 2:
                # Annexure at end
                # ---------------------------------

                (.*?)

                \s*

                [\-–—:.\u2013\u2014]?
                \s*

                \bannexure\b
                \s*

                (
                    \d+
                    |
                    [A-Z]+
                    |
                    {self.roman_re}
                )

            )

            \s*$
        """

        return bool(
            re.match(
                pattern,
                text,
                re.IGNORECASE | re.VERBOSE
            )
        )

    def is_appendix(self, text):

        pattern = rf"""
            ^\s*

            # optional numbering like:
            # 13
            # 13.1
            (?:\d+(?:\.\d+)*\s+)?

            \bappendix\b
            \s*

            # optional separator
            [\-–—:.\u2013\u2014]?
            \s*

            # optional appendix identifier
            (
                \d+
                |
                [A-Z]+
                |
                {self.roman_re}
            )?

            \s*

            # optional separator after identifier
            [\-–—:.\u2013\u2014.]?
            \s*

            # optional title
            (.*?)

            \s*$
        """

        return bool(
            re.match(
                pattern,
                text,
                re.IGNORECASE | re.VERBOSE
            )
        )

    def is_form(self, text):

        pattern = rf"""
            ^\s*

            # optional numbering like:
            # 13
            # 13.1
            (?:\d+(?:\.\d+)*\s+)?

            \bform\b
            \s*

            # optional separator
            [\-–—:.\u2013\u2014]?
            \s*

            # optional form identifier
            (
                \d+
                |
                [A-Z]+
                |
                {self.roman_re}
            )?

            \s*

            # optional separator after identifier
            [\-–—:.\u2013\u2014.]?
            \s*

            # optional title
            (.*?)

            |

            # ---------------------------------
            # CASE 2:
            # title ending with "Form X"
            # ---------------------------------

            (.*?)

            \s*

            [\-–—:.\u2013\u2014]?
            \s*

            \bform\b
            \s*

            (
                \d+
                |
                [A-Z]+
                |
                {self.roman_re}
            )

            \s*$

        """

        return bool(
            re.match(
                pattern,
                text,
                re.IGNORECASE | re.VERBOSE
            )
        )

    def get_textline_statistics(self, texts):

        sizes = []
        bottoms = []

        for text in texts:

            try:

                if "size" in text.attrib:
                    sizes.append(
                        float(text.attrib["size"])
                    )

                if "bbox" in text.attrib:

                    x0, y0, x1, y1 = map(
                        float,
                        text.attrib["bbox"].split(",")
                    )

                    bottoms.append(y0)

            except Exception:
                continue

        if not sizes:
            return None, None

        base_size = statistics.median(sizes)

        base_bottom = statistics.median(bottoms)

        return base_size, base_bottom

    def is_superscript_text(
        self,
        text,
        base_size,
        base_bottom
    ):

        try:

            raw = text.text or ""

            if not raw.strip():
                return False

            normalized = self.normalize_text(raw)

            size = float(
                text.attrib.get(
                    "size",
                    base_size
                )
            )

            x0, y0, x1, y1 = map(
                float,
                text.attrib["bbox"].split(",")
            )

            smaller_font = (
                size < (base_size * 0.85)
            )

            raised = (
                y0 > (
                    base_bottom + (base_size * 0.15)
                )
            )

            valid_mark = bool(
                re.fullmatch(
                    r"[0-9a-zA-Z*†‡]+",
                    normalized.strip()
                )
            )

            return (
                raised
                and (
                    smaller_font
                    or valid_mark
                )
            )

        except Exception:
            return False

    def textline_has_superscript(
        self,
        texts,
        base_size,
        base_bottom
    ):

        for text in texts:

            if self.is_superscript_text(
                text,
                base_size,
                base_bottom
            ):
                return True

        return False

    def build_plain_textline(self, texts):

        line_parts = []

        for text in texts:

            raw = text.text or ""

            if raw:
                line_parts.append(
                    raw.replace("\n", " ")
                )

        line = "".join(line_parts)

        return self.normalize_text(
            line.strip()
        )

    def build_footnote_textline(
        self,
        texts,
        base_size,
        base_bottom
    ):

        line_parts = []

        pending_superscript = []

        for text in texts:

            raw = text.text or ""

            if raw == "":
                continue

            raw = raw.replace("\n", " ")

            if raw.isspace():

                if pending_superscript:

                    marker = "".join(
                        pending_superscript
                    )

                    line_parts.append(
                        "{{^{{FOOTNOTE "
                        + marker +
                        "}}}}"
                    )

                    pending_superscript = []

                line_parts.append(raw)

                continue

            is_super = self.is_superscript_text(
                text,
                base_size,
                base_bottom
            )

            if is_super:

                pending_superscript.append(
                    raw.strip()
                )

                continue

            if pending_superscript:

                marker = "".join(
                    pending_superscript
                )

                line_parts.append(
                    "{{^{{FOOTNOTE "
                    + marker +
                    "}}}}"
                )

                pending_superscript = []

            line_parts.append(raw)

        if pending_superscript:

            marker = "".join(
                pending_superscript
            )

            line_parts.append(
                "{{^{{FOOTNOTE "
                + marker +
                "}}}}"
            )

        line = "".join(line_parts)

        return self.normalize_text(
            line.strip()
        )



    def addTitle(self, tb):
        try:
          is_sentence_completed = tb.extract_text_from_tb().strip().endswith(self.sentence_completion_punctuation)
          self.previous_sentence_end_status = True
          for textline in tb.tbox.findall('.//textline'):
                texts = textline.findall('.//text')

                if not texts:
                    continue

                if not self.footnote_to_add:

                    line = self.build_plain_textline(texts)

                else:

                    base_size, base_bottom = (
                        self.get_textline_statistics(texts)
                    )

                    if base_size is None:

                        line = self.build_plain_textline(texts)

                    else:

                        has_superscript = (
                            self.textline_has_superscript(
                                texts,
                                base_size,
                                base_bottom
                            )
                        )

                        if has_superscript:

                            line = self.build_footnote_textline(
                                texts,
                                base_size,
                                base_bottom
                            )

                        else:

                            line = self.build_plain_textline(
                                texts
                            )

                if not line:
                    continue

                if re.fullmatch(self.act_end_re, line):
                        self.is_act_ended = True
                        if self.docend_symbol and self.is_act_ended:
                                break
                        else:
                                continue
                
                
                if self.section_shorttitle_notend_status:
                    is_sentence_completed = line.endswith(self.sentence_completion_punctuation)
                    
                    if re.search(r'\{\{\^\{\{FOOTNOTE\s*\d+\}\}\}\}\s*$', line):
                        self.builder += " " + line
                        self.previous_sentence_end_status = True
                        self.section_shorttitle_notend_status = False  
                        if  self.previous_sentence_end_status and self.footnote_to_add:
                            self.add_footnote(footnotes = self.footnote_to_add)
                            self.footnote_to_add = None
                        continue
               
                    match = COMBINED_RE.match(line)

                    if match and match.group("bullet"):
                        rest_text = line.strip()   # FULL line goes to findType

                        rest_text_type, value, remain_text = self.findType(rest_text)

                        if rest_text_type is None:
                            self.builder += (
                                "\n"
                                + ("\t" * (self.curr_tab_level + 1))
                                + f"{remain_text}"
                            )
                        else:
                            self.curr_tab_level = self.get_hierarchy_level(rest_text_type)
                            self.builder += (
                                "\n"
                                + ("\t" * self.curr_tab_level)
                                + f"{rest_text_type} {value}"
                            )
                            self.builder += (
                                "\n"
                                + ("\t" * (self.curr_tab_level + 1))
                                + f"{remain_text}"
                            )

                        self.section_shorttitle_notend_status = False
                        self.previous_sentence_end_status = is_sentence_completed
                        # print(self.previous_sentence_end_status, text)
                        if  self.previous_sentence_end_status and self.is_footnote_detected:
                            # self.logger.info('footnote 1')
                            self.add_footnote(footnotes = self.footnote_to_add)
                            self.footnote_to_add = None
                        continue

                    # ---------- SECOND PRIORITY: TITLE SPLIT ----------
                    if match and match.group("title"):
                        title_part = match.group("title").strip().rstrip("-—")
                        rest_text = match.group("rest").strip()

                        self.builder += " " + title_part

                        rest_text_type, value, remain_text = self.findType(rest_text)

                        if rest_text_type is None:
                            self.builder += (
                                "\n"
                                + ("\t" * (self.curr_tab_level + 1))
                                + f"{remain_text}"
                            )
                        else:
                            self.curr_tab_level = self.get_hierarchy_level(rest_text_type)
                            self.builder += (
                                "\n"
                                + ("\t" * self.curr_tab_level)
                                + f"{rest_text_type} {value}"
                            )
                            self.builder += (
                                "\n"
                                + ("\t" * (self.curr_tab_level + 1))
                                + f"{remain_text}"
                            )

                        self.section_shorttitle_notend_status = False
                        self.previous_sentence_end_status = is_sentence_completed
                        # print(self.previous_sentence_end_status, text)
                        if  self.previous_sentence_end_status and self.footnote_to_add:
                            # self.logger.info('footnote 2')
                            self.add_footnote(footnotes = self.footnote_to_add)
                            self.footnote_to_add = None
                        continue

                    # ---------- FALLBACK ----------
                    self.builder += " " + line
                    self.previous_sentence_end_status = is_sentence_completed
                    # print(self.previous_sentence_end_status, text)
                    if  self.previous_sentence_end_status and self.footnote_to_add:
                        # self.logger.info('footnote 3')
                        self.add_footnote(footnotes = self.footnote_to_add)
                        self.footnote_to_add = None
                    continue


                if self.is_preamble_reached and line:
                    matched = self.is_schedule(line)
                    if matched:
                        if not self.is_body_added:
                            tab_level = self.get_tab_level('BODY')
                            if tab_level is not None:
                                self.builder += "\n" + ("\t" * tab_level) + f"BODY"
                                self.is_body_added = True
                                self.curr_tab_level = tab_level

                        tab_level = self.get_tab_level('SCHEDULE')
                        if tab_level is not None:
                            self.builder += "\n" + ("\t" * tab_level) + f"SCHEDULE {line}"
                            self.curr_tab_level = tab_level
                            self.hierarchy = ['SCHEDULE']
                            self.is_schedule_open = True
                            continue
                    
                    matched = self.is_annexure(line)
                    if matched:
                        if not self.is_body_added:
                            tab_level = self.get_tab_level('BODY')
                            if tab_level is not None:
                                self.builder += "\n" + ("\t" * tab_level) + f"BODY"
                                self.is_body_added = True
                                self.curr_tab_level = tab_level

                        tab_level = self.get_tab_level('ANNEXURE')
                        if tab_level is not None:
                            self.builder += "\n" + ("\t" * tab_level) + f"ANNEXURE {line}"
                            self.curr_tab_level = tab_level
                            self.hierarchy = ['ANNEXURE']
                            self.is_schedule_open = True
                            continue

                    matched = self.is_appendix(line)
                    if matched:
                        if not self.is_body_added:
                            tab_level = self.get_tab_level('BODY')
                            if tab_level is not None:
                                self.builder += "\n" + ("\t" * tab_level) + f"BODY"
                                self.is_body_added = True
                                self.curr_tab_level = tab_level

                        tab_level = self.get_tab_level('APPENDIX')
                        if tab_level is not None:
                            self.builder += "\n" + ("\t" * tab_level) + f"APPENDIX {line}"
                            self.curr_tab_level = tab_level
                            self.hierarchy = ['APPENDIX']
                            self.is_schedule_open = True
                            continue

                    matched = self.is_form(line)
                    if matched:
                        if not self.is_body_added:
                            tab_level = self.get_tab_level('BODY')
                            if tab_level is not None:
                                self.builder += "\n" + ("\t" * tab_level) + f"BODY"
                                self.is_body_added = True
                                self.curr_tab_level = tab_level

                        tab_level = self.get_tab_level('ATTACHMENT')
                        if tab_level is not None:
                            self.builder += "\n" + ("\t" * tab_level) + f"ATTACHMENT {line}"
                            self.curr_tab_level = tab_level
                            self.hierarchy = ['ATTACHMENT']
                            self.is_schedule_open = True
                            continue                    
                    


                    if self.is_schedule_open:
                        self.curr_tab_level = self.get_hierarchy_level('SUBPART')
                        self.builder += "\n" + ("\t" * self.curr_tab_level) + f"SUBPART - {line}"
                        continue

                    matched, val, title = self.is_chapter(line)
                    if matched:
                        if not self.is_body_added:
                            tab_level = self.get_tab_level('BODY')
                            if tab_level is not None:
                                self.builder += "\n" + ("\t" * tab_level) + f"BODY"
                                self.is_body_added = True
                                self.curr_tab_level = tab_level

                        tab_level = self.get_tab_level('CHAP')
                        if tab_level is not None:
                            if not title:
                                self.builder += "\n" + ("\t" * tab_level) + f"CHAP {val} -"
                            else:
                                self.builder += "\n" + ("\t" * tab_level) + f"CHAP {val} - {title.strip()}"
                            self.curr_tab_level = tab_level
                            self.hierarchy = ['CHAP']
                            self.is_schedule_open = False
                            continue
                    
                    matched, val, title = self.is_part(line)
                    if matched:
                        if not self.is_body_added:
                            tab_level = self.get_tab_level('BODY')
                            if tab_level is not None:
                                self.builder += "\n" + ("\t" * tab_level) + f"BODY"
                                self.is_body_added = True
                                self.curr_tab_level = tab_level

                        tab_level = self.get_tab_level('PART')
                        if tab_level is not None:
                            if not title:
                                self.builder += "\n" + ("\t" * tab_level) + f"PART {val} -"
                            else:
                                self.builder += "\n" + ("\t" * tab_level) + f"PART {val} - {title.strip()}"
                            self.curr_tab_level = tab_level
                            self.hierarchy = ['PART']
                            self.is_schedule_open = False
                            continue

                    if self.table_visited_lastly:
                        self.builder += "\n" + ("\t" * (self.curr_tab_level+1)) + line
                        self.table_visited_lastly = False
                        continue
                    
                    self.builder += " " + line
                
                else:
                    if self.check_for_an_or_act(line):
                        continue
                    is_matched = self.check_preamble_start(line)
                    if is_matched:
                        is_sentence_completed = line.endswith(self.sentence_completion_punctuation)
                        self.is_preamble_reached = True
                        self.table_visited_lastly = False
                        if self.is_body_added:
                            self.builder = ""
                            self.is_body_added = False
                            self.set_empty_hierarchy()
                        tab_level = self.get_tab_level('PREAMBLE')
                        if tab_level is not None:
                            self.builder +=  ("\t" * tab_level) + "PREAMBLE"
                            self.curr_tab_level = tab_level
                            self.builder += "\n" + ("\t" * (self.curr_tab_level+1) + line)
                            self.previous_sentence_end_status = is_sentence_completed
                            # print(self.previous_sentence_end_status, text)
                            if  self.previous_sentence_end_status and self.footnote_to_add:
                                # self.logger.info('footnote 4')
                                self.add_footnote(footnotes = self.footnote_to_add)
                                self.footnote_to_add = None
                            continue
                        else:
                            self.curr_tab_level += 1
                            self.builder +=   ("\t" * (self.curr_tab_level)) + "PREAMBLE"
                            self.builder += "\n" + ("\t" * (self.curr_tab_level+1) + line)
                            self.previous_sentence_end_status = is_sentence_completed
                            # print(self.previous_sentence_end_status, text)
                            if  self.previous_sentence_end_status and self.footnote_to_add:
                                # self.logger.info('footnote 5')
                                self.add_footnote(footnotes = self.footnote_to_add)
                                self.footnote_to_add = None
                            continue
                    
                    matched = self.is_schedule(line)
                    if matched:
                        if not self.is_body_added:
                            tab_level = self.get_tab_level('BODY')
                            if tab_level is not None:
                                self.builder += "\n" + ("\t" * tab_level) + f"BODY"
                                self.is_body_added = True
                                self.curr_tab_level = tab_level

                        tab_level = self.get_tab_level('SCHEDULE')
                        if tab_level is not None:
                            self.builder += "\n" + ("\t" * tab_level) + f"SCHEDULE {line}"
                            self.curr_tab_level = tab_level
                            self.hierarchy = ['SCHEDULE']
                            self.is_schedule_open = True
                            continue
                    
                    matched = self.is_annexure(line)
                    if matched:
                        if not self.is_body_added:
                            tab_level = self.get_tab_level('BODY')
                            if tab_level is not None:
                                self.builder += "\n" + ("\t" * tab_level) + f"BODY"
                                self.is_body_added = True
                                self.curr_tab_level = tab_level

                        tab_level = self.get_tab_level('ANNEXURE')
                        if tab_level is not None:
                            self.builder += "\n" + ("\t" * tab_level) + f"ANNEXURE {line}"
                            self.curr_tab_level = tab_level
                            self.hierarchy = ['ANNEXURE']
                            self.is_schedule_open = True
                            continue

                    matched = self.is_appendix(line)
                    if matched:
                        if not self.is_body_added:
                            tab_level = self.get_tab_level('BODY')
                            if tab_level is not None:
                                self.builder += "\n" + ("\t" * tab_level) + f"BODY"
                                self.is_body_added = True
                                self.curr_tab_level = tab_level

                        tab_level = self.get_tab_level('APPENDIX')
                        if tab_level is not None:
                            self.builder += "\n" + ("\t" * tab_level) + f"APPENDIX {line}"
                            self.curr_tab_level = tab_level
                            self.hierarchy = ['APPENDIX']
                            self.is_schedule_open = True
                            continue

                    matched = self.is_form(line)
                    if matched:
                        if not self.is_body_added:
                            tab_level = self.get_tab_level('BODY')
                            if tab_level is not None:
                                self.builder += "\n" + ("\t" * tab_level) + f"BODY"
                                self.is_body_added = True
                                self.curr_tab_level = tab_level

                        tab_level = self.get_tab_level('ATTACHMENT')
                        if tab_level is not None:
                            self.builder += "\n" + ("\t" * tab_level) + f"ATTACHMENT {line}"
                            self.curr_tab_level = tab_level
                            self.hierarchy = ['ATTACHMENT']
                            self.is_schedule_open = True
                            continue                   

                    if self.is_schedule_open:
                        self.curr_tab_level = self.get_hierarchy_level('SUBPART')
                        self.builder += "\n" + ("\t" * self.curr_tab_level) + f"SUBPART - {line}"
                        continue

                    matched, val, title = self.is_chapter(line)
                    if matched:
                        if not self.is_body_added:
                            tab_level = self.get_tab_level('BODY')
                            if tab_level is not None:
                                self.builder += "\n" + ("\t" * tab_level) + f"BODY"
                                self.is_body_added = True
                                self.curr_tab_level = tab_level

                        tab_level = self.get_tab_level('CHAP')
                        if tab_level is not None:
                            if not title:
                                self.builder += "\n" + ("\t" * tab_level) + f"CHAP {val} -"
                            else:
                                self.builder += "\n" + ("\t" * tab_level) + f"CHAP {val} - {title.strip()}"
                            self.curr_tab_level = tab_level
                            self.hierarchy = ['CHAP']
                            self.is_schedule_open = False
                            continue 
                    
                    matched, val, title = self.is_part(line)
                    if matched:
                        if not self.is_body_added:
                            tab_level = self.get_tab_level('BODY')
                            if tab_level is not None:
                                self.builder += "\n" + ("\t" * tab_level) + f"BODY"
                                self.is_body_added = True
                                self.curr_tab_level = tab_level

                        tab_level = self.get_tab_level('PART')
                        if tab_level is not None:
                            if not title:
                                self.builder += "\n" + ("\t" * tab_level) + f"PART {val} -"
                            else:
                                self.builder += "\n" + ("\t" * tab_level) + f"PART {val} - {title.strip()}"
                            self.curr_tab_level = tab_level
                            self.hierarchy = ['PART']
                            self.is_schedule_open = False
                            continue
                    
        except Exception as e:
          self.logger.exception("Error while adding title - [%s] in html: %s",tb.extract_text_from_tb(),e)
    
    def check_for_an_or_act(self, text):
        pattern = re.compile(r'^\s*(An|Act|A|Bill)\s*$', re.IGNORECASE)
        if re.match(pattern, text):
            if (text.lower().strip() == 'act' or text.lower().strip() == 'bill') \
                and (self.an_or_act.lower().strip() == 'an' 
                     or self.an_or_act.lower().strip() == 'a'):
                temp = self.an_or_act + ' ' + text
                self.add_preamble(temp)
                self.an_or_act = ""
                return True
            else:
                self.an_or_act += text.strip()
                return True
        return False
    
    def add_preamble(self, text):
        self.is_preamble_reached = True
        self.table_visited_lastly = False
        tab_level = self.get_tab_level('PREAMBLE')
        is_sentence_completed = text.endswith(self.sentence_completion_punctuation)
        if self.is_body_added:
            self.builder = ""
            self.is_body_added = False
            self.set_empty_hierarchy()
        if tab_level is not None:
            self.builder += ("\t" * tab_level) + "PREAMBLE"
            self.curr_tab_level = tab_level
            self.builder += "\n" +  ("\t" * (self.curr_tab_level+1) + text)
            self.previous_sentence_end_status = is_sentence_completed
            return
        else:
            self.curr_tab_level += 1
            self.builder +=  ("\t" * (self.curr_tab_level)) + "PREAMBLE"
            self.builder += "\n" + ("\t" * (self.curr_tab_level+1) + text)
            self.previous_sentence_end_status = is_sentence_completed
            return

    def find_closest_side_note(self, tb_bbox, side_note_datas, page_height, vertical_threshold_ratio=0.05): # 0.05
        try:
            tb_x0, tb_y0, tb_x1, tb_y1 = tb_bbox
            vertical_threshold = page_height * vertical_threshold_ratio

            self.logger.debug("Target TB BBox: %s", tb_bbox)
            self.logger.debug("Vertical threshold: %.4f", vertical_threshold)

            closest_key = None
            closest_text = None

            for sn_bbox, sn_text in side_note_datas.items():
                sn_x0, sn_y0, sn_x1, sn_y1 = sn_bbox
        
                # Check if sidenote is to the immediate left or right
                is_left = sn_x1 <= tb_x0
                is_right = sn_x0 >= tb_x1
                if not (is_left or is_right):
                    continue

                # Compare Y positions of top-right corners (you said y1 is top)
                if abs(sn_y1 - tb_y1) <= vertical_threshold:
                    closest_key = sn_bbox
                    closest_text = sn_text
                    self.logger.debug("Matched side note: %s", closest_text)
                    break  # found one match, stop

            if closest_key:
                del side_note_datas[closest_key]
                self.logger.debug("Removing matched side note BBox from the side note datas: %s", closest_key)

            return closest_text
        
        except Exception as e:
            self.logger.exception("Error finding closest side note for TB BBox %s: %s", tb_bbox, e)
            return None

    def findType(self,text):
        group_re = re.compile(
                r'^\s*(\(\s*(?:[1-9]\d{0,2}|[A-Z]{1,3}|(?:CM|CD|D?C{0,3})?'
                r'(?:XC|XL|L?X{0,3})?(?:IX|IV|V?I{0,3}))\s*\))\s*(.*)',
                re.IGNORECASE
            )
    
        match = group_re.match(text.strip())
        if match:
            value_with_paren = match.group(1)  
            rest_text = match.group(2)     
            return "SUBSEC", value_with_paren, rest_text
        
        return None, "", text
    
    def find_value_and_text(self, text):

        if not text:
            return "", ""

        patterns = [

            re.compile(
                r'^('
                    r'[1-9]\d{0,3}'
                    r'(?:\.[1-9]\d{0,3}){1,5}'
                    r'\.?'
                r')'
                r'(?:\s+|$)(.*)$',
                re.IGNORECASE
            ),

            re.compile(
                r'^('
                    r'[1-9]\d{0,3}[A-Z]?'
                    r'(?:-[A-Z]+)?'
                    r'\.'
                r')'
                r'(?:\s+|$)(.*)$',
                re.IGNORECASE
            ),

            re.compile(
                r'^('
                    r'(?:\([A-Za-z]{1,5}\))'
                    r'|(?:[A-Za-z]{1,5}[.)])'
                r')'
                r'(?:\s+|$)(.*)$',
                re.IGNORECASE
            ),

            re.compile(
                r'^('
                    r'(?:\([IVXLCDMivxlcdm]{1,10}\))'
                    r'|(?:[IVXLCDMivxlcdm]{1,10}[.)])'
                r')'
                r'(?:\s+|$)(.*)$',
                re.IGNORECASE
            ),

            re.compile(
                r'^('
                    r'(?:\([1-9]\d{0,3}\))'
                    r'|(?:[1-9]\d{0,3}[.)]?)'
                r')'
                r'(?:\s+|$)(.*)$',
                re.IGNORECASE
            ),
        ]

        for pattern in patterns:

            match = pattern.match(text)

            if match:

                value = match.group(1).strip()
                rest_text = match.group(2).strip()

                return value, rest_text

        return "", text

    def get_hierarchy_level(self, category):
        if category not in self.hierarchy:
            self.hierarchy.append(category)
        return self.hierarchy.index(category)
        
    def addSection(self, tb, side_note_datas, page_height, has_side_notes):
        try:
            text = self.normalize_text(tb.extract_text_from_tb())
            if not self.is_body_added:
                # self.is_preamble_reached = True
                tab_level = self.get_tab_level('BODY')
                if tab_level is not None:
                    self.builder += "\n" + ("\t" * tab_level) + f"BODY"
                    self.is_body_added = True
                    self.curr_tab_level = tab_level

            self.curr_tab_level = self.get_hierarchy_level('SEC')
            is_sentence_completed = text.endswith(self.sentence_completion_punctuation)
            self.previous_sentence_end_status = is_sentence_completed
            side_note_text = self.find_closest_side_note(tb.coords, side_note_datas,page_height)
            self.logger.debug("Side note matched for section text [%s] : %s",text, side_note_text)
            if not has_side_notes:
                match = re.match(r'^(\s*\d{1,3}[A-Z]*(?:-[A-Z]+)?\.\s*)(.*)', text.strip())
                prefix = match.group(1)
                rest_text = match.group(2).strip()
                rest_text_type, value, remain_text = self.findType(rest_text)
                if rest_text:
                    if rest_text_type is None:
                        self.builder += "\n" + ("\t" * (self.curr_tab_level))+f"SEC {prefix}"
                        self.builder += "\n" + ("\t" * (self.curr_tab_level+1)) + f"{remain_text}"  #<br>  
                    else:
                        self.builder += "\n" + ("\t" * (self.curr_tab_level))+f"SEC {prefix}"
                        self.curr_tab_level = self.get_hierarchy_level(rest_text_type)

                        self.builder  += "\n" + ("\t" * (self.curr_tab_level))+f"{rest_text_type} {value}"
                        self.builder  += "\n" + ("\t" * (self.curr_tab_level+1))+f"{remain_text}"
                else:
                        self.builder += "\n" + ("\t" * (self.curr_tab_level))+f"SEC {prefix}"
                        self.previous_sentence_end_status = True
                # print(self.previous_sentence_end_status, text)
                if self.previous_sentence_end_status and self.footnote_to_add:
                    # self.logger.info('footnote 6')
                    self.add_footnote(footnotes =  self.footnote_to_add)
                    self.footnote_to_add = None
                return
            if side_note_text:
                match = re.match(r'^(\s*\d{1,3}[A-Z]*(?:-[A-Z]+)?\.\s*)(.*)', text.strip())
                if match:
                    prefix = match.group(1)
                    short_title = self.normalize_text((side_note_text or "").strip()) or ""
                    rest_text = match.group(2).strip()
                    rest_text_type, value, remain_text = self.findType(rest_text)
                    if rest_text:
                        if rest_text_type is None:
                            self.builder += "\n" + ("\t" * (self.curr_tab_level))+f"SEC {prefix} - {short_title}"
                            self.builder += "\n" + ("\t" * (self.curr_tab_level+1)) + f"{remain_text}"  #<br>  
                        else:
                            self.builder += "\n" + ("\t" * (self.curr_tab_level))+f"SEC {prefix} - {short_title}"
                            self.curr_tab_level = self.get_hierarchy_level(rest_text_type)

                            self.builder  += "\n" + ("\t" * (self.curr_tab_level))+f"{rest_text_type} {value}"
                            self.builder  += "\n" + ("\t" * (self.curr_tab_level+1))+f"{remain_text}"
                    else:
                        self.builder += "\n" + ("\t" * (self.curr_tab_level))+f"SEC {prefix} - {short_title}"
                        self.previous_sentence_end_status = True
                # print(self.previous_sentence_end_status, text)
                if self.previous_sentence_end_status and self.footnote_to_add:
                    # self.logger.info('footnote 7')
                    self.add_footnote(footnotes =  self.footnote_to_add)
                    self.footnote_to_add = None
                
            else:
                check_re = re.compile(
                        r'^'
                        r'(\s*\d{1,3}[A-Z]*(?:-[A-Z]+)?\.\s*)'   # Group 1: Number/marker like '13.'
                        r'(?!\s*\([^)]+\))'                       # Negative lookahead: fail if second group starts with anything in parentheses
                        r'(.*?(?:\.\s*(?:-|—)?|:\s*(?:-|—)?))'   # Group 2: Text up to first . or : optionally followed by -/—
                        r'(.*)$',                                 # Group 3: Rest of text
                        re.DOTALL
                    )
                match = re.match(check_re, text.strip())
                if match:
                    prefix = match.group(1)
                    short_title = match.group(2).strip().rstrip("-—")
                    rest_text = match.group(3).strip()
                    rest_text_type, value, remain_text = self.findType(rest_text)
                    if rest_text_type is None:
                        self.builder += "\n" + ("\t" * (self.curr_tab_level))+f"SEC {prefix} - {short_title}"
                        self.builder += "\n" + ("\t" * (self.curr_tab_level+1)) + f"{remain_text}"  #<br>  
                    else:
                        self.builder += "\n" + ("\t" * (self.curr_tab_level))+f"SEC {prefix} - {short_title}"
                        self.curr_tab_level = self.get_hierarchy_level(rest_text_type)
                        self.builder  += "\n" + ("\t" * (self.curr_tab_level))+f"{rest_text_type} {value}"
                        self.builder  += "\n" + ("\t" * (self.curr_tab_level+1))+f"{remain_text}"
                    # print(self.previous_sentence_end_status, text)
                    if self.previous_sentence_end_status and self.footnote_to_add:
                        # self.logger.info('footer 8')
                        self.add_footnote(footnotes =  self.footnote_to_add)
                        self.footnote_to_add = None
                    return
                
                match = re.match(
                                r'^(\s*\d{1,3}[A-Z]*(?:-[A-Z]+)?\.\s*)(.*)',
                                text.strip()
                            )

                if match:
                    prefix = match.group(1).strip()
                    short_title = match.group(2).strip()

                    # ---------- CASE 1: short_title starts with bullet ----------
                    if short_title and re.match(r'^\s*\([^)]*\)', short_title):
                        # Build section header without consuming bullet text
                        self.builder += (
                            "\n"
                            + ("\t" * self.curr_tab_level)
                            + f"SEC {prefix} -"
                        )
                        # Treat bullet text as rest_text
                        rest_text_type, value, remain_text = self.findType(short_title)

                        if rest_text_type is None:
                            self.builder += (
                                "\n"
                                + ("\t" * (self.curr_tab_level + 1))
                                + f"{remain_text}"
                            )
                        else:
                            self.curr_tab_level = self.get_hierarchy_level(rest_text_type)
                            self.builder += (
                                "\n"
                                + ("\t" * self.curr_tab_level)
                                + f"{rest_text_type} {value}"
                            )
                            self.builder += (
                                "\n"
                                + ("\t" * (self.curr_tab_level + 1))
                                + f"{remain_text}"
                            )

                        self.section_shorttitle_notend_status = False
                    
                    # ---------- CASE 2: normal short title ----------
                    else:
                        if not re.search(r'\{\{\^\{\{FOOTNOTE\s*\d+\}\}\}\}\s*$', text):
                            if short_title:
                                self.builder += (
                                    "\n"
                                    + ("\t" * self.curr_tab_level)
                                    + f"SEC {prefix} - {short_title}"
                                )
                            else:
                                self.builder += (
                                    "\n"
                                    + ("\t" * self.curr_tab_level)
                                    + f"SEC {prefix} -"
                                )

                            self.section_shorttitle_notend_status = True
                        else:
                            self.builder += (
                                "\n"
                                + ("\t" * self.curr_tab_level)
                                + f"SEC {prefix} - {short_title}"
                            )
                            self.section_shorttitle_notend_status = False
                            self.previous_sentence_end_status = True
                # print(self.previous_sentence_end_status, text)
                if self.previous_sentence_end_status and self.footnote_to_add:
                    # self.logger.info('footnote 9')
                    self.add_footnote(footnotes =  self.footnote_to_add)
                    self.footnote_to_add = None

        except Exception as e:
            self.logger.exception("Error while adding section [%s]: %s",text, e)
    
    def addArticle(self, line):
        is_sentence_completed = line.endswith(self.sentence_completion_punctuation)
        matched, val = self.is_article(line)
        if matched:
            if not self.is_body_added:
                tab_level = self.get_tab_level('BODY')
                if tab_level is not None:
                    self.builder += "\n" + ("\t" * tab_level) + f"BODY\n"
                    self.is_body_added = True
                    self.curr_tab_level = tab_level

            tab_level = self.get_tab_level('ART')
            if tab_level is not None:
                if not self.is_schedule_open:
                    self.builder += "\n" + ("\t" * tab_level) + f"ART {val}"
                    self.curr_tab_level = tab_level
                    self.hierarchy = ['ART']
                    self.is_schedule_open = False
                    
                else:
                    self.clear_article_hierarchy(level = 'SUBPART')
                    self.curr_tab_level = self.get_hierarchy_level('SUBPART')
                    self.builder += "\n" + ("\t" * self.curr_tab_level) + f"ART {val}"
            self.previous_sentence_end_status = True
        else:
            if not self.previous_sentence_end_status:
                self.builder += " " + line
            else:
                self.builder += "\n" + ("\t" * (self.curr_tab_level+1)) + f"{line}"
            self.previous_sentence_end_status = is_sentence_completed
    
    def clear_article_hierarchy(self, level):
        try:
            if level in self.hierarchy:
                idx = self.hierarchy.index(level)
                self.hierarchy = self.hierarchy[:idx]
        except Exception as e:
            self.logger.error(f'while try to clear article hierarchy- {e}')

    def addSubsection(self, text):
        try:
            self.previous_sentence_end_status = text.endswith(self.sentence_completion_punctuation)
            self.curr_tab_level = self.get_hierarchy_level('SUBSEC')
            value, remain_text = self.find_value_and_text(text)
            self.builder  += "\n" + ("\t" * (self.curr_tab_level))+f"SUBSEC {value}"
            value2, remain_text2 = self.find_value_and_text(remain_text)
            if value2 == "":
                self.builder  += "\n" + ("\t" * (self.curr_tab_level+1))+f"{remain_text2}"
            else:
                self.curr_tab_level = self.get_hierarchy_level('PARA')
                self.builder  += "\n" + ("\t" * (self.curr_tab_level))+f"PARA {value2}"
                self.builder  += "\n" + ("\t" * (self.curr_tab_level+1))+f"{remain_text2}"
            # print(self.previous_sentence_end_status, text)
            if self.previous_sentence_end_status and self.footnote_to_add:
                # self.logger.info('footnote 10')
                self.add_footnote(footnotes =  self.footnote_to_add)
                self.footnote_to_add = None
        except Exception as e:
            self.logger.exception("Error while adding subsection [%s]: %s",text, e)
    
    def addPara(self, text):
        try:
            self.previous_sentence_end_status = text.endswith(self.sentence_completion_punctuation)
            self.curr_tab_level = self.get_hierarchy_level('PARA')
            value, remain_text = self.find_value_and_text(text)
            self.builder  += "\n" + ("\t" * (self.curr_tab_level))+f"PARA {value}"
            value2, remain_text2 = self.find_value_and_text(remain_text)
            if value2 == "":
                self.builder  += "\n" + ("\t" * (self.curr_tab_level+1))+f"{remain_text2}"
            else:
                self.curr_tab_level = self.get_hierarchy_level('SUBPARA')
                self.builder  += "\n" + ("\t" * (self.curr_tab_level))+f"SUBPARA {value2}"
                self.builder  += "\n" + ("\t" * (self.curr_tab_level+1))+f"{remain_text2}"
            # print(self.previous_sentence_end_status, text)
            if self.previous_sentence_end_status and self.footnote_to_add:
                # self.logger.info('footnote 11')
                self.add_footnote(footnotes =  self.footnote_to_add)
                self.footnote_to_add = None
        except Exception as e:
            self.logger.exception("Error while adding para [%s]: %s",text, e)


    def addSubpara(self, text):
        try:
            self.previous_sentence_end_status = text.endswith(self.sentence_completion_punctuation)
            self.curr_tab_level = self.get_hierarchy_level('SUBPARA')
            value, remain_text = self.find_value_and_text(text)
            self.builder  += "\n" + ("\t" * (self.curr_tab_level))+f"SUBPARA {value}"
            self.builder  += "\n" + ("\t" * (self.curr_tab_level+1))+f"{remain_text}"
            # print(self.previous_sentence_end_status, text)
            if self.previous_sentence_end_status and self.footnote_to_add:
                # self.logger.info('foonote 12')
                self.add_footnote(footnotes =  self.footnote_to_add)
                self.footnote_to_add = None
        except Exception as e:
            self.logger.exception("Error while adding subpara [%s]: %s",text, e)
    
    def addFigure(self, tb):
        try:
            if tb.figname in self.unique_image:
                img_path = self.unique_image[tb.figname] \
                    .get("path","")
                if img_path:
                    img_name = os.path.basename(img_path)
                    self.builder  += "\n" + ("\t" * (self.curr_tab_level))+f"{{{{IMG media/{img_name}}}}}"
        
        except Exception as e:
            self.logger.exception('Error while adding Figure [%s]: %s',tb.figname, e)
    
    def addUnlabelled(self, text):
        try:
            is_sentence_completed = text.endswith(self.sentence_completion_punctuation)
            if not self.is_preamble_reached and text:
                is_matched = self.check_preamble_start(text)
                if is_matched:
                    self.is_preamble_reached = True
                    self.table_visited_lastly = False
                    if self.is_body_added:
                        self.builder = ""
                        self.is_body_added = False
                        self.set_empty_hierarchy()
                    tab_level = self.get_tab_level('PREAMBLE')
                    if tab_level is not None:
                        self.builder += ("\t" * tab_level) + "PREAMBLE"
                        self.curr_tab_level = tab_level
                        self.builder += "\n" +  ("\t" * (self.curr_tab_level+1) + text)
                        self.previous_sentence_end_status = is_sentence_completed
                        # print(self.previous_sentence_end_status, text)
                        if self.previous_sentence_end_status and self.footnote_to_add:
                            # self.logger.info('footnote 13')
                            self.add_footnote(footnotes =  self.footnote_to_add)
                            self.footnote_to_add = None
                        return
                    else:
                        self.curr_tab_level += 1
                        self.builder +=  ("\t" * (self.curr_tab_level)) + "PREAMBLE"
                        self.builder += "\n" + ("\t" * (self.curr_tab_level+1) + text)
                        self.previous_sentence_end_status = is_sentence_completed
                        # print(self.previous_sentence_end_status, text)
                        if self.previous_sentence_end_status and self.footnote_to_add:
                            # self.logger.info('footnote 14')
                            self.add_footnote(footnotes =  self.footnote_to_add)
                            self.footnote_to_add = None
                        return
                return
            else:
                if re.fullmatch(self.act_end_re, text):
                    self.is_act_ended = True
                    if self.docend_symbol and self.is_act_ended:
                        return
                last_tag = self.get_last_hierarchy_tag()
                if last_tag == 'SUBPART':
                    self.builder += "\n" + ("\t" * (self.curr_tab_level+1) + text)
                elif self.table_visited_lastly:
                    self.builder += "\n" + ("\t" * (self.curr_tab_level+1) + text)
                    self.table_visited_lastly = False
                elif self.section_shorttitle_notend_status:
                    if re.search(r'\{\{\^\{\{FOOTNOTE\s*\d+\}\}\}\}\s*$', text):
                        self.builder += " " + text
                        self.previous_sentence_end_status = True
                        self.section_shorttitle_notend_status = False  
                        if  self.previous_sentence_end_status and self.footnote_to_add:
                            self.add_footnote(footnotes = self.footnote_to_add)
                            self.footnote_to_add = None
                        return

                    match = COMBINED_RE.match(text)

                    if match and match.group("bullet"):
                        rest_text = text.strip()

                        rest_text_type, value, remain_text = self.findType(rest_text)

                        if rest_text_type is None:
                            self.builder += "\n" + ("\t" * (self.curr_tab_level + 1)) + f"{remain_text}"
                        else:
                            self.curr_tab_level = self.get_hierarchy_level(rest_text_type)
                            self.builder += "\n" + ("\t" * self.curr_tab_level) + f"{rest_text_type} {value}"
                            self.builder += "\n" + ("\t" * (self.curr_tab_level + 1)) + f"{remain_text}"

                        self.section_shorttitle_notend_status = False
                        self.previous_sentence_end_status = is_sentence_completed

                    elif match and match.group("title"):
                        self.builder += " " + match.group("title").strip().rstrip("-—")

                        rest_text = match.group("rest").strip()
                        rest_text_type, value, remain_text = self.findType(rest_text)

                        if rest_text_type is None:
                            self.builder += "\n" + ("\t" * (self.curr_tab_level + 1)) + f"{remain_text}"
                        else:
                            self.curr_tab_level = self.get_hierarchy_level(rest_text_type)
                            self.builder += "\n" + ("\t" * self.curr_tab_level) + f"{rest_text_type} {value}"
                            self.builder += "\n" + ("\t" * (self.curr_tab_level + 1)) + f"{remain_text}"

                        self.section_shorttitle_notend_status = False
                        self.previous_sentence_end_status = is_sentence_completed

                    else:
                        self.builder += " " + text
                        self.previous_sentence_end_status = is_sentence_completed
                    # print(self.previous_sentence_end_status, text)
                    if  self.previous_sentence_end_status and self.footnote_to_add:
                        # self.logger.info('foonote 15')
                        self.add_footnote(footnotes = self.footnote_to_add)
                        self.footnote_to_add = None

                else:
                    if not self.previous_sentence_end_status:
                        self.builder += " " + text      
                    else:
                        self.builder += "\n" + ("\t" * (self.curr_tab_level+1)) + f"{text}"
                    self.previous_sentence_end_status = is_sentence_completed
                    # print('suspected area', self.footnote_to_add)
                    # print(self.previous_sentence_end_status, text)
                    if  self.previous_sentence_end_status and self.footnote_to_add:
                        # self.logger.info('footnote 16')
                        self.add_footnote(footnotes = self.footnote_to_add)
                        self.footnote_to_add = None
            
        except Exception as e:
            self.logger.exception("Error while adding unlabelled [%s]: %s",text, e)
    
    def get_last_hierarchy_tag(self):
        if len(self.hierarchy) == 0:
            return ""
        else:
            return self.hierarchy[-1]
         
    def addTable(self, table):
        try:
          self.previous_sentence_end_status = True
          self.table_visited_lastly = True
          table_tab = self.curr_tab_level + 1
          self.builder += "\n" + ("\t" * (table_tab))+f"TABLE"

          for index, row in table.iterrows():
            row_tab = table_tab + 1
            self.builder += "\n" + ("\t" * (row_tab))+f"TR"
            for col in table.columns:
                cell_tab = row_tab + 1
                if index == 0:
                    self.builder += "\n" + ("\t" * (cell_tab))+f"TH"
                else:
                    self.builder += "\n" + ("\t" * (cell_tab))+f"TC"
                value = row[col]
                value = str(value)
                text = self.normalize_text(value)
                text = self.clean_text(text)
                value_tab = cell_tab + 1
                indent =  ("\t" * (value_tab))
                if isinstance(text, list):
                    text = "\n".join(text)
                self.builder += "\n" + textwrap.indent(text, indent)

        except Exception as e:
            self.logger.exception("Error while adding table in html - %s .\nTable preview\n %s",e, table.head().to_string(index=False))

    def addAmendment(self, label, tb, side_note_datas, page_height):
        try:
            text = self.normalize_text(tb.extract_text_from_tb())
            if re.fullmatch(self.act_end_re, text):
                    self.is_act_ended = True
                    if self.docend_symbol and self.is_act_ended:
                        return
            is_sentence_completed = text.endswith(self.sentence_completion_punctuation)
            if len(label) > 1:
                if label[1]=="title":
                    self.logger.debug("The text [%s] is a title block of Amendments.",text)
                    if self.is_section_amended(text):
                        self.add_section_amendment(text, tb, side_note_datas, page_height)
                        return
                    if not self.previous_sentence_end_status:
                        self.builder += " " + text
                    else:
                        self.builder += "\n" + ("\t" * (self.curr_tab_level+1)) + f"{text}"
                    self.previous_sentence_end_status = is_sentence_completed  
                    return
            if self.is_section_amended(text):
                self.add_section_amendment(text, tb, side_note_datas, page_height)
                return
            else:
                if not self.previous_sentence_end_status:
                    self.builder += " " + text      
                else:
                    self.builder += "\n" + ("\t" * (self.curr_tab_level+1)) + f"{text}"
                self.previous_sentence_end_status = is_sentence_completed
        except Exception as e:
            self.logger.warning("Exception while adding amendment [%s]: %s",text, e)

    def add_section_amendment(self, text, tb, side_note_datas, page_height):
        try:
            is_sentence_completed = text.endswith(self.sentence_completion_punctuation)
            side_note_text = self.find_closest_side_note(tb.coords, side_note_datas,page_height)
            self.logger.debug("Side note matched for amendment text [%s] : %s",text, side_note_text)
            if side_note_text:
                self.curr_tab_level = self.get_hierarchy_level('SUBSEC')
                self.builder  += "\n" + ("\t" * (self.curr_tab_level))+f"SUBSEC - {side_note_text}"
                self.builder  += "\n" + ("\t" * (self.curr_tab_level+1))+f"{text}"
                self.previous_sentence_end_status = is_sentence_completed
            else:
                self.curr_tab_level = self.get_hierarchy_level('SUBSEC')
                self.builder  += "\n" + ("\t" * (self.curr_tab_level))+f"SUBSEC"
                self.builder  += "\n" + ("\t" * (self.curr_tab_level+1))+f"{text}"
                self.previous_sentence_end_status = is_sentence_completed
        except Exception as e:
            self.logger.warning("Exception while adding section amendment [%s]: %s",text, e)
    
    def is_section_amended(self, text):
        match = re.match(
                r'''^\s*['"]?              # optional leading ' or "
                    (\d{1,3}[A-Z]*(?:-[A-Z]+)?\.\s*)   # your numbering token
                    (.*)                   # rest of the text
                ''',
                text.strip(),
                re.VERBOSE
            )
        return match

    def remove_unwanted_sidenotes(self, side_note_datas):
        pattern = re.compile(r'^(\d+\s+of\s+\d+\.|Ord\.?\s*\d+\s+of\s+\d+\. | Ordinance\.?\s*\d+\s+of\s+\d+\.)$')
        for sn_bbox, sn_text in list(side_note_datas.items()):
            if pattern.search(sn_text.strip()):
                try:
                    del side_note_datas[sn_bbox]
                    self.logger.debug("Removed unwanted side note: %s", sn_text)
                except Exception as e:
                    self.logger.warning("Exception while removing unwanted side notes: %s", e)

    def contains_footnote(self, text):
        pattern = re.compile(
            r'\{\{\^\{\{FOOTNOTE\s+(\d+)\}\}\}\}'
        )

        matches = pattern.findall(text)

        return matches if matches else []
        
    def add_footnote(self, footnotes):
        if not footnotes:
            return
        
        if isinstance(footnotes, str):
            footnotes = [footnotes]
        
        for footnote in footnotes:
            if footnote not in self.all_footnote_text:
                continue

            rawlines = self.all_footnote_text[footnote].split('\n')
            if not rawlines:
                continue
            arranged_text = []
            current_sentence = ""

            for line in rawlines:
                if current_sentence:
                    current_sentence += " " + line
                else:
                    current_sentence = line
                
                is_sentence_completed =( current_sentence.endswith(
                                        self.sentence_completion_punctuation)

                                        and
                                        not current_sentence.endswith('w.e.f.')
                                        
                                        )
                
                if is_sentence_completed:
                    arranged_text.append(current_sentence.strip())
                    current_sentence = ""
            
            if current_sentence:
                arranged_text.append(current_sentence.strip())

            self.builder  += "\n" + ("\t" * (self.curr_tab_level+1))+f"FOOTNOTE {footnote}"
            for textline in arranged_text:
                self.builder  += "\n" + ("\t" * (self.curr_tab_level+2))+f"{textline}"
    
    def build(self, page, has_side_notes) :
        self.remove_unwanted_sidenotes(page.side_notes_datas)
        visited_for_table = set()
       
        all_items = list(page.all_tbs.items())
        for idx, (tb, label) in enumerate(all_items):
            if self.is_act_ended and self.docend_symbol:
                break
            if label == "header" or label == "footer" \
                or label == "footnote":
               continue
            
            if label in ('figure',) and (tb.figname not in self.unique_image):
                self.logger.warning("The figure may be header or junk image, skipping...")
                continue
            
            if not ((isinstance(label, tuple) and label[0] == "table")):
                if self.pending_table is not None and len(self.pending_table) <= 2:
                    self.addTable(self.pending_table[0])
                    self.pending_table = None
 
            if label in ('title', 'level1', 'level2', 
                         'level3', 'level4', 'figure'):
                if self.footnote_to_add:
                    # self.logger.info('foonote 18')
                    self.add_footnote(footnotes = self.footnote_to_add)
                    self.footnote_to_add = None
             
            text = ''
            if label not in ('figure',):
                text = self.normalize_text(tb.extract_text_from_tb())

            self.is_footnote_detected = self.contains_footnote(text)
            if self.is_footnote_detected: 
                if not self.footnote_to_add:
                    self.footnote_to_add = self.is_footnote_detected
                else:
                    self.footnote_to_add.extend(self.is_footnote_detected)
            
            if isinstance(label, tuple) and label[0] == "table":
                table_id = label[1]
                if table_id not in visited_for_table:
                    table_obj = page.tabular_datas.tables.get(table_id)
                    table_width = page.tabular_datas.get_table_width(table_id)

                    if table_obj is not None:
                        if self.pending_table is None:
                            self.pending_table = [table_obj, table_width]
                        
                        else:
                            if self.is_table_continuation(table_obj, table_width):
                                self.merge_tables(table_obj, table_width)#, html_builder=self)
                               
                            else:
                                self.addTable(self.pending_table[0])
                                self.pending_table = [table_obj, table_width]

                    visited_for_table.add(table_id)

            elif label == "title":
                self.addTitle(tb)
            elif label == "level1":
                self.table_visited_lastly = False
                self.addSection(tb,page.side_notes_datas,page.pg_height, has_side_notes)
            elif label == "level2":
                self.table_visited_lastly = False
                self.addSubsection(text)
            elif label == "level3":
                self.table_visited_lastly = False
                self.addPara(text)
            elif label == "level4":
                self.table_visited_lastly = False                
                self.addSubpara(text)
            elif label == "figure":
               self.addFigure(tb)
            elif label is None:
                if not self.is_pg_num(tb,page.pg_width):
                    self.addUnlabelled(text)
    
    def flushTables(self):
        if self.pending_table is not None and len(self.pending_table) <= 2:
            self.addTable(self.pending_table[0])
            self.pending_table = None

    def set_empty_hierarchy(self):
        self.hierarchy = []
        self.curr_tab_level = 0

    def is_pg_num(self,tb,pg_width):
        if  tb.width < 0.04 * pg_width and self.check_isDigit(tb):
            self.logger.debug("The unlabelled textbox [%s] is classified as pg_num",tb.extract_text_from_tb())
            return True
        return False
    
    def check_isDigit(self, tb):
      text = tb.extract_text_from_tb()
      if not text:
          return False

      raw = text.strip()
      cleaned = raw.lower()

      # --- Reject common bullet forms: 'i.', 'ii)', '1.' followed by text ---
      if re.match(r"^\(?[ivxlcdm0-9]+\)?[.)]\s+\w+", cleaned, re.IGNORECASE):
          return False

      # Remove enclosing brackets/parentheses/braces only if whole thing is wrapped
      stripped = re.sub(r"^[\(\[\{]\s*|\s*[\)\]\}]$", "", cleaned)

      # Case 1: Arabic numbers
      if re.fullmatch(r"\d{1,4}", stripped):
          return True

      # Case 2: Roman numerals (valid strict form, 1–3999)
      roman_pattern = r"^(m{0,3})(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$"
      if re.fullmatch(roman_pattern, stripped, flags=re.IGNORECASE):
          return True
      
      return False
