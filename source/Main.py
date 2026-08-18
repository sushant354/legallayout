import os
import argparse
from difflib import SequenceMatcher
from pathlib import Path
from collections import defaultdict
import re
import codecs
import html
import logging
import shutil
import pymupdf
from .ParserTool import ParserTool, ChromeLensParserTool, TesseractParserTool
from .Page import Page, SectionState
from .Judgment import JudgmentBuilder
from .HTMLBuilder import HTMLBuilder, HTMLBuilderChromeLens
from .Acts import Acts
from .SebiCirculars import SebiCirculars
from .Amendment import Amendment
from .Utils import *
from .FontMapper import DynamicFontMapper
from .Manifest import IIIFManifest
from .TableExtraction import HeaderRowClassifier, RegionMergeClassifier, ContinuationClassifier

from contextlib import contextmanager

try:
    from indic2unicode.fontconv import FontConv
    INDIC2UNICODE_AVAILABLE = True
except ImportError:
    INDIC2UNICODE_AVAILABLE = False

try:
    from indic2unicode.tools.fix_tounicode import ToUnicodeFixer, get_font_converter
    TOUNICODE_FIX_AVAILABLE = True
except ImportError:
    TOUNICODE_FIX_AVAILABLE = False

try:
    from camelot import handlers as camelot_handlers
    from camelot.utils import get_image_char_and_text_objects
    # camelot offers no hook of its own, so the conversion is installed by
    # wrapping the one function all of its layouts come from
    CAMELOT_LAYOUT_AVAILABLE = hasattr(camelot_handlers, "get_page_layout")
except ImportError:
    CAMELOT_LAYOUT_AVAILABLE = False


# --- accessors that let one conversion run over both kinds of char it has to
# --- handle: the <text> elements of the xml, and camelot's own LTChar objects

def get_xml_text_font(text):
    return text.attrib.get("font")


def get_xml_text_text(text):
    return text.text


def set_xml_text_text(text, value):
    text.text = value


def get_lt_char_font(char):
    # LTAnno, the spaces and newlines pdfminer inserts itself, carries no font
    return getattr(char, "fontname", None)


def get_lt_char_text(char):
    return char.get_text()


def set_lt_char_text(char, value):
    char._text = value


XML_TEXT_ACCESSORS = (get_xml_text_font, get_xml_text_text, set_xml_text_text)
LT_CHAR_ACCESSORS = (get_lt_char_font, get_lt_char_text, set_lt_char_text)

# --- names a legacy indic font is known by in a pdf, when they are not the
# --- name of its indic2unicode converter: Kruti Dev is written with a space
# --- (and usually a face number after it, 'Kruti Dev 010'), so the converter
# --- key 'krutidev' never matches the name the pdf carries. Vivek and DevLys
# --- are the same encoding under other names, and the pdf font is named for
# --- its face rather than its encoding ('Vivek-BoldA', 'DevLys 010'), which a
# --- face number ('DevLys 020') or a style word can follow. indic2unicode
# --- points its own 'vivek'/'devlys' keys at the very same converter object,
# --- so naming them here only makes the equivalence hold on a build that
# --- predates those keys
INDIC_FONT_NAME_ALIASES = {
    'krutidev': [r'kruti[\s_-]*dev', r'vivek', r'dev[\s_-]*lys'],
}

# --- the model machinelearning/training.py writes, which says what a font is
# --- drawing from the text extracted from it, see detect_unknown_fonts()
FONT_MODEL_PATH = PROJECT_ROOT / 'model' / 'eng_hin_fonts.pkl'

# --- the classes that model is trained on, mapped to the converter the text of
# --- such a font needs. A class already named after its converter needs no
# --- entry here, these are only the ones the two spell differently
FONT_CLASS_CONVERTERS = {
    'nirmala': 'nirmalaui',
}

# --- the class of the fonts that need no decoder at all: their text is already
# --- unicode and is taken as it is
FONT_CLASS_NOT_REQUIRED = 'not_required'

# --- classes that do identify the font but have no converter to point it at. A
# --- Type3 font's text is put right by repair_tounicode() before anything is
# --- extracted from the pdf, so by the time it can be classified at all there
# --- is nothing left to do to it
FONT_CLASSES_WITHOUT_CONVERTER = {'type3'}

# --- how much text drawn in one font is enough to say what it is. The model is
# --- trained on samples of 50 words and its confidence falls apart well below
# --- that: the chanakya of union_hindi.pdf scores 1.00 on the 1000 words it
# --- draws, 0.6 to 0.8 on the first 20 of them, 0.3 on the first 5, and a font
# --- that draws two words gets nothing but the model's prior. A font drawing
# --- less than this is left alone rather than decoded on a coin toss
FONT_DETECT_MIN_WORDS = 20

# --- and how sure the model has to be before its answer is acted on
FONT_DETECT_MIN_PROB = 0.5

# --- the fonts whose name does say what they are drawing, and says that it is
# --- plain latin unicode: the standard text faces, the ones the corpus's
# --- not_required class is built out of (see FontSurvey's -nf). A font named
# --- like one of these is never handed to the model at all - there is nothing
# --- for detection to add, since the answer is already known, and a false
# --- positive on one of them would decode text that is perfectly readable as
# --- it is. This is the name-based half of the same guard get_detected_font_key()
# --- applies to the model's answers. Written with an optional separator inside
# --- the multi word names because a pdf spells them both ways (Book Antiqua and
# --- BookAntiqua, Yu Gothic and YuGothic), and matched anywhere in the name,
# --- case insensitively, exactly as the -fc and the built in font names are -
# --- so a subset prefix or a style suffix (ABCDEF+TimesNewRoman,Bold) matches too
FONT_DETECT_SKIP_RE = re.compile(
    r'times|arial|calibri|cambria|courier|helvetica|verdana|tahoma|garamond'
    r'|book[\s_-]*antiqua|bookman|liberation|nimbus|myriad[\s_-]*pro'
    r'|minion[\s_-]*pro|segoe|malgun|yu[\s_-]*gothic',
    re.IGNORECASE
)

# --- the one name the list above catches that must still be classified: Arial
# --- Unicode MS is not a plain latin face, it is a devanagari font shipping a
# --- ToUnicode map that was built wrong, so its text extracts as the wrong
# --- devanagari and does need a decoder (see get_repaired_font_res). Normally
# --- repair_tounicode() places it long before detection is reached, and the
# --- whitelist is never consulted for it; this exception is for the build where
# --- that does not happen - indic2unicode without ToUnicodeFixer - where the
# --- model naming it arialuni is the only thing left that can place it. Spelt
# --- with the same optional separator as the names above, since a pdf embeds it
# --- both as Arial Unicode MS and as ArialUnicodeMS
FONT_DETECT_SKIP_EXCEPT_RE = re.compile(r'arial[\s_-]*unicode', re.IGNORECASE)

# --- evidence saturates long before a whole document is read (100 words of the
# --- gazette's chanakya already score 1.00), while the features are every 1 to
# --- 5 word phrase of the text, so there is nothing to gain from featurizing
# --- the 300,000 words a long gazette draws in one font
FONT_DETECT_MAX_WORDS = 20000

# --- the classes whose text is already drawn in an indic script by the time it
# --- is extracted: a font whose ToUnicode map is broken draws real devanagari,
# --- it is just the wrong devanagari ('निर्माण' as 'जिमावण'), and type3 text was
# --- put right by repair_tounicode() before anything read the pdf. Every other
# --- class is a legacy 8-bit encoding that overloads the latin codepoints, so
# --- its text extracts as latin and cannot contain an indic character at all -
# --- which is what makes the check in get_detected_font_key() possible, and
# --- what makes assuming it of a class not named here the safe default
FONT_CLASSES_INDIC_TEXT = {
    'arialuni', 'nirmala', 'nirmalaui', 'type3', FONT_CLASS_NOT_REQUIRED,
}

# --- the indic scripts, from devanagari through sinhala plus the devanagari
# --- extended block, used to tell text that is already decoded from the latin
# --- a legacy encoding draws
INDIC_SCRIPT_RE = re.compile(r'[\u0900-\u0DFF\uA8E0-\uA8FF]')

# --- and the share of a font's sampled characters that has to be in one of
# --- those scripts before the model saying it is a legacy latin encoding is
# --- read as the impossibility it is, see get_detected_font_key(). A little
# --- indic text does turn up inside an otherwise latin font (a stray glyph, a
# --- symbol picked out of another block), so this is not zero; it is nowhere
# --- near 0.05 either way in practice - the mixed hindi/english CIDFont+F1 of
# --- test/test_pdfs/lsdebate.pdf sits at 0.32, and the chanakya of
# --- union_hindi.pdf and the vivek of act1.pdf at 0.00
FONT_DETECT_MAX_INDIC_RATIO = 0.05

# --- how much of a space's own width has to sit inside the glyph next to it
# --- before it counts as painted over rather than as a real word break, see
# --- Main.drop_overlapping_spaces(). A space that is genuinely there does
# --- overlap its neighbour a little - kerning pulls a letter back over the
# --- space before it, most of a space's width for a '.' followed by a capital
# --- - so nothing short of the whole of it being covered says anything. That
# --- is what an overprinted space looks like anyway: it sits entirely within
# --- the glyph drawn over it, having advanced the pen by nothing at all
SPACE_OVERLAP_RATIO = 0.95

# --- when the two glyphs on either side of a space overlap each other by this
# --- much of their own width, the text itself is drawn overlapping - rotated
# --- watermarks come out of pdfminer with every char's bbox covering the next
# --- one's - and no conclusion can be drawn from a space overlapping too
NEIGHBOUR_OVERLAP_RATIO = 0.1


