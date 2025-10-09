import logging

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

            if label is None: #and startPage is not None and endPage is not None and  startPage <= page_num <= endPage:
                doubleQuote_count = text.count('"')
                singleQuote_count = text.count("'") 
                self.logger.debug(f"Page {page.pg_num}, Text: '{text}'")
                self.logger.debug(f"Quote counts — Double: {doubleQuote_count}, Single: {singleQuote_count}")
                self.logger.debug(f"Quote Stack: {self.quote_stack}")
                

                try:
                    # Check for self-contained quotes
                    if ((text.startswith('"') and (text.endswith('".') or text.endswith('";'))) or \
                    (text.startswith("'") and (text.endswith("'.") or text.endswith("';")))):
                        self.isAmendmentPDF = True
                        self.logger.debug(f"Detected self-contained quote on page {page.pg_num}. Marked as amendment PDF.")
                        page.all_tbs[tb] = ["amendment"]
                        

                    # Check for opening quote
                    elif  (text.startswith('"')) and (doubleQuote_count%2!=0):
                        self.quote_stack.append(text[0])
                        self.logger.debug(f"Detected opening quote with imbalance on page {page.pg_num}. Pushed to quote_stack.")
                        self.isAmendmentPDF = True
                        page.all_tbs[tb] = ["amendment"]
                    
                    elif  (text.startswith("'")) and (singleQuote_count%2!=0):
                        self.quote_stack.append(text[0])
                        self.logger.debug(f"Detected opening quote with imbalance on page {page.pg_num}. Pushed to quote_stack.")
                        self.isAmendmentPDF = True
                        page.all_tbs[tb] = ["amendment"]
                        

                    # Check for closing quote
                    elif self.quote_stack and self.quote_stack[-1] == "'" and singleQuote_count%2!=0 and (text.endswith(self.quote_stack[-1] + ".") or text.endswith(self.quote_stack[-1] + ";")):
                        self.quote_stack.pop()
                        self.logger.debug(f"Detected closing quote on page {page.pg_num}. Popped from quote_stack.")
                        page.all_tbs[tb] = ["amendment"]
                    
                    elif self.quote_stack and self.quote_stack[-1] == '"' and doubleQuote_count%2!=0 and (text.endswith(self.quote_stack[-1] + ".") or text.endswith(self.quote_stack[-1] + ";")):
                        self.quote_stack.pop()
                        self.logger.debug(f"Detected closing quote on page {page.pg_num}. Popped from quote_stack.")
                        page.all_tbs[tb] = ["amendment"]
                        

                    # Inside an open quote block
                    elif self.quote_stack:
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
                    # if ((text.startswith('"') and (text.endswith('".') or text.endswith('";'))) or \
                    # (text.startswith("'") and (text.endswith("'.") or text.endswith("';")))):
                    if (
                            (
                                 text.startswith('"') and (
                                    text.lower().endswith('(emphasis supplied)') or
                                    text.endswith('".') or
                                    text.endswith('";') or
                                    text.endswith('…"') or 
                                    text.lower().endswith('…" (emphasis supplied)') or
                                    text.lower().endswith('." (emphasis supplied)') or
                                    text.lower().endswith(';" (emphasis supplied)') or
                                    text.lower().endswith('…"(emphasis supplied)') or
                                    text.lower().endswith('."(emphasis supplied)') or
                                    text.lower().endswith(';"(emphasis supplied)') or
                                    text.endswith('."') or
                                    text.endswith(';"') or
                                    text.lower().endswith('". (emphasis supplied)') or
                                    text.lower().endswith('"; (emphasis supplied)') or
                                    text.lower().endswith('".(emphasis supplied)') or
                                    text.lower().endswith('";(emphasis supplied)') or
                                    text.endswith('"')
                                )
                            )
                            or
                            (
                                text.startswith("'") and (
                                    text.lower().endswith('(emphasis supplied)') or
                                    text.endswith("'.") or
                                    text.endswith("';") or
                                    text.endswith("…'") or 
                                    text.lower().endswith("…' (emphasis supplied)") or
                                    text.lower().endswith(".' (emphasis supplied)") or
                                    text.lower().endswith(";' (emphasis supplied)") or
                                    text.lower().endswith("…'(emphasis supplied)") or
                                    text.lower().endswith(".'(emphasis supplied)") or
                                    text.lower().endswith(";'(emphasis supplied)") or
                                    text.endswith(".'") or
                                    text.endswith(";'") or
                                    text.lower().endswith("'.(emphasis supplied)") or
                                    text.lower().endswith("';(emphasis supplied)") or
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
            
                    # elif self.quote_stack and (text.endswith(self.quote_stack[-1] + ".") or text.endswith(self.quote_stack[-1] + ";")\
                    #                            or  text.endswith("."+self.quote_stack[-1]) or  text.endswith(";"+self.quote_stack[-1])):
                    # Check for closing quote
                    elif self.quote_stack and self.quote_stack[-1] == '"' and doubleQuote_count%2!=0  and (text.lower().endswith('(emphasis supplied)') or text.endswith(self.quote_stack[-1] + ".") or text.endswith(self.quote_stack[-1] + ";") or \
                                               text.lower().endswith(self.quote_stack[-1] + "." + " (emphasis supplied)") or text.lower().endswith(self.quote_stack[-1] + ";" + " (emphasis supplied)") or\
                                               text.lower().endswith(self.quote_stack[-1] + "." + "(emphasis supplied)") or text.lower().endswith(self.quote_stack[-1] + ";" + "(emphasis supplied)")\
                                               or  text.endswith("."+self.quote_stack[-1]) or  text.endswith(";"+self.quote_stack[-1]) or \
                                               text.lower().endswith("."+self.quote_stack[-1]+ " (emphasis supplied)") or text.lower().endswith(";"+self.quote_stack[-1]+ " (emphasis supplied)") or \
                                               text.lower().endswith("."+self.quote_stack[-1]+ "(emphasis supplied)") or text.lower().endswith(";"+self.quote_stack[-1]+ "(emphasis supplied)") or \
                                               text.lower().endswith("…"+self.quote_stack[-1]+ " (emphasis supplied)") or text.lower().endswith("…"+self.quote_stack[-1]+ "(emphasis supplied)") or text.endswith("…"+self.quote_stack[-1]) or \
                                               text.endswith(self.quote_stack[-1])
                                               ):
                        self.quote_stack.pop()
                        self.logger.debug(f"Detected closing quote on page {page.pg_num}. Popped from quote_stack.")
                        page.all_tbs[tb] = 'blockquote'

                    elif self.quote_stack and self.quote_stack[-1] == "'" and singleQuote_count%2!=0  and (text.lower().endswith('(emphasis supplied)') or text.endswith(self.quote_stack[-1] + ".") or text.endswith(self.quote_stack[-1] + ";") or \
                                               text.lower().endswith(self.quote_stack[-1] + "." + " (emphasis supplied)") or text.lower().endswith(self.quote_stack[-1] + ";" + " (emphasis supplied)") or\
                                               text.lower().endswith(self.quote_stack[-1] + "." + "(emphasis supplied)") or text.lower().endswith(self.quote_stack[-1] + ";" + "(emphasis supplied)")\
                                               or  text.endswith("."+self.quote_stack[-1]) or  text.endswith(";"+self.quote_stack[-1]) or \
                                               text.lower().endswith("."+self.quote_stack[-1]+ " (emphasis supplied)") or text.lower().endswith(";"+self.quote_stack[-1]+ " (emphasis supplied)") or \
                                               text.lower().endswith("."+self.quote_stack[-1]+ "(emphasis supplied)") or text.lower().endswith(";"+self.quote_stack[-1]+ "(emphasis supplied)") or \
                                               text.lower().endswith("…"+self.quote_stack[-1]+ " (emphasis supplied)") or text.lower().endswith("…"+self.quote_stack[-1]+ "(emphasis supplied)") or text.endswith("…"+self.quote_stack[-1]) or \
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
                    