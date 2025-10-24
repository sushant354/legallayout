import re
import math
from collections import OrderedDict
import numpy as np
import logging
import pandas as pd
from difflib import SequenceMatcher
from sklearn.cluster import DBSCAN
from bs4 import BeautifulSoup
import copy

from .SentenceEndDetector import LegalSentenceDetector
from .NormalizeText import NormalizeText

RELEVANT_TAGS = {"body", "section", "p", "table", "tr", "td", "a", "blockquote", "br",
                 "h4", "center", "li"}
VOID_TAGS = {"br"}

class Acts:
    
    def __init__(self, sentence_completion_punctuation = tuple(), pdf_type = None):
        self.logger = logging.getLogger(__name__)
        self.pdf_type = pdf_type
        self.pending_text = ""
        self.pending_tag = None
        self.sentence_completion_punctuation = sentence_completion_punctuation
        self.stack_for_section = []
        self.hierarchy = []
        self.pending_table = None
        self.is_real_sentence_end =LegalSentenceDetector().is_real_sentence_end
        self.previous_sentence_end_status = True
        self.is_preamble_reached = False
        self.is_body_added = False
        self.normalize_text = NormalizeText().normalize_text
        self.min_word_threshold_tableRows = 2
        self.table_terminators = {".", "?", "!"} #";", ":",
        self.builder = ""
        self.main_builder = ""
        self.is_schedule_open = False
        self.curr_tab_level = 0
        self.is_act_ended = False
        self.roman_re  = r"(?:M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3}))"

        self.tab_level = {
            0 : ['PREFACE', 'PREAMBLE', 'CHAP', 'BODY', 'SCHEDULE', 'ART']
        }
    
    def get_tab_level(self, category):
        for key, value in self.tab_level.items():
            if category in value :
                return key
        return None
    
    def check_preamble_start(self, text):
        pattern = re.compile(r'^\s*(?:A\s+)?An\s+Act\s*(?:\|\s*BE\s+it\s+enacted\s+by\b)?', re.I)
        match = re.search(pattern, text)
        return bool(match)
    
    def close_bluebell(self):
       bluebell = self.main_builder + self.builder 
       return bluebell
    
    def get_content(self):
        # self.flushPrevious()
        self.flushTables()
        return self.close_bluebell()
    
    def is_chapter(self, text):
        pattern = rf"^\s*chapter\s+(\d+|{self.roman_re})"
        match = re.match(pattern, text, re.IGNORECASE)
        if match:
            return True, match.group(1)  # Return True and the number/roman
        return False, None

    def is_article(self, text):
        pattern = rf"^\s*article\s+(\d+|{self.roman_re})"
        match = re.match(pattern, text, re.IGNORECASE)
        if match:
            return True, match.group(1)
        return False, None

    def is_schedule(self, text):
        ordinals = [
        "first", "second", "third", "fourth", "fifth",
        "sixth", "seventh", "eighth", "ninth", "tenth",
        "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth",
        "sixteenth", "seventeenth", "eighteenth", "nineteenth", "twentieth"
        ]
        ordinals_re = r"(?:{})".format("|".join(ordinals))

        # Numbers 1–20
        numbers_re = r"(?:[1-9]|1[0-9]|20)"

        # Single regex with capturing group
        pattern = rf"(?:schedule\s*({ordinals_re}|{numbers_re}|{self.roman_re})|" \
                rf"({ordinals_re}|{numbers_re}|{self.roman_re})\s*schedule)"

        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return True

        return False


    def addTitle(self, tb):
        try:
          for textline in tb.tbox.findall('.//textline'):
                line_texts = []
                for text in textline.findall('.//text'):
                    if text.text:
                        line_texts.append(text.text)
                line = ''.join(line_texts).replace("\n", " ").strip()

                if re.fullmatch(r'—{3,}', line):
                        self.is_act_ended = True
                        break
                
                if self.is_preamble_reached and line:

                    # matched, val = self.is_article(line)
                    # if matched:
                    #     if not self.is_body_added:
                    #         tab_level = self.get_tab_level('BODY')
                    #         if tab_level is not None:
                    #             self.builder += "\n" + ("\t" * tab_level) + f"BODY\n"
                    #             self.is_body_added = True
                    #             self.curr_tab_level = tab_level

                    #     tab_level = self.get_tab_level('ART')
                    #     if tab_level is not None:
                    #         if not self.is_schedule:
                    #             self.builder += "\n" + ("\t" * tab_level) + f"ART {val}"
                    #             self.curr_tab_level = tab_level
                    #             self.hierarchy = ['ART']
                    #             self.is_schedule_open = False
                    #             continue
                    #         else:
                    #             self.curr_tab_level = self.get_hierarchy_level('SUBPART')
                    #             self.builder += "\n" + ("\t" * self.curr_tab_level) + f"ART {val}"
                    #             continue
                    
                    matched = self.is_schedule(line)
                    if matched:
                        if not self.is_body_added:
                            tab_level = self.get_tab_level('BODY')
                            if tab_level is not None:
                                self.builder += "\n" + ("\t" * tab_level) + f"BODY\n"
                                self.is_body_added = True
                                self.curr_tab_level = tab_level

                        tab_level = self.get_tab_level('SCHEDULE')
                        if tab_level is not None:
                            self.builder += "\n" + ("\t" * tab_level) + f"SCHEDULE {line}"
                            self.curr_tab_level = tab_level
                            self.hierarchy = ['SCHEDULE']
                            self.is_schedule_open = True
                            continue



                    if self.is_schedule_open:
                        self.curr_tab_level = self.get_hierarchy_level('SUBPART')
                        self.builder += "\n" + ("\t" * self.curr_tab_level) + f"SUBPART - {line}"
                        continue

                    matched, val = self.is_chapter(line)
                    if matched:
                        if not self.is_body_added:
                            tab_level = self.get_tab_level('BODY')
                            if tab_level is not None:
                                self.builder += "\n" + ("\t" * tab_level) + f"BODY\n"
                                self.is_body_added = True
                                self.curr_tab_level = tab_level

                        tab_level = self.get_tab_level('CHAP')
                        if tab_level is not None:
                            self.builder += "\n" + ("\t" * tab_level) + f"CHAP {val} -"
                            self.curr_tab_level = tab_level
                            self.hierarchy = ['CHAP']
                            self.is_schedule_open = False
                            continue
                    
                    
                    
                    self.builder += " " + line
                
                else:
                    is_matched = self.check_preamble_start(line)
                    if is_matched:
                        self.is_preamble_reached = True
                        tab_level = self.get_tab_level('PREAMBLE')
                        if tab_level is not None:
                            self.builder += ("\t" * tab_level) + "PREAMBLE\n"
                            self.curr_tab_level = tab_level
                            self.builder += ("\t" * (self.curr_tab_level+1) + line)
                            continue
                        else:
                            self.curr_tab_level += 1
                            self.builder += ("\t" * (self.curr_tab_level)) + "PREAMBLE\n"
                            self.builder += ("\t" * (self.curr_tab_level+1) + line)
                            continue
                        
                    
        except Exception as e:
          self.logger.exception("Error while adding title - [%s] in html: %s",tb.extract_text_from_tb(),e)
    
    def find_closest_side_note(self, tb_bbox, side_note_datas, page_height, vertical_threshold_ratio=0.005):
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
        group_re = re.compile(r'^(\(\s*[^\s\)]+\s*\))\s*(.*)', re.IGNORECASE)
    
        match = group_re.match(text.strip())
        if match:
            value_with_paren = match.group(1)  
            rest_text = match.group(2)         
            return "SUBSEC", value_with_paren, rest_text
        
        return None, "", text
    
    def find_value_and_text(self,text):
        group_re = re.compile(r'^(\(\s*[^\s\)]+\s*\))\s*(.*)', re.IGNORECASE)
    
        match = group_re.match(text.strip())
        if match:
            value_with_paren = match.group(1)  
            rest_text = match.group(2)         
            return value_with_paren, rest_text
        
        return "", text  

    def get_hierarchy_level(self, category):
        try:
            return self.hierarchy.index(category)
        except Exception as e:
            self.hierarchy.append(category)
            return self.hierarchy.index(category)
        
    def addSection(self, tb, side_note_datas, page_height):
        try:
            text = self.normalize_text(tb.extract_text_from_tb())
            if not self.is_body_added:
                tab_level = self.get_tab_level('BODY')
                if tab_level is not None:
                    self.builder += "\n" + ("\t" * tab_level) + f"BODY\n"
                    self.is_body_added = True
                    self.curr_tab_level = tab_level

            self.curr_tab_level = self.get_hierarchy_level('SEC')
            
            side_note_text = self.find_closest_side_note(tb.coords, side_note_datas,page_height)
            self.logger.debug("Side note matched for section text [%s] : %s",text, side_note_text)
            if side_note_text:
                match = re.match(r'^(\s*\d+[A-Z]*(?:-[A-Z]+)?\.\s*)(.*)', text.strip())
                if match:
                    prefix = match.group(1)
                    short_title = self.normalize_text(side_note_text.strip())
                    rest_text = match.group(2).strip()
                    rest_text_type, value, remain_text = self.findType(rest_text)
                    if rest_text_type is None:
                        self.builder += "\n" + ("\t" * (self.curr_tab_level))+f"SEC {prefix} - {short_title}"
                        self.builder += "\n" + ("\t" * (self.curr_tab_level+1)) + f"{remain_text}"  #<br>  
                    else:
                        self.builder += "\n" + ("\t" * (self.curr_tab_level))+f"SEC {prefix} - {short_title}"
                        self.curr_tab_level = self.get_hierarchy_level(rest_text_type)

                        self.builder  += "\n" + ("\t" * (self.curr_tab_level))+f"{rest_text_type} {value}"
                        self.builder  += "\n" + ("\t" * (self.curr_tab_level+1))+f"{remain_text}"
                
            else:
                match = re.match(r'^(\s*\d+[A-Z]*(?:-[A-Z]+)?\.\s*)(.*)', text.strip())
                if match:
                    prefix = match.group(1)
                    rest_text = match.group(2).strip()
                    rest_text_type, value, remain_text = self.findType(rest_text)
                    if rest_text_type is None:
                        self.builder += "\n" + ("\t" * (self.curr_tab_level))+f"SEC {prefix}"
                        self.builder += "\n" + ("\t" * (self.curr_tab_level+1)) + f"{remain_text}"  #<br>  
                    else:
                        self.builder += "\n" + ("\t" * (self.curr_tab_level))+f"SEC {prefix}"
                        self.curr_tab_level = self.get_hierarchy_level(rest_text_type)
                        self.builder  += "\n" + ("\t" * (self.curr_tab_level))+f"{rest_text_type} {value}"
                        self.builder  += "\n" + ("\t" * (self.curr_tab_level+1))+f"{remain_text}"
                        
        except Exception as e:
            self.logger.exception("Error while adding section [%s]: %s",text, e)
    
    def addArticle(self, line):
        if self.is_preamble_reached and line:
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
                    if not self.is_schedule:
                        self.builder += "\n" + ("\t" * tab_level) + f"ART {val}"
                        self.curr_tab_level = tab_level
                        self.hierarchy = ['ART']
                        self.is_schedule_open = False
                        
                    else:
                        self.curr_tab_level = self.get_hierarchy_level('SUBPART')
                        self.builder += "\n" + ("\t" * self.curr_tab_level) + f"ART {val}"
            return
        self.builder += " " + line
                    
    def addSubsection(self, text):
        try:
            self.curr_tab_level = self.get_hierarchy_level('SUBSEC')
            value, remain_text = self.find_value_and_text(text)
            self.builder  += "\n" + ("\t" * (self.curr_tab_level))+f"SUBSEC {value}"
            self.builder  += "\n" + ("\t" * (self.curr_tab_level+1))+f"{remain_text}"

        except Exception as e:
            self.logger.exception("Error while adding subsection [%s]: %s",text, e)
    
    def addPara(self, text):
        try:
            self.curr_tab_level = self.get_hierarchy_level('PARA')
            value, remain_text = self.find_value_and_text(text)
            self.builder  += "\n" + ("\t" * (self.curr_tab_level))+f"PARA {value}"
            self.builder  += "\n" + ("\t" * (self.curr_tab_level+1))+f"{remain_text}"

        except Exception as e:
            self.logger.exception("Error while adding para [%s]: %s",text, e)


    def addSubpara(self, text):
        try:
            self.curr_tab_level = self.get_hierarchy_level('SUBPARA')
            value, remain_text = self.find_value_and_text(text)
            self.builder  += "\n" + ("\t" * (self.curr_tab_level))+f"SUBPARA {value}"
            self.builder  += "\n" + ("\t" * (self.curr_tab_level+1))+f"{remain_text}"

        except Exception as e:
            self.logger.exception("Error while adding subpara [%s]: %s",text, e)
    
    def addUnlabelled(self, text):
        try:
            if not self.is_preamble_reached and text:
                is_matched = self.check_preamble_start(text)
                if is_matched:
                    self.is_preamble_reached = True
                    tab_level = self.get_tab_level('PREAMBLE')
                    if tab_level is not None:
                        self.builder += ("\t" * tab_level) + "PREAMBLE\n"
                        self.curr_tab_level = tab_level
                        self.builder += ("\t" * (self.curr_tab_level+1) + text)
                        return
                    else:
                        self.curr_tab_level += 1
                        self.builder += ("\t" * (self.curr_tab_level)) + "PREAMBLE\n"
                        self.builder += ("\t" * (self.curr_tab_level+1) + text)
                        return
            else:
                if re.fullmatch(r'—{3,}', text):
                    self.is_act_ended = True
                    return
                self.builder += " " + text
        except Exception as e:
            self.logger.exception("Error while adding unlabelled [%s]: %s",text, e)
        
    def addTable(self, table):
        try:
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
                value_tab = cell_tab + 1
                value = row[col]
                value = str(value).replace("\\n", " ")
                text = self.normalize_text(value)
                self.builder += "\n" + ("\t" * (value_tab))+f"{text}"

        except Exception as e:
            self.logger.exception("Error while adding table in html - %s .\nTable preview\n",e, table.head().to_string(index=False))
  
    def build(self, page) :
        visited_for_table = set()
       
        all_items = list(page.all_tbs.items())
        for idx, (tb, label) in enumerate(all_items):
            next_text = None
            next_text_tb = None
            if idx + 1 < len(all_items):
                next_tb, next_label = all_items[idx + 1]
                
                if next_label not in ("figure", "header", "footer"):
                    next_text = self.normalize_text(next_tb.extract_text_from_tb())
                    next_text_tb = next_tb

            at_page_end = (idx == len(all_items) - 1)

            if self.is_act_ended:
                break
            if label == "header" or label == "footer" :
               continue
            if not ((isinstance(label, tuple) and label[0] == "table")):
                if self.pending_table is not None and len(self.pending_table) <= 2:
                    self.addTable(self.pending_table[0])
                    self.pending_table = None

            text = ''
            if label not in ('figure',):
                text = self.normalize_text(tb.extract_text_from_tb())

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
                                self.merge_tables(table_obj, table_width)
                               
                            else:
                                self.addTable(self.pending_table[0])
                                self.pending_table = [table_obj, table_width]

                    visited_for_table.add(table_id)

            elif isinstance(label,list) and label[0] == "amendment":
               self.addAmendment(label,tb,page.side_notes_datas,page.pg_height)
            elif label == "title":
                self.addTitle(tb)
            elif  label == "article":
                self.addArticle(text)
            elif label == "section":
                self.addSection(tb,page.side_notes_datas,page.pg_height)
            elif (isinstance(label, tuple) and label[1] == 'subsection') or label == "subsection":
                self.addSubsection(text)
            elif (isinstance(label, tuple) and label[1] == 'para') or label == "para":
                self.addPara(text)
            elif (isinstance(label, tuple) and label[1] == 'subpara') or label == "subpara":
                self.addSubpara(text)
            # elif label == "figure":
            #    self.addFigure(tb, page)
            elif label is None:
                # if not self.is_preamble_reached:
                #     continue
                self.addUnlabelled(text)
    
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
        text = str(cell).strip().lower()
        if not text or text in ["nan", ""]:
            return False

        # Digits
        if text.isdigit():
            return True

        # Roman numerals
        if re.fullmatch(r"(m{0,3})(cm|cd|d?c{0,3})"
                        r"(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})", text):
            return True

        # Alphanumeric IDs
        if re.fullmatch(r"[a-z]\d+(\.\d+)?", text):
            return True
        if re.fullmatch(r"\d+(\.\d+)?[a-z]", text):
            return True
        if re.fullmatch(r"(sec|art|clause)[\-\s]?\d+", text):
            return True

        # Ends with a dot, like "1." or "ii."
        if re.fullmatch(r"\d+\.", text) or re.fullmatch(r"[ivxlcdm]+\.", text):
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

        # Rule 1: prev doesn’t end with punctuation + curr starts lowercase
        if prev_text and prev_text[-1] not in self.table_terminators and curr_text and curr_text[0].islower():
            return True

        # Rule 2: curr row is sparse (only 1 column filled beyond first col)
        non_empty_cols = sum(bool(str(c).strip()) for c in curr_row[1:])
        if non_empty_cols == 1:
            return True

        # Rule 3: curr row very short (few words)
        if len(curr_text.split()) < self.min_word_threshold_tableRows:
            return True

        return False

    def merge_broken_rows(self, table):
        merged = []
        for idx, row in table.iterrows():
            row_list = list(row)

            if not merged:
                merged.append(row_list)
                continue

            prev = merged[-1]
            first_cell = str(row_list[0]).strip()
            # Case 1: Explicit serial number → new row
            if self._has_serial_number(first_cell):
                merged.append(row_list)
                continue

            # Case 2: Camelot-style continuation → col1 text, col2 empty
            prev_text = str(prev[1]) if len(prev) > 1 else ""
            curr_text = str(row_list[1]) if len(row_list) > 1 else ""
            col2_text = str(row_list[2]) if len(row_list) > 2 else ""

            if curr_text and not col2_text.strip():
                prev[1] = (str(prev[1]).rstrip() + " " + curr_text.lstrip()).strip()
                continue

            # Case 3: Heuristic continuation
            if self._looks_like_continuation(prev_text, curr_text, row_list):
                for c in range(1, len(row_list)):
                    if str(row_list[c]).strip() and str(row_list[c]).lower() not in ["nan", ""]:
                        prev[c] = (str(prev[c]).rstrip() + " " +
                                   str(row_list[c]).lstrip()).strip()
            else:
                merged.append(row_list)

        return pd.DataFrame(merged, columns=table.columns)

    def is_table_continuation(self, table2, table2_width):
      table1, table1_width = self.pending_table

      # 1. Width similarity check
      width_ratio = min(table1_width, table2_width) / max(table1_width, table2_width)
      if width_ratio < 0.95:
          return False

      # 2. Column count check
      if table1.shape[1] != table2.shape[1]:
          return False
      if table2.shape[1] == table1.shape[1] and table2.iloc[0].isnull().sum() >= table2.shape[1] - 1:
          return True
      # 3. Check for serial restart (likely new table)
      first_col_prev_last = str(table1.iloc[-1, 0]).strip()
      first_col_curr_first = str(table2.iloc[0, 0]).strip()
      
      # If first column of next table is 1, A, or (a) → new table
      first_col_pattern = re.compile(
          r"^(\(?[1aAiI]\)?[\.\)]?|[\[\(]?[1aAiI][\]\)]?)$",
          re.IGNORECASE
      )

      # Example usage
      first_col_curr_first = table2.iloc[0, 0]  # first cell of next table
      if first_col_pattern.match(str(first_col_curr_first).strip()):
          return False

      # 4. Sequential numbering check (only if not restarting)
      if self.is_sequential(first_col_prev_last, first_col_curr_first):
          return True

      # 5. Header similarity check
      header_sim = self.row_similarity(table1.iloc[0], table2.iloc[0])
      if header_sim > 0.9:
          return True

      # 6. Fallback: treat as new table
      return False


    def merge_tables(self, table2, table2_width):
        table1, table1_width = self.pending_table

        # Step 1: Header similarity → skip duplicate header
        header_sim = self.row_similarity(table1.iloc[0], table2.iloc[0])
        if header_sim > 0.9:
            table2 = table2.iloc[1:].reset_index(drop=True)

        # Step 2: Align columns if mismatch
        if table2.shape[1] != table1.shape[1]:
            if table2.shape[1] < table1.shape[1]:
                for i in range(table1.shape[1] - table2.shape[1]):
                    table2[f"_pad{i}"] = ""
            else:
                table2 = table2.iloc[:, :table1.shape[1]]

        table2.columns = table1.columns

        # Step 3: Merge and handle broken rows
        merged_table = pd.concat([table1, table2], ignore_index=True)
        merged_table = self.merge_broken_rows(merged_table)

        # Step 4: Update average width
        avg_width = (table1_width + table2_width) / 2.0
        self.pending_table = [merged_table, avg_width]


    def flushTables(self):
        """Flush pending_table into final storage."""
        if self.pending_table is not None and len(self.pending_table) <= 2:
            self.addTable(self.pending_table[0])
            self.pending_table = None


    