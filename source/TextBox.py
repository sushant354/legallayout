# import re
# import string
# import logging

# class TextBox:
    
#     def __init__(self, tb, font_mapper):
#         self.logger = logging.getLogger(__name__)
#         self.tbox = tb
#         self.coords = tuple(map(float, tb.attrib["bbox"].split(",")))
#         self.height = self.coords[3] - self.coords[1]
#         self.width = self.coords[2] - self.coords[0]
#         self.font_mapper = font_mapper
#         self.footnotes_superscript   = {}


#     # --- get texts from the textbox ---
#     def extract_text_from_tb(self):
#         all_text = []
#         try:
#             for textline in self.tbox.findall('.//textline'):
#                 line_texts = []
#                 for text in textline.findall('.//text'):
#                     # font = text.attrib.get("font", "")
#                     # raw_text = text.text or ""
#                     # if font and text.text:
#                     #     resolved_text = self.font_mapper.resolve_char(font, raw_text)
#                     #     line_texts.append(resolved_text)
#                     if text.text:
#                         line_texts.append(text.text)
                
#                 line = ''.join(line_texts).replace("\n", " ").strip()
#                 if line:
#                     all_text.append(line)
            
#             # Join all lines with space (or newline if you want separation)
#             return ' '.join(all_text)
#         except Exception as e:
#             self.logger.error(f"Failed to extract text from textbox on page {getattr(self, 'pg_num', 'unknown')}: {e}")
#             return ""


import re
import string
import logging


