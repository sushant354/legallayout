import camelot

class TableExtraction:
    def __init__(self,pdf_path,pg_num):
        self.tables, self.table_bbox = self.get_table_and_bbox(pdf_path,pg_num)
    
    # --- func to find the table contents and their coordinates ---
    def get_table_and_bbox(self,pdf_path,page_num):
        tables_and_bbox = camelot.read_pdf(pdf_path, pages=page_num)
        table = {}
        bbox = {}
        for idx,tab in enumerate(tables_and_bbox):
            table[idx] = tab.df
            bbox[idx] = tab._bbox
        
        return table,bbox
