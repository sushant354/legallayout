from sklearn.cluster import DBSCAN
from sklearn.cluster import KMeans
from collections import OrderedDict
import numpy as np
import re
import logging


from .TextBox import TextBox
from .TableExtraction import TableExtraction
from .CompareLevel import CompareLevel, CompareLevelSebi
from .NormalizeText import NormalizeText
from .Figure import Figure,Pictures

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
    def __init__(self,pg,pdfPath, base_name_of_file, output_dir, pdf_type, has_side_notes, is_amendment_pdf, font_mapper):
        self.logger = logging.getLogger(__name__)
        self.pdf_path = pdfPath
        self.page_in_xml = pg
        self.pg_width, self.pg_height = self.get_pg_coords(pg)
        self.pg_num = pg.attrib["id"]
        self.logger.debug(f"page: {self.pg_num} --- page_height: {self.pg_height} , page_width: {self.pg_width}")
        self.all_tbs = {}
        self.all_figbox = {}
        self.has_side_notes = has_side_notes
        self.pdf_type = pdf_type
        self.is_amendment_pdf = is_amendment_pdf
        self.figures = Pictures(self.pdf_path, self.pg_num, base_name_of_file, output_dir)
        self.tabular_datas = TableExtraction(self.pdf_path,self.pg_num, pdf_type)
        self.side_notes_datas ={}
        self.font_mapper = font_mapper
    

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
                    tb_obj = TextBox(tb, self.font_mapper)
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
            self.all_tbs = dict(sorted(self.all_tbs.items(), key=sort_key))

    #original     
    # --- func for gathering the sidenotes textboxes ---
    # def get_side_notes(self): #,startPage,endPage):
    #     try:
    #         # if startPage is not None and endPage is not None and int(self.pg_num) >=startPage and int(self.pg_num)<=endPage:
    #         if self.has_side_notes:
    #             if not hasattr(self, 'body_startX') and not hasattr(self, 'body_endX'):
    #                 self.logger.warning("Body boundaries (body_startX, body_endX) are not defined for page %s", self.pg_num)
    #                 return  # Skip if body region not defined
                
    #             pattern = re.compile(r'^(\d+\s+of\s+\d+\.|Ord\.\s*\d+\s+of\s+\d+\.)$')
    #             for tb in list(self.all_tbs.keys()):
    #                 try:
    #                     if (tb.coords[2]< (self.body_startX ) or tb.coords[0] > (self.body_endX) ) \
    #                         and (self.all_tbs[tb] is None ) \
    #                         and tb.height < 0.25 * self.pg_height \
    #                         and tb.width < 0.25 * self.pg_width \
    #                         and tb.width > 0.04 * self.pg_width:
    #                         texts = tb.extract_text_from_tb()
    #                         if  texts.strip() and not pattern.match(texts.strip()):
    #                             # if not texts.strip().endswith("."):
    #                             #     continue 
    #                             self.all_tbs[tb]="side notes"
    #                             try:
    #                                 tb.get_side_note_datas(self.side_notes_datas)
    #                             except Exception as e:
    #                                 self.logger.warning("Failed to preprocess side note data from textbox on page %s: %s", self.pg_num, e)
    #                         else:
    #                             del self.all_tbs[tb]
    #                 except Exception as e:
    #                     self.logger.warning("Error processing textbox in page %s: %s", self.pg_num, e)
    #                     continue
    #     except Exception as e:
    #         self.logger.exception("Failed in get_side_notes for page %s: %s", self.pg_num, e)

    def get_side_notes(self): #,startPage,endPage):
        try:
            # if startPage is not None and endPage is not None and int(self.pg_num) >=startPage and int(self.pg_num)<=endPage:
            if self.has_side_notes:
                if not hasattr(self, 'body_startX') and not hasattr(self, 'body_endX'):
                    self.logger.warning("Body boundaries (body_startX, body_endX) are not defined for page %s", self.pg_num)
                    return  # Skip if body region not defined
                
                pattern = re.compile(r'^(\d+\s+of\s+\d+\.|Ord\.?\s*\d+\s+of\s+\d+\. | Ordinance\.?\s*\d+\s+of\s+\d+\.)$')

                left_previous_text = ""
                right_previous_text = ""
                left_tb_coords = None
                left_sn_start_coords = None
                right_tb_coords = None
                right_sn_start_coords = None

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
                                            self.logger.warning("Failed to preprocess side note data from textbox on page %s: %s", self.pg_num, e)
                            else:
                                del self.all_tbs[tb]
                    except Exception as e:
                        self.logger.warning("Error processing textbox in page %s: %s", self.pg_num, e)
                        continue
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
            if tb.textFont_is_italic(pdf_type):
                self.all_tbs[tb] = ('italic', 'blockquote')

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
    
    def find_closest_side_note(self, tb_bbox, side_note_datas, page_height, vertical_threshold_ratio=0.05):
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
        if average_left > 0:
            self.body_startX = max(round(average_left, 2), self.body_startX)
        
        average_right = sum(right_sidenote_start_coords) / len(right_sidenote_start_coords) if right_sidenote_start_coords else 0
        if average_right > 0:
            if self.body_endX < 0:
                self.body_endX = round(average_right, 2)
            else:
                self.body_endX = min(self.body_endX, round(average_right, 2))
        

    def check_preamble_start(self, text):
        # pattern = re.compile(
        #     r'^\s*(?:(?:A\s+)?An\s+Act\b\s*(?:\|\s*BE\s+it\s+enacted\s+by\b)?|BE\s+it\s+enacted\s+by\b)',
        #     re.IGNORECASE
        # )
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
        match = re.match(r"^(.*?[.:]\s*(?:-|—)?)(?:\s*)(.*)$", text)
        #re.match(r"^(.*?)\.[\-\—]?\s*(.*)", text)
        if match:
            rest_text = match.group(2).strip()
            main.section_shorttitle_notend_status = False
            self.inner_group_assign(rest_text = rest_text, sectionState = sectionState, group_re = group_re, findtype = findtype)
            return 

    
    #original
    #--- func to find section, subsection, para, subpara ---
    def get_section_para(self,sectionState, main):  #,startPage,endPage):
        hierarchy_type = ("section","subsection","para","subpara","subsubpara")
        #original
        # section_re = re.compile(r'^(\s*\d+[A-Z]*(?:-[A-Z]+)?\s*\.)(.*)', re.IGNORECASE)
        section_re = re.compile(r'^(\s*\d{1,3}[A-Z]*(?:-[A-Z]+)?\s*\.)(.*)', re.IGNORECASE)
        #original
        # group_re = re.compile(r'^\(\s*([^\s\)]+)\s*\)(.*)', re.IGNORECASE)
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
                elif isinstance(label,tuple) and label[0] == 'table':
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

        # pattern = rf"""(?ix)
        #     ^
        #     (?:the\s+)?
        #     (?:
        #         schedule[\s\-:]*(?:{ordinals_re}|{numbers_re}|{roman_re})\b
        #         |
        #         (?:{ordinals_re}|{numbers_re}|{roman_re})[\s\-:]*schedule\b
        #         |
        #         schedule\b
        #     )
        #     [\s\(\)\.\-]*$
        # """

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
            if label is not None and isinstance(label,tuple) and label[0] == 'table':
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
    
    # def bbox_satisfies(self, tb_box,table_box,x_tolerance = 8, y_tolerance = 5):
    #     try:
    #         x_min_table, y_min_table, x_max_table, y_max_table = table_box
    #         x_min_textbox, y_min_textbox, x_max_textbox, y_max_textbox = tb_box

    #         return (
    #                 round(x_min_textbox, 2) >= round(x_min_table, 2) - x_tolerance and
    #                 round(y_min_textbox, 2) >= round(y_min_table, 2) - y_tolerance and
    #                 round(x_max_textbox, 2) <= round(x_max_table, 2) + x_tolerance and
    #                 round(y_max_textbox, 2) <= round(y_max_table, 2) + y_tolerance
    #             )
    #     except Exception as e:
    #         self.logger.warning(f"Error comparing bounding boxes: {tb_box} vs {table_box} -- {e}")
    #         return False

    def bbox_satisfies(self, tb_box, table_box,
                   width_threshold=0.4, y_tolerance_pct=0.01,
                   x_tolerance=8, y_tolerance=5):

        try:
            x_min_table, y_min_table, x_max_table, y_max_table = table_box
            x_min_textbox, y_min_textbox, x_max_textbox, y_max_textbox = tb_box
            table_width = y_max_table - y_min_table
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
         
    def print_levels(self):
        for tb,label in self.all_tbs.items():
            if label and isinstance(label, str) and label[:-1] == 'level':
                print(tb.extract_text_from_tb(), label)
    
    def line_based_header_footer_detection(self):
        probable_lines = []
        for line in self.page_in_xml.findall(".//line"):
            bbox = tuple(map(float, line.attrib["bbox"].split(",")))
            x0, y0, x1, y1 = bbox

            if (y0 == y1) and not self.is_table_line(bbox):
                probable_lines.append(y1)

        for curve in self.page_in_xml.findall(".//curve"):
            bbox = tuple(map(float, curve.attrib["bbox"].split(",")))

            if self.is_line_like(bbox) and not self.is_table_line(bbox):
                _, y0, _, y1 = bbox
                probable_lines.append(max(y0, y1))

        for rect in self.page_in_xml.findall(".//rect"):
            bbox = tuple(map(float, rect.attrib["bbox"].split(",")))

            if self.is_line_like(bbox) and not self.is_table_line(bbox):
                _, y0, _, y1 = bbox
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