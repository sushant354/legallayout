# import os, re, json, unicodedata
# from io import BytesIO
# from pdfminer.pdfparser import PDFParser
# from pdfminer.pdfdocument import PDFDocument
# from pdfminer.pdftypes import resolve1
# from pdfminer.cmapdb import CMapDB
# from fontTools.ttLib import TTFont
# import logging

# class PDFEmbeddedFontMapper:
#     def __init__(self, pdf_path, out_dir="fonts"):
#         self.logger = logging.getLogger(__name__)
#         self.pdf_path = pdf_path
#         self.out_dir = out_dir
#         os.makedirs(out_dir, exist_ok=True)
#         self.font_maps = {}   

#     def extract_fonts(self):
#         with open(self.pdf_path, "rb") as f:
#             parser = PDFParser(f)
#             doc = PDFDocument(parser)
#             parser.set_document(doc)

#             for objid in doc.xrefs.get_object_ids():
#                 try:
#                     obj = resolve1(doc.getobj(objid))
#                     if not isinstance(obj, dict):
#                         continue

#                     font_id = f"font_{objid}"
#                     info = {"tounicode": {}, "cmap": {}, "cid": {}}

#                     for key in ("FontFile", "FontFile2", "FontFile3"):
#                         if key in obj:
#                             data = obj[key].get_data()
#                             path = os.path.join(self.out_dir, f"{font_id}.ttf")
#                             with open(path, "wb") as o:
#                                 o.write(data)
#                             info["cmap"] = self._extract_cmap(data)
#                             info["path"] = path

                   
#                     if "ToUnicode" in obj:
#                         stream = obj["ToUnicode"].get_data().decode("latin-1", "ignore")
#                         info["tounicode"] = self._parse_tounicode(stream)

                   
#                     if "DescendantFonts" in obj:
#                         desc = resolve1(obj["DescendantFonts"])[0]
#                         if "CIDSystemInfo" in desc:
#                             sysinfo = resolve1(desc["CIDSystemInfo"])
#                             cmap_name = f"{sysinfo.get('Registry','')}–{sysinfo.get('Ordering','')}"
#                             try:
#                                 cmap = CMapDB.get_cmap(cmap_name)
#                                 info["cid"] = {cid: chr(u) for cid, u in cmap.cid2unichr.items()}
#                             except Exception:
#                                 pass

#                     self.font_maps[font_id] = info

#                 except Exception:
#                     continue

#         self.logger.info(f"Extracted {len(self.font_maps)} font objects.")
#         self.logger.debug(json.dumps(self.font_maps, indent=2, ensure_ascii=False))
#         return self.font_maps

   
#     def _extract_cmap(self, font_bytes):
        
#         try:
#             tt = TTFont(BytesIO(font_bytes))
#             cmap = tt.getBestCmap() or {}
#             return {f"U+{code:04X}": name for code, name in cmap.items()}
#         except Exception:
#             return {}

   
#     def _parse_tounicode(self, txt):
       
#         mapping = {}
#         for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", txt):
#             try:
#                 mapping[f"U+{int(src,16):04X}"] = chr(int(dst,16))
#             except Exception:
#                 pass
#         return mapping

    
#     @staticmethod
#     def _normalize_font_name(name):
#         return name.split("+")[-1] if "+" in name else name

  
#     def resolve_char(self, font_name, raw_char):
        
#         ukey = f"U+{ord(raw_char):04X}"

#         for fobj, info in self.font_maps.items():
           
#             if ukey in info["tounicode"]:
#                 return info["tounicode"][ukey]
           
#             if ukey in info["cmap"]:
#                 gname = info["cmap"][ukey]
#                 if gname.startswith("uni") and len(gname) == 7:
#                     try:
#                         return chr(int(gname[3:], 16))
#                     except Exception:
#                         pass
#                 if gname.startswith("u") and len(gname) == 5:
#                     try:
#                         return chr(int(gname[1:], 16))
#                     except Exception:
#                         pass
#                 return gname
            
#             if ord(raw_char) in info.get("cid", {}):
#                 return info["cid"][ord(raw_char)]

        
#         return raw_char

#     def save_json(self, path="font_maps.json"):
#         with open(path, "w", encoding="utf-8") as f:
#             json.dump(self.font_maps, f, indent=2, ensure_ascii=False)
#         return path


# import os, re, json
# from io import BytesIO
# from pdfminer.pdfparser import PDFParser
# from pdfminer.pdfdocument import PDFDocument
# from pdfminer.pdftypes import resolve1
# from fontTools.ttLib import TTFont
# import logging