class TextBox:

    def __init__(self, tb, pdf_type, font_mapper):
        self.logger = logging.getLogger(__name__)

        self.tbox = tb

        self.coords = tuple(map(float, tb.attrib["bbox"].split(",")))
        self.height = self.coords[3] - self.coords[1]
        self.width = self.coords[2] - self.coords[0]

        self.font_mapper = font_mapper

        self.avg_font_size = self.get_avg_font_size()

        # {(x0,y0,x1,y1): "3"}
        self.footnotes_superscript = {}

        if pdf_type in set(['sebi_circulars']):
            self.get_footnotes_superscript()


    def get_avg_font_size(self):

        total_size = 0.0

        count = 0

        for txt in self.tbox.findall(".//text"):

            try:

                size = float(
                    txt.attrib.get("size")
                )

                total_size += size

                count += 1

            except:
                continue

        if count == 0:
            return 0

        return total_size / count
    
    def get_footnotes_superscript(self):

        try:
            for textline in self.tbox.findall(".//textline"):

                chars = textline.findall(".//text")

                if not chars:
                    continue

                # collect sizes
                sizes = []
                bottoms = []

                for ch in chars:
                    try:
                        if "size" in ch.attrib:
                            sizes.append(float(ch.attrib["size"]))

                        if "bbox" in ch.attrib:
                            x0, y0, x1, y1 = map(
                                float,
                                ch.attrib["bbox"].split(",")
                            )
                            bottoms.append(y0)

                    except Exception:
                        continue

                if not sizes:
                    continue

                # normal text assumptions
                base_size = max(sizes)
                base_bottom = min(bottoms) if bottoms else 0

                for ch in chars:

                    txt = ch.text or ""

                    if not txt.strip():
                        continue

                    try:
                        size = float(ch.attrib.get("size", base_size))

                        x0, y0, x1, y1 = map(
                            float,
                            ch.attrib["bbox"].split(",")
                        )


                        smaller_font = size < (base_size * 0.85)

                        raised = y0 > (base_bottom + (base_size * 0.15))

                        # mostly digits/symbol markers
                        valid_mark = bool(
                            re.fullmatch(r"[0-9*†‡]+", txt.strip())
                        )

                        if smaller_font and raised and valid_mark:
                            self.footnotes_superscript[
                                (x0, y0, x1, y1)
                            ] = txt.strip()

                    except Exception:
                        continue

        except Exception as e:
            self.logger.error(
                f"Failed superscript detection: {e}"
            )

    def extract_plain_text(self):
        all_text = []

        try:
            for textline in self.tbox.findall(".//textline"):

                chars = []

                for text in textline.findall(".//text"):
                    if text.text:
                        chars.append(text.text)

                line = "".join(chars).replace("\n", " ").strip()

                if line:
                    all_text.append(line)

            return " ".join(all_text)

        except Exception as e:
            self.logger.error(f"Plain text extraction failed: {e}")
            return ""
    
    def extract_text_from_tb(self):
        if not self.footnotes_superscript:
            return self.extract_plain_text()

        all_text = []

        try:
            for textline in self.tbox.findall(".//textline"):

                line_texts = []
                pending_superscript = []

                for text in textline.findall(".//text"):

                    raw = text.text or ""

                    if not raw:
                        continue

                    is_super = False

                    if "bbox" in text.attrib:
                        try:
                            bbox = tuple(
                                map(float,
                                    text.attrib["bbox"].split(","))
                            )

                            if bbox in self.footnotes_superscript:
                                pending_superscript.append(
                                    self.footnotes_superscript[bbox]
                                )
                                is_super = True

                        except Exception:
                            pass

                    if not is_super:

                        if pending_superscript:
                            marker = "".join(pending_superscript)

                            line_texts.append(
                                "{{^{{FOOTNOTE " + marker + "}}}}"
                            )

                            pending_superscript = []

                        line_texts.append(raw)

                if pending_superscript:
                    marker = "".join(pending_superscript)

                    line_texts.append(
                        "{{^{{FOOTNOTE " + marker + "}}}}"
                    )

                line = "".join(line_texts).replace("\n", " ").strip()

                if line:
                    all_text.append(line)

            return " ".join(all_text)

        except Exception as e:
            self.logger.error(f"Failed to extract text: {e}")
            return ""
    
    # --- func to detect the textbox having texts font in bold for heading/title detection ---
    def textFont_is_bold(self, pdf_type = None):
        bold_font_re = re.compile(r'bold', re.IGNORECASE)
        no_of_chars = 0
        no_of_bold_chars = 0

        try:
            for textline in self.tbox.findall(".//textline"):
                for text in textline.findall(".//text"):
                    if text.text :
                        no_of_chars += 1
                        font_name = text.attrib.get("font", "")
                        if bold_font_re.search(font_name):
                            no_of_bold_chars += 1

            if no_of_chars == 0:
                return False  # Avoid division by zero
            
            if pdf_type == 'sebi':
                return (no_of_bold_chars / no_of_chars) > 0.50
            elif pdf_type == 'acts':
                return (no_of_bold_chars / no_of_chars) > 0.50#0.1
            else:
                return (no_of_bold_chars / no_of_chars) > 0.75
            
        except Exception as e:
            self.logger.error(f"Error detecting is_bold text in textbox [{self.extract_text_from_tb()}]: {e}")
            return False


    # --- func to detect the textbox having texts font in italic for heading/title detection ---
    def textFont_is_italic(self, pdf_type = None):
        italic_font_re = re.compile(r'italic', re.IGNORECASE)
        no_of_chars = 0
        no_of_italic_chars = 0
        try:
            for textline in self.tbox.findall(".//textline"):
                for text in textline.findall(".//text"):
                    if text.text:
                        no_of_chars += 1
                        font_name = text.attrib.get("font", "")
                        if italic_font_re.search(font_name):
                            no_of_italic_chars += 1

            if no_of_chars == 0:
                return False  # Avoid division by zero

            if pdf_type == 'sebi':
                return (no_of_italic_chars / no_of_chars) > 0.7
            elif pdf_type == 'acts':
                return (no_of_italic_chars / no_of_chars) > 0.50 #0.1
            else:
                return (no_of_italic_chars / no_of_chars) > 0.75
        except Exception as e:
            self.logger.error(f"Error detecting is_italic text in textbox [{self.extract_text_from_tb()}]: {e}")
            return False

        
    # --- func to detect the textbox having texts font in Upper Case for heading/title detection ---
    def is_uppercase(self, pdf_type = None):
        total_letters = 0
        total_uppercase = 0

        if pdf_type == 'sebi':
            return False
        try:
            for textline in self.tbox.findall(".//textline"):
                for text in textline.findall(".//text"):
                    if text.text:
                        for char in text.text:
                            if char.isalpha():
                                total_letters += 1
                                if char.isupper():
                                    total_uppercase += 1

            if total_letters == 0:
                return False  # Avoid division by zero

            # if pdf_type == 'sebi':
            #     return (total_uppercase / total_letters) >= 0.70
            if pdf_type == 'acts':
                return (total_uppercase / total_letters) >= 0.40  #0.25
            else:
                return (total_uppercase / total_letters) >= 0.75 

        except Exception as e:
            self.logger.error(f"Error detecting is_uppercase text in textbox [{self.extract_text_from_tb()}]: {e}")
            return False

    
    # --- func to detect the textbox having texts font in Title Case for heading/title detection ---
    def is_titlecase(self, pdf_type = None):
        words = []
        if pdf_type == 'sebi':
            return False
        try:
            for textline in self.tbox.findall(".//textline"):
                for text in textline.findall(".//text"):
                    if text.text and isinstance(text.text, str):
                        # Optional: strip brackets around the text
                        cleaned_text = re.sub(r'^[\[\(\{]+|[\]\)\}]+$', '', text.text.strip())
                        words.extend(cleaned_text.split())

            if not words:
                return False

            titlecase_count = 0
            valid_word_count = 0

            for word in words:
                # Remove trailing punctuation like commas, periods, etc.
                word = word.strip(string.punctuation)

                # Skip empty words after cleaning
                if not word:
                    continue

                # Check if the word contains at least one alphabetic character
                if any(c.isalpha() for c in word):
                    valid_word_count += 1

                    # Check if first letter uppercase and the rest lowercase
                    if len(word) == 1:

                        # For single letter words, just check uppercase
                        if word[0].isupper():
                            titlecase_count += 1
                    else:
                        if word[0].isupper() and word[1:].islower():
                            titlecase_count += 1

            if valid_word_count == 0:
                return False

            # Return True if at least 25% of words are titlecase
            # if pdf_type == 'sebi':
            #     return (titlecase_count / valid_word_count) >= 0.70
            if pdf_type == 'acts':
                return (titlecase_count / valid_word_count) >= 0.40#0.25
            else:
                return (titlecase_count / valid_word_count) >= 0.75
        
        except Exception as e:
            self.logger.error(f"Error detecting is_titlecase text in textbox [{self.extract_text_from_tb()}]: {e}")
            return False

    
    # --- func to get the first char coords of the textbox ---
    def get_first_char_coordX0(self):
        try:
            for textline in self.tbox.findall('.//textline'):
                for text in textline.findall('.//text'):
                    if text.text and 'bbox' in text.attrib:
                        parts = text.attrib['bbox'].split(',')
                        if len(parts) >= 1:
                            x0 = float(parts[0])
                            return x0
                        else:
                            self.logger.warning("Malformed bbox attribute: '%s'", text.attrib['bbox'])
            self.logger.debug("No valid bbox found for first character X0 in textbox.",self.extract_text_from_tb())
            return None
        except Exception as e:
            self.logger.error("Error in get_first_char_coordX0: %s", str(e))
            return None
    
    # --- func to get the cleaned side note datas ---
    def get_side_note_datas(self, side_note_datas):
        current_sentence = []
        sentence_start_coords = None
        recording = False
        try:
            for textline in self.tbox.findall('.//textline'):
                line_texts = []
                for text in textline.findall('.//text'):
                    if text.text:
                        line_texts.append(text.text)

                line = ''.join(line_texts).replace("\n", " ").strip()
                if not line:
                    continue

                if not recording:
                    sentence_start_coords = textline.attrib
                    recording = True

                current_sentence.append(line)

                if line.endswith('.'): # if '.' in line
                    sentence = ' '.join(current_sentence).strip()
                    coord_key = tuple(map(float,sentence_start_coords.get('bbox').split(",")))
                    if sentence and sentence not in set(side_note_datas.values()):
                        side_note_datas[coord_key] = sentence

                    # Reset for next sentence
                    current_sentence = []
                    recording = False
                    sentence_start_coords = None
        except Exception as e:
            self.logger.error("Error in get_side_note_datas: %s", str(e))

    

    def get_first_char_coords(self):
        try:
            for textline in self.tbox.findall('.//textline'):
                for text in textline.findall('.//text'):
                    if text.text and 'bbox' in text.attrib:
                        parts = text.attrib['bbox'].split(',')
                        if len(parts) == 4:
                            try:
                                coords = tuple(map(float, parts))
                                return coords
                            except ValueError:
                                self.logger.warning("Non-numeric bbox attribute: '%s'", text.attrib['bbox'])
                        else:
                            self.logger.warning("Malformed bbox attribute: '%s'", text.attrib['bbox'])
            self.logger.debug("No valid bbox found for first character in textbox: %s", self.extract_text_from_tb())
            return None
        except Exception as e:
            self.logger.error("Error in get_first_char_coords: %s", str(e))
            return None


    def get_last_char_coords(self):
        try:
            last_coords = None
            for textline in self.tbox.findall('.//textline'):
                for text in textline.findall('.//text'):
                    if text.text and 'bbox' in text.attrib:
                        parts = text.attrib['bbox'].split(',')
                        if len(parts) == 4:
                            try:
                                coords = tuple(map(float, parts))
                                last_coords = coords  # keep overwriting → last char at end
                            except ValueError:
                                self.logger.warning("Non-numeric bbox attribute: '%s'", text.attrib['bbox'])
                        else:
                            self.logger.warning("Malformed bbox attribute: '%s'", text.attrib['bbox'])
            if last_coords is None:
                self.logger.debug("No valid bbox found for last character in textbox: %s", self.extract_text_from_tb())
            return last_coords
        except Exception as e:
            self.logger.error("Error in get_last_char_coords: %s", str(e))
            return None

