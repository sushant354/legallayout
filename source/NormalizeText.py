NORMALIZE_MAP = {
    # Bullets & list markers
    "": "•",    # U+F0B7 (PUA) → Bullet
    "∙": "•",    # U+2219 → Bullet operator
    "◦": "•",    # U+25E6 → Small circle
    "●": "•",    # U+25CF → Black circle
    "": "•",    # U+F0DF (PUA) → Bullet
    "": "▪",    # U+F0E8 (PUA) → Small square
    "■": "▪",    # U+25A0 → Black square
    "□": "▫",    # U+25A1 → White square
    "▪": "▪",    # U+25AA → Small square
    "▫": "▫",    # U+25AB → White small square

    # Arrows
    "": "→",    # U+F0E0 → Right arrow
    "": "⇨",    # U+F0DC → Heavy right arrow
    "": "←",    # U+F0D9 → Left arrow
    "": "→",    # U+F0DA → Right arrow
    "": "↔",    # U+F0DB → Left-right arrow
    "⇒": "→",    # Double arrow → normalize
    "➔": "→",    # Dingbat arrow
    "➤": "→",    # Black arrowhead
    "➝": "→",    # Long arrow
    "←": "←",    # Left arrow
    "→": "→",    # Right arrow
    "↑": "↑",    # Up arrow
    "↓": "↓",    # Down arrow

    # Quotes (normalize to ASCII)
    "“": "\"", "”": "\"", "‟": "\"", "〝": "\"", "〞": "\"",
    "«": "\"", "»": "\"",
    "‘": "'",  "’": "'",  "‚": "'",  "‛": "'",

    # # Dashes / Hyphens
    # "–": "-",    # En dash
    # "—": "-",    # Em dash
    # "―": "-",    # Horizontal bar
    # "−": "-",    # Minus sign
    # "-": "-",    # Non-breaking hyphen
    # "‒": "-",    # Figure dash

    # # Ellipsis
    # "…": "...",  # Horizontal ellipsis
    # "⋯": "...",  # Midline ellipsis
    # "⋮": "...",  # Vertical ellipsis (rare)

    # Stars & checkmarks
    "★": "*",    # Black star
    "☆": "*",    # White star
    "✦": "*", "✧": "*",  # Fancy stars
    "✪": "*", "✫": "*",  # Circle stars
    "✔": "✓",    # Checkmark
    "✓": "✓",    # Checkmark (normalized)
    "✗": "✕",    # Cross mark
    "✘": "✕",    # Cross mark
    "☑": "✓",    # Ballot box with check
    "☒": "✕",    # Ballot box with X
    "√": "✓",    # Sometimes used as check

    # Section / paragraph marks
    "§": "§",    # Section sign
    "¶": "¶",    # Paragraph mark
    "¤": "¤",    # Currency generic

    # Currency
    "₹": "₹",    # Indian Rupee
    "$": "$",    # Dollar
    "€": "€",    # Euro
    "£": "£",    # Pound
    "¥": "¥",    # Yen/Yuan

    # # Miscellaneous symbols
    # "○": "o",    # Circle → o
    # "◯": "o",    # Large circle
    # "◉": "o",    # Fisheye
    # "□": "[]",   # Empty box
    # "☐": "[]",   # Ballot box
    # "❑": "[]",   # Square
    # "❒": "[]",   # Square
    # "■": "[]",   # Black square
    # "▲": "^",    # Up triangle
    # "▼": "v",    # Down triangle
    # "►": ">",    # Right triangle
    # "◄": "<",    # Left triangle

    # OCR garbage / placeholders
    "�": "",     # Replacement character
    "": "", "": "", "": "", "": "",  # PUA junk from OCR fonts
}

class NormalizeText():
    def __init__(self):
        pass
    
    def normalize_text(self, text):
      if not isinstance(text, str):
          return text
      for bad, good in NORMALIZE_MAP.items():
          text = text.replace(bad, good)
      return text