from TextBox import TextBox
from TableExtraction import TableExtraction
from CompareLevel import CompareLevel
from sklearn.cluster import DBSCAN
import numpy as np
import re


ARTICLE      = 4
DECIMAL      = 3
SMALLSTRING  = 2
GENSTRING    = 1
ROMAN        = 0

class Page:
    stack_for_para_subpara = []
    # compareLevel = CompareLevel()
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

        for tb in self.all_tbs.keys():
            label = self.all_tbs.get(tb)
            # Skip known structural blocks
            if label not in (None,["amendment"]):
                continue

            if  tb.is_titlecase():
                if label == ["amendment"]:
                    self.all_tbs[tb].append("title")
                else:
                    self.all_tbs[tb] = "title"
                continue

            if  tb.textFont_is_bold():
                if label == ["amendment"]:
                    self.all_tbs[tb].append("title")
                else:
                    self.all_tbs[tb] = "title"
                continue
            
            if  tb.is_uppercase():
                if label == ["amendment"]:
                    self.all_tbs[tb].append("title")
                else:
                    self.all_tbs[tb] = "title"
                continue

            if tb.textFont_is_italic():
                if label == ["amendment"]:
                    self.all_tbs[tb].append("title")
                else:
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
                        if label == ["amendment"]:
                            self.all_tbs[tb].append("title")
                        else:
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
            if isinstance(label,list) and label[0] == "amendment" and label[1]=="sentences":
                print("i'm from amendment ",label[1])
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
    
    # # --- func to find section, subsection, para, subpara ---
    # def get_section_para(self):
    #     section_re = re.compile(r'^\s*\d+[A-Z]*(?:-[A-Z]+)?\.\s*\S+', re.IGNORECASE)         # 1. Clause text
    #     subsection_re = re.compile(r'^\s*\(\d+[A-Z]*(?:-[A-Z]+)?\)\s*\S+', re.IGNORECASE)    # (1) Clause text
    #     para_re = re.compile(r'^\s*\([a-z]+\)\s*\S+', re.IGNORECASE)       # (a) Clause text
    #     subpara_re = re.compile(r'^\s*\([ivxlcdm]+\)\s*\S+', re.IGNORECASE) # (i) Clause text


    #     amendment_section_re = re.compile(r'^\s*[\'"]?\d+[A-Z]*(?:-[A-Z]+)?\.\s*\S+', re.IGNORECASE)
    #     amendment_subsection_re = re.compile(r'^\s*[\'"]?\(\d+[A-Z]*(?:-[A-Z]+)?\)\s*\S+', re.IGNORECASE)
    #     amendment_para_re = re.compile(r'^\s*[\'"]?\([a-z]+\)\s*\S+', re.IGNORECASE)
    #     amendment_subpara_re = re.compile(r'^\s*[\'"]?\([ivxlcdm]+\)\s*\S+', re.IGNORECASE)



    #     for tb in self.all_tbs.keys():
    #         texts  = tb.extract_text_from_tb()
    #         texts = texts.replace('“', '"').replace('”', '"').replace('‘‘','"').replace('’’','"').replace('‘', "'").replace('’', "'")
    #         label = self.all_tbs[tb]
    #         threshold = 0.03 * self.pg_width
    #         if  label is None and section_re.match(texts.strip()):
    #             Page.coordX_for_para_subpara = None
    #             self.all_tbs[tb] = "section"
    #             continue

    #         if  label is None and subsection_re.match(texts.strip()):
    #             Page.coordX_for_para_subpara = None
    #             self.all_tbs[tb] = "subsection"
    #             continue

    #         # if label is None and para_re.match(texts.strip()) and subpara_re.match(texts.strip()) :
    #         #     closeness = abs(Page.coordX_for_para_subpara - tb.get_first_char_coordX0())
    #         #     if closeness < threshold:
    #         #         self.all_tbs[tb] = "para"
    #         #     else:
    #         #         self.all_tbs[tb] = "subpara"
    #         #     Page.coordX_for_para_subpara = tb.get_first_char_coordX0()
    #         #     continue
        
    #         if label is None and para_re.match(texts.strip()):
    #             Page.coordX_for_para_subpara = tb.get_first_char_coordX0()
    #             self.all_tbs[tb] = "para"
    #             continue

    #         if label is None and subpara_re.match(texts.strip()):
    #             Page.coordX_for_para_subpara = tb.get_first_char_coordX0()
    #             self.all_tbs[tb] = "subpara"
    #             continue 
            
    #         if label ==["amendment"] and amendment_section_re.match(texts.strip()):
    #             self.all_tbs[tb].append("section")
    #             continue

    #         if label ==["amendment"] and amendment_subsection_re.match(texts.strip()):
    #             self.all_tbs[tb].append("subsection")
    #             continue

    #         if label ==["amendment"] and amendment_para_re.match(texts.strip()):
    #             Page.coordX_for_para_subpara = tb.get_first_char_coordX0()
    #             self.all_tbs[tb].append("para")
    #             continue

    #         if label == ["amendment"] and amendment_subpara_re.match(texts.strip()):
    #             Page.coordX_for_para_subpara = tb.get_first_char_coordX0()
    #             self.all_tbs[tb].append("subpara")
    #             continue

    def roman_to_int(self,s):
        roman_map ={'i': 1,
        'v': 5,
        'x': 10,
        'l': 50,
        'c': 100,
        'd': 500,
        'm': 1000}

        if any(c not in roman_map for c in s):
            return None
        
        total = 0
        prev_value = 0
        for char in reversed(s):
            value = roman_map[char]
            if value < prev_value:
                total -= value
            else:
                total +=value
                prev_value = value
        return total


    # def get_section_para(self,startPage,endPage):
    #     hierarchy_type = ("section","subsection","para","subpara","subsubpara")
    #     section_re = re.compile(r'^\s*\d+[A-Z]*(?:-[A-Z]+)?\.\s*\S+', re.IGNORECASE)
    #     type1_re = re.compile(r'^\s*\((?P<group>\d+[A-Z]*(?:-[A-Z]+)?)\)\s*\S+', re.IGNORECASE)
    #     type2_re = re.compile(r'^\s*\((?P<group>[a-z]+)\)\s*\S+', re.IGNORECASE)
    #     type3_re = re.compile(r'^\s*\((?P<group>[ivxlcdm]+)\)\s*\S+', re.IGNORECASE)

    #     if int(self.pg_num) >=startPage and int(self.pg_num)<=endPage:
    #         for tb,label in self.all_tbs.items():
    #             texts = tb.extract_text_from_tb().strip()
    #             texts = texts.replace('“', '"').replace('”', '"').replace('‘‘','"').replace('’’','"').replace('‘', "'").replace('’', "'")
    #             if label is None and section_re.match(texts):
    #                 Page.stack_for_para_subpara=[]
    #                 self.all_tbs[tb] = hierarchy_type[0]
    #                 print("im from",self.all_tbs[tb],texts)
    #                 check_inside = re.match(r'^(\s*\d+[A-Z]*(?:-[A-Z]+)?\.\s*)(.*)', texts)
    #                 Page.stack_for_para_subpara.append((hierarchy_type[0],check_inside.group(1).strip(),tb.get_first_char_coordX0()))
                    
    #                 if check_inside:
    #                     rest_text = check_inside.group(2).strip()
    #                     match = (type1_re.match(rest_text) or type2_re.match(rest_text) or type3_re.match(rest_text))
    #                     if Page.stack_for_para_subpara and match:
    #                         group =match.group("group")
    #                         recently_visited = Page.stack_for_para_subpara[-1]
    #                         if recently_visited[0]=="section":
    #                             classification = hierarchy_type[hierarchy_type.index(recently_visited[0])+1]
    #                             self.all_tbs[tb] = classification
    #                             print("im from",self.all_tbs[tb],texts)
    #                             Page.stack_for_para_subpara.append((classification,group,tb.get_first_char_coordX0()))

    #                 continue

    #             match = (type1_re.match(texts) or type2_re.match(texts) or type3_re.match(texts))
    #             if Page.stack_for_para_subpara and match :
    #                 group =match.group("group")
    #                 recently_visited = Page.stack_for_para_subpara[-1]
    #                 if recently_visited[0]=="section":
    #                     classification = hierarchy_type[hierarchy_type.index(recently_visited[0])+1] #subsection
    #                     self.all_tbs[tb] = classification
    #                     print("im from",self.all_tbs[tb],texts)
    #                     Page.stack_for_para_subpara.append((classification,group,tb.get_first_char_coordX0()))
    #                     continue
    #                 prev_literal = recently_visited[1]
    #                 curr_literal = group 
    #                 Page.compareLevel.compare_literal(prev_literal,curr_literal)
    #                 print("prev_literal",prev_literal,"curr_literal",curr_literal)


    def get_section_para(self,startPage,endPage):
        hierarchy_type = ("section","subsection","para","subpara","subsubpara")
        section_re = re.compile(r'^\s*\d+[A-Z]*(?:-[A-Z]+)?\.\s*\S+', re.IGNORECASE)
        group_re = re.compile(r'^\(([^\s\)]+)\)\s+\S+',re.IGNORECASE)

        if int(self.pg_num) >=startPage and int(self.pg_num)<=endPage:
            for tb,label in self.all_tbs.items():
                texts = tb.extract_text_from_tb().strip()
                texts = texts.replace('“', '"').replace('”', '"').replace('‘‘','"').replace('’’','"').replace('‘', "'").replace('’', "'")
                if label is None and section_re.match(texts):
                    section_number = section_re.match(texts).group().split('.')[0].strip()
                    Page.compare_obj = CompareLevel(section_number, ARTICLE)
                    Page.prev_value = section_number
                    Page.prev_type = ARTICLE
                    Page.curr_depth = 0
                    self.all_tbs[tb] = hierarchy_type[0]
                    print("im from",self.all_tbs[tb],texts)
                    check_inside = re.match(r'^(\s*\d+[A-Z]*(?:-[A-Z]+)?\.\s*)(.*)', texts)
                    # Page.stack_for_para_subpara.append((hierarchy_type[0],section_number))
                    
                    if check_inside:
                        rest_text = check_inside.group(2).strip()
                        match = group_re.match(rest_text)
                        if match:
                            group =match.group(1).strip()
                            valueType2, compValue = Page.compare_obj.comp_nums(Page.curr_depth, Page.prev_value, group, Page.prev_type)
                            Page.curr_depth = Page.curr_depth - compValue
                            classification = hierarchy_type[min(Page.curr_depth, len(hierarchy_type) - 1)]
                            self.all_tbs[tb] = classification
                            print("I'm from", self.all_tbs[tb], texts)
                            Page.prev_value = group
                            Page.prev_type = valueType2
                    continue

                match = group_re.match(texts)
                if label is None and hasattr(Page, "compare_obj") and  match :
                    group =match.group(1).strip()
                    valueType2, compValue = Page.compare_obj.comp_nums(Page.curr_depth,Page.prev_value,group,Page.prev_type)
                    Page.curr_depth = Page.curr_depth - compValue
                    classification = hierarchy_type[min(Page.curr_depth, len(hierarchy_type) - 1)]
                    self.all_tbs[tb] = classification
                    print("i'm from ",self.all_tbs[tb],texts)
                    Page.prev_value = group
                    Page.prev_type = valueType2
    # def get_section_para(self, startPage, endPage):

    #     hierarchy_type = ["section", "subsection", "para", "subpara", "subsubpara"]
    #     section_re = re.compile(r'^\s*\d+[A-Z]*(?:-[A-Z]+)?\.\s*\S+', re.IGNORECASE)
    #     group_re = re.compile(r'^\(*([^)]+)\)*\s+\S+')

    #     if not hasattr(Page, "stack_for_para_subpara"):
    #         Page.stack_for_para_subpara = []

    #     if not hasattr(Page, "compareLevel"):
    #         raise Exception("Page.compareLevel (CompareNumber object) is not initialized.")

    #     if int(self.pg_num) < startPage or int(self.pg_num) > endPage:
    #         return

    #     for tb, label in self.all_tbs.items():
    #         texts = tb.extract_text_from_tb().strip()
    #         texts = texts.replace('“', '"').replace('”', '"')\
    #                     .replace('‘‘', '"').replace('’’', '"')\
    #                     .replace('‘', "'").replace('’', "'")

    #         # --- Detect Section ---
    #         if label is None and section_re.match(texts):
    #             Page.stack_for_para_subpara = []
    #             self.all_tbs[tb] = hierarchy_type[0]
    #             print("I'm from", self.all_tbs[tb], texts)

    #             check_inside = re.match(r'^(\s*\d+[A-Z]*(?:-[A-Z]+)?\.\s*)(.*)', texts)
    #             if check_inside:
    #                 section_num = check_inside.group(1).strip()
    #                 Page.stack_for_para_subpara.append((hierarchy_type[0], section_num, tb.get_first_char_coordX0()))
                    
    #                 rest_text = check_inside.group(2).strip()
    #                 match = group_re.match(rest_text)
    #                 if match:
    #                     group = match.group(1)
    #                     classification = hierarchy_type[1]
    #                     self.all_tbs[tb] = classification
    #                     print("I'm from", self.all_tbs[tb], texts)
    #                     Page.stack_for_para_subpara.append((classification, group, tb.get_first_char_coordX0()))
    #             continue

    #         # --- Detect Sub-Levels (subsection, para, etc.) ---
    #         match = group_re.match(texts)
    #         if Page.stack_for_para_subpara and match:
    #             curr_literal = match.group(1)
    #             prev_level, prev_literal, _ = Page.stack_for_para_subpara[-1]

    #             # Compare current group with previous one using CompareNumber
    #             valueType = Page.compareLevel.value_type(prev_literal)
    #             valueType2, compValue = Page.compareLevel.comp_nums(
    #                 depth=len(Page.stack_for_para_subpara) - 1,
    #                 value1=prev_literal,
    #                 value2=curr_literal,
    #                 valueType1=valueType
    #             )

    #             # Determine new depth and type
    #             if compValue == 0:
    #                 curr_level_index = hierarchy_type.index(prev_level)
    #             elif compValue < 0:
    #                 curr_level_index = hierarchy_type.index(prev_level) + abs(compValue)
    #             elif compValue > 0:
    #                 # Go up in hierarchy
    #                 for _ in range(compValue):
    #                     Page.stack_for_para_subpara.pop()
    #                 curr_level_index = hierarchy_type.index(Page.stack_for_para_subpara[-1][0]) if Page.stack_for_para_subpara else 0
    #                 curr_level_index += 1

    #             if curr_level_index >= len(hierarchy_type):
    #                 curr_level_index = len(hierarchy_type) - 1

    #             classification = hierarchy_type[curr_level_index]
    #             self.all_tbs[tb] = classification
    #             print("I'm from", self.all_tbs[tb], texts)

    #             Page.stack_for_para_subpara.append((classification, curr_literal, tb.get_first_char_coordX0()))


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
    
    def get_untitled_amendments(self):
        for tb,label in self.all_tbs.items():
            if isinstance(label,list) and label[0] == "amendment" and len(label)==1:
                self.all_tbs[tb].append("sentences")