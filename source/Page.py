from sklearn.cluster import DBSCAN
from sklearn.cluster import KMeans
from collections import OrderedDict, defaultdict
import numpy as np
import re
import logging
import pandas as pd


from .TextBox import TextBox
from .TableExtraction import TableExtraction, BorderlessTableExtraction
from .CompareLevel import CompareLevel, CompareLevelSebi
from .NormalizeText import NormalizeText
from .Figure import Figure,Pictures
from .Utils import *

ARTICLE      = 4
DECIMAL      = 3
SMALLSTRING  = 2
GENSTRING    = 1


ROMAN        = 0

class SectionState:
    def __init__(self):
        self.compare_obj = None
        self.prev_value = None
        self.prev_type = None
        self.curr_depth = 0


class Page:
    def __init__(self,pg,pdfPath, base_name_of_file, output_dir,
                 pdf_type, has_side_notes, is_amendment_pdf,
                 font_mapper, unique_images, min_img_size, ocr_language,
                 scanned_copy, figure_text=False):
        self.logger = logging.getLogger(__name__)
        self.pdf_path = pdfPath
        self.page_in_xml = pg
        self.pg_width, self.pg_height = self.get_pg_coords(pg)
        self.body_startX = 0.000
        self.body_endX   = self.pg_width
        self.pg_num = pg.attrib["id"]
        self.logger.debug(f"page: {self.pg_num} --- page_height: {self.pg_height} , page_width: {self.pg_width}")
        self.all_tbs = {}
        self.all_figbox = {}
        self.has_side_notes = has_side_notes
        self.pdf_type = pdf_type
        self.ocr_language = ocr_language
        self.is_amendment_pdf = is_amendment_pdf
        figure_text = figure_text or pdf_type in ('acts', 'sebi_circulars')
        self.figures = Pictures(self.pdf_path, self.pg_num, base_name_of_file,
                                output_dir, unique_images, min_img_size,
                                ocr_language, scanned_copy, figure_text)
        self.tabular_datas = TableExtraction(self.pdf_path,self.pg_num, pdf_type,
                                            scanned_copy)
        self.borderless_tabular_datas = None
        self.side_notes_datas ={}
        self.is_multicolumn = False
        self.column_bounds = []
        self.column_split_x = None
        self.font_mapper = font_mapper
        self.title_type_map = {
            'schedule' :is_schedule,
            'annexure': is_annexure,
            'appendix': is_appendix,
            'form': is_form,
        }
         

    # --- func for getting page coordinates, height, width ---
    def get_pg_coords(self,pg):
        coords = tuple(map(float, pg.attrib["bbox"].split(",")))
        height = abs(coords[1] - coords[3])
        width = abs(coords[2] - coords[0])
        return width,height
    
    def remove_out_of_page_tb(self, textboxes):
        valid_tbs = []
        for tb in textboxes:
            try:
                x0, y0, x1, y1 = map(float, tb.attrib["bbox"].split(","))
                if 0 <= x0 <= self.pg_width and 0 <= x1 <= self.pg_width and 0 <= y0 <= self.pg_height and 0 <= y1 <= self.pg_height:
                    valid_tbs.append(tb)
                else:
                    self.logger.warning("Skipping textbox with out-of-bounds bbox: %s", tb.attrib["bbox"])
            except Exception as e:
                self.logger.warning("Skipping textbox due to bbox parsing error: %s", e)
        return valid_tbs
    
     # --- func for gathering all the textboxes ---
    def process_textboxes(self):#,pg):
        pg = self.page_in_xml
        def parse_bbox(textbox):
            try:
                x0, y0, x1, y1 = map(float, textbox.attrib["bbox"].split(","))
                return x0, y0, x1, y1
            except (KeyError, ValueError) as e:
                self.logger.warning("Skipping textbox due to bbox parsing error: %s", e)
                return None
        
        def get_sorted_textboxes(tbs):
            def sort_key(tb):
                bbox = parse_bbox(tb)
                if bbox is None:
                    return (float('inf'), float('inf'), float('inf'), float('inf'))
                x0, y0, x1, y1 = bbox
                return (-y0, x0, -y1, x1)

            return sorted(tbs, key=sort_key)
        try:
            tbs = self.remove_out_of_page_tb(pg.findall(".//textbox"))
            textBoxes = get_sorted_textboxes(tbs)
            for tb in textBoxes:
                try:
                    tb_obj = TextBox(tb, self.pdf_type, self.font_mapper)
                    text = tb_obj.extract_text_from_tb()
                    if text and text.strip():
                        self.all_tbs[tb_obj] = None
                except Exception as e:
                    self.logger.warning("Failed to process a textbox: %s", e)
                    continue
        except Exception as e:
            self.logger.exception("Failed to process textboxes for page %s: %s", getattr(pg, 'pg_num', 'unknown'), e)
        
    def get_figures(self): #, pg):
        pg = self.page_in_xml
        try:
            figBoxes = pg.findall(".//figure")
            for figbox in figBoxes:
                try:
                    img_obj = Figure(figbox)
                    if img_obj.has_fig:
                        self.all_figbox[img_obj] = "figure"
                except Exception as e:
                    self.logger.warning("Failed to process a figure: %s", e)
                    continue
        except Exception as e:
            self.logger.exception("Failed to process figures for page %s: %s", getattr(pg, 'pg_num', 'unknown'), e)
        
    def sort_all_boxes(self):
            def parse_bbox(obj):
                try:
                    x0, y0, x1, y1 = obj.coords
                    return x0, y0, x1, y1
                except Exception as e:
                    self.logger.warning("Skipping object due to bbox parsing error: %s", e)
                    return None

            def sort_key(item):
                obj, _ = item
                bbox = parse_bbox(obj)
                if bbox is None:
                    return (float('inf'), float('inf'), float('inf'), float('inf'))
                x0, y0, x1, y1 = bbox
                return (-y0, x0, -y1, x1)

            # Merge text + figures
            if self.pdf_type == 'acts':
                return
            self.all_tbs.update(self.all_figbox)

            # Sort while preserving mapping
            if self.is_multicolumn:
                self.all_tbs = dict(self._reorder_by_columns(list(self.all_tbs.items())))
            else:
                self.all_tbs = dict(sorted(self.all_tbs.items(), key=sort_key))

    def get_side_notes(self): #,startPage,endPage):
        try:

            left_previous_text = ""
            right_previous_text = ""
            left_tb_coords = None
            left_sn_start_coords = None
            right_tb_coords = None
            right_sn_start_coords = None
            # if startPage is not None and endPage is not None and int(self.pg_num) >=startPage and int(self.pg_num)<=endPage:
            if self.has_side_notes:
                if not hasattr(self, 'body_startX') and not hasattr(self, 'body_endX'):
                    self.logger.warning("Body boundaries (body_startX, body_endX) are not defined for page %s", self.pg_num)
                    return  # Skip if body region not defined
                
                pattern = re.compile(r'^(\d+\s+of\s+\d+\.|Ord\.?\s*\d+\s+of\s+\d+\. | Ordinance\.?\s*\d+\s+of\s+\d+\.)$')

                

                for tb in list(self.all_tbs.keys()):
                    try:
                        if (tb.coords[2]< (self.body_startX ) or tb.coords[0] > (self.body_endX) ) \
                            and (self.all_tbs[tb] is None ) \
                            and tb.height < 0.25 * self.pg_height \
                            and tb.width < 0.25 * self.pg_width \
                            and tb.width > 0.04 * self.pg_width:
                            texts = tb.extract_text_from_tb()
                            if  texts.strip() and not pattern.match(texts.strip()):
                                if not texts.strip().endswith("."):
                                    if tb.coords[2] < self.body_startX:
                                        # Left side note
                                        if left_tb_coords:#and abs(tb.coords[1] - left_tb_coords[3]) < 0.05 * self.pg_height:
                                            texts = left_previous_text + " " + texts.strip()
                                        else:
                                            left_sn_start_coords = tb.coords
                                        left_previous_text = texts.strip()
                                        left_tb_coords = tb.coords
                                    elif tb.coords[0] > self.body_endX:
                                        # Right side note
                                        if right_tb_coords:# and abs(tb.coords[1] - right_tb_coords[3]) < 0.05 * self.pg_height:
                                            texts = right_previous_text + " " + texts.strip()
                                        else:
                                            right_sn_start_coords = tb.coords
                                        right_previous_text = texts.strip()
                                        right_tb_coords = tb.coords
                                    self.all_tbs[tb]="side notes"
                                else:
                                    if left_tb_coords and tb.coords[2] < self.body_startX:
                                        #and abs(tb.coords[1] - left_tb_coords[3]) < 0.05 * self.pg_height:
                                            texts = left_previous_text + " " + texts.strip()
                                            self.all_tbs[tb]="side notes"
                                            self.side_notes_datas[left_sn_start_coords] = texts.strip()
                                            left_previous_text = ""
                                            left_tb_coords = None
                                            left_sn_start_coords = None
                                    elif right_tb_coords and tb.coords[0] > self.body_endX:
                                        # and abs(tb.coords[1] - right_tb_coords[3]) < 0.05 * self.pg_height:
                                            texts = right_previous_text + " " + texts.strip()
                                            self.all_tbs[tb]="side notes"
                                            self.side_notes_datas[right_sn_start_coords] = texts.strip()
                                            right_previous_text = ""
                                            right_tb_coords = None
                                            right_sn_start_coords = None
                                    else:
                                        try:
                                            tb.get_side_note_datas(self.side_notes_datas)
                                            self.all_tbs[tb]="side notes"
                                        except Exception as e:
                                            self.logger.warning("Failed to preprocess \
                                                                side note data from textbox on page %s: %s", self.pg_num, e)
                            else:
                                del self.all_tbs[tb]
                    except Exception as e:
                        self.logger.warning("Error processing textbox in page %s: %s", self.pg_num, e)
                        continue

            if not self.side_notes_datas:
                self.fallback_side_notes()
            
            if not self.side_notes_datas:
                if left_previous_text:
                    self.side_notes_datas[left_sn_start_coords] = left_previous_text
                    left_previous_text = ""
                    left_tb_coords = None
                    left_sn_start_coords = None
                if right_previous_text:
                    self.side_notes_datas[right_sn_start_coords] = right_previous_text
                    right_previous_text = ""
                    right_tb_coords = None
                    right_sn_start_coords = None
        except Exception as e:
            self.logger.exception("Failed in get_side_notes for page %s: %s", self.pg_num, e)

    def fallback_side_notes(self): #,startPage,endPage):
        try:
            left_previous_text = ""
            right_previous_text = ""
            left_tb_coords = None
            left_sn_start_coords = None
            right_tb_coords = None
            right_sn_start_coords = None
            if self.has_side_notes:
                pattern = re.compile(r'^(\d+\s+of\s+\d+\.|Ord\.?\s*\d+\s+of\s+\d+\. | Ordinance\.?\s*\d+\s+of\s+\d+\.)$')

                for tb, label in list(self.all_tbs.items()):
                    if label != 'side notes':
                        continue
                    try:
                        if (tb.coords[2]< (self.body_startX ) or tb.coords[0] > (self.body_endX)):
                            texts = tb.extract_text_from_tb()
                            if  texts.strip() and not pattern.match(texts.strip()):
                                texts = texts.strip()
                                if not texts.endswith("."):
                                    if not re.match(r'\s*[A-Z]', texts):
                                        if tb.coords[2] < self.body_startX:
                                            # Left side note
                                            if left_tb_coords:#and abs(tb.coords[1] - left_tb_coords[3]) < 0.05 * self.pg_height:
                                                texts = left_previous_text + " " + texts
                                            else:
                                                left_sn_start_coords = tb.coords
                                            left_previous_text = texts
                                            left_tb_coords = tb.coords
                                        elif tb.coords[0] > self.body_endX:
                                            # Right side note
                                            if right_tb_coords:# and abs(tb.coords[1] - right_tb_coords[3]) < 0.05 * self.pg_height:
                                                texts = right_previous_text + " " + texts
                                            else:
                                                right_sn_start_coords = tb.coords
                                            right_previous_text = texts
                                            right_tb_coords = tb.coords

                                    elif re.match(r'\s*[A-Z]', texts) and\
                                        not (left_previous_text or right_previous_text):
                                        if tb.coords[2] < self.body_startX:
                                            # Left side note
                                            if left_tb_coords:#and abs(tb.coords[1] - left_tb_coords[3]) < 0.05 * self.pg_height:
                                                texts = left_previous_text + " " + texts
                                            else:
                                                left_sn_start_coords = tb.coords
                                            left_previous_text = texts
                                            left_tb_coords = tb.coords
                                        elif tb.coords[0] > self.body_endX:
                                            # Right side note
                                            if right_tb_coords:# and abs(tb.coords[1] - right_tb_coords[3]) < 0.05 * self.pg_height:
                                                texts = right_previous_text + " " + texts
                                            else:
                                                right_sn_start_coords = tb.coords
                                            right_previous_text = texts
                                            right_tb_coords = tb.coords
                                    else:
                                        if left_tb_coords and tb.coords[2] < self.body_startX:
                                        #and abs(tb.coords[1] - left_tb_coords[3]) < 0.05 * self.pg_height:
                                            texts1 = left_previous_text
                                            self.side_notes_datas[left_sn_start_coords] = texts1.strip()
                                            left_previous_text = texts
                                            left_tb_coords = tb.coords
                                            left_sn_start_coords = tb.coords
                                        elif right_tb_coords and tb.coords[0] > self.body_endX:
                                            # and abs(tb.coords[1] - right_tb_coords[3]) < 0.05 * self.pg_height:
                                                texts1 = right_previous_text
                                                self.side_notes_datas[right_sn_start_coords] = texts1.strip()
                                                right_previous_text = texts
                                                right_tb_coords = tb.coords
                                                right_sn_start_coords = tb.coords

                                    self.all_tbs[tb]="side notes"
                                else:
                                    if left_tb_coords and tb.coords[2] < self.body_startX:
                                        #and abs(tb.coords[1] - left_tb_coords[3]) < 0.05 * self.pg_height:
                                            texts = left_previous_text + " " + texts
                                            self.all_tbs[tb]="side notes"
                                            self.side_notes_datas[left_sn_start_coords] = texts.strip()
                                            left_previous_text = ""
                                            left_tb_coords = None
                                            left_sn_start_coords = None
                                    elif right_tb_coords and tb.coords[0] > self.body_endX:
                                        # and abs(tb.coords[1] - right_tb_coords[3]) < 0.05 * self.pg_height:
                                            texts = right_previous_text + " " + texts
                                            self.all_tbs[tb]="side notes"
                                            self.side_notes_datas[right_sn_start_coords] = texts.strip()
                                            right_previous_text = ""
                                            right_tb_coords = None
                                            right_sn_start_coords = None
                                    else:
                                        try:
                                            tb.get_side_note_datas(self.side_notes_datas)
                                            self.all_tbs[tb]="side notes"
                                        except Exception as e:
                                            self.logger.warning("Failed to preprocess side note data \
                                                                from textbox on page %s: %s", self.pg_num, e)
                            else:
                                del self.all_tbs[tb]
                    except Exception as e:
                        self.logger.warning("Error processing textbox in page %s: %s", self.pg_num, e)
                        continue
            if left_previous_text:
                self.side_notes_datas[left_sn_start_coords] = left_previous_text
                left_previous_text = ""
                left_tb_coords = None
                left_sn_start_coords = None
            if right_previous_text:
                self.side_notes_datas[right_sn_start_coords] = right_previous_text
                right_previous_text = ""
                right_tb_coords = None
                right_sn_start_coords = None

        except Exception as e:
            self.logger.exception("Failed in get_side_notes for page %s: %s", self.pg_num, e)


    # -- func for getting the title boxes --- 
    def get_titles(self, pdf_type):
        center_tolerance = 0.07          # Allow more deviation (6% of body width)
        max_width_ratio = 0.75          # Titles can be narrower in multi-column layouts
        min_width_ratio = 0.1
        max_tb_height_ratio = 0.3      # Slightly taller allowed for multiline headings
        min_tb_height_ratio = 0.01       # Avoid tiny noise lines
        bad_end_re = re.compile(r'[\.\,\;\:\?\-]\s*$') 
        if hasattr(self, 'body_startX') and hasattr(self, 'body_endX'):
            body_cx = (self.body_startX + self.body_endX) / 2
        else:
            body_cx = round(self.pg_width/2,2)

        tolerance = center_tolerance * self.body_width

        for tb in self.all_tbs.keys():
            text = tb.extract_text_from_tb()
            try:
                label = self.all_tbs.get(tb)

                # Skip known structural blocks
                if label not in (None,["amendment"]):
                    continue
                
                if  tb.textFont_is_bold(pdf_type):
                    if label == ["amendment"]:
                        self.all_tbs[tb].append("title")
                    else:
                        self.all_tbs[tb] = "title"
                    self.logger.debug(f"Title detected by font style - bold: '{text}' on page {self.pg_num}")
                    continue
                
                if  tb.is_uppercase(pdf_type):
                    if label == ["amendment"]:
                        self.all_tbs[tb].append("title")
                    else:
                        self.all_tbs[tb] = "title"
                    self.logger.debug(f"Title detected by font style - upper case: '{text}' on page {self.pg_num}")
                    continue

                if pdf_type != "sebi":
                    if tb.textFont_is_italic(pdf_type):
                            if label == ["amendment"]:
                                self.all_tbs[tb].append("title")
                            else:
                                self.all_tbs[tb] = "title"
                            self.logger.debug(f"Title detected by font style -  italic: '{text}' on page {self.pg_num}")
                            continue
                
                if  tb.is_titlecase(pdf_type):
                    if label == ["amendment"]:
                        self.all_tbs[tb].append("title")
                    else:
                        self.all_tbs[tb] = "title"
                    self.logger.debug(f"Title detected by font style - titlecase: '{text}' on page {self.pg_num}")
                    continue

                # Centered within tolerance
                tb_cx = (tb.coords[0] + tb.coords[2]) / 2
                if abs(tb_cx - body_cx) > tolerance:
                    continue

                # Size constraints for a visually prominent block
                if (max_width_ratio * self.body_width >= tb.width >= min_width_ratio * self.body_width) and \
                (min_tb_height_ratio * self.pg_height <= tb.height <= max_tb_height_ratio * self.pg_height) and \
                    self.all_tbs[tb] is None:
                    
                    text = tb.extract_text_from_tb().strip()
                    if text and text.count(' ') < 10:  # Optional: skip full sentences
                        if not bad_end_re.search(text):
                            if label == ["amendment"]:
                                self.all_tbs[tb].append("title")
                            else:
                                self.all_tbs[tb] = "title"
                            self.logger.debug(f"Title detected by page centered : '{text}' on page {self.pg_num}")
                            continue
            except Exception as e:
                self.logger.warning("Error while detection of  textbox for title on page %s: %s", self.pg_num, e)
                continue

    def get_italic_blockquotes(self, pdf_type):
        for tb, label in self.all_tbs.items():
            if label is not None:
                continue
            if tb.textFont_is_italic(pdf_type) and not re.fullmatch(r'\(?[a-zA-Z0-9]+\)?[.)]', tb.extract_text_from_tb().strip()):
                self.all_tbs[tb] = ('italic', 'blockquote')

    # def detect_pre(self):
    #     PAGE_W = float(self.pg_width)

    #     ROW_PRE_THRESHOLD = 4.0

    #     GAP_RATIO = 0.075
    #     LARGE_GAP = PAGE_W * GAP_RATIO

    #     def parse_bbox(elem):
    #         bbox_attr = elem.attrib.get("bbox")
    #         if not bbox_attr:
    #             return None
    #         try:
    #             return tuple(map(float, bbox_attr.split(",")))
    #         except Exception:
    #             return None

    #     def norm(txt):
    #         return re.sub(r"\s+", " ", txt or "").strip()

    #     def words_in_textline(tl, y0, y1):
    #         words = []
    #         cur_x0 = None
    #         prev_x1 = None
    #         cur_chars = []
    #         for ch in tl.findall(".//text"):
    #             raw = ch.text
    #             bbox = parse_bbox(ch)
    #             if not bbox or bbox == (0.0, 0.0, 0.0, 0.0):
    #                 continue
    #             cx0, _, cx1, _ = bbox
    #             if raw is None or raw.strip() == "":
    #                 if cur_chars:
    #                     words.append((cur_x0, prev_x1, "".join(cur_chars)))
    #                 cur_x0 = None
    #                 cur_chars = []
    #                 continue
    #             if cur_x0 is None:
    #                 cur_x0 = cx0
    #             cur_chars.append(raw)
    #             prev_x1 = cx1
    #         if cur_chars:
    #             words.append((cur_x0, prev_x1, "".join(cur_chars)))
    #         return [
    #             {"x0": x0, "x1": x1, "y0": y0, "y1": y1, "width": x1 - x0, "text": t}
    #             for x0, x1, t in words
    #         ]

    #     def wc(txt):
    #         return len(txt.split())

    #     def sentence_end(txt):
    #         return bool(re.search(r"[.!?;:]\s*$", txt))

    #     roman = r"(?:C|XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"
    #     bullet_pat = re.compile(
    #         rf"""
    #         ^
    #         [\s"“”'‘’]*
    #         (
    #             (\(?\d{{1,3}}\)?|\[\d{{1,3}}\])
    #             |
    #             (\(?[A-Za-z]{{1,3}}\)?|\[[A-Za-z]{{1,3}}\])
    #             |
    #             (\(?{roman}\)?|\[{roman}\])
    #         )
    #         [\.\)\]:-]
    #         \s*
    #         """,
    #         re.VERBOSE | re.IGNORECASE
    #     )

    #     rows = []
    #     for tb, label in self.all_tbs.items():
    #         if label is not None:
    #             continue
    #         for tl in tb.tbox.findall(".//textline"):
    #             print('am i here')
    #             bb = parse_bbox(tl)
    #             if not bb:
    #                 continue
    #             _, y0, _, y1 = bb
    #             for w in words_in_textline(tl, y0, y1):
    #                 w["tb"] = tb
    #                 rows.append(w)

    #     if not rows:
    #         return

    #     groups = defaultdict(list)
    #     for r in rows:
    #         key = round(r["y0"], 1)
    #         groups[key].append(r)

    #     blocked_boxes = set()
    #     for _, items in groups.items():
    #         items.sort(key=lambda z: z["x0"])
    #         merged = norm(" ".join(x["text"] for x in items))
    #         if bullet_pat.match(merged):
    #             for x in items:
    #                 blocked_boxes.add(x["tb"])

    #     for _, items in groups.items():
    #         usable = [x for x in items if x["tb"] not in blocked_boxes]
    #         if len(usable) < 2:
    #             continue
    #         usable.sort(key=lambda z: z["x0"])
    #         joined = norm(" ".join(x["text"] for x in usable))

    #         gaps = []
    #         for prev, cur in zip(usable, usable[1:]):
    #             g = cur["x0"] - prev["x1"]
    #             if g > 0:
    #                 gaps.append(g)

    #         if not gaps or max(gaps) <= LARGE_GAP:
    #             continue

    #         score = 2.0

    #         av = sum(gaps) / len(gaps)
    #         if av > LARGE_GAP * 0.65:
    #             score += 1.0

    #         occupied = sum(x["width"] for x in usable)
    #         if occupied / PAGE_W < 0.45:
    #             score += 1.5

    #         anchors = {round(x["x0"] / 25) for x in usable}
    #         if len(anchors) >= 2:
    #             score += 1.0

    #         if wc(joined) <= 18:
    #             score += 0.75

    #         if not sentence_end(joined):
    #             score += 0.75

    #         if score >= ROW_PRE_THRESHOLD:
    #             for x in usable:
    #                 print(tb.extract_text_from_tb(), 'pre')
    #                 self.all_tbs[x["tb"]] = "pre"

    def print_section_para(self):
        for tb,label in self.all_tbs.items():
            if isinstance(label,str) and label in set(["section","para","subsection","subpara"]):
                print("i'm from ",label)
                print(tb.extract_text_from_tb())
    
    def print_all(self):
        for tb,label in self.all_tbs.items():
            if label != "figure":
                self.logger.info(f"i'm from {label} : {tb.extract_text_from_tb()}")
            else:
                self.logger.info(f"i'm from figure: {tb.figname}")
            
            
    def print_tbs(self):
        for tb , label in self.all_tbs.items():
            if label not in ('figure',):
                print(tb.extract_text_from_tb(),'\n')

    def print_titles(self):
        print("i'm from headings")
        for tb,label in self.all_tbs.items():
            if label == "title":
                print(tb.extract_text_from_tb())
        
    def print_headers(self):
        print("i'm from header")
        for tb, label in self.all_tbs.items():
            if label == "header":
                print(tb.extract_text_from_tb())

    def print_footers(self):
        print("i'm from footer")
        for tb,label in self.all_tbs.items():
            if label == "footer":
                print(tb.extract_text_from_tb())

    def print_sidenotes(self):
        print("i'm from sidenotes")
        for tb,label in self.all_tbs.items():
            if label == "side notes":
                print(tb.extract_text_from_tb())
        print(self.side_notes_datas)

    def print_table_content(self):
        print("i'm from table contents")
        for tb,label in self.all_tbs.items():
            if isinstance(label, tuple) and label[0] == "table":
                print("From table:",label[1])
                print(tb.extract_text_from_tb())
    
    def print_borderless_table_content(self):
        print("i'm from borderless table contents")
        for tb,label in self.all_tbs.items():
            if isinstance(label, tuple) and label[0] == "borderless_table":
                print("From table:",label[1])
                print(tb.extract_text_from_tb())
    
    def print_amendment(self):
        print("i'm from amendment")
        for tb,label in self.all_tbs.items():
            if isinstance(label,list) and label[0] == "amendment":
                # print("i'm from amendment ",label[1])
                print(label)
                print(tb.extract_text_from_tb())
    
    def print_blockquote(self):
        print('iam from blockquotes')
        for tb, label in self.all_tbs.items():
            if label == "blockquote":
                print(tb.extract_text_from_tb())

    #  --- func to find the tbs which has more than 50% of page width ---
    def  get_width_ofTB_moreThan_Half_of_pg(self):
        self.fiftyPercent_moreWidth_tbs = []
        for tb in self.all_tbs.keys():
            if round(tb.width,2) >= 0.5 * self.pg_width :
                self.fiftyPercent_moreWidth_tbs.append(tb)

    # # --- func to find the page is single column or not ---
    # def is_single_column_page(self):
    #         # sum_height_of_tbs = round(sum([tb.height for tb in self.fiftyPercent_moreWidth_tbs]),2)
    #         # if sum_height_of_tbs > 0.4 * self.pg_height:
    #         #     return True 
    #         # else:
    #         #     return False
    #         # print(self.pg_width)
    #         # print(self.body_width)
    #         sum_height_of_tbs = round(sum([tb.height for tb in self.all_tbs.keys() if tb.width > 0.5*self.body_width]))
    #         if sum_height_of_tbs > 0.08 * self.pg_height:
    #             return True
    #         else:
    #             return False

    # --- cluster the textboxes which make max_height span --- 
    def cluster_coord_with_max_height_span(self, textboxes, eps=8, min_samples=2):
        if not textboxes:
            self.logger.warning(f"Page {self.pg_num}: No textboxes available for clustering.")
            return round(0.75 * self.pg_width, 2) # fallback - default value

                  # Cluster based on x0
        x_coords = np.array([tb.coords[0] for tb in textboxes]).reshape(-1, 1)
        db = DBSCAN(eps=eps, min_samples=min_samples)
        labels = db.fit_predict(x_coords)

                 # Group textboxes into clusters
        clusters = {}
        for tb, label in zip(textboxes, labels):
            clusters.setdefault(label, []).append(tb)

                 # Calculate total height for each cluster
        max_height_sum = 0
        best_cluster = []

        for label, group in clusters.items():
            total_height = sum(tb.height for tb in group if hasattr(tb, 'height'))
            if total_height > max_height_sum:
                max_height_sum = total_height
                best_cluster = group

        if not best_cluster:
            self.logger.warning(f"Page {self.pg_num}: No valid cluster found.")
            return round(0.75 * self.pg_width, 2) # fallback - default value
            
         # Calculate bounding box of best cluster
        self.body_startX = min(tb.coords[0] for tb in best_cluster)
        self.body_endX = max(tb.coords[2] for tb in best_cluster)
        self.logger.debug(f"page: {self.pg_num} --- Calculated body-startx: {self.body_startX} ,body-endX: {self.body_endX}")
        return round((self.body_endX - self.body_startX),2)
    
    # --- func to find body width if fiftyPercent_moreWidth_tbs exists ---
    def get_body_width_by_binning(self):
        if self.fiftyPercent_moreWidth_tbs:
            self.body_width = self.cluster_coord_with_max_height_span(self.fiftyPercent_moreWidth_tbs)
        else:
            self.body_width = self.get_body_width()
        self.logger.debug(f"page: {self.pg_num} --- Calculated body_width {self.body_width}")
        
    # --- func to find body width if fiftyPercent_moreWidth_tbs not exists ---
    def get_body_width(self):
        body_candidates = [
        tb for tb in self.all_tbs.keys()
        if self.all_tbs.get(tb) != "header"
        and tb.coords[0] > 0.125 * self.pg_width
        and tb.coords[2] < 0.875 * self.pg_width
        ]

        if not body_candidates:
            self.logger.warning(f"Page {self.pg_num}: No body candidates found to calculate body width.")
            return  round(0.75 * self.pg_width, 2) # fallback - default value
        
        self.body_startX = min(tb.coords[0] for tb in body_candidates)
        self.body_endX = max(tb.coords[2] for tb in body_candidates)
        self.logger.debug(f"page: {self.pg_num} --- Calculated body-startx: {self.body_startX} ,body-endX: {self.body_endX}")
        return round(self.body_endX - self.body_startX, 2)

    # --- func to detect whether the page body is laid out in multiple (usually two) columns ---
    def detect_multicolumn_layout(self, min_items_per_column=3, min_column_height_ratio=0.25,
                                   min_gap_ratio=0.03, min_gap_em_ratio=1.0, max_overlap_ratio=0.15):
        self.is_multicolumn = False
        self.column_bounds = []
        self.column_split_x = None

        all_candidates = [tb for tb, label in self.all_tbs.items() if label is None]
        if len(all_candidates) < 2 * min_items_per_column:
            return

        page_mid = self.pg_width / 2.0
        candidates = [tb for tb in all_candidates if not (tb.coords[0] < page_mid < tb.coords[2])]

        candidates = [tb for tb in candidates if tb.height <= 1.5 * tb.width]
        if len(candidates) < 2 * min_items_per_column:
            return

        x_centers = np.array([[(tb.coords[0] + tb.coords[2]) / 2.0] for tb in candidates])

        x_spread = float(x_centers.max() - x_centers.min())
        if x_spread < min_gap_ratio * self.pg_width:
            return

        try:
            km = KMeans(n_clusters=2, n_init=10, random_state=0).fit(x_centers)
        except Exception as e:
            self.logger.debug(f"Page {self.pg_num}: multicolumn KMeans failed: {e}")
            return

        centers = km.cluster_centers_.flatten()
        left_cluster_id = int(np.argmin(centers))
        right_cluster_id = 1 - left_cluster_id

        left_items = [tb for tb, lbl in zip(candidates, km.labels_) if lbl == left_cluster_id]
        right_items = [tb for tb, lbl in zip(candidates, km.labels_) if lbl == right_cluster_id]

        if len(left_items) < min_items_per_column or len(right_items) < min_items_per_column:
            return

        left_font_sizes = [tb.avg_font_size for tb in left_items if tb.avg_font_size]
        right_font_sizes = [tb.avg_font_size for tb in right_items if tb.avg_font_size]
        if left_font_sizes and right_font_sizes:
            left_font_med = float(np.median(left_font_sizes))
            right_font_med = float(np.median(right_font_sizes))
            smaller, larger = sorted((left_font_med, right_font_med))
            if larger > 0 and (smaller / larger) < 0.5:
                return

        left_height = sum(tb.height for tb in left_items)
        right_height = sum(tb.height for tb in right_items)
        if left_height < min_column_height_ratio * self.pg_height or \
           right_height < min_column_height_ratio * self.pg_height:
            return

        left_x0 = min(tb.coords[0] for tb in left_items)
        left_x1 = max(tb.coords[2] for tb in left_items)
        right_x0 = min(tb.coords[0] for tb in right_items)
        right_x1 = max(tb.coords[2] for tb in right_items)

        gap = right_x0 - left_x1
        font_sizes = [tb.avg_font_size for tb in candidates if tb.avg_font_size]
        typical_font_size = float(np.median(font_sizes)) if font_sizes else None
        min_gap = min_gap_em_ratio * typical_font_size if typical_font_size else min_gap_ratio * self.pg_width
        if gap < min_gap:
            return

        overlap = max(0.0, min(left_x1, right_x1) - max(left_x0, right_x0))
        narrower_width = min(left_x1 - left_x0, right_x1 - right_x0)
        if narrower_width > 0 and (overlap / narrower_width) > max_overlap_ratio:
            return

        self.is_multicolumn = True
        self.column_bounds = [(left_x0, left_x1), (right_x0, right_x1)]
        self.column_split_x = (left_x1 + right_x0) / 2.0
        self.logger.debug(
            f"Page {self.pg_num}: detected multicolumn layout, "
            f"column_bounds={self.column_bounds}, split_x={self.column_split_x}"
        )

    # --- band-based reading-order reorder shared by apply_column_reading_order and sort_all_boxes ---
    def _reorder_by_columns(self, items, full_width_ratio=0.6):
        split_x = self.column_split_x
        combined_x0 = self.column_bounds[0][0]
        combined_x1 = self.column_bounds[-1][1]
        combined_width = max(combined_x1 - combined_x0, 1.0)

        def y0_desc(pair):
            return -pair[0].coords[1]

        sorted_items = sorted(items, key=y0_desc)

        bands = []
        current_left, current_right = [], []

        def flush():
            nonlocal current_left, current_right
            if current_left or current_right:
                current_left.sort(key=y0_desc)
                current_right.sort(key=y0_desc)
                bands.append(current_left + current_right)
                current_left, current_right = [], []

        for tb, label in sorted_items:
            x0, y0, x1, y1 = tb.coords
            is_full_width = (x1 - x0) >= full_width_ratio * combined_width and x0 < split_x < x1
            if is_full_width:
                flush()
                bands.append([(tb, label)])
            else:
                center = (x0 + x1) / 2.0
                if center < split_x:
                    current_left.append((tb, label))
                else:
                    current_right.append((tb, label))
        flush()

        return [pair for band in bands for pair in band]

    # --- func to reorder all_tbs into correct left-column-then-right-column reading order ---
    def apply_column_reading_order(self):
        if not self.is_multicolumn:
            return
        ordered = self._reorder_by_columns(list(self.all_tbs.items()))
        self.all_tbs = dict(ordered)
        self.logger.debug(f"Page {self.pg_num}: applied multicolumn reading order")


    def find_closest_side_note(self, tb_bbox, side_note_datas, page_height, vertical_threshold_ratio=0.05): #0.05
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
                return True
            return False
        except Exception as e:
            self.logger.exception("Error finding closest side note for TB BBox %s: %s", tb_bbox, e)
            return False
        
    def find_sidenote_leftend_rightstart_coords(self):
        section_re = re.compile(r'^(\s*\d{1,3}[A-Z]*(?:-[A-Z]+)?\s*\.)(.*)', re.IGNORECASE)
        left_sidenote_end_coords = []
        right_sidenote_start_coords = []
        for tb, label in self.all_tbs.items():
            texts = tb.extract_text_from_tb().strip()
            texts = texts.replace('“', '"').replace('”', '"').replace('‘‘','"').replace('’’','"').replace('‘', "'").replace('’', "'")
            match = section_re.match(texts)
            tb_width = round(tb.width,2)
            pg_width_35 = round(0.35 * self.pg_width, 2)
            if tb_width >= pg_width_35:
                right_sidenote_start_coords.append(round(tb.coords[2],2))
            if match:
                left_sidenote_end_coords.append(tb.coords[0])
        average_left = sum(left_sidenote_end_coords)/len(left_sidenote_end_coords) if left_sidenote_end_coords else 0
        if average_left > 0 and hasattr(self, 'body_startX'):
            self.body_startX = max(round(average_left, 2), self.body_startX)
        
        average_right = sum(right_sidenote_start_coords) / len(right_sidenote_start_coords) if right_sidenote_start_coords else 0
        if average_right > 0 and hasattr(self, 'body_endX'):
            if self.body_endX < 0:
                self.body_endX = round(average_right, 2)
            else:
                self.body_endX = min(self.body_endX, round(average_right, 2))
        

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
    
    def inner_group_assign(self, rest_text, sectionState, group_re, findtype):
        match = group_re.match(rest_text)
        if match:
            if findtype == 'section':
                group =match.group(1).strip()
            elif findtype == 'article':
                group = match.group("marker").strip() or match.group("marker_paren").strip()

            valueType2, compValue = sectionState.compare_obj.comp_nums(sectionState.curr_depth, sectionState.prev_value, group, sectionState.prev_type)
            sectionState.curr_depth = sectionState.curr_depth - compValue
            sectionState.prev_value = group
            sectionState.prev_type = valueType2
            self.logger.debug(f"Page {self.pg_num}: Nested under section: {group} as {valueType2}")
    
    def inner_sidenote_check(self, text, sectionState, main, group_re, findtype):
        
        check_re = re.compile(
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
        match = check_re.match(text)
        if not match:
            return
        
        if match.group("bullet"):
            main.section_shorttitle_notend_status = False
            self.inner_group_assign(
                rest_text=text,
                sectionState=sectionState,
                group_re=group_re,
                findtype=findtype
            )
            return

        if match.group("title"):
            rest_text = match.group("rest").strip()
            main.section_shorttitle_notend_status = False
            self.inner_group_assign(
                rest_text=rest_text,
                sectionState=sectionState,
                group_re=group_re,
                findtype=findtype
            )
            return

    
    #original
    #--- func to find section, subsection, para, subpara ---
    def get_section_para(self,sectionState, main):  #,startPage,endPage):
        hierarchy_type = ("section","subsection","para","subpara","subsubpara")
        section_re = re.compile(r'^(\s*\d{1,3}[A-Z]*(?:-[A-Z]+)?\s*\.)(.*)', re.IGNORECASE)
        group_re = re.compile(
            r'^\(\s*((?:[1-9]\d{0,2})|(?:[A-Z]{1,3})|(?:(?:CM|CD|D?C{0,3})?(?:XC|XL|L?X{0,3})?(?:IX|IV|V?I{0,3})))\s*\)(.*)',
            re.IGNORECASE
        )
        try:
            page_num = int(self.pg_num)
        except Exception as e:
            self.logger.error(f"Invalid page number: {self.pg_num}")
            return

        # if startPage is not None and endPage is not None and startPage <= page_num <= endPage:
        for tb,label in self.all_tbs.items():
            side_note_status = self.find_closest_side_note(tb_bbox = tb.coords, side_note_datas = self.side_notes_datas, page_height = self.pg_height)
            if label is not None:
                if isinstance(label, tuple) and label[0] == 'article' and not side_note_status:
                    continue
                elif isinstance(label,tuple) and (label[0] == 'table' or \
                                                  label[0] == 'borderless_table'):
                    continue
                elif isinstance(label,list) and label[0] == 'amendment':
                    continue
            texts = tb.extract_text_from_tb().strip()
            texts = texts.replace('“', '"').replace('”', '"').replace('‘‘','"').replace('’’','"').replace('‘', "'").replace('’', "'")
            try:
                if main.section_shorttitle_notend_status:
                    self.inner_sidenote_check(text = texts, sectionState = sectionState, main = main, group_re = group_re, findtype = 'section')
                    continue
                
                match1 = section_re.match(texts)
                if match1:
                    section_number = match1.group(1).split('.')[0].strip()
                    sectionState.compare_obj = CompareLevel(section_number, ARTICLE)
                    sectionState.prev_value = section_number
                    sectionState.prev_type = ARTICLE
                    sectionState.curr_depth = 0
                    self.all_tbs[tb] = hierarchy_type[0]
                    self.logger.debug(f"Page {self.pg_num}: Detected section: {section_number}")
                    rest_text = match1.group(2).strip()
                    if  rest_text:
                        if main.has_side_notes and not side_note_status:
                            main.section_shorttitle_notend_status = True
                            self.inner_sidenote_check(text = rest_text, sectionState = sectionState, main = main, group_re = group_re, findtype = 'section')
                        else:
                            self.inner_group_assign(rest_text = rest_text, sectionState = sectionState, group_re = group_re, findtype = 'section')                    
                    continue

                match = group_re.match(texts)
                if sectionState.compare_obj != None and  match :
                    group =match.group(1).strip()
                    valueType2, compValue = sectionState.compare_obj.comp_nums(sectionState.curr_depth,sectionState.prev_value,group,sectionState.prev_type)
                    sectionState.curr_depth = sectionState.curr_depth - compValue
                    if sectionState.curr_depth >= len(hierarchy_type)-1:
                                continue
                    else:
                        classification = hierarchy_type[sectionState.curr_depth]
                        self.all_tbs[tb] = classification
                        sectionState.prev_value = group
                        sectionState.prev_type = valueType2
                        self.logger.debug(f"Page {self.pg_num}: Classified '{group}' as {classification}")

                    rest_text = match.group(2).strip()
                    self.inner_group_assign(rest_text = rest_text, sectionState = sectionState, group_re = group_re, findtype = 'section')


            
            except Exception as e:
                self.logger.warning(f"Page {self.pg_num}: Failed to classify textbox '{texts[:30]}...' due to: {e}")
                continue
    
    def is_schedule(self, text):
        roman_re = r"(?:M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3}))"

        ordinals = [
            "first", "second", "third", "fourth", "fifth", "sixth", "seventh",
            "eighth", "ninth", "tenth", "eleventh", "twelfth", "thirteenth",
            "fourteenth", "fifteenth", "sixteenth", "seventeenth", "eighteenth",
            "nineteenth", "twentieth"
        ]
        ordinals_re = r"(?:{})".format("|".join(ordinals))

        numbers_re = r"(?:[1-9][0-9]?)"

        pattern = rf"""(?ix)
                ^
                (?:the\s+)?                     # optional 'the'

                (?:
                    # schedule + optional separators + number/ordinal/roman
                    schedule
                    [\s\-–—:.\u2013\u2014]*     # optional separators
                    (?:{ordinals_re}|{numbers_re}|{roman_re})
                    \b
                    |

                    # number/ordinal/roman + optional separators + schedule
                    (?:{ordinals_re}|{numbers_re}|{roman_re})
                    [\s\-–—:.\u2013\u2014]*
                    schedule
                    \b
                    |

                    # just 'schedule'
                    schedule
                    \b
                )

                [\s\(\)\.\-–—:]*$               # optional trailing punctuation
            """
        return bool(re.match(pattern, text))

    
    def get_article(self,sectionState, main): #,startPage,endPage):
        hierarchy_type = ("article","subsection","para","subpara","subsubpara")
        
        roman_re = r"[IVXLCDM]+"
        article_number = rf"({roman_re}|\d+)"

        section_re = re.compile(
            rf"(?:^\s*ARTICLE\s+{article_number}$)",
            re.IGNORECASE
        )
  
        group_re = re.compile(
            r'^\s*'
            r'(?:'
                r'(?P<marker>\d+[A-Z]*(?:-[A-Z]+)?\s*\.)'
                r'|'
                r'\(\s*(?P<marker_paren>[^\s\)]+)\s*\)'
            r')\s*(?P<text>.*)$',
            re.IGNORECASE
        )
        try:
            page_num = int(self.pg_num)
        except Exception as e:
            self.logger.error(f"Invalid page number: {self.pg_num}")
            return

        # if startPage is not None and endPage is not None and startPage <= page_num <= endPage:
        for tb,label in self.all_tbs.items():
            if label is not None and isinstance(label,tuple) and (label[0] == 'table' or \
                                                                  label[0] == 'borderless_table'):
                continue
            texts = tb.extract_text_from_tb().strip()
            texts = texts.replace('“', '"').replace('”', '"').replace('‘‘','"').replace('’’','"').replace('‘', "'").replace('’', "'")
            try:
                if self.check_preamble_start(texts):
                    main.is_preamble_reached = True
                    continue
                    
                schedule_status = self.is_schedule(texts)

                if not main.is_preamble_reached and schedule_status:
                    sectionState.compare_obj = None #CompareLevel(1, ARTICLE)
                    sectionState.prev_value = None #1
                    sectionState.prev_type = None #ARTICLE
                    sectionState.curr_depth = 0
                    continue

                elif main.is_preamble_reached and schedule_status:
                    sectionState.compare_obj = CompareLevel(1, ARTICLE)
                    sectionState.prev_value = 1
                    sectionState.prev_type = ARTICLE
                    sectionState.curr_depth = 0
                    continue

                if not isinstance(label,list) and section_re.match(texts): # does not consider amendments label
                    section_number = section_re.match(texts).group().split('.')[0].strip()
                    sectionState.compare_obj = CompareLevel(section_number, ARTICLE)
                    sectionState.prev_value = section_number
                    sectionState.prev_type = ARTICLE
                    sectionState.curr_depth = 0
                    self.all_tbs[tb] = ('article', hierarchy_type[0])
                    self.logger.debug(f"Page {self.pg_num}: Detected Article: {section_number}")
                    continue

                match = group_re.match(texts)
                if not isinstance(label,list) and sectionState.compare_obj != None and  match : # does not consider amendments label
                    group =match.group("marker") or match.group("marker_paren")
                    group = group.strip()
                    valueType2, compValue = sectionState.compare_obj.comp_nums(sectionState.curr_depth,sectionState.prev_value,group,sectionState.prev_type)
                    sectionState.curr_depth = sectionState.curr_depth - compValue
                    if sectionState.curr_depth >= len(hierarchy_type)-1:
                                continue
                    else:
                        classification = hierarchy_type[sectionState.curr_depth]
                        self.all_tbs[tb] = ('article', classification)
                        sectionState.prev_value = group
                        sectionState.prev_type = valueType2
                        self.logger.debug(f"Page {self.pg_num}: Classified '{group}' as {classification}")
                    
                    rest_text = match.group("text").strip()
                    self.inner_group_assign(rest_text = rest_text, sectionState = sectionState, group_re = group_re, findtype = 'article')
            
            except Exception as e:
                self.logger.warning(f"Page {self.pg_num}: Failed to classify textbox '{texts[:30]}...' due to: {e}")
                continue
    

    def bbox_satisfies(self, tb_box, table_box,
                   width_threshold=0.4, y_tolerance_pct=0.01,
                   x_tolerance=8, y_tolerance=5):

        try:
            x_min_table, y_min_table, x_max_table, y_max_table = table_box
            x_min_textbox, y_min_textbox, x_max_textbox, y_max_textbox = tb_box
            table_width = x_max_table - x_min_table
            width_ratio = round(table_width / self.pg_width , 2)

            # --- CASE 1: wide table ---
            if width_ratio >= width_threshold:
                tol_y = self.pg_height * y_tolerance_pct

                cy = (y_min_textbox + y_max_textbox) / 2  # vertical center of textbox

                return (y_min_table - tol_y) <= cy <= (y_max_table + tol_y)

            return (
                    round(x_min_textbox, 2) >= round(x_min_table, 2) - x_tolerance and
                    round(y_min_textbox, 2) >= round(y_min_table, 2) - y_tolerance and
                    round(x_max_textbox, 2) <= round(x_max_table, 2) + x_tolerance and
                    round(y_max_textbox, 2) <= round(y_max_table, 2) + y_tolerance
                )
        except Exception:
            return False

    
    # --- func to label the textboxes comes in table layout ---
    def label_table_tbs(self):
        for idx,tab_bbox in self.tabular_datas.table_bbox.items():
            for tb in self.all_tbs.keys():
                try:
                    if self.all_tbs[tb] is None and self.bbox_satisfies(tb.coords,tab_bbox):
                        self.all_tbs[tb] = ("table",idx)
                    self.logger.debug(f"Page {self.pg_num}: Labelled textbox within table {idx}")
                except Exception as e:
                    self.logger.warning(f"Page {self.pg_num}: Failed to label textbox '{tb}' for table {idx} -- {e}")
    
    def label_borderless_table_tbs(self):
        if self.borderless_tabular_datas is None:
            return

        item_objs = getattr(self.borderless_tabular_datas, "table_item_objs", {}) or {}

        for idx, tab_bbox in self.borderless_tabular_datas.table_bbox.items():
            obj_ids = item_objs.get(idx)
            for tb in self.all_tbs.keys():
                try:
                    if self.all_tbs[tb] is not None:
                        continue
                    if obj_ids is not None:
                        is_member = id(tb) in obj_ids
                    else:
                        is_member = self.bbox_satisfies(tb.coords, tab_bbox)
                    if is_member:
                        self.all_tbs[tb] = ("borderless_table", idx)
                        self.logger.debug(f"Page {self.pg_num}: Labelled textbox within table {idx}")
                except Exception as e:
                    self.logger.warning(f"Page {self.pg_num}: Failed to label textbox '{tb}' for table {idx} -- {e}")

        if self.pdf_type != 'acts':
            for idx in self.tabular_datas.table_bbox.keys():
                try:
                    rows = self.tabular_datas.table_shape[idx]["rows"]
                    cols = self.tabular_datas.table_shape[idx]["cols"]

                    # empty dataframe
                    df = pd.DataFrame(
                        [["" for _ in range(cols)] for _ in range(rows)]
                    )

                    table_cells = self.tabular_datas.table_cells[idx]

                    # fill cells using labelled textboxes
                    for tb in self.all_tbs.keys():

                        # if self.all_tbs[tb] != ("table", idx):
                        #     continue

                        for cell in table_cells:

                            if self.bbox_satisfies(
                                tb.coords,
                                cell["bbox"]
                            ):

                                r = cell["row_index"]
                                c = cell["col_index"]

                                old_val = str(df.iat[r, c]).strip()
                                new_val = tb.text.strip()

                                if old_val:
                                    df.iat[r, c] = old_val + " " + new_val
                                else:
                                    df.iat[r, c] = new_val

                                break

                    # store dataframe
                    self.tabular_datas.tables[idx] = df

                    self.logger.debug(
                        f"Page {self.pg_num}: Built dataframe for table {idx}"
                    )

                except Exception as e:
                    self.logger.warning(
                        f"Page {self.pg_num}: Failed dataframe build "
                        f"for table {idx} -- {e}"
                    )

    def get_bulletins(self, sectionState):
        normalize_text = NormalizeText().normalize_text
        hierarchy_type = ("level1","level2","level3","level4","level5")
        
        # original
        section_re = re.compile(
            r'^(?!\s*\d{1,4}\.\d{1,4}\.\d{2,4})\s*[1-9]\d{0,2}[A-Z]?\.(?!\))(?:\s+.*)?$',
            re.IGNORECASE
        )

        group_re = re.compile(
            r'\s*('
                r'(?:[a-z]{1,2}[.\)]|\([a-z]{1,2}\))|'                     # a., a), (a)
                r'(?:[IVXLCDMivxlcdm]{1,4}[.\)]|\([IVXLCDMivxlcdm]{1,4}\))|'  # i., i), IX., (IX)
                r'(?:\(?[1-9]\d{0,2}(?:\.[1-9]\d{0,2}){0,3}\)?(?:[.\)])?)'    # allow trailing . or ) optional
            r')(?!\w)',  # ensure not followed by alphanumeric (safety)
        )

        
        for tb,label in self.all_tbs.items():
            if label is not None:
                    continue
            # if label is not None and (isinstance(label,tuple) and label  not in (('italic', 'blockquote'),)):
            #     continue
            texts = tb.extract_text_from_tb().strip()
            texts = texts.replace('“', '"').replace('”', '"').replace('‘‘','"').replace('’’','"').replace('‘', "'").replace('’', "'")
            try:
                if not isinstance(label,list) and section_re.match(texts): 
                    section_number = section_re.match(texts).group().split('.')[0].strip()
                    sectionState.compare_obj = CompareLevelSebi(section_number, ARTICLE)
                    sectionState.prev_value = section_number
                    sectionState.prev_type = ARTICLE
                    sectionState.curr_depth = 0
                    self.all_tbs[tb] = hierarchy_type[0]
                    self.logger.debug(f"Page {self.pg_num}: Detected section: {section_number}")
                    check_inside = re.match(r'^(\s*\d+[A-Z]*(?:-[A-Z]+)?\.\s*)(.*)', texts)
                    
                    if check_inside:
                        rest_text = check_inside.group(2).strip()
                        match = group_re.match(rest_text)
                        if match:
                            group =match.group(1).strip()
                            valueType2, compValue = sectionState.compare_obj.comp_nums(sectionState.curr_depth, sectionState.prev_value, group, sectionState.prev_type)
                            if valueType2 is not None and compValue is not None:
                                sectionState.curr_depth = sectionState.curr_depth - compValue
                                sectionState.prev_value = group
                                sectionState.prev_type = valueType2
                                self.logger.debug(f"Page {self.pg_num}: Nested under section: {group} as {valueType2}")
                    continue

                match = group_re.match(texts)

                if not isinstance(label,list) and sectionState.compare_obj != None and  match : # does not consider amendments label
                    group =match.group(1).strip()
                    valueType2, compValue = sectionState.compare_obj.comp_nums(sectionState.curr_depth,sectionState.prev_value,group,sectionState.prev_type)
                    if valueType2 is not None and compValue is not None:
                        sectionState.curr_depth = sectionState.curr_depth - compValue
                        if sectionState.curr_depth >= len(hierarchy_type)-1:
                                    continue
                        else:
                            classification = hierarchy_type[sectionState.curr_depth]
                            if classification == hierarchy_type[0]:
                                continue
                            self.all_tbs[tb] = classification
                            sectionState.prev_value = group
                            sectionState.prev_type = valueType2
                            self.logger.debug(f"Page {self.pg_num}: Classified '{group}' as {classification}")
            
            except Exception as e:
                self.logger.warning(f"Page {self.pg_num}: Failed to classify textbox '{texts[:30]}...' due to: {e}")
                continue
    
    def get_bulletins_sebi_circulars(self, sectionState):
        normalize_text = NormalizeText().normalize_text
        hierarchy_type = ("level1","level2","level3","level4","level5")
        
        # original
        section_re = re.compile(
            r'^(?!\s*\d{1,4}\.\d{1,4}\.\d{2,4})\s*[1-9]\d{0,2}[A-Z]?\.(?!\))(?:\s+.*)?$',
            re.IGNORECASE
        )

        group_re = re.compile(
            r'\s*('
                r'(?:[A-z]{1,2}[.\)]|\([A-z]{1,2}\))|'                     # a., a), (a)
                r'(?:[IVXLCDMivxlcdm]{1,4}[.\)]|\([IVXLCDMivxlcdm]{1,4}\))|'  # i., i), IX., (IX)
                r'(?:\(?[1-9]\d{0,2}(?:\.[1-9]\d{0,2}){0,3}\)?(?:[.\)])?)'    # allow trailing . or ) optional
            r')(?!\w)',  # ensure not followed by alphanumeric (safety)
        )

        
        for tb,label in self.all_tbs.items():
            
            if label == 'footnote':
                break

            if label is not None:
                    continue
            
            # if label is not None and (isinstance(label,tuple) and label  not in (('italic', 'blockquote'),)):
            #     continue
            texts = tb.extract_text_from_tb().strip()
            texts = texts.replace('“', '"').replace('”', '"').replace('‘‘','"').replace('’’','"').replace('‘', "'").replace('’', "'")
            try:
                if not isinstance(label,list) and section_re.match(texts): 
                    section_number = section_re.match(texts).group().split('.')[0].strip()
                    sectionState.compare_obj = CompareLevelSebi(section_number, ARTICLE)
                    sectionState.prev_value = section_number
                    sectionState.prev_type = ARTICLE
                    sectionState.curr_depth = 0
                    self.all_tbs[tb] = hierarchy_type[0]
                    self.logger.debug(f"Page {self.pg_num}: Detected section: {section_number}")
                    check_inside = re.match(r'^(\s*\d+[A-Z]*(?:-[A-Z]+)?\.\s*)(.*)', texts)
                    
                    if check_inside:
                        rest_text = check_inside.group(2).strip()
                        match = group_re.match(rest_text)
                        if match:
                            group =match.group(1).strip()
                            valueType2, compValue = sectionState.compare_obj.comp_nums(sectionState.curr_depth, sectionState.prev_value, group, sectionState.prev_type)
                            if valueType2 is not None and compValue is not None:
                                sectionState.curr_depth = sectionState.curr_depth - compValue
                                sectionState.prev_value = group
                                sectionState.prev_type = valueType2
                                self.logger.debug(f"Page {self.pg_num}: Nested under section: {group} as {valueType2}")
                    continue

                match = group_re.match(texts)

                if not isinstance(label,list) and sectionState.compare_obj != None and  match : # does not consider amendments label
                    group =match.group(1).strip()
                    valueType2, compValue = sectionState.compare_obj.comp_nums(sectionState.curr_depth,sectionState.prev_value,group,sectionState.prev_type)
                    if valueType2 is not None and compValue is not None:
                        sectionState.curr_depth = sectionState.curr_depth - compValue
                        if sectionState.curr_depth >= len(hierarchy_type)-1:
                                    continue
                        else:
                            classification = hierarchy_type[sectionState.curr_depth]
                            if classification == hierarchy_type[0]:
                                continue
                            self.all_tbs[tb] = classification
                            sectionState.prev_value = group
                            sectionState.prev_type = valueType2
                            self.logger.debug(f"Page {self.pg_num}: Classified '{group}' as {classification}")
            
            except Exception as e:
                self.logger.warning(f"Page {self.pg_num}: Failed to classify textbox '{texts[:30]}...' due to: {e}")
                continue
         
    def print_levels(self):
        for tb,label in self.all_tbs.items():
            if label and isinstance(label, str) and label[:-1] == 'level':
                print(tb.extract_text_from_tb(), label)
    
    def line_based_header_footer_detection(self):
        probable_lines = []
        for line in self.page_in_xml.findall(".//line"):
            bbox = tuple(map(float, line.attrib["bbox"].split(",")))
            x0, y0, x1, y1 = bbox
            line_width = abs(x1 - x0)
            if (y0 == y1) and line_width > 0.7*self.pg_width and not self.is_table_line(bbox):
                probable_lines.append(y1)

        for curve in self.page_in_xml.findall(".//curve"):
            bbox = tuple(map(float, curve.attrib["bbox"].split(",")))

            if self.is_line_like(bbox) and not self.is_table_line(bbox):
                x0, y0, x1, y1 = bbox
                line_width = abs(x1 - x0)
                if line_width > 0.7*self.pg_width:
                    probable_lines.append(max(y0, y1))

        for rect in self.page_in_xml.findall(".//rect"):
            bbox = tuple(map(float, rect.attrib["bbox"].split(",")))

            if self.is_line_like(bbox) and not self.is_table_line(bbox):
                x0, y0, x1, y1 = bbox
                line_width = abs(x1 - x0)
                if line_width > 0.7*self.pg_width:
                    probable_lines.append(max(y0, y1))

        if not probable_lines:
            return

        probable_lines.sort(reverse=True)

        if len(probable_lines) >= 2:
            self.label_header_zone_tbs(probable_lines[0])
            self.label_footer_zone_tbs(probable_lines[-1])
        else:
            line = probable_lines[0]
            if line > self.pg_height * 0.5:
                self.label_header_zone_tbs(line)
            else:
                self.label_footer_zone_tbs(line)
    
    def is_line_like(self, bbox, thickness_threshold=2.0):
        x0, y0, x1, y1 = bbox
        width  = abs(x1 - x0)
        height = abs(y1 - y0)

        if height < thickness_threshold:
            return True
        return False
   
    def label_header_zone_tbs(self, header_y):
        tol = self.pg_height * 0.01
        tbs_sorted = sorted(self.all_tbs.keys(), key=lambda tb: tb.coords[3], reverse=True)
        same_line_header_zone_tbs= []
        unique_header_tbs = []
        last_y = None

        for tb in tbs_sorted:
            x0, y0, x1, y1 = tb.coords

            if y1 < header_y:
                continue

            if last_y is None:
                unique_header_tbs.append(tb)
                last_y = y1
                continue

            if abs(y1 - last_y) <= tol:
                same_line_header_zone_tbs.append(tb)
                continue

            unique_header_tbs.append(tb)
            last_y = y1
        
        tbs_height = self.calculate_height_of_tbs(unique_header_tbs)
        if tbs_height < 0.08 * self.pg_height:
            for tb in (unique_header_tbs + same_line_header_zone_tbs):
                self.all_tbs[tb] = 'header'
    
    def label_footer_zone_tbs(self, footer_y):
        tol = self.pg_height * 0.01
        tbs_sorted = sorted(self.all_tbs.keys(), key=lambda tb: tb.coords[3])
        same_line_footer_zone_tbs = []
        unique_footer_tbs = []
        last_y = None

        for tb in tbs_sorted:
            x0, y0, x1, y1 = tb.coords

            if y1 > footer_y:
                continue

            if last_y is None:
                unique_footer_tbs.append(tb)
                last_y = y1
                continue

            if abs(y1 - last_y) <= tol:
                same_line_footer_zone_tbs.append(tb)
                continue

            unique_footer_tbs.append(tb)
            last_y = y1

        tbs_height = self.calculate_height_of_tbs(unique_footer_tbs)
        if tbs_height < 0.08 * self.pg_height:
            for tb in (unique_footer_tbs + same_line_footer_zone_tbs):
                self.all_tbs[tb] = 'footer'
    
    def calculate_height_of_tbs(self, tbs):
        total_height = 0
        for tb in tbs:
            total_height += tb.height
        return round(total_height, 2)
    
    def is_table_line(self, coords):
        for idx,tab_bbox in self.tabular_datas.table_bbox.items():
            if self.bbox_satisfies(coords,tab_bbox):
                return True
        return False
    
    def get_footnotes(
        self,
        seen_footnotes=None,
        previous_page_footnote_font_size=None
    ):

        if seen_footnotes is None:
            seen_footnotes = set()

        FOOTNOTE_RE = re.compile(
            r'\{\{\^\{\{FOOTNOTE\s*(\d+)\}\}\}\}'
        )

        current_footnote_font_size = (
            previous_page_footnote_font_size
        )

        footnote_started = (
            previous_page_footnote_font_size is not None
        )

        for tb in self.all_tbs.keys():

            text = tb.extract_text_from_tb()

            if not text:
                continue

            # -----------------------------------------
            # detect second occurrence of footnote ref
            # -----------------------------------------

            if not footnote_started:

                matches = FOOTNOTE_RE.findall(text)

                if matches:

                    for footnote_num in matches:

                        if footnote_num in seen_footnotes:

                            self.all_tbs[tb] = 'footnote'

                            remainder = FOOTNOTE_RE.sub('', text).strip()

                            current_footnote_font_size = (
                                tb.avg_font_size if remainder else None
                            )

                            footnote_started = True

                            break

                        seen_footnotes.add(footnote_num)

                    if footnote_started:
                        continue

            # -----------------------------------------
            # bootstrap font size from the first content
            # line after a marker-only footnote number
            # -----------------------------------------

            if footnote_started and current_footnote_font_size is None:

                is_bare_number = bool(
                    re.fullmatch(r'\(?[0-9]{1,4}\)?', text.strip())
                )

                if not is_bare_number and self.all_tbs[tb] is None:
                    self.all_tbs[tb] = 'footnote'
                    current_footnote_font_size = tb.avg_font_size

                continue

            # -----------------------------------------
            # continuation based on font size
            # -----------------------------------------

            if current_footnote_font_size is not None and self.all_tbs[tb] is None:

                same_font = (

                    abs(
                        tb.avg_font_size -
                        current_footnote_font_size
                    )

                    <=

                    (
                        current_footnote_font_size * 0.05
                    )
                )

                is_bare_number = bool(
                    re.fullmatch(r'\(?[0-9]{1,4}\)?', text.strip())
                )

                if same_font and not is_bare_number:

                    self.all_tbs[tb] = 'footnote'

                    current_footnote_font_size = (
                        tb.avg_font_size
                    )

                    continue

        return current_footnote_font_size, seen_footnotes

    def get_referenced_footnote_nums(self):
        referenced_nums = set()
        for tb in self.all_tbs.keys():
            referenced_nums.update(tb.footnotes_superscript.values())
        return referenced_nums

    def mark_standalone_footnote_markers(self):
        referenced_nums = self.get_referenced_footnote_nums()
        if not referenced_nums:
            return
        self._mark_standalone_footnote_citations(referenced_nums)

    def detect_footnote_blocks_by_style(self):
        try:
            referenced_nums = self.get_referenced_footnote_nums()

            if not referenced_nums:
                return

            self._mark_standalone_footnote_citations(referenced_nums)

            unlabeled_in_order = [tb for tb, label in self.all_tbs.items() if label is None]
            if not unlabeled_in_order:
                return

            leading_marker_re = re.compile(r'^\s*\(?([0-9*†‡]{1,3})\)?[.\s]')
            bare_number_re = re.compile(r'^\(?[0-9]{1,4}\)?$')
            dotted_clause_re = re.compile(r'^\d+\.\d+(\.\d+)*[.\s]')
            embedded_marker_re = re.compile(r'^\{\{\^\{\{FOOTNOTE\s+([0-9*†‡]{1,3})\}\}\}\}')

            texts = [tb.extract_text_from_tb().strip() for tb in unlabeled_in_order]

            def marker_num(text):
                embedded_match = embedded_marker_re.match(text)
                if embedded_match:
                    value = embedded_match.group(1)
                    return value if value in referenced_nums else None

                if dotted_clause_re.match(text):
                    return None
                match = leading_marker_re.match(text)
                if not match:
                    return None
                value = match.group(1)
                return value if value in referenced_nums else None

            last_match_idx = None
            for idx, text in enumerate(texts):
                if marker_num(text) is not None:
                    last_match_idx = idx

            if last_match_idx is None:
                return

            for text in texts[last_match_idx + 1:]:
                if not bare_number_re.match(text):
                    return

            run_start = last_match_idx
            run_first_num = marker_num(texts[last_match_idx])
            idx = last_match_idx - 1
            while idx >= 0:
                text = texts[idx]

                if bare_number_re.match(text):
                    idx -= 1
                    continue

                num = marker_num(text)
                if (
                    num is not None
                    and num.isdigit()
                    and run_first_num.isdigit()
                    and int(num) == int(run_first_num) - 1
                ):
                    run_start = idx
                    run_first_num = num
                    idx -= 1
                    continue

                break

            for tb, text in zip(unlabeled_in_order[run_start:], texts[run_start:]):
                if bare_number_re.match(text):
                    continue

                self._mark_footnote_markers_in_tb(tb, referenced_nums, leading_marker_re, dotted_clause_re)
                self.all_tbs[tb] = 'footnote'

        except Exception as e:
            self.logger.warning(f"Page {self.pg_num}: Failed footnote-style detection: {e}")

    def _mark_standalone_footnote_citations(self, referenced_nums):
        bare_number_re = re.compile(r'^\(?([0-9]{1,3})\)?$')
        tbs_in_order = list(self.all_tbs.keys())

        for idx, tb in enumerate(tbs_in_order):
            if self.all_tbs[tb] is not None:
                continue

            if tb.footnotes_superscript:
                continue

            text = tb.extract_text_from_tb().strip()
            match = bare_number_re.match(text)
            if not match:
                continue

            num = match.group(1)
            if num not in referenced_nums:
                continue

            prev_tb = tbs_in_order[idx - 1] if idx > 0 else None
            next_tb = tbs_in_order[idx + 1] if idx + 1 < len(tbs_in_order) else None

            raised_inline = False
            for neighbor in (prev_tb, next_tb):
                if neighbor is None:
                    continue

                overlap = min(tb.coords[3], neighbor.coords[3]) - max(tb.coords[1], neighbor.coords[1])
                if overlap > 0 and tb.coords[1] > neighbor.coords[1]:
                    raised_inline = True
                    break

            if not raised_inline:
                continue

            for ch in tb.tbox.findall('.//text'):
                raw = ch.text or ""
                if not raw or "bbox" not in ch.attrib:
                    continue

                bbox = tuple(map(float, ch.attrib["bbox"].split(",")))
                tb.footnotes_superscript[bbox] = raw

    def _mark_footnote_markers_in_tb(self, tb, referenced_nums, leading_marker_re, dotted_clause_re):
        if tb.footnotes_superscript:
            return

        try:
            for textline in tb.tbox.findall('.//textline'):
                chars = textline.findall('.//text')

                line_text = "".join(ch.text or "" for ch in chars).replace('\n', ' ').strip()

                if dotted_clause_re.match(line_text):
                    continue

                match = leading_marker_re.match(line_text)
                if not match or match.group(1) not in referenced_nums:
                    continue

                remaining = match.group(1)
                for ch in chars:
                    if not remaining:
                        break

                    raw = ch.text or ""
                    if not raw:
                        continue

                    if not remaining.startswith(raw):
                        break

                    if "bbox" not in ch.attrib:
                        break

                    bbox = tuple(map(float, ch.attrib["bbox"].split(",")))
                    tb.footnotes_superscript[bbox] = raw
                    remaining = remaining[len(raw):]

        except Exception as e:
            self.logger.warning(f"Page {self.pg_num}: Failed to mark footnote number bboxes: {e}")

    def get_title_hierarchy(self, section_state, sentence_status,
                      sentence_completion_punctutation):
        
        hierarchy_type = ("level0","level1","level2","level3","level4")
        group_re = re.compile(
            r'\s*('
                r'(?:[A-z]{1,2}[.\)]|\([A-z]{1,2}\))|'                     # a., a), (a)
                r'(?:[IVXLCDMivxlcdm]{1,4}[.\)]|\([IVXLCDMivxlcdm]{1,4}\))|'  # i., i), IX., (IX)
                r'(?:\(?[1-9]\d{0,2}(?:\.[1-9]\d{0,2}){0,3}\)?(?:[.\)])?)'    # allow trailing . or ) optional
            r')(?!\w)',  # ensure not followed by alphanumeric (safety)
        )
        try:

            for tb, label in self.all_tbs.items():
                text = tb.extract_text_from_tb().strip()
                text = text.replace('“', '"').replace('”', '"').replace('‘‘','"').replace('’’','"').replace('‘', "'").replace('’', "'")
                if label in ['footnote', 'header', 'footer', 'title']:
                    is_sentence_completed = True
                elif isinstance(label, tuple) and (label[0] == 'table' or \
                                                   label[0] == 'borderless_table'):
                    is_sentence_completed = True
                else:
                    is_sentence_completed = text.endswith(sentence_completion_punctutation)
                if not (label in set(['title', 'level1', 'level2', 'level3', 'level4',
                            'sec', 'subsec', 'para', 'subpara'])\
                   or label is None):
                    continue
                
                is_match = False

                for func in self.title_type_map.values():
                    is_match = (is_match or func(text))
                try:
                    if label == 'title' and is_match and sentence_status:
                        section_number = 0
                        section_state.compare_obj = CompareLevelSebi(section_number, ARTICLE)
                        section_state.prev_value = section_number
                        section_state.prev_type = ARTICLE
                        section_state.curr_depth = 0
                        self.logger.debug(f"Page {self.pg_num}: Detected section: {section_number}")


                    match = group_re.match(text)
                  
                    if section_state.compare_obj != None and  match : # does not consider amendments label
                        group =match.group(1).strip()
                        valueType2, compValue = section_state.compare_obj.comp_nums(section_state.curr_depth,section_state.prev_value,group,section_state.prev_type)
                        if valueType2 is not None and compValue is not None:
                            section_state.curr_depth = section_state.curr_depth - compValue
                            if section_state.curr_depth >= len(hierarchy_type):
                                        continue
                            else:
                                classification = hierarchy_type[section_state.curr_depth]
                                if label != 'title':
                                    self.all_tbs[tb] = ('title', classification)
                            
                                section_state.prev_value = group
                                section_state.prev_type = valueType2
                                self.logger.debug(f"Page {self.pg_num}: Classified '{group}' as {classification}")
                
                except Exception as e:
                    self.logger.warning(f"Page {self.pg_num}: Failed to classify textbox '{text[:30]}...' due to: {e}")
                    continue

                sentence_status = is_sentence_completed
            return sentence_status
        except Exception as e:
            self.logger.error(f"Error in get_hierarchy: {e}")
            return False
    
    def reclaim_header_footer_for_continuation(self, continuation_template, top_band_ratio=0.30, x_tol_ratio=0.03):
        if not continuation_template:
            return

        cols = continuation_template.get("columns_norm", [])
        if not cols:
            return
        tmpl_x0 = min(c0 for c0, _ in cols) * self.pg_width - self.pg_width * x_tol_ratio
        tmpl_x1 = max(c1 for _, c1 in cols) * self.pg_width + self.pg_width * x_tol_ratio
        top_cut = self.pg_height * (1.0 - top_band_ratio)

        reclaimable = {"header", "footer", "side notes"}
        for tb, label in list(self.all_tbs.items()):
            if label not in reclaimable:
                continue
            x0, y0, x1, y1 = tb.coords
            if y1 < top_cut:
                continue
            center_x = (x0 + x1) / 2.0
            if tmpl_x0 <= center_x <= tmpl_x1:
                self.all_tbs[tb] = None
                self.logger.debug(
                    f"Page {self.pg_num}: Reclaimed '{label}' textbox for continuation candidacy"
                )

    def get_borderless_table(self, pdf_type, header_classifier=None, region_merge_classifier=None,
                             continuation_template=None, continuation_classifier=None):
        self.borderless_tabular_datas = BorderlessTableExtraction(
                self.all_tbs, pdf_type, self.pg_width, self.pg_height,
                header_classifier=header_classifier,
                region_merge_classifier=region_merge_classifier,
                continuation_classifier=continuation_classifier,
                continuation_template=continuation_template,
            )

        return self.borderless_tabular_datas.continuation_out

    def get_borderless_continuation_template(self):
        if self.borderless_tabular_datas is None:
            return None
        return getattr(self.borderless_tabular_datas, "continuation_out", None)
    def detect_sparse_pre(self):
        SPARSE_MARGIN_RATIO = 0.30
        ROW_GAP_RATIO = 0.15
        SPLIT_GAP_RATIO = ROW_GAP_RATIO / 2.0
        ROW_Y_TOL_RATIO = 0.4
        ALIGN_TOL_RATIO = 0.03
        MAX_GAP_HEIGHT_RATIO = 1.8
        MIN_GROUP = 2
        GAP_SHORT_ITEM_WORD_LIMIT = 6

        body_start = self.body_startX
        body_end = self.body_endX
        body_width = body_end - body_start
        if body_width <= 0:
            return

        split_gap_abs = body_width * SPLIT_GAP_RATIO

        items = []
        for tb, label in self.all_tbs.items():
            if label is not None:
                continue
            for textline in tb.tbox.findall(".//textline"):
                bbox = textline.attrib.get("bbox")
                if not bbox:
                    continue
                try:
                    y0, y1 = map(float, bbox.split(",")[1::2])
                except ValueError:
                    continue

                run_chars = []
                run_x0 = None
                prev_x1 = None

                def flush_run():
                    if not run_chars:
                        return
                    text = "".join(run_chars).strip()
                    if text:
                        items.append({
                            "tb": tb, "x0": run_x0, "y0": y0, "x1": prev_x1, "y1": y1,
                            "text": text, "line_id": bbox,
                        })

                for ch in textline.findall(".//text"):
                    ch_bbox = ch.attrib.get("bbox")
                    raw = ch.text or ""
                    if not ch_bbox or not raw:
                        continue
                    try:
                        cx0, _, cx1, _ = map(float, ch_bbox.split(","))
                    except ValueError:
                        continue

                    if raw.strip():
                        if prev_x1 is not None and (cx0 - prev_x1) > split_gap_abs:
                            flush_run()
                            run_chars = []
                            run_x0 = None
                        if run_x0 is None:
                            run_x0 = cx0
                        prev_x1 = cx1

                    if run_x0 is not None:
                        run_chars.append(raw)

                flush_run()

        if not items:
            return

        items.sort(key=lambda it: (-it["y0"], it["x0"]))

        def item_column(item):
            if not self.is_multicolumn or not self.column_bounds:
                return None
            center = (item["x0"] + item["x1"]) / 2.0
            for idx, (cx0, cx1) in enumerate(self.column_bounds):
                if cx0 <= center <= cx1:
                    return idx
            return None

        rows = []
        for it in items:
            placed_row = None
            for row in rows:
                ref = row[0]
                row_height = max(ref["y1"] - ref["y0"], it["y1"] - it["y0"], 1.0)
                same_line = it["line_id"] == ref["line_id"]
                if abs(it["y0"] - ref["y0"]) <= row_height * ROW_Y_TOL_RATIO \
                        and (same_line or item_column(it) == item_column(ref)):
                    placed_row = row
                    break
            if placed_row is not None:
                placed_row.append(it)
            else:
                rows.append([it])

        rows.sort(key=lambda row: -row[0]["y0"])
        for row in rows:
            row.sort(key=lambda it: it["x0"])

        def classify_row(row):
            row_x0 = row[0]["x0"]
            row_x1 = row[-1]["x1"]

            row_col = item_column(row[0])
            if row_col is not None:
                ref_start, ref_end = self.column_bounds[row_col]
                ref_width = ref_end - ref_start
            else:
                ref_start, ref_end, ref_width = body_start, body_end, body_width
            if ref_width <= 0:
                ref_start, ref_end, ref_width = body_start, body_end, body_width

            max_gap = 0.0
            if len(row) > 1:
                max_gap = max(nxt["x0"] - prev["x1"] for prev, nxt in zip(row, row[1:]))
            if (max_gap / ref_width) >= ROW_GAP_RATIO:
                return "gap", row_x0

            left_space_ratio = (row_x0 - ref_start) / ref_width
            right_space_ratio = (ref_end - row_x1) / ref_width
            if right_space_ratio >= SPARSE_MARGIN_RATIO and left_space_ratio < SPARSE_MARGIN_RATIO:
                return "left", row_x0
            if left_space_ratio >= SPARSE_MARGIN_RATIO and right_space_ratio < SPARSE_MARGIN_RATIO:
                return "right", row_x1
            if left_space_ratio >= SPARSE_MARGIN_RATIO and right_space_ratio >= SPARSE_MARGIN_RATIO:
                return "center", (row_x0 + row_x1) / 2.0
            return None, None

        row_info = [classify_row(row) for row in rows]

        i = 0
        n = len(rows)
        while i < n:
            kind, ref = row_info[i]

            if kind is None:
                i += 1
                continue

            group = [rows[i]]
            group_ref = ref
            j = i + 1
            while j < n:
                nxt_kind, nxt_ref = row_info[j]
                if nxt_kind != kind:
                    break
                prev_row = group[-1]
                nxt_row = rows[j]
                row_height = max(
                    prev_row[0]["y1"] - prev_row[0]["y0"],
                    nxt_row[0]["y1"] - nxt_row[0]["y0"],
                    1.0,
                )
                if prev_row[0]["y0"] - nxt_row[0]["y0"] > row_height * MAX_GAP_HEIGHT_RATIO:
                    break
                if abs(group_ref - nxt_ref) > ALIGN_TOL_RATIO * body_width:
                    break
                group.append(nxt_row)
                group_ref = nxt_ref
                j += 1

            required_group = MIN_GROUP
            if kind == "gap" and all(
                max(len(it["text"].split()) for it in row) <= GAP_SHORT_ITEM_WORD_LIMIT
                for row in group
            ):
                required_group = 1

            if len(group) >= required_group:
                for row in group:
                    for it in row:
                        if self.all_tbs[it["tb"]] is None:
                            self.all_tbs[it["tb"]] = "pre"
            i = j

