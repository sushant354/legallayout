import re
import math

class HTMLBuilder:
    
    def __init__(self):
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


    def addTitle(self, tb,pg_width,pg_height):
        
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
        self.builder += table.to_html(index=False, border=1)
        self.builder += "\n" 

    def addUnlabelled(self,text):
        self.builder += f"<p>{text}</p>\n"

    def get_center(self,bbox):
      x0, y0, x1, y1 = bbox
      return ((x0 + x1) / 2, (y0 + y1) / 2)

    def euclidean_distance(self,c1, c2):
        return math.sqrt((c1[0] - c2[0])**2 + (c1[1] - c2[1])**2)


    def find_closest_side_note(self, tb_bbox, side_note_datas, page_height, vertical_threshold_ratio=0.005):
      tb_x0, tb_y0, tb_x1, tb_y1 = tb_bbox
      vertical_threshold = page_height * vertical_threshold_ratio

      tb_top_right = (tb_x1, tb_y1)

      closest_key = None
      closest_text = None

      for sn_bbox, sn_text in side_note_datas.items():
          sn_x0, sn_y0, sn_x1, sn_y1 = sn_bbox
          sn_top_right = (sn_x1, sn_y1)

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
        text = tb.extract_text_from_tb()
        subsection_re = re.compile(r'^\s*\(\d+\)\s*\S+', re.IGNORECASE) 
        side_note_text = self.find_closest_side_note(tb.coords, side_note_datas,page_height)
        if side_note_text:
          match = re.match(r'^(\d+\.\s*)(.*)', text.strip())
          if match:
            prefix = match.group(1)
            short_title = side_note_text.strip()
            rest = match.group(2).strip()
            if not subsection_re.match(rest):
                self.builder += f"<div class=\"section\">{prefix}{short_title}<br>{rest}</div>\n"
            else:
                self.builder += f"<div class=\"section\">{prefix}{short_title}</div>\n"
                self.builder += f"<div class=\"subsection\">{rest}</div>\n"
                
        else:
          self.builder += f"<div class=\"section\">{text}</div>\n"

    def addSubsection(self,text):
        self.builder += f"<div class=\"subsection\">{text}</div>\n"
    
    def addPara(self,text):
        self.builder += f"<section class=\"para\">{text}</section>\n"
    
    def addSubpara(self,text):
        self.builder += f"<section class=\"subpara\">{text}</section>\n"

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
        return self.builder + "\n</body>\n</html>"


