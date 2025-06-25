from TextBox import TextBox
from TableExtraction import TableExtraction
from sklearn.cluster import DBSCAN
import numpy as np
import re

class Page:
    def __init__(self,pg,pdfPath):
        self.pdf_path = pdfPath
        self.pg_width, self.pg_height = self.get_pg_coords(pg)
        self.pg_num = pg.attrib["id"]
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
            x0, y0, x1, y1 = map(float, textbox.attrib["bbox"].split(","))
            return x0, y0, x1, y1
        
        def get_sorted_textboxes(tbs):
            return sorted(tbs,
        key=lambda tb: (
            -float(parse_bbox(tb)[1]),  # y0: top to bottom (higher y lower)
           float(parse_bbox(tb)[0]), #,   # x0: left to right
            -float(parse_bbox(tb)[3]),  # y1: optional secondary vertical order
            float(parse_bbox(tb)[2])    # x1: optional secondary horizontal order
         )
    )
        textBoxes = get_sorted_textboxes(pg.findall(".//textbox"))
        for tb in textBoxes:
            tb_obj = TextBox(tb)
            if tb_obj.extract_text_from_tb().strip():
                self.all_tbs[tb_obj] = None

    # --- func for gathering the sidenotes textboxes ---
    def get_side_notes(self):
        if not hasattr(self, 'body_startX') or not hasattr(self, 'body_endX'):
            return  # Skip if body region not defined
        
        # pattern = re.compile(r'^\d+\s+of\s+\d+\.$')
        pattern = re.compile(r'^(\d+\s+of\s+\d+\.|Ord\.\s*\d+\s+of\s+\d+\.)$')
        for tb in list(self.all_tbs.keys()):
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
                    tb.get_side_note_datas(self.side_notes_datas)
                else:
                    del self.all_tbs[tb]

    # -- func for getting the title boxes --- 
    def get_titles(self):
        center_tolerance = 0.07          # Allow more deviation (6% of body width)
        max_width_ratio = 0.75          # Titles can be narrower in multi-column layouts
        min_width_ratio = 0.1
        max_tb_height_ratio = 0.3      # Slightly taller allowed for multiline headings
        min_tb_height_ratio = 0.01       # Avoid tiny noise lines
        bad_end_re = re.compile(r'[\.\,\;\:\?\-]\s*$') 

        body_cx = (self.body_startX + self.body_endX) / 2

        tolerance = center_tolerance * self.body_width

        for tb in self.all_tbs:
            # Skip known structural blocks
            if self.all_tbs.get(tb) is not None:
                continue

            if  tb.is_titlecase():
                self.all_tbs[tb] = "title"
                continue

            if  tb.textFont_is_bold():
                self.all_tbs[tb] = "title"
                continue
            
            if  tb.is_uppercase():
                self.all_tbs[tb] = "title"
                continue

            if tb.textFont_is_italic():
                self.all_tbs[tb] = "title"
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
                        # print("text height,text width of box:",tb.height,tb.width)
                        self.all_tbs[tb] = "title"
                        continue

            

    def print_section_para(self):
        for tb,label in self.all_tbs.items():
            if label in set(["section","para","subsection","subpara"]):
                print("i'm from ",label)
                print(tb.extract_text_from_tb())
    
    def print_all(self):
        for tb,label in self.all_tbs.items():
            print("i'm from ",label,": ",tb.extract_text_from_tb())

    def print_titles(self):
        print("i'm from headings")
        for tb,label in self.all_tbs.items():
            if label == "title":
                # print("i'm from ",label[1])
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
                print(tb.extract_text_from_tb())

    #  --- func to find the tbs which has more than 50% of page width ---
    def  get_width_ofTB_moreThan_Half_of_pg(self):
        self.fiftyPercent_moreWidth_tbs = []
        for tb in self.all_tbs.keys():
            if round(tb.width,2) >= 0.5 * self.pg_width :
                self.fiftyPercent_moreWidth_tbs.append(tb)

    # --- func to find the page is single column or not ---
    def is_single_column_page(self):
            sum_height_of_tbs = round(sum([tb.height for tb in self.fiftyPercent_moreWidth_tbs]),2)
            if sum_height_of_tbs > 0.4 * self.pg_height:
                return True 
            else:
                return False
    
    # --- cluster the textboxes which make max_height span --- 
    def cluster_coord_with_max_height_span(self, textboxes, eps=8, min_samples=2):
        if not textboxes:
            return None

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
            total_height = sum(tb.height for tb in group)
            if total_height > max_height_sum:
                max_height_sum = total_height
                best_cluster = group

        if not best_cluster:
            return None

                 # Calculate bounding box of best cluster
        self.body_startX = min(tb.coords[0] for tb in best_cluster)
        self.body_endX = max(tb.coords[2] for tb in best_cluster)

        return round((self.body_endX - self.body_startX),2)
    
    # --- func to find body width if fiftyPercent_moreWidth_tbs exists ---
    def get_body_width_by_binning(self):
        if self.fiftyPercent_moreWidth_tbs:
            self.body_width = self.cluster_coord_with_max_height_span(self.fiftyPercent_moreWidth_tbs)
        else:
            self.body_width = self.get_body_width()
        
    # --- func to find body width if fiftyPercent_moreWidth_tbs not exists ---
    def get_body_width(self):
        body_candidates = [
        tb for tb in self.all_tbs.keys()
        if self.all_tbs.get(tb) != "header"
        and tb.coords[0] > 0.125 * self.pg_width
        and tb.coords[2] < 0.875 * self.pg_width
        ]
        
        self.body_startX = min(tb.coords[0] for tb in body_candidates)
        self.body_endX = max(tb.coords[2] for tb in body_candidates)

        return round(self.body_endX - self.body_startX, 2)
    
    # --- func to find section, subsection, para, subpara ---
    def get_section_para(self):
        section_re = re.compile(r'^\s*\d+\.\s*\S+', re.IGNORECASE)         # 1. Clause text
        subsection_re = re.compile(r'^\s*\(\d+\)\s*\S+', re.IGNORECASE)    # (1) Clause text
        para_re = re.compile(r'^\s*\([a-z]+\)\s*\S+', re.IGNORECASE)       # (a) Clause text
        subpara_re = re.compile(r'^\s*\([ivxlcdm]+\)\s*\S+', re.IGNORECASE) # (i) Clause text

        for tb in list(self.all_tbs.keys()):
            texts  = tb.extract_text_from_tb()

            if self.all_tbs[tb] is None and section_re.match(texts.strip()):
                self.all_tbs[tb] = "section"
                continue

            if self.all_tbs[tb] is None and subsection_re.match(texts.strip()):
                self.all_tbs[tb] = "subsection"
                continue

            if self.all_tbs[tb] is None and para_re.match(texts.strip()) and tb.get_first_char_coordX0()<0.3*self.pg_width:
                self.all_tbs[tb] = "para"
                continue

            if self.all_tbs[tb] is None and subpara_re.match(texts.strip()):
                self.all_tbs[tb] = "subpara"
                continue 



    # --- func to label the textboxes comes in table layout ---
    def label_table_tbs(self):
        def bbox_satisfies(tb_box,table_box,tolerance = 5):
            x_min_table, y_min_table, x_max_table, y_max_table = table_box
            x_min_textbox, y_min_textbox, x_max_textbox, y_max_textbox = tb_box

            return (
                    round(x_min_textbox, 2) >= round(x_min_table, 2) - tolerance and
                    round(y_min_textbox, 2) >= round(y_min_table, 2) - tolerance and
                    round(x_max_textbox, 2) <= round(x_max_table, 2) + tolerance and
                    round(y_max_textbox, 2) <= round(y_max_table, 2) + tolerance
                )
                        
        
        for idx,tab_bbox in self.tabular_datas.table_bbox.items():
            for tb in self.all_tbs.keys():
                if self.all_tbs[tb] is None and bbox_satisfies(tb.coords,tab_bbox):
                    self.all_tbs[tb] = ("table",idx)
                    