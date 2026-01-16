import re
from typing import Optional, Tuple


BBox = Tuple[float, float, float, float]

LEGAL_ABBREVIATIONS= {
            # People / titles
            "Dr.", "Mr.", "Mrs.", "Ms.", "Hon.", "J.", "JJ.", "CJ.", "CJI.",

            # Generic writing
            "e.g.", "i.e.", "etc.", "viz.", "cf.", "ibid.", "supra.", "infra.", "op. cit.",

            # Legal structure markers
            "Sec.", "Art.", "Cl.", "Sch.", "Ch.", "Pt.", "Sub-sec.", "Sub-cl.", "Reg.", 
            "Rule.", "S.O.", "G.O.", "N.O.", "O.M.", "SRO.",

            # Statute / Act references
            "Act.", "Const.", "Code.", "Ordin.", "Regd.", "Notif.", "Gaz.",

            # Corporate / institutional
            "Co.", "Ltd.", "Pvt.", "Inc.", "Corp.", "Univ.", "Dept.", "Assn.",

            # Case citations (Indian & foreign)
            "AIR", "SCC", "SCR", "SCC (Cri.)", "SCC (L&S)", "SCC (Tax)", 
            "All ER", "WLR", "USC", "F. Supp.", "F.2d", "F.3d",

            # Domain-specific shorthand
            "XRH.", "SEBI.", "RBI.", "CBI.", "CBDT.", "ITAT.", "NCLT.", "NCLAT.", "HC.", "SC.",

            # Special references
            "No.", "pp.", "para.", "cl.", "art.", "reg.", "sch.", "Vol.", "Ed.", "Ch."
        }