class Main:
    def __init__(self,pdfPath,is_amendment_pdf,output_dir, pdf_type, has_side_notes, has_doc_end,
                 is_footnote_continuation, min_img_pixels, ocr_language, is_scanned_copy,
                 table_extract, public_base_url=None, server_root=None,
                 rights=None, provider_id=None, provider_name=None, 
                 attribution=None, figure_text=False, font_conv_map=None,
                 ocr_engine="tesseract", font_model=None,
                 font_detect=True): #start,end,is_amendment_pdf,output_dir, pdf_type):
        self.logger = logging.getLogger('source.Main')
        if self.is_url_like(output_dir):
            raise ValueError(
                f"output_dir ('{output_dir}') looks like a URL, not a local filesystem "
                f"path where output files get written. Did you mean to pass that as "
                f"public_base_url/-pu instead? The public URL used for IIIF manifest "
                f"links is always supplied separately and never derived from output_dir."
            )
        if server_root:
            if self.is_url_like(server_root):
                raise ValueError(
                    f"server_root ('{server_root}') looks like a URL, not a local "
                    f"filesystem path. It must be the local directory that corresponds "
                    f"to the web server's document root; the public URL is supplied "
                    f"separately via public_base_url/-pu."
                )
            try:
                Path(output_dir).resolve().relative_to(Path(server_root).resolve())
            except ValueError:
                raise ValueError(
                    f"output_dir ('{output_dir}') is not located within "
                    f"server_root ('{server_root}') - IIIF manifest URLs can't be "
                    f"expressed relative to a server root that doesn't contain the "
                    f"output directory."
                )

        if rights and not self.is_url_like(rights):
            self.logger.warning(
                f"[!] rights ('{rights}') doesn't look like a URI (IIIF requires a "
                f"Creative Commons or RightsStatements.org URI) - ignoring it."
            )
            rights = None
        if provider_id and not self.is_url_like(provider_id):
            self.logger.warning(
                f"[!] provider_id ('{provider_id}') doesn't look like a URI - ignoring it."
            )
            provider_id = None
        if ocr_engine == "paddleocr" and ocr_language not in TESSERACT_TO_PADDLE_LANG:
            self.logger.warning(
                f"[!] ocr_language ('{ocr_language}') has no paddleocr equivalent. "
                f"Languages supported for ocr_engine='paddleocr': "
                f"{', '.join(sorted(TESSERACT_TO_PADDLE_LANG))}. Falling back to 'eng'."
            )
            ocr_language = "eng"

        self.pdf_path = pdfPath
        self.output_dir = output_dir
        self.server_root = server_root
        self.rights = rights
        self.provider_id = provider_id
        self.provider_name = provider_name
        self.attribution = attribution
        self.parserTool = ParserTool()
        self.total_pgs = 0
        self.all_pgs = {}
        self.pdf_type = pdf_type  # Store pdf_type for later use
        self.has_doc_end = has_doc_end
        self.is_amendment_pdf = is_amendment_pdf
        self.has_side_notes = has_side_notes
        self.amendment = Amendment()
        self.section_state = SectionState()
        self.article_state = SectionState()
        self.title_state = SectionState()
        self.is_preamble_reached = False
        self.section_shorttitle_notend_status = False
        self.is_footnote_continuation = is_footnote_continuation
        self.fontmapper = DynamicFontMapper(self.pdf_path, out_dir=self.output_dir)
        self.unique_images = {}
        self.all_footnote_text = {}
        self.html_builder = None
        self.min_img_pixels = min_img_pixels
        self.ocr_language = ocr_language
        self.ocr_engine = ocr_engine
        self.is_scanned_copy = is_scanned_copy
        self.table_extract = table_extract
        self.figure_text = figure_text
        self.public_base_url = public_base_url
        # Public URL of the IIIF manifest (egazette only), set by write_manifest() once
        self.manifest_url = None
        self.header_classifier = HeaderRowClassifier.default()
        self.region_merge_classifier = RegionMergeClassifier.default()
        self.continuation_classifier = ContinuationClassifier.default()
        # Column template carried between pages so a borderless table that runs off the
        # bottom of one page can be picked up at the top of the next (set per page in the
        # processing loops, reset here so it never leaks across documents/runs).
        self.pending_continuation = None
        # Legacy (non unicode) indic font handling, see convert_indic_fonts()
        self.font_conv = self.get_font_conv()
        # mappings given by the caller come first, so that a font can be pointed at
        # a converter its name does not name, or at a different one than it does
        self.font_conv_map_res = self.get_font_conv_map_res(font_conv_map)
        self.indic_font_res = self.font_conv_map_res + self.get_indic_font_res()
        # pdf font name -> font key in FontConv.converters (None if not a legacy font)
        self.indic_font_keys = {}
        # (font key, text drawn in that font) -> converted unicode text
        self.indic_text_cache = {}
        # The fonts none of the above can place are identified from the text they
        # draw instead, with the model of machinelearning/, see detect_unknown_fonts()
        self.font_detect = font_detect
        self.font_model = font_model or FONT_MODEL_PATH
        # loaded when a document first has a font that needs it: None means not
        # tried yet, False means tried and failed (so it is reported just once)
        self.font_classifier = None
        # self.fontmapper.extract_fonts()

    # --- func to get the indic2unicode font convertor, None if unavailable ---
    def get_font_conv(self):
        if not INDIC2UNICODE_AVAILABLE:
            self.logger.info(
                "indic2unicode is not installed, conversion of text in legacy "
                "indic fonts is disabled"
            )
            return None

        try:
            return FontConv()
        except Exception as e:
            self.logger.warning(
                "Could not initialize indic2unicode FontConv, conversion of "
                "text in legacy indic fonts is disabled: %s", e
            )
            return None

    # --- func to get the regexps for the font mappings the caller supplied ---
    def get_font_conv_map_res(self, font_conv_map):
        """Turn -fc/--font-conv 'FONT=CONVERTER' mappings into matching regexps.

        Legacy indic fonts are usually recognisable by name, but a subsetted pdf
        can name one anything at all (a gazette in the chanakya encoding whose
        font is called 'TT572t00', with no ToUnicode map and latin glyph names,
        is indistinguishable from a genuine latin font), so the caller gets to
        say which converter such a font needs.
        """
        if not font_conv_map:
            return []

        if isinstance(font_conv_map, str):
            font_conv_map = [font_conv_map]

        if self.font_conv is None:
            self.logger.warning(
                "[!] indic2unicode is not installed, ignoring the font conversion "
                "mapping(s): %s", ', '.join(font_conv_map)
            )
            return []

        supported = sorted(self.font_conv.converters)
        font_res = []

        for mapping in font_conv_map:
            # a single option can carry several comma separated mappings
            for entry in mapping.split(','):
                entry = entry.strip()

                if not entry:
                    continue

                font_name, separator, font_key = entry.partition('=')
                font_name = font_name.strip()
                font_key = font_key.strip().lower()

                if not separator or not font_name or not font_key:
                    raise ValueError(
                        f"font conversion mapping '{entry}' is not of the form "
                        f"FONT=CONVERTER (e.g. TT572t00=chanakya)"
                    )

                if font_key not in self.font_conv.converters:
                    raise ValueError(
                        f"font conversion mapping '{entry}' asks for the converter "
                        f"'{font_key}', which does not exist. The supported ones are: "
                        f"{', '.join(supported)}"
                    )

                self.logger.info(
                    "Text in font %s will be converted to unicode using %s, as asked for",
                    font_name, font_key
                )

                font_res.append(
                    (re.compile('.*%s.*' % re.escape(font_name), re.IGNORECASE), font_key)
                )

        return font_res

    # --- func to repair the broken ToUnicode maps a pdf's fonts may carry ---
    def repair_tounicode(self):
        """Rewrites the broken ToUnicode maps of the pdf into a repaired copy.

        A gazette set in Arial Unicode MS or in Nirmala UI carries a ToUnicode
        map that was built by pairing the glyphs of a run with the characters
        of that run one by one, which devanagari shaping makes slip: निर्माण
        is extracted as जिमावण. The glyphs are drawn correctly, so only the
        extraction is wrong and the map can be built again from what the font
        says about its own glyphs - the characters its cmap draws, the names
        it keeps for them and the rules its GSUB shapes them with - see
        indic2unicode/tools/fix_tounicode.py.

        The repair is done before anything reads the pdf, and only for the
        fonts that are known to carry a broken map, so pdfminer and camelot
        both get the characters that are really there. What they get is still
        in the order in which the glyphs are drawn, so the text of a repaired
        font is pointed at the converter that only reorders it rather than at
        the one named after the font, which is for an unrepaired pdf.
        """
        if not TOUNICODE_FIX_AVAILABLE:
            self.logger.debug(
                "indic2unicode's ToUnicodeFixer is not available, the ToUnicode "
                "maps of %s are left as they are", self.pdf_path
            )
            return

        if not os.path.exists(self.pdf_path) or not self.is_pdf_file(self.pdf_path):
            return

        try:
            fixer = ToUnicodeFixer()
            doc = pymupdf.open(self.pdf_path)
            num = fixer.fix_document(doc)

            if not num:
                doc.close()
                return

            # the repaired copy keeps the name of the document, everything
            # that is written out is named after it
            fixed_dir = os.path.join(self.get_path_cache_pdf(), 'tounicode')
            os.makedirs(fixed_dir, exist_ok = True)
            fixed_path = os.path.join(fixed_dir, os.path.basename(self.pdf_path))
            doc.save(fixed_path)
            doc.close()
        except Exception as e:
            self.logger.warning(
                "[!] Could not repair the ToUnicode maps of %s, its text is "
                "extracted as it is: %s", self.pdf_path, e
            )
            return

        self.logger.info(
            "Repaired %d glyphs of the ToUnicode maps of the font(s) %s, "
            "parsing %s instead", num, ', '.join(sorted(fixer.fixed_fonts)),
            fixed_path
        )

        self.pdf_path = fixed_path
        # the fonts of the repaired pdf are read from the repaired copy too
        if self.fontmapper is not None:
            self.fontmapper.pdf_path = fixed_path

        self.indic_font_res = self.font_conv_map_res + \
                              self.get_repaired_font_res(fixer.fixed_fonts) + \
                              self.get_indic_font_res()
        # a font may already have been looked up while the map was broken
        self.indic_font_keys = {}
        self.indic_text_cache = {}

    # --- func to get the regexps for the fonts whose map was repaired ---
    def get_repaired_font_res(self, fixed_fonts):
        if self.font_conv is None:
            return []

        font_res = []

        for font_name in sorted(fixed_fonts):
            # the name is the one the pdf carries, which is not always the
            # spelling the converter is listed under: Arial Unicode MS is
            # embedded as ArialUnicodeMS too and Nirmala UI carries its bold
            # as a font of its own named Nirmala UI,Bold, so the lookup is by
            # a spelling that the separators, the style and the case do not
            # change
            font_key = get_font_converter(font_name)

            if font_key is None or font_key not in self.font_conv.converters:
                self.logger.warning(
                    "[!] The ToUnicode map of %s was repaired but there is no "
                    "converter to put its text in the order unicode wants, so "
                    "it stays in the order the glyphs are drawn in", font_name
                )
                continue

            self.logger.info(
                "Text in the repaired font %s will be reordered using %s",
                font_name, font_key
            )
            font_res.append(
                (re.compile('.*%s.*' % re.escape(font_name), re.IGNORECASE), font_key)
            )

        return font_res

    # --- func to get the regexps that match a pdf font to a legacy indic font ---
    def get_indic_font_res(self):
        if self.font_conv is None:
            return []

        font_res = []

        for font_key in self.font_conv.converters:
            patterns = [re.escape(font_key)]
            # a font whose pdf name is not its converter's name (Kruti Dev)
            patterns.extend(INDIC_FONT_NAME_ALIASES.get(font_key, []))

            font_res.extend(
                (re.compile('.*%s.*' % pattern, re.IGNORECASE), font_key)
                for pattern in patterns
            )

        return font_res

    # --- func to get the legacy indic font a pdf font name corresponds to ---
    def get_indic_font_key(self, font_name):
        if font_name in self.indic_font_keys:
            return self.indic_font_keys[font_name]

        font_key = None

        for font_re, key in self.indic_font_res:
            if font_re.search(font_name):
                font_key = key
                break

        self.indic_font_keys[font_name] = font_key

        if font_key:
            self.logger.info(
                "Text in font %s will be converted to unicode using %s",
                font_name, font_key
            )

        return font_key

    # --- func to load the classifier that says what a font is drawing ---
    def get_font_classifier(self):
        """The trained model, loaded the first time a document needs it.

        Orange takes a second to import and the model only matters for a pdf
        that has a font nothing else can place, so neither is paid for until
        one turns up.
        """
        if self.font_classifier is not None:
            # False is a load that has already failed and been reported, which
            # is not worth trying again
            return self.font_classifier or None

        try:
            from machinelearning.predict import FontClassifier

            self.font_classifier = FontClassifier(str(self.font_model))
        except Exception as e:
            self.logger.warning(
                "[!] Could not load the font detection model %s, the fonts whose "
                "name does not say what they are drawing are left as they are: %s",
                self.font_model, e
            )
            self.font_classifier = False
            return None

        self.logger.info("Loaded the font detection model %s", self.font_model)

        return self.font_classifier

    # --- func to point the fonts that name no encoding at the right decoder ---
    def detect_unknown_fonts(self, pages):
        """Identifies the legacy indic fonts that nothing else can identify.

        A font's name usually says which encoding its text is in, and when it
        does not the caller can say so with -fc/--font-conv. Neither is any
        help for a subsetted gazette that names its fonts TT572t00 and TT447t00
        and carries no ToUnicode map: nothing about such a font says whether it
        draws latin or devanagari, which is the very thing that has to be known
        before its text can be read.

        What does say it is the text itself - chanakya extracts as latin
        gibberish with a vocabulary of its own ('fnYyh', 'ds', 'ls') - so every
        font left unplaced is classified by the model machinelearning/ trains
        on that text (see machinelearning/README.md). A font the model calls
        not_required is drawing unicode already and is taken as it is; any
        other class is registered like a -fc mapping would be, so that the
        conversion which follows picks it up without knowing where the answer
        came from.

        This runs on the parsed xml before convert_indic_fonts(), i.e. on the
        text as the pdf drew it, since converted text is exactly what the model
        is not trained on.
        """
        if not self.font_detect or self.font_conv is None or not pages:
            return

        if not os.path.exists(self.font_model):
            self.logger.debug(
                "There is no font detection model at %s, the fonts whose name "
                "does not say what they are drawing are left as they are",
                self.font_model
            )
            return

        try:
            font_texts = self.get_unknown_font_texts(pages)
        except Exception as e:
            self.logger.warning(
                "[!] Could not collect the text of the unidentified fonts of %s, "
                "they are left as they are: %s", self.pdf_path, e
            )
            return

        if not font_texts:
            return

        classifier = self.get_font_classifier()

        if classifier is None:
            return

        font_names = sorted(font_texts)

        try:
            # every font of the document in one go, the model featurizes a
            # batch as cheaply as it does a single text
            results = classifier.classify_all([font_texts[n] for n in font_names])
        except Exception as e:
            self.logger.warning(
                "[!] Could not detect what the unidentified fonts of %s are "
                "drawing, they are left as they are: %s", self.pdf_path, e
            )
            return

        font_res = []

        for font_name, (label, probability) in zip(font_names, results):
            font_key = self.get_detected_font_key(
                font_name, label, probability, font_texts[font_name]
            )

            if font_key:
                font_res.append(
                    (re.compile('.*%s.*' % re.escape(font_name), re.IGNORECASE), font_key)
                )

        if not font_res:
            return

        # a detected font is one that matches nothing else, so where these go
        # decides nothing; they go first for the same reason -fc's do, that an
        # answer about this document beats a general rule about names
        self.indic_font_res = font_res + self.indic_font_res
        # collecting the text looked every font of the document up, so the ones
        # that are placed now were cached as unplaced then; the ones that were
        # already placed keep their answer, which has not changed
        self.indic_font_keys = {
            font_name: font_key
            for font_name, font_key in self.indic_font_keys.items() if font_key
        }

    # --- func to get the text drawn in each font that nothing else identifies ---
    def get_unknown_font_texts(self, pages):
        """{font name: the text the pdf draws in it}, for the unplaced fonts.

        A font is unplaced here when neither its name, nor a -fc mapping, nor a
        ToUnicode repair has said what its text is - which is exactly the case
        the classifier is for. The fonts that are already placed are left out:
        their answer is known and a worse one must not overrule it. So are the
        standard latin faces of FONT_DETECT_SKIP_RE, for the same reason read
        the other way round: their name places them as needing no decoder at
        all, and the model cannot improve on that, only get it wrong. Arial
        Unicode MS is named like one of them but is not one of them
        (FONT_DETECT_SKIP_EXCEPT_RE), so it is classified like anything else.

        The text of a font is collected as the runs of consecutive chars drawn
        in it joined with a space, and not as one string of every char it draws:
        the spaces between words are <text> elements of their own carrying no
        font at all, so concatenating a font's own chars would run its words
        into each other and hand the model text split into words quite
        differently from the corpus it was trained on.
        """
        font_runs = defaultdict(list)

        for page in pages:
            for element in page.iter():
                # a <text> element has no children, so every one of them sits
                # under exactly one parent and is read exactly once
                run = []
                run_font = None

                for child in element:
                    if child.tag != 'text':
                        continue

                    font_name = child.attrib.get('font')

                    if font_name != run_font:
                        if run:
                            font_runs[run_font].append(''.join(run))
                        run = []
                        run_font = font_name

                    if font_name:
                        run.append(child.text or '')

                if run:
                    font_runs[run_font].append(''.join(run))

        font_texts = {}

        for font_name, runs in font_runs.items():
            if self.get_indic_font_key(font_name):
                continue

            if FONT_DETECT_SKIP_RE.search(font_name) and \
                    not FONT_DETECT_SKIP_EXCEPT_RE.search(font_name):
                self.logger.debug(
                    "Font %s is one of the standard latin faces, whose text needs "
                    "no decoder, so it is not classified at all", font_name
                )
                continue

            words = ' '.join(runs).split()

            if len(words) < FONT_DETECT_MIN_WORDS:
                self.logger.debug(
                    "Font %s draws %d word(s), too few to tell what it is, so its "
                    "text is left as it is", font_name, len(words)
                )
                continue

            font_texts[font_name] = ' '.join(words[:FONT_DETECT_MAX_WORDS])

        return font_texts

    # --- func to get the converter the model's answer about a font means ---
    def get_detected_font_key(self, font_name, label, probability, text):
        if probability < FONT_DETECT_MIN_PROB:
            self.logger.info(
                "The text in font %s looks like %s but only with a probability of "
                "%.2f, which is too little to act on, so it is left as it is",
                font_name, label, probability
            )
            return None

        if label == FONT_CLASS_NOT_REQUIRED:
            self.logger.info(
                "Text in font %s needs no decoder (%.2f), it is taken as it is",
                font_name, probability
            )
            return None

        if label in FONT_CLASSES_WITHOUT_CONVERTER:
            self.logger.info(
                "Text in font %s was detected as %s (%.2f), which has no converter "
                "of its own, so it is taken as it is", font_name, label, probability
            )
            return None

        # a class not named as drawing indic text is a legacy 8-bit encoding,
        # whose text extracts as latin. Text that is already in an indic script
        # cannot have come out of one, whatever the model says, and running it
        # through that decoder would turn readable text into rubbish - which is
        # the one outcome worth guarding against, since leaving a font alone
        # only costs the improvement detection was there to make
        if label not in FONT_CLASSES_INDIC_TEXT:
            indic_ratio = self.get_indic_char_ratio(text)

            if indic_ratio > FONT_DETECT_MAX_INDIC_RATIO:
                self.logger.warning(
                    "[!] Text in font %s was detected as %s (%.2f), but %.0f%% of it "
                    "is already in an indic script and %s decodes latin, so the "
                    "detection is wrong and the text is left as it is",
                    font_name, label, probability, indic_ratio * 100, label
                )
                return None

        font_key = FONT_CLASS_CONVERTERS.get(label, label)

        if font_key not in self.font_conv.converters:
            self.logger.warning(
                "[!] Text in font %s was detected as %s, which no indic2unicode "
                "converter goes by, so it is left as it is. The model was trained "
                "on a class this build of indic2unicode does not have a decoder for",
                font_name, label
            )
            return None

        self.logger.info(
            "Text in font %s will be converted to unicode using %s, detected from "
            "the text it draws with a probability of %.2f",
            font_name, font_key, probability
        )

        return font_key

    # --- func to get the share of a text already drawn in an indic script ---
    def get_indic_char_ratio(self, text):
        """How much of text is in an indic script, ignoring whitespace.

        Whitespace is left out because it belongs to no script and a text's
        share of it says nothing about what drew it: the same words with the
        runs joined differently would otherwise score differently.
        """
        chars = ''.join(text.split())

        if not chars:
            return 0.0

        return len(INDIC_SCRIPT_RE.findall(chars)) / len(chars)

    # --- func to convert text drawn in a legacy indic font into unicode ---
    def indic_to_unicode(self, font_key, text):
        cache_key = (font_key, text)

        if cache_key in self.indic_text_cache:
            return self.indic_text_cache[cache_key]

        try:
            converted = self.font_conv.to_unicode(font_key, text)
        except Exception as e:
            self.logger.warning(
                "Failed to convert text [%s] in font %s to unicode: %s",
                text, font_key, e
            )
            converted = text

        if not isinstance(converted, str):
            converted = text

        self.indic_text_cache[cache_key] = converted

        return converted

    def drop_overlapping_spaces(self, pages):
        """Removes spaces that the glyph next to them is drawn on top of.

        A pdf can paint a space that never advances the pen, the following
        glyph then being drawn back over it. That happens on devanagari text
        in particular, where a conjunct is two glyphs and the space lands
        between them. pdfminer has no way of telling such a space from a real
        one and reports it as an ordinary space, which splits one word into
        two everywhere downstream ('िभन् न' for 'िभन्न'). They are recognised
        here by their coordinates - the space sits inside its neighbour's
        bbox instead of beside it - and dropped from the xml before the indic
        font conversion (which would otherwise convert a broken up conjunct)
        and before any layout analysis, so that all the downstream consumers
        of the xml see the text without knowing about overprinted spaces.
        """
        if not pages:
            return

        for page in pages:
            try:
                self.drop_overlapping_spaces_in_page(page)
            except Exception as e:
                self.logger.error(
                    "Failed removal of overprinted spaces on page %s: %s",
                    page.get("id"), e
                )

    def drop_overlapping_spaces_in_page(self, page):
        for element in page.iter():
            # a <text> element never has children of its own, so every one of
            # them is grouped under exactly one parent and looked at just once
            chars = [child for child in element if child.tag == 'text']

            for char in self.get_overprinted_spaces(chars):
                element.remove(char)

    # --- func to pick out the spaces drawn over by a neighbouring glyph ---
    def get_overprinted_spaces(self, chars):
        overprinted = []

        for idx, char in enumerate(chars):
            if not self.is_space_char(char):
                continue

            space = self.get_char_x_range(char)

            # the newlines pdfminer inserts itself carry no bbox, and a space
            # of no width cannot be overlapped by anything in the first place
            if space is None:
                continue

            # the nearest real glyph on either side, not simply the adjacent
            # elements: a combining matra is emitted with a zero width bbox
            # sitting on its base consonant and says nothing about spacing
            previous = self.get_neighbour_x_range(chars, range(idx - 1, -1, -1))
            following = self.get_neighbour_x_range(chars, range(idx + 1, len(chars)))

            if self.is_overprinted_space(space, previous, following):
                overprinted.append(char)

        return overprinted

    # --- func to tell a space painted over by its neighbour from a real one ---
    def is_overprinted_space(self, space, previous, following):
        if previous and following:
            drawn_over = self.get_x_overlap(previous, following)

            narrowest = min(
                previous[1] - previous[0],
                following[1] - following[0]
            )

            if drawn_over > NEIGHBOUR_OVERLAP_RATIO * narrowest:
                return False

        overlap = max(
            self.get_x_overlap(space, previous),
            self.get_x_overlap(space, following)
        )

        return overlap > SPACE_OVERLAP_RATIO * (space[1] - space[0])

    # --- func to walk to the nearest glyph a space can be compared against ---
    def get_neighbour_x_range(self, chars, indices):
        for idx in indices:
            char = chars[idx]

            if self.is_space_char(char):
                continue

            x_range = self.get_char_x_range(char)

            if x_range is not None:
                return x_range

        return None

    @staticmethod
    def is_space_char(char):
        return bool(char.text) and not char.text.strip()

    @staticmethod
    def get_char_x_range(char):
        bbox = char.attrib.get("bbox")

        if not bbox:
            return None

        try:
            x0, _, x1, _ = map(float, bbox.split(","))
        except (ValueError, TypeError):
            return None

        return (x0, x1) if x1 > x0 else None

    @staticmethod
    def get_x_overlap(x_range, other):
        if x_range is None or other is None:
            return 0.0

        return max(0.0, min(x_range[1], other[1]) - max(x_range[0], other[0]))

    def convert_indic_fonts(self, pages):
        """Rewrites text drawn in legacy indic fonts into unicode, in place.

        Legacy fonts like Chanakya/Kruti Dev (the Gazette of India) or
        Aryan2/Divya/Surekh (LokSabha) overload ascii codepoints with
        devanagari glyphs, so what pdfminer extracts for them is not readable
        text at all. Every <text> element whose 'font' attribute matches one of
        the fonts supported by indic2unicode is replaced with its unicode
        equivalent here, i.e. before any layout analysis runs, so that all the
        downstream consumers of the xml (Page, TextBox, HTMLBuilder,
        TableExtraction, ...) see unicode without knowing about fonts.
        """
        if self.font_conv is None or not pages:
            return

        for page in pages:
            try:
                self.convert_indic_fonts_in_page(page)
            except Exception as e:
                self.logger.error(
                    "Failed conversion of legacy indic fonts on page %s: %s",
                    page.get("id"), e
                )

    def convert_indic_fonts_in_page(self, page):
        for element in page.iter():
            # a <text> element never has children of its own, so every one of
            # them is grouped under exactly one parent and converted just once
            texts = [child for child in element if child.tag == 'text']

            if texts:
                self.convert_indic_font_runs(texts, XML_TEXT_ACCESSORS)

    # --- func to convert chars sitting side by side, one font run at a time ---
    def convert_indic_font_runs(self, chars, accessors):
        # conversion is contextual - matras get reordered and glyph pairs get
        # composed - so it is applied to the longest run of consecutive chars
        # sharing the same font instead of one char at a time
        get_font = accessors[0]

        run = []
        run_font_key = None

        for char in chars:
            # chars without a font (the spaces and newlines pdfminer inserts
            # itself) end the current run
            font_name = get_font(char)

            font_key = self.get_indic_font_key(font_name) if font_name else None

            if font_key != run_font_key:
                self.convert_indic_font_run(run, run_font_key, accessors)
                run = []
                run_font_key = font_key

            if font_key:
                run.append(char)

        self.convert_indic_font_run(run, run_font_key, accessors)

    # --- func to convert one run of chars sharing the same legacy indic font ---
    def convert_indic_font_run(self, run, font_key, accessors):
        _, get_text, set_text = accessors

        if not run or not font_key:
            return

        original = ''.join(get_text(char) or '' for char in run)

        if not original.strip():
            return

        converted = self.indic_to_unicode(font_key, original)

        if converted == original:
            return

        # conversion is not one char in, one char out - a single legacy glyph
        # can become a consonant + halant + matra sequence, and a pair of them
        # can collapse into one char - so the converted text is spread
        # proportionally over the chars it came from. That keeps it in reading
        # order and keeps every char's own coordinates, which the layout code
        # (first/last char coords, per char gaps within a textline) relies on.
        no_of_chars = len(run)
        length = len(converted)

        for idx, char in enumerate(run):
            start = (idx * length) // no_of_chars
            end = ((idx + 1) * length) // no_of_chars

            set_text(char, converted[start:end])

    @contextmanager
    def camelot_font_conversion(self):
        """Make camelot's own text extraction see the same unicode the xml does.

        camelot re-reads the pdf itself - it has to, the lattice flavour finds
        cells by looking for lines in a rendered image of the page - so the
        conversion done on the xml never reaches the text in a table's cells.
        It does build the same kind of layout though, and its chars carry a
        font name and a text that can be set, so the conversion is applied
        there too, at the single function every one of its layouts comes from.
        """
        if self.font_conv is None or not self.indic_font_res:
            yield
            return

        if not CAMELOT_LAYOUT_AVAILABLE:
            self.logger.warning(
                "[!] camelot has no get_page_layout() to convert legacy indic "
                "fonts in, text extracted from bordered tables will stay in the "
                "font's own encoding"
            )
            yield
            return

        original_get_page_layout = camelot_handlers.get_page_layout

        def get_page_layout(page, **kwargs):
            layout, dimensions = original_get_page_layout(page, **kwargs)

            try:
                self.convert_indic_fonts_in_layout(layout)
            except Exception as e:
                self.logger.error(
                    "Failed conversion of legacy indic fonts in a camelot layout: %s", e
                )

            return layout, dimensions

        camelot_handlers.get_page_layout = get_page_layout

        try:
            yield
        finally:
            camelot_handlers.get_page_layout = original_get_page_layout

    def convert_indic_fonts_in_layout(self, layout):
        images, chars, horizontal_text, vertical_text = \
            get_image_char_and_text_objects(layout)

        # one textline at a time and never across two of them: a run spanning
        # two cells would move text from one into the other once its converted
        # form is spread back over the chars it came from
        for textline in list(horizontal_text) + list(vertical_text):
            self.convert_indic_font_runs(list(textline), LT_CHAR_ACCESSORS)

    def get_all_footnote_text(self):

        FOOTNOTE_START_RE = re.compile(
            r'^\{\{\^\{\{FOOTNOTE\s*(.+?)\}\}\}\}'
        )

        active_footnote_num = None
        active_footnote_page = None

        for pg_num in sorted(self.all_pgs.keys()):

            page = self.all_pgs[pg_num]

            page_footnote_text = self.all_footnote_text.setdefault(pg_num, {})

            for tb in page.all_tbs.keys():

                if page.all_tbs[tb] != 'footnote':
                    continue

                for textline in tb.tbox.findall(".//textline"):

                    line_parts = []

                    pending_superscript = []

                    for text in textline.findall(".//text"):

                        raw = text.text or ""

                        if not raw:
                            continue

                        is_super = False

                        if "bbox" in text.attrib:

                            try:

                                bbox = tuple(
                                    map(
                                        float,
                                        text.attrib["bbox"].split(",")
                                    )
                                )

                                if bbox in tb.footnotes_superscript:

                                    pending_superscript.append(
                                        tb.footnotes_superscript[bbox]
                                    )

                                    is_super = True

                            except Exception:
                                pass

                        if not is_super:

                            if pending_superscript:

                                marker = "".join(
                                    pending_superscript
                                )

                                line_parts.append(
                                    "{{^{{FOOTNOTE "
                                    + marker +
                                    "}}}}"
                                )

                                pending_superscript = []

                            line_parts.append(raw)

                    if pending_superscript:

                        marker = "".join(
                            pending_superscript
                        )

                        line_parts.append(
                            "{{^{{FOOTNOTE "
                            + marker +
                            "}}}}"
                        )

                    text = "".join(line_parts)

                    text = re.sub(
                        r'\s+',
                        ' ',
                        text
                    ).strip()

                    if not text:
                        continue

                    start_match = FOOTNOTE_START_RE.match(text)

                    if start_match:

                        footnote_num = (
                            start_match.group(1).strip()
                        )

                        active_footnote_num = footnote_num
                        active_footnote_page = pg_num

                        cleaned_text = FOOTNOTE_START_RE.sub(
                            '',
                            text,
                            count=1
                        ).strip()

                        cleaned_text = re.sub(
                            r'^[.\):\]]\s*',
                            '',
                            cleaned_text
                        )

                        if (
                            footnote_num
                            not in page_footnote_text
                        ):

                            page_footnote_text[
                                footnote_num
                            ] = cleaned_text

                        else:

                            page_footnote_text[
                                footnote_num
                            ] += "\n" + cleaned_text

                    else:

                        if not active_footnote_num or active_footnote_page is None:
                            continue

                        self.all_footnote_text[
                            active_footnote_page
                        ][
                            active_footnote_num
                        ] += "\n" + text

            if not self.is_footnote_continuation:

                active_footnote_num = None
                active_footnote_page = None

    def finalize_unique_images(self):

        remove_hashes = []

        for img_hash, meta in self.unique_images.items():

            if meta.get("count", 0) > 1:

                img_path = meta.get("path")


                if img_path and os.path.exists(img_path):

                    try:
                        os.remove(img_path)

                        self.logger.info(
                            f"Deleted duplicate image: {img_path}"
                        )

                        self.remove_empty_parent_dir(img_path)
                    
                    except Exception as e:

                        self.logger.warning(
                            f"Failed deleting image "
                            f"{img_path}: {e}"
                        )


                for pg_num in meta.get("pages", set()):
                    pg_num = int(pg_num)
                    page_obj = self.all_pgs.get(pg_num)
                    if page_obj and hasattr(page_obj, "figures"):
                        try:
                            page_obj.figures.remove_hash(img_hash)

                        except Exception as e:

                            self.logger.warning(
                                f"Failed removing image hash "
                                f"{img_hash} from page {pg_num}: {e}"
                            )


                remove_hashes.append(img_hash)


        for img_hash in remove_hashes:

            del self.unique_images[img_hash]

        self.logger.info(
            f"Remaining unique images: "
            f"{len(self.unique_images)}"
        )
    
    def remove_empty_parent_dir(self, file_path):
        try:
            current = os.path.dirname(file_path)
            while current and os.path.isdir(current) and os.path.basename(current) != "images":
                os.rmdir(current)

                self.logger.debug(
                    f"Removed empty image directory: {current}"
                )

                current = os.path.dirname(current)

        except OSError:
            pass

        except Exception:
            self.logger.exception(
                f"Failed removing directory for: {file_path}"
            )
    
    def get_htmlBuilder(self, pdf_type, docend_symbol = False):
        if pdf_type == 'sebi':
            sentence_completion_punctutation = ("'.",'".',".'", '."', "';", ";'", ';"','";') #( ".", ":", "?",  ".'", '."', ";", ";'", ';"')
            return HTMLBuilder(self.unique_images, self.all_footnote_text, sentence_completion_punctutation, pdf_type)
            # return JudgmentBuilder(self.unique_images, self.all_footnote_text, sentence_completion_punctutation, pdf_type)
        elif pdf_type in set(['acts']):
            sentence_completion_punctutation = ('.', ';', ':', '—', ':—', '; or',\
                                                ': or', '; and', ': and', ':––', ';––',\
                                                '––', '."', '.\'', ';"', ';\'' , \
                                                '.”', '.’', ';”' , ';’', ':-')
            return Acts(self.all_footnote_text, sentence_completion_punctutation, pdf_type, docend_symbol)
        elif pdf_type in set(['sebi_circulars']):
            sentence_completion_punctutation = ('.', ';', ':', '—', ':—', '; or',\
                                                ': or', '; and', ': and', ':––', ';––',\
                                                '––', '."', '.\'', ';"', ';\'' , \
                                                '.”', '.’', ';”' , ';’', ':-', '.]',
                                                ',-', ':-', ';-', '--')
            return SebiCirculars(self.unique_images, self.all_footnote_text, sentence_completion_punctutation, pdf_type, docend_symbol)

        elif pdf_type == 'judgments':
            sentence_completion_punctutation = ("'.",'".',".'", '."', "';", ";'", ';"','";')
            return JudgmentBuilder(self.unique_images, self.all_footnote_text, sentence_completion_punctutation, pdf_type)
        else:
            sentence_completion_punctutation = ('.', ':')
            return HTMLBuilder(self.unique_images, self.all_footnote_text, sentence_completion_punctutation, pdf_type)
            # return JudgmentBuilder(self.unique_images, self.all_footnote_text, sentence_completion_punctutation, pdf_type)
        
    # --- func to build HTML after text classification ---
    def buildHTML(self, start_page, end_page): #, section_page_end):
        if not self.html_builder:
            return
        if not self.all_pgs:
            self.html_builder.build(start_page, end_page)
            html_content = self.html_builder.get_html()
            self.write_html(html_content, start_page, end_page)
            return
        
        for page in self.all_pgs.values():
            self.logger.info(f"HTML build starts for page num-{page.pg_num}")
            self.html_builder.build(page, self.has_side_notes) #, section_page_end)
        
        self.logger.debug("Fetching Full HTML content")
        if self.pdf_type not in set(['acts', 'sebi_circulars']):
            html_content = self.html_builder.get_html()
            self.write_html(html_content, start_page, end_page)
        else:
            content = self.html_builder.get_content()
            self.write_bluebell(content, start_page, end_page)

    # --- classify the page texboxes sidenotes, section, para, titles(headings) ---
    def process_pages_acts(self, pdf_type):
        for page in self.all_pgs.values():
            self.logger.info(f"Processing page num-{page.pg_num}")
            # page.print_tbs()
            page.get_width_ofTB_moreThan_Half_of_pg()
            page.get_body_width_by_binning()
            # page.is_single_column_page = page.is_single_column_page()
            page.find_sidenote_leftend_rightstart_coords()
            page.get_side_notes() #self.section_start_page,self.section_end_page)
            # page.is_single_column_page = page.is_single_column_page_kmeans_elbow()
            # print(page.is_single_column_page)
            if self.is_amendment_pdf:
                self.amendment.check_for_amendment_acts(page)#,self.section_start_page,self.section_end_page)
            page.get_article(self.article_state, self)
            page.get_section_para(self.section_state, self)#, self.section_start_page,self.section_end_page)
            page.get_titles(pdf_type)
            page.sort_all_boxes()
            page.print_all()
            # page.print_headers()
            # page.print_footers()

    def process_pages_sebi_circulars(self, pdf_type):
        prev_sent_end_status = True
        sentence_completion_punctutation = ('.', ';', ':', '—', ':—', '; or',\
                                                ': or', '; and', ': and', ':––', ';––',\
                                                '––', '."', '.\'', ';"', ';\'' , \
                                                '.”', '.’', ';”' , ';’', ':-', '.]',
                                                ',-', ':-', ';-', '--')

        for page in self.all_pgs.values():
            self.logger.info(f"Processing page num-{page.pg_num}")
            page.get_width_ofTB_moreThan_Half_of_pg()
            page.get_body_width_by_binning()
            # page.is_single_column_page = page.is_single_column_page()
            # page.is_single_column_page = page.is_single_column_page_kmeans_elbow()
            # print(page.is_single_column_page)
            page.get_bulletins_sebi_circulars(self.section_state)
            page.get_titles(pdf_type)
            prev_sent_end_status = page.get_title_hierarchy(self.title_state, prev_sent_end_status, sentence_completion_punctutation)   
            page.sort_all_boxes()
            # page.print_blockquote()
            # page.print_headers()
            # page.print_footers()
            # page.print_levels()
            page.print_all()
            # page.print_tbs()

    def process_pages_sebi(self, pdf_type):
        for page in self.all_pgs.values():
            self.logger.info(f"Processing page num-{page.pg_num}")
            page.get_width_ofTB_moreThan_Half_of_pg()
            page.get_body_width_by_binning()
            # page.is_single_column_page = page.is_single_column_page()
            # page.is_single_column_page = page.is_single_column_page_kmeans_elbow()
            # print(page.is_single_column_page)
            # page.get_italic_blockquotes(pdf_type)
            # self.amendment.check_for_blockquotes(page)
            self.amendment.check_for_blockquotes_judgments(page)
            page.detect_sparse_pre()
            # page.get_titles(pdf_type)
            page.get_bulletins(self.section_state)
            page.get_titles(pdf_type)
            page.sort_all_boxes()
            # page.print_blockquote()
            # page.print_headers()
            # page.print_footers()
            # page.print_levels()
            page.print_all()
            # page.print_tbs()

    def process_pages_judgments(self, pdf_type):
        for page in self.all_pgs.values():
            self.logger.info(f"Processing page num-{page.pg_num}")
            page.get_width_ofTB_moreThan_Half_of_pg()
            page.get_body_width_by_binning()
            # page.is_single_column_page = page.is_single_column_page()
            # page.is_single_column_page = page.is_single_column_page_kmeans_elbow()
            # print(page.is_single_column_page)
            # page.get_italic_blockquotes(pdf_type)
            self.amendment.check_for_blockquotes_judgments(page)
            page.detect_sparse_pre()
            # page.detect_pre()
           
            # page.get_titles(pdf_type)
            # page.get_bulletins(self.section_state)
            page.sort_all_boxes()
            # page.print_headers()
            # page.print_footers()
            page.print_all()
    
    def process_pages(self, pdf_type):
        for page in self.all_pgs.values():
            self.logger.info(f"Processing page num-{page.pg_num}")
            page.get_width_ofTB_moreThan_Half_of_pg()
            page.get_body_width_by_binning()
            # page.is_single_column_page = page.is_single_column_page()
            # page.is_single_column_page = page.is_single_column_page_kmeans_elbow()
            # print(page.is_single_column_page)
            page.get_titles(pdf_type)
            # page.get_bulletins(self.section_state)
            page.sort_all_boxes()
            # page.print_headers()
            # page.print_footers()
            page.print_all()

    def print_labels(self, pdf_type):
        #for page in self.all_pgs.values():
            # page.print_table_content()
            # page.print_headers()
            # page.print_footers()
            # page.print_sidenotes()
            # page.print_titles()
            # page.print_section_para()
            # page.print_all()
            # page.print_amendment()
            # # page.print_tbs()
            # self.bq_layout.print_sections()
        pass

    # --- NEW ADAPTIVE HEADER/FOOTER DETECTION ---
    def get_page_header_footer(self, pages, base_name_of_file, output_dir):
        # Initialize page objects first
        for pg in pages:
            pdf_dir = self.get_path_cache_pdf()
            if not self.pdf_path.lower().endswith(".pdf"):
                base_name = os.path.basename(self.pdf_path) + ".pdf"
                new_pdf_path = os.path.join(pdf_dir, base_name)
                shutil.copy(self.pdf_path, new_pdf_path)
                self.logger.debug(f"Copied input file to cache dir as: {new_pdf_path}")
                self.pdf_path = new_pdf_path

            page = Page(pg, self.pdf_path, base_name_of_file, output_dir,
                        self.pdf_type, self.has_side_notes, self.is_amendment_pdf,
                        self.fontmapper, self.unique_images, self.min_img_pixels,
                        self.ocr_language,
                        self.is_scanned_copy, self.figure_text, self.ocr_engine)
            self.total_pgs += 1
            self.all_pgs[self.total_pgs] = page
            page.process_textboxes()#pg)
            page.get_figures()#pg)
            page.label_table_tbs()

            # page.line_based_header_footer_detection()

        self.logger.info("Starting adaptive header/footer detection...")
        if not self.is_scanned_copy:
            self.adaptive_header_footer_detection(pages, self.pdf_type)

        protected_tbs_by_page = defaultdict(set)
        for group in self.adaptive_headers + self.adaptive_footers:
            for elem in group['elements']:
                protected_tbs_by_page[elem['page_num']].add(elem['textbox'])

        previous_page_footnote_font_size  = None
        seen_footnote = set()
        for page_num, page in self.all_pgs.items():
            protected_tbs = protected_tbs_by_page.get(page_num, set())
            page.mark_standalone_footnote_markers(protected_tbs)
            if self.is_footnote_continuation:
                previous_page_footnote_font_size, seen_footnote = (
                    page.get_footnotes(
                        seen_footnote,
                        previous_page_footnote_font_size,
                        protected_tbs
                    )
                )
            else:
                page.get_footnotes(protected_tbs=protected_tbs)
            page.detect_footnote_blocks_by_style(protected_tbs)

        if not self.is_scanned_copy:
            self.finalize_adaptive_header_footer_detection()

        if self.table_extract and self.pdf_type != 'judgments':
            self.logger.info("Detecting borderless tables...")
            self.pending_continuation = None
            for page in self.all_pgs.values():
                page.reclaim_header_footer_for_continuation(self.pending_continuation)
                self.pending_continuation = page.get_borderless_table(
                    self.pdf_type, self.header_classifier, self.region_merge_classifier,
                    continuation_template=self.pending_continuation,
                    continuation_classifier=self.continuation_classifier,
                )
                page.label_borderless_table_tbs()

        self.logger.info("Detecting multicolumn page layouts...")
        for page in self.all_pgs.values():
            page.detect_multicolumn_layout()
            page.apply_column_reading_order()

        if self.pdf_type in {'judgments'}:
            self.detect_header_pre(pages)
        # elif self.pdf_type in {'sebi'}:
        #     self.detect_sebi_header_pre(pages)

        self.detect_toc(pages)

        self.finalize_unique_images()
        if not self.unique_images:
            self.remove_empty_manifest_dir(base_name_of_file, output_dir)
        self.get_all_footnote_text()
        self.logger.info(self.all_footnote_text)

    def remove_empty_manifest_dir(self, base_name_of_file, output_dir, image_base_dir="manifest"):
        manifest_pdf_dir = os.path.join(output_dir, image_base_dir, base_name_of_file)
        images_dir = os.path.join(manifest_pdf_dir, "images")
        for directory in (images_dir, manifest_pdf_dir):
            try:
                if os.path.isdir(directory) and not os.listdir(directory):
                    os.rmdir(directory)
                    self.logger.debug(f"Removed empty manifest directory: {directory}")
            except OSError as e:
                self.logger.debug(f"Could not remove manifest directory {directory}: {e}")

    def adaptive_header_footer_detection(self, pages, pdf_type=None):
        self.adaptive_headers = []
        self.adaptive_footers = []
        page_elements = []
        
        # Simple working configuration
        if pdf_type not in set(['sebi_circulars']):
            HEADER_ZONE_THRESHOLD = 0.12#0.15    # Top 15% of page height
            FOOTER_ZONE_THRESHOLD = 0.12#0.15    # Bottom 15% of page height
            SIMILARITY_THRESHOLD =  0.8       # 80% similarity
            MIN_OCCURRENCE_RATE =   0.4     # Must appear on at least 40% of pages
            LINE_TOLERANCE = 0.02           # 2% of page height tolerance for same line detection
        
        else:
            HEADER_ZONE_THRESHOLD = 0.12#0.15    # Top 15% of page height
            FOOTER_ZONE_THRESHOLD = 0.12#0.15    # Bottom 15% of page height
            SIMILARITY_THRESHOLD =  0.9       # 80% similarity
            MIN_OCCURRENCE_RATE =   0.6     # Must appear on at least 40% of pages
            LINE_TOLERANCE = 0.02 

        try:
            total_pages = len(pages)
            self.logger.info("Starting adaptive header/footer detection on %d pages", total_pages)
            
            # Special handling for single-page PDFs
            if total_pages == 1:
                self.logger.info("Single-page PDF detected - using strict header/footer detection")
                self._handle_single_page_header_footer_detection(pages, pdf_type, HEADER_ZONE_THRESHOLD, FOOTER_ZONE_THRESHOLD)
                return
            
            # Step 1: Extract all textboxes with normalized coordinates
            for pg_idx, pg in enumerate(pages):
                page_num = pg_idx + 1
                if page_num not in self.all_pgs:
                    continue
                    
                page_obj = self.all_pgs[page_num]
                
                for tb, label in page_obj.all_tbs.items():
                    try:
                        if label is not None:
                            continue
                        text = tb.extract_text_from_tb().strip()
                        if not text or text.isspace():
                            continue
                            
                        # Normalize coordinates as percentages of page dimensions
                        x0_pct = tb.coords[0] / page_obj.pg_width
                        y0_pct = tb.coords[1] / page_obj.pg_height
                        x1_pct = tb.coords[2] / page_obj.pg_width
                        y1_pct = tb.coords[3] / page_obj.pg_height
                        
                        width_pct = x1_pct - x0_pct
                        height_pct = y1_pct - y0_pct
                        
                        # Calculate relative position zones
                        is_header_zone = y0_pct >= (1 - HEADER_ZONE_THRESHOLD)
                        is_footer_zone = y0_pct <= FOOTER_ZONE_THRESHOLD
                        
                        page_elements.append({
                            'page_num': page_num,
                            'text': text,
                            'textbox': tb,
                            'x0_pct': x0_pct,
                            'y0_pct': y0_pct,
                            'x1_pct': x1_pct,
                            'y1_pct': y1_pct,
                            'width_pct': width_pct,
                            'height_pct': height_pct,
                            'is_header_zone': is_header_zone,
                            'is_footer_zone': is_footer_zone,
                            'is_centered': abs(x0_pct + width_pct/2 - 0.5) < 0.1,
                            'is_left_aligned': x0_pct < 0.1,
                            'is_right_aligned': x1_pct > 0.9
                        })
                        
                    except Exception as e:
                        self.logger.warning("Error processing textbox on page %d: %s", page_num, e)
                        continue
            
            if not page_elements:
                self.logger.warning("No valid page elements found for header/footer detection")
                return
                
            # Count elements in zones
            header_zone_count = sum(1 for elem in page_elements if elem['is_header_zone'])
            footer_zone_count = sum(1 for elem in page_elements if elem['is_footer_zone'])
            self.logger.info("Found %d elements in header zones, %d in footer zones", 
                           header_zone_count, footer_zone_count)
            
            # Debug: Show coordinate distribution to understand the issue
            if page_elements:
                y_coords = [elem['y0_pct'] for elem in page_elements]
                min_y = min(y_coords)
                max_y = max(y_coords)
                self.logger.info("Y-coordinate range: %.3f to %.3f", min_y, max_y)
                self.logger.info("Header zone threshold (y >= %.3f), Footer zone threshold (y <= %.3f)", 
                               1 - HEADER_ZONE_THRESHOLD, FOOTER_ZONE_THRESHOLD)
                
                # Show some sample elements with their coordinates
                self.logger.info("Sample elements by Y position:")
                sorted_elements = sorted(page_elements, key=lambda e: e['y0_pct'])
                for i in [0, len(sorted_elements)//2, -1]:
                    if 0 <= i < len(sorted_elements):
                        elem = sorted_elements[i]
                        self.logger.info("  Y=%.3f: '%s' (header_zone=%s, footer_zone=%s)", 
                                       elem['y0_pct'], elem['text'][:40], 
                                       elem['is_header_zone'], elem['is_footer_zone'])
            
            # Step 2: Simple similarity calculation
            def calculate_similarity(elem1, elem2):
                if re.fullmatch(r'\d+', elem1['text'].strip()) and re.fullmatch(r'\d+', elem2['text'].strip()):
                    return 1.0
                text_sim = SequenceMatcher(None, elem1['text'], elem2['text']).ratio()
                x_sim = 1 - abs(elem1['x0_pct'] - elem2['x0_pct'])
                y_sim = 1 - abs(elem1['y0_pct'] - elem2['y0_pct'])
                width_sim = 1 - abs(elem1['width_pct'] - elem2['width_pct'])
                
                alignment_sim = 1.0 if (elem1['is_centered'] == elem2['is_centered'] and 
                                      elem1['is_left_aligned'] == elem2['is_left_aligned'] and 
                                      elem1['is_right_aligned'] == elem2['is_right_aligned']) else 0.8
                
                overall_sim = (text_sim * 0.4 + x_sim * 0.2 + y_sim * 0.2 + 
                             width_sim * 0.1 + alignment_sim * 0.1)
                
                return overall_sim
            
            # Step 3: Find header candidates (including those marked by uploaded_by detection)
            header_candidates = [elem for elem in page_elements if elem['is_header_zone']]
            
            # Add any headers marked by uploaded_by detection
            uploaded_by_headers = [elem for elem in page_elements if elem.get('marked_by_uploaded_by') and elem.get('is_header_zone')]
            for header_elem in uploaded_by_headers:
                if header_elem not in header_candidates:
                    header_candidates.append(header_elem)
            
            header_groups = self._group_similar_elements(header_candidates, calculate_similarity, 
                                                       SIMILARITY_THRESHOLD, total_pages, MIN_OCCURRENCE_RATE)
            
            # Step 4: Find footer candidates with adaptive detection
            footer_candidates = [elem for elem in page_elements if elem['is_footer_zone']]
            
            # Add special regex-based detection for "uploaded by" patterns in footer area (only for 'acts' pdf type)
            uploaded_by_candidates = []
            if pdf_type == 'acts':
                self.logger.info("Processing 'uploaded by' patterns for PDF type 'acts'")
                
                # Group elements by page for easier processing
                pages_dict = {}
                for elem in page_elements:
                    page_num = elem['page_num']
                    if page_num not in pages_dict:
                        pages_dict[page_num] = []
                    pages_dict[page_num].append(elem)
                
                for elem in page_elements:
                    text_lower = elem['text'].lower().strip()
                    # Check if text matches "uploaded by" pattern and is in footer area (bottom 50% of page)
                    if re.search(r'^uploaded\s*by\s*\S*\s*', text_lower) and elem['y0_pct'] <= 0.5:
                        elem['is_footer_zone'] = True  # Mark as footer zone
                        uploaded_by_candidates.append(elem)
                        self.logger.info("Found 'uploaded by' pattern in footer area: page=%d, y=%.3f, text='%s'", 
                                       elem['page_num'], elem['y0_pct'], elem['text'][:40])
                        
                        # Find and mark related textboxes on the same page within threshold areas
                        page_num = elem['page_num']
                        if page_num in pages_dict:
                            self._mark_related_header_footer_textboxes(elem, pages_dict[page_num], uploaded_by_candidates, 
                                                                     HEADER_ZONE_THRESHOLD, FOOTER_ZONE_THRESHOLD)
            else:
                self.logger.debug("Skipping 'uploaded by' pattern detection for PDF type '%s' (only works for 'acts')", pdf_type)
            
            # Add uploaded by candidates to footer candidates
            footer_candidates.extend(uploaded_by_candidates)
            
            # If no footers found with current logic, try finding elements at actual bottom of pages
            if not footer_candidates:
                self.logger.info("No footers found with standard detection, trying adaptive approach...")
                
                # Group elements by page and find the ones at the bottom of each page
                pages_dict = {}
                for elem in page_elements:
                    page_num = elem['page_num']
                    if page_num not in pages_dict:
                        pages_dict[page_num] = []
                    pages_dict[page_num].append(elem)
                
                # For each page, find elements that are actually at the bottom
                adaptive_footer_candidates = []
                for page_num, page_elems in pages_dict.items():
                    if len(page_elems) < 2:
                        continue
                    
                    # Sort by Y coordinate to find bottom elements
                    sorted_elems = sorted(page_elems, key=lambda e: e['y0_pct'])
                    
                    # Take elements from the bottom portion of the page
                    bottom_threshold = 0.25  # Bottom 25% of elements
                    num_bottom_elements = max(1, int(len(sorted_elems) * bottom_threshold))
                    bottom_elements = sorted_elems[:num_bottom_elements]
                    
                    # Add these as footer candidates
                    for elem in bottom_elements:
                        elem['is_footer_zone'] = True  # Mark as footer zone
                        adaptive_footer_candidates.append(elem)
                        self.logger.debug("Adaptive footer candidate: page=%d, y=%.3f, text='%s'", 
                                        page_num, elem['y0_pct'], elem['text'][:40])
                
                footer_candidates.extend(adaptive_footer_candidates)
                self.logger.info("Found %d adaptive footer candidates", len(adaptive_footer_candidates))
            
            # Group footer candidates, but handle "uploaded by" patterns separately
            regular_footer_candidates = [elem for elem in footer_candidates 
                                       if not re.search(r'^uploaded\s*by\s*\S*\s*', elem['text'].lower().strip())]
            footer_groups = self._group_similar_elements(regular_footer_candidates, calculate_similarity,
                                                       SIMILARITY_THRESHOLD, total_pages, MIN_OCCURRENCE_RATE)
            
            # Add special groups for "uploaded by" patterns with relaxed criteria
            uploaded_by_groups = self._group_uploaded_by_patterns(uploaded_by_candidates, total_pages)
            footer_groups.extend(uploaded_by_groups)
            
            self.logger.info("Grouped into %d header groups and %d footer groups",
                           len(header_groups), len(footer_groups))

            # Step 5: Simple validation - just use the groups as they are
            self.adaptive_headers = header_groups
            self.adaptive_footers = footer_groups

            self.logger.info("Adaptive detection complete: %d header groups, %d footer groups",
                           len(self.adaptive_headers), len(self.adaptive_footers))

            self._pending_page_elements = page_elements
            self._pending_line_tolerance = LINE_TOLERANCE

        except Exception as e:
            self.logger.exception("Error during adaptive header/footer detection: %s", e)

    def finalize_adaptive_header_footer_detection(self):
        page_elements = getattr(self, '_pending_page_elements', None)
        line_tolerance = getattr(self, '_pending_line_tolerance', 0.02)
        if page_elements is None:
            return

        try:
            page_elements = [
                elem for elem in page_elements
                if self.all_pgs[elem['page_num']].all_tbs.get(elem['textbox']) is None
            ]
            for group in self.adaptive_headers + self.adaptive_footers:
                group['elements'] = [
                    elem for elem in group['elements']
                    if self.all_pgs[elem['page_num']].all_tbs.get(elem['textbox']) is None
                ]

            self._extend_headers_footers_by_line(page_elements, line_tolerance)
            self._apply_adaptive_headers_footers()
        except Exception as e:
            self.logger.exception("Error finalizing adaptive header/footer detection: %s", e)
        finally:
            self._pending_page_elements = None
    
    
    def _analyze_header_footer_content(self, text):
        import re
        
        text_lower = text.lower().strip()
        
        # Common header/footer patterns
        header_footer_patterns = [
            r'page\s*\d+',           # Page numbers
            r'\d+\s*page',           # Page numbers (reverse)
            r'^\d+$',                # Just numbers
            r'chapter\s*\d+',        # Chapter references
            r'section\s*\d+',        # Section references
            r'\d{4}',                # Years
            r'copyright',            # Copyright notices
            r'©',                    # Copyright symbol
            r'confidential',         # Confidentiality notices
            r'draft',                # Draft notices
            r'www\.',                # Web addresses
            r'\.com|\.org|\.gov',    # Domain extensions
            r'^\d+[-./]\d+',         # Date patterns
            r'rev\.|revision',       # Revision markers
            r'version\s*\d+',        # Version numbers
            r'^uploaded\s*by\s*\S*\s*',  # Uploaded by patterns
        ]
        
        # Content that's unlikely to be header/footer
        unlikely_patterns = [
            r'\w{50,}',              # Very long words (likely body text)
            r'[.!?]\s+[A-Z]',        # Sentences (multiple sentences)
            r'\w+\s+\w+\s+\w+\s+\w+\s+\w+',  # 5+ words (likely paragraph)
        ]
        
        # Check for header/footer indicators
        for pattern in header_footer_patterns:
            if re.search(pattern, text_lower):
                return True
        
        # Check for unlikely content
        for pattern in unlikely_patterns:
            if re.search(pattern, text):
                return False
        
        # Additional heuristics
        if len(text) < 5:  # Very short text might be page numbers
            return True
        
        if len(text) > 100:  # Long text unlikely to be header/footer
            return False
        
        # Check if it's mostly numbers or special characters
        alphanumeric_ratio = sum(c.isalnum() for c in text) / len(text)
        if alphanumeric_ratio < 0.5:  # Less than 50% alphanumeric
            return True
        
        # Default to False for body content
        return False
    
    def _group_uploaded_by_patterns(self, uploaded_by_candidates, total_pages):
        groups = []
        
        if not uploaded_by_candidates:
            return groups
        
        # Since "uploaded by" patterns can vary (different usernames), group them more loosely
        # Just check if they have the basic "uploaded by" pattern
        used_elements = set()
        
        for candidate in uploaded_by_candidates:
            if id(candidate) in used_elements:
                continue
                
            # Find all elements with "uploaded by" pattern
            similar_elements = [candidate]
            used_elements.add(id(candidate))
            
            for other in uploaded_by_candidates:
                if id(other) in used_elements:
                    continue
                    
                # For "uploaded by" patterns, just check if both match the pattern
                # (don't require high text similarity since usernames will differ)
                other_text_lower = other['text'].lower().strip()
                if re.search(r'^uploaded\s*by\s*\S*\s*', other_text_lower):
                    similar_elements.append(other)
                    used_elements.add(id(other))
            
            # For "uploaded by" patterns, accept even single occurrences
            # since they are explicitly identified by regex pattern
            if len(similar_elements) >= 1:  # Accept even single occurrence
                representative_text = "uploaded by [user]"  # Generic representative text
                
                avg_x0_pct = sum(elem['x0_pct'] for elem in similar_elements) / len(similar_elements)
                avg_y0_pct = sum(elem['y0_pct'] for elem in similar_elements) / len(similar_elements)
                
                groups.append({
                    'elements': similar_elements,
                    'representative_text': representative_text,
                    'avg_x0_pct': avg_x0_pct,
                    'avg_y0_pct': avg_y0_pct,
                    'occurrence_rate': len(similar_elements) / total_pages,
                    'pages': [elem['page_num'] for elem in similar_elements],
                    'quality_score': 0.9,  # High quality score for regex-matched patterns
                    'pattern_type': 'uploaded_by'
                })
                
                self.logger.info("Created 'uploaded by' footer group with %d elements across pages: %s", 
                               len(similar_elements), [elem['page_num'] for elem in similar_elements])
        
        return groups
    
    def _handle_single_page_header_footer_detection(self, pages, pdf_type, header_zone_threshold, footer_zone_threshold):
        try:
            page_elements = []
            page = pages[0]
            page_num = 1
            
            if page_num not in self.all_pgs:
                self.logger.warning("Page 1 not found in all_pgs for single-page detection")
                return
                
            page_obj = self.all_pgs[page_num]
            
            # Extract all textboxes with normalized coordinates
            for tb in page_obj.all_tbs.keys():
                try:
                    text = tb.extract_text_from_tb().strip()
                    if not text or text.isspace():
                        continue
                        
                    # Normalize coordinates as percentages of page dimensions
                    x0_pct = tb.coords[0] / page_obj.pg_width
                    y0_pct = tb.coords[1] / page_obj.pg_height
                    x1_pct = tb.coords[2] / page_obj.pg_width
                    y1_pct = tb.coords[3] / page_obj.pg_height
                    
                    width_pct = x1_pct - x0_pct
                    height_pct = y1_pct - y0_pct
                    
                    # Calculate relative position zones
                    is_header_zone = y0_pct >= (1 - header_zone_threshold)
                    is_footer_zone = y0_pct <= footer_zone_threshold
                    
                    page_elements.append({
                        'page_num': page_num,
                        'text': text,
                        'textbox': tb,
                        'x0_pct': x0_pct,
                        'y0_pct': y0_pct,
                        'x1_pct': x1_pct,
                        'y1_pct': y1_pct,
                        'width_pct': width_pct,
                        'height_pct': height_pct,
                        'is_header_zone': is_header_zone,
                        'is_footer_zone': is_footer_zone,
                        'is_centered': abs(x0_pct + width_pct/2 - 0.5) < 0.1,
                        'is_left_aligned': x0_pct < 0.1,
                        'is_right_aligned': x1_pct > 0.9
                    })
                    
                except Exception as e:
                    self.logger.warning("Error processing textbox on single page: %s", e)
                    continue
            
            self.logger.info("Found %d elements on single page", len(page_elements))
            
            # For single-page PDFs, be very strict about what constitutes headers/footers
            header_candidates = []
            footer_candidates = []
            
            # Only consider elements that are in the zones AND match header/footer patterns
            for elem in page_elements:
                text_lower = elem['text'].lower().strip()
                
                # Check if text has header/footer characteristics
                is_likely_header_footer = self._analyze_header_footer_content(elem['text'])
                
                # Be stricter for single-page: must be in zone AND match pattern
                if elem['is_header_zone'] and is_likely_header_footer:
                    header_candidates.append(elem)
                    self.logger.info("Single-page header candidate: y=%.3f, text='%s'", 
                                   elem['y0_pct'], elem['text'][:40])
                elif elem['is_footer_zone'] and is_likely_header_footer:
                    footer_candidates.append(elem)
                    self.logger.info("Single-page footer candidate: y=%.3f, text='%s'", 
                                   elem['y0_pct'], elem['text'][:40])
            
            # Handle special "uploaded by" patterns for acts PDFs (only if in appropriate zones)
            if pdf_type == 'acts':
                for elem in page_elements:
                    text_lower = elem['text'].lower().strip()
                    if re.search(r'^uploaded\s*by\s*\S*\s*', text_lower):
                        if elem['is_footer_zone'] or elem['y0_pct'] <= 0.5:  # Footer zone or bottom half
                            footer_candidates.append(elem)
                            self.logger.info("Single-page 'uploaded by' footer: y=%.3f, text='%s'", 
                                           elem['y0_pct'], elem['text'][:40])
                            # For single page, don't mark related textboxes to avoid false positives
            
            # Create simple groups for single-page elements
            if header_candidates:
                self.adaptive_headers = [{
                    'elements': header_candidates,
                    'representative_text': f"Single-page headers ({len(header_candidates)} items)",
                    'quality_score': 0.8,
                    'pattern_type': 'single_page_header'
                }]
            
            if footer_candidates:
                self.adaptive_footers = [{
                    'elements': footer_candidates,
                    'representative_text': f"Single-page footers ({len(footer_candidates)} items)",
                    'quality_score': 0.8,
                    'pattern_type': 'single_page_footer'
                }]
            
            self.logger.info("Single-page detection complete: %d header candidates, %d footer candidates", 
                           len(header_candidates), len(footer_candidates))
            
            # Apply the detected headers and footers
            self._apply_adaptive_headers_footers()
            
        except Exception as e:
            self.logger.exception("Error during single-page header/footer detection: %s", e)
    
    def _mark_related_header_footer_textboxes(self, uploaded_by_elem, page_elements, uploaded_by_candidates, 
                                            header_zone_threshold, footer_zone_threshold):
        try:
            uploaded_by_y = uploaded_by_elem['y0_pct']
            page_num = uploaded_by_elem['page_num']
            
            # Calculate threshold boundaries
            header_zone_min_y = 1 - header_zone_threshold  # Top threshold% of page
            footer_zone_max_y = footer_zone_threshold       # Bottom threshold% of page
            
            self.logger.debug("Marking related textboxes for 'uploaded by' pattern on page %d (y=%.3f)", 
                            page_num, uploaded_by_y)
            self.logger.debug("Header zone: y >= %.3f, Footer zone: y <= %.3f", 
                            header_zone_min_y, footer_zone_max_y)
            
            for elem in page_elements:
                # Skip if it's the same element or different page
                if elem['textbox'] == uploaded_by_elem['textbox'] or elem['page_num'] != page_num:
                    continue
                
                # Skip if already marked by uploaded_by detection (to avoid double processing)
                if elem.get('marked_by_uploaded_by'):
                    continue
                
                elem_y = elem['y0_pct']
                
                # Mark textboxes above the 'uploaded by' pattern as headers 
                # BUT only if they are in the header zone area
                if elem_y > uploaded_by_y and elem_y >= header_zone_min_y:
                    elem['is_header_zone'] = True
                    elem['marked_by_uploaded_by'] = True
                    self.logger.debug("Marked textbox above 'uploaded by' as header (in header zone): page=%d, y=%.3f, text='%s'", 
                                    page_num, elem_y, elem['text'][:30])
                    
                # Mark textboxes below the 'uploaded by' pattern as footers
                # BUT only if they are in the footer zone area  
                elif elem_y < uploaded_by_y and elem_y <= footer_zone_max_y:
                    elem['is_footer_zone'] = True
                    elem['marked_by_uploaded_by'] = True
                    uploaded_by_candidates.append(elem)  # Add to uploaded_by_candidates for grouping
                    self.logger.debug("Marked textbox below 'uploaded by' as footer (in footer zone): page=%d, y=%.3f, text='%s'", 
                                    page_num, elem_y, elem['text'][:30])
                
                # Log textboxes that are above/below but outside threshold areas
                elif elem_y > uploaded_by_y and elem_y < header_zone_min_y:
                    self.logger.debug("Textbox above 'uploaded by' but outside header zone (y=%.3f < %.3f): '%s'", 
                                    elem_y, header_zone_min_y, elem['text'][:30])
                elif elem_y < uploaded_by_y and elem_y > footer_zone_max_y:
                    self.logger.debug("Textbox below 'uploaded by' but outside footer zone (y=%.3f > %.3f): '%s'", 
                                    elem_y, footer_zone_max_y, elem['text'][:30])
                    
        except Exception as e:
            self.logger.exception("Error marking related textboxes for 'uploaded by' pattern: %s", e)
    
    def _group_similar_elements(self, candidates, similarity_func, threshold, total_pages, min_occurrence_rate):
        groups = []
        used_elements = set()

        for candidate in candidates:
            if id(candidate) in used_elements:
                continue
                
            # Find all similar elements
            similar_elements = [candidate]
            used_elements.add(id(candidate))
            
            for other in candidates:
                if id(other) in used_elements:
                    continue
                    
                if similarity_func(candidate, other) >= threshold:
                    similar_elements.append(other)
                    used_elements.add(id(other))
            
            # Check if this group meets minimum occurrence criteria
            occurrence_rate = len(similar_elements) / total_pages
            self.logger.debug("Group with %d elements has occurrence rate %.3f (min required: %.3f)", 
                            len(similar_elements), occurrence_rate, min_occurrence_rate)
            if occurrence_rate >= min_occurrence_rate:
                # Calculate representative text and position
                texts = [elem['text'] for elem in similar_elements]
                representative_text = max(set(texts), key=texts.count)
                
                avg_x0_pct = sum(elem['x0_pct'] for elem in similar_elements) / len(similar_elements)
                avg_y0_pct = sum(elem['y0_pct'] for elem in similar_elements) / len(similar_elements)
                
                groups.append({
                    'elements': similar_elements,
                    'representative_text': representative_text,
                    'avg_x0_pct': avg_x0_pct,
                    'avg_y0_pct': avg_y0_pct,
                    'occurrence_rate': occurrence_rate,
                    'pages': [elem['page_num'] for elem in similar_elements]
                })
        
        return groups
    
    def _extend_headers_footers_by_line(self, page_elements, line_tolerance=0.02):
        try:
            
            # Group page elements by page for easier processing
            pages_dict = {}
            for elem in page_elements:
                page_num = elem['page_num']
                if page_num not in pages_dict:
                    pages_dict[page_num] = []
                pages_dict[page_num].append(elem)
            
            # Process headers
            for header_group in self.adaptive_headers:
                self._extend_group_by_line(header_group, pages_dict, 'header', line_tolerance)
            
            # Process footers  
            for footer_group in self.adaptive_footers:
                self._extend_group_by_line(footer_group, pages_dict, 'footer', line_tolerance)
                
            self.logger.info("Extended headers/footers to include same-line textboxes")
            
        except Exception as e:
            self.logger.exception("Error extending headers/footers by line: %s", e)
    
    def _extend_group_by_line(self, group, pages_dict, group_type, line_tolerance):
        extended_elements = []
        
        # For each element in the group, find other textboxes on the same line
        for element in group['elements']:
            page_num = element['page_num']
            element_y = element['y0_pct']
            
            if page_num not in pages_dict:
                continue
                
            # Find textboxes on the same line (within tolerance)
            same_line_elements = []
            for other_elem in pages_dict[page_num]:
                # Skip if it's the same element
                if other_elem['textbox'] == element['textbox']:
                    continue
                    
                # Check if it's on the same line (within tolerance)
                y_diff = abs(other_elem['y0_pct'] - element_y)
                if y_diff <= line_tolerance:
                    # Check if this element is not already marked as header/footer
                    if not self._is_already_marked_as_header_footer(other_elem):
                        same_line_elements.append(other_elem)
                        
            # Add same-line elements to the group
            for same_line_elem in same_line_elements:
                extended_elements.append(same_line_elem)
                self.logger.debug("Extended %s on page %d: added '%s' (y=%.3f) to line with '%s' (y=%.3f)", 
                                group_type, page_num, same_line_elem['text'][:30], 
                                same_line_elem['y0_pct'], element['text'][:30], element_y)
        
        # Add extended elements to the group
        if extended_elements:
            group['elements'].extend(extended_elements)
            self.logger.info("Extended %s group '%s' with %d additional same-line elements", 
                           group_type, group.get('representative_text', '')[:40], len(extended_elements))
    
    def _is_already_marked_as_header_footer(self, element):
        # Check if element is in any header group
        for header_group in self.adaptive_headers:
            for header_elem in header_group['elements']:
                if header_elem['textbox'] == element['textbox'] and header_elem['page_num'] == element['page_num']:
                    return True
        
        # Check if element is in any footer group  
        for footer_group in self.adaptive_footers:
            for footer_elem in footer_group['elements']:
                if footer_elem['textbox'] == element['textbox'] and footer_elem['page_num'] == element['page_num']:
                    return True
                    
        return False

    def _apply_adaptive_headers_footers(self):
        try:
            for header_group in self.adaptive_headers:
                for element in header_group['elements']:
                    page_num = element['page_num']
                    textbox = element['textbox']

                    if page_num in self.all_pgs and textbox in self.all_pgs[page_num].all_tbs:
                        self.all_pgs[page_num].all_tbs[textbox] = "header"
                        self.logger.debug("Applied adaptive header on page %d: '%s'",
                                        page_num, element['text'][:50])

            for footer_group in self.adaptive_footers:
                for element in footer_group['elements']:
                    page_num = element['page_num']
                    textbox = element['textbox']

                    if page_num in self.all_pgs and textbox in self.all_pgs[page_num].all_tbs:
                        self.all_pgs[page_num].all_tbs[textbox] = "footer"
                        self.logger.debug("Applied adaptive footer on page %d: '%s'",
                                        page_num, element['text'][:50])

            self.logger.info("Successfully applied adaptive headers and footers to pages")

        except Exception as e:
            self.logger.exception("Error applying adaptive headers and footers: %s", e)
    
    def get_path_cache_xml(self):
        current_file = Path(__file__).resolve()       
        source_dir = current_file.parent.parent              
        cache_xml_dir = source_dir / "cache_xml"      
        cache_xml_dir.mkdir(parents=True, exist_ok=True)  
        return cache_xml_dir
    
    def is_pdf_file(self, path):
        try:
            with open(path, "rb") as f:
                header = f.read(1024)  # read first 1KB, enough for header
                return b"%PDF-" in header
        except Exception:
            return False

    @staticmethod
    def is_url_like(value):
        return bool(re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', str(value)))
    
    def set_htmlbuilder(self):
        if self.pdf_type in set(['acts']):
            self.html_builder = self.get_htmlBuilder(self.pdf_type, self.has_doc_end)
        elif self.pdf_type in set(['sebi_circulars']):
            self.html_builder = self.get_htmlBuilder(self.pdf_type)
        else:
            self.html_builder = self.get_htmlBuilder(self.pdf_type)

    def process_scanned_copy(self, pdf_type, base_name_of_file, start_page,
                             end_page):
        if pdf_type == 'egazette':
            pages = ChromeLensParserTool(self.pdf_path)\
                                .build_xml(start_page, end_page)
        else:
            pages = TesseractParserTool(self.pdf_path, self.ocr_language)\
                                            .build_xml(start_page, end_page)
        self.print_page_xml(pages)
        self.set_htmlbuilder()
        self.logger.debug("Extracting header and footer info...")
        self.get_page_header_footer(pages, base_name_of_file, self.output_dir)
        self.logger.debug("Processing content from pages...")
        if pdf_type == 'acts':
            self.process_pages_acts(pdf_type)
        elif pdf_type == 'sebi_circulars':
            self.process_pages_sebi_circulars(pdf_type)
        elif pdf_type == 'sebi':
            self.process_pages_sebi(pdf_type)
        elif pdf_type == 'judgments':
            self.process_pages_judgments(pdf_type)
        else:
            self.process_pages(pdf_type)
        self.logger.info("Finished Processing of pages for: %s", self.pdf_path)
    
    # --- parse pdf using pdfminer to convert to XML ---       
    def parsePDF(self, pdf_type, char_margin, word_margin, line_margin, \
                start_page, end_page):
        # a font whose ToUnicode map is broken is repaired in a copy of the pdf
        # before anything reads it, so that the xml parsed here and camelot
        # both get the characters that are really on the page
        self.repair_tounicode()
        # camelot reads the pdf itself rather than the xml parsed here, so the
        # legacy indic font conversion is installed into it for the whole run
        with self.camelot_font_conversion():
            return self.parse_pdf_pages(pdf_type, char_margin, word_margin,
                                        line_margin, start_page, end_page)

    def parse_pdf_pages(self, pdf_type, char_margin, word_margin, line_margin, \
                start_page, end_page):
        try:
            if not os.path.exists(self.pdf_path):
                self.logger.error(f"[✖] Input file not found: {self.pdf_path}")
                return False
        
            if not self.is_pdf_file(self.pdf_path):
                self.logger.error(f"[✖] Input is not a valid PDF file: {self.pdf_path}")
                return False

            if self.is_url_like(self.output_dir):
                self.logger.error(
                    f"[✖] -o/--output-directory ('{self.output_dir}') looks like a URL, "
                    f"not a local filesystem path where output files get written. Did you "
                    f"mean to pass that as -pu/--public-base-url instead? The public URL "
                    f"used for IIIF manifest links is always supplied separately via "
                    f"-pu/--public-base-url (or the PUBLIC_BASE_URL env var) and never "
                    f"derived from output_dir."
                )
                return False

            base_name_of_file = os.path.splitext(os.path.basename(self.pdf_path))[0]
            self.logger.info("Starting PDF parsing for: %s", self.pdf_path)
            if self.is_scanned_copy:
                self.process_scanned_copy(pdf_type, base_name_of_file, start_page, 
                                          end_page)
                return True
            
            cache_xml_path = self.get_path_cache_xml()
            self.xml_path =  cache_xml_path / f"{base_name_of_file}.xml"
            self.logger.debug("Converting PDF to XML...")
            self.parserTool.convert_to_xml(self.pdf_path,self.xml_path, self.pdf_type, \
                                           char_margin, word_margin, line_margin)

            
            if not os.path.exists(self.xml_path):
                self.logger.error("XML file was not created: %s", self.xml_path)
                return False

            self.logger.debug("Parsing pages from XML: %s", self.xml_path)
            pages = self.parserTool.get_pages_from_xml(self.xml_path, start_page, end_page)
            if pages:
                self.logger.debug("Removing spaces overprinted by the next glyph...")
                self.drop_overlapping_spaces(pages)
                self.logger.debug("Detecting what the fonts that name no encoding draw...")
                self.detect_unknown_fonts(pages)
                self.logger.debug("Converting text in legacy indic fonts to unicode...")
                self.convert_indic_fonts(pages)
                self.set_htmlbuilder()
                self.logger.debug("Extracting header and footer info...")
                self.get_page_header_footer(pages, base_name_of_file, self.output_dir)
                self.logger.debug("Processing content from pages...")
                if pdf_type == 'acts':
                    self.process_pages_acts(pdf_type)
                elif pdf_type == 'sebi_circulars':
                    self.process_pages_sebi_circulars(pdf_type)
                elif pdf_type == 'sebi':
                    self.process_pages_sebi(pdf_type)
                elif pdf_type == 'judgments':
                    self.process_pages_judgments(pdf_type)
                else:
                    self.process_pages(pdf_type)
                self.logger.info("Finished Processing of pages for: %s", self.pdf_path)
            else:
                # if pdf_type in {'egazette'}:
                #     self.logger.info('using chrome lens for the scanned copy')
                #     self.html_builder = HTMLBuilderChromeLens(self.pdf_path)
                # else:
                    self.is_scanned_copy = True
                    self.process_scanned_copy(pdf_type, base_name_of_file,
                                              start_page, end_page)

            if pdf_type in {'egazette', 'sebi'}:
                self.write_manifest()

            return True
        except Exception as e:
            self.logger.exception("Exception occurred while parsing PDF: %s", e)
            return False

    def print_page_xml(self, pages):
        import xml.etree.ElementTree as ET
        from xml.dom import minidom
        for page_el in pages:
            raw = ET.tostring(page_el, encoding="unicode")
            pretty = minidom.parseString(raw).toprettyxml(indent="  ")
            pretty = "\n".join(
                line for line in pretty.split("\n") if line.strip()
            )
            self.logger.info(pretty)
            
    
    def escape_inline_markup(self, content):
        if not content:
            return content

        pattern = r'(?<!\\)([*_])'

        return re.sub(
            pattern,
            r'\\\1',
            content
        )
    
    # --- func for writing the html content to the desired output file ---
    def write_manifest(self):
        if not self.unique_images:
            self.logger.info("No images to build an IIIF manifest from; skipping manifest generation.")
            return None
        if self.is_url_like(self.output_dir):
            self.logger.error(
                f"[✖] output_dir ('{self.output_dir}') looks like a URL, not a local "
                f"filesystem path - skipping manifest generation. Use -pu/--public-base-url "
                f"(or PUBLIC_BASE_URL) to supply the public URL instead."
            )
            return None
        if not self.public_base_url and not os.environ.get("PUBLIC_BASE_URL"):
            self.logger.warning(
                "[!] No -pu/--public-base-url (or PUBLIC_BASE_URL env var) supplied - "
                "IIIF manifest and HTML manifest-link URLs will fall back to "
                "http://localhost:8000, which is almost certainly wrong outside local "
                "development."
            )
        try:
            output_dir = Path(self.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            # Build manifest from finalized images collected in self.unique_images
            manifest_builder = IIIFManifest(
                    Path(output_dir),
                    label=Path(self.pdf_path).stem,
                    base_prez_uri=self.public_base_url,
                    server_root=self.server_root,
                    rights=self.rights,
                    provider_id=self.provider_id,
                    provider_name=self.provider_name,
                    attribution=self.attribution
                )

            # Page ids come from the source XML as strings (e.g. "1", "12"), so a plain
            # lexicographic sort would order page 12 before page 2 - sort numerically
            # where possible, falling back to the raw value for anything non-numeric.
            def page_sort_key(p):
                return (0, int(p)) if str(p).isdigit() else (1, str(p))

            image_entries = []
            for meta in self.unique_images.values():
                p = meta.get('path', None)
                if not p:
                    continue
                try:
                    pp = Path(p)
                    if not pp.exists():
                        continue
                except Exception:
                    continue
                image_entries.append({
                    "path": pp,
                    # Every page this (deduplicated) image appeared on, in reading order -
                    # carried through so the manifest can label each image by its actual
                    # source page(s) instead of a meaningless "Image N" counter.
                    "pages": sorted(meta.get("pages", set()), key=page_sort_key),
                    # OCR text already extracted from the image itself (Figure.py runs a
                    # real OCR pass to decide whether to keep the image at all) - surfaced
                    # here as a IIIF "supplementing" annotation instead of being discarded
                    # after that gate check, so it isn't computed and thrown away.
                    "text": meta.get("text") or None,
                    "language": meta.get("language"),
                })

            # Present in the order the images actually appear in the source document
            # (first page each deduplicated image was seen on), not dict-insertion order.
            image_entries.sort(key=lambda e: page_sort_key(e["pages"][0]) if e["pages"] else (2, ""))

            # "Generated from" must never leak the local filesystem path (server
            # username, directory layout, repo/project structure) into what's a
            # publicly-served manifest.json - only the filename itself carries useful
            # provenance information, so strip the directory component entirely rather
            # than embedding self.pdf_path verbatim.
            manifest_path = manifest_builder.create_from_images(
                image_entries, metadata={"Generated from": Path(self.pdf_path).name}
            )
            if manifest_path:
                self.logger.info("Created IIIF manifest at %s", manifest_path)
                self.manifest_url = manifest_builder.get_manifest_uri()
            return manifest_path
        except Exception as e:
            self.logger.exception("Failed to create IIIF manifest: %s", e)
            return None

    def add_manifest_link_to_html(self, content):
        if self.pdf_type not in {'egazette', 'sebi'} or not self.manifest_url:
            return content
        link_html = (
            f'<p><a href="{self.manifest_url}" target="_blank">'
            f'click here for IIIF manifest</a></p>\n'
        )
        if '<body>' in content:
            return content.replace('<body>', '<body>\n' + link_html, 1)
        return link_html + content

    def write_html(self, content, start_page, end_page):
        if not content:
            self.logger.warning(f'HTML content not generate for pdf pdth: {self.pdf_path}')
            return
        content = self.add_manifest_link_to_html(content)
        try:
            if start_page or end_page:
                if start_page is None:
                    start_page = 1
                elif end_page is None:
                    end_page = self.total_pgs - 1 + int(start_page)
                filename =  os.path.splitext(os.path.basename(self.pdf_path))[0] +f"pg:{start_page}_pg:{end_page}.html"
            else:
                filename =  os.path.splitext(os.path.basename(self.pdf_path))[0] +".html"
        except Exception as e:
            filename =  os.path.splitext(os.path.basename(self.pdf_path))[0] +".html"
        try:
            output_dir = Path(self.output_dir)

            # Check if the directory exists
            if not output_dir.exists():
                output_dir.mkdir(parents=True, exist_ok=True)
                self.logger.info(f"Created directory: {output_dir.resolve()}")
            else:
                self.logger.info(f"Directory already exists: {output_dir.resolve()}")

            # Write the HTML content to the specified file
            output_path = output_dir / filename
            with output_path.open("w", encoding="utf-8") as f:
                f.write(content)

            self.logger.info("content written successfully to %s", output_path)
        except Exception as e:
            self.logger.exception("Failed to write HTML content: %s", e)

    def write_bluebell(self, content, start_page, end_page):
        content = self.escape_inline_markup(content)
        if not content:
            self.logger.warning('Content not available to save')
            return
        try:
            if start_page or end_page:
                if start_page is None:
                    start_page = 1
                elif end_page is None:
                    end_page = self.total_pgs - 1 + int(start_page)
                filename =  os.path.splitext(os.path.basename(self.pdf_path))[0] +f"pg:{start_page}_pg:{end_page}.bluebell"
            else:
                filename =  os.path.splitext(os.path.basename(self.pdf_path))[0] +".bluebell"
        except Exception as e:
            filename =  os.path.splitext(os.path.basename(self.pdf_path))[0] +".bluebell"
        try:
            output_dir = Path(self.output_dir)

            # Check if the directory exists
            if not output_dir.exists():
                output_dir.mkdir(parents=True, exist_ok=True)
                self.logger.info(f"Created directory: {output_dir.resolve()}")
            else:
                self.logger.info(f"Directory already exists: {output_dir.resolve()}")

            # Write the HTML content to the specified file
            output_path = output_dir / filename
            with output_path.open("w", encoding="utf-8") as f:
                f.write(content)

            self.logger.info("content written successfully to %s", output_path)

        except Exception as e:
            self.logger.exception("Failed to write  content: %s", e)
    
    def clear_xml_cache(self):
        if self.is_scanned_copy:
            return
        if not hasattr(self, "xml_path") or not self.xml_path:
            self.logger.warning("No xml_path attribute set for this instance")
            return
        if not os.path.exists(self.xml_path):
            self.logger.warning("XML file was not created or already deleted: %s", self.xml_path)
            return

        try:
            os.remove(self.xml_path)
            self.logger.info("Successfully removed XML file: %s", self.xml_path)
        except OSError as e:
            self.logger.error("Error deleting XML file %s: %s", self.xml_path, e)


    def get_path_cache_pdf(self):
        current_file = Path(__file__).resolve()       
        source_dir = current_file.parent.parent              
        cache_xml_dir = source_dir / "cache_pdf"      
        cache_xml_dir.mkdir(parents=True, exist_ok=True)  
        return cache_xml_dir

    def clear_cache_pdf(self):
        cache_dir = self.get_path_cache_pdf()
        if not os.path.exists(self.pdf_path):
            self.logger.warning("File was not created or already deleted: %s", self.pdf_path)
        else:
            if os.path.commonpath([os.path.abspath(self.pdf_path), os.path.abspath(cache_dir)]) == os.path.abspath(cache_dir):
                try:
                    os.remove(self.pdf_path)
                    self.logger.info("Successfully removed cached_pdf: %s", self.pdf_path)
                except OSError as e:
                    self.logger.error("Error deleting cached file %s: %s", self.pdf_path, e)
            else:
                self.logger.debug("Skipping delete, file not in cache_pdf: %s", self.pdf_path)

    def clear_ocr_engines(self):
        if self.ocr_engine == "paddleocr":
            clear_paddle_ocr_engines()
            self.logger.info("Released paddleocr model(s) held by this run")


    def detect_header_pre(self, pages):

        def parse_bbox(elem):
            try:
                return tuple(map(float, elem.attrib["bbox"].split(",")))
            except:
                return None

        def norm(txt):
            return re.sub(r"[\s_]+", " ", txt or "").strip()

        def tl_text(tl):
            vals = []
            for t in tl.findall(".//text"):
                if t.text:
                    vals.append(t.text)
            return norm("".join(vals))

        def spaced(word):
            chars = []
            for ch in word:
                if ch.isspace():
                    chars.append(r'[\s_]+')
                else:
                    chars.append(re.escape(ch))
            return r'[\s_]*'.join(chars)


        def phrase(txt):
            return r'[\s_]+'.join(spaced(x) for x in txt.split())


        months = (
            f"{spaced('January')}|{spaced('February')}|{spaced('March')}|"
            f"{spaced('April')}|{spaced('May')}|{spaced('June')}|"
            f"{spaced('July')}|{spaced('August')}|{spaced('September')}|"
            f"{spaced('October')}|{spaced('November')}|{spaced('December')}"
        )

        
        # TIER 1
        tier1 = [

            re.compile(
                rf'\n[\s_]*({phrase("THE")}[\s_]+)?'
                rf'({phrase("BRIEF")}[\s_]+)?'
                rf'({phrase("REASONS FOR THE")}[\s_]+)?'
                rf'{spaced("JUDGMENT")}[\s_]*:?[\s_]*\n?',
                re.I
            ),

            re.compile(
                rf'\n[\s_]*({spaced("JUDGMENT")}|{spaced("JUDGEMENT")})'
                rf'[\s_]*(\(.+\))?[\s_]*(:|\n)?',
                re.I
            ),

            re.compile(
                rf'^[\s_]*({spaced("JUDGMENT")}|{spaced("JUDGEMENT")})',
                re.I
            ),

            re.compile(
                rf'\n[\s_]*[-:]*[\s_]*{spaced("JUDGMENT")}[\s_]*[-:]*[\s_]*\n?',
                re.I
            ),

            re.compile(
                rf'\n[\s_]*[-:]*[\s_]*{spaced("JUDGEMENT")}[\s_]*[-:]*[\s_]*\n?',
                re.I
            ),


            re.compile(
                rf'\n[\s_]*{phrase("EX PARTE JUDGMENT")}[\s_]*\n?',
                re.I
            ),


            re.compile(
                rf'\n[\s_]*[-:]*[\s_]*{spaced("\u0ca4\u0cc0\u0cb0\u0ccd\u0caa\u0cc1")}[\s_]*[-:]*[\s_]*\n?',   # ತೀರ್ಪು
                re.I
            ),

            re.compile(
                rf'^[\s_]*{spaced("\u0ca4\u0cc0\u0cb0\u0ccd\u0caa\u0cc1")}[\s_]*$',   # ತೀರ್ಪು
                re.I
            ),

            re.compile(
                rf'\n[\s_]*[-:]*[\s_]*{spaced("\u0906\u0926\u0947\u0936")}[\s_]*[-:]*[\s_]*\n?',   # आदेश
                re.I
            ),

            re.compile(
                rf'\n[\s_]*{spaced("\u0928\u094d\u092f\u093e\u092f\u0928\u093f\u0930\u094d\u0923\u092f")}[\s_]*\n?',   # न्यायनिर्णय
                re.I
            ),

            
            re.compile(
                rf'\n[\s_]*[-:]*[\s_]*{spaced("\u0928\u093f\u0915\u093e\u0932\u092a\u0924\u094d\u0930")}[\s_]*[-:]*[\s_]*\n?',   # निकालपत्र
                re.I
            ),

            re.compile(
                rf'\n[\s_]*{phrase("\u090f\u0915\u0924\u0930\u094d\u092b\u0940 \u0928\u093f\u0915\u093e\u0932\u092a\u0924\u094d\u0930")}[\s_]*\n?',   # एकतर्फी निकालपत्र
                re.I
            ),

            re.compile(
                rf'\n[\s_]*{phrase("\u090f\u0915\u0924\u0930\u094d\u092b\u093e \u0928\u093f\u0915\u093e\u0932\u092a\u0924\u094d\u0930")}[\s_]*\n?',   # एकतर्फा निकालपत्र
                re.I
            ),

            re.compile(
                rf'\n[\s_]*{phrase("\u0935\u093e\u091f\u092a \u0906\u0926\u0947\u0936")}[\s_]*\n?',   # वाटप आदेश
                re.I
            ),

            re.compile(
                rf'\n[\s_]*{phrase("\u090f\u0915\u0924\u0930\u094d\u092b\u093e \u0906\u0926\u0947\u0936")}[\s_]*\n?',   # एकतर्फा आदेश
                re.I
            ),

            re.compile(
                rf'\n[\s_]*{phrase("\u090f\u0915\u0924\u0930\u094d\u092b\u0940 \u0906\u0926\u0947\u0936")}[\s_]*\n?',   # एकतर्फी आदेश
                re.I
            ),

            re.compile(
                rf'\n[\s_]*{spaced("\u0ba4\u0bc0\u0bb0\u0bcd\u0baa\u0bc1\u0bb0\u0bc8")}[\s_]*\n?',   # தீர்ப்புரை
                re.I
            ),

            re.compile(
                rf'\n[\s_]*{spaced("\u0ba4\u0bc0\u0bb0\u0bcd\u0baa\u0bcd\u0baa\u0bc1")}[\s_]*\n?',   # தீர்ப்பு
                re.I
            ),

            re.compile(
                rf'\n[\s_]*{spaced("\u0b89\u0ba4\u0bcd\u0ba4\u0bbf\u0bb0\u0bb5\u0bc1")}[\s_]*\n?',   # உத்திரவு
                re.I
            ),

            re.compile(
                rf'\n[\s_]*{spaced("\u0b89\u0ba4\u0bcd\u0ba4\u0bb0\u0bb5\u0bc1")}[\s_]*\n?',   # உத்தரவு
                re.I
            ),

            re.compile(
                rf'\n[\s_]*{spaced("\u0d35\u0d3f\u0d27\u0d3f\u0d28\u0d4d\u0d2f\u0d3e\u0d2f\u0d02")}[\s_]*\n?',   # വിധിന്യായം
                re.I
            ),

            re.compile(
                rf'\n[\s_]*{spaced("\u0d09\u0d24\u0d4d\u0d24\u0d30\u0d35\u0d4d")}[\s_]*\n?',   # ഉത്തരവ്
                re.I
            ),



            # AWARD
            re.compile(
                rf'\n[\s_]*({phrase("FINAL AWARD")}|{phrase("INTERIM AWARD")}|{spaced("AWARD")})[\s_]*(:|\n)?',
                re.I
            ),

            re.compile(
                rf'\n[\s_]*({phrase("ORAL AWARD")}|{spaced("AWARD")}[\s_]*\([\s_]*{spaced("ORAL")}[\s_]*\))',
                re.I
            ),

            # P.C.
            re.compile(
                r'\n[\s_]*((P[\s_]*\.?[\s_]*C[\s_]*\.?)|(P[\s_]*E[\s_]*R[\s_]*C[\s_]*O[\s_]*U[\s_]*R[\s_]*T))[\s_]*(:|-)?[\s_]*\n?',
                re.I
            ),

            # ORDER
            re.compile(
                rf'\n[\s_]*({spaced("ORDER")}|{phrase("COMMON ORDER")}|'
                rf'{phrase("FINAL ORDER")}|{phrase("INTERIM ORDER")})'
                rf'[\s_]*(\(.+\))?[\s_]*(:|\n)?',
                re.I
            ),

            re.compile(
                rf'\n[\s_]*{phrase("DISTRIBUTION ORDER")}[\s_]*\n?',
                re.I
            ),


            re.compile(
                rf'^[\s_]*{spaced("ORDER")}',
                re.I
            ),

            re.compile(
                rf'\n[\s_]*{phrase("ORDER UNDER")}.*',
                re.I
            ),

            # ORAL ORDER / ORAL JUDGMENT
            re.compile(
                rf'\n[\s_]*(({spaced("ORAL")}[\s_]+({spaced("ORDER")}|{spaced("JUDGMENT")}))|'
                rf'(({spaced("ORDER")}|{spaced("JUDGMENT")})[\s_]*\([\s_]*{spaced("ORAL")}[\s_]*\)))',
                re.I
            ),

            re.compile(
                rf'\n[\s_]*{phrase("ORDER BELOW EXH")}[\s_]*\n?',
                re.I
            ),


            # COMMON
            re.compile(
                rf'\n[\s_]*{phrase("COMMON")}[\s_]+'
                rf'({spaced("JUDGMENT")}|{spaced("ORDER")})[\s_]*(\n|:)?',
                re.I
            ),
        ]

        
        # TIER 2

        tier2 = [

            # MANDEEP PANNU, J. (ORAL)
            re.compile(
                rf'\n?[A-Z .]{{3,120}},[\s_]*J\.?[\s_]*\(?{spaced("ORAL")}\)?[\s_]*\n?',
                re.I
            ),

            # XYZ, J.
            re.compile(
                r',[ ]*(J|Judge|Justice|Chief Justice|C\.J\.?)'
                r'([. ]+\(?(Oral|ORAL|oral)\)?)?:?[ ]*\n?',
                re.I
            ),

            re.compile(
                r',[ \xa0]*(((C\.|J)?(J[. \r\t]+:?|Judge:)'
                r'([ ]*\(?(Oral|ORAL)\)?)?)|(J[\s_]*\(Oral\)))'
                r'[. \r\t:]*\n?',
                re.I
            ),

            re.compile(
                r'\n[\s_]*(Per|PER).+,[\s_]*J[\s_]*(:[\s_]*)?\n?',
                re.I
            ),

            # CORAM
            re.compile(
                rf'\n[\s_]*({spaced("PER")}|{spaced("CORAM")})[\s_]*:[\s_]*{spaced("JUSTICE")}.*',
                re.I
            ),

            # "HON'BLE" (straight or curly apostrophe) is the near-universal
            # spelling in Indian court captions (apostrophe elided from
            # "Honourable") - spaced("HONBLE") doesn't tolerate it, so this
            # never matched that spelling and fell through to the much
            # weaker generic comma-based fallback below.
            re.compile(
                rf"\n[\s_]*(H[\s_]*O[\s_]*N[\s_]*['’]?[\s_]*B[\s_]*L[\s_]*E|{spaced('HONOURABLE')}).{{3,20}}{spaced('JUSTICE')}.*",
                re.I
            ),

            re.compile(
                rf'\n[\s_]*{spaced("JUSTICE")}.{{0,30}}',
                re.I
            ),

            # PRESENT
            re.compile(
                rf'\n[\s_]*{spaced("PRESENT")}[\s_]*\n?',
                re.I
            ),

            # PRONOUNCED
            re.compile(
                rf'\n[\s_]*({spaced("PRONOUNCED")}|{spaced("DICTATED")})'
                rf'([\s_]+{phrase("IN COURT")})?',
                re.I
            ),
        ]


        # TIER 3

        tier3 = [

            re.compile(
                rf'\n[\s_]*(({spaced("MEMBER")})[\s_]*\((J|A|T)\)|'
                rf'({spaced("CHAIRMAN")})([\s_]*\((A|J)\))?|'
                rf'(({phrase("JUDICIAL MEMBER")}|{phrase("ADMINISTRATIVE MEMBER")}|{phrase("TECHNICAL MEMBER")})))'
                rf'[\s_]*:?[\s_]*\n?',
                re.I
            ),

            re.compile(
                rf'\n[\s_]*{spaced("MEMBER")}[ .:-]*\n?',
                re.I
            ),

            re.compile(
                rf'\n[ \r\t]*{spaced("PER")}[ \r\t]+.*,[ \r\t]+'
                rf'({phrase("JUDICIAL MEMBER")}|A\.?M|J\.?M|{phrase("ACCOUNTANT MEMBER")}).*',
                re.I
            ),
        ]


        # TIER 4

        tier4 = [

            # Arabic numerals
            re.compile(
                r'\n[ ]*[12]\.',
                re.I
            ),

            # Devanagari / Marathi / Hindi : १ २
            re.compile(
                r'\n[ ]*[\u0967\u0968]\.',
                re.I
            ),

            # Kannada : ೧ ೨
            re.compile(
                r'\n[ ]*[\u0ce7\u0ce8]\.',
                re.I
            ),

            # Tamil : ௧ ௨
            re.compile(
                r'\n[ ]*[\u0be7\u0be8]\.',
                re.I
            ),

            # Malayalam : ൧ ൨
            re.compile(
                r'\n[ ]*[\u0d67\u0d68]\.',
                re.I
            ),

            re.compile(
                rf'\n[ ]*((({phrase("BRIEF FACTS")})|{spaced("BACKGROUND")}|({phrase("FACTUAL BACKGROUND")}))).*',
                re.I
            ),

            re.compile(
                rf'\n[\s_]*{phrase("FACT OF THE CASE")}[\s_]*:?[\s_]*\n?',
                re.I
            ),

            re.compile(
                rf'\n[\s_]*{spaced("EXORDIUM")}[\s_]*:?[\s_]*\n?',
                re.I
            ),

            re.compile(
                rf'\n[ \r]*{phrase("INFORMATION SOUGHT")}',
                re.I
            ),

            re.compile(
                rf'\n[\s_]*{spaced("RESPONDENT")}(S)?.{{0,15}}',
                re.I
            ),
        ]

        # TIER 5

        tier5 = [

            re.compile(
                rf'\n[\s_]*({spaced("DATED")}[\s:,]+({spaced("THE")}[\s_]+)?)'
                rf'\d{{1,2}}[\s_]*(th|rd|nd|st)[\s_]+({months})[,\s]+\d{{4}}[\s_]*\n?',
                re.I
            ),

            re.compile(
                rf'\n[\s_]*{spaced("DATED")}[\s_]*:.*',
                re.I
            ),

            re.compile(
                r'\n(\d+[ /]+)?\d+[/.-]\d+[/.-]\d+',
                re.I
            ),

            re.compile(
                rf'\n[\s_]*{phrase("ORDER DATED")}[\s_]*:[\s_]*\d{{1,2}}[./-]\d{{1,2}}[./-]\d{{2,4}}[\s_]*\n?',
                re.I
            ),

            re.compile(
                rf'\n[\s_]*{phrase("ORDER DATED")}[\s_]*:[\s_]*\n?',
                re.I
            ),
        ]

        # TIER 6
        tier6 = [

            re.compile(
                rf'\n[\s_]*({phrase("BY THE COURT")}|{phrase("BY COURT")}|{phrase("PER COURT")}).*',
                re.I
            ),

            re.compile(
                rf'\n[\s_]*{phrase("FOR THE PETITIONER")}(S)?[\s.]*\n?',
                re.I
            ),

            re.compile(
                rf'\n[\s_]*({phrase("PUBLIC PROSECUTOR")}|{phrase("FOR THE RESPONDENT")})(S)?[\s.]*\n?',
                re.I
            ),

            re.compile(
                r'\n[ \t]*([-]+|=+|\*+|[.]+)[ \t]*\n?',
                re.I
            ),
        ]


       
        # FINAL PRIORITY
        

        tiers = [tier1, tier2, tier3, tier4, tier5, tier6]

        # COLLECT ROWS


        rows = []

        for pg_idx, pg in enumerate(pages):

            page_num = pg_idx + 1

            if page_num not in self.all_pgs:
                continue

            page_obj = self.all_pgs[page_num]

            for tb, label in page_obj.all_tbs.items():

                if label is not None:
                    continue

                for tl in tb.tbox.findall(".//textline"):

                    bb = parse_bbox(tl)
                    if not bb:
                        continue

                    txt = tl_text(tl)
                    if not txt:
                        continue

                    x0, y0, x1, y1 = bb

                    rows.append({
                        "page": page_num,
                        "tb": tb,
                        "text": txt,
                        "x0": x0,
                        "y0": y0,
                        "x1": x1,
                        "y1": y1,
                    })

        if not rows:
            return


        # top to bottom
        rows.sort(key=lambda z: (z["page"], -z["y0"], z["x0"]))


        # SEARCH TIER BY TIER

        hit = None
        hit_row = None

        for tier_idx, tier in enumerate(tiers, start=1):

            built = ""

            for i, row in enumerate(rows):

                built += "\n" + row["text"]

                matched = False

                # A real heading occupies its own row - nothing of substance
                # follows it there. A tier regex is anchored to the *start* of
                # a row (via the "\n" every row is joined with) but has no
                # matching guarantee at the other end, so it can just as
                # easily match the first word of an ordinary sentence that
                # happens to line-wrap onto its own row (e.g. a body
                # paragraph reading "judgment of the Supreme Court does not
                # apply..." - "judgment" alone satisfies the JUDGMENT tier1
                # pattern). Requiring the match to reach (within a small
                # trailing-punctuation/whitespace slack of) the end of the
                # text built so far rules that out: it forces the matched
                # row to be essentially *only* the heading, not a sentence
                # that merely starts with the heading word.
                trailing_slack = 3

                for rgx in tier:
                    m = rgx.search(built)
                    if m and (len(built) - m.end()) <= trailing_slack:
                        hit = i
                        hit_row = row
                        matched = True

                        self.logger.info(
                            f"Matched tier {tier_idx} regex: {rgx.pattern}"
                        )
                        break

                if matched:
                    break

            if hit is not None:
                break


        if hit is None:
            return

        # FIND ALL ROWS WITH EXACT SAME y0,y1 AS MATCHED ROW

        same_line_idx = set()

        target_page = hit_row["page"]
        target_y0 = hit_row["y0"]
        target_y1 = hit_row["y1"]

        for j, r in enumerate(rows):

            if r["page"] != target_page:
                continue

            if r["y0"] == target_y0 and r["y1"] == target_y1:
                same_line_idx.add(j)


        # LABEL
        # 1) all rows upto hit
        # 2) all rows having exact same y0,y1 as matched row

        mark_indexes = set(range(hit + 1)) | same_line_idx

        seen = set()

        for i in sorted(mark_indexes):

            row = rows[i]

            page_obj = self.all_pgs[row["page"]]
            tb = row["tb"]

            key = (row["page"], id(tb))

            if key in seen:
                continue

            seen.add(key)

            if page_obj.all_tbs[tb] is None:
                page_obj.all_tbs[tb] = "pre_header"

    def detect_sebi_header_pre(self, pages):
        body_start_re = re.compile(
            r'^(?!\s*\d{1,4}\.\d{1,4}\.\d{2,4})\s*[1-9]\d{0,2}[A-Z]?\.(?!\))(?:\s+.*)?$',
        )

        rows = []
        for pg_idx, pg in enumerate(pages):
            page_num = pg_idx + 1
            if page_num not in self.all_pgs:
                continue
            page_obj = self.all_pgs[page_num]
            for tb, label in page_obj.all_tbs.items():
                if label is not None:
                    continue
                text = re.sub(r'\s+', ' ', tb.extract_text_from_tb()).strip()
                if not text:
                    continue
                x0, y0, x1, y1 = tb.coords
                rows.append({"page": page_num, "tb": tb, "text": text, "y0": y0})

        if not rows:
            return

        rows.sort(key=lambda z: (z["page"], -z["y0"]))

        body_start_idx = None
        for i, row in enumerate(rows):
            if body_start_re.match(row["text"]):
                body_start_idx = i
                break

        if not body_start_idx:
            return

        for row in rows[:body_start_idx]:
            page_obj = self.all_pgs[row["page"]]
            if page_obj.all_tbs[row["tb"]] is None:
                page_obj.all_tbs[row["tb"]] = "pre_header"

    def detect_toc(self, pages):
        TOC_HEADING_RE = re.compile(
            r'^\s*(TABLE\s+OF\s+CONTENTS?|INDEX|CONTENTS?|SYNOPSIS|'
            r'LIST\s+OF\s+CONTENTS?|'
            r'ARRANGEMENT\s+OF\s+(?:SECTIONS?|CLAUSES?|PARAGRAPHS?|REGULATIONS?))\s*$',
            re.I
        )
        PAGE_NO_HEADER_RE = re.compile(r'^\s*PAGE\s*(?:NO\.?|NUMBER)\s*$', re.I)
        ROMAN_RE = re.compile(r'^M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$', re.I)
        TOC_ENTRY_RE = re.compile(r'^(.*?\S)[\s.․…]{2,}(\(?[A-Za-z0-9]{1,7}\)?)\.?\s*$')
        MAX_MISS_STREAK = 2
        MAX_CONTINUATION_LEN = 120
        LEVEL_TOL = 10.0
        MIN_ENTRIES = 3
        ROW_Y_TOL = 0.6

        def page_token(raw):
            token = raw.strip().strip('().').strip()
            if not token:
                return None
            if token.isdigit() and len(token) <= 4:
                return token
            if ROMAN_RE.match(token):
                return token
            return None

        def match_entry(row):
            if len(row["texts"]) > 1:
                token = page_token(row["texts"][-1])
                if token:
                    body = " ".join(row["texts"][:-1]).strip()
                    if body:
                        return body, token
            match = TOC_ENTRY_RE.match(row["text"])
            if match:
                token = page_token(match.group(2))
                if token:
                    return match.group(1).strip(), token
            return None

        rows = []
        for pg_idx, pg in enumerate(pages):
            page_num = pg_idx + 1
            if page_num not in self.all_pgs:
                continue
            page_obj = self.all_pgs[page_num]
            for tb, label in page_obj.all_tbs.items():
                if label is not None:
                    continue
                text = re.sub(r'\s+', ' ', tb.extract_text_from_tb()).strip()
                if not text:
                    continue
                x0, y0, x1, y1 = tb.coords
                rows.append({
                    "page": page_num, "tbs": [tb], "texts": [text], "text": text,
                    "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                })

        if not rows:
            return

        rows.sort(key=lambda z: (z["page"], -z["y0"], z["x0"]))

        merged_rows = []
        i = 0
        while i < len(rows):
            group = [rows[i]]
            j = i + 1
            while j < len(rows) and rows[j]["page"] == group[0]["page"]:
                row_height = max(group[-1]["y1"] - group[-1]["y0"], rows[j]["y1"] - rows[j]["y0"], 1.0)
                if abs(rows[j]["y0"] - group[-1]["y0"]) <= row_height * ROW_Y_TOL:
                    group.append(rows[j])
                    j += 1
                    continue
                break
            if len(group) > 1:
                merged_rows.append({
                    "page": group[0]["page"],
                    "tbs": [g["tbs"][0] for g in group],
                    "texts": [g["text"] for g in group],
                    "text": " ".join(g["text"] for g in group),
                    "x0": group[0]["x0"], "y0": group[0]["y0"],
                    "x1": group[-1]["x1"], "y1": group[0]["y1"],
                })
            else:
                merged_rows.append(rows[i])
            i = j
        rows = merged_rows

        heading_idx = None
        for idx, row in enumerate(rows):
            if any(TOC_HEADING_RE.match(t) for t in row["texts"]):
                heading_idx = idx
                break

        if heading_idx is None:
            return

        heading_row = rows[heading_idx]
        consumed_tbs = [(heading_row["page"], tb) for tb in heading_row["tbs"]]

        entries = []
        pending_prefix = ""
        pending_start_x0 = None
        pending_tbs = []
        miss_streak = 0

        i = heading_idx + 1
        while i < len(rows):
            row = rows[i]
            text = row["text"]

            if len(row["tbs"]) == 1 and PAGE_NO_HEADER_RE.match(text):
                consumed_tbs.extend((row["page"], tb) for tb in row["tbs"])
                i += 1
                continue

            matched = match_entry(row)
            if matched:
                body, page_no = matched
                if pending_prefix:
                    body = f"{pending_prefix} {body}"
                entries.append({
                    "text": body,
                    "page_no": page_no,
                    "x0": pending_start_x0 if pending_start_x0 is not None else row["x0"],
                })
                consumed_tbs.extend((row["page"], tb) for tb in row["tbs"])
                consumed_tbs.extend(pending_tbs)
                pending_prefix = ""
                pending_start_x0 = None
                pending_tbs = []
                miss_streak = 0
                i += 1
                continue

            if len(text) <= MAX_CONTINUATION_LEN and miss_streak < MAX_MISS_STREAK:
                if pending_start_x0 is None:
                    pending_start_x0 = row["x0"]
                pending_prefix = f"{pending_prefix} {text}".strip()
                pending_tbs.extend((row["page"], tb) for tb in row["tbs"])
                miss_streak += 1
                i += 1
                continue

            break

        if len(entries) < MIN_ENTRIES:
            return

        stack = []
        for entry in entries:
            x0 = entry["x0"]
            while stack and x0 < stack[-1][0] - LEVEL_TOL:
                stack.pop()
            if stack and abs(x0 - stack[-1][0]) <= LEVEL_TOL:
                level = stack[-1][1]
            elif stack and x0 > stack[-1][0] + LEVEL_TOL:
                level = stack[-1][1] + 1
                stack.append((x0, level))
            else:
                level = 1
                stack = [(x0, level)]
            entry["level"] = level

        out = [
            '<nav class="toc">',
            '<p class="toc-title">Table of Contents</p>',
            '<table class="toc-table">',
        ]
        for entry in entries:
            level = entry["level"]
            indent = f' style="padding-left: {(level - 1) * 1.5}em;"' if level > 1 else ''
            title = html.escape(entry["text"])
            page_no = html.escape(entry["page_no"])
            out.append(
                f'<tr class="toc-level-{level}">'
                f'<td class="toc-entry"{indent}>{title}</td>'
                f'<td class="toc-page">{page_no}</td></tr>'
            )
        out.append('</table>')
        out.append('</nav>')

        self.html_builder.toc_html = '\n'.join(out) + '\n'

        for page_num, tb in consumed_tbs:
            page_obj = self.all_pgs[page_num]
            if page_obj.all_tbs[tb] is None:
                page_obj.all_tbs[tb] = "toc"

# --- func to define argument parser required for the tool ---
def get_arg_parser():
    parser = argparse.ArgumentParser(description="To automate pdf Parse and Convert to structured", add_help=True)
    parser.add_argument('-i','--input-filePath',dest='input_file_path',action='store',\
                        required=True,help='mention input file path')
    parser.add_argument('-fp','--start-page',dest='start_page', action='store',\
                        type=int,required=False, default=None, help='mention start page')
    parser.add_argument('-lp','--end-page',dest='end_page', action='store',\
                        type=int,required=False, default = None, help='mention end page')
    parser.add_argument('-s', '--sidenotes', dest = 'has_sidenotes', action = 'store_true', \
                        required = False, default = False, help = 'mention if pdf has sidenotes')
    parser.add_argument('-a','--amendments',dest= "is_amendment_pdf",action = "store_true",\
                        required = False,default=False, help = 'mention if pdf contains amendments')
    parser.add_argument('-l', '--loglevel', dest='loglevel', action='store',\
                        required = False, default = 'info', \
                        help='log level(error|warning|info|debug)')
    parser.add_argument('-g', '--logfile', dest='logfile', action='store',\
                        required = False, default = None, help='log file')
    parser.add_argument('-o','--output-directory',dest = "output_dir",action="store",\
                        required=True,help = "Directory to store output file")
    parser.add_argument('-x','--keep-xml',dest="keep_xml",action = "store_true",\
                        required = False, default = False, help = "saves the intermediate xml in cache_xml folder")
    parser.add_argument('-t','--type', dest= 'pdf_type', action = 'store', \
                        required = False, help= 'which helps to process and convert html type = (sebi | acts)' )
    parser.add_argument('-lm', '--line-margin', dest='line_margin', action='store', \
                        required=False, default=None, help = 'if requires, set line margin threshold for pdf miner')
    parser.add_argument('-cm', '--char-margin', dest='char_margin', action='store', \
                        required=False, default=None, help = 'if requires, set char margin threshold for pdf miner')
    parser.add_argument('-wm', '--word-margin', dest='word_margin', action='store', \
                        required=False, default=None, help = 'if requires, set word margin threshold for pdf miner')
    parser.add_argument('-de', '--doc-end', dest = 'has_doc_end', action = 'store_true', \
                        required = False, default = False, help = 'mention if pdf has document end symbol (---)')
    parser.add_argument('-fnc', '--footnote-continuation', dest='is_footnote_continuation', action = 'store_true', \
                        required = False, default = False, help = 'mention if pdf has footnote that continued across the pages')
    parser.add_argument('-mip', '--min-img-pixels', dest = 'min_img_pixels', action = 'store', \
                      required = False,  default = 0,  help = 'minimum pixel area threshold for initial filtering (area = dimension^2). Images are further filtered based on text content detection.')
    parser.add_argument('-ol', '--ocr-language', dest='ocr_language', action='store', \
                      required=False, default='eng', choices=TESSERACT_LANGUAGES,
                      help=f'tesseract language code for OCR (default: eng). One of: {", ".join(TESSERACT_LANGUAGES)}')
    parser.add_argument('-oe', '--ocr-engine', dest='ocr_engine', action='store', \
                      required=False, default='tesseract', choices=OCR_ENGINES_AVAILABLE,
                      help=f'OCR engine to use for figure text extraction (default: tesseract). One of: {", ".join(OCR_ENGINES_AVAILABLE)}')
    parser.add_argument('-sc', '--scanned-copy', dest = 'scanned_copy', action = 'store_true',
                        required = False, default = False, help = 'mention if the pdf copy is scanned')
    parser.add_argument('-te', '--table-extract', dest = 'table_extract', action = 'store_true',
                        required = False, default = False, help = 'mention if the pdf has borderless table or pdf is scanned copy to extract table content')
    parser.add_argument('-ftx', '--figure-text', dest = 'figure_text', action = 'store_true',
                        required = False, default = False,
                        help = 'extract OCR text for figures (checked against a fasttext language-confidence '
                               'threshold) and include it in the html output; images without confident text are '
                               'dropped. Default: keep every figure as-is, with no OCR/confidence check. Has no '
                               'effect for acts/sebi_circulars (bluebell output never includes figure text).')
    parser.add_argument('-pu', '--public-base-url', dest = 'public_base_url', action = 'store',
                        required = False, default = None,
                        help = 'public URL that --output-directory will be served from (e.g. '
                               'https://gazettes.servantsofknowledge.in/gzdl/html/andhra_extraordinary/2025-01-01), '
                               'used as the base for image/canvas/manifest URIs in the IIIF manifest (egazette/sebi types only). '
                               'Falls back to the PUBLIC_BASE_URL env var, then http://localhost:8000.')
    parser.add_argument('-sr', '--server-root', dest = 'server_root', action = 'store',
                        required = False, default = None,
                        help = 'local filesystem directory that acts as the web server\'s document root '
                               '(e.g. /var/www), used only to compute the URL path segment between '
                               '--public-base-url and "manifest/<pdfname>/..." in the IIIF manifest '
                               '(egazette/sebi types only) - never affects where output files are written. '
                               'output_dir must be located within it. If not given, output_dir itself is '
                               'assumed to be the server root (no extra path segment).')
    parser.add_argument('-rt', '--rights', dest = 'rights', action = 'store',
                        required = False, default = None,
                        help = 'IIIF manifest "rights" URI (a Creative Commons or RightsStatements.org '
                               'license URI, e.g. https://creativecommons.org/publicdomain/mark/1.0/) '
                               '(egazette/sebi types only). Omitted entirely if not supplied - never '
                               'defaulted to a guessed license.')
    parser.add_argument('-pi', '--provider-id', dest = 'provider_id', action = 'store',
                        required = False, default = None,
                        help = 'URI identifying the organization presenting the manifest (e.g. its '
                               'homepage), used for the IIIF "provider" field (egazette/sebi types only). '
                               'Requires --provider-name too to be added; ignored alone.')
    parser.add_argument('-pn', '--provider-name', dest = 'provider_name', action = 'store',
                        required = False, default = None,
                        help = 'Display name of the organization presenting the manifest, used for the '
                               'IIIF "provider" field (egazette/sebi types only). Requires --provider-id '
                               'too to be added; ignored alone.')
    parser.add_argument('-at', '--attribution', dest = 'attribution', action = 'store',
                        required = False, default = None,
                        help = 'Attribution text for the IIIF manifest\'s "requiredStatement" (egazette/sebi '
                               'types only). Omitted entirely if not supplied.')
    parser.add_argument('-fc', '--font-conv', dest = 'font_conv_map', action = 'append',
                        required = False, default = None, metavar = 'FONT=CONVERTER',
                        help = 'Convert text in FONT to unicode using CONVERTER, for legacy indic '
                               'fonts whose name does not say which one they are (e.g. '
                               '-fc TT572t00=chanakya). FONT is matched against the pdf font name '
                               'the same way the built-in ones are, i.e. anywhere in it and '
                               'ignoring case, and takes precedence over them. Repeat the option '
                               'or separate the mappings with commas. Fonts named after a '
                               'supported converter are converted anyway and need no mapping.')
    parser.add_argument('-fm', '--font-model', dest = 'font_model', action = 'store',
                        required = False, default = None, metavar = 'MODEL',
                        help = 'Model that says what a font whose name identifies no '
                               f'encoding is drawing, as written by machinelearning/'
                               f'training.py (default {FONT_MODEL_PATH}, and detection '
                               'is simply skipped when there is no model there). A font '
                               'the model places needs no -fc mapping; a -fc mapping '
                               'wins over the model for the font it names.')
    parser.add_argument('-nfd', '--no-font-detect', dest = 'font_detect',
                        action = 'store_false',
                        help = 'Do not detect the encoding of the fonts whose name does '
                               'not give it away, i.e. use nothing but the font names '
                               'and the -fc mappings, as before the model existed.')
    return parser



logformat = '%(asctime)s: %(name)s: [%(funcName)s:%(lineno)d] %(levelname)s  %(message)s'
dateformat  = '%Y-%m-%d %H:%M:%S'

def initialize_file_logging(loglevel, filepath):
    logging.basicConfig(\
        level    = loglevel,   \
        format   = logformat,  \
        datefmt  = dateformat, \
        stream   = filepath
    )

def initialize_stream_logging(loglevel = logging.INFO):
    logging.basicConfig(\
        level    = loglevel,  \
        format   = logformat, \
        datefmt  = dateformat \
    )

def setup_logging(level, filename = None):
    leveldict = {'critical': logging.CRITICAL, 'error': logging.ERROR, \
                 'warning': logging.WARNING,   'info': logging.INFO, \
                 'debug': logging.DEBUG}
    loglevel = leveldict[level]

    if filename:
        filestream = codecs.open(filename, 'w', encoding='utf8')
        initialize_file_logging(loglevel, filestream)
    else:
        initialize_stream_logging(loglevel)

if __name__ == "__main__":
    logger = logging.getLogger(__name__)

    parser = get_arg_parser()
    args = parser.parse_args()
    setup_logging(args.loglevel, filename = args.logfile)
    pdf_path = args.input_file_path
    logger.debug(f"Input PDF path attached to process-{pdf_path}")
    start_page = None
    if args.start_page:
        start_page = int(args.start_page)
    logger.debug(f"Mentioned section start page-{start_page}")
    end_page = None
    if args.end_page:
        end_page = int(args.end_page)
        logger.debug(f"Mentioned section end page-{end_page}")
    is_amendment_pdf = args.is_amendment_pdf
    logger.debug(f"Is the pdf contains amendments - {"Yes" if is_amendment_pdf else "No"}")
    has_sidenotes = args.has_sidenotes
    logger.debug(f"Is the pdf contains side notes - {"Yes" if has_sidenotes else "No"}")
    output_dir = args.output_dir
    has_doc_end = args.has_doc_end
    is_footnote_continuation = args.is_footnote_continuation
    min_img_pixels = args.min_img_pixels
    if min_img_pixels and isinstance(min_img_pixels, str):
        min_img_pixels = int(min_img_pixels)
    ocr_language = args.ocr_language
    ocr_engine = args.ocr_engine
    is_scanned_copy = args.scanned_copy
    table_extract = args.table_extract
    figure_text = args.figure_text
    public_base_url = args.public_base_url
    server_root = args.server_root
    rights = args.rights
    provider_id = args.provider_id
    provider_name = args.provider_name
    attribution = args.attribution
    main = Main(pdf_path,is_amendment_pdf,output_dir, args.pdf_type, 
                has_sidenotes, has_doc_end,
                is_footnote_continuation, min_img_pixels, ocr_language,
                is_scanned_copy, table_extract, public_base_url, server_root,
                rights, provider_id, provider_name, attribution,
                figure_text, args.font_conv_map, ocr_engine,
                args.font_model, args.font_detect)
    # margins = compute_optimal_char_margin(pdf_path)
    char_margin = args.char_margin # str(margins)
    word_margin = args.word_margin # str(margins['word_margin'])
    line_margin = args.line_margin # str(margins['line_margin'])
    logger.info(f'char_margin : {char_margin}, word_margin: {word_margin}, line_margin: {line_margin}')
    try:
        is_success = main.parsePDF(args.pdf_type, char_margin, word_margin, line_margin, \
                                   start_page, end_page)
        if is_success:
            main.buildHTML(start_page, end_page) #end)
    finally:
        main.clear_cache_pdf()
        if not args.keep_xml:
            main.clear_xml_cache()
        main.clear_ocr_engines()
