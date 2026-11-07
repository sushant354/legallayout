import io
import logging
import re

import pymupdf
from fontTools.ttLib import TTFont

SUBSET_PREFIX_RE = re.compile(r'^[A-Z]{6}\+')
BOLD_NAME_RE = re.compile(r'bold|demi|heavy|black', re.IGNORECASE)
ITALIC_NAME_RE = re.compile(r'italic|oblique|slanted', re.IGNORECASE)
TYPE1_WEIGHT_RE = re.compile(r'/Weight\s*\(([^)]*)\)')
TYPE1_NAME_RE = re.compile(r'/(?:FullName|FontName)\s*[(/]([^)\s]*)')

BOLD_WEIGHT = 700
OS2_STYLE_BITS = 0x61
TYPE1_HEADER_BYTES = 8192


def normalize_font_name(name):
    return SUBSET_PREFIX_RE.sub('', name or '')


def style_from_name(name):
    style = ''
    if BOLD_NAME_RE.search(name or ''):
        style += 'b'
    if ITALIC_NAME_RE.search(name or ''):
        style += 'i'
    return style


class FontStyleReader:

    def __init__(self, pdf_path):
        self.logger = logging.getLogger(__name__)
        self.pdf_path = pdf_path
        self.styles = {}

    def read(self):
        try:
            doc = pymupdf.open(self.pdf_path)
        except Exception as e:
            self.logger.warning("[!] Could not open %s to read font styles: %s", self.pdf_path, e)
            return {}

        try:
            self.read_declared_fonts(doc)
        except Exception as e:
            self.logger.warning("[!] Could not read the font styles of %s: %s", self.pdf_path, e)
        finally:
            doc.close()

        return {name: ''.join(sorted(style, key='bi'.index)) for name, style in self.styles.items() if style}

    def add(self, name, style):
        if not style:
            return
        key = normalize_font_name(name)
        self.styles[key] = ''.join(sorted(set(self.styles.get(key, '')) | set(style), key='bi'.index))

    def read_declared_fonts(self, doc):
        seen = set()
        for pagenum in range(doc.page_count):
            for font in doc[pagenum].get_fonts(full=True):
                xref, ext, base_name = font[0], font[1], font[3]
                if xref in seen:
                    continue
                seen.add(xref)
                style = style_from_name(base_name)
                style += self.style_from_program(doc, xref, ext)
                self.add(base_name, style)

    def style_from_program(self, doc, xref, ext):
        if ext not in ('ttf', 'otf', 'cff', 'pfa', 'pfb', 'ttc'):
            return ''
        try:
            buffer = doc.extract_font(xref)[3]
        except Exception:
            return ''
        if not buffer:
            return ''
        if ext in ('ttf', 'otf', 'ttc'):
            return self.style_from_sfnt(buffer)
        if ext == 'cff':
            return self.style_from_cff(buffer)
        return self.style_from_type1(buffer)

    @staticmethod
    def style_from_sfnt(buffer):
        try:
            font = TTFont(io.BytesIO(buffer), lazy=True, fontNumber=0)
            mac_style = font['head'].macStyle if 'head' in font else 0
            has_os2 = 'OS/2' in font
            fs_selection = font['OS/2'].fsSelection if has_os2 else 0
            weight = font['OS/2'].usWeightClass if has_os2 else 0
            names = ' '.join(
                str(record) for record in font['name'].names
                if record.nameID in (2, 4, 17) and record.platformID in (1, 3)
                and (record.platformID == 1 or record.langID == 0x409)
            ) if 'name' in font else ''
        except Exception:
            return ''
        style = ''
        if has_os2 and fs_selection & OS2_STYLE_BITS:
            bold = fs_selection & 0x20 or weight >= BOLD_WEIGHT
            italic = fs_selection & 1
        else:
            bold = mac_style & 1
            italic = mac_style & 2
        if bold or BOLD_NAME_RE.search(names):
            style += 'b'
        if italic or ITALIC_NAME_RE.search(names):
            style += 'i'
        return style

    @staticmethod
    def style_from_cff(buffer):
        try:
            from fontTools.cffLib import CFFFontSet
            cff = CFFFontSet()
            cff.decompile(io.BytesIO(buffer), None)
            top = cff[cff.fontNames[0]]
            text = ' '.join(str(getattr(top, key, '') or '') for key in ('Weight', 'FullName', 'FontName'))
        except Exception:
            return ''
        return style_from_name(text)

    @staticmethod
    def style_from_type1(buffer):
        header = buffer[:TYPE1_HEADER_BYTES].decode('latin-1', 'ignore')
        weight = TYPE1_WEIGHT_RE.search(header)
        name = TYPE1_NAME_RE.search(header)
        text = ' '.join(m.group(1) for m in (weight, name) if m)
        return style_from_name(text)
