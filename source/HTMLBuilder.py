import re
import math
import numpy as np
import logging
import pandas as pd
from difflib import SequenceMatcher
import copy
from pathlib import Path
from .Table import TableBuilder
from .SentenceEndDetector import LegalSentenceDetector
from .NormalizeText import NormalizeText


RELEVANT_TAGS = {"body", "section", "p", "table", "tr", "td", "a", "blockquote", "br",
                 "h4", "center", "li"}
VOID_TAGS = {"br"}
FOOTNOTE_MARKER_RE = re.compile(r'\{\{\^\{\{FOOTNOTE\s+(\d+)\}\}\}\}')
FOOTNOTE_ABBREVIATION_RE = re.compile(
    r'(?:\b[a-z]\.){2,}$|\b(?:no|ref)\.$',
    re.IGNORECASE
)

class HTMLBuilder(TableBuilder):
    
    def __init__(self, unique_images, all_footnote_text, sentence_completion_punctuation = tuple(), pdf_type = None):
        TableBuilder.__init__(self)
        self.logger = logging.getLogger(__name__)
        self.pdf_type = pdf_type
        self.all_footnote_text = all_footnote_text
        self.current_page_num = None
        self.footnote_refs_used = []
        self.toc_html = None
        self.toc_rendered = False
        self.pending_text = ""
        self.pending_tag = None
        self.sentence_completion_punctuation = sentence_completion_punctuation
        self.stack_for_section = []
        self.stack_for_level = []
        self.hierarchy = ("section","subsection","para","subpara","subsubpara")
        self.level_hierarchy = ('level1', 'level2', 'level3', 'level4','level5')
        self._sentence_detector = LegalSentenceDetector()
        self.is_real_sentence_end = self._sentence_detector.is_real_sentence_end
        self.previous_sentence_end_status = True
        self.is_pre_added = False
        self._base_normalize_text = NormalizeText().normalize_text
        self.normalize_text = self._normalize_and_linkify_footnotes
        self.builder = ""
        self.unique_images = unique_images
        self.pending_header_footer = []
        self.pending_pre_lines = []
        self.main_builder = '''<!DOCTYPE HTML>
<html>
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {
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

  .paragraph {
    display: block;
    margin-left: 8%;
  }

  .subparagraph {
    display: block;
    margin-left: 11%;
  }

  .amendment {
    display: block;
    margin-left: 20%;
  }

  .footnotes {
    display: block;
    font-size: 0.9em;
    border-top: 1px solid #999;
    margin-top: 1em;
  }

  sup a {
    text-decoration: none;
  }
  p, pre {
    white-space: pre-wrap;
  }

  p.figure-text {
    display: none;
  }

  span.header-text{
    display:None;
  }
  
  span.footer-text{
    display:None;
  }
  
  h4 {
    text-align: center;
  }

  table {
    border-collapse: collapse;
    width: 100%;
    font-size: 0.95em;
  }

  table, td, th {
    border: 1px solid #333;
  }

  td {
    white-space: pre-wrap;
  }

  nav.toc .toc-title {
    font-weight: bold;
  }

  nav.toc .toc-table {
    border-collapse: collapse;
    width: 100%;
  }

  nav.toc .toc-table td {
    border: none;
    padding: 0.15em 0.4em;
    white-space: pre-wrap;
  }

  nav.toc .toc-page {
    text-align: right;
    color: #555;
    white-space: nowrap;

  img {
    display: block;
    max-width: 100%;
    height: auto;
  }
</style>
</head>
<body>
'''

   
    # --- func to flush previous textbox text --
    def flushPrevious(self):
      try:
        if self.pending_tag and self.pending_text:
          self.pending_text += f"</{self.pending_tag}>\n"
          self.builder += ' '+self.pending_text
          self.pending_text =""
          self.pending_tag = None
      except Exception as e:
          self.logger.exception("Error while flushing previous content - [%s] in html: %s",self.pending_text,e)

    def flush_pre_lines(self):
        if self.pending_pre_lines:
            self.render_pre_block(self.pending_pre_lines)
            self.pending_pre_lines = []

    
    def close_levels(self):
        try:
            while self.stack_for_level:
                tag = self.stack_for_level.pop()
                if tag == 0:
                    self.builder += "</p>\n"
                else:
                    self.builder += "</li>\n</ul>\n"
        except Exception as e:
           self.logger.warning(f'when closing levels: {e}')
          
    def check_for_last_token(self, html):
      last_token, last_tag = self.get_last_token(html)
      if last_tag and last_token: 
          if not last_token.endswith(('.','?','!',';',':',":-", "---", "...", '—',':','."', ".'",';"',";'", '…')): #, '-'
             return True, last_tag
      return False, last_tag 
    
    def handle_pending_text_continuation(self, text, next_text,at_page_end, tb, next_text_tb, pg_height, pg_width):
        if self.pending_text and self.pending_tag:
                status, _ = self.check_for_last_token(self.pending_text)
                if status:
                    is_sentence_completed = self.is_real_sentence_end(text, next_text, at_page_end, tb, next_text_tb, pg_height, pg_width)
                    if is_sentence_completed:
                        self.pending_text += (" "+text.strip())
                        self.pending_text += f"</{self.pending_tag}>\n"
                        self.builder += self.pending_text
                        self.pending_tag = None
                        self.pending_text = ""
                        self.previous_sentence_end_status = is_sentence_completed
                        return True
                    else:
                        self.pending_text += (' '+text.strip())
                        self.previous_sentence_end_status = is_sentence_completed
                        return True
        return False
    
    def handle_continuation(self, html, text, next_text, at_page_end, tb, next_text_tb, pg_height, pg_width):
        status, last_tag = self.check_for_last_token(html)
        if status and last_tag:
            if self.stack_for_section:
               self.builder += (" "+text.strip()+" ")
               return True
            if self.stack_for_level:
               self.builder += (" "+text.strip()+" ")
               self.previous_sentence_end_status = self.is_real_sentence_end(text, next_text, at_page_end, tb, next_text_tb, pg_height, pg_width)
               return True
            is_sentence_completed = self.is_real_sentence_end(text, next_text, at_page_end, tb, next_text_tb, pg_height, pg_width)
            if is_sentence_completed:
                self.pending_text += (" "+text.strip())
                self.pending_text += f"</{last_tag}>\n"
                self.builder += self.pending_text
                self.pending_tag = None
                self.pending_text = ""
                self.previous_sentence_end_status = is_sentence_completed
                return True
            else:
                self.pending_tag = last_tag
                self.pending_text += (' '+text.strip())
                self.previous_sentence_end_status = is_sentence_completed
                return True
        return False
    # --- func to add Title in the html ---
    def addTitle(self, tb,pg_width,pg_height, next_text, next_text_tb,  at_page_end,next_label = None):
        try:
          text = self.normalize_text(tb.extract_text_from_tb()).strip()
          #original
          sebi_level_close_re = re.compile(r'^(?:(?:Date|Dated)\s*[:\-]{1}\s*(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|[A-Za-z]+\s+\d{1,2},\s*\d{4})|(?:Place)\s*[:\-]{1}\s*[A-Z][A-Za-z .,&-]*|\(.*?(?:Judgment\s+pronounced|Order\s+pronounced|Decision\s+pronounced).*?\)|Sd/-)$', re.IGNORECASE)
          if self.handle_pending_text_continuation(text, next_text,at_page_end, tb, next_text_tb, pg_height, pg_width):
                return
                  
          if not self.previous_sentence_end_status and self.handle_continuation(self.builder, text, next_text,at_page_end, tb, next_text_tb, pg_height, pg_width):
                return
          
          self.previous_sentence_end_status = self.is_real_sentence_end(text, next_text, at_page_end, tb, next_text_tb, pg_height, pg_width)
          if not self.stack_for_section:
              self.flushPrevious()
          else:
              # Close everything up to and including the last "section"
              while self.stack_for_section:
                  tag = self.stack_for_section.pop()
                  self.builder += "</section>\n"
                  # if tag == 0:
                  #     break 
           
          if not self.stack_for_level:
              self.flushPrevious()
          else:
              if text and sebi_level_close_re.match(text):
                  self.close_levels()
              elif self.stack_for_level: #and self.stack_for_level[-1] == 0:
                 self.close_levels()
              elif self.stack_for_level and next_label == 'level1':
                  while self.stack_for_level:
                          if len(self.stack_for_level) >= 2:
                            if self.stack_for_level[-1] == self.stack_for_level[-2]:
                                tag = self.stack_for_level.pop()
                                if tag == 0:
                                    self.builder += "</p>\n"
                            else:
                                tag = self.stack_for_level.pop()
                                if tag == 0:
                                    self.builder += "</p>\n"
                                else:
                                    self.builder += "</li>\n</ul>\n"
                          else:
                              tag = self.stack_for_level.pop()
                              if tag == 0:
                                    self.builder += "</p>\n"
                              else:
                                    self.builder += "</li>\n</ul>\n"
              
          if(tb.width > 0.58 * pg_width and tb.height > 0.15 * pg_height):
              self.builder += f"<p class=\"preamble\">{self.normalize_text(tb.extract_text_from_tb())}</p>\n"
          else:
              doc = ''
              for textline in tb.tbox.findall('.//textline'):
                  line_texts = []
                  for text in textline.findall('.//text'):
                      if text.text:
                          line_texts.append(text.text)
                  line = ''.join(line_texts).replace("\n", " ").strip()
                  if line:
                      doc += f"<h4>{self.normalize_text(line)}</h4>\n"
              self.builder += doc
        except Exception as e:
          self.logger.exception("Error while adding title - [%s] in html: %s",tb.extract_text_from_tb(),e)
    
    # --- func to add the table in the html ---
    def addTable(self, table):
        try:
          if (not self.stack_for_section):
              self.flushPrevious()
          elif (not self.stack_for_level):
              self.flushPrevious()
          # else:
          #     # Close everything up to and including the last "section"
          #     while self.stack_for_section:
          #         tag = self.stack_for_section.pop()
          #         self.builder += "</section>\n"
          #         if tag == 0:
          #             break
          # if self.stack_for_level:
          #    self.close_levels()
          if self.stack_for_level and self.stack_for_level[-1] == 0:
             self.close_levels()
          table_html = (
              table.replace('\n', '&#10;', regex=True)   # preserve newline inside HTML
                  .to_html(escape=False, index=False, header=False)
                  .replace("<table", "<table style='white-space: pre-wrap;'")
            )
          self.builder += self.normalize_text(table_html)
          # self.builder += self.normalize_text(table.to_html(index=False, header = False, border=1).replace("\\n",""))
          self.builder += "\n" 
        except Exception as e:
            self.logger.exception("Error while adding table in html - %s .\nTable preview\n",e, table.head().to_string(index=False))
  
    def close_sections(self):
       while self.stack_for_section:
          self.stack_for_section.pop()
          self.builder += "</section>\n"
       
    # --- func to add the unknown label of textbox in the html - classified as <p> tag ---
    def addItalicBlockQuote(self, text, next_text, text_tb, next_text_tb, pg_height, pg_width, at_page_end, tb):
        try:
            is_sentence_completed = self.is_real_sentence_end(text, next_text, at_page_end, text_tb, next_text_tb, pg_height, pg_width)
            if self.pending_tag and self.pending_tag != 'blockquote':
                self.pending_text += f"</{self.pending_tag}>\n"
                self.builder += self.pending_text
                self.pending_tag = None
                self.pending_text = ""
            elif self.stack_for_level and self.stack_for_level[-1] == 0:
                self.close_levels()
               
            if not self.previous_sentence_end_status and self.handle_continuation(self.builder, text, next_text,at_page_end, tb, next_text_tb, pg_height, pg_width):
                return
            if (not self.pending_tag) and (not self.pending_text) and is_sentence_completed:
              self.builder += f"<blockquote>{text}</blockquote>\n"
              self.pending_tag = None
              self.pending_text = ""
              self.previous_sentence_end_status = is_sentence_completed
            elif (not self.pending_tag) and (not self.pending_text) and (not is_sentence_completed):
              self.pending_tag = "blockquote"
              self.pending_text = f"<blockquote>{text}"
              self.previous_sentence_end_status = is_sentence_completed
            elif self.pending_text and self.pending_tag and is_sentence_completed:
                self.pending_text += (" "+text.strip())
                self.pending_text += f"</{self.pending_tag}>\n"
                self.builder += self.pending_text
                self.pending_tag = None
                self.pending_text = ""
                self.previous_sentence_end_status = is_sentence_completed

            else:
                self.pending_text += (' '+text.strip())
                self.previous_sentence_end_status = is_sentence_completed
        except Exception as e:
            self.logger.exception("Error while adding italic blockquote text [%s] : %s",text, e)

    def addUnlabelled(self,text, next_text, text_tb, next_text_tb, pg_height, pg_width, at_page_end):
      #original
      sebi_level_close_re = re.compile(r'^(?:(?:Date|Dated)\s*[:\-]{1}\s*(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|[A-Za-z]+\s+\d{1,2},\s*\d{4})|(?:Place)\s*[:\-]{1}\s*[A-Z][A-Za-z .,&-]*|\(.*?(?:Judgment\s+pronounced|Order\s+pronounced|Decision\s+pronounced).*?\)|Sd/-)$', re.IGNORECASE)
      
      try:
        if self.stack_for_section:
          if re.fullmatch(r'—{3,}', text.strip()):
            self.close_sections()
            self.builder += f"<center>{text}</center>"
            return
          if self.pdf_type != 'acts':
            is_sentence_completed = self.is_real_sentence_end(text, next_text, at_page_end, text_tb, next_text_tb, pg_height, pg_width)
          else:
            is_sentence_completed = text.strip().endswith(self.sentence_completion_punctuation)
          if is_sentence_completed:
            self.builder += (' ' +text +"<br>")
          else:
            self.builder += (' ' + text)
        elif self.stack_for_level:
          if text and sebi_level_close_re.match(text):
                  self.close_levels()
                  # return
          if self.pdf_type != 'acts':
            is_sentence_completed = self.is_real_sentence_end(text, next_text, at_page_end, text_tb, next_text_tb, pg_height, pg_width)
          else:
            is_sentence_completed = text.strip().endswith(self.sentence_completion_punctuation)
          if is_sentence_completed:
            self.builder += (' ' +text +"<br>")
            self.previous_sentence_end_status = is_sentence_completed
          else:
            if text in set(["•","▪","▫","✓","✕","o"]) and self.pending_text == "":
               self.pending_tag = 'blockquote'
               self.pending_text = f'<blockquote>{text}'   
               self.previous_sentence_end_status = is_sentence_completed
            else:   
                self.builder += (' ' + text)
                self.previous_sentence_end_status = is_sentence_completed
        else:
            if self.pdf_type != 'acts':
              is_sentence_completed = self.is_real_sentence_end(text, next_text, at_page_end, text_tb, next_text_tb, pg_height, pg_width)
            else:
              is_sentence_completed = text.strip().endswith(self.sentence_completion_punctuation)
            if (not self.pending_tag) and (not self.pending_text) and is_sentence_completed:
              self.builder += f"<p>{text}</p>\n"
              self.pending_tag = None
              self.pending_text = ""
              self.previous_sentence_end_status = is_sentence_completed
            elif (not self.pending_tag) and (not self.pending_text) and (not is_sentence_completed):
              self.pending_tag = "p"
              self.pending_text = f"<p>{text}"
              self.previous_sentence_end_status = is_sentence_completed
            elif self.pending_text and self.pending_tag and is_sentence_completed:
                self.pending_text += (" "+text.strip())
                self.pending_text += f"</{self.pending_tag}>\n"
                self.builder += self.pending_text
                self.pending_tag = None
                self.pending_text = ""
                self.previous_sentence_end_status = is_sentence_completed
            else:
                self.pending_text += (' '+text.strip())
                self.previous_sentence_end_status = is_sentence_completed

      except Exception as e:
        self.logger.exception("Error while adding unlabelled text [%s] : %s",text, e)

    def get_center(self,bbox):
      x0, y0, x1, y1 = bbox
      return ((x0 + x1) / 2, (y0 + y1) / 2)

    def euclidean_distance(self,c1, c2):
        return math.sqrt((c1[0] - c2[0])**2 + (c1[1] - c2[1])**2)

    # --- func to fit the side notes to their corresponding sections ---
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
      
    def get_last_token(self, html):
        tag_stack = []

        # Regex to find all opening and closing tags
        for match in re.finditer(r'<(/?)(\w+)[^>]*?>', html):
            closing, tag_name = match.groups()
            tag_name = tag_name.lower()
            
            # Only process relevant tags
            if tag_name not in RELEVANT_TAGS:
                continue

            # Ignore void/self-closing tags
            if tag_name in VOID_TAGS:
                continue

            if closing:  # closing tag
                if tag_name in tag_stack:
                    last_index = len(tag_stack) - 1 - tag_stack[::-1].index(tag_name)
                    tag_stack.pop(last_index)
            else:  # opening tag
                tag_stack.append(tag_name)

        # No unclosed tags
        if not tag_stack:
            return None, None

        # Last unclosed tag
        last_open_tag = tag_stack[-1]

        # Extract content inside last unclosed tag from the end
        pattern = rf'<{last_open_tag}[^>]*?>([^<]*)$'
        m = re.search(pattern, html, re.DOTALL)
        if m:
            content = m.group(1).strip()
            tokens = content.split()
            last_token = tokens[-1] if tokens else ""
            return last_token, last_open_tag
        else:
            return "", last_open_tag

    # --- func to add the section labelled textbox in the html ---
    def addSection(self,tb,side_note_datas,page_height,hierarchy_index):
        try:
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
          text = self.normalize_text(tb.extract_text_from_tb())
          is_sentence_completed = text.strip().endswith(self.sentence_completion_punctuation)
          side_note_text = self.find_closest_side_note(tb.coords, side_note_datas,page_height)
          self.logger.debug("Side note matched for section text [%s] : %s",text, side_note_text)
          if side_note_text:
            match = re.match(r'^(\s*\d+[A-Z]*(?:-[A-Z]+)?\.\s*)(.*)', text.strip())
            if match:
              prefix = match.group(1)
              short_title = self.normalize_text(side_note_text.strip())
              rest_text = match.group(2).strip()
              rest_text_type = self.findType(rest_text)
              if rest_text_type is None:
                  if is_sentence_completed:
                    self.builder += f"<section class=\"section\">{prefix}{short_title}<br>{rest_text}\n" #<br>
                    self.stack_for_section.append(hierarchy_index)
                  else:
                      self.builder +=f"<section class=\"section\">{prefix}{short_title}<br>{rest_text}\n"
                      self.stack_for_section.append(hierarchy_index)
              else:
                  self.builder += f"<section class=\"section\">{prefix}{short_title}\n"
                  self.stack_for_section.append(hierarchy_index)
                  if is_sentence_completed:
                    self.builder += f"<section class=\"{rest_text_type}\">{rest_text}\n" #<br>
                    self.stack_for_section.append(hierarchy_index+1)
                  else:
                      self.builder+= f"<section class=\"{rest_text_type}\">{rest_text}\n"
                      self.stack_for_section.append(hierarchy_index+1)
                  
          else:
            match = re.match(r'^(\s*\d+[A-Z]*(?:-[A-Z]+)?\.\s*)(.*)', text.strip())
            if match:
              prefix = match.group(1)
              rest_text = match.group(2).strip()
              rest_text_type = self.findType(rest_text)
              if rest_text_type is None:
                  if is_sentence_completed:
                    self.builder += f"<section class=\"section\">{prefix}{rest_text}\n" #<br>{rest_text}\n" #<br>
                    self.stack_for_section.append(hierarchy_index)
                  else:
                      self.builder +=f"<section class=\"section\">{prefix} {rest_text}\n" #<br>{rest_text}\n"
                      self.stack_for_section.append(hierarchy_index)
              else:
                  self.builder += f"<section class=\"section\">{prefix}\n"
                  self.stack_for_section.append(hierarchy_index)
                  if is_sentence_completed:
                    self.builder += f"<section class=\"{rest_text_type}\">{rest_text}\n" #<br>
                    self.stack_for_section.append(hierarchy_index+1)
                  else:
                      self.builder += f"<section class=\"{rest_text_type}\">{rest_text}\n"
                      self.stack_for_section.append(hierarchy_index+1)
          self.logger.debug("Opened section at hierarchy level: %d",hierarchy_index)
        except Exception as e:
          self.logger.exception("Error while adding section [%s]: %s",tb.extract_text_from_tb(), e)


    
    def findType(self,texts):
      group_re = re.compile(r'^\(\s*([^\s\)]+)\s*\)\s*\S*', re.IGNORECASE)

      if group_re.match(texts.strip()):
         return "subsection"
      return None
    
    
    # --- func to add the subsection labelled textbox in the html ---
    def addSubsection(self,text,hierarchy_index):
        try:
          while self.stack_for_section:
            if self.stack_for_section[-1]>=hierarchy_index:
              self.builder += "</section>"
              popped_index = self.stack_for_section.pop()
              self.logger.debug("Closed section at hierarchy level: %d", popped_index)
            else:
              break
          
          is_sentence_completed = text.strip().endswith(self.sentence_completion_punctuation)
          if is_sentence_completed:
            self.builder += f"<section class=\"subsection\">{text}\n" #<br>
            self.stack_for_section.append(hierarchy_index)
          else:
            self.builder += f"<section class=\"subsection\">{text}"
            self.stack_for_section.append(hierarchy_index)
        
          self.logger.debug("Opened section at hierarchy level: %d",hierarchy_index)
        except Exception as e:
          self.logger.exception("Error while adding subsection [%s]: %s",text, e)

    
    # --- func to add the para labelled textbox in the html --- 
    def addPara(self,text,hierarchy_index):
        try:
          while self.stack_for_section:
            if self.stack_for_section[-1] >= hierarchy_index:
              self.builder += "</section>"
              popped_index = self.stack_for_section.pop()
              self.logger.debug("Closed section at hierarchy level: %d", popped_index)
            else:
              break

          is_sentence_completed = text.strip().endswith(self.sentence_completion_punctuation)
          if is_sentence_completed:
            self.builder += f"<section class=\"paragraph\">{text}\n" #<br>
            self.stack_for_section.append(hierarchy_index)
          else:
            self.builder += f"<section class=\"paragraph\">{text}"
            self.stack_for_section.append(hierarchy_index)
            self.logger.debug("Opened section at hierarchy level: %d", hierarchy_index)
        except Exception as e:
          self.logger.exception("Error while adding para [%s]: %s",text,e)


    # --- func to add the subpara labelled textbox in the html ---
    def addSubpara(self,text,hierarchy_index):
        try:
          while self.stack_for_section:
            if self.stack_for_section[-1] >= hierarchy_index:
              self.builder += "</section>"
              popped_index = self.stack_for_section.pop()
              self.logger.debug("Closed section at hierarchy level: %d", popped_index)
            else:
              break

          is_sentence_completed = text.strip().endswith(self.sentence_completion_punctuation)
          if is_sentence_completed:
            self.builder += f"<section class=\"subparagraph\">{text}\n" #<br>
            self.stack_for_section.append(hierarchy_index)
          else:
            self.builder += f"<section class=\"subparagraph\">{text}"
            self.stack_for_section.append(hierarchy_index)
          self.logger.debug("Opened Section at hierarchy level : %d",hierarchy_index)
        
        except Exception as e:
          self.logger.exception("Error while adding subpara [%s]: %s",text,e)
        
    def addBlockQuote(self, text, next_text, text_tb, next_text_tb, pg_height, pg_width, at_page_end, tb):
        text = text.replace('“', '"').replace('”', '"').replace('‘‘','"').replace('’’','"').replace('‘', "'").replace('’', "'")
        is_sentence_completed = self.is_real_sentence_end(text, next_text, at_page_end, text_tb, next_text_tb, pg_height, pg_width) #text.strip().endswith(self.sentence_completion_punctuation)

        if self.pending_tag and self.pending_tag != "blockquote":
            self.pending_text += f"</{self.pending_tag}>\n"
            self.builder += self.pending_text
            self.pending_tag = None
            self.pending_text = ""
        elif self.stack_for_level and self.stack_for_level[-1] == 0:
            self.close_levels()
            
        if not self.previous_sentence_end_status and self.handle_continuation(self.builder, text, next_text,at_page_end, tb, next_text_tb, pg_height, pg_width):
                return
        if (not self.pending_tag) and (not self.pending_text) and is_sentence_completed:
          self.builder += f"<blockquote>{text}</blockquote>\n"
          self.pending_tag = None
          self.pending_text = ""
          self.previous_sentence_end_status = is_sentence_completed
        elif (not self.pending_tag) and (not self.pending_text) and (not is_sentence_completed):
          self.pending_tag = "blockquote"
          self.pending_text = f"<blockquote>{text}"
          self.previous_sentence_end_status = is_sentence_completed
        elif self.pending_text and self.pending_tag and is_sentence_completed:
            self.pending_text += (" "+text.strip())
            self.pending_text += f"</{self.pending_tag}>\n"
            self.builder += self.pending_text
            self.pending_tag = None
            self.pending_text = ""
            self.previous_sentence_end_status = is_sentence_completed
        else:
            self.pending_text += (' '+text.strip())
            self.previous_sentence_end_status = is_sentence_completed

    # ---func to add the textbox labelled as amendments in the html ---
    def addAmendment(self,label,tb,side_notes,pg_height):
        
        text = self.normalize_text(tb.extract_text_from_tb())
        try:
          if len(label) >1 :
            if label[1]=="title":
                self.logger.debug("The text [%s] is a title block of Amendments.",text)
                self.builder += f"<p class=\"amendment\">{text}</p>\n"
          else:
            is_sentence_completed = text.strip().endswith(self.sentence_completion_punctuation)
            if not self.pending_tag and not self.pending_text and is_sentence_completed:
              if self.is_section(text):
                self.logger.debug("Text detected as section; delegating to add_amendment_section.")
                self.add_amendment_section(tb,side_notes,pg_height)    
              else:
                self.builder += f"<p class=\"amendment\">{text}</p>\n"
                self.pending_tag = None
                self.pending_text = ""
            elif not self.pending_tag and not self.pending_text and not is_sentence_completed:
              if self.is_section(text):
                self.logger.debug("Unfinished section-like text; delegating to add_amendment_section.")
                self.add_amendment_section(tb,side_notes,pg_height)
              else:
                self.pending_tag = "p"
                self.pending_text = f"<p class=\"amendment\">{text}"
            elif self.pending_text and self.pending_tag and is_sentence_completed:
                self.pending_text += (" "+text.strip())
                self.pending_text += f"</{self.pending_tag}>\n"
                self.builder += self.pending_text
                self.logger.debug("Completed pending amendment: %s", self.pending_text.strip())
                self.pending_tag = None
                self.pending_text = ""
            else:
                self.pending_text += (' '+text.strip())
                self.logger.debug("Continuing pending amendment: %s", self.pending_text)
        except Exception as e:
          self.logger.exception("Error while adding amendment [%s]: %s", text, e)
    
    def add_amendment_section(self,tb,side_note_datas,page_height):
      self.flushPrevious()
      text = self.normalize_text(tb.extract_text_from_tb())
      try:
        is_sentence_completed = text.strip().endswith(self.sentence_completion_punctuation)
        side_note_text = self.find_closest_side_note(tb.coords, side_note_datas,page_height)
        self.logger.debug("Side note matched for the amendments [%s]: %s",text, side_note_text)
        if side_note_text:
          match = re.match(r'^(\s*[\' | \"]?\d+[A-Z]*(?:-[A-Z]+)?\.\s*)(.*)', text.strip())
          if match:
            prefix = match.group(1)
            short_title = self.normalize_text(side_note_text.strip())
            rest_text = match.group(2).strip()
            rest_text_type = self.findType(rest_text)
            self.logger.debug("Match groups — Prefix: '%s', Short Title: '%s', Remain Text: '%s', Remain Text Type: %s",
                                  prefix, short_title, rest_text, rest_text_type)
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
        else:
          if is_sentence_completed:
            self.builder += f"<p class=\"amendment\">{text}</p>\n"
          else:
            self.pending_text += f"<p class=\"amendment\">{text}"
            self.pending_tag="p"
      except Exception as e:
        self.logger.exception("Error in add_amendment_section [%s]: %s",text, e)
       
    def is_section(self,texts):
      section_re = re.compile(r'^\s*[\' | \"]?\d+[A-Z]*(?:-[A-Z]+)?\s*\.\s*\S*', re.IGNORECASE) # 
      texts = texts.strip()
      texts = texts.replace('“', '"').replace('”', '"').replace('‘‘','"').replace('’’','"').replace('‘', "'").replace('’', "'")
      if section_re.match(texts):
         return True 
      return False
    
    def is_nextlabel_blockquote(self, label, nextlabel):
        if isinstance(label, tuple) and isinstance(nextlabel, tuple):
          if label[1] == 'blockquote' and nextlabel[1] == 'blockquote':
             return True
        return False
    
    from pathlib import Path

    def extract_img_path(self, full_path):
      try:
        p = Path(full_path)
        parts = p.parts

        if 'manifest' in parts:
            idx = parts.index('manifest')
            return str(Path(*parts[idx:]))
        else:
            return None  # or raise error
      except Exception as e:
         self.logger.warning(f'Extracting img path while building html {e}')
         return None
    
    def addFigure(self, tb, page):
      try:
        if tb.figname in self.unique_images:
            if self.pending_tag and self.pending_text:
              self.flushPrevious()
            if self.pending_table:
              self.flushTables()
            # if self.stack_for_level:
            #   self.close_levels()
             
            img_data = self.unique_images[tb.figname]
            img_path = self.extract_img_path(img_data.get("path",""))
            width = img_data.get("width")
            height = img_data.get("height")

            size_attrs = ""
            if width:
                size_attrs += f' width="{width}"'
            if height:
                size_attrs += f' height="{height}"'

            self.builder += f'<img src="{img_path}"{size_attrs} loading="lazy">\n'

            text_content = self.unique_images[tb.figname].get("text", "")
            if text_content:
                self.builder += f'<p class="figure-text">{text_content}</p>\n'
      except Exception as e:
         self.logger.warning(f'While adding figure to html, {e}')
   
    def add_pre(self):
        html = copy.deepcopy(self.builder)
        cleaned_html = re.sub(r'</?center>', '', html)
        cleaned_html = re.sub(r'</?h4>', '', cleaned_html)
        self.main_builder += f'<pre>\n{cleaned_html}</pre>\n'
        self.builder = ""
        self.is_pre_added = True

    def check_for_pre_ended(self, text, label):
        text = text.strip()
        if not text:
           return False
        background_re = re.compile(
                            r'(?i)\bB\s*[\W_]*A\s*[\W_]*C\s*[\W_]*K\s*[\W_]*G\s*[\W_]*R\s*[\W_]*O\s*[\W_]*U\s*[\W_]*N\s*[\W_]*D\b'
                        )

        toc_re = re.compile(
            r'(?i)\b(?:table\s*[\W_]*of\s*[\W_]*contents?|table\s*[\W_]*contents?|contents?)\b',
            re.IGNORECASE
        )

        section_re = re.compile(
            r'^(?!\s*\d{1,4}\.\d{1,4}\.\d{2,4})\s*[1]\d{0,2}[A-Z]?\.(?!\))(?:\s+.*)?$',
            # re.IGNORECASE
        )

        facts_case_pattern = re.compile(
                      r'f\s*a\s*c\s*t\s*s\s*.*o\s*f\s*.*t\s*h\s*e\s*.*c\s*a\s*s\s*e\s*.*i\s*n\s*.*b\s*r\s*i\s*e\s*f',
                      re.IGNORECASE
                  )
        if background_re.search(text):
            self.add_pre()
        elif toc_re.search(text):
            self.add_pre()
        elif section_re.search(text):
            self.add_pre()
        elif facts_case_pattern.search(text):
            self.add_pre()
        
    def add_header(self, text):
        if not self.pending_table:
            if self.pending_tag and self.pending_text:
                self.pending_text += f'<span class="header-text">{text}</span>\n'
            else:
                self.builder += f'<span class="header-text">{text}</span>\n'
        
        else:
           header = f'<span class="header-text">{text}</span>\n'
           self.pending_header_footer.append(header)

    def add_footer(self, text):
        if not self.pending_table:
            if self.pending_tag and self.pending_text:
                self.pending_text += f'<span class="footer-text">{text}</span>\n'
            else:
                self.builder += f'<span class="footer-text">{text}</span>\n' 

        else:
           footer = f'<span class="footer-text">{text}</span>\n'
           self.pending_header_footer.append(footer) 

    def flush_pending_header_footer(self):
        if self.pending_header_footer:
            self.builder += '\n'
            for item in self.pending_header_footer:
                self.builder += item
            self.pending_header_footer = []
        
    def _normalize_and_linkify_footnotes(self, text):
        text = self._base_normalize_text(text)
        if not text or self.current_page_num is None:
            return text

        def replace(match):
            footnote_num = match.group(1)
            if footnote_num not in self.footnote_refs_used:
                self.footnote_refs_used.append(footnote_num)
            anchor = f"fn-{self.current_page_num}-{footnote_num}"
            ref = f"fnref-{self.current_page_num}-{footnote_num}"
            return f'<sup id="{ref}"><a href="#{anchor}">{footnote_num}</a></sup>'

        return FOOTNOTE_MARKER_RE.sub(replace, text)

    def arrange_footnote_sentences(self, raw_text):
        rawlines = raw_text.split('\n')
        arranged_text = []
        current_sentence = ""

        for line in rawlines:
            if current_sentence:
                current_sentence += " " + line
            else:
                current_sentence = line

            is_sentence_completed = (current_sentence.endswith(
                                    self.sentence_completion_punctuation)
                                    and
                                    not FOOTNOTE_ABBREVIATION_RE.search(current_sentence)
                                    )

            if is_sentence_completed:
                arranged_text.append(current_sentence.strip())
                current_sentence = ""

        if current_sentence:
            arranged_text.append(current_sentence.strip())

        return " ".join(arranged_text)

    def render_footnote_section(self):
        if not self.footnote_refs_used:
            return

        page_footnote_text = self.all_footnote_text.get(self.current_page_num, {})
        items = []

        for footnote_num in sorted(self.footnote_refs_used, key=lambda n: int(n) if n.isdigit() else n):
            if footnote_num not in page_footnote_text:
                continue

            body = self.arrange_footnote_sentences(page_footnote_text[footnote_num])
            anchor = f"fn-{self.current_page_num}-{footnote_num}"
            ref = f"fnref-{self.current_page_num}-{footnote_num}"
            items.append(f'<li id="{anchor}" value="{footnote_num}">{body} <a href="#{ref}">↩</a></li>\n')

        if items:
            self.builder += '<section class="footnotes">\n<hr>\n<ol>\n'
            for item in items:
                self.builder += item
            self.builder += '</ol>\n</section>\n'

        self.footnote_refs_used = []

    def build(self, page, has_side_notes):#, section_end_page):
        visited_for_table = set()
        self.current_page_num = int(page.pg_num)
        self._sentence_detector.column_bounds = page.column_bounds if page.is_multicolumn else None
        # try:
        #   if section_end_page and int(section_end_page)+1 == int(page.pg_num):
        #       while self.stack_for_section:
        #           popped_index = self.stack_for_section.pop()
        #           self.builder += "</section>"
        #           self.logger.debug("Closed section at hierarchy level: %d", popped_index)
        # except Exception as e:
        #     self.logger.warning(f'when closing sections tag after section end page - {e}')
              
        all_items = list(page.all_tbs.items())
        for idx, (tb, label) in enumerate(all_items):
            next_text = None
            next_text_tb = None
            if idx + 1 < len(all_items):
                next_tb, next_label = all_items[idx + 1]
                
                # if next_label is None:  # only consider unlabelled continuation
                #     next_text = self.normalize_text(next_tb.extract_text_from_tb())
                #     next_text_tb = next_tb
                # elif self.is_nextlabel_blockquote(label, next_label):
                #     next_text = self.normalize_text(next_tb.extract_text_from_tb())
                #     next_text_tb = next_tb
                # elif next_label[:-1] == 'level' or next_label == 'blockquote' or next_label == 'title' or (isinstance(next_label, tuple) and next_label[1] == 'blockquote'):
                #     next_text = self.normalize_text(next_tb.extract_text_from_tb())
                #     next_text_tb = next_tb
                if next_label not in ("figure", "header", "footer"):
                    next_text = self.normalize_text(next_tb.extract_text_from_tb())
                    next_text_tb = next_tb

            at_page_end = (idx == len(all_items) - 1)

            if label not in ("pre", "pre_header") and self.pending_pre_lines:
                self.flushPrevious()
                self.flush_pre_lines()

            if label in ("pre", "pre_header"):
                self.pending_pre_lines.extend(self.extract_textlines(tb))
                continue

            if label == "header":
                self.add_header(
                  self.normalize_text(tb.extract_text_from_tb())
               )
                continue

            elif label == "footer":#or self.is_pg_num(tb,page.pg_width):
                self.add_footer(
                  self.normalize_text(tb.extract_text_from_tb())
               )
                continue

            elif label == "footnote":
                continue

            elif label == "toc":
                if self.toc_html and not self.toc_rendered:
                    self.flushPrevious()
                    if self.pending_table is not None and len(self.pending_table) <= 2:
                        self.addTable(self.pending_table[0])
                        self.pending_table = None
                        self.flush_pending_header_footer()
                    self.builder += self.toc_html
                    self.toc_rendered = True
                continue

            if not ((isinstance(label, tuple) and (label[0] == "table" or\
                                                   label[0] == "borderless_table"))):
                if self.pending_table is not None and len(self.pending_table) <= 2:
                    self.addTable(self.pending_table[0])
                    self.pending_table = None
                    self.flush_pending_header_footer()

            if self.pdf_type == 'sebi' and not self.is_pre_added and label in ('title', 'level1'):
                self.check_for_pre_ended(self.normalize_text(tb.extract_text_from_tb()), label)

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
                                self.merge_tables(table_obj, table_width)#, html_builder=self)
                               
                            else:
                                self.addTable(self.pending_table[0])
                                self.pending_table = [table_obj, table_width]

                    visited_for_table.add(table_id)
            
            elif isinstance(label, tuple) and label[0] == "borderless_table":
                table_id = label[1]
                if table_id not in visited_for_table:
                    table_obj = page.borderless_tabular_datas.tables.get(table_id)
                    table_width = page.borderless_tabular_datas.get_table_width(table_id)

                    if table_obj is not None:
                        if self.pending_table is None:
                            self.pending_table = [table_obj, table_width]
                        
                        else:
                            if self.is_table_continuation(table_obj, table_width):
                                self.merge_tables(table_obj, table_width)#, html_builder=self)
                               
                            else:
                                self.addTable(self.pending_table[0])
                                self.pending_table = [table_obj, table_width]

                    visited_for_table.add(table_id)

            elif isinstance(label,list) and label[0] == "amendment":
               self.addAmendment(label,tb,page.side_notes_datas,page.pg_height)
            elif isinstance(label, tuple) and label[1] == 'blockquote':
               self.addItalicBlockQuote(self.normalize_text(tb.extract_text_from_tb()), next_text, tb, next_text_tb, page.pg_height, page.pg_width, at_page_end, tb)
            elif label == "title":
                self.addTitle(tb,page.pg_width,page.pg_height, next_text, next_text_tb,at_page_end,next_label)
            elif label == "section":
                self.addSection(tb,page.side_notes_datas,page.pg_height,self.hierarchy.index(label))
            elif label == "subsection":
                self.addSubsection(self.normalize_text(tb.extract_text_from_tb()),self.hierarchy.index(label))
            elif label == "para":
                self.addPara(self.normalize_text(tb.extract_text_from_tb()),self.hierarchy.index(label))
            elif label == "subpara":
                self.addSubpara(self.normalize_text(tb.extract_text_from_tb()),self.hierarchy.index(label))
            elif label == 'blockquote':
                self.addBlockQuote(self.normalize_text(tb.extract_text_from_tb()), next_text,tb, next_text_tb, page.pg_height, page.pg_width,  at_page_end, tb)
            elif label == 'level1' or label == 'level2' or label == 'level3' or label == 'level4':
                self.addLevel(self.normalize_text(tb.extract_text_from_tb()), self.level_hierarchy.index(label), next_text,tb, next_text_tb, page.pg_height, page.pg_width,  at_page_end)
            elif label == "figure":
               self.addFigure(tb, page)
            elif label is None:
                # if not self.is_pg_num(tb,page.pg_width):
                  self.addUnlabelled(self.normalize_text(tb.extract_text_from_tb()), next_text,tb, next_text_tb, page.pg_height, page.pg_width,  at_page_end)

        self.render_footnote_section()

    def is_pg_num(self,tb,pg_width):
        if  tb.width < 0.04 * pg_width and self.check_isDigit(tb):
            self.logger.debug("The unlabelled textbox [%s] is classified as pg_num",tb.extract_text_from_tb())
            return True
        return False
    
    def check_isDigit(self, tb):
      text = tb.extract_text_from_tb()
      if not text:
          return False

      raw = text.strip()
      cleaned = raw.lower()

      # --- Reject common bullet forms: 'i.', 'ii)', '1.' followed by text ---
      if re.match(r"^\(?[ivxlcdm0-9]+\)?[.)]\s+\w+", cleaned, re.IGNORECASE):
          return False

      # Remove enclosing brackets/parentheses/braces only if whole thing is wrapped
      stripped = re.sub(r"^[\(\[\{]\s*|\s*[\)\]\}]$", "", cleaned)

      # Case 1: Arabic numbers
      if re.fullmatch(r"\d{1,4}", stripped):
          return True

      # Case 2: Roman numerals (valid strict form, 1–3999)
      roman_pattern = r"^(m{0,3})(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$"
      if re.fullmatch(roman_pattern, stripped, flags=re.IGNORECASE):
          return True
      
      return False

    def close_html(self):
        if not self.builder:
           return None
        html = self.main_builder + self.builder + "\n</body>\n</html>"
        return html
    
    def get_html(self):
        self.flushPrevious()
        self.flush_pre_lines()
        self.close_levels()
        self.close_sections()
        self.flushTables()
        return self.close_html()

    def flushTables(self):
        """Flush pending_table into final storage."""
        if self.pending_table is not None and len(self.pending_table) <= 2:
            self.addTable(self.pending_table[0])
            self.pending_table = None

    
    def addLevel(self, text, hierarchy_index, next_text,tb, next_text_tb, pg_height,pg_width,  at_page_end):
          try:
              
              # if self.handle_pending_text_continuation(text, next_text,at_page_end, tb, next_text_tb, pg_height, pg_width):
              #       return
                   
              if not self.previous_sentence_end_status and self.handle_continuation(self.builder, text, next_text,at_page_end, tb, next_text_tb, pg_height, pg_width):
                    return
              if not self.stack_for_level:
                  self.flushPrevious()
              else:
                  self.flushPrevious()
                  if hierarchy_index == 0:
                      while self.stack_for_level:
                          tag = self.stack_for_level.pop()
                          if tag == 0:
                              self.builder += "</p>\n"
                          else:
                              self.builder += "</li>\n</ul>\n"
                  else:
                      if self.stack_for_level and self.stack_for_level[-1] == 0:
                          self.stack_for_level.pop()
                          self.builder += "</p>\n"
                      while self.stack_for_level and self.stack_for_level[-1] > hierarchy_index:
                          self.stack_for_level.pop()
                          self.builder += "</li>\n</ul>\n"
                      if self.stack_for_level and self.stack_for_level[-1] == hierarchy_index:
                          self.builder += "</li>\n"

              self.previous_sentence_end_status = self.is_real_sentence_end(text, next_text, at_page_end, tb, next_text_tb, pg_height, pg_width)
              # Open new tag depending on level
              if hierarchy_index == 0:
                  # Paragraph level always opens fresh
                  self.builder += f"<p>{text}"
                  self.stack_for_level.append(hierarchy_index)
              else:
                  # If going deeper than parent, open a new <ul>
                  if not self.stack_for_level or self.stack_for_level[-1] < hierarchy_index:
                      self.builder += "<ul>\n"
                      self.stack_for_level.append(hierarchy_index)
                  self.builder += f"<li>{text}"

              self.logger.debug("Opened section at hierarchy level: %d", hierarchy_index)

          except Exception as e:
              self.logger.exception("Error while adding section [%s]: %s", text, e)


