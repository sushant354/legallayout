import logging
import re
import unicodedata
from collections import deque

class Amendment:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.isAmendmentPDF = False
        self.quote_stack = []
    
    # --- func to classify the textbox if it is detected with sign of amendments properties ---
    def check_for_amendment_acts(self, page): #,startPage,endPage):
        for tb in page.all_tbs.keys():
            try:
                text = tb.extract_text_from_tb().strip()
            except Exception as e:
                self.logger.warning(f"Failed to extract text from textbox on page {getattr(page, 'pg_num', '?')}: {e}")
                continue

            text = text.replace('“', '"').replace('”', '"').replace('‘‘','"').replace('’’','"').replace('‘', "'").replace('’', "'")

            try:
                label = page.all_tbs[tb]
            except Exception as e:
                self.logger.warning(f"Failed to retrieve label for textbox on page {getattr(page, 'pg_num', '?')}: {e}")
                continue

            try:
                page_num = int(page.pg_num)
            except Exception as e:
                self.logger.error(f"Invalid page number: {page.pg_num}")
                continue
            
            if ((label is not None and isinstance(label, tuple) and label[0] == "table") \
              or (label is None)): #and startPage is not None and endPage is not None and  startPage <= page_num <= endPage:
                doubleQuote_count = text.count('"')
                singleQuote_count = text.count("'") 
                self.logger.debug(f"Page {page.pg_num}, Text: '{text}'")
                self.logger.debug(f"Quote counts — Double: {doubleQuote_count}, Single: {singleQuote_count}")
                self.logger.debug(f"Quote Stack: {self.quote_stack}")
                

                try:
                    # Check for self-contained quotes
                    if ((text.startswith('"') and (text.endswith('".') or text.endswith('";') or \
                                                   text.endswith('."') or text.endswith(';"') or \
                                                   text.endswith('". and') or text.endswith('." and') or \
                                                   text.endswith(';" and') or text.endswith('"; and') or \
                                                   text.endswith('". or') or text.endswith('." or') or \
                                                   text.endswith(';" or') or text.endswith('"; or'))) or \
                        (text.startswith("'") and (text.endswith("'.") or text.endswith("';") or \
                                               text.endswith('.\'') or text.endswith(';\'') or \
                                               text.endswith('\'. and') or text.endswith('.\' and') or \
                                               text.endswith(';\' and') or text.endswith('\'; and') or \
                                               text.endswith('\'. or') or text.endswith('.\' or') or \
                                               text.endswith(';\' or') or text.endswith('\'; or')))):
                        self.isAmendmentPDF = True
                        self.logger.debug(f"Detected self-contained quote on page {page.pg_num}. Marked as amendment PDF.")
                        if label is not None and isinstance(label, tuple) and label[0] == "table":
                            continue
                        page.all_tbs[tb] = ["amendment"]
                        

                    # Check for opening quote
                    elif  (text.startswith('"')) and (doubleQuote_count%2!=0):
                        self.quote_stack.append(text[0])
                        self.logger.debug(f"Detected opening quote with imbalance on page {page.pg_num}. Pushed to quote_stack.")
                        self.isAmendmentPDF = True
                        if label is not None and isinstance(label, tuple) and label[0] == "table":
                            continue
                        page.all_tbs[tb] = ["amendment"]
                    
                    elif  (text.startswith("'")) and (singleQuote_count%2!=0):
                        self.quote_stack.append(text[0])
                        self.logger.debug(f"Detected opening quote with imbalance on page {page.pg_num}. Pushed to quote_stack.")
                        self.isAmendmentPDF = True
                        if label is not None and isinstance(label, tuple) and label[0] == "table":
                            continue
                        page.all_tbs[tb] = ["amendment"]
                        

                    # Check for closing quote
                    elif self.quote_stack and self.quote_stack[-1] == "'" and singleQuote_count%2!=0 and \
                        (text.endswith(self.quote_stack[-1] + ".") or text.endswith(self.quote_stack[-1] + ";") or\
                         text.endswith(self.quote_stack[-1] + ". and") or text.endswith(self.quote_stack[-1] + "; and") or \
                         text.endswith(self.quote_stack[-1] + ". or") or text.endswith(self.quote_stack[-1] + "; or") or\
                         text.endswith("."+self.quote_stack[-1]) or text.endswith(";"+self.quote_stack[-1]) or\
                         text.endswith("."+self.quote_stack[-1]+" and") or text.endswith(";"+self.quote_stack[-1]+" and") or\
                         text.endswith("."+self.quote_stack[-1]+" or") or text.endswith(";"+self.quote_stack[-1]+" or") \
                         ):
                        self.quote_stack.pop()
                        self.logger.debug(f"Detected closing quote on page {page.pg_num}. Popped from quote_stack.")
                        if label is not None and isinstance(label, tuple) and label[0] == "table":
                            continue
                        page.all_tbs[tb] = ["amendment"]
                    
                    elif self.quote_stack and self.quote_stack[-1] == '"' and doubleQuote_count%2!=0 and \
                        (text.endswith(self.quote_stack[-1] + ".") or text.endswith(self.quote_stack[-1] + ";") or \
                        text.endswith(self.quote_stack[-1] + ". and") or text.endswith(self.quote_stack[-1] + "; and") or \
                        text.endswith(self.quote_stack[-1] + ". or") or text.endswith(self.quote_stack[-1] + "; or") or \
                        text.endswith("."+self.quote_stack[-1]) or text.endswith(";"+self.quote_stack[-1]) or \
                        text.endswith("."+self.quote_stack[-1]+" and") or text.endswith(";"+self.quote_stack[-1]+" and") or \
                        text.endswith("."+self.quote_stack[-1]+" or") or text.endswith(";"+self.quote_stack[-1]+" or") \
                         ):
                        self.quote_stack.pop()
                        self.logger.debug(f"Detected closing quote on page {page.pg_num}. Popped from quote_stack.")
                        if label is not None and isinstance(label, tuple) and label[0] == "table":
                            continue
                        page.all_tbs[tb] = ["amendment"]
                        

                    # Inside an open quote block
                    elif self.quote_stack:
                        if label is not None and isinstance(label, tuple) and label[0] == "table":
                            continue
                        page.all_tbs[tb] = ["amendment"]
                        self.logger.debug(f"Inside open quote block on page {page.pg_num}.The text [{text}] marked as amendment.")
                
                except Exception as e:
                    self.logger.error(f"Error while processing amendment logic on page {page_num}: {e}")
    
    def check_for_blockquotes(self, page):
        for tb in page.all_tbs.keys():
            try:
                text = tb.extract_text_from_tb().strip()
            except Exception as e:
                self.logger.warning(f"Failed to extract text from textbox on page {getattr(page, 'pg_num', '?')}: {e}")
                continue

            text = text.replace('“', '"').replace('”', '"').replace('‘‘','"').replace('’’','"').replace('‘', "'").replace('’', "'")

            try:
                label = page.all_tbs[tb]
            except Exception as e:
                self.logger.warning(f"Failed to retrieve label for textbox on page {getattr(page, 'pg_num', '?')}: {e}")
                continue

            try:
                page_num = int(page.pg_num)
            except Exception as e:
                self.logger.error(f"Invalid page number: {page.pg_num}")
                continue

            if label is None:
                doubleQuote_count = text.count('"')
                singleQuote_count = text.count("'") 
                self.logger.debug(f"Page {page.pg_num}, Text: '{text}'")
                self.logger.debug(f"Quote counts — Double: {doubleQuote_count}, Single: {singleQuote_count}")
                self.logger.debug(f"Quote Stack: {self.quote_stack}")
                

                try:
                    # Check for self-contained quotes
                    if (
                            (
                                 text.startswith('"') and (
                                    text.lower().endswith('(emphasis supplied)') or
                                    text.lower().endswith('[emphasis supplied]') or
                                    text.endswith('".') or
                                    text.endswith('";') or
                                    text.endswith('…"') or 
                                    text.lower().endswith('…" (emphasis supplied)') or
                                    text.lower().endswith('." (emphasis supplied)') or
                                    text.lower().endswith(';" (emphasis supplied)') or
                                    text.lower().endswith('…"(emphasis supplied)') or
                                    text.lower().endswith('."(emphasis supplied)') or
                                    text.lower().endswith(';"(emphasis supplied)') or
                                    
                                    text.lower().endswith('…" [emphasis supplied]') or
                                    text.lower().endswith('." [emphasis supplied]') or
                                    text.lower().endswith(';" [emphasis supplied]') or
                                    text.lower().endswith('…"[emphasis supplied]') or
                                    text.lower().endswith('."[emphasis supplied]') or
                                    text.lower().endswith(';"[emphasis supplied]') or

                                    text.endswith('."') or
                                    text.endswith(';"') or
                                    text.lower().endswith('". (emphasis supplied)') or
                                    text.lower().endswith('"; (emphasis supplied)') or
                                    text.lower().endswith('".(emphasis supplied)') or
                                    text.lower().endswith('";(emphasis supplied)') or

                                    text.lower().endswith('". [emphasis supplied]') or
                                    text.lower().endswith('"; [emphasis supplied]') or
                                    text.lower().endswith('".[emphasis supplied]') or
                                    text.lower().endswith('";[emphasis supplied]') or
                                    text.endswith('"')
                                )
                            )
                            or
                            (
                                text.startswith("'") and (
                                    text.lower().endswith('(emphasis supplied)') or
                                    text.lower().endswith('[emphasis supplied]') or
                                    text.endswith("'.") or
                                    text.endswith("';") or
                                    text.endswith("…'") or 
                                    text.lower().endswith("…' (emphasis supplied)") or
                                    text.lower().endswith(".' (emphasis supplied)") or
                                    text.lower().endswith(";' (emphasis supplied)") or
                                    text.lower().endswith("…'(emphasis supplied)") or
                                    text.lower().endswith(".'(emphasis supplied)") or
                                    text.lower().endswith(";'(emphasis supplied)") or
                                    
                                    text.lower().endswith("…' [emphasis supplied]") or 
                                    text.lower().endswith(".' [emphasis supplied]") or 
                                    text.lower().endswith(";' [emphasis supplied]") or 
                                    text.lower().endswith("…'[emphasis supplied]") or 
                                    text.lower().endswith(".'[emphasis supplied]") or 
                                    text.lower().endswith(";'[emphasis supplied]") or
                                    text.endswith(".'") or
                                    text.endswith(";'") or
                                    text.lower().endswith("'.(emphasis supplied)") or
                                    text.lower().endswith("';(emphasis supplied)") or
                                    text.lower().endswith("'.[emphasis supplied]") or
                                    text.lower().endswith("';[emphasis supplied]") or
                                    text.endswith("'")
                                )
                            )
                    ):
                        self.isAmendmentPDF = True
                        self.logger.debug(f"Detected self-contained quote on page {page.pg_num}. Marked as amendment PDF.")
                        page.all_tbs[tb] = 'blockquote'
                        

                    # Check for opening quote
                    elif (text.startswith('"')) and (doubleQuote_count%2!=0):
                        self.quote_stack.append(text[0])
                        self.logger.debug(f"Detected opening quote with imbalance on page {page.pg_num}. Pushed to quote_stack.")
                        self.isAmendmentPDF = True
                        page.all_tbs[tb] = 'blockquote'
                    
                    elif (text.startswith("'")) and (singleQuote_count%2!=0):
                        self.quote_stack.append(text[0])
                        self.logger.debug(f"Detected opening quote with imbalance on page {page.pg_num}. Pushed to quote_stack.")
                        self.isAmendmentPDF = True
                        page.all_tbs[tb] = 'blockquote'
            
                    # Check for closing quote
                    elif self.quote_stack and self.quote_stack[-1] == '"' and doubleQuote_count%2!=0  and (text.lower().endswith('(emphasis supplied)') or
                                               text.lower().endswith('[emphasis supplied]') or text.endswith(self.quote_stack[-1] + ".") or text.endswith(self.quote_stack[-1] + ";") or \
                                               text.lower().endswith(self.quote_stack[-1] + "." + " (emphasis supplied)") or text.lower().endswith(self.quote_stack[-1] + ";" + " (emphasis supplied)") or\
                                               text.lower().endswith(self.quote_stack[-1] + "." + "(emphasis supplied)") or text.lower().endswith(self.quote_stack[-1] + ";" + "(emphasis supplied)") or \

                                               text.lower().endswith(self.quote_stack[-1] + "." + " [emphasis supplied]") or text.lower().endswith(self.quote_stack[-1] + ";" + " [emphasis supplied]") or\
                                               text.lower().endswith(self.quote_stack[-1] + "." + "[emphasis supplied]") or text.lower().endswith(self.quote_stack[-1] + ";" + "[emphasis supplied]")\
                                                
                                               or  text.endswith("."+self.quote_stack[-1]) or  text.endswith(";"+self.quote_stack[-1]) or \
                                               text.lower().endswith("."+self.quote_stack[-1]+ " (emphasis supplied)") or text.lower().endswith(";"+self.quote_stack[-1]+ " (emphasis supplied)") or \
                                               text.lower().endswith("."+self.quote_stack[-1]+ "(emphasis supplied)") or text.lower().endswith(";"+self.quote_stack[-1]+ "(emphasis supplied)") or \
                                               text.lower().endswith("…"+self.quote_stack[-1]+ " (emphasis supplied)") or text.lower().endswith("…"+self.quote_stack[-1]+ "(emphasis supplied)") or text.endswith("…"+self.quote_stack[-1]) or \
                                               
                                               text.lower().endswith("."+self.quote_stack[-1]+ " [emphasis supplied]") or text.lower().endswith(";"+self.quote_stack[-1]+ " [emphasis supplied]") or \
                                               text.lower().endswith("."+self.quote_stack[-1]+ "[emphasis supplied]") or text.lower().endswith(";"+self.quote_stack[-1]+ "[emphasis supplied]") or \
                                               text.lower().endswith("…"+self.quote_stack[-1]+ " [emphasis supplied]") or text.lower().endswith("…"+self.quote_stack[-1]+ "[emphasis supplied]") or text.endswith("…"+self.quote_stack[-1]) or \
                                               text.endswith(self.quote_stack[-1])
                                               ):
                        self.quote_stack.pop()
                        self.logger.debug(f"Detected closing quote on page {page.pg_num}. Popped from quote_stack.")
                        page.all_tbs[tb] = 'blockquote'

                    elif self.quote_stack and self.quote_stack[-1] == "'" and singleQuote_count%2!=0  and (text.lower().endswith('(emphasis supplied)') or 
                                               text.lower().endswith('[emphasis supplied]') or text.endswith(self.quote_stack[-1] + ".") or text.endswith(self.quote_stack[-1] + ";") or \
                                               text.lower().endswith(self.quote_stack[-1] + "." + " (emphasis supplied)") or text.lower().endswith(self.quote_stack[-1] + ";" + " (emphasis supplied)") or\
                                               text.lower().endswith(self.quote_stack[-1] + "." + "(emphasis supplied)") or text.lower().endswith(self.quote_stack[-1] + ";" + "(emphasis supplied)") or \
                                               
                                               text.lower().endswith(self.quote_stack[-1] + "." + " [emphasis supplied]") or text.lower().endswith(self.quote_stack[-1] + ";" + " [emphasis supplied]") or\
                                               text.lower().endswith(self.quote_stack[-1] + "." + "[emphasis supplied]") or text.lower().endswith(self.quote_stack[-1] + ";" + "[emphasis supplied]")\
                                               
                                               or  text.endswith("."+self.quote_stack[-1]) or  text.endswith(";"+self.quote_stack[-1]) or \
                                               text.lower().endswith("."+self.quote_stack[-1]+ " (emphasis supplied)") or text.lower().endswith(";"+self.quote_stack[-1]+ " (emphasis supplied)") or \
                                               text.lower().endswith("."+self.quote_stack[-1]+ "(emphasis supplied)") or text.lower().endswith(";"+self.quote_stack[-1]+ "(emphasis supplied)") or \
                                               text.lower().endswith("…"+self.quote_stack[-1]+ " (emphasis supplied)") or text.lower().endswith("…"+self.quote_stack[-1]+ "(emphasis supplied)") or text.endswith("…"+self.quote_stack[-1]) or \
                                               
                                               text.lower().endswith("."+self.quote_stack[-1]+ " [emphasis supplied]") or text.lower().endswith(";"+self.quote_stack[-1]+ " [emphasis supplied]") or \
                                               text.lower().endswith("."+self.quote_stack[-1]+ "[emphasis supplied]") or text.lower().endswith(";"+self.quote_stack[-1]+ "[emphasis supplied]") or \
                                               text.lower().endswith("…"+self.quote_stack[-1]+ " [emphasis supplied]") or text.lower().endswith("…"+self.quote_stack[-1]+ "[emphasis supplied]") or text.endswith("…"+self.quote_stack[-1]) or \
                                               
                                            
                                               text.endswith(self.quote_stack[-1])
                                               ):
                        self.quote_stack.pop()
                        self.logger.debug(f"Detected closing quote on page {page.pg_num}. Popped from quote_stack.")
                        page.all_tbs[tb] = 'blockquote'

                    # Inside an open quote block
                    elif self.quote_stack:
                        page.all_tbs[tb] = 'blockquote'
                        self.logger.debug(f"Inside open quote block on page {page.pg_num}.The text [{text}] marked as amendment.")
                
                except Exception as e:
                    self.logger.error(f"Error while processing blockquote logic on page {page_num}: {e}")
    

    # def check_for_blockquotes_judgments(self, page):
    #     """
    #     PRODUCTION-GRADE BLOCKQUOTE DETECTOR (LAYOUT-FIRST)

    #     Works on:
    #     - fragmented PDF textboxes
    #     - multi-box rows
    #     - horizontal splits
    #     - unlabeled regions only

    #     Does NOT use:
    #     - trigger words
    #     - full-text offsets
    #     - section trees

    #     Only:
    #     - punctuation cues
    #     - geometry
    #     - reading order
    #     """

    #     import re

    #     # =========================
    #     # STATE
    #     # =========================
    #     if not hasattr(self, "bq_active"):
    #         self.bq_active = False
    #     if not hasattr(self, "bq_count"):
    #         self.bq_count = 0

    #     # =========================
    #     # CLEAN TEXT
    #     # =========================
    #     def clean(t):
    #         t = (t or "").strip()
    #         t = t.replace("“", '"').replace("”", '"')
    #         t = t.replace("‘", "'").replace("’", "'")
    #         t = re.sub(r"\s+", " ", t)
    #         return t.strip()

    #     # =========================
    #     # BBOX HELPERS
    #     # =========================
    #     def bbox(tb):
    #         return getattr(tb, "x0", 0), getattr(tb, "y0", 0), getattr(tb, "x1", 0), getattr(tb, "y1", 0)

    #     def same_row(a, b, tol=3):
    #         ax0, ay0, ax1, ay1 = bbox(a)
    #         bx0, by0, bx1, by1 = bbox(b)
    #         return abs((ay0 + ay1)/2 - (by0 + by1)/2) <= tol

    #     # =========================
    #     # PUNCTUATION STARTER
    #     # =========================
    #     def is_starter(txt):
    #         txt = txt.strip()

    #         return (
    #             re.search(r':\s*$', txt) or
    #             re.search(r':-\s*$', txt) or
    #             re.search(r'::\s*$', txt) or
    #             re.search(r'--\s*$', txt) or
    #             re.search(r';\s*$', txt) or
    #             re.match(r'^:+.*:+$', txt)
    #         )

    #     # =========================
    #     # PARAGRAPH / STOP RULES
    #     # =========================
    #     def is_new_paragraph(txt):
    #         return bool(re.match(r'^(\d+[\.\)]|[ivxlcdm]+[\.\)]|[A-Za-z]\))\s+', txt))

    #     def is_heading(txt):
    #         if len(txt) < 4:
    #             return True
    #         if txt.isupper() and len(txt.split()) <= 8:
    #             return True
    #         return False

    #     def sentence_end(txt):
    #         return bool(re.search(r'[.!?]["\']?\s*$', txt))

    #     # =========================
    #     # STEP 1: collect UNLABELED boxes only
    #     # =========================
    #     boxes = []

    #     for tb in page.all_tbs.keys():

    #         # IMPORTANT: skip pre/table/header already labeled
    #         if page.all_tbs[tb] is not None:
    #             continue

    #         try:
    #             txt = clean(tb.extract_text_from_tb())
    #         except:
    #             continue

    #         if not txt:
    #             continue

    #         x0, y0, x1, y1 = bbox(tb)

    #         boxes.append({
    #             "tb": tb,
    #             "text": txt,
    #             "x0": x0,
    #             "y0": y0,
    #             "x1": x1,
    #             "y1": y1
    #         })

    #     if not boxes:
    #         return

    #     # =========================
    #     # STEP 2: reading order sort
    #     # =========================
    #     boxes.sort(key=lambda b: (-b["y0"], b["x0"]))

    #     # =========================
    #     # STEP 3: ROW CLUSTERING (critical)
    #     # =========================
    #     rows = []
    #     used = [False] * len(boxes)

    #     for i in range(len(boxes)):

    #         if used[i]:
    #             continue

    #         group = [boxes[i]]
    #         used[i] = True

    #         for j in range(i + 1, len(boxes)):
    #             if used[j]:
    #                 continue

    #             if same_row(boxes[i]["tb"], boxes[j]["tb"]):
    #                 group.append(boxes[j])
    #                 used[j] = True

    #         group.sort(key=lambda b: b["x0"])

    #         row_text = clean(" ".join(g["text"] for g in group))

    #         rows.append({
    #             "boxes": group,
    #             "text": row_text
    #         })

    #     # top → bottom
    #     rows.sort(key=lambda r: -r["boxes"][0]["y0"])

    #     # =========================
    #     # STEP 4: BLOCKQUOTE DETECTION
    #     # =========================
    #     i = 0

    #     while i < len(rows):

    #         row = rows[i]
    #         txt = row["text"]

    #         # ----------------------------------
    #         # START CONDITION
    #         # ----------------------------------
    #         if is_starter(txt):
    #             self.bq_active = True
    #             self.bq_count = 0
    #             i += 1
    #             continue

    #         # ----------------------------------
    #         # ACTIVE MODE
    #         # ----------------------------------
    #         if self.bq_active:

    #             # stop conditions
    #             if is_heading(txt):
    #                 self.bq_active = False
    #                 i += 1
    #                 continue

    #             if is_new_paragraph(txt):
    #                 self.bq_active = False
    #                 i += 1
    #                 continue

    #             # ignore noise / pre fragments
    #             words = txt.split()
    #             if len(words) <= 1 and len(txt) < 10:
    #                 self.bq_active = False
    #                 i += 1
    #                 continue

    #             # mark ALL boxes in row
    #             for b in row["boxes"]:
    #                 page.all_tbs[b["tb"]] = "blockquote"

    #             self.bq_count += 1

    #             # natural stop
    #             if self.bq_count >= 3 and sentence_end(txt):
    #                 self.bq_active = False

    #             # safety cap
    #             if self.bq_count >= 20:
    #                 self.bq_active = False

    #         i += 1



    def check_for_blockquotes_judgments(self, page):
        """
        FORWARD PROPAGATION BLOCKQUOTE LABELER

        RULE:
        - if a line ends with ':' or ':-'
        - next rows become blockquote
        - stop on heading / paragraph / strong break
        """

        import re
        import unicodedata

        def normalize(t):
            if not t:
                return ""
            t = unicodedata.normalize("NFKC", t)
            t = t.replace("।", ".").replace("॥", ".")
            t = re.sub(r"\s+", " ", t).strip()
            return t

        def bbox(tb):
            return getattr(tb, "x0", 0), getattr(tb, "y0", 0), getattr(tb, "x1", 0), getattr(tb, "y1", 0)

        def same_row(a, b, tol=3):
            ax0, ay0, ax1, ay1 = bbox(a)
            bx0, by0, bx1, by1 = bbox(b)
            return abs(((ay0 + ay1) / 2) - ((by0 + by1) / 2)) <= tol

        # ----------------------------
        # STOP CONDITIONS
        # ----------------------------
        def is_stop(txt):
            return (
                len(txt.strip()) < 3 or
                txt.isupper() or
                bool(re.match(r"^\d+[\.\)]\s", txt))  # paragraph restart
            )

        # ----------------------------
        # TRIGGER CONDITION
        # ----------------------------
        def is_trigger(txt):
            return bool(re.search(r":\s*$|:-\s*$", txt))

        # =========================
        # COLLECT ONLY UNLABELED
        # =========================
        boxes = []

        for tb in page.all_tbs.keys():

            if page.all_tbs[tb] is not None:
                continue

            try:
                txt = normalize(tb.extract_text_from_tb())
            except:
                continue

            if not txt:
                continue

            x0, y0, x1, y1 = bbox(tb)

            boxes.append({
                "tb": tb,
                "text": txt,
                "x0": x0,
                "y0": y0
            })

        if not boxes:
            return

        boxes.sort(key=lambda b: (-b["y0"], b["x0"]))

        # =========================
        # ROW GROUPING
        # =========================
        rows = []
        used = set()

        for i in range(len(boxes)):
            if i in used:
                continue

            group = [boxes[i]]
            used.add(i)

            for j in range(i + 1, len(boxes)):
                if j in used:
                    continue
                if same_row(boxes[i]["tb"], boxes[j]["tb"]):
                    group.append(boxes[j])
                    used.add(j)

            group.sort(key=lambda b: b["x0"])

            rows.append({
                "boxes": group,
                "text": normalize(" ".join(g["text"] for g in group))
            })

        rows.sort(key=lambda r: -r["boxes"][0]["y0"])

        # =========================
        # FORWARD LABELING ENGINE
        # =========================
        active = False

        for row in rows:
            txt = row["text"]

            if not txt:
                continue

            # -------------------------
            # TRIGGER → START PROPAGATION
            # -------------------------
            if is_trigger(txt):
                active = True

                # also label trigger row itself if needed
                for b in row["boxes"]:
                    page.all_tbs[b["tb"]] = "blockquote"

                continue

            # -------------------------
            # APPLY LABEL IF ACTIVE
            # -------------------------
            if active:

                if is_stop(txt):
                    active = False
                    continue

                for b in row["boxes"]:
                    page.all_tbs[b["tb"]] = "blockquote"
        