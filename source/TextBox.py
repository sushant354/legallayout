import re
import string
import logging


# The font a piece of text is drawn in is carried through the html builder as a
# mark in the text itself rather than as real <span> markup: the builder reads
# the text it is given (a label's number, a sentence's last token, the
# punctuation a paragraph ends on) to decide what to emit, and a tag in the
# middle of it would change those decisions - i.e. the document's structure -
# rather than just annotate the result. The marks are turned into
# <span data-font="..."> only once the whole html is built
# (HTMLBuilder.render_font_spans).
#
# The two delimiters are unicode *noncharacters*: permanently unassigned, never
# interchanged, so no pdf can draw one and nothing on the way to the html can
# have an opinion about them. The private use area is not usable here even
# though nothing draws from it either - NormalizeText.NORMALIZE_MAP deletes
# U+E000..U+E003 outright as OCR junk, and every piece of text the builder
# emits goes through it.
FONT_MARK_START = '\ufdd0'
FONT_MARK_END = '\ufdd1'
FONT_MARK_RE = re.compile('%s([^%s]*)%s' % (FONT_MARK_START, FONT_MARK_END,
                                            FONT_MARK_END))


def font_mark(font):
    return FONT_MARK_START + font + FONT_MARK_END


def strip_font_marks(text):
    """The text as it would have been without the font marks in it."""
    if not isinstance(text, str) or FONT_MARK_START not in text:
        return text

    return FONT_MARK_RE.sub('', text)


def mark_font_runs(chars):
    """(character, font) pairs -> that text with a mark at every font change.

    A character with no font of its own - whitespace, and the text this tool
    inserts itself, e.g. a footnote placeholder - carries on in the run around
    it. A mark therefore always sits immediately before a non-space character,
    which is what makes str.strip() (used all over the html builder) unable to
    separate a run of text from the mark that names its font.
    """
    out = []
    current = None

    for char, font in chars:
        if font is not None and font != current:
            out.append(font_mark(font))
            current = font

        out.append(char)

    return ''.join(out)


def split_marked_text(text, index):
    """Split marked text at an index of its *plain* text, into (head, tail).

    The tail is given the mark that was in force at the split, so that either
    half can be emitted on its own. This is how a regexp that has to run on the
    plain text (a section number, say) still yields marked halves.
    """
    head = []
    current = None
    plain_count = 0
    pos = 0

    while pos < len(text) and plain_count < index:
        match = FONT_MARK_RE.match(text, pos)

        if match:
            current = match.group(1)
            head.append(match.group(0))
            pos = match.end()
            continue

        head.append(text[pos])
        pos += 1
        plain_count += 1

    tail = text[pos:]

    if current is not None and tail:
        # the inherited mark goes in front of the tail's first non-space
        # character, keeping the invariant mark_font_runs() establishes
        start = 0
        while start < len(tail) and tail[start].isspace():
            start += 1

        if not FONT_MARK_RE.match(tail, start):
            tail = tail[:start] + font_mark(current) + tail[start:]

    return ''.join(head), tail


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
    
    # --- the same text these two return, with the font of every character ---
    def get_char_lines(self, use_footnotes=None):
        r"""One list of (character, font) per textline, as extract_text_from_tb()
        assembles them: '\n' turned into a space, the line stripped and dropped
        when nothing is left of it.

        Whitespace and the footnote placeholder carry no font of their own (see
        mark_font_runs), so they never break a run in two.
        """
        if use_footnotes is None:
            use_footnotes = bool(self.footnotes_superscript)

        all_lines = []

        try:
            for textline in self.tbox.findall(".//textline"):
                line = []
                pending_superscript = []

                for text in textline.findall(".//text"):
                    raw = text.text or ""

                    if not raw:
                        continue

                    if use_footnotes:
                        if self.is_superscript_char(text):
                            pending_superscript.append(
                                self.footnotes_superscript[
                                    tuple(map(float, text.attrib["bbox"].split(",")))
                                ]
                            )
                            continue

                        if pending_superscript:
                            line.extend(
                                self.get_footnote_placeholder_chars(pending_superscript)
                            )
                            pending_superscript = []

                    font = text.attrib.get("font") or None

                    for char in raw:
                        char = " " if char == "\n" else char
                        line.append((char, font if not char.isspace() else None))

                if pending_superscript:
                    line.extend(
                        self.get_footnote_placeholder_chars(pending_superscript)
                    )

                line = self.strip_chars(line)

                if line:
                    all_lines.append(line)

        except Exception as e:
            self.logger.error(f"Failed to extract the fonts of the text: {e}")
            return []

        return all_lines

    def is_superscript_char(self, text):
        if "bbox" not in text.attrib:
            return False

        try:
            bbox = tuple(map(float, text.attrib["bbox"].split(",")))
        except Exception:
            return False

        return bbox in self.footnotes_superscript

    @staticmethod
    def get_footnote_placeholder_chars(pending_superscript):
        placeholder = "{{^{{FOOTNOTE " + "".join(pending_superscript) + "}}}}"

        return [(char, None) for char in placeholder]

    @staticmethod
    def strip_chars(chars):
        start = 0
        end = len(chars)

        while start < end and chars[start][0].isspace():
            start += 1

        while end > start and chars[end - 1][0].isspace():
            end -= 1

        return chars[start:end]

    def extract_text_with_fonts(self):
        """extract_text_from_tb()'s text with a font mark at every font change."""
        lines = self.get_char_lines()

        chars = []

        for line in lines:
            if chars:
                chars.append((" ", None))

            chars.extend(line)

        return mark_font_runs(chars)

    def extract_lines_with_fonts(self):
        """The textbox's lines, each with the font marks of its own text.

        The footnote placeholder is deliberately left out, matching the line by
        line extraction the title path does inline.
        """
        return [mark_font_runs(line)
                for line in self.get_char_lines(use_footnotes=False)]

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
            elif pdf_type == 'sebi_circulars':
                return (no_of_bold_chars / no_of_chars) > 0.80
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
            if pdf_type == 'sebi_circulars':
                return False
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

        if pdf_type == 'sebi' or pdf_type == 'sebi_circulars':
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
            elif pdf_type == 'sebi_circulars':
                return False
            elif pdf_type == 'egazette':
                return False
            else:
                return (total_uppercase / total_letters) >= 0.75 

        except Exception as e:
            self.logger.error(f"Error detecting is_uppercase text in textbox [{self.extract_text_from_tb()}]: {e}")
            return False

    
    # --- func to detect the textbox having texts font in Title Case for heading/title detection ---
    def is_titlecase(self, pdf_type=None):
        words = []

        if pdf_type == 'sebi' or pdf_type == 'sebi_circulars':
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
                # Remove leading/trailing punctuation like commas, periods, etc.
                word = word.strip(string.punctuation)

                # Skip empty words after cleaning
                if not word:
                    continue

                # Must contain at least one alphabetic character
                if not any(c.isalpha() for c in word):
                    continue

                if len(word) == 1:
                    continue

                valid_word_count += 1

                # Titlecase = first letter uppercase, remaining letters lowercase
                if word[0].isupper() and word[1:].islower():
                    titlecase_count += 1

            if valid_word_count == 0:
                return False

            if pdf_type == 'acts':
                return (titlecase_count / valid_word_count) >= 0.40  # 0.25
            elif pdf_type == 'sebi_circulars':
                return False
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

