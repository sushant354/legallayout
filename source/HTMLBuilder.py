import re
import math
from collections import OrderedDict
import numpy as np
from sklearn.cluster import DBSCAN

class HTMLBuilder:
    
    def __init__(self):
        self.pending_text = ""
        self.pending_tag = None
        self.sentence_completion_punctuation = ('.', ';', ':', '—')
        self.stack_for_section = []
        self.hierarchy = ("section","subsection","para","subpara","subsubpara")
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

    # --- func to flush previous textbox text --
    def flushPrevious(self):
      if self.pending_tag and self.pending_text:
        self.pending_text += f"</{self.pending_tag}>\n"
        self.builder += self.pending_text
        self.pending_text =""
        self.pending_tag = None

    # --- func to add Title in the html ---
    def addTitle(self, tb,pg_width,pg_height):
        if not self.stack_for_section:
            self.flushPrevious()
        else:
            # Close everything up to and including the last "section"
            while self.stack_for_section:
                tag = self.stack_for_section.pop()
                self.builder += "</section>\n"
                if tag == 0:
                    break
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
    
    # --- func to add the table in the html ---
    def addTable(self, table):
        if not self.stack_for_section:
            self.flushPrevious()
        else:
            # Close everything up to and including the last "section"
            while self.stack_for_section:
                tag = self.stack_for_section.pop()
                self.builder += "</section>\n"
                if tag == 0:
                    break
        self.builder += table.to_html(index=False, border=1).replace("\\n"," ")
        self.builder += "\n" 
  
    # --- func to add the unknown label of textbox in the html - classified as <p> tag ---
    def addUnlabelled(self,text):
      if self.stack_for_section:
        is_sentence_completed = text.strip().endswith(self.sentence_completion_punctuation)
        if is_sentence_completed:
          self.builder += text+"<br>"
        else:
          self.builder += text
      else:
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

    # --- func to fit the side notes to their corresponding sections ---
    def find_closest_side_note(self, tb_bbox, side_note_datas, page_height, vertical_threshold_ratio=0.005):
      tb_x0, tb_y0, tb_x1, tb_y1 = tb_bbox
      vertical_threshold = page_height * vertical_threshold_ratio

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
              break  # found one match, stop

      if closest_key:
          del side_note_datas[closest_key]

      return closest_text

    # --- func to add the section labelled textbox in the html ---
    def addSection(self,tb,side_note_datas,page_height,hierarchy_index):
        if not self.stack_for_section:
            self.flushPrevious()
        else:
            # Close everything up to and including the last "section"
            while self.stack_for_section:
                tag = self.stack_for_section.pop()
                self.builder += "</section>\n"
                if tag == hierarchy_index:
                    break

        self.pending_text = ""
        self.pending_tag = None
        text = tb.extract_text_from_tb()
        is_sentence_completed = text.strip().endswith(self.sentence_completion_punctuation)
        side_note_text = self.find_closest_side_note(tb.coords, side_note_datas,page_height)
        if side_note_text:
          match = re.match(r'^(\s*\d+[A-Z]*(?:-[A-Z]+)?\.\s*)(.*)', text.strip())
          if match:
            prefix = match.group(1)
            short_title = side_note_text.strip()
            rest_text = match.group(2).strip()
            rest_text_type = self.findType(rest_text)
            if rest_text_type is None:
                if is_sentence_completed:
                  self.builder += f"<section class=\"section\">{prefix}{short_title}<br>{rest_text}<br>\n"
                  self.stack_for_section.append(hierarchy_index)
                else:
                    self.builder +=f"<section class=\"section\">{prefix}{short_title}<br>{rest_text}\n"
                    self.stack_for_section.append(hierarchy_index)
            else:
                self.builder += f"<section class=\"section\">{prefix}{short_title}\n"
                self.stack_for_section.append(hierarchy_index)
                if is_sentence_completed:
                  self.builder += f"<section class=\"{rest_text_type}\">{rest_text}<br>\n"
                  self.stack_for_section.append(hierarchy_index+1)
                else:
                    self.builder+= f"<section class=\"{rest_text_type}\">{rest_text}\n"
                    self.stack_for_section.append(hierarchy_index+1)
                
        else:
          match = re.match(r'^(\s*\d+[A-Z]*(?:-[A-Z]+)?\.\s*)(.*)', text.strip())
          if match:
            prefix = match.group(1)
            short_title = side_note_text.strip()
            rest_text = match.group(2).strip()
            rest_text_type = self.findType(rest_text)
            if rest_text_type is None:
                if is_sentence_completed:
                  self.builder += f"<section class=\"section\">{prefix}<br>{rest_text}<br>\n"
                  self.stack_for_section.append(hierarchy_index)
                else:
                    self.builder +=f"<section class=\"section\">{prefix}<br>{rest_text}\n"
                    self.stack_for_section.append(hierarchy_index)
            else:
                self.builder += f"<section class=\"section\">{prefix}\n"
                self.stack_for_section.append(hierarchy_index)
                if is_sentence_completed:
                  self.builder += f"<section class=\"{rest_text_type}\">{rest_text}<br>\n"
                  self.stack_for_section.append(hierarchy_index+1)
                else:
                    self.builder += f"<section class=\"{rest_text_type}\">{rest_text}\n"
                    self.stack_for_section.append(hierarchy_index+1)
    
    def findType(self,texts):
      group_re = re.compile(r'^\(([^\s\)]+)\)\s*\S*',re.IGNORECASE)
      if group_re.match(texts.strip()):
         return "subsection"
      return None
    
    
    # --- func to add the subsection labelled textbox in the html ---
    def addSubsection(self,text,hierarchy_index):
        while self.stack_for_section:
          if self.stack_for_section[-1]>=hierarchy_index:
            self.builder += "</section>"
            self.stack_for_section.pop()
          else:
            break
        
        is_sentence_completed = text.strip().endswith(self.sentence_completion_punctuation)
        if is_sentence_completed:
          self.builder += f"<section class=\"subsection\">{text}<br>\n"
          self.stack_for_section.append(hierarchy_index)
        else:
          self.builder += f"<section class=\"subsection\">{text}"
          self.stack_for_section.append(hierarchy_index)
    
    # --- func to add the para labelled textbox in the html --- 
    def addPara(self,text,hierarchy_index):
        while self.stack_for_section:
          if self.stack_for_section[-1] >= hierarchy_index:
            self.builder += "</section>"
            self.stack_for_section.pop()
          else:
             break

        is_sentence_completed = text.strip().endswith(self.sentence_completion_punctuation)
        if is_sentence_completed:
          self.builder += f"<section class=\"paragraph\">{text}<br>\n"
          self.stack_for_section.append(hierarchy_index)
        else:
           self.builder += f"<section class=\"paragraph\">{text}"
           self.stack_for_section.append(hierarchy_index)

    # --- func to add the subpara labelled textbox in the html ---
    def addSubpara(self,text,hierarchy_index):
        while self.stack_for_section:
          if self.stack_for_section[-1] >= hierarchy_index:
            self.builder += "</section>"
            self.stack_for_section.pop()
          else:
             break

        is_sentence_completed = text.strip().endswith(self.sentence_completion_punctuation)
        if is_sentence_completed:
          self.builder += f"<section class=\"subparagraph\">{text}<br>\n"
          self.stack_for_section.append(hierarchy_index)
        else:
           self.builder += f"<section class=\"subparagraph\">{text}"
           self.stack_for_section.append(hierarchy_index)
    
    # ---func to add the textbox labelled as amendments in the html ---
    def addAmendment(self,label,tb,side_notes,pg_height):
        text = tb.extract_text_from_tb()
        if len(label) >1 :
           if label[1]=="title":
              self.builder += f"<p class=\"amendment\">{text}</p>\n"
        else:
          is_sentence_completed = text.strip().endswith(self.sentence_completion_punctuation)         
          if not self.pending_tag and not self.pending_text and is_sentence_completed:
            if self.is_section(text):
              self.add_amendment_section(tb,side_notes,pg_height)
            else:
              self.builder += f"<p class=\"amendment\">{text}</p>\n"
              self.pending_tag = None
              self.pending_text = ""
          elif not self.pending_tag and not self.pending_text and not is_sentence_completed:
            if self.is_section(text):
              self.add_amendment_section(tb,side_notes,pg_height)
            else:
              self.pending_tag = "p"
              self.pending_text = f"<p class=\"amendment\">{text}"
          elif self.pending_text and is_sentence_completed:
              self.pending_text += " "+text.strip() + f"</{self.pending_tag}>\n"
              self.builder += self.pending_text
              self.pending_tag = None
              self.pending_text = ""
          else:
              self.pending_text += text.strip()
    
    def add_amendment_section(self,tb,side_note_datas,page_height):
      self.flushPrevious()
      text = tb.extract_text_from_tb()
      is_sentence_completed = text.strip().endswith(self.sentence_completion_punctuation)
      side_note_text = self.find_closest_side_note(tb.coords, side_note_datas,page_height)
      if side_note_text:
        match = re.match(r'^(\s*[\' | \"]?\d+[A-Z]*(?:-[A-Z]+)?\.\s*)(.*)', text.strip())
        if match:
          prefix = match.group(1)
          short_title = side_note_text.strip()
          rest_text = match.group(2).strip()
          rest_text_type = self.findType(rest_text)
          if rest_text_type is None:
              if is_sentence_completed:
                self.builder += f"<p class=\"amendment\">{prefix}{short_title}<br>{rest_text}</p>\n"
              else:
                  self.pending_text +=f"<p class=\"amendment\">{prefix}{short_title}<br>{rest_text}"
                  self.pending_tag = "p"
          else:
              self.builder += f"<p class=\"amendment\">{prefix}{short_title}</p>\n"
              if is_sentence_completed:
                self.builder += f"<p class=\"amendment\">{rest_text}</p>\n"
              else:
                  self.pending_text += f"<p class=\"amendment\">{rest_text}"
                  self.pending_tag = "p"
              
      else:
        if is_sentence_completed:
          self.builder += f"<p class=\"amendment\">{text}</p>\n"
        else:
          self.pending_text += f"<p class=\"amendment\">{text}"
          self.pending_tag="p"
       
    def is_section(self,texts):
      section_re = re.compile(r'^\s*[\' | \"]?\d+[A-Z]*(?:-[A-Z]+)?\.\s*\S*', re.IGNORECASE)
      texts = texts.strip()
      texts = texts.replace('“', '"').replace('”', '"').replace('‘‘','"').replace('’’','"').replace('‘', "'").replace('’', "'")
      if section_re.match(texts):
         return True 
      return False
    
    # --- func to build the textbox  as html ---
    def build(self, page):
        visited_for_table = set()
        # if not page.is_single_column_page:
        #    page.all_tbs = self.get_orderBy_textboxes(page)
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
               self.addAmendment(label,tb,page.side_notes_datas,page.pg_height)
            elif label == "title":
                self.addTitle(tb,page.pg_width,page.pg_height)
            elif label == "section":
                self.addSection(tb,page.side_notes_datas,page.pg_height,self.hierarchy.index(label))
            elif label == "subsection":
                self.addSubsection(tb.extract_text_from_tb(),self.hierarchy.index(label))
            elif label == "para":
                self.addPara(tb.extract_text_from_tb(),self.hierarchy.index(label))
            elif label == "subpara":
                self.addSubpara(tb.extract_text_from_tb(),self.hierarchy.index(label))
            elif label is None:
                if not self.is_pg_num(tb,page.pg_width):
                  self.addUnlabelled(tb.extract_text_from_tb())

    def is_pg_num(self,tb,pg_width):
        if  tb.width < 0.04 * pg_width:
            return True
        return False
    
    def get_orderBy_textboxes(self,page):
      column_gap = 0.1 * page.pg_width
      items = list(page.all_tbs.items())
      items.sort(key=lambda pair:pair[0].coords[0])

      columns = []
      current = []
      prev_x0 =None

      for tb, label in items:
        if prev_x0 is None or (tb.coords[0]-prev_x0) < column_gap:
           current.append((tb,label))
        else:
           columns.append(current)
           current=[(tb,label)]
        prev_x0 = tb.coords[0]
      if current:
         columns.append(current)
      for col in columns:
         col.sort(key=lambda  pair: -pair[0].coords[3])

      flat = [pair for col in columns for pair in col]
      return OrderedDict(flat)           
    
    def get_html(self):
        self.flushPrevious()
        return self.builder + "\n</body>\n</html>"