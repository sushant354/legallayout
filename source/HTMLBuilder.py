class HTMLBuilder:
    
    def __init__(self):
        # self.builder = '''<!DOCTYPE HTML>\n<html>\n<head><meta charset="UTF-8" /></head>\n<body>\n'''
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
    margin-left: 0%;
  }

  .subsection {
    display: block;
    margin-left: 2%;
  }

  .para {
    display: block;
    margin-left: 5%;
  }

  .subpara {
    display: block;
    margin-left: 8%;
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


    def addTitle(self, tb,pg_width):
        
        if(tb.width > 0.58 * pg_width):
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
    
    def addSection(self,text):
        self.builder += f"<div class=\"section\">{text}</div>\n"

    def addSubsection(self,text):
        self.builder += f"<div class=\"subsection\">{text}</div>\n"
    
    def addPara(self,text):
        self.builder += f"<section class=\"para\">{text}</section>\n"
    
    def addSubpara(self,text):
        self.builder += f"<section class=\"subpara\">{text}</section>\n"

    def build(self, page):
        visited_for_table = set()
        visited_sideNotes = set()

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
                self.addTitle(tb,page.pg_width)
            elif label == "section":
                self.addSection(tb.extract_text_from_tb())
            elif label == "para":
                self.addPara(tb.extract_text_from_tb())
            elif label == "subpara":
                self.addSubpara(tb.extract_text_from_tb())
            elif label is None:
                self.addUnlabelled(tb.extract_text_from_tb())


    def get_html(self):
        return self.builder + "\n</body>\n</html>"




# class HTMLBuilder:
#     def __init__(self):
#         self.html = [
#     '<!DOCTYPE HTML>',
#     '<html>',
#     '<head>',
#     '<meta charset="UTF-8" />',
#     '<style>',
#     '  body {',
#     '    font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;',
#     '    margin: 5%;',
#     '    line-height: 1.6;',
#     '    white-space: normal;',
#     '  }',
#     '  .section {',
#     '    display: block;',
#     '    margin-left: 0%;',
#     '  }',
#     '  .subsection {',
#     '    display: block;',
#     '    margin-left: 5%;',
#     '  }',
#     '  .para {',
#     '    display: block;',
#     '    margin-left: 10%;',
#     '  }',
#     '  .subpara {',
#     '    display: block;',
#     '    margin-left: 15%;',
#     '  }',
#     '  p {',
#     '    white-space: pre-wrap;',
#     '  }',
#     '  table {',
#     '    border-collapse: collapse;',
#     '    width: 100%;',
#     '    font-size: 0.95em;',
#     '  }',
#     '  table, td, th {',
#     '    border: 1px solid #333;',
#     '  }',
#     '  th {',
#     '    display: none;',
#     '  }',
#     '  td {',
#     '    white-space: pre-wrap;',
#     '  }',
#     '</style>',
#     '</head>',
#     '<body>'
# ]


#         self.stack = []
#         self.tag_order = ['section', 'subsection', 'para', 'subpara']
#         self.tag_rank = {tag: i for i, tag in enumerate(self.tag_order)}
#         self.tag_map = {
#             'section': 'div class="section"',
#             'subsection': 'div class="subsection"',
#             'para': 'section class="para"',
#             'subpara': 'section class="subpara"'
#         }

#     def _close_stack(self, level):
#         while self.stack and self.tag_rank[self.stack[-1]] >= level:
#             tag = self.stack.pop()
#             html_tag = self.tag_map[tag].split()[0]
#             self.html.append(f"</{html_tag}>")

#     def _open_tag(self, label, content):
#         html_tag = self.tag_map[label]
#         self.html.append(f"<{html_tag}>{content}")
#         self.stack.append(label)

#     def addTitle(self, tbox):
#         self._close_stack(0)
#         for textline in tbox.findall('.//textline'):
#             text = ''.join(t.text for t in textline.findall('.//text') if t.text)
#             if text.strip():
#                 self.html.append(f"<center><h5>{text.strip()}</h5></center>")

#     def addTable(self, table_df):
#         self._close_stack(0)
#         self.html.append(table_df.to_html(index=False, border=1))

#     def addText(self, text):
#         self._close_stack(0)
#         self.html.append(f"<p>{text.strip()}</p>")

#     def addStructuredText(self, label, text):
#         level = self.tag_rank[label]
#         self._close_stack(level)
#         self._open_tag(label, text)

#     def build(self, page):
#         visited_tables = set()

#         for tb, label in page.all_tbs.items():
#             if isinstance(label, tuple) and label[0] == "table":
#                 table_id = label[1]
#                 if table_id not in visited_tables:
#                     table_df = page.tabular_datas.tables.get(table_id)
#                     if table_df is not None:
#                         self.addTable(table_df)
#                         visited_tables.add(table_id)

#             elif label == "title":
#                 self.addTitle(tb.tbox)

#             elif label in self.tag_order:
#                 text = tb.extract_text_from_tb().strip()
#                 if text:
#                     self.addStructuredText(label, text)

#             elif label is None:
#                 self.addText(tb.extract_text_from_tb().strip())

#         self._close_stack(0)

#     def get_html(self):
#         self.html.append('</body>')
#         self.html.append('</html>')
#         return '\n'.join(self.html)

