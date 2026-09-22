import re
import io
import os
import contextlib
import logging
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODEL_PATH = PROJECT_ROOT / "model" / "lid.176.bin"

TESSERACT_LANGUAGES = [
    "eng",  # English
    "asm",  # Assamese
    "ben",  # Bengali
    "guj",  # Gujarati
    "hin",  # Hindi
    "kan",  # Kannada
    "mal",  # Malayalam
    "mar",  # Marathi
    "nep",  # Nepali
    "ori",  # Odia
    "pan",  # Punjabi
    "san",  # Sanskrit
    "snd",  # Sindhi
    "tam",  # Tamil
    "tel",  # Telugu
    "urd",  # Urdu
]

OCR_ENGINES_AVAILABLE = ["tesseract", "paddleocr"]

OCR_PDF_PARSERS_AVAILABLE = ["chromelens", "tesseract"]

TESSERACT_TO_PADDLE_LANG = {
    "eng": "en",
    "hin": "hi",
    "mar": "mr",
    "nep": "ne",
    "san": "sa",
    "tam": "ta",
    "tel": "te",
}

_LANG_MODEL = None
_PADDLE_OCR_ENGINES = {}
LOGGER = logging.getLogger(__name__)

def _get_lang_model():
    global _LANG_MODEL
    if _LANG_MODEL is None:
        import fasttext
        _LANG_MODEL = fasttext.load_model(str(MODEL_PATH))
    return _LANG_MODEL

def _get_paddle_ocr_engine(paddle_lang):
    if paddle_lang not in _PADDLE_OCR_ENGINES:
        os.environ.setdefault("OMP_NUM_THREADS", "1")
        os.environ.setdefault("MKL_NUM_THREADS", "1")
        os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

        import paddlex.utils.logging as pdx_logging
        pdx_logger = logging.getLogger("paddlex")
        pdx_logger.setLevel(logging.ERROR)
        pdx_logger.propagate = False

        from paddleocr import PaddleOCR
        with contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            _PADDLE_OCR_ENGINES[paddle_lang] = PaddleOCR(
                lang=paddle_lang,
                device="cpu",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
    return _PADDLE_OCR_ENGINES[paddle_lang]

def _extract_text_tesseract(image_path, lang):
    import pytesseract
    return pytesseract.image_to_string(
        str(image_path), lang=lang, config="--oem 3 --psm 6"
    ).strip()

def _extract_text_paddleocr(image_path, lang):
    paddle_lang = TESSERACT_TO_PADDLE_LANG.get(lang)
    if paddle_lang is None:
        raise ValueError(
            f"paddleocr engine has no mapping for language code '{lang}'. "
            f"Supported languages for paddleocr: {', '.join(sorted(TESSERACT_TO_PADDLE_LANG))}"
        )

    ocr = _get_paddle_ocr_engine(paddle_lang)
    result = ocr.predict(str(image_path))

    texts = []
    for item in result:
        texts.extend(item.json["res"]["rec_texts"])

    return "\n".join(texts).strip()

def clear_paddle_ocr_engines():
    _PADDLE_OCR_ENGINES.clear()

def extract_text(image_path, lang, engine="tesseract"):
    try:
        if engine == "paddleocr":
            return _extract_text_paddleocr(image_path, lang)
        return _extract_text_tesseract(image_path, lang)
    except Exception:
        return None


def detect_language(text):
    text = text.strip()

    if not text:
        return None, 0.0

    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text)

    labels, scores = _get_lang_model().predict(text)

    return (
        labels[0].replace("__label__", ""),
        float(scores[0]),
    )

ROMAN_RE  = r"(?:M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3}))"

INDIC_DIGIT_CHARS = (
    "\u0660-\u0669\u06F0-\u06F9\u0966-\u096F\u09E6-\u09EF\u0A66-\u0A6F\u0AE6-\u0AEF\u0B66-\u0B6F\u0BE6-\u0BEF\u0C66-\u0C6F\u0CE6-\u0CEF\u0D66-\u0D6F\u1C50-\u1C59\uABF0-\uABF9"
)

