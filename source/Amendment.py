class Amendment:
    def __init__(self):
        self.isAmendmentPDF = False
        self.quote_stack = []
    
    # --- func to classify the textbox if it is detected with sign of amendments properties ---
    def check_for_amendments(self, page,startPage,endPage):
        for tb in page.all_tbs.keys():
            text = tb.extract_text_from_tb().strip()
            text = text.replace('“', '"').replace('”', '"').replace('‘‘','"').replace('’’','"').replace('‘', "'").replace('’', "'")
            label = page.all_tbs[tb]

            if label is None and startPage is not None and endPage is not None and int(page.pg_num) >=startPage and int(page.pg_num)<=endPage:
                doubleQuote_count = text.count('"')
                singleQuote_count = text.count("'") 

                # Check for self-contained quotes
                if ((text.startswith('"') and (text.endswith('".') or text.endswith('";'))) or \
                   (text.startswith("'") and (text.endswith("'.") or text.endswith("';")))):
                    self.isAmendmentPDF = True
                    page.all_tbs[tb] = ["amendment"]
                    

                # Check for opening quote
                elif  (text.startswith('"') or text.startswith("'")) and (doubleQuote_count%2!=0 or singleQuote_count%2!=0):
                    self.quote_stack.append(text[0])
                    self.isAmendmentPDF = True
                    page.all_tbs[tb] = ["amendment"]
                    

                # Check for closing quote
                elif self.quote_stack and (text.endswith(self.quote_stack[-1] + ".") or text.endswith(self.quote_stack[-1] + ";")):
                    self.quote_stack.pop()
                    page.all_tbs[tb] = ["amendment"]
                    

                # Inside an open quote block
                elif self.quote_stack:
                    page.all_tbs[tb] = ["amendment"]
                    
