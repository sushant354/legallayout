import camelot
import logging 

class TableExtraction:
    def __init__(self,pdf_path,pg_num):
        self.logger = logging.getLogger(__name__)
        self.tables, self.table_bbox = self.get_table_and_bbox(pdf_path,pg_num)
    
    # --- func to find the table contents and their coordinates ---
    def get_table_and_bbox(self,pdf_path,page_num):
        table = {}
        bbox = {}
        try:
            tables_and_bbox = camelot.read_pdf(pdf_path, pages=page_num, flavor='lattice')
            for idx,tab in enumerate(tables_and_bbox):
                table[idx] = tab.df
                bbox[idx] = tab._bbox
        except Exception as e:
            self.logger.error("Exception occurred while checking for table contents: %s" % (str(e)))

        return table,bbox

    def get_table_width(self, idx):
        if idx not in self.table_bbox:
            return None
        x1, y1, x2, y2 = self.table_bbox[idx]
        width = abs(x2 - x1)
        return width
    
    def get_table_height(self, idx):
        if idx not in self.table_bbox:
            return None
        x1, y1, x2, y2 = self.table_bbox[idx]
        height = abs(y2 - y1)
        return height