INDIC_LETTER_CHARS = (
    "\u0620-\u063F\u0641-\u064A\u0679\u067E\u0686\u0688\u0691\u06A9\u06AF\u06BA\u06BE\u06C1\u06C3\u06CC\u06D2\u0904-\u0939\u093D\u0950\u0958-\u0961\u0980\u0985-\u098C\u098F-\u0990\u0993-\u09A8\u09AA-\u09B0\u09B2\u09B6-\u09B9\u09BD\u09CE\u09DC-\u09DD\u09DF-\u09E1\u0A05-\u0A0A\u0A0F-\u0A10\u0A13-\u0A28\u0A2A-\u0A30\u0A32-\u0A33\u0A35-\u0A36\u0A38-\u0A39\u0A59-\u0A5C\u0A5E\u0A85-\u0A8D\u0A8F-\u0A91\u0A93-\u0AA8\u0AAA-\u0AB0\u0AB2-\u0AB3\u0AB5-\u0AB9\u0ABD\u0AD0\u0AE0-\u0AE1\u0B05-\u0B0C\u0B0F-\u0B10\u0B13-\u0B28\u0B2A-\u0B30\u0B32-\u0B33\u0B35-\u0B39\u0B3D\u0B5C-\u0B5D\u0B5F-\u0B61\u0B83\u0B85-\u0B8A\u0B8E-\u0B90\u0B92-\u0B95\u0B99-\u0B9A\u0B9C\u0B9E-\u0B9F\u0BA3-\u0BA4\u0BA8-\u0BAA\u0BAE-\u0BB9\u0BD0\u0C05-\u0C0C\u0C0E-\u0C10\u0C12-\u0C28\u0C2A-\u0C39\u0C3D\u0C58-\u0C5A\u0C5D\u0C60-\u0C61\u0C80\u0C85-\u0C8C\u0C8E-\u0C90\u0C92-\u0CA8\u0CAA-\u0CB3\u0CB5-\u0CB9\u0CBD\u0CDD-\u0CDE\u0CE0-\u0CE1\u0D04-\u0D0C\u0D0E-\u0D10\u0D12-\u0D3A\u0D3D\u0D4E\u0D54-\u0D56\u0D5F-\u0D61\u1C5A-\u1C7D\uABC0-\uABE2"
)

INDIC_NONZERO_DIGIT_CHARS = (
    "\u0661-\u0669\u06F1-\u06F9\u0967-\u096F\u09E7-\u09EF\u0A67-\u0A6F\u0AE7-\u0AEF\u0B67-\u0B6F\u0BE7-\u0BEF\u0C67-\u0C6F\u0CE7-\u0CEF\u0D67-\u0D6F\u1C51-\u1C59\uABF1-\uABF9"
)

