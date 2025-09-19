import re
import math
from collections import OrderedDict
import numpy as np
import logging
import pandas as pd
from difflib import SequenceMatcher
from sklearn.cluster import DBSCAN
from SentenceEndDetector import LegalSentenceDetector
from bs4 import BeautifulSoup
from NormalizeText import NormalizeText

class HTMLBuilder:
    
    def __init__(self, sentence_completion_punctuation = tuple(), pdf_type = None):
        self.logger = logging.getLogger(__name__)
        self.pdf_type = pdf_type
        self.pending_text = ""
        self.pending_tag = None
        self.sentence_completion_punctuation = sentence_completion_punctuation
        self.stack_for_section = []
        self.stack_for_level = []
        self.hierarchy = ("section","subsection","para","subpara","subsubpara")
        self.level_hierarchy = ('level1', 'level2', 'level3', 'level4','level5')
        self.pending_table = None
        self.is_real_sentence_end =LegalSentenceDetector().is_real_sentence_end
        self.normalize_text = NormalizeText().normalize_text
        self.min_word_threshold_tableRows = 2
        self.table_terminators = {".", "?", "!"} #";", ":",
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

  td {
    white-space: pre-wrap;
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

    
    def close_levels(self):
        try:
            while self.stack_for_level:
                  if len(self.stack_for_level) >= 2:
                    if self.stack_for_level[-1] == self.stack_for_level[-2]:
                        tag = self.stack_for_level.pop()
                        if tag == 0:
                            self.builder += "</section>\n"
                        else:
                            self.builder += "</li>\n"
                    else:
                        tag = self.stack_for_level.pop()
                        if tag == 0:
                            self.builder += "</section>\n"
                        else:
                            self.builder += "</li>\n</ul>\n"
                  else:
                      tag = self.stack_for_level.pop()
                      if tag == 0:
                            self.builder += "</section>\n"
                      else:
                            self.builder += "</li>\n</ul>\n"
        except Exception as e:
           self.logger.warning(f'when closing levels: {e}')
          
    def check_for_last_token(self, html):
      last_token, last_tag = self.get_last_token(html)
      if last_tag and last_token and (last_tag!='h4'):
          if not last_token.endswith(('.','?','!',":-", "---", "...", '—',':','."', ".'",';"',";'")):
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
                        return True
                    else:
                        self.pending_text += (' '+text.strip())
                        return True
        return False
    
    def handle_continuation(self, html, text, next_text, at_page_end, tb, next_text_tb, pg_height, pg_width):
        status, last_tag = self.check_for_last_token(html)
        if status and last_tag:
            is_sentence_completed = self.is_real_sentence_end(text, next_text, at_page_end, tb, next_text_tb, pg_height, pg_width)
            if is_sentence_completed:
                self.pending_text += (" "+text.strip())
                self.pending_text += f"</{last_tag}>\n"
                self.builder += self.pending_text
                self.pending_tag = None
                self.pending_text = ""
                return True
            else:
                self.pending_text += (' '+text.strip())
                return True
        return False
    # --- func to add Title in the html ---
    def addTitle(self, tb,pg_width,pg_height, next_text, next_text_tb,  at_page_end,next_label = None):
        try:
          text = tb.extract_text_from_tb().strip()
          sebi_level_close_re = re.compile(r'^(?:(?:Date|Dated)\s*[:\-]?\s*(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|[A-Za-z]+\s+\d{1,2},\s*\d{4})|(?:Place|At)\s*[:\-]?\s*[A-Z][A-Za-z .,&-]*|\(.*?(?:Judgment\s+pronounced|Order\s+pronounced|Decision\s+pronounced).*?\))$', re.IGNORECASE)
          
          if self.handle_pending_text_continuation(text, next_text,at_page_end, tb, next_text_tb, pg_height, pg_width):
             return
                   
          if self.handle_continuation(self.builder, text, next_text,at_page_end, tb, next_text_tb, pg_height, pg_width):
                 return
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
              elif self.stack_for_level and next_label == 'level1':
                  # while len(self.stack_for_level) >= 2:
                  #       if self.stack_for_level[-1] == self.stack_for_level[-2]:
                  #           self.stack_for_level.pop()
                  #           self.builder += "</li>\n"
                  #       else:
                  #           self.stack_for_level.pop()
                  #           self.builder += "</li>\n</ul>\n"
                  #       tag = self.stack_for_level.pop()
                  #       if tag == 0:
                  #             self.builder += "</section>\n"
                  #       else:
                  #             self.builder += "</li>\n</ul>\n"
                  while self.stack_for_level:
                          if len(self.stack_for_level) >= 2:
                            if self.stack_for_level[-1] == self.stack_for_level[-2]:
                                tag = self.stack_for_level.pop()
                                if tag == 0:
                                    self.builder += "</section>\n"
                                else:
                                    self.builder += "</li>\n"
                            else:
                                tag = self.stack_for_level.pop()
                                if tag == 0:
                                    self.builder += "</section>\n"
                                else:
                                    self.builder += "</li>\n</ul>\n"
                          else:
                              tag = self.stack_for_level.pop()
                              if tag == 0:
                                    self.builder += "</section>\n"
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
                      doc += f"<center><h4>{self.normalize_text(line)}</h4></center>\n"
              self.builder += doc
        except Exception as e:
          self.logger.exception("Error while adding title - [%s] in html: %s",tb.extract_text_from_tb(),e)
    
    # --- func to add the table in the html ---
    def addTable(self, table):
        try:
          if (not self.stack_for_section) or (not self.stack_for_level):
              self.flushPrevious()
          # else:
          #     # Close everything up to and including the last "section"
          #     while self.stack_for_section:
          #         tag = self.stack_for_section.pop()
          #         self.builder += "</section>\n"
          #         if tag == 0:
          #             break

          self.builder += self.normalize_text(table.to_html(index=False, header = False, border=1).replace("\\n"," "))
          self.builder += "\n" 
        except Exception as e:
            self.logger.exception("Error while adding table in html - %s .\nTable preview\n",e, table.head().to_string(index=False))
  
    def close_sections(self):
       while self.stack_for_section:
          self.stack_for_section.pop()
          self.builder += "</section>\n"
       
    # --- func to add the unknown label of textbox in the html - classified as <p> tag ---
    def addItalicBlockQuote(self, text, next_text, text_tb, next_text_tb, pg_height, pg_width, at_page_end):
        try:
            is_sentence_completed = self.is_real_sentence_end(text, next_text, at_page_end, text_tb, next_text_tb, pg_height, pg_width)
            if self.pending_tag and self.pending_tag != 'blockquote':
                self.pending_text += f"</{self.pending_tag}>\n"
                self.builder += self.pending_text
                self.pending_tag = None
                self.pending_text = ""
            if not self.pending_tag and not self.pending_text and is_sentence_completed:
              self.builder += f"<blockquote>{text}</blockquote>\n"
              self.pending_tag = None
              self.pending_text = ""

            elif not self.pending_tag and not self.pending_text and not is_sentence_completed:
              self.pending_tag = "blockquote"
              self.pending_text = f"<blockquote>{text}"
            
            elif self.pending_text and self.pending_tag and is_sentence_completed:
                self.pending_text += (" "+text.strip())
                self.pending_text += f"</{self.pending_tag}>\n"
                self.builder += self.pending_text
                self.pending_tag = None
                self.pending_text = ""

            else:
                self.pending_text += (' '+text.strip())
                
        except Exception as e:
            self.logger.exception("Error while adding italic blockquote text [%s] : %s",text, e)

    def addUnlabelled(self,text, next_text, text_tb, next_text_tb, pg_height, pg_width, at_page_end):
      sebi_level_close_re = re.compile(r'^(?:(?:Date|Dated)\s*[:\-]?\s*(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|[A-Za-z]+\s+\d{1,2},\s*\d{4})|(?:Place|At)\s*[:\-]?\s*[A-Z][A-Za-z .,&-]*|\(.*?(?:Judgment\s+pronounced|Order\s+pronounced|Decision\s+pronounced).*?\))$', re.IGNORECASE)
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
                  return
          if self.pdf_type != 'acts':
            is_sentence_completed = self.is_real_sentence_end(text, next_text, at_page_end, text_tb, next_text_tb, pg_height, pg_width)
          else:
            is_sentence_completed = text.strip().endswith(self.sentence_completion_punctuation)
          if is_sentence_completed:
            self.builder += (' ' +text )#+"<br>")
          else:
            if text in set(["•","▪","▫","✓","✕","o"]) and self.pending_text == "":
               self.pending_tag = 'blockquote'
               self.pending_text = f'<blockquote>{text}'   
            else:   
                self.builder += (' ' + text)
        else:
            if self.pdf_type != 'acts':
              is_sentence_completed = self.is_real_sentence_end(text, next_text, at_page_end, text_tb, next_text_tb, pg_height, pg_width)
            else:
              is_sentence_completed = text.strip().endswith(self.sentence_completion_punctuation)
            if not self.pending_tag and not self.pending_text and is_sentence_completed:
              self.builder += f"<p>{text}</p>\n"
              self.pending_tag = None
              self.pending_text = ""
            elif not self.pending_tag and not self.pending_text and not is_sentence_completed:
              self.pending_tag = "p"
              self.pending_text = f"<p>{text}"
            elif self.pending_text and self.pending_tag and is_sentence_completed:
                self.pending_text += (" "+text.strip())
                self.pending_text += f"</{self.pending_tag}>\n"
                self.builder += self.pending_text
                self.pending_tag = None
                self.pending_text = ""
            else:
                self.pending_text += (' '+text.strip())

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
      
    # def get_last_token(self, html):
    #     # Try lxml first, fallback to built-in parser
    #     try:
    #         soup = BeautifulSoup(html, "lxml")
    #     except Exception:
    #         soup = BeautifulSoup(html, "html.parser")

    #     # Find the last tag in the parsed tree
    #     last_tag = None
    #     for tag in soup.find_all(True):
    #         last_tag = tag

    #     # If last tag is still "open" in the input HTML, keep it
    #     last_open_tag = last_tag.name if last_tag and not str(html).strip().endswith(f"</{last_tag.name}>") else None

    #     # Extract last text token
    #     text_only = soup.get_text().strip()
    #     tokens = text_only.split()
    #     last_token = tokens[-1] if tokens else ""

    #     return  last_token, last_open_tag

    def get_last_token(self, html):
        """
        Extract the last token and the last unclosed tag from an HTML string.

        Returns:
            (last_token, last_open_tag)
            - If the last tag is unclosed → (token, tagname)
            - Otherwise → (None, None)
        """
        # Try lxml first, fallback to built-in parser
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")

        # Find the last tag
        last_tag = None
        for tag in soup.find_all(True):
            last_tag = tag

        if not last_tag:
            return None, None

        # Check if input HTML does NOT close this tag
        if not str(html).strip().endswith(f"</{last_tag.name}>"):
            # Extract last text token
            text_only = soup.get_text().strip()
            tokens = text_only.split()
            last_token = tokens[-1] if tokens else ""
            return last_token, last_tag.name

        # If properly closed, return None
        return None, None

      
    def addLevel(self, text, hierarchy_index):
          try:
              
              # last_token = self.get_last_token(self.builder)
              # if last_token and (not last_token.endswith(('.','?'))):
              #     self.builder += text
              #     return
              if not self.stack_for_level:
                  self.flushPrevious()
              else:
                  self.flushPrevious()
                  if hierarchy_index == 0:
                      while self.stack_for_level:
                          if len(self.stack_for_level) >= 2:
                            if self.stack_for_level[-1] == self.stack_for_level[-2]:
                                tag = self.stack_for_level.pop()
                                if tag == 0:
                                    self.builder += "</section>\n"
                                else:
                                    self.builder += "</li>\n"
                            else:
                                tag = self.stack_for_level.pop()
                                if tag == 0:
                                    self.builder += "</section>\n"
                                else:
                                    self.builder += "</li>\n</ul>\n"
                          else:
                              tag = self.stack_for_level.pop()
                              if tag == 0:
                                    self.builder += "</section>\n"
                              else:
                                    self.builder += "</li>\n</ul>\n"
                  
                  else:
                      while self.stack_for_level and self.stack_for_level[-1] > hierarchy_index:
                          if len(self.stack_for_level) >= 2:
                              if self.stack_for_level[-1] == self.stack_for_level[-2]:
                                  self.stack_for_level.pop()
                                  self.builder += "</li>\n"
                              else:
                                  self.stack_for_level.pop()
                                  self.builder += "</li>\n</ul>\n"
              # Open new tag depending on level
              if hierarchy_index == 0:
                  # Paragraph level always opens fresh
                  self.builder += f"<section>{text}"
              else:
                  # If going deeper than parent, open a new <ul>
                  if not self.stack_for_level or self.stack_for_level[-1] < hierarchy_index:
                      self.builder += "<ul>\n"
                  self.builder += f"<li>{text}"

              # Push this level onto stack
              self.stack_for_level.append(hierarchy_index)

              self.logger.debug("Opened section at hierarchy level: %d", hierarchy_index)

          except Exception as e:
              self.logger.exception("Error while adding section [%s]: %s", text, e)
              
    # def addLevel(self, text, hierarchy_index):
    #     try:
    #         # If stack empty, start fresh
    #         if not self.stack_for_level:
    #             self.flushPrevious()
    #         # elif self.stack_for_level:
    #         #     last_token = self.get_last_token(self.builder)
    #         #     is_sentence_completed = last_token.strip().endswith(self.sentence_completion_punctuation)
    #         #     if last_token and (not is_sentence_completed):
    #         #         print(last_token, text)
    #         #         self.builder += text
    #         #         return
    #         else:
    #             # If a new paragraph starts, close everything before it
    #             if hierarchy_index == 0:
    #                 while self.stack_for_level:
    #                     tag = self.stack_for_level.pop()
    #                     if tag == 0:
    #                         self.builder += "</section>\n"
    #                     else:
    #                         self.builder += "</li>\n</ul>\n"

    #             else:
    #                 # Pop until we are at the right depth
    #                 while self.stack_for_level and self.stack_for_level[-1] > hierarchy_index:
    #                     tag = self.stack_for_level.pop()
    #                     if tag == 0:
    #                         self.builder += "</section>\n"
    #                     else:
    #                         self.builder += "</li>\n</ul>\n"

    #                 # If same level, close previous <li>
    #                 if self.stack_for_level and self.stack_for_level[-1] == hierarchy_index and hierarchy_index != 0:
    #                     self.builder += "</li>\n"

    #         # Open new tag depending on level
    #         if hierarchy_index == 0:
    #             # Paragraph level always opens fresh
    #             self.builder += f"<section>{text}"
    #         else:
    #             # If going deeper than parent, open a new <ul>
    #             if not self.stack_for_level or self.stack_for_level[-1] < hierarchy_index:
    #                 self.builder += "<ul>\n"
    #             self.builder += f"<li>{text}"

    #         # Push this level onto stack
    #         self.stack_for_level.append(hierarchy_index)

    #         self.logger.debug("Opened section at hierarchy level: %d", hierarchy_index)

    #     except Exception as e:
    #         self.logger.exception("Error while adding section [%s]: %s", text, e)
    


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
                    self.builder += f"<section class=\"section\">{prefix}<br>{rest_text}\n" #<br>
                    self.stack_for_section.append(hierarchy_index)
                  else:
                      self.builder +=f"<section class=\"section\">{prefix}<br>{rest_text}\n"
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
      # group_re = re.compile(r'^\(([^\s\)]+)\)\s*\S*',re.IGNORECASE)
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
        
    def addBlockQuote(self, text, next_text, at_page_end, text_tb, next_text_tb, pg_height, pg_width):
        text = text.replace('“', '"').replace('”', '"').replace('‘‘','"').replace('’’','"').replace('‘', "'").replace('’', "'")
        is_sentence_completed = self.is_real_sentence_end(text, next_text, at_page_end, text_tb, next_text_tb, pg_height, pg_width) #text.strip().endswith(self.sentence_completion_punctuation)
        
        if self.pending_tag and self.pending_tag != "blockquote":
            self.pending_text += f"</{self.pending_tag}>\n"
            self.builder += self.pending_text
            self.pending_tag = None
            self.pending_text = ""
        if not self.pending_tag and not self.pending_text and is_sentence_completed:
          self.builder += f"<blockquote>{text}</blockquote>\n"
          self.pending_tag = None
          self.pending_text = ""
        elif not self.pending_tag and not self.pending_text and not is_sentence_completed:
          self.pending_tag = "blockquote"
          self.pending_text = f"<blockquote>{text}"
        elif self.pending_text and self.pending_tag and is_sentence_completed:
            self.pending_text += (" "+text.strip())
            self.pending_text += f"</{self.pending_tag}>\n"
            self.builder += self.pending_text
            self.pending_tag = None
            self.pending_text = ""
        else:
           
            self.pending_text += (' '+text.strip())

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
      section_re = re.compile(r'^\s*[\' | \"]?\d+[A-Z]*(?:-[A-Z]+)?\s*\.\s*\S*', re.IGNORECASE)
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
    
    # --- func to build the textbox  as html ---
    # def build(self, page, section_end_page):
    #     visited_for_table = set()
    #     # if not page.is_single_column_page:
    #     #    page.all_tbs = self.get_orderBy_textboxes(page)
    #     try:
    #       if section_end_page and int(section_end_page)+1 == int(page.pg_num):
    #           while self.stack_for_section:
    #               popped_index = self.stack_for_section.pop()
    #               self.builder += "</section>"
    #               self.logger.debug("Closed section at hierarchy level: %d", popped_index)
    #     except Exception as e:
    #         self.logger.warning(f'when closing sections tag after section end page - {e}')
              
    #     all_items = list(page.all_tbs.items())
    #     for idx, (tb, label) in enumerate(all_items):
    #         next_text = None
    #         next_text_coords = None
    #         if idx + 1 < len(all_items):
    #             next_tb, next_label = all_items[idx + 1]
                
    #             if next_label is None:  # only consider unlabelled continuation
    #                 next_text = self.normalize_text(next_tb.extract_text_from_tb())
    #                 next_text_coords = next_tb.coords
    #             elif self.is_nextlabel_blockquote(label, next_label):
    #                 next_text = self.normalize_text(next_tb.extract_text_from_tb())
    #                 next_text_coords = next_tb.coords
    #             elif next_label[:-1] == 'level':
    #                 next_text = self.normalize_text(next_tb.extract_text_from_tb())
    #                 next_text_coords = next_tb.coords
    #         at_page_end = (idx == len(all_items) - 1)

    #         if label == "header" or label == "footer" or self.is_pg_num(tb,page.pg_width):
    #            continue
    #         if not ((isinstance(label, tuple) and label[0] == "table")):
    #             if self.pending_table is not None and len(self.pending_table) <= 2:
    #                 self.addTable(self.pending_table[0])
    #                 self.pending_table = None
    #         if isinstance(label, tuple) and label[0] == "table":
    #             table_id = label[1]
    #             if table_id not in visited_for_table:
    #                 table_obj = page.tabular_datas.tables.get(table_id)
    #                 table_width = page.tabular_datas.get_table_width(table_id)

    #                 if table_obj is not None:
    #                     if self.pending_table is None:
    #                         self.pending_table = [table_obj, table_width]
                        
    #                     else:
    #                         if self.is_table_continuation(table_obj, table_width):
    #                             self.merge_tables(table_obj, table_width)
                               
    #                         else:
    #                             self.addTable(self.pending_table[0])
    #                             self.pending_table = [table_obj, table_width]

    #                 visited_for_table.add(table_id)

    #         elif isinstance(label,list) and label[0] == "amendment":
    #            self.addAmendment(label,tb,page.side_notes_datas,page.pg_height)
    #         elif isinstance(label, tuple) and label[1] == 'blockquote':
    #            self.addItalicBlockQuote(tb.extract_text_from_tb(), next_text, tb.coords, next_text_coords, page.pg_height, page.pg_width, at_page_end)
    #         elif label == "title":
    #             self.addTitle(tb,page.pg_width,page.pg_height, next_label)
    #         elif label == "section":
    #             self.addSection(tb,page.side_notes_datas,page.pg_height,self.hierarchy.index(label))
    #         elif label == "subsection":
    #             self.addSubsection(tb.extract_text_from_tb(),self.hierarchy.index(label))
    #         elif label == "para":
    #             self.addPara(tb.extract_text_from_tb(),self.hierarchy.index(label))
    #         elif label == "subpara":
    #             self.addSubpara(tb.extract_text_from_tb(),self.hierarchy.index(label))
    #         elif label == 'blockquote':
    #             self.addBlockQuote(tb.extract_text_from_tb(), next_text,tb.coords, next_text_coords, page.pg_height, page.pg_width,  at_page_end)
    #         elif label == 'level1' or label == 'level2' or label == 'level3' or label == 'level4':
    #             self.addLevel(tb.extract_text_from_tb(), self.level_hierarchy.index(label))
    #         # elif label == 'level2':
    #         #     self.addLevel2(tb.extract_text_from_tb(), self.level_hierarchy.index(label))
    #         # elif label == 'level3':
    #         #     self.addLevel3(tb.extract_text_from_tb(), self.level_hierarchy.index(label))
    #         # elif label == 'level4':
    #         #     self.addLevel4(tb.extract_text_from_tb(), self.level_hierarchy.index(label))
    #         elif label is None:
    #             if not self.is_pg_num(tb,page.pg_width):
    #               self.addUnlabelled(tb.extract_text_from_tb(), next_text,tb.coords, next_text_coords, page.pg_height, page.pg_width,  at_page_end)
    def build(self, page, section_end_page):
        visited_for_table = set()
        # if not page.is_single_column_page:
        #    page.all_tbs = self.get_orderBy_textboxes(page)
        try:
          if section_end_page and int(section_end_page)+1 == int(page.pg_num):
              while self.stack_for_section:
                  popped_index = self.stack_for_section.pop()
                  self.builder += "</section>"
                  self.logger.debug("Closed section at hierarchy level: %d", popped_index)
        except Exception as e:
            self.logger.warning(f'when closing sections tag after section end page - {e}')
              
        all_items = list(page.all_tbs.items())
        for idx, (tb, label) in enumerate(all_items):
            next_text = None
            next_text_tb = None
            if idx + 1 < len(all_items):
                next_tb, next_label = all_items[idx + 1]
                
                if next_label is None:  # only consider unlabelled continuation
                    next_text = self.normalize_text(next_tb.extract_text_from_tb())
                    next_text_tb = next_tb
                elif self.is_nextlabel_blockquote(label, next_label):
                    next_text = self.normalize_text(next_tb.extract_text_from_tb())
                    next_text_tb = next_tb
                elif next_label[:-1] == 'level' or next_label == 'title' or (isinstance(next_label, tuple) and next_label[1] == 'blockquote'):
                    next_text = self.normalize_text(next_tb.extract_text_from_tb())
                    next_text_tb = next_tb
            at_page_end = (idx == len(all_items) - 1)
            if label == "header" or label == "footer" or self.is_pg_num(tb,page.pg_width):
               continue
            if not ((isinstance(label, tuple) and label[0] == "table")):
                if self.pending_table is not None and len(self.pending_table) <= 2:
                    self.addTable(self.pending_table[0])
                    self.pending_table = None
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
                                self.merge_tables(table_obj, table_width)
                               
                            else:
                                self.addTable(self.pending_table[0])
                                self.pending_table = [table_obj, table_width]

                    visited_for_table.add(table_id)

            elif isinstance(label,list) and label[0] == "amendment":
               self.addAmendment(label,tb,page.side_notes_datas,page.pg_height)
            elif isinstance(label, tuple) and label[1] == 'blockquote':
               self.addItalicBlockQuote(self.normalize_text(tb.extract_text_from_tb()), next_text, tb, next_text_tb, page.pg_height, page.pg_width, at_page_end)
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
                self.addBlockQuote(self.normalize_text(tb.extract_text_from_tb()), next_text,tb, next_text_tb, page.pg_height, page.pg_width,  at_page_end)
            elif label == 'level1' or label == 'level2' or label == 'level3' or label == 'level4':
                self.addLevel(self.normalize_text(tb.extract_text_from_tb()), self.level_hierarchy.index(label))
            # elif label == 'level2':
            #     self.addLevel2(tb.extract_text_from_tb(), self.level_hierarchy.index(label))
            # elif label == 'level3':
            #     self.addLevel3(tb.extract_text_from_tb(), self.level_hierarchy.index(label))
            # elif label == 'level4':
            #     self.addLevel4(tb.extract_text_from_tb(), self.level_hierarchy.index(label))
            elif label is None:
                if not self.is_pg_num(tb,page.pg_width):
                  self.addUnlabelled(self.normalize_text(tb.extract_text_from_tb()), next_text,tb, next_text_tb, page.pg_height, page.pg_width,  at_page_end)

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
        self.flushTables()
        return self.builder + "\n</body>\n</html>"
    
    def is_sequential(self, text1, text2):
        """Check if two strings represent sequential numbers."""
        try:
            s1, s2 = str(text1).strip(), str(text2).strip()
            if s1.isdigit() and s2.isdigit():
                return int(s2) == int(s1) + 1
            n1, n2 = re.findall(r"\d+", s1), re.findall(r"\d+", s2)
            if n1 and n2:
                return int(n2[0]) == int(n1[0]) + 1
            return False
        except:
            return False

    def row_similarity(self, row1, row2):
        """Compute similarity ratio between two rows."""
        s1, s2 = " ".join(str(x) for x in row1), " ".join(str(x) for x in row2)
        return SequenceMatcher(None, s1, s2).ratio()

    def _has_serial_number(self, cell):
        """
        Check if a cell looks like a serial number:
        - Arabic numerals
        - Roman numerals
        - Alphanumeric IDs (A1, B.2, Sec-3)
        """
        text = str(cell).strip().lower()
        if not text or text in ["nan", ""]:
            return False

        # Digits
        if text.isdigit():
            return True

        # Roman numerals
        if re.fullmatch(r"(m{0,3})(cm|cd|d?c{0,3})"
                        r"(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})", text):
            return True

        # Alphanumeric IDs
        if re.fullmatch(r"[a-z]\d+(\.\d+)?", text):
            return True
        if re.fullmatch(r"\d+(\.\d+)?[a-z]", text):
            return True
        if re.fullmatch(r"(sec|art|clause)[\-\s]?\d+", text):
            return True

        # Ends with a dot, like "1." or "ii."
        if re.fullmatch(r"\d+\.", text) or re.fullmatch(r"[ivxlcdm]+\.", text):
            return True

        return False

    def _is_numeric_or_symbolic(self, cell):
        """Return True if cell is purely numeric/measurement/symbolic."""
        text = str(cell).strip().lower()
        if not text or text in ["-", "—", "–", "na", "n/a", "nil", "none", "✓", "x"]:
            return True
        if re.fullmatch(r"\d+(\.\d+)?(\s?(kg|g|mg|cm|mm|m|km|%|hrs?|days?|years?))?", text):
            return True
        return False

    def _looks_like_continuation(self, prev_text, curr_text, curr_row):
        """Heuristics when serial numbers are absent."""
        prev_text, curr_text = str(prev_text).strip(), str(curr_text).strip()

        # Guard: numeric/measurement-only rows should not merge
        numeric_like = sum(self._is_numeric_or_symbolic(c) for c in curr_row[1:])
        if numeric_like >= len(curr_row) - 2:  # all except maybe one col
            return False

        # Rule 1: prev doesn’t end with punctuation + curr starts lowercase
        if prev_text and prev_text[-1] not in self.table_terminators and curr_text and curr_text[0].islower():
            return True

        # Rule 2: curr row is sparse (only 1 column filled beyond first col)
        non_empty_cols = sum(bool(str(c).strip()) for c in curr_row[1:])
        if non_empty_cols == 1:
            return True

        # Rule 3: curr row very short (few words)
        if len(curr_text.split()) < self.min_word_threshold_tableRows:
            return True

        return False

    def merge_broken_rows(self, table):
        """
        Merge rows where first column is missing OR looks like a continuation.
        Handles Camelot-style DataFrames like the one you shared.
        """
        merged = []
        for idx, row in table.iterrows():
            row_list = list(row)

            if not merged:
                merged.append(row_list)
                continue

            prev = merged[-1]
            first_cell = str(row_list[0]).strip()
            # Case 1: Explicit serial number → new row
            if self._has_serial_number(first_cell):
                merged.append(row_list)
                continue

            # Case 2: Camelot-style continuation → col1 text, col2 empty
            prev_text = str(prev[1]) if len(prev) > 1 else ""
            curr_text = str(row_list[1]) if len(row_list) > 1 else ""
            col2_text = str(row_list[2]) if len(row_list) > 2 else ""

            if curr_text and not col2_text.strip():
                prev[1] = (str(prev[1]).rstrip() + " " + curr_text.lstrip()).strip()
                continue

            # Case 3: Heuristic continuation
            if self._looks_like_continuation(prev_text, curr_text, row_list):
                for c in range(1, len(row_list)):
                    if str(row_list[c]).strip() and str(row_list[c]).lower() not in ["nan", ""]:
                        prev[c] = (str(prev[c]).rstrip() + " " +
                                   str(row_list[c]).lstrip()).strip()
            else:
                merged.append(row_list)

        return pd.DataFrame(merged, columns=table.columns)

    def is_table_continuation(self, table2, table2_width):
      """Check if table2 is a continuation of pending_table."""
      table1, table1_width = self.pending_table

      # 1. Width similarity check
      width_ratio = min(table1_width, table2_width) / max(table1_width, table2_width)
      if width_ratio < 0.95:
          return False

      # 2. Column count check
      if table1.shape[1] != table2.shape[1]:
          return False
      if table2.shape[1] == table1.shape[1] and table2.iloc[0].isnull().sum() >= table2.shape[1] - 1:
          return True
      # 3. Check for serial restart (likely new table)
      first_col_prev_last = str(table1.iloc[-1, 0]).strip()
      first_col_curr_first = str(table2.iloc[0, 0]).strip()
      
      # If first column of next table is 1, A, or (a) → new table
      first_col_pattern = re.compile(
          r"^(\(?[1aAiI]\)?[\.\)]?|[\[\(]?[1aAiI][\]\)]?)$",
          re.IGNORECASE
      )

      # Example usage
      first_col_curr_first = table2.iloc[0, 0]  # first cell of next table
      if first_col_pattern.match(str(first_col_curr_first).strip()):
          return False

      # 4. Sequential numbering check (only if not restarting)
      if self.is_sequential(first_col_prev_last, first_col_curr_first):
          return True

      # 5. Header similarity check
      header_sim = self.row_similarity(table1.iloc[0], table2.iloc[0])
      if header_sim > 0.9:
          return True

      # 6. Fallback: treat as new table
      return False


    def merge_tables(self, table2, table2_width):
        """Merge table2 into pending_table with broken-row handling."""
        table1, table1_width = self.pending_table

        # Step 1: Header similarity → skip duplicate header
        header_sim = self.row_similarity(table1.iloc[0], table2.iloc[0])
        if header_sim > 0.9:
            table2 = table2.iloc[1:].reset_index(drop=True)

        # Step 2: Align columns if mismatch
        if table2.shape[1] != table1.shape[1]:
            if table2.shape[1] < table1.shape[1]:
                for i in range(table1.shape[1] - table2.shape[1]):
                    table2[f"_pad{i}"] = ""
            else:
                table2 = table2.iloc[:, :table1.shape[1]]

        table2.columns = table1.columns

        # Step 3: Merge and handle broken rows
        merged_table = pd.concat([table1, table2], ignore_index=True)
        merged_table = self.merge_broken_rows(merged_table)

        # Step 4: Update average width
        avg_width = (table1_width + table2_width) / 2.0
        self.pending_table = [merged_table, avg_width]


    def flushTables(self):
        """Flush pending_table into final storage."""
        if self.pending_table is not None and len(self.pending_table) <= 2:
            self.addTable(self.pending_table[0])
            self.pending_table = None
    
    # def is_real_sentence_end(self, text, next_text, at_page_end, text_coords, next_text_coords, pg_height, pg_width):
    #   """
    #   Detects the end of sentences in legal documents, accounting for legal text complexities.
    #   Handles:
    #   - Legal abbreviations (Dr., Ld. Adv., Sec., Co., Ltd., SCC, Exh.)
    #   - Citations and references (ILR 1951 480, (2004) 4 SCC 2036, 2012 SCC online Del 4864)
    #   - Numbered/bulleted lists ((1), 1., (a), i., 10.)
    #   - Section/subsection references (2., 2.1., Sec. 33, Exh. 101.)
    #   - Internal tokens and acronyms (SEBI., RBI., 1.23)
    #   - Lookahead context checks
    #   - Page boundary handling (don’t force close paragraph at page end)
    #   """
    #   # if self.check_line_preserve(text, next_text, at_page_end, text_coords, next_text_coords, pg_height, pg_width):
    #   #    return True
      
    #   if not text:
    #       return False

    #   s = text.strip()
    #   if not s:
    #       return False

    #   # 0. PURE BULLET OR LIST MARKER (standalone line like "10.", "(a)", "(i)")
    #   # pure_bullet_patterns = [
    #   #     re.compile(r'^\d+\.$'),              # "10."
    #   #     re.compile(r'^\(\d+\)$'),            # "(10)"
    #   #     re.compile(r'^\d+\)$'),              # "10)"
    #   #     re.compile(r'^[a-z]\.$', re.I),      # "a."
    #   #     re.compile(r'^\([a-z]\)$', re.I),    # "(a)"
    #   #     re.compile(r'^[ivxlcdm]+\.$', re.I), # "ii."
    #   #     re.compile(r'^\([ivxlcdm]+\)$', re.I),
    #   # ]

    #   pure_bullet_patterns = [
    #       re.compile(r'^\(?\d+[A-Z]?\)?[.)]?$'),   # (1), 1A., 2)
    #       re.compile(r'^\(?[ivxlcdm]+\)?[.)]?$' , re.I), # (iv), ii.
    #       re.compile(r'^\(?[a-z]\)?[.)]?$' , re.I),      # (a), b.
    #   ]
    #   for pattern in pure_bullet_patterns:
    #       if pattern.fullmatch(s):
    #           return False
    #   if re.match(r'^\d+(\.\d+)*\.$', s):
    #       return False
    #   # Continuation punctuation (:-, ---, ...)
    #   continuation_punct = [":-", "---", "...", '—']
    #   for cp in continuation_punct:
    #       if s.endswith(cp):
    #           return False if at_page_end else True

    #   # Special colon handling
    #   if s.endswith(':'):
    #       if next_text:
    #           nxt_clean = next_text.strip()
    #           if re.match(r'^\(?[a-z0-9ivxlcdm]+\)', nxt_clean, re.I):  # bullet-like
    #               return True
    #       return False if at_page_end else True

    #   # 1. SENTENCE-ENDING PUNCTUATION CHECK
    #   sentence_end_pattern = re.compile(r'.*[.?!:;]\s*$')
    #   if not sentence_end_pattern.match(s):
    #       return False

    #   last_token_match = re.search(r'(\S+?)([.?!:;]+)\s*$', s)
    #   if not last_token_match:
    #       return False if at_page_end else True

    #   last_token = last_token_match.group(1)
    #   trailing_punct = last_token_match.group(2)

    #   # 2. LIST-LIKE END TOKENS
    #   list_boundary_patterns = [
    #       re.compile(r'^\(\s*\d+\s*\)$'),
    #       re.compile(r'^\(\s*[a-z]\s*\)$', re.I),
    #       re.compile(r'^\(\s*[ivxlcdm]+\s*\)$', re.I),
    #       re.compile(r'^\d+\.$'),
    #       re.compile(r'^[a-z]\.$', re.I),
    #       re.compile(r'^[ivxlcdm]+\.$', re.I),
    #   ]
    #   clean_token = re.sub(r'[^\w\(\)]', '', last_token).lower()
    #   for pattern in list_boundary_patterns:
    #       if pattern.match(last_token.strip()) or pattern.match(clean_token):
    #           return False

    #   # 3. LEGAL CITATIONS
    #   citation_patterns = [
    #       re.compile(r'\b(ILR|SCC|SCR|AIR|CrLJ|DLT|Mad|Cal|Bom|All|Ker|Guj|MP|Raj)\s+\d{4}\s+\d+\b'),
    #       re.compile(r'\(\d{4}\)\s+\d+\s+(SCC|SCR|AIR|CrLJ|DLT|Mad|Cal|Bom|All|Ker|Guj|MP|Raj)\s+\d+'),
    #       re.compile(r'\d{4}\s+(SCC|SCR|AIR|CrLJ|DLT|Mad|Cal|Bom|All|Ker|Guj|MP|Raj)\s+online\s+\w+\s+\d+'),
    #       re.compile(r'\[\d{4}\]\s+\d+\s+\w+\s+\d+'),
    #       re.compile(r'\d{4}\s+\(\d+\)\s+\w+\s+\d+'),
    #       re.compile(r'Vol\.\s*\d+.*p\.\s*\d+', re.I),
    #       re.compile(r'pp\.\s*\d+[-–]\d+', re.I),
    #   ]
    #   for pattern in citation_patterns:
    #       if pattern.search(s):
    #           if next_text and next_text.strip():
    #               nxt_clean = next_text.strip()
    #               if nxt_clean[0].islower():
    #                   return False
    #               else:
    #                   return True
    #           return False   

    #   # 4. DECIMALS & ACRONYMS
    #   if re.match(r'^\d+\.\d+$', last_token):
    #       return False
    #   if re.match(r'^[A-Z]+(\.[A-Z]+)+\.?$', last_token, re.I):
    #       return False
    #   if re.match(r'^(SEBI|RBI|CBDT|ITAT|NCLT|NCLAT|CBI|ED|FIU|MCA|ROC|DIN|PAN|TAN|GST|CGST|SGST|IGST|UTI|LIC|SBI|HDFC|ICICI|AXIS)\.$', last_token, re.I):
    #       return False

    #   # 5. SECTION/EXHIBIT REFERENCES
    #   section_patterns = [
    #       re.compile(r'^(Sec|Section|Art|Article|Rule|Cl|Clause|Para|Paragraph|Sub-sec|Sub-cl|Sch|Schedule|Ch|Chapter|Pt|Part)\.\s*\d+$', re.I),
    #       re.compile(r'^\d+\.$'),
    #       re.compile(r'^\d+\.\d+\.$'),
    #       re.compile(r'^\d+\.\d+\.\d+\.$'),
    #       re.compile(r'^(Sec|Section|Art|Article|Rule)\s+\d+\.$', re.I),
    #       re.compile(r'^(Exh|Exhibit|Ex)\.?\s*\d+[A-Za-z]*\.?$', re.I),
    #   ]
    #   for pattern in section_patterns:
    #       if pattern.match(last_token.strip()):
    #           return False

    #   # 6. LEGAL ABBREVIATIONS
    #   clean_token_for_abbr = re.sub(r'[^\w]', '', last_token).lower()
    #   abbr_variants = [
    #       clean_token_for_abbr + '.',
    #       last_token.lower(),
    #       clean_token_for_abbr,
    #       last_token.lower().rstrip('.')
    #   ]
    #   for abbr in abbr_variants:
    #       if abbr in self._abbr_clean:
    #           return False

    #   extended_legal_abbrevs = {
    #       'ld.', 'learned', 'adv.', 'advocate', 'sr.', 'senior', 'jr.', 'junior',
    #       'retd.', 'retired', 'addl.', 'additional', 'asstt.', 'assistant',
    #       'govt.', 'government', 'dept.', 'department', 'min.', 'ministry',
    #       'commr.', 'commissioner', 'collr.', 'collector', 'dist.', 'district',
    #       'tehsildar', 'sdo', 'bdo', 'ceo', 'cfo', 'cmd', 'md', 'gm', 'dgm',
    #       'exh.', 'ex.', 'exhibit', 'v/s.', 'vs.', 'v/s', 'ors', 'ors.'
    #   }
    #   for abbr_variant in abbr_variants:
    #       if abbr_variant in extended_legal_abbrevs:
    #           return False

    #   # 7. LOOKAHEAD CHECK
    #   if next_text is not None:
    #       nxt = next_text.strip()
    #       if nxt:
    #           nxt_clean = re.sub('^[\'"\\u00AB\\u00BB\\[\\(\\{\\s]+', '', nxt)
    #           if nxt_clean:
    #               if re.search(r'[.?!:;][\'"”’)]*$', s) and re.match(r'^[\'"“”‘’]', nxt):
    #                   return True

    #               # Rule 1: punctuation + uppercase → True
    #               if trailing_punct and nxt_clean and nxt_clean[0].isupper():
    #                   return True

    #               # 🔧 NEW Rule: punctuation + bullet → True
    #               if trailing_punct  and re.match(r'^\(?[a-z0-9ivxlcdm]+\)', nxt_clean, re.I):
    #                   return True

    #               # Rule 2: punctuation + next bullet form → True
    #               bullet_start_patterns = [
    #                   re.compile(r'^\d+\.$'),
    #                   re.compile(r'^\(\d+\)$'),
    #                   re.compile(r'^\d+\)$'),
    #                   re.compile(r'^[a-z]\.$', re.I),
    #                   re.compile(r'^\([a-z]\)$', re.I),
    #                   re.compile(r'^[ivxlcdm]+\.$', re.I),
    #                   re.compile(r'^\([ivxlcdm]+\)$', re.I),
    #                   re.compile(r'^\s*\d+[A-Z]*(?:-[A-Z]+)?\s*\.\s*\S*', re.IGNORECASE),
    #                   re.compile(r'^\(\s*([^\s\)]+)\s*\)\s*\S*', re.IGNORECASE),
    #                   re.compile(r'^(\s*\d+[A-Z]*(?:-[A-Z]+)?\.\s*)(.*)', re.IGNORECASE)
    #               ]
    #               if trailing_punct:
    #                   for pattern in bullet_start_patterns:
    #                       if pattern.match(nxt_clean):
    #                           return True

    #               # If next is a pure bullet
    #               for pattern in pure_bullet_patterns:
    #                   if pattern.match(nxt_clean.split()[0]):
    #                       return True

    #               # Section headings
    #               section_start_patterns = [
    #                   re.compile(r'^\d+\s*\(\d+\)'),
    #                   re.compile(r'^(Sec|Section|Art|Article|Rule|Cl|Clause|Para|Paragraph|Sub-sec|Sub-cl|Sch|Schedule|Ch|Chapter|Pt|Part)\s+\d+', re.I),
    #                   re.compile(r'^\d+\.\s+[A-Z]'),
    #                   re.compile(r'^\d+\.\d+\s'),
    #               ]
    #               for pattern in section_start_patterns:
    #                   if pattern.match(nxt_clean):
    #                       return True

    #               # Continuations (don’t split)
    #               continuation_patterns = [
    #                   re.compile(r'^\d+'),
    #                   re.compile(r'^[ivxlcdmIVXLCDM]+[.)\]\}]'),
    #                   re.compile(r'^\([a-z0-9ivxlcdm]+\)', re.I),
    #                   re.compile(r'^[a-z0-9ivxlcdm]+\.', re.I),
    #                   re.compile(r'^\('),
    #               ]
    #               for pattern in continuation_patterns:
    #                   if pattern.match(nxt_clean):
    #                       return False
    #       return True
    #   # 🔧 Patch 2: At page end → don’t force False
    #   return True


    def check_line_preserve(self, text, next_text, at_page_end, text_coords, next_text_coords, pg_height, pg_width):
      # if text_coords:
      #     x0, y0, x1, y1 = text_coords
      # else:
      #     x0 = y0 = x1 = y1 = None

      # if next_text_coords:
      #     nx0, ny0, nx1, ny1 = next_text_coords
      # else:
      #     nx0 = ny0 = nx1 = ny1 = None

      # s = (text or "").strip()
      # nxt = (next_text or "").strip()
      # if not s:
      #     return False

      # # === Bullet / numbered point detection ===
      # if re.match(r'^\s*(\d+(\.\d+)*|\(?[a-zivxlcdm]+\)|[•\-–])', s, re.I):
      #     return True

      # # === TOC / dot leaders ===
      # if re.search(r'\.{3,}$', s):
      #     return True

      # # === Same line continuation → merge ===
      # if text_coords and next_text_coords:
      #     line_height = (y1 - y0) if (y1 and y0) else 10
      #     same_line = abs(y0 - ny0) < 0.5 * line_height
      #     if same_line:
      #         if pg_width and abs(nx0 - x0) > 0.35 * pg_width:  # columns
      #             return True
      #         return False

      # # === Soft wrap continuation → merge ===
      # if text_coords and next_text_coords:
      #     if not re.search(r'[.?!:;]$', s):
      #         vertical_gap = (ny0 - y1) if (ny0 and y1) else 0
      #         if 0 < vertical_gap < 1.3 * (y1 - y0 if (y1 and y0) else 12):
      #             if abs((nx0 or 0) - (x0 or 0)) < 0.05 * pg_width:
      #                 return False

      # # === Sentence end → preserve ===
      # if re.search(r'[.?!:;]$', s):
      #     if nxt and nxt[0].isupper():
      #         return True

      # # === Headings, signatures, right align, etc. (same as before) ===
      # if len(s) < 40 and text_coords and pg_width:
      #     mid = (x0 + x1) / 2
      #     if abs(mid - pg_width / 2) < 0.1 * pg_width:
      #         return True
      # if x1 and pg_width and x1 > 0.85 * pg_width:
      #     return True
      # if nx0 and pg_width and nx0 > 0.75 * pg_width:
      #     return True

      # # === Page boundary ===
      # if at_page_end:
      #     if not re.search(r'[.?!:;]$', s):
      #         return False
      #     return True

      # # Default → merge
      # return False
      if not text_coords or not next_text_coords:
        return False  # if no coords, fallback to merge

      x0, y0, x1, y1 = text_coords
      nx0, ny0, nx1, ny1 = next_text_coords
      line_height = (y1 - y0) if (y1 and y0) else 12

      # === 1. Same line continuation ===
      if abs(y0 - ny0) < 0.5 * line_height:
          return False

      # === 2. Left margin aligned + not touching right margin → merge ===
      if abs(x0 - nx0) < 0.05 * pg_width and x1 < 0.8 * pg_width:
          return False

      # === 3. Big right margin gap + next line → preserve ===
      if (pg_width - x1) > 0.25 * pg_width and ny0 > y1:
          return True

      # === 4. Indented next line → preserve ===
      if (nx0 - x0) > 0.08 * pg_width:
          return True

      # === 5. Large vertical spacing → preserve ===
      if ny0 - y1 > 1.5 * line_height:
          return True

      # === 6. Page boundary → preserve ===
      if at_page_end:
          return True

      # Default → merge
      return False