class LegalSentenceDetector:
    
    def __init__(self):
        self._abbr_clean = {abbr.lower() for abbr in LEGAL_ABBREVIATIONS}
        self.same_line_tolerance = 0.25

    def is_real_sentence_end(self, text, next_text, at_page_end, text_tb, next_text_tb, pg_height, pg_width):
      """
      Detects the end of sentences in legal documents, accounting for legal text complexities.
      Handles:
      - Legal abbreviations (Dr., Ld. Adv., Sec., Co., Ltd., SCC, Exh.)
      - Citations and references (ILR 1951 480, (2004) 4 SCC 2036, 2012 SCC online Del 4864)
      - Numbered/bulleted lists ((1), 1., (a), i., 10.)
      - Section/subsection references (2., 2.1., Sec. 33, Exh. 101.)
      - Internal tokens and acronyms (SEBI., RBI., 1.23)
      - Lookahead context checks
      - Page boundary handling (don’t force close paragraph at page end)
      """
      
      same_line_status = self.is_on_same_line(text_tb, next_text_tb)
      if same_line_status:
          return False 
      
      if not same_line_status and self.indent_check(text_tb, next_text_tb, pg_width):
          return True
    
      if not text:
          return False

      s = text.strip()
      if not s:
          return False
      
      if re.fullmatch(r'\d+(?:\.\d+)*\.', s):
        return False
      pure_bullet_patterns = [
          re.compile(r'^\(?\d+[A-Z]?\)?[.)]?$'),   # (1), 1A., 2)
          re.compile(r'^\(?[ivxlcdm]+\)?[.)]?$' , re.I), # (iv), ii.
          re.compile(r'^\(?[a-z]\)?[.)]?$' , re.I),      # (a), b.
      ]
      for pattern in pure_bullet_patterns:
          if pattern.fullmatch(s):
              return False
        
      # Continuation punctuation (:-, ---, ...)
      continuation_punct = [":-", "---", "...", '—', '…'] #'".','."',"'.",".'"]
      for cp in continuation_punct:
          if s.endswith(cp):
              return False if at_page_end else True

      # Special colon handling
      if s.endswith(':'):
          if next_text:
              nxt_clean = next_text.strip()
              if re.match(r'^\(?[a-z0-9ivxlcdm]+\)', nxt_clean, re.I):  # bullet-like
                  return True
          return False if at_page_end else True
      

      # 1. SENTENCE-ENDING PUNCTUATION CHECK
      sentence_end_pattern = re.compile(r'.*[.?!:;]\s*$')
      if not sentence_end_pattern.match(s):
          return False

      last_token_match = re.search(r'(\S+?)([.?!:;]+)\s*$', s)
      if not last_token_match:
          return False if at_page_end else True

      last_token = last_token_match.group(1)
      trailing_punct = last_token_match.group(2)

      # 2. LIST-LIKE END TOKENS
      list_boundary_patterns = [
          re.compile(r'^\(\s*\d+\s*\)$'),
          re.compile(r'^\(\s*[a-z]\s*\)$', re.I),
          re.compile(r'^\(\s*[ivxlcdm]+\s*\)$', re.I),
          re.compile(r'^\d+\.$'),
          re.compile(r'^[a-z]\.$', re.I),
          re.compile(r'^[ivxlcdm]+\.$', re.I),
      ]
      clean_token = re.sub(r'[^\w\(\)]', '', last_token).lower()
      for pattern in list_boundary_patterns:
          if pattern.match(last_token.strip()) or pattern.match(clean_token):
              return False

      # 3. LEGAL CITATIONS
      citation_patterns = [
          re.compile(r'\b(ILR|SCC|SCR|AIR|CrLJ|DLT|Mad|Cal|Bom|All|Ker|Guj|MP|Raj)\s+\d{4}\s+\d+\b'),
          re.compile(r'\(\d{4}\)\s+\d+\s+(SCC|SCR|AIR|CrLJ|DLT|Mad|Cal|Bom|All|Ker|Guj|MP|Raj)\s+\d+'),
          re.compile(r'\d{4}\s+(SCC|SCR|AIR|CrLJ|DLT|Mad|Cal|Bom|All|Ker|Guj|MP|Raj)\s+online\s+\w+\s+\d+'),
          re.compile(r'\[\d{4}\]\s+\d+\s+\w+\s+\d+'),
          re.compile(r'\d{4}\s+\(\d+\)\s+\w+\s+\d+'),
          re.compile(r'Vol\.\s*\d+.*p\.\s*\d+', re.I),
          re.compile(r'pp\.\s*\d+[-–]\d+', re.I),
      ]
      for pattern in citation_patterns:
          if pattern.search(s):
              if next_text and next_text.strip():
                  nxt_clean = next_text.strip()
                  if nxt_clean[0].islower():
                      return False
                  else:
                      return True
              return False   

      # 4. DECIMALS & ACRONYMS
      if re.match(r'^\d+\.\d+$', last_token):
          return False
      if re.match(r'^[A-Z]+(\.[A-Z]+)+\.?$', last_token, re.I):
          return False
      if re.match(r'^(SEBI|RBI|CBDT|ITAT|NCLT|NCLAT|CBI|ED|FIU|MCA|ROC|DIN|PAN|TAN|GST|CGST|SGST|IGST|UTI|LIC|SBI|HDFC|ICICI|AXIS)\.$', last_token, re.I):
          return False

      # 5. SECTION/EXHIBIT REFERENCES
      section_patterns = [
          re.compile(r'^(Sec|Section|Art|Article|Rule|Cl|Clause|Para|Paragraph|Sub-sec|Sub-cl|Sch|Schedule|Ch|Chapter|Pt|Part)\.\s*\d+$', re.I),
          re.compile(r'^\d+\.$'),
          re.compile(r'^\d+\.\d+\.$'),
          re.compile(r'^\d+\.\d+\.\d+\.$'),
          re.compile(r'^(Sec|Section|Art|Article|Rule)\s+\d+\.$', re.I),
          re.compile(r'^(Exh|Exhibit|Ex)\.?\s*\d+[A-Za-z]*\.?$', re.I),
      ]
      for pattern in section_patterns:
          if pattern.match(last_token.strip()):
              return False

      # 6. LEGAL ABBREVIATIONS
      clean_token_for_abbr = re.sub(r'[^\w]', '', last_token).lower()
      abbr_variants = [
          clean_token_for_abbr + '.',
          last_token.lower(),
          clean_token_for_abbr,
          last_token.lower().rstrip('.')
      ]
      for abbr in abbr_variants:
          if abbr in self._abbr_clean:
              return False

      extended_legal_abbrevs = {
          'ld.', 'learned', 'adv.', 'advocate', 'sr.', 'senior', 'jr.', 'junior',
          'retd.', 'retired', 'addl.', 'additional', 'asstt.', 'assistant',
          'govt.', 'government', 'dept.', 'department', 'min.', 'ministry',
          'commr.', 'commissioner', 'collr.', 'collector', 'dist.', 'district',
          'tehsildar', 'sdo', 'bdo', 'ceo', 'cfo', 'cmd', 'md', 'gm', 'dgm',
          'exh.', 'ex.', 'exhibit', 'v/s.', 'vs.', 'v/s', 'ors', 'ors.'
      }
      for abbr_variant in abbr_variants:
          if abbr_variant in extended_legal_abbrevs:
              return False

      # 7. LOOKAHEAD CHECK
      if next_text is not None:
          nxt = next_text.strip()
          if nxt:
              nxt_clean = re.sub('^[\'"\\u00AB\\u00BB\\[\\(\\{\\s]+', '', nxt)
              if nxt_clean:
                  
                  if re.fullmatch(r'\d+(?:\.\d+)*\.', nxt_clean):
                        return True
                  if re.search(r'[.?!:;][\'"”’)]*$', s) and re.match(r'^[\'"“”‘’]', nxt):
                      return True

                  # Rule 1: punctuation + uppercase → True
                  if trailing_punct and nxt_clean and nxt_clean[0].isupper():
                      return True

                  # 🔧 NEW Rule: punctuation + bullet → True
                  if trailing_punct  and re.match(r'^\(?[a-z0-9ivxlcdm]+\)', nxt_clean, re.I):
                      return True

                  # Rule 2: punctuation + next bullet form → True
                  bullet_start_patterns = [
                      re.compile(r'^\d+\.$'),
                      re.compile(r'^\(\d+\)$'),
                      re.compile(r'^\d+\)$'),
                      re.compile(r'^[a-z]\.$', re.I),
                      re.compile(r'^\([a-z]\)$', re.I),
                      re.compile(r'^[ivxlcdm]+\.$', re.I),
                      re.compile(r'^\([ivxlcdm]+\)$', re.I),
                      re.compile(r'^\s*\d+[A-Z]*(?:-[A-Z]+)?\s*\.\s*\S*', re.IGNORECASE),
                      re.compile(r'^\(\s*([^\s\)]+)\s*\)\s*\S*', re.IGNORECASE),
                      re.compile(r'^(\s*\d+[A-Z]*(?:-[A-Z]+)?\.\s*)(.*)', re.IGNORECASE)
                  ]
                  if trailing_punct:
                      for pattern in bullet_start_patterns:
                          if pattern.match(nxt_clean):
                              return True

                  # If next is a pure bullet
                  for pattern in pure_bullet_patterns:
                      if pattern.match(nxt_clean.split()[0]):
                          return True

                  # Section headings
                  section_start_patterns = [
                      re.compile(r'^\d+\s*\(\d+\)'),
                      re.compile(r'^(Sec|Section|Art|Article|Rule|Cl|Clause|Para|Paragraph|Sub-sec|Sub-cl|Sch|Schedule|Ch|Chapter|Pt|Part)\s+\d+', re.I),
                      re.compile(r'^\d+\.\s+[A-Z]'),
                      re.compile(r'^\d+\.\d+\s'),
                  ]
                  for pattern in section_start_patterns:
                      if pattern.match(nxt_clean):
                          return True

                  # Continuations (don’t split)
                  continuation_patterns = [
                      re.compile(r'^\d+'),
                      re.compile(r'^[ivxlcdmIVXLCDM]+[.)\]\}]'),
                      re.compile(r'^\([a-z0-9ivxlcdm]+\)', re.I),
                      re.compile(r'^[a-z0-9ivxlcdm]+\.', re.I),
                      re.compile(r'^\('),
                  ]
                  for pattern in continuation_patterns:
                      if pattern.match(nxt_clean):
                          return False
          return True
      # 🔧 Patch 2: At page end → don’t force False
      return True

    def is_on_same_line(self, text_tb, next_text_tb):
            """
            Decide whether two textboxes are on the same line.
            Uses last char of text_tb and first char of next_text_tb if available,
            otherwise falls back to bbox/coords.
            """

            # Try to get char-level boxes
            last_char = getattr(text_tb, "get_last_char_coords", lambda: None)()
            next_first_char = getattr(next_text_tb, "get_first_char_coords", lambda: None)()

            # Fallback to textbox-level coords
            box1 = last_char or getattr(text_tb, "coords", None) or getattr(text_tb, "bbox", None)
            box2 = next_first_char or getattr(next_text_tb, "coords", None) or getattr(next_text_tb, "bbox", None)

            if not box1 or not box2:
                return False  # cannot decide → assume not same line

            # Normalize
            x0a, y0a, x1a, y1a = self._normalize_bbox(box1)
            x0b, y0b, x1b, y1b = self._normalize_bbox(box2)

            # Heights and midlines
            h1 = max(1.0, y1a - y0a)
            h2 = max(1.0, y1b - y0b)
            mid1 = (y0a + y1a) / 2
            mid2 = (y0b + y1b) / 2

            # 1. Baseline alignment: check vertical distance between midlines
            avg_height = (h1 + h2) / 2
            baseline_gap = abs(mid1 - mid2)
            if baseline_gap > avg_height * 0.35:  # dynamic tolerance
                return False

            # 2. Vertical overlap (secondary check)
            vertical_overlap = min(y1a, y1b) - max(y0a, y0b)
            min_height = min(h1, h2)
            if vertical_overlap / min_height < (1 - self.same_line_tolerance):
                return False

            # 3. Horizontal order: next should start after current
            horizontal_gap = x0b - x1a
            if horizontal_gap < -1:  # overlapping in x
                return False
            if horizontal_gap > avg_height * 3:  
                # Too big gap → probably next line
                return False

            return True

    def _normalize_bbox(self, box: BBox):
        if not box or len(box) != 4:
            raise ValueError(f"Invalid BBox: {box}")

        x0, y0, x1, y1 = box
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    
    def check_lastcharoftext_firstcharofnexttext(self, s, next_text):
        if not next_text:
            return False
        next_text = next_text.strip()
        if not next_text:
            return False
        last_char = s[-1]
        first_char = next_text[0]

        if last_char.isalpha() and first_char.isalpha():
            if last_char.islower() and first_char.isupper():
                return True
        return False


    def indent_check(self, text_tb, next_text_tb, pg_width):
            # Attempt to get character-level bounding boxes
            box1 = getattr(text_tb, "get_last_char_coords", lambda: None)() \
                or getattr(text_tb, "coords", None) \
                or getattr(text_tb, "bbox", None)

            box2 = getattr(next_text_tb, "get_first_char_coords", lambda: None)() \
                or getattr(next_text_tb, "coords", None) \
                or getattr(next_text_tb, "bbox", None)

            if not box1:
                return False
            
            if not box2:
                text = text_tb.extract_text_from_tb().strip()
                if text.endswith((":-", "---", "...", '—', '…','.','?','!',':',';')):
                    return True
                return False
            
            x0a, _, x1a, _ = self._normalize_bbox(box1)
            x0b, _, _, _ = self._normalize_bbox(box2)

            # Condition 1: First box ends ≥ 30% of page width from right margin
            right_gap_a = pg_width - x1a
            if right_gap_a >= 0.2 * pg_width:
                return True

            # Condition 2: Second box starts ≥ 40% of page width from left margin
            if x0b >= 0.35 * pg_width:
                return True

            return False