LEGAL_TERM_TRANSLATIONS = {
    "chapter": (
        "chapter", "section",
        "\u0905\u0927\u094D\u092F\u093E\u092F", "\u0927\u093E\u0930\u093E", "\u092A\u094D\u0930\u0915\u0930\u0923", "\u0915\u0932\u092E", "\u092A\u0930\u093F\u091A\u094D\u091B\u0947\u0926", "\u0926\u092B\u093E",
        "\u0985\u09A7\u09CD\u09AF\u09BE\u09AF\u09BC", "\u09A7\u09BE\u09B0\u09BE", "\u09A7\u09BE\u09F0\u09BE",
        "\u0AAA\u0ACD\u0AB0\u0A95\u0AB0\u0AA3", "\u0A95\u0AB2\u0AAE", "\u0A05\u0A27\u0A3F\u0A06\u0A07", "\u0A27\u0A3E\u0A30\u0A3E",
        "\u0B05\u0B27\u0B4D\u0B5F\u0B3E\u0B5F", "\u0B27\u0B3E\u0B30\u0B3E", "\u0C85\u0CA7\u0CCD\u0CAF\u0CBE\u0CAF", "\u0CAA\u0CCD\u0CB0\u0C95\u0CB0\u0CA3",
        "\u0C05\u0C27\u0C4D\u0C2F\u0C3E\u0C2F\u0C2E\u0C41", "\u0C38\u0C46\u0C15\u0C4D\u0C37\u0C28\u0C4D",
        "\u0B85\u0BA4\u0BCD\u0BA4\u0BBF\u0BAF\u0BBE\u0BAF\u0BAE\u0BCD", "\u0BAA\u0BBF\u0BB0\u0BBF\u0BB5\u0BC1", "\u0D05\u0D27\u0D4D\u0D2F\u0D3E\u0D2F\u0D02", "\u0D35\u0D15\u0D41\u0D2A\u0D4D\u0D2A\u0D4D",
        "\u0628\u0627\u0628", "\u062F\u0641\u0639\u06C1", "\u062F\u0641\u0639\u0648",
    ),
    "part": (
        "part",
        "\u092D\u093E\u0917", "\u09AD\u09BE\u0997", "\u0AAD\u0ABE\u0A97", "\u0A2D\u0A3E\u0A17", "\u0B2D\u0B3E\u0B17", "\u0CAD\u0CBE\u0C97", "\u0C2D\u0C3E\u0C17\u0C2E\u0C41", "\u0BAA\u0B95\u0BC1\u0BA4\u0BBF", "\u0D2D\u0D3E\u0D17\u0D02",
        "\u062D\u0635\u06C1", "\u062D\u0635\u0648",
    ),
    "article": (
        "article",
        "\u0905\u0928\u0941\u091A\u094D\u091B\u0947\u0926", "\u0927\u093E\u0930\u093E",
        "\u0985\u09A8\u09C1\u099A\u09CD\u099B\u09C7\u09A6", "\u0A85\u0AA8\u0AC1\u0A9A\u0ACD\u0A9B\u0AC7\u0AA6", "\u0A27\u0A3E\u0A30\u0A3E",
        "\u0B05\u0B28\u0B41\u0B1A\u0B4D\u0B1B\u0B47\u0B26", "\u0C85\u0CA8\u0CC1\u0C9A\u0CCD\u0C9B\u0CC7\u0CA6", "\u0C05\u0C27\u0C3F\u0C15\u0C30\u0C23",
        "\u0B89\u0BB1\u0BC1\u0BAA\u0BCD\u0BAA\u0BC1\u0BB0\u0BC8", "\u0D05\u0D28\u0D41\u0D1A\u0D4D\u0D1B\u0D47\u0D26\u0D02",
        "\u062F\u0641\u0639\u06C1", "\u062F\u0641\u0639\u0648",
    ),
    "schedule": (
        "schedule",
        "\u0905\u0928\u0941\u0938\u0942\u091A\u0940",
        "\u09A4\u09AB\u09B8\u09BF\u09B2", "\u0985\u09A8\u09C1\u09B8\u09C2\u099A\u09C0",
        "\u0A85\u0AA8\u0AC1\u0AB8\u0AC2\u0A9A\u0ABF", "\u0A05\u0A28\u0A41\u0A38\u0A42\u0A1A\u0A40", "\u0B05\u0B28\u0B41\u0B38\u0B42\u0B1A\u0B40", "\u0C85\u0CA8\u0CC1\u0CB8\u0CC2\u0C9A\u0CBF", "\u0C05\u0C28\u0C41\u0C38\u0C42\u0C1A\u0C3F\u0C15",
        "\u0B85\u0B9F\u0BCD\u0B9F\u0BB5\u0BA3\u0BC8", "\u0D2A\u0D1F\u0D4D\u0D1F\u0D3F\u0D15",
        "\u062C\u062F\u0648\u0644",
    ),
    "annexure": (
        "annexures?",
        "\u0905\u0928\u0941\u0932\u0917\u094D\u0928\u0915", "\u092A\u0930\u093F\u0936\u093F\u0937\u094D\u091F", "\u0938\u0902\u0932\u0917\u094D\u0928\u0915",
        "\u09B8\u0982\u09AF\u09C1\u0995\u09CD\u09A4\u09BF",
        "\u0AAA\u0AB0\u0ABF\u0AB6\u0ABF\u0AB7\u0ACD\u0A9F", "\u0A38\u0A70\u0A32\u0A17\u0A28\u0A15", "\u0B2A\u0B30\u0B3F\u0B36\u0B3F\u0B37\u0B4D\u0B1F", "\u0C85\u0CA8\u0CC1\u0CAC\u0C82\u0CA7", "\u0C05\u0C28\u0C41\u0C2C\u0C02\u0C27\u0C02",
        "\u0B87\u0BA3\u0BC8\u0BAA\u0BCD\u0BAA\u0BC1", "\u0D05\u0D28\u0D41\u0D2C\u0D28\u0D4D\u0D27\u0D02",
        "\u0636\u0645\u06CC\u0645\u06C1", "\u0636\u0645\u064A\u0645\u0648",
    ),
    "appendix": (
        "appendix",
        "\u092A\u0930\u093F\u0936\u093F\u0937\u094D\u091F",
        "\u09AA\u09B0\u09BF\u09B6\u09BF\u09B7\u09CD\u099F", "\u09AA\u09F0\u09BF\u09B6\u09BF\u09B7\u09CD\u099F",
        "\u0AAA\u0AB0\u0ABF\u0AB6\u0ABF\u0AB7\u0ACD\u0A9F", "\u0A2A\u0A30\u0A3F\u0A38\u0A3C\u0A3F\u0A38\u0A3C\u0A1F", "\u0B2A\u0B30\u0B3F\u0B36\u0B3F\u0B37\u0B4D\u0B1F", "\u0CAA\u0CB0\u0CBF\u0CB6\u0CBF\u0CB7\u0CCD\u0C9F", "\u0C2A\u0C30\u0C3F\u0C36\u0C3F\u0C37\u0C4D\u0C1F\u0C02",
        "\u0BAA\u0BBF\u0BA9\u0BCD\u0BA9\u0BBF\u0BA3\u0BC8\u0BAA\u0BCD\u0BAA\u0BC1", "\u0D05\u0D28\u0D41\u0D2C\u0D28\u0D4D\u0D27\u0D02",
        "\u0636\u0645\u06CC\u0645\u06C1", "\u0636\u0645\u064A\u0645\u0648",
    ),
    "form": (
        "form",
        "\u092A\u094D\u0930\u092A\u0924\u094D\u0930", "\u0928\u092E\u0941\u0928\u093E", "\u092B\u093E\u0930\u093E\u092E",
        "\u09AB\u09B0\u09AE", "\u09AB\u09F0\u09AE",
        "\u0AAB\u0ACB\u0AB0\u0ACD\u0AAE", "\u0A2B\u0A3E\u0A30\u0A2E", "\u0B2B\u0B30\u0B4D\u0B2E", "\u0CA8\u0CAE\u0CC2\u0CA8\u0CC6", "\u0C28\u0C2E\u0C42\u0C28\u0C3E",
        "\u0BAA\u0B9F\u0BBF\u0BB5\u0BAE\u0BCD", "\u0D2B\u0D4B\u0D31\u0D02",
        "\u0641\u0627\u0631\u0645",
    ),
}


