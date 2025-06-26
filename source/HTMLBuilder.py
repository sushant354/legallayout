import re
import math

class HTMLBuilder:
    
    def __init__(self):
        self.pending_text = ""
        self.pending_tag = None
        self.sentence_completion_punctuation = (('.', ';', ':', '—',' or'))
        self.builder = '''<!DOCTYPE HTML>
<html>
<head>
<meta charset="UTF-8" />
<style>
  body {
    font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
    line-height: 1.6;
    white-space: normal;
  }

  .section {
    display: block;
    margin-left: 2%;
  }

  .subsection {
    display: block;
    margin-left: 5%;
  }

  .para {
    display: block;
    margin-left: 8%;
  }

  .subpara {
    display: block;
    margin-left: 11%;
  }

  .amendment {
    display: block;
    margin-left: 20%;
  }
  p {
    white-space: pre-wrap;
  }

  table {
    border-collapse: collapse;
    width: 100%;
    font-size: 0.95em;
  }

  table, td, th {
    border: 1px solid #333;
  }

  th {
    display: none;
  }

  td {
    white-space: pre-wrap;
  }
</style>
</head>
<body>
'''

    def flushPrevious(self):
      if self.pending_tag and self.pending_text:
        self.pending_text += f"</{self.pending_tag}>\n"
        self.builder += self.pending_text
        self.pending_text =""
        self.pending_tag = None

    def addTitle(self, tb,pg_width,pg_height):
        self.flushPrevious()
        if(tb.width > 0.58 * pg_width and tb.height > 0.15 * pg_height):
            self.builder += f"<p class=\"preamble\">{tb.extract_text_from_tb()}</p>\n"
        else:
            doc = ''
            for textline in tb.tbox.findall('.//textline'):
                line_texts = []
                for text in textline.findall('.//text'):
                    if text.text:
                        line_texts.append(text.text)
                line = ''.join(line_texts).replace("\n", " ").strip()
                if line:
                    doc += f"<center><h4>{line}</h4></center>\n"
            self.builder += doc
    
    def addTable(self, table):
        self.flushPrevious()
        self.builder += table.to_html(index=False, border=1)
        self.builder += "\n" 
  

    def addUnlabelled(self,text):
        is_sentence_completed = text.strip().endswith(self.sentence_completion_punctuation)
        if not self.pending_tag and not self.pending_text and is_sentence_completed:
          self.builder += f"<p>{text}</p>\n"
          self.pending_tag = None
          self.pending_text = ""
        elif not self.pending_tag and not self.pending_text and not is_sentence_completed:
          self.pending_tag = "p"
          self.pending_text = f"<p>{text}"
        elif self.pending_text and is_sentence_completed:
            self.pending_text += " "+text.strip() + f"</{self.pending_tag}>\n"
            self.builder += self.pending_text
            self.pending_tag = None
            self.pending_text = ""
        else:
            self.pending_text += text.strip()

    def get_center(self,bbox):
      x0, y0, x1, y1 = bbox
      return ((x0 + x1) / 2, (y0 + y1) / 2)

    def euclidean_distance(self,c1, c2):
        return math.sqrt((c1[0] - c2[0])**2 + (c1[1] - c2[1])**2)


    def find_closest_side_note(self, tb_bbox, side_note_datas, page_height, vertical_threshold_ratio=0.005):
      tb_x0, tb_y0, tb_x1, tb_y1 = tb_bbox
      vertical_threshold = page_height * vertical_threshold_ratio

      # tb_top_right = (tb_x1, tb_y1)

      closest_key = None
      closest_text = None

      for sn_bbox, sn_text in side_note_datas.items():
          sn_x0, sn_y0, sn_x1, sn_y1 = sn_bbox
          # sn_top_right = (sn_x1, sn_y1)

          # Check if sidenote is to the immediate left or right
          is_left = sn_x1 <= tb_x0
          is_right = sn_x0 >= tb_x1
          if not (is_left or is_right):
              continue

          # Compare Y positions of top-right corners (you said y1 is top)
          if abs(sn_y1 - tb_y1) <= vertical_threshold:
              closest_key = sn_bbox
              closest_text = sn_text
              break  # found one match, stop

      if closest_key:
          del side_note_datas[closest_key]

      return closest_text

    
    def addSection(self,tb,side_note_datas,page_height):
        self.flushPrevious()
        text = tb.extract_text_from_tb()
        is_sentence_completed = text.strip().endswith(self.sentence_completion_punctuation)
        side_note_text = self.find_closest_side_note(tb.coords, side_note_datas,page_height)
        if side_note_text:
          match = re.match(r'^(\d+\.\s*)(.*)', text.strip())
          if match:
            prefix = match.group(1)
            short_title = side_note_text.strip()
            rest_text = match.group(2).strip()
            rest_text_type = self.findType(rest_text)
            if rest_text_type is None:
                if is_sentence_completed:
                  self.builder += f"<section class=\"section\">{prefix}{short_title}<br>{rest_text}</section>\n"
                else:
                    self.pending_text +=f"<section class=\"section\">{prefix}{short_title}<br>{rest_text}"
                    self.pending_tag = "section"
            else:
                self.builder += f"<section class=\"section\">{prefix}{short_title}</section>\n"
                if is_sentence_completed:
                  self.builder += f"<section class=\"{rest_text_type}\">{rest_text}</section>\n"
                else:
                    self.pending_text += f"<section class=\"{rest_text_type}\">{rest_text}"
                    self.pending_tag = "section"
                
        else:
          if is_sentence_completed:
            self.builder += f"<section class=\"section\">{text}</section>\n"
          else:
            self.pending_text += f"<section class=\"section\">{text}"
            self.pending_tag="section"
    
    def findType(self,texts):
      subsection_re = re.compile(r'^\s*\(\d+[A-Z]*(?:-[A-Z]+)?\)\s*\S+', re.IGNORECASE)    # (1) Clause text
      para_re = re.compile(r'^\s*\([a-z]+\)\s*\S+', re.IGNORECASE)       # (a) Clause text
      subpara_re = re.compile(r'^\s*\([ivxlcdm]+\)\s*\S+', re.IGNORECASE) # (i) Clause text
      
      if  subsection_re.match(texts.strip()):
        return "subsection"

      if subpara_re.match(texts.strip()):
        return "subpara"
      
      if para_re.match(texts.strip()):
        return "para"
      
      return None

    def addSubsection(self,text):
        self.flushPrevious()
        is_sentence_completed = text.strip().endswith(self.sentence_completion_punctuation)
        if is_sentence_completed:
          self.builder += f"<section class=\"subsection\">{text}</section>\n"
        else:
          self.pending_text = f"<section class=\"subsection\">{text}"
          self.pending_tag = "section"
    
    def addPara(self,text):
        self.flushPrevious()
        is_sentence_completed = text.strip().endswith(self.sentence_completion_punctuation)
        if is_sentence_completed:
          self.builder += f"<section class=\"para\">{text}</section>\n"
        else:
           self.pending_text += f"<section class=\"para\">{text}"
           self.pending_tag = "section"
    
    def addSubpara(self,text):
        self.flushPrevious()
        is_sentence_completed = text.strip().endswith(self.sentence_completion_punctuation)
        if is_sentence_completed:
          self.builder += f"<section class=\"subpara\">{text}</section>\n"
        else:
           self.pending_text += f"<section class=\"subpara\">{text}"
           self.pending_tag ="section"
    
    def addAmendment(self,text):
        self.flushPrevious()
        # self.builder  += f"<section class=\"amendment\">{text}</section>"
        is_sentence_completed = text.strip().endswith(self.sentence_completion_punctuation)
        if is_sentence_completed:
           self.builder  += f"<section class=\"amendment\">{text}</section>"
        else:
           self.pending_text += f"<section class=\"amendment\">{text}"
           self.pending_tag = "section"
    
    # def add_title_from_amendment(self,tb,pg_width,pg_height):
    #   self.flushPrevious()
    #   if(tb.width > 0.58 * pg_width and tb.height > 0.15 * pg_height):
    #     self.builder += f"<p class=\"preamble\">{tb.extract_text_from_tb()}</p>\n"
    #   else:
    #     doc = ''
    #     for textline in tb.tbox.findall('.//textline'):
    #         line_texts = []
    #         for text in textline.findall('.//text'):
    #             if text.text:
    #                 line_texts.append(text.text)
    #         line = ''.join(line_texts).replace("\n", " ").strip()
    #         if line:
    #             doc += f"<center><h4>{line}</h4></center>\n"
    #     self.builder += doc
       
    def build(self, page):
        visited_for_table = set()

        for tb, label in page.all_tbs.items():
            if isinstance(label, tuple) and label[0] == "table":
                table_id = label[1]
                if table_id not in visited_for_table:
                    # Access table object safely
                    table_obj = page.tabular_datas.tables.get(table_id)
                    if table_obj is not None:
                        self.addTable(table_obj)
                        visited_for_table.add(table_id)
            elif isinstance(label,list) and label[0] == "amendment":
               self.addAmendment(tb.extract_text_from_tb())
            elif label == "title":
                self.addTitle(tb,page.pg_width,page.pg_height)
            elif label == "section":
                self.addSection(tb,page.side_notes_datas,page.pg_height)
            elif label == "subsection":
                self.addSubsection(tb.extract_text_from_tb())
            elif label == "para":
                self.addPara(tb.extract_text_from_tb())
            elif label == "subpara":
                self.addSubpara(tb.extract_text_from_tb())
            elif label is None:
                if not self.is_pg_num(tb,page.pg_width):
                  self.addUnlabelled(tb.extract_text_from_tb())

    def is_pg_num(self,tb,pg_width):
        if  tb.width < 0.04 * pg_width:
            return True
        return False
        
    def get_html(self):
        self.flushPrevious()
        return self.builder + "\n</body>\n</html>"