class SentenceMaker:
    def clean_text(self, raw_text):
        lines = [l.strip() for l in raw_text.split("\n") if l.strip()]
        merged = self._merge_lines(lines)
        return [self._normalize_punctuation(line) for line in merged]
    
    def _is_list_marker(self, line: str) -> bool:
        l = line.strip()

        bullet_patterns = [
            r"^[\-•●▪♦▫]{1,3}$",
            r"^[\-•●▪♦▫]{1,3}\s+.+"
        ]
        for p in bullet_patterns:
            if re.match(p, l):
                return True

        numeric_patterns = [
            r"^\d+$",                     # 1
            r"^\d+\.$",                   # 1.
            r"^\d+\)$",                   # 1)
            r"^\(\d+\)$",                 # (1)
            r"^\d+\.\d+$",                # 1.1
            r"^\d+(?:\.\d+){2,}$",        # 1.1.1, 2.4.10.3
            r"^\(\d+(?:\.\d+)+\)$",       # (1.1.1)
        ]
        for p in numeric_patterns:
            if re.match(p, l):
                return True


        alpha_patterns = [
            r"^[A-Za-z]\.$",              # a., A.
            r"^[A-Za-z]\)$",              # a), A)
            r"^\([A-Za-z]\)$",            # (a), (A)
        ]
        for p in alpha_patterns:
            if re.match(p, l):
                return True

        roman = r"(?:i|ii|iii|iv|v|vi|vii|viii|ix|x|xi|xii|xiii|xiv|xv|xvi|xvii|xviii|xix|xx|xl|l|c|d|m)"
        roman_patterns = [
            rf"^{roman}\.$",
            rf"^{roman}\)$",
            rf"^\({roman}\)$"
        ]
        for p in roman_patterns:
            if re.match(p, l, re.IGNORECASE):
                return True

        return False

    def _ends_sentence(self, line: str) -> bool:
        return bool(re.search(r"[.!?]$", line.strip()))

    def _is_title_like(self, line: str) -> bool:
        words = line.split()
        if not words:
            return False
        count = sum(1 for w in words if w[:1].isupper())
        return count >= len(words) * 0.6

    def _is_fragment(self, line: str) -> bool:
        if len(line.split()) <= 2:
            return True
        if not re.search(r"[,.!?;:]", line):
            return True
        return False

    def _should_merge(self, prev: str, curr: str) -> bool:
        prev = prev.strip()
        curr = curr.strip()

        if self._is_list_marker(curr):
            return False

        if self._ends_sentence(prev):
            return False

        if curr[:1].islower():
            return True

        if self._is_fragment(prev):
            return True

        if self._is_title_like(prev) and self._is_title_like(curr):
            return True

        if re.search(r"[a-zA-Z]", prev) and not self._ends_sentence(prev):
            return True

        return False

    def _merge_lines(self, lines):
        merged = []
        buffer = ""

        for line in lines:
            if not buffer:
                buffer = line
                continue

            if self._should_merge(buffer, line):
                buffer += " " + line
            else:
                merged.append(buffer)
                buffer = line

        if buffer:
            merged.append(buffer)

        return merged

    def _normalize_punctuation(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text)        # collapse spaces
        text = re.sub(r"\s+([,.;:])", r"\1", text)  # remove space before punctuation
        text = re.sub(r",(\S)", r", \1", text)  # ensure space after commas
        return text.strip()