INDIC_COMBINING_MARK_CHARS = (
    "\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E4\u06E7-\u06E8\u06EA-\u06ED\u0900-\u0903\u093A-\u093C\u093E-\u094F\u0951-\u0957\u0962-\u0963\u0981-\u0983\u09BC\u09BE-\u09C4\u09C7-\u09C8\u09CB-\u09CD\u09D7\u09E2-\u09E3\u09FE\u0A01-\u0A03\u0A3C\u0A3E-\u0A42\u0A47-\u0A48\u0A4B-\u0A4D\u0A51\u0A70-\u0A71\u0A75\u0A81-\u0A83\u0ABC\u0ABE-\u0AC5\u0AC7-\u0AC9\u0ACB-\u0ACD\u0AE2-\u0AE3\u0AFA-\u0AFF\u0B01-\u0B03\u0B3C\u0B3E-\u0B44\u0B47-\u0B48\u0B4B-\u0B4D\u0B55-\u0B57\u0B62-\u0B63\u0B82\u0BBE-\u0BC2\u0BC6-\u0BC8\u0BCA-\u0BCD\u0BD7\u0C00-\u0C04\u0C3C\u0C3E-\u0C44\u0C46-\u0C48\u0C4A-\u0C4D\u0C55-\u0C56\u0C62-\u0C63\u0C81-\u0C83\u0CBC\u0CBE-\u0CC4\u0CC6-\u0CC8\u0CCA-\u0CCD\u0CD5-\u0CD6\u0CE2-\u0CE3\u0CF3\u0D00-\u0D03\u0D3B-\u0D3C\u0D3E-\u0D44\u0D46-\u0D48\u0D4A-\u0D4D\u0D57\u0D62-\u0D63"
)