import asyncio
import io
from html import escape

import pymupdf
from PIL import Image
from chrome_lens_py import LensAPI
from statistics import median


class HTMLBuilderChromeLens:

    def __init__(self, pdf_path):
        self.pdf_path = pdf_path
        self.lens = LensAPI()
        self.builder = ""
        self.total_pages = 0
        self.logger = logging.getLogger(__name__)

    async def process_page(self, page):

        pix = page.get_pixmap(
            dpi=300,
            alpha=False
        )

        image = Image.open(
            io.BytesIO(
                pix.tobytes("png")
            )
        )

        result = await self.lens.process_image(
            image_path=image,
            output_format="detailed"
        )

        return result.get(
            "detailed_blocks",
            []
        )

    async def _build_async(
        self,
        start_page,
        end_page
    ):

        doc = pymupdf.open(
            self.pdf_path
        )

        try:

            for page_num in range(
                start_page,
                end_page + 1
            ):

                page = doc[
                    page_num - 1
                ]

                detailed_blocks = await self.process_page(
                    page
                )

                self.builder += (
                    self.build_page_html(
                        detailed_blocks,
                        page_num
                    )
                    + "\n\n"
                )

        finally:

            doc.close()

    def build(
        self,
        start_page=None,
        end_page=None
    ):

        doc = pymupdf.open(
            self.pdf_path
        )

        self.total_pages = len(
            doc
        )

        doc.close()

        if start_page is None:
            start_page = 1

        if end_page is None:
            end_page = self.total_pages

        start_page = max(
            1,
            start_page
        )

        end_page = min(
            self.total_pages,
            end_page
        )

        i = 1

        while i <= 3:
            try:
                asyncio.run(
                    self._build_async(
                        start_page,
                        end_page
                    )
                )
            
            except Exception as e:
               self.logger.warning(f'While using chrome lens to build html: {e}')
            
            i += 1

    def _detect_word_columns(self, rows, n_bins=100, coverage_threshold=0.15,
                              min_gap_ratio=0.02, min_zone=0.15, max_zone=0.85,
                              min_words_per_column=5, min_column_height_ratio=0.25):
        words = [w for row in rows for w in row["words"]]
        if len(rows) < 4 or len(words) < 2 * min_words_per_column:
            return None

        x0 = min(w["left"] for w in words)
        x1 = max(w["right"] for w in words)
        width = x1 - x0
        if width <= 0:
            return None

        def to_bin(x):
            return max(0, min(n_bins - 1, int((x - x0) / width * n_bins)))

        n_rows = len(rows)
        bin_row_count = [0] * n_bins
        for row in rows:
            row_bins = set()
            for w in row["words"]:
                for b in range(to_bin(w["left"]), to_bin(w["right"]) + 1):
                    row_bins.add(b)
            for b in row_bins:
                bin_row_count[b] += 1
        bin_fraction = [count / n_rows for count in bin_row_count]

        lo, hi = int(min_zone * n_bins), int(max_zone * n_bins)
        best_start, best_len = None, 0
        run_start = None
        for i in range(lo, hi):
            if bin_fraction[i] <= coverage_threshold:
                if run_start is None:
                    run_start = i
            elif run_start is not None:
                if i - run_start > best_len:
                    best_start, best_len = run_start, i - run_start
                run_start = None
        if run_start is not None and hi - run_start > best_len:
            best_start, best_len = run_start, hi - run_start

        if best_start is None or (best_len / n_bins) * width < min_gap_ratio * width:
            return None

        split_x = x0 + (best_start + best_len / 2.0) / n_bins * width

        left_words = [w for w in words if w["cx"] < split_x]
        right_words = [w for w in words if w["cx"] >= split_x]
        if len(left_words) < min_words_per_column or len(right_words) < min_words_per_column:
            return None

        top = min(w["top"] for w in words)
        bottom = max(w["bottom"] for w in words)
        total_height = max(bottom - top, 1e-6)
        left_height = max(w["bottom"] for w in left_words) - min(w["top"] for w in left_words)
        right_height = max(w["bottom"] for w in right_words) - min(w["top"] for w in right_words)
        if left_height < min_column_height_ratio * total_height or \
           right_height < min_column_height_ratio * total_height:
            return None

        left_bounds = (min(w["left"] for w in left_words), max(w["right"] for w in left_words))
        right_bounds = (min(w["left"] for w in right_words), max(w["right"] for w in right_words))
        return split_x, [left_bounds, right_bounds]

    # --- func to split a row whose words actually belong to two different columns that
    # happened to land in the same row band, using the row's own word gap near split_x ---
    def _split_row_by_column(self, row, split_x, min_word_gap=0.04, gutter_tolerance=0.08):
        ws = sorted(row["words"], key=lambda w: w["left"])
        if not ws or not (ws[0]["left"] < split_x < ws[-1]["right"]):
            return [row]

        for i in range(len(ws) - 1):
            gap = ws[i + 1]["left"] - ws[i]["right"]
            mid = (ws[i]["right"] + ws[i + 1]["left"]) / 2.0
            if gap >= min_word_gap and abs(mid - split_x) <= gutter_tolerance:
                left_part, right_part = ws[:i + 1], ws[i + 1:]
                return [
                    {"cy": row["cy"], "words": left_part},
                    {"cy": row["cy"], "words": right_part},
                ]
        # No internal gutter found - it's a genuine full-width line (e.g. a heading), keep as-is
        return [row]

    # --- func to order rows left-column-then-right-column, using full-width rows (that
    # weren't split above) as band separators, mirroring Page._reorder_by_columns ---
    def _reorder_rows_by_column(self, rows, split_x, column_bounds, full_width_ratio=0.6):
        combined_left = column_bounds[0][0]
        combined_right = column_bounds[-1][1]
        combined_width = max(combined_right - combined_left, 1e-6)

        rows_sorted = sorted(rows, key=lambda r: r["cy"])

        bands = []
        current_left, current_right = [], []

        def flush():
            nonlocal current_left, current_right
            if current_left or current_right:
                current_left.sort(key=lambda r: r["cy"])
                current_right.sort(key=lambda r: r["cy"])
                bands.append(current_left + current_right)
                current_left, current_right = [], []

        for row in rows_sorted:
            row_left = min(w["left"] for w in row["words"])
            row_right = max(w["right"] for w in row["words"])
            is_full_width = (row_right - row_left) >= full_width_ratio * combined_width and \
                row_left < split_x < row_right
            if is_full_width:
                flush()
                bands.append([row])
            else:
                row_cx = (row_left + row_right) / 2.0
                if row_cx < split_x:
                    current_left.append(row)
                else:
                    current_right.append(row)
        flush()

        return [row for band in bands for row in band]

    def build_page_html(self, detailed_blocks, page_number):

        words = []

        for block in detailed_blocks:

            for line in block.get("lines", []):

                for word in line.get("words", []):

                    txt = word.get("text", "").strip()

                    if not txt:
                        continue

                    g = word["geometry"]

                    words.append({

                        "text": txt,

                        "left": g["center_x"] - g["width"] / 2,
                        "right": g["center_x"] + g["width"] / 2,

                        "top": g["center_y"] - g["height"] / 2,
                        "bottom": g["center_y"] + g["height"] / 2,

                        "cx": g["center_x"],
                        "cy": g["center_y"],

                        "width": g["width"],
                        "height": g["height"]

                    })

        if not words:
            return ""

        median_height = median(
            w["height"]
            for w in words
        )

        row_threshold = median_height * 0.60

        words.sort(
            key=lambda w: (
                w["cy"],
                w["left"]
            )
        )

        rows = []

        for word in words:

            found = False

            for row in rows:

                if abs(row["cy"] - word["cy"]) <= row_threshold:

                    row["words"].append(word)

                    n = len(row["words"])

                    row["cy"] = (
                        row["cy"] * (n - 1)
                        + word["cy"]
                    ) / n

                    found = True
                    break

            if not found:

                rows.append({

                    "cy": word["cy"],

                    "words": [word]

                })

        # Exclude genuine full-width rows (headings/tables: wide, and no internal gap
        # bigger than ordinary word-spacing) from the rows fed into gutter detection.
        # With only a handful of rows on a page, even one such row can look "rare
        # enough" under a pure row-fraction threshold to be mistaken for part of the
        # gutter - excluding it outright avoids that regardless of how many rows there
        # are. A minimum gap of several character-widths is used as an absolute
        # floor (not relative to page/row width) since ordinary word-spacing is a
        # roughly fixed number of character-widths regardless of a row's own extent.
        min_gutter_gap = 4 * 0.010
        overall_word_x0 = min(w["left"] for row in rows for w in row["words"])
        overall_word_x1 = max(w["right"] for row in rows for w in row["words"])
        overall_width_for_filter = max(overall_word_x1 - overall_word_x0, 1e-6)

        def is_full_width_no_gutter(row):
            ws = sorted(row["words"], key=lambda w: w["left"])
            row_width = ws[-1]["right"] - ws[0]["left"]
            if row_width < 0.6 * overall_width_for_filter:
                return False
            gaps = (ws[i + 1]["left"] - ws[i]["right"] for i in range(len(ws) - 1))
            return not any(gap >= min_gutter_gap for gap in gaps)

        gutter_detection_rows = [row for row in rows if not is_full_width_no_gutter(row)]

        column_info = self._detect_word_columns(gutter_detection_rows)
        if column_info is None:
            rows.sort(key=lambda r: r["cy"])
        else:
            split_x, column_bounds = column_info
            split_rows = []
            for row in rows:
                split_rows.extend(self._split_row_by_column(row, split_x))
            rows = self._reorder_rows_by_column(split_rows, split_x, column_bounds)

        html = []

        AVG_CHAR_WIDTH = 0.010

        for row in rows:

            row["words"].sort(
                key=lambda w: w["left"]
            )

            line = ""

            previous_right = None

            for word in row["words"]:

                if previous_right is None:

                    indent = max(
                        0,
                        round(word["left"] / AVG_CHAR_WIDTH)
                    )

                    line += " " * indent + word["text"]

                else:

                    gap = word["left"] - previous_right

                    spaces = max(
                        1,
                        round(gap / AVG_CHAR_WIDTH)
                    )

                    line += " " * spaces + word["text"]

                previous_right = word["right"]

            html.append(
                f'<p style="white-space: pre-wrap;">{escape(line)}</p>'
            )

        return "\n".join(html)

    def get_html(self):
        if not self.builder:
           self.logger.warning(f'OOPS! chrome lens couldn\'t generate html for pdf path:{self.pdf_path}')
           return None

        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Document</title>
</head>
<body>

{self.builder}

</body>
</html>
"""