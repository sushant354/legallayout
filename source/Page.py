from TextBox import TextBox
from TableExtraction import TableExtraction
from CompareLevel import CompareLevel, CompareLevelSebi
from NormalizeText import NormalizeText
from sklearn.cluster import DBSCAN
from sklearn.cluster import KMeans
from collections import OrderedDict
import numpy as np
import re
import logging


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
    def __init__(self,pg,pdfPath):
        self.logger = logging.getLogger(__name__)
        self.pdf_path = pdfPath
        self.pg_width, self.pg_height = self.get_pg_coords(pg)
        self.pg_num = pg.attrib["id"]
        self.logger.debug(f"page: {self.pg_num} --- page_height: {self.pg_height} , page_width: {self.pg_width}")
        self.all_tbs = {}
        self.tabular_datas = TableExtraction(self.pdf_path,self.pg_num)
        self.side_notes_datas ={}
    

    # --- func for getting page coordinates, height, width ---
    def get_pg_coords(self,pg):
        coords = tuple(map(float, pg.attrib["bbox"].split(",")))
        height = abs(coords[1] - coords[3])
        width = abs(coords[2] - coords[0])
        return width,height

    # --- gather all textboxes of the page and store it in the list ---
    def process_textboxes(self,pg):
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
    #         return sorted(tbs,
    #     key=lambda tb: (
    #         -float(parse_bbox(tb)[1]),  # y0: top to bottom (higher y lower)
    #        float(parse_bbox(tb)[0]), #,   # x0: left to right
    #         -float(parse_bbox(tb)[3]),  # y1: optional secondary vertical order
    #         float(parse_bbox(tb)[2])    # x1: optional secondary horizontal order
    #      )
    # )
        try:
            textBoxes = get_sorted_textboxes(pg.findall(".//textbox"))
            for tb in textBoxes:
                try:
                    tb_obj = TextBox(tb)
                    text = tb_obj.extract_text_from_tb()
                    if text and text.strip():
                        self.all_tbs[tb_obj] = None
                except Exception as e:
                    self.logger.warning("Failed to process a textbox: %s", e)
                    continue
        except Exception as e:
            self.logger.exception("Failed to process textboxes for page %s: %s", getattr(pg, 'pg_num', 'unknown'), e)


    # --- func for gathering the sidenotes textboxes ---
    def get_side_notes(self,startPage,endPage):
        try:
            if startPage is not None and endPage is not None and int(self.pg_num) >=startPage and int(self.pg_num)<=endPage:
                if not hasattr(self, 'body_startX') and not hasattr(self, 'body_endX'):
                    self.logger.warning("Body boundaries (body_startX, body_endX) are not defined for page %s", self.pg_num)
                    return  # Skip if body region not defined
                
                pattern = re.compile(r'^(\d+\s+of\s+\d+\.|Ord\.\s*\d+\s+of\s+\d+\.)$')
                for tb in list(self.all_tbs.keys()):
                    try:
                        if (tb.coords[2]< self.body_startX or tb.coords[0] > self.body_endX ) \
                            and (self.all_tbs[tb] is None ) \
                            and tb.height < 0.25 * self.pg_height \
                            and tb.width < 0.25 * self.pg_width \
                            and tb.width > 0.04 * self.pg_width:
                            texts = tb.extract_text_from_tb()
                            if  texts.strip() and not pattern.match(texts.strip()):
                                if not texts.strip().endswith("."):
                                    continue 
                                self.all_tbs[tb]="side notes"
                                try:
                                    tb.get_side_note_datas(self.side_notes_datas)
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
        bad_end_re_sebi = re.compile(r'[\?\.]\s*$') 
        body_cx = (self.body_startX + self.body_endX) / 2

        tolerance = center_tolerance * self.body_width

        for tb in self.all_tbs.keys():
            text = tb.extract_text_from_tb()
            try:
                label = self.all_tbs.get(tb)
                
                # Skip known structural blocks
                if label not in (None,["amendment"]):
                    continue
                
                if  tb.is_titlecase(pdf_type):
                    if label == ["amendment"]:
                        self.all_tbs[tb].append("title")
                    else:
                        # if pdf_type == 'sebi':# and bad_end_re_sebi.search(text):
                        #     continue
                        self.all_tbs[tb] = "title"
                    self.logger.debug(f"Title detected by font style - titlecase: '{text}' on page {self.pg_num}")
                    continue

                if  tb.textFont_is_bold(pdf_type):
                    if label == ["amendment"]:
                        self.all_tbs[tb].append("title")
                    else:
                        # if pdf_type == 'sebi': #and bad_end_re_sebi.search(text):
                        #     continue
                        self.all_tbs[tb] = "title"
                    self.logger.debug(f"Title detected by font style - bold: '{text}' on page {self.pg_num}")
                    continue
                
                if  tb.is_uppercase(pdf_type):
                    if label == ["amendment"]:
                        self.all_tbs[tb].append("title")
                    else:
                        # if pdf_type == 'sebi': # and bad_end_re_sebi.search(text):
                        #     continue
                        self.all_tbs[tb] = "title"
                    self.logger.debug(f"Title detected by font style - upper case: '{text}' on page {self.pg_num}")
                    continue

                if tb.textFont_is_italic(pdf_type):
                        if pdf_type == 'sebi':
                            self.all_tbs[tb] = ('italic', 'blockquote')
                            continue
                        if label == ["amendment"]:
                            self.all_tbs[tb].append("title")
                        else:
                            self.all_tbs[tb] = "title"
                        self.logger.debug(f"Title detected by font style -  italic: '{text}' on page {self.pg_num}")
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
                            # print("text height,text width of box:",tb.height,tb.width)
                                self.all_tbs[tb] = "title"
                            self.logger.debug(f"Title detected by page centered : '{text}' on page {self.pg_num}")
                            continue
            except Exception as e:
                self.logger.warning("Error while detection of  textbox for title on page %s: %s", self.pg_num, e)
                continue


            

    def print_section_para(self):
        for tb,label in self.all_tbs.items():
            if isinstance(label,str) and label in set(["section","para","subsection","subpara"]):
                print("i'm from ",label)
                print(tb.extract_text_from_tb())
    
    def print_all(self):
        for tb,label in self.all_tbs.items():
            print("i'm from ",label,": ",tb.extract_text_from_tb())
    def print_tbs(self):
        for tb in self.all_tbs.keys():
            print(tb.extract_text_from_tb())

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
    
    #--- func to find section, subsection, para, subpara ---
    def get_section_para(self,sectionState,startPage,endPage):
        hierarchy_type = ("section","subsection","para","subpara","subsubpara")
        section_re = re.compile(r'^\s*\d+[A-Z]*(?:-[A-Z]+)?\s*\.\s*\S*', re.IGNORECASE)
        # group_re = re.compile(r'^\(([^\s\)]+)\)\s*\S*',re.IGNORECASE)
        group_re = re.compile(r'^\(\s*([^\s\)]+)\s*\)\s*\S*', re.IGNORECASE)
        try:
            page_num = int(self.pg_num)
        except Exception as e:
            self.logger.error(f"Invalid page number: {self.pg_num}")
            return

        if startPage is not None and endPage is not None and startPage <= page_num <= endPage:
            for tb,label in self.all_tbs.items():
                texts = tb.extract_text_from_tb().strip()
                texts = texts.replace('“', '"').replace('”', '"').replace('‘‘','"').replace('’’','"').replace('‘', "'").replace('’', "'")
                try:
                    if not isinstance(label,list) and section_re.match(texts): # does not consider amendments label
                        section_number = section_re.match(texts).group().split('.')[0].strip()
                        sectionState.compare_obj = CompareLevel(section_number, ARTICLE)
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
                                sectionState.curr_depth = sectionState.curr_depth - compValue
                                sectionState.prev_value = group
                                sectionState.prev_type = valueType2
                                self.logger.debug(f"Page {self.pg_num}: Nested under section: {group} as {valueType2}")
                        continue

                    match = group_re.match(texts)
                    # print(label)
                    if not isinstance(label,list) and sectionState.compare_obj != None and  match : # does not consider amendments label
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
                
                except Exception as e:
                    self.logger.warning(f"Page {self.pg_num}: Failed to classify textbox '{texts[:30]}...' due to: {e}")
                    continue
    
    # --- func to label the textboxes comes in table layout ---
    def label_table_tbs(self):
        def bbox_satisfies(tb_box,table_box,tolerance = 5):
            try:
                x_min_table, y_min_table, x_max_table, y_max_table = table_box
                x_min_textbox, y_min_textbox, x_max_textbox, y_max_textbox = tb_box

                return (
                        round(x_min_textbox, 2) >= round(x_min_table, 2) - tolerance and
                        round(y_min_textbox, 2) >= round(y_min_table, 2) - tolerance and
                        round(x_max_textbox, 2) <= round(x_max_table, 2) + tolerance and
                        round(y_max_textbox, 2) <= round(y_max_table, 2) + tolerance
                    )
            except Exception as e:
                self.logger.warning(f"Error comparing bounding boxes: {tb_box} vs {table_box} -- {e}")
                return False
        


        for idx,tab_bbox in self.tabular_datas.table_bbox.items():
            for tb in self.all_tbs.keys():
                try:
                    if self.all_tbs[tb] is None and bbox_satisfies(tb.coords,tab_bbox):
                        self.all_tbs[tb] = ("table",idx)
                    self.logger.debug(f"Page {self.pg_num}: Labelled textbox within table {idx}")
                except Exception as e:
                    self.logger.warning(f"Page {self.pg_num}: Failed to label textbox '{tb}' for table {idx} -- {e}")

    def get_bulletins(self, sectionState):
        normalize_text = NormalizeText().normalize_text
        hierarchy_type = ("level1","level2","level3","level4","level5")
        #original
        # section_re = re.compile(r'^\s*\d+[A-Z]*\s*\.\s+.*$', re.IGNORECASE)
        # group_re = re.compile(r'^\s*((?:[A-Za-z]{1,3}\)|\([A-Za-z]{1,3}\))|(?:[IVXLCDM]+\)|\([IVXLCDM]+\))|(?:\(?\d+(?:\.\d+)*\)?[.\)]))', re.IGNORECASE)
        
        
        # section_re = re.compile(r'^(?!\s*[1-9]\d{0,2}[./-][1-9]\d{0,2}[./-]\d{2,4})\s*[1-9]\d{0,2}[A-Z]*\s*\.\s*(.*)?$', re.IGNORECASE)
        # group_re = re.compile(
        #         r'^\s*((?:[A-Za-z]{1,3}\)|\([A-Za-z]{1,3}\))|'                  # a), b), A)
        #         r'(?:[IVXLCDM]+\)|\([IVXLCDM]+\))|'                               # i), ii), (iv)
        #         r'(?!(?:[1-9]\d{0,2}(?:\.[1-9]\d{0,2}){1,3}[./-]\d{2,4}))'       # negative lookahead for dates
        #         r'\(?[1-9]\d{0,2}(?:\.[1-9]\d{0,2}){0,3}\)?[.\)])',              # numeric groups up to 4 levels, no leading zeros
        #         re.IGNORECASE
        #     )
        # section_re = re.compile(
        #     r'^\s*[1-9]\d{0,2}[A-Z]?\.(?!\))\s+.*$', 
        #     re.IGNORECASE
        # )


        # group_re = re.compile(
        #     r'^\s*('
        #         r'(?:[A-Za-z]{1,3}\)|\([A-Za-z]{1,3}\))|'        # a, aa, aaa or (a), (aa), (aaa)
        #         r'(?:[IVXLCDM]{1,3}\)|\([IVXLCDM]{1,3}\))|'      # roman numerals max 3 chars like iii, iv
        #         r'(?:\(?[1-9]\d{0,2}(?:\.[1-9]\d{0,2}){0,2}\)?[.\)])' # numeric: 1, 1.1, 1.1.1 max 3 levels, no leading zeros
        #     r')',
        #     re.IGNORECASE
        # )

        # section_re = re.compile(
        #     r'^(?!\s*\d{1,4}\.\d{1,4}\.\d{2,4})\s*[1-9]\d{0,2}[A-Z]?\.(?!\))\s+.*$',
        #     re.IGNORECASE
        # )
        section_re = re.compile(
            r'^(?!\s*\d{1,4}\.\d{1,4}\.\d{2,4})\s*[1-9]\d{0,2}[A-Z]?\.(?!\))(?:\s+.*)?$',
            re.IGNORECASE
        )

        group_re = re.compile(
            r'^(?!\s*\d{1,4}\.\d{1,4}\.\d{2,4})\s*('
                r'(?:[A-Za-z]{1,3}\)|\([A-Za-z]{1,3}\))|'         # a), aa), (a), etc.
                r'(?:[IVXLCDM]{1,3}\)|\([IVXLCDM]{1,3}\))|'       # i), ii), (iv), etc.
                r'(?:\(?[1-9]\d{0,2}(?:\.[1-9]\d{0,2}){0,3}\)?[.\)])' # 1, 1.1, 1.1.1, 1.1.1.1 (max 4 levels, no leading zeros)
            r')',
            re.IGNORECASE
        )
        for tb,label in self.all_tbs.items():
            if label is not None:
                continue
            texts = tb.extract_text_from_tb().strip()
            texts = texts.replace('“', '"').replace('”', '"').replace('‘‘','"').replace('’’','"').replace('‘', "'").replace('’', "'")
            try:
                if not isinstance(label,list) and section_re.match(texts): # does not consider amendments label
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
                            sectionState.curr_depth = sectionState.curr_depth - compValue
                            sectionState.prev_value = group
                            sectionState.prev_type = valueType2
                            self.logger.debug(f"Page {self.pg_num}: Nested under section: {group} as {valueType2}")
                    continue

                match = group_re.match(texts)

                if not isinstance(label,list) and sectionState.compare_obj != None and  match : # does not consider amendments label
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
            
            except Exception as e:
                self.logger.warning(f"Page {self.pg_num}: Failed to classify textbox '{texts[:30]}...' due to: {e}")
                continue