def _term_alternation(key):
    words = sorted(set(LEGAL_TERM_TRANSLATIONS[key]), key=len, reverse=True)
    boundary_chars = r"\w" + INDIC_COMBINING_MARK_CHARS
    return (
        r"(?<![" + boundary_chars + r"])(?:" + "|".join(words) + r")(?![" + boundary_chars + r"])"
    )

def is_chapter(text):
        chapter_alt = _term_alternation("chapter")
        pattern = rf"""
            ^\s*

            {chapter_alt}

            \s*

            # optional separator before chapter number
            [\-–—:.\u2013\u2014]?
            \s*

            # chapter number
            (?P<number>
                \d+
                |
                {ROMAN_RE}
            )

            # optional separator after number
            \s*
            [\-–—:.\u2013\u2014.]?
            \s*

            # optional title
            (?P<title>.*?)

            \s*$
        """

        match = re.match(
            pattern,
            text,
            re.IGNORECASE | re.VERBOSE
        )

        if match:
            return  True, match.group("number"),match.group("title")
            

        return False, None, None
    
def is_part(text):

    part_alt = _term_alternation("part")
    pattern = rf"""
        ^\s*

        {part_alt}
        \s*

        # optional separator before part number
        [\-–—:.\u2013\u2014]?
        \s*

        # part identifier
        (
            \d+
            |
            [A-Z{INDIC_LETTER_CHARS}]+
            |
            {ROMAN_RE}
        )

        # optional separator after identifier
        \s*
        [\-–—:.\u2013\u2014.]?
        \s*

        # optional title
        (.*?)

        \s*$
    """

    match = re.match(
        pattern,
        text,
        re.IGNORECASE | re.VERBOSE
    )

    if match:
        return True, match.group(1), match.group(2)


    return False, None, None

def is_article(text):
    article_alt = _term_alternation("article")
    pattern = rf"""
        ^\s*{article_alt}              # word 'article'
        \s*                      # optional spaces
        [\-–—:.\u2013\u2014]?    # one optional separator
        \s*                      # optional spaces
        (\d+|{ROMAN_RE})    # number or roman
    """
    match = re.match(pattern, text, re.IGNORECASE | re.VERBOSE)
    if match:
        return True, match.group(1)
    return False, None


