import re

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