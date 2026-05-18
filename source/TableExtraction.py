import camelot
import pymupdf4llm
import pymupdf.layout
import logging 
import pandas as pd
import json

class TableExtraction:
    def __init__(self,pdf_path,pg_num, pdf_type):
        self.logger = logging.getLogger(__name__)
        self.pdf_type = pdf_type
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

class TableExtractionJudgments:
    def __init__(self, pdf_path, pg_num, pdf_type=None):
        self.logger = logging.getLogger(__name__)
        self.pdf_type = pdf_type

        (
            self.tables,
            self.table_bbox,
            self.table_shape,
            self.table_cells
        ) = self.get_table_and_bbox(pdf_path, pg_num)

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

    def get_table_and_bbox(self, pdf_path, page_num):

        tables = {}
        bbox = {}
        shape = {}
        cells_meta = {}

        try:
            page_num = int(page_num)

            with pymupdf.open(pdf_path) as doc:
                page_json = pymupdf4llm.to_json(
                    doc,
                    pages=[page_num - 1]
                )

            if isinstance(page_json, str):
                page_json = json.loads(page_json)


            pages = page_json.get("pages", [])

            if not pages:
                return tables, bbox, shape, cells_meta

            page_data = pages[0]

            page_height = round(
                float(page_data.get("height", 0.0)),
                3
            )

            boxes = page_data.get("boxes", [])

            table_idx = 0

            for box in boxes:

                if box.get("boxclass") != "table":
                    continue

                tb = box.get("table")

                if not tb:
                    continue

                tables[table_idx] = pd.DataFrame()


                raw_bbox = tb.get("bbox", None)

                if raw_bbox:
                    x0, y0, x1, y1 = raw_bbox

                    bbox[table_idx] = (
                        round(float(x0), 3),
                        round(page_height - float(y1), 3),
                        round(float(x1), 3),
                        round(page_height - float(y0), 3)
                    )
                else:
                    bbox[table_idx] = None

               
                shape[table_idx] = {
                    "rows": int(tb.get("row_count", 0)),
                    "cols": int(tb.get("col_count", 0))
                }

            
                cell_list = []

                cells = tb.get("cells", [])

                for r in range(len(cells)):
                    for c in range(len(cells[r])):

                        cb = cells[r][c]

                        if cb:
                            cx0, cy0, cx1, cy1 = cb

                            fixed_bbox = (
                                round(float(cx0), 3),
                                round(page_height - float(cy1), 3),
                                round(float(cx1), 3),
                                round(page_height - float(cy0), 3)
                            )
                        else:
                            fixed_bbox = None

                        cell_list.append({
                            "row_index": r,
                            "col_index": c,
                            "bbox": fixed_bbox
                        })

                cells_meta[table_idx] = cell_list

                table_idx += 1

        except Exception as e:
            self.logger.error(
                "Exception occurred while checking for table contents: %s"
                % str(e)
            )

        return tables, bbox, shape, cells_meta