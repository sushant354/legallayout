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

def is_chapter(text):
        pattern = rf"""
            ^\s*

            \b(?:chapter|section)\b
            
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

    pattern = rf"""
        ^\s*

        \bpart\b
        \s*

        # optional separator before part number
        [\-–—:.\u2013\u2014]?
        \s*

        # part identifier
        (
            \d+
            |
            [A-Z]+
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
    pattern = rf"""
        ^\s*\barticle\b              # word 'article'
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

    numbers_re = r"(?:[1-9][0-9]*)"

    pattern = rf"""
        ^\s*

        (?:the\s+)?                 # optional 'the'

        \bschedule\b
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

    pattern = rf"""
        ^\s*

        (?:

            # ---------------------------------
            # CASE 1:
            # Annexure at beginning
            # ---------------------------------

            (?:\d+(?:\.\d+)*\s+)?     # optional numbering

            \bannexures?\b

            \s*

            [\-–—:.\u2013\u2014]?
            \s*

            (
                \d+
                |
                [A-Z]+
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

            \bannexure\b
            \s*

            (
                \d+
                |
                [A-Z]+
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

    pattern = rf"""
        ^\s*

        # optional numbering like:
        # 13
        # 13.1
        (?:\d+(?:\.\d+)*\s+)?

        \bappendix\b
        \s*

        # optional separator
        [\-–—:.\u2013\u2014]?
        \s*

        # optional appendix identifier
        (
            \d+
            |
            [A-Z]+
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

    pattern = rf"""
        ^\s*

        # optional numbering like:
        # 13
        # 13.1
        (?:\d+(?:\.\d+)*\s+)?

        \bform\b
        \s*

        # optional separator
        [\-–—:.\u2013\u2014]?
        \s*

        # optional form identifier
        (
            \d+
            |
            [A-Z]+
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

        \bform\b
        \s*

        (
            \d+
            |
            [A-Z]+
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