# try:
#     import fontforge
#     FONTFORGE_AVAILABLE = True
# except ImportError:
#     FONTFORGE_AVAILABLE = False


# class DynamicFontMapper:
#     def __init__(self, pdf_path, out_dir="fonts"):
#         self.pdf_path = pdf_path
#         self.out_dir = out_dir
#         self.logger = logging.getLogger(__name__)
#         os.makedirs(out_dir, exist_ok=True)
#         self.font_maps = {}

#     def extract_fonts(self):
#         with open(self.pdf_path, "rb") as f:
#             parser = PDFParser(f)
#             doc = PDFDocument(parser)
#             parser.set_document(doc)

#             object_ids = set()

#             if hasattr(doc, "xrefs"):
#                 if isinstance(doc.xrefs, list):
#                     for xref in doc.xrefs:
#                         try:
#                             object_ids.update(xref.get_object_ids())
#                         except Exception:
#                             continue
#                 else:
#                     try:
#                         object_ids.update(doc.xrefs.get_object_ids())
#                     except Exception:
#                         pass

#             for objid in object_ids:
#                 try:
#                     obj = resolve1(doc.getobj(objid))
#                     if not isinstance(obj, dict):
#                         continue

#                     font_id = f"font_{objid}"
#                     info = {"tounicode": {}, "cmap": {}, "fontfile": None}

#                     for key in ("FontFile", "FontFile2", "FontFile3"):
#                         if key in obj:
#                             data = obj[key].get_data()
#                             path = os.path.join(self.out_dir, f"{font_id}.ttf")
#                             with open(path, "wb") as o:
#                                 o.write(data)
#                             info["fontfile"] = path
#                             info["cmap"] = self._extract_cmap(data)

#                     if "ToUnicode" in obj:
#                         txt = obj["ToUnicode"].get_data().decode("latin-1", "ignore")
#                         info["tounicode"] = self._parse_tounicode(txt)

#                     self.font_maps[font_id] = info

#                 except Exception:
#                     continue

#         self.logger.info(f"Extracted {len(self.font_maps)} embedded fonts.")
#         self.logger.debug(json.dumps(self.font_maps, indent=2, ensure_ascii=False))
#         return self.font_maps

#     def _extract_cmap(self, font_bytes):
#         try:
#             tt = TTFont(BytesIO(font_bytes))
#             cmap = tt.getBestCmap() or {}
#             return {f"U+{code:04X}": name for code, name in cmap.items()}
#         except Exception:
#             return {}

#     def _parse_tounicode(self, txt):
#         """Parse /ToUnicode CMap into dictionary."""
#         mapping = {}
#         for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", txt):
#             try:
#                 mapping[f"U+{int(src,16):04X}"] = chr(int(dst,16))
#             except Exception:
#                 pass
#         return mapping


#     def _glyph_outline_signature(self, font_path, glyph):
#         if not FONTFORGE_AVAILABLE or not os.path.exists(font_path):
#             return None
#         try:
#             f = fontforge.open(font_path)
#             g = f[glyph]
#             if not g or g.isWorthOutputting() is False:
#                 return None
#             bounds = g.boundingBox()
#             signature = f"{len(g.foreground)}:{bounds}"
#             f.close()
#             return signature
#         except Exception:
#             return None


#     def resolve_char(self, font_name, raw_char):
#         ukey = f"U+{ord(raw_char):04X}"

#         for fobj, info in self.font_maps.items():
    
#             if ukey in info["tounicode"]:
#                 return info["tounicode"][ukey]
            
#             if ukey in info["cmap"]:
#                 gname = info["cmap"][ukey]
#                 if re.match(r"uni[0-9A-Fa-f]{4}", gname):
#                     return chr(int(gname[3:], 16))
#                 if re.match(r"u[0-9A-Fa-f]{4}", gname):
#                     return chr(int(gname[1:], 16))
#                 if len(gname) == 1:
#                     return gname

#             if FONTFORGE_AVAILABLE and info["fontfile"]:
#                 sig = self._glyph_outline_signature(info["fontfile"], f"uni{ord(raw_char):04X}")
#                 if sig:
#                     return raw_char

#         return raw_char


import os, re, json, logging
from io import BytesIO
from pdfminer.pdfparser import PDFParser
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdftypes import resolve1
from fontTools.ttLib import TTFont

try:
    import fontforge
    FONTFORGE_AVAILABLE = True
except ImportError:
    FONTFORGE_AVAILABLE = False


class DynamicFontMapper:
    def __init__(self, pdf_path, out_dir="fonts"):
        self.pdf_path = pdf_path
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.font_maps = {}
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)

    # ========================
    # Phase 1: Extract fonts
    # ========================
    def extract_fonts(self):
        with open(self.pdf_path, "rb") as f:
            parser = PDFParser(f)
            doc = PDFDocument(parser)
            parser.set_document(doc)

            object_ids = []
            try:
                # PDFDocument.xrefs can be list in some versions
                for xref in doc.xrefs:
                    object_ids.extend(xref.get_objids())
            except Exception:
                object_ids = range(1, len(doc.xrefs) + 1)

            for objid in object_ids:
                try:
                    obj = resolve1(doc.getobj(objid))
                    if not isinstance(obj, dict):
                        continue

                    fonts_found = self._extract_font_from_obj(obj, f"font_{objid}")
                    if fonts_found:
                        self.font_maps.update(fonts_found)

                except Exception as e:
                    self.logger.debug(f"Skipping obj {objid}: {e}")
                    continue

        self.logger.info(f"Extracted {len(self.font_maps)} embedded fonts.")
        return self.font_maps

    # ========================
    # Recursive font extractor
    # ========================
    def _extract_font_from_obj(self, obj, prefix):
        fonts = {}
        if not isinstance(obj, dict):
            return fonts

        info = {"tounicode": {}, "cmap": {}, "fontfile": None}
        print(obj, fonts)
        # Direct font file extraction
        for key in ("FontFile", "FontFile2", "FontFile3"):
            if key in obj:
                try:
                    data = obj[key].get_data()
                    path = os.path.join(self.out_dir, f"{prefix}.ttf")
                    with open(path, "wb") as o:
                        o.write(data)
                    info["fontfile"] = path
                    info["cmap"] = self._extract_cmap(data)
                    fonts[prefix] = info
                    return fonts
                except Exception:
                    pass

        # ToUnicode map extraction
        if "ToUnicode" in obj:
            try:
                txt = obj["ToUnicode"].get_data().decode("latin-1", "ignore")
                info["tounicode"] = self._parse_tounicode(txt)
            except Exception:
                pass

        # Recursive search for DescendantFonts or nested structures
        for k, v in obj.items():
            try:
                sub = resolve1(v)
                if isinstance(sub, dict):
                    fonts.update(self._extract_font_from_obj(sub, f"{prefix}_{k}"))
                elif isinstance(sub, list):
                    for i, it in enumerate(sub):
                        sub_res = resolve1(it)
                        if isinstance(sub_res, dict):
                            fonts.update(self._extract_font_from_obj(sub_res, f"{prefix}_{k}{i}"))
            except Exception:
                continue

        return fonts

    # ========================
    # Extract cmap + unicode maps
    # ========================
    def _extract_cmap(self, font_bytes):
        try:
            tt = TTFont(BytesIO(font_bytes))
            cmap = tt.getBestCmap() or {}
            return {f"U+{code:04X}": name for code, name in cmap.items()}
        except Exception:
            return {}

    def _parse_tounicode(self, txt):
        mapping = {}
        for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", txt):
            try:
                mapping[f"U+{int(src,16):04X}"] = chr(int(dst,16))
            except Exception:
                pass
        return mapping

    # ========================
    # FontForge outline matching (optional)
    # ========================
    def _glyph_outline_signature(self, font_path, glyph):
        if not FONTFORGE_AVAILABLE or not os.path.exists(font_path):
            return None
        try:
            f = fontforge.open(font_path)
            g = f[glyph]
            if not g or not g.isWorthOutputting():
                return None
            bounds = g.boundingBox()
            sig = f"{len(g.foreground)}:{bounds}"
            f.close()
            return sig
        except Exception:
            return None

    # ========================
    # Character resolution
    # ========================
    def resolve_char(self, font_name, raw_char):
        ukey = f"U+{ord(raw_char):04X}"

        for fobj, info in self.font_maps.items():
            # 1. ToUnicode map first
            if ukey in info["tounicode"]:
                return info["tounicode"][ukey]

            # 2. Try cmap
            if ukey in info["cmap"]:
                gname = info["cmap"][ukey]
                if re.match(r"uni[0-9A-Fa-f]{4,6}", gname):
                    return chr(int(gname[3:], 16))
                if re.match(r"u[0-9A-Fa-f]{4}", gname):
                    return chr(int(gname[1:], 16))
                if len(gname) == 1:
                    return gname

            # 3. Optional: try FontForge visual matching
            if FONTFORGE_AVAILABLE and info["fontfile"]:
                sig = self._glyph_outline_signature(info["fontfile"], f"uni{ord(raw_char):04X}")
                if sig:
                    return raw_char

        # fallback: raw char
        return raw_char