def is_schedule(text):

    ordinals = [
        "first", "second", "third", "fourth", "fifth", "sixth", "seventh",
        "eighth", "ninth", "tenth", "eleventh", "twelfth", "thirteenth",
        "fourteenth", "fifteenth", "sixteenth", "seventeenth", "eighteenth",
        "nineteenth", "twentieth"
    ]

    ordinals_re = r"(?:{})".format("|".join(ordinals))

    numbers_re = r"(?:[1-9" + INDIC_NONZERO_DIGIT_CHARS + r"][0-9" + INDIC_DIGIT_CHARS + r"]*)"

    schedule_alt = _term_alternation("schedule")
    pattern = rf"""
        ^\s*

        (?:the\s+)?                 # optional 'the'

        {schedule_alt}
        \s*

        # optional separator after schedule
        [\-–—:.\u2013\u2014]?
        \s*

        # optional schedule identifier
        (
            {ordinals_re}
            |
            {numbers_re}
            |
            {ROMAN_RE}
        )?

        \s*

        # optional separator after identifier
        [\-–—:.\u2013\u2014.]?
        \s*

        # optional title
        (.*?)

        \s*$
    """

    return bool(
        re.match(
            pattern,
            text,
            re.IGNORECASE | re.VERBOSE
        )
    )


def is_annexure(text):

    annexure_alt = _term_alternation("annexure")
    pattern = rf"""
        ^\s*

        (?:

            # ---------------------------------
            # CASE 1:
            # Annexure at beginning
            # ---------------------------------

            (?:\d+(?:\.\d+)*\s+)?     # optional numbering

            {annexure_alt}

            \s*

            [\-–—:.\u2013\u2014]?
            \s*

            (
                \d+
                |
                [A-Z{INDIC_LETTER_CHARS}]+
                |
                {ROMAN_RE}
            )?

            \s*

            [\-–—:.\u2013\u2014.]?
            \s*

            (.*?)

            |

            # ---------------------------------
            # CASE 2:
            # Annexure at end
            # ---------------------------------

            (.*?)

            \s*

            [\-–—:.\u2013\u2014]?
            \s*

            {annexure_alt}
            \s*

            (
                \d+
                |
                [A-Z{INDIC_LETTER_CHARS}]+
                |
                {ROMAN_RE}
            )

        )

        \s*$
    """

    return bool(
        re.match(
            pattern,
            text,
            re.IGNORECASE | re.VERBOSE
        )
    )

def is_appendix(text):

    appendix_alt = _term_alternation("appendix")
    pattern = rf"""
        ^\s*

        # optional numbering like:
        # 13
        # 13.1
        (?:\d+(?:\.\d+)*\s+)?

        {appendix_alt}
        \s*

        # optional separator
        [\-–—:.\u2013\u2014]?
        \s*

        # optional appendix identifier
        (
            \d+
            |
            [A-Z{INDIC_LETTER_CHARS}]+
            |
            {ROMAN_RE}
        )?

        \s*

        # optional separator after identifier
        [\-–—:.\u2013\u2014.]?
        \s*

        # optional title
        (.*?)

        \s*$
    """

    return bool(
        re.match(
            pattern,
            text,
            re.IGNORECASE | re.VERBOSE
        )
    )

def is_form(text):

    form_alt = _term_alternation("form")
    pattern = rf"""
        ^\s*

        # optional numbering like:
        # 13
        # 13.1
        (?:\d+(?:\.\d+)*\s+)?

        {form_alt}
        \s*

        # optional separator
        [\-–—:.\u2013\u2014]?
        \s*

        # optional form identifier
        (
            \d+
            |
            [A-Z{INDIC_LETTER_CHARS}]+
            |
            {ROMAN_RE}
        )?

        \s*

        # optional separator after identifier
        [\-–—:.\u2013\u2014.]?
        \s*

        # optional title
        (.*?)

        |

        # ---------------------------------
        # CASE 2:
        # title ending with "Form X"
        # ---------------------------------

        (.*?)

        \s*

        [\-–—:.\u2013\u2014]?
        \s*

        {form_alt}
        \s*

        (
            \d+
            |
            [A-Z{INDIC_LETTER_CHARS}]+
            |
            {ROMAN_RE}
        )

        \s*$

    """

    return bool(
        re.match(
            pattern,
            text,
            re.IGNORECASE | re.VERBOSE
        )
    )
