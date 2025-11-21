import pandas as pd
import re
import numpy as np
from difflib import SequenceMatcher
import logging

class TableBuilder:
    def __init__(self):
        self.pending_table = None
        self.min_word_threshold_tableRows = 2
        self.table_terminators = {".", "!", "?"}
        self.logger = logging.getLogger(__name__)
        
        self.serial_patterns = [
            r"^(\(?[1-9]\d*[\)\)]?[\.\)]?)$",  # Numbers with brackets/dots: (1), 1., 1)
            r"^([a-zA-Z][\)\.]?)$",           # Single letters with brackets/dots: a), A.
            r"^([ivxlcdmIVXLCDM]+[\)\.]?)$",   # Roman numerals with brackets/dots: i), ii.
            r"^(\(?[1-9]\d*\)[\.\:]?)",       # (1). or (1):
            r"^([a-zA-Z]\d+(\.\d+)?)$",       # Alphanumeric: a1, a2.1
            r"^(\d+(\.\d+)?[a-zA-Z])$",       # Numeric-letter: 1a, 2.1b
            r"^(sec|art|clause|section)\s*[\-\:]?\s*\d+",  # Legal references
            r"^(\d+\s*of\s*\d+)$",            # "1 of 10" pattern
        ]
    
    def is_sequential(self, text1, text2):
        try:
            s1, s2 = str(text1).strip(), str(text2).strip()
            if s1.isdigit() and s2.isdigit():
                return int(s2) == int(s1) + 1
            n1, n2 = re.findall(r"\d+", s1), re.findall(r"\d+", s2)
            if n1 and n2:
                return int(n2[0]) == int(n1[0]) + 1
            return False
        except:
            return False

    def row_similarity(self, row1, row2):
        s1, s2 = " ".join(str(x) for x in row1), " ".join(str(x) for x in row2)
        return SequenceMatcher(None, s1, s2).ratio()

    def _has_serial_number(self, cell):
        text = str(cell).strip()
        if not text or text.lower() in ["nan", ""]:
            return False

        # Check against improved serial patterns
        for pattern in self.serial_patterns:
            if re.fullmatch(pattern, text, re.IGNORECASE):
                return True

        # Fallback checks for edge cases
        # Simple digits (catch all)
        if text.isdigit():
            return True

        # Simple Roman numerals
        if re.fullmatch(r"[ivxlcdmIVXLCDM]+", text):
            return True

        return False

    def _is_numeric_or_symbolic(self, cell):
        text = str(cell).strip().lower()
        if not text or text in ["-", "—", "–", "na", "n/a", "nil", "none", "✓", "x"]:
            return True
        if re.fullmatch(r"\d+(\.\d+)?(\s?(kg|g|mg|cm|mm|m|km|%|hrs?|days?|years?))?", text):
            return True
        return False

    def _looks_like_continuation(self, prev_text, curr_text, curr_row):
        prev_text, curr_text = str(prev_text).strip(), str(curr_text).strip()

        # Guard: numeric/measurement-only rows should not merge
        numeric_like = sum(self._is_numeric_or_symbolic(c) for c in curr_row[1:])
        if numeric_like >= len(curr_row) - 2:  # all except maybe one col
            return False

        # Rule 1: prev doesn't end with punctuation + curr starts lowercase
        if prev_text and prev_text[-1] not in self.table_terminators and curr_text and curr_text[0].islower():
            return True

        # Rule 2: curr row is sparse (only 1 column filled beyond first col)
        non_empty_cols = sum(bool(str(c).strip()) for c in curr_row[1:])
        if non_empty_cols == 1:
            return True

        # Rule 3: curr row very short (few words)
        if len(curr_text.split()) < self.min_word_threshold_tableRows:
            return True

        # Rule 4: Text indentation pattern (continuation often indented)
        if curr_text and (curr_text.startswith('    ') or curr_text.startswith('\t')):
            return True

        # Rule 5: Continuation markers like hyphens or em-dashes
        if curr_text and curr_text[:3] in ['...', '— ', '- ']:
            return True

        return False


    def _is_sparse_row(self, row_list):
        if len(row_list) <= 1:
            return False
            
        empty_count = 0
        for cell in row_list[1:]:  # Skip first column for sparse check
            cell_str = str(cell).strip()
            if not cell_str or cell_str.lower() in ['nan', '']:
                empty_count += 1
        
        empty_ratio = empty_count / (len(row_list) - 1)
        return empty_ratio > 0.7  # More than 70% empty

    def _smart_concatenate(self, prev_text, curr_text):
        if not prev_text:
            return curr_text
        if not curr_text:
            return prev_text
            
        prev_text = prev_text.rstrip()
        curr_text = curr_text.lstrip()
        
        # If previous text ends with sentence terminator, add space
        if prev_text[-1] in self.table_terminators:
            return prev_text + " " + curr_text
        
        # If current text starts with lowercase, likely continuation - just space
        if curr_text[0].islower():
            return prev_text + " " + curr_text
            
        # If current text starts with punctuation (like continuation), join without space
        if curr_text[0] in [',', ';', '-', '—']:
            return prev_text + curr_text
            
        # Default: add space
        return prev_text + " " + curr_text

    def is_table_continuation(self, table2, table2_width):
        if self.pending_table is None:
            return False
            
        table1, table1_width = self.pending_table
        
        if table1.empty or table2.empty:
            return False

        try:
            # 1. Width similarity check (more flexible threshold)
            if max(table1_width, table2_width) > 0:
                width_ratio = min(table1_width, table2_width) / max(table1_width, table2_width)
                if width_ratio < 0.85:  # More flexible than 0.95
                    self.logger.debug(f"Width ratio too low: {width_ratio}")
                    return False

            # 2. Column count check with flexibility
            col_diff = abs(table1.shape[1] - table2.shape[1])
            if col_diff > 1:  # Allow 1 column difference
                self.logger.debug(f"Column count difference too high: {col_diff}")
                return False

            # 3. NEW: Header similarity check - if headers are identical, remove duplicate
            header_sim = self._calculate_header_similarity(table1, table2)
            if header_sim > 0.9:
                self.logger.debug("Identical headers detected, treating as continuation")
                # Remove the header row from table2 before merging
                if len(table2) > 1:
                    table2 = table2.iloc[1:].reset_index(drop=True)
                return True

            # 4. Check for obvious new table patterns
            if not self._is_new_table_start(table1, table2):
                return True

            # 5. ENHANCED: Last row first column check for numeric vs non-numeric
            last_row_first_col = str(table1.iloc[-1, 0]).strip()
            first_row_first_col = str(table2.iloc[0, 0]).strip()
            
            # If last row's first cell is numeric AND current row's first cell is also numeric
            # They should be separate rows (continuation)
            if (self._is_numeric_content(last_row_first_col) and 
                self._is_numeric_content(first_row_first_col)):
                self.logger.debug(f"Both numeric first columns: {last_row_first_col} and {first_row_first_col}")
                return True

            # 6. NEW: Check if last row of table1 ends with sentence terminators
            last_row_text = self._get_last_row_text(table1)
            if self._ends_with_sentence_terminator(last_row_text):
                self.logger.debug("Last row ends with sentence terminator, treating as continuation")
                return True

            # 7. NEW: Sparse table with mostly empty cells - treat as continuation
            if self._is_very_sparse_table(table2):
                self.logger.debug("Very sparse table detected, likely continuation")
                return True

            # 8. Sequential numbering check (for non-numeric cases)
            if first_row_first_col and last_row_first_col:
                if self.is_sequential(first_row_first_col, last_row_first_col):
                    self.logger.debug(f"Sequential numbering: {last_row_first_col} → {first_row_first_col}")
                    return True

            # 9. Content similarity check
            if self._has_similar_structure(table1, table2):
                self.logger.debug("Similar structure detected")
                return True

            # Fallback: treat as new table
            self.logger.debug("Defaulting to new table")
            return False

        except Exception as e:
            self.logger.error(f"Error in is_table_continuation: {e}")
            return False

    def _calculate_header_similarity(self, table1, table2):
        try:
            if len(table1) == 0 or len(table2) == 0:
                return 0.0
                
            header1 = table1.iloc[0]
            header2 = table2.iloc[0]
            
            # Normalize headers for comparison
            normalized1 = [self._normalize_header_cell(str(cell)) for cell in header1]
            normalized2 = [self._normalize_header_cell(str(cell)) for cell in header2]
            
            # Calculate similarity ratio
            similarity = SequenceMatcher(None, normalized1, normalized2).ratio()
            return similarity
            
        except Exception as e:
            self.logger.error(f"Error calculating header similarity: {e}")
            return 0.0

    def _normalize_header_cell(self, cell_text):
        if not cell_text or cell_text.lower() in ['nan', '']:
            return ""
        
        # Remove extra spaces, convert to lowercase, remove special formatting
        normalized = ' '.join(cell_text.split())
        normalized = normalized.lower()
        # Remove common table header prefixes/suffixes
        normalized = re.sub(r'^(s\.no|sl\.?no|serial|item|part)\s*\.?\s*', '', normalized)
        normalized = re.sub(r'\s*(no|num|number)\s*\.?\s*$', '', normalized)
        
        return normalized.strip()

    def _is_numeric_content(self, text):
        if not text or text.lower() in ['nan', '']:
            return False
            
        # Remove common formatting
        clean_text = re.sub(r'[^\w]', '', text.lower())
        
        # Check for pure numbers
        if clean_text.isdigit():
            return True
            
        # Check for Roman numerals
        if re.fullmatch(r'[ivxlcdm]+', clean_text):
            return True
            
        # Check for alphanumeric serial patterns
        if re.fullmatch(r'[a-z]\d+|[a-z]+\d+', clean_text):
            return True
            
        return False

    def _get_last_row_text(self, table):
        if table.empty:
            return ""
            
        last_row = table.iloc[-1]
        text_parts = []
        
        for cell in last_row:
            cell_str = str(cell).strip()
            if cell_str and cell_str.lower() not in ['nan', '']:
                text_parts.append(cell_str)
        
        return ' '.join(text_parts)

    def _ends_with_sentence_terminator(self, text):
        if not text:
            return False
            
        # Remove trailing spaces and check last character
        text = text.rstrip()
        
        # Check for sentence terminators including some legal document patterns
        terminators = ['.', '!', '?', ':', ';']
        
        for char in reversed(text):
            if char in terminators:
                return True
            elif char.isspace():
                continue
            else:
                break
                
        return False

    def _is_very_sparse_table(self, table):
        if table.empty:
            return False
            
        total_cells = table.shape[0] * table.shape[1]
        empty_cells = 0
        
        for _, row in table.iterrows():
            for cell in row:
                cell_str = str(cell).strip()
                if not cell_str or cell_str.lower() in ['nan', '']:
                    empty_cells += 1
        
        empty_ratio = empty_cells / total_cells
        return empty_ratio > 0.6  # More than 60% empty

    def _is_new_table_start(self, table1, table2):
        first_cell = str(table2.iloc[0, 0]).strip()
        
        # Common new table patterns
        new_table_patterns = [
            r"^(\(?[1aAiI]\)?[\.\)]?)$",           # (1), 1., 1), a), A., (i)
            r"^[\[\(]?(table|tbl|chart)\s*[\d\.]*",  # Table 1, Tbl. 2
            r"^(schedule|annexure|appendix)\s*[A-Z0-9]*",  # Schedule A, Annexure 1
            r"^part\s+[A-Z\d]+",                     # Part I, Part 1
            r"^section\s+\d+",                      # Section 1
        ]
        
        for pattern in new_table_patterns:
            if re.match(pattern, first_cell, re.IGNORECASE):
                return True
                
        return False

    def _has_similar_structure(self, table1, table2):
        try:
            # Header similarity
            header_sim = self.row_similarity(table1.iloc[0], table2.iloc[0])
            if header_sim > 0.8:
                return True
            
            # Overall content pattern similarity
            if len(table1) >= 2 and len(table2) >= 2:
                # Compare first data row patterns
                row1_pattern = self._get_row_pattern(table1.iloc[1])
                row2_pattern = self._get_row_pattern(table2.iloc[1])
                pattern_sim = SequenceMatcher(None, row1_pattern, row2_pattern).ratio()
                if pattern_sim > 0.7:
                    return True
            
            return False
        except:
            return False

    def _get_row_pattern(self, row):
        pattern = []
        for cell in row:
            cell_str = str(cell).strip().lower()
            if not cell_str or cell_str in ['nan', '']:
                pattern.append('E')  # Empty
            elif cell_str.replace('.', '').replace(',', '').isdigit():
                pattern.append('N')  # Numeric
            elif self._has_serial_number(cell):
                pattern.append('S')  # Serial
            else:
                pattern.append('T')  # Text
        return ''.join(pattern)


    def merge_tables(self, table2, table2_width):
        if self.pending_table is None:
            self.pending_table = [table2, table2_width]
            return
            
        table1, table1_width = self.pending_table
        
        if table1.empty or table2.empty:
            return

        try:
            # Step 1: ENHANCED: Check if table2 has duplicate header
            table2_adjusted = self._handle_duplicate_headers(table1, table2)
            
            # Step 2: Intelligent column alignment
            table2_aligned = self._align_columns(table1, table2_adjusted)
            if table2_aligned is None:
                self.logger.warning("Failed to align columns, skipping merge")
                return

            # Step 3: ENHANCED: Check for broken rows between tables (last row of table1 + first row of table2)
            merged_table = self._merge_table_boundaries(table1, table2_aligned)

            # Step 4: Update average width
            avg_width = (table1_width + table2_width) / 2.0
            self.pending_table = [merged_table, avg_width]
            self.logger.debug(f"Successfully merged tables. New shape: {merged_table.shape}")

        except Exception as e:
            self.logger.error(f"Error during table merge: {e}")

    def _handle_duplicate_headers(self, table1, table2):
        if table2.empty:
            return table2
            
        # Start with table1 header (first row)
        table1_header = table1.iloc[0] if len(table1) > 0 else None
        
        if table1_header is None:
            return table2
            
        # Compare rows positionally: table1 row 1 with table2 row 1, table1 row 2 with table2 row 2, etc.
        header_rows_to_remove = 0
        max_rows_to_check = min(len(table1), len(table2))
        
        for i in range(max_rows_to_check):
            table1_row = table1.iloc[i]
            table2_row = table2.iloc[i]
            
            # Check if this row matches positionally
            header_similarity = self._calculate_row_similarity(table1_row, table2_row)
            
            if header_similarity > 0.85:  # High similarity indicates duplicate header
                header_rows_to_remove += 1
                self.logger.debug(f"Table1 row {i+1} matches Table2 row {i+1} (similarity: {header_similarity}), marking for removal")
            else:
                # Found first non-matching row, stop checking
                self.logger.debug(f"Table1 row {i+1} doesn't match Table2 row {i+1} (similarity: {header_similarity}), stopping header detection")
                break
        
        # Remove detected header rows from the beginning of table2
        if header_rows_to_remove > 0:
            self.logger.debug(f"Removing {header_rows_to_remove} duplicate header rows from beginning of table2")
            if len(table2) > header_rows_to_remove:
                return table2.iloc[header_rows_to_remove:].reset_index(drop=True)
            else:
                # If table2 only has header rows, return empty DataFrame
                return pd.DataFrame(columns=table1.columns)
        
        return table2

    def _calculate_row_similarity(self, row1, row2):
        try:
            # Handle different length rows
            min_length = min(len(row1), len(row2))
            if min_length == 0:
                return 0.0
            
            # Normalize cells for comparison
            normalized1 = [self._normalize_header_cell(str(cell)) for cell in row1[:min_length]]
            normalized2 = [self._normalize_header_cell(str(cell)) for cell in row2[:min_length]]
            
            # Calculate similarity ratio
            similarity = SequenceMatcher(None, normalized1, normalized2).ratio()
            return similarity
            
        except Exception as e:
            self.logger.error(f"Error calculating row similarity: {e}")
            return 0.0

    def _merge_table_boundaries(self, table1, table2):
        if table2.empty:
            return table1
            
        # Get last row of table1 and first row of table2
        if table1.empty:
            return table2
            
        last_row_table1 = table1.iloc[-1]
        first_row_table2 = table2.iloc[0]
        
        # Check if we should merge the boundary rows
        should_merge_boundary = self._should_merge_boundary_rows(last_row_table1, first_row_table2)
        
        if should_merge_boundary:
            # Merge the boundary rows
            merged_boundary = self._merge_boundary_rows(last_row_table1, first_row_table2)
            
            # Create new merged table
            # Remove last row from table1 and first row from table2, then add merged row
            table1_without_last = table1.iloc[:-1]
            table2_without_first = table2.iloc[1:] if len(table2) > 1 else pd.DataFrame(columns=table2.columns)
            
            # Combine: table1 + merged_boundary + table2_without_first
            merged_table = pd.concat([
                table1_without_last,
                pd.DataFrame([merged_boundary], columns=table1.columns),
                table2_without_first
            ], ignore_index=True)
            
            self.logger.debug("Merged boundary rows between tables")
        else:
            # No merging needed, just concatenate
            merged_table = pd.concat([table1, table2], ignore_index=True)
            self.logger.debug("No boundary merge needed, concatenated tables")
        
        return merged_table

    def _should_merge_boundary_rows(self, last_row, first_row):
        try:
            # Check if any cell in last row ends with sentence terminators
            for cell in last_row:
                cell_text = str(cell).strip()
                if self._ends_with_sentence_terminator(cell_text):
                    self.logger.debug(f"Last row cell ends with terminator: '{cell_text[-20:]}'")
                    return False  # Don't merge if sentence is complete
            
            # Check if first column of both rows has numeric content
            last_first_col = str(last_row.iloc[0] if hasattr(last_row, 'iloc') else last_row[0]).strip()
            first_first_col = str(first_row.iloc[0] if hasattr(first_row, 'iloc') else first_row[0]).strip()
            
            if (self._is_numeric_content(last_first_col) and 
                self._is_numeric_content(first_first_col)):
                self.logger.debug(f"Both first columns numeric: '{last_first_col}' and '{first_first_col}'")
                return False  # Don't merge if both have numeric serials
            
            # Check if first row of table2 is sparse (likely continuation)
            first_row_list = list(first_row)
            if self._is_sparse_row(first_row_list):
                self.logger.debug("First row of table2 is sparse, merging")
                return True
            
            # Check continuation patterns
            # Get text content from non-first columns for comparison
            last_content = self._get_content_columns(last_row)
            first_content = self._get_content_columns(first_row)
            
            return self._looks_like_continuation(last_content, first_content, list(first_row))
            
        except Exception as e:
            self.logger.error(f"Error checking boundary rows: {e}")
            return False

    def _get_content_columns(self, row):
        content_parts = []
        # Skip first column (usually serial number) and get text from other columns
        for i, cell in enumerate(row):
            if i == 0:  # Skip first column
                continue
            cell_str = str(cell).strip()
            if cell_str and cell_str.lower() not in ['nan', '']:
                content_parts.append(cell_str)
        return ' '.join(content_parts)

    def _merge_boundary_rows(self, last_row, first_row):
        merged_row = []
        
        for i in range(max(len(last_row), len(first_row))):
            last_cell = str(last_row.iloc[i] if hasattr(last_row, 'iloc') else last_row[i]).strip() if i < len(last_row) else ""
            first_cell = str(first_row.iloc[i] if hasattr(first_row, 'iloc') else first_row[i]).strip() if i < len(first_row) else ""
            
            if first_cell and first_cell.lower() not in ['nan', '']:
                if last_cell:
                    merged_text = self._smart_concatenate(last_cell, first_cell)
                    merged_row.append(merged_text)
                else:
                    merged_row.append(first_cell)
            else:
                merged_row.append(last_cell)
        
        return merged_row

    def _align_columns(self, table1, table2):
        try:
            col_diff = table2.shape[1] - table1.shape[1]
            
            if col_diff == 0:
                return table2
            elif col_diff < 0:
                # Add padding columns to table2
                table2_copy = table2.copy()
                for i in range(abs(col_diff)):
                    table2_copy[f"_pad{i}"] = ""
                return table2_copy
            else:
                # Table2 has more columns - try to intelligently truncate or merge
                if col_diff == 1:
                    # Might be an extra column that can be merged with the last one
                    table2_copy = table2.copy()
                    # Merge last two columns if the last one looks like continuation
                    if table2_copy.shape[1] >= 2:
                        last_col = table2_copy.iloc[:, -1]
                        second_last_col = table2_copy.iloc[:, -2]
                        
                        # If last column is mostly empty, merge with second last
                        if last_col.isnull().sum() > len(last_col) * 0.7:
                            table2_copy.iloc[:, -2] = (table2_copy.iloc[:, -2].astype(str) + 
                                                      " " + table2_copy.iloc[:, -1].astype(str))
                            table2_copy = table2_copy.iloc[:, :-1]
                        else:
                            # Truncate to match table1
                            table2_copy = table2_copy.iloc[:, :table1.shape[1]]
                    
                    return table2_copy
                else:
                    # Too many columns difference, truncate to match table1
                    return table2.iloc[:, :table1.shape[1]].copy()
                    
        except Exception as e:
            self.logger.error(f"Error aligning columns: {e}")
            return None

# import pandas as pd
# import re
# import numpy as np
# from difflib import SequenceMatcher
# import logging

# class TableBuilder:
#     def __init__(self):
#         self.pending_table = None
#         self.min_word_threshold_tableRows = 2
#         self.table_terminators = {".", "!", "?"}
#         self.logger = logging.getLogger(__name__)
        
#         # RULESET Serial Number Patterns (RULE 1)
#         self.serial_patterns = [
#             r"^(\(?[1-9]\d*[\)\)]?[\.\)]?)$",  # Numbers with brackets/dots: (1), 1., 1)
#             r"^([a-zA-Z][\)\.]?)$",           # Single letters with brackets/dots: a), A.
#             r"^([ivxlcdmIVXLCDM]+[\)\.]?)$",   # Roman numerals with brackets/dots: i), ii.
#             r"^(\(?[1-9]\d*\)[\.\:]?)",       # (1). or (1):
#             r"^([a-zA-Z]\d+(\.\d+)?)$",       # Alphanumeric: a1, a2.1
#             r"^(\d+(\.\d+)?[a-zA-Z])$",       # Numeric-letter: 1a, 2.1b
#             r"^(sec|art|clause|section)\s*[\-\:]?\s*\d+",  # Legal references
#             r"^(\d+\s*of\s*\d+)$",            # "1 of 10" pattern
#         ]
        
#         # RULESET Measurement Units (RULE 3)
#         self.measurement_units = [
#             '%', 'kg', 'g', 'mg', 'km', 'cm', 'mm', 'm', 'hrs', 'hrs?', 'days?', 'years?',
#             'Rs', '₹', 'INR', 'USD', '$', 'EUR', '€'
#         ]
        
#         # RULESET New Record Indicators (RULE 4)
#         self.designation_patterns = [
#             r'^(Mr\.|Ms\.|Shri|Smt\.|Dr\.|M/s)\s+',
#             r'^(M/s)\s+'
#         ]
        
#         # RULESET Date patterns (RULE 4)
#         self.date_patterns = [
#             r'^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$',  # dd/mm/yyyy or dd-mm-yyyy
#             r'^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$',  # mm/dd/yyyy or mm-dd-yyyy
#             r'^[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}$',  # Month yyyy or Month dd, yyyy
#             r'^[A-Za-z]{3,9}\s+\d{4}$'  # Month yyyy
#         ]
        
#         # RULESET Sequence indicators (RULE 4)
#         self.sequence_indicators = [
#             r'^(First|Second|Third|Fourth|Fifth|One|Two|Three|Four|Five)\b',
#             r'^(Firstly|Secondly|Thirdly|Fourthly|Fifthly)\b'
#         ]
    
#     def is_sequential(self, text1, text2):
#         try:
#             s1, s2 = str(text1).strip(), str(text2).strip()
#             if s1.isdigit() and s2.isdigit():
#                 return int(s2) == int(s1) + 1
#             n1, n2 = re.findall(r"\d+", s1), re.findall(r"\d+", s2)
#             if n1 and n2:
#                 return int(n2[0]) == int(n1[0]) + 1
#             return False
#         except:
#             return False

#     def row_similarity(self, row1, row2):
#         s1, s2 = " ".join(str(x) for x in row1), " ".join(str(x) for x in row2)
#         return SequenceMatcher(None, s1, s2).ratio()

#     def _has_serial_number(self, cell):
#         text = str(cell).strip()
#         if not text or text.lower() in ["nan", ""]:
#             return False

#         # Check against improved serial patterns
#         for pattern in self.serial_patterns:
#             if re.fullmatch(pattern, text, re.IGNORECASE):
#                 return True

#         # Fallback checks for edge cases
#         # Simple digits (catch all)
#         if text.isdigit():
#             return True

#         # Simple Roman numerals
#         if re.fullmatch(r"[ivxlcdmIVXLCDM]+", text):
#             return True

#         return False

#     def _is_numeric_or_symbolic(self, cell):
#         text = str(cell).strip().lower()
#         if not text or text in ["-", "—", "–", "na", "n/a", "nil", "none", "✓", "x"]:
#             return True
#         if re.fullmatch(r"\d+(\.\d+)?(\s?(kg|g|mg|cm|mm|m|km|%|hrs?|days?|years?))?", text):
#             return True
#         return False

#     def _looks_like_continuation(self, prev_text, curr_text, curr_row):
#         prev_text, curr_text = str(prev_text).strip(), str(curr_text).strip()

#         # Guard: numeric/measurement-only rows should not merge
#         numeric_like = sum(self._is_numeric_or_symbolic(c) for c in curr_row[1:])
#         if numeric_like >= len(curr_row) - 2:  # all except maybe one col
#             return False

#         # Rule 1: prev doesn't end with punctuation + curr starts lowercase
#         if prev_text and prev_text[-1] not in self.table_terminators and curr_text and curr_text[0].islower():
#             return True

#         # Rule 2: curr row is sparse (only 1 column filled beyond first col)
#         non_empty_cols = sum(bool(str(c).strip()) for c in curr_row[1:])
#         if non_empty_cols == 1:
#             return True

#         # Rule 3: curr row very short (few words)
#         if len(curr_text.split()) < self.min_word_threshold_tableRows:
#             return True

#         # Rule 4: Text indentation pattern (continuation often indented)
#         if curr_text and (curr_text.startswith('    ') or curr_text.startswith('\t')):
#             return True

#         # Rule 5: Continuation markers like hyphens or em-dashes
#         if curr_text and curr_text[:3] in ['...', '— ', '- ']:
#             return True

#         return False


#     def _is_sparse_row(self, row_list):
#         if len(row_list) <= 1:
#             return False
            
#         empty_count = 0
#         for cell in row_list[1:]:  # Skip first column for sparse check
#             cell_str = str(cell).strip()
#             if not cell_str or cell_str.lower() in ['nan', '']:
#                 empty_count += 1
        
#         empty_ratio = empty_count / (len(row_list) - 1)
#         return empty_ratio > 0.7  # More than 70% empty

#     def _smart_concatenate(self, prev_text, curr_text):
#         if not prev_text:
#             return curr_text
#         if not curr_text:
#             return prev_text
            
#         prev_text = prev_text.rstrip()
#         curr_text = curr_text.lstrip()
        
#         # If previous text ends with sentence terminator, add space
#         if prev_text[-1] in self.table_terminators:
#             return prev_text + " " + curr_text
        
#         # If current text starts with lowercase, likely continuation - just space
#         if curr_text[0].islower():
#             return prev_text + " " + curr_text
            
#         # If current text starts with punctuation (like continuation), join without space
#         if curr_text[0] in [',', ';', '-', '—']:
#             return prev_text + curr_text
            
#         # Default: add space
#         return prev_text + " " + curr_text

#     def is_table_continuation(self, table2, table2_width):
#         if self.pending_table is None:
#             return False
            
#         table1, table1_width = self.pending_table
        
#         if table1.empty or table2.empty:
#             return False

#         try:
#             # 1. Width similarity check (more flexible threshold)
#             if max(table1_width, table2_width) > 0:
#                 width_ratio = min(table1_width, table2_width) / max(table1_width, table2_width)
#                 if width_ratio < 0.85:  # More flexible than 0.95
#                     self.logger.debug(f"Width ratio too low: {width_ratio}")
#                     return False

#             # 2. Column count check with flexibility
#             col_diff = abs(table1.shape[1] - table2.shape[1])
#             if col_diff > 1:  # Allow 1 column difference
#                 self.logger.debug(f"Column count difference too high: {col_diff}")
#                 return False

#             # 3. NEW: Header similarity check - if headers are identical, remove duplicate
#             header_sim = self._calculate_header_similarity(table1, table2)
#             if header_sim > 0.9:
#                 self.logger.debug("Identical headers detected, treating as continuation")
#                 # Remove the header row from table2 before merging
#                 if len(table2) > 1:
#                     table2 = table2.iloc[1:].reset_index(drop=True)
#                 return True

#             # 4. Check for obvious new table patterns
#             if not self._is_new_table_start(table1, table2):
#                 return True

#             # 5. ENHANCED: Last row first column check for numeric vs non-numeric
#             last_row_first_col = str(table1.iloc[-1, 0]).strip()
#             first_row_first_col = str(table2.iloc[0, 0]).strip()
            
#             # If last row's first cell is numeric AND current row's first cell is also numeric
#             # They should be separate rows (continuation)
#             if (self._is_numeric_content(last_row_first_col) and 
#                 self._is_numeric_content(first_row_first_col)):
#                 self.logger.debug(f"Both numeric first columns: {last_row_first_col} and {first_row_first_col}")
#                 return True

#             # 6. NEW: Check if last row of table1 ends with sentence terminators
#             last_row_text = self._get_last_row_text(table1)
#             if self._ends_with_sentence_terminator(last_row_text):
#                 self.logger.debug("Last row ends with sentence terminator, treating as continuation")
#                 return True

#             # 7. NEW: Sparse table with mostly empty cells - treat as continuation
#             if self._is_very_sparse_table(table2):
#                 self.logger.debug("Very sparse table detected, likely continuation")
#                 return True

#             # 8. Sequential numbering check (for non-numeric cases)
#             if first_row_first_col and last_row_first_col:
#                 if self.is_sequential(first_row_first_col, last_row_first_col):
#                     self.logger.debug(f"Sequential numbering: {last_row_first_col} → {first_row_first_col}")
#                     return True

#             # 9. Content similarity check
#             if self._has_similar_structure(table1, table2):
#                 self.logger.debug("Similar structure detected")
#                 return True

#             # Fallback: treat as new table
#             self.logger.debug("Defaulting to new table")
#             return False

#         except Exception as e:
#             self.logger.error(f"Error in is_table_continuation: {e}")
#             return False

#     def _calculate_header_similarity(self, table1, table2):
#         try:
#             if len(table1) == 0 or len(table2) == 0:
#                 return 0.0
                
#             header1 = table1.iloc[0]
#             header2 = table2.iloc[0]
            
#             # Normalize headers for comparison
#             normalized1 = [self._normalize_header_cell(str(cell)) for cell in header1]
#             normalized2 = [self._normalize_header_cell(str(cell)) for cell in header2]
            
#             # Calculate similarity ratio
#             similarity = SequenceMatcher(None, normalized1, normalized2).ratio()
#             return similarity
            
#         except Exception as e:
#             self.logger.error(f"Error calculating header similarity: {e}")
#             return 0.0

#     def _normalize_header_cell(self, cell_text):
#         if not cell_text or cell_text.lower() in ['nan', '']:
#             return ""
        
#         # Remove extra spaces, convert to lowercase, remove special formatting
#         normalized = ' '.join(cell_text.split())
#         normalized = normalized.lower()
#         # Remove common table header prefixes/suffixes
#         normalized = re.sub(r'^(s\.no|sl\.?no|serial|item|part)\s*\.?\s*', '', normalized)
#         normalized = re.sub(r'\s*(no|num|number)\s*\.?\s*$', '', normalized)
        
#         return normalized.strip()

#     def _is_numeric_content(self, text):
#         if not text or text.lower() in ['nan', '']:
#             return False
            
#         # Remove common formatting
#         clean_text = re.sub(r'[^\w]', '', text.lower())
        
#         # Check for pure numbers
#         if clean_text.isdigit():
#             return True
            
#         # Check for Roman numerals
#         if re.fullmatch(r'[ivxlcdm]+', clean_text):
#             return True
            
#         # Check for alphanumeric serial patterns
#         if re.fullmatch(r'[a-z]\d+|[a-z]+\d+', clean_text):
#             return True
            
#         return False

#     def _get_last_row_text(self, table):
#         if table.empty:
#             return ""
            
#         last_row = table.iloc[-1]
#         text_parts = []
        
#         for cell in last_row:
#             cell_str = str(cell).strip()
#             if cell_str and cell_str.lower() not in ['nan', '']:
#                 text_parts.append(cell_str)
        
#         return ' '.join(text_parts)

#     def _ends_with_sentence_terminator(self, text):
#         if not text:
#             return False
            
#         # Remove trailing spaces and check last character
#         text = text.rstrip()
        
#         # Check for sentence terminators including some legal document patterns
#         terminators = ['.', '!', '?', ':', ';']
        
#         for char in reversed(text):
#             if char in terminators:
#                 return True
#             elif char.isspace():
#                 continue
#             else:
#                 break
                
#         return False

#     def _is_very_sparse_table(self, table):
#         if table.empty:
#             return False
            
#         total_cells = table.shape[0] * table.shape[1]
#         empty_cells = 0
        
#         for _, row in table.iterrows():
#             for cell in row:
#                 cell_str = str(cell).strip()
#                 if not cell_str or cell_str.lower() in ['nan', '']:
#                     empty_cells += 1
        
#         empty_ratio = empty_cells / total_cells
#         return empty_ratio > 0.6  # More than 60% empty

#     def _is_new_table_start(self, table1, table2):
#         first_cell = str(table2.iloc[0, 0]).strip()
        
#         # Common new table patterns
#         new_table_patterns = [
#             r"^(\(?[1aAiI]\)?[\.\)]?)$",           # (1), 1., 1), a), A., (i)
#             r"^[\[\(]?(table|tbl|chart)\s*[\d\.]*",  # Table 1, Tbl. 2
#             r"^(schedule|annexure|appendix)\s*[A-Z0-9]*",  # Schedule A, Annexure 1
#             r"^part\s+[A-Z\d]+",                     # Part I, Part 1
#             r"^section\s+\d+",                      # Section 1
#         ]
        
#         for pattern in new_table_patterns:
#             if re.match(pattern, first_cell, re.IGNORECASE):
#                 return True
                
#         return False

#     def _has_similar_structure(self, table1, table2):
#         try:
#             # Header similarity
#             header_sim = self.row_similarity(table1.iloc[0], table2.iloc[0])
#             if header_sim > 0.8:
#                 return True
            
#             # Overall content pattern similarity
#             if len(table1) >= 2 and len(table2) >= 2:
#                 # Compare first data row patterns
#                 row1_pattern = self._get_row_pattern(table1.iloc[1])
#                 row2_pattern = self._get_row_pattern(table2.iloc[1])
#                 pattern_sim = SequenceMatcher(None, row1_pattern, row2_pattern).ratio()
#                 if pattern_sim > 0.7:
#                     return True
            
#             return False
#         except:
#             return False

#     def _get_row_pattern(self, row):
#         pattern = []
#         for cell in row:
#             cell_str = str(cell).strip().lower()
#             if not cell_str or cell_str in ['nan', '']:
#                 pattern.append('E')  # Empty
#             elif cell_str.replace('.', '').replace(',', '').isdigit():
#                 pattern.append('N')  # Numeric
#             elif self._has_serial_number(cell):
#                 pattern.append('S')  # Serial
#             else:
#                 pattern.append('T')  # Text
#         return ''.join(pattern)


#     def merge_tables(self, table2, table2_width, html_builder=None):
#         if self.pending_table is None:
#             self.pending_table = [table2, table2_width]
#             return
            
#         table1, table1_width = self.pending_table
        
#         if table1.empty or table2.empty:
#             return

#         try:
#             # Step 1: ENHANCED: Check if table2 has duplicate header
#             table2_adjusted = self._handle_duplicate_headers(table1, table2)
            
#             # RULESET Check for column count mismatch (RULE 5 with fallback)
#             if abs(table1.shape[1] - table2_adjusted.shape[1]) > 1:
#                 self.logger.debug(f"Column count mismatch detected: {table1.shape[1]} vs {table2_adjusted.shape[1]}")
                
#                 # Fallback: add current table and start new one
#                 if html_builder:
#                     self.logger.debug("Calling addTable() for existing table due to column mismatch")
#                     html_builder.addTable(table1)
                
#                 self.pending_table = [table2_adjusted, table2_width]
#                 return
            
#             # Step 2: Intelligent column alignment
#             table2_aligned = self._align_columns(table1, table2_adjusted)
#             if table2_aligned is None:
#                 self.logger.warning("Failed to align columns, skipping merge")
#                 return

#             # Step 3: ENHANCED: Check for broken rows between tables (last row of table1 + first row of table2)
#             merged_table = self._merge_table_boundaries(table1, table2_aligned)

#             # Step 4: Update average width
#             avg_width = (table1_width + table2_width) / 2.0
#             self.pending_table = [merged_table, avg_width]
#             self.logger.debug(f"Successfully merged tables. New shape: {merged_table.shape}")

#         except Exception as e:
#             self.logger.error(f"Error during table merge: {e}")

#     def _handle_duplicate_headers(self, table1, table2):
#         if table2.empty:
#             return table2
            
#         # Start with table1 header (first row)
#         table1_header = table1.iloc[0] if len(table1) > 0 else None
        
#         if table1_header is None:
#             return table2
            
#         # Compare rows positionally: table1 row 1 with table2 row 1, table1 row 2 with table2 row 2, etc.
#         header_rows_to_remove = 0
#         max_rows_to_check = min(len(table1), len(table2))
        
#         for i in range(max_rows_to_check):
#             table1_row = table1.iloc[i]
#             table2_row = table2.iloc[i]
            
#             # Check if this row matches positionally
#             header_similarity = self._calculate_row_similarity(table1_row, table2_row)
            
#             if header_similarity > 0.85:  # High similarity indicates duplicate header
#                 header_rows_to_remove += 1
#                 self.logger.debug(f"Table1 row {i+1} matches Table2 row {i+1} (similarity: {header_similarity}), marking for removal")
#             else:
#                 # Found first non-matching row, stop checking
#                 self.logger.debug(f"Table1 row {i+1} doesn't match Table2 row {i+1} (similarity: {header_similarity}), stopping header detection")
#                 break
        
#         # Remove detected header rows from the beginning of table2
#         if header_rows_to_remove > 0:
#             self.logger.debug(f"Removing {header_rows_to_remove} duplicate header rows from beginning of table2")
#             if len(table2) > header_rows_to_remove:
#                 return table2.iloc[header_rows_to_remove:].reset_index(drop=True)
#             else:
#                 # If table2 only has header rows, return empty DataFrame
#                 return pd.DataFrame(columns=table1.columns)
        
#         return table2

#     def _calculate_row_similarity(self, row1, row2):
#         try:
#             # Handle different length rows
#             min_length = min(len(row1), len(row2))
#             if min_length == 0:
#                 return 0.0
            
#             # Normalize cells for comparison
#             normalized1 = [self._normalize_header_cell(str(cell)) for cell in row1[:min_length]]
#             normalized2 = [self._normalize_header_cell(str(cell)) for cell in row2[:min_length]]
            
#             # Calculate similarity ratio
#             similarity = SequenceMatcher(None, normalized1, normalized2).ratio()
#             return similarity
            
#         except Exception as e:
#             self.logger.error(f"Error calculating row similarity: {e}")
#             return 0.0

#     def _merge_table_boundaries(self, table1, table2):
#         if table2.empty:
#             return table1
            
#         # Get last row of table1 and first row of table2
#         if table1.empty:
#             return table2
            
#         last_row_table1 = table1.iloc[-1]
#         first_row_table2 = table2.iloc[0]
        
#         # Check if we should merge the boundary rows
#         should_merge_boundary = self._should_merge_boundary_rows(last_row_table1, first_row_table2)
        
#         if should_merge_boundary:
#             # Merge the boundary rows
#             merged_boundary = self._merge_boundary_rows(last_row_table1, first_row_table2)
            
#             # Create new merged table
#             # Remove last row from table1 and first row from table2, then add merged row
#             table1_without_last = table1.iloc[:-1]
#             table2_without_first = table2.iloc[1:] if len(table2) > 1 else pd.DataFrame(columns=table2.columns)
            
#             # Combine: table1 + merged_boundary + table2_without_first
#             merged_table = pd.concat([
#                 table1_without_last,
#                 pd.DataFrame([merged_boundary], columns=table1.columns),
#                 table2_without_first
#             ], ignore_index=True)
            
#             self.logger.debug("Merged boundary rows between tables")
#         else:
#             # No merging needed, just concatenate
#             merged_table = pd.concat([table1, table2], ignore_index=True)
#             self.logger.debug("No boundary merge needed, concatenated tables")
        
#         return merged_table

#     def _should_merge_boundary_rows(self, existing_row, upcoming_row):
#         """
#         RULESET: Decide whether to MERGE two rows or treat upcoming row as NEW ROW
#         E = existing_row (last row of existing table)
#         U = upcoming_row (first non-header row of upcoming table)
#         """
#         try:
#             self.logger.debug("Applying RULESET for row merging decision")
            
#             # Convert to list for easier processing
#             E_list = list(existing_row)
#             U_list = list(upcoming_row)
            
#             # RULE 1 — SERIAL NUMBER CHECK (highest priority)
#             if len(U_list) > 0:
#                 first_cell_u = str(U_list[0]).strip()
#                 if self._is_serial_number(first_cell_u):
#                     self.logger.debug(f"RULE 1: Serial number detected in first cell: '{first_cell_u}' → NEW ROW")
#                     return False  # NEW ROW
            
#             # RULE 2 — PURE NUMERIC ROW
#             if self._is_pure_numeric_row(U_list):
#                 self.logger.debug("RULE 2: Pure numeric row detected → NEW ROW")
#                 return False  # NEW ROW
            
#             # RULE 3 — SEMI-NUMERIC OR SUMMARY ROW
#             if self._is_semi_numeric_row(U_list):
#                 self.logger.debug("RULE 3: Semi-numeric/summary row detected → NEW ROW")
#                 return False  # NEW ROW
            
#             # RULE 4 — ROW APPEARS TO START A NEW RECORD
#             if self._is_new_record_start(U_list):
#                 self.logger.debug("RULE 4: New record pattern detected → NEW ROW")
#                 return False  # NEW ROW
            
#             # RULE 5 — COLUMN COUNT MISMATCH
#             if abs(len(E_list) - len(U_list)) > 1:
#                 self.logger.debug(f"RULE 5: Column count mismatch ({len(E_list)} vs {len(U_list)}) → NEW ROW")
#                 return False  # NEW ROW
            
#             # RULE 6 — IF NONE OF THE ABOVE MATCH → MERGE
#             self.logger.debug("RULE 6: No NEW-ROW conditions matched → MERGE")
#             return True  # MERGE
            
#         except Exception as e:
#             self.logger.error(f"Error in RULESET evaluation: {e}")
#             return False  # Default to NEW ROW on error

#     def _is_pure_numeric_row(self, row_list):
#         """RULE 2: Check if all non-empty cells contain only numbers or empty markers"""
#         try:
#             for cell in row_list:
#                 cell_str = str(cell).strip()
#                 if not cell_str or cell_str.lower() in ['nan', '']:
#                     continue  # Skip empty cells
                
#                 # Check for pure numbers with optional decimals
#                 if re.fullmatch(r'^\d+(\.\d+)?$', cell_str):
#                     continue  # Valid number
                
#                 # Check for empty markers
#                 if cell_str in ['-', '—', '–', 'na', 'n/a', 'nil', 'none']:
#                     continue  # Valid empty marker
                
#                 return False  # Invalid cell found
            
#             return True  # All cells are numeric or empty markers
            
#         except Exception as e:
#             self.logger.error(f"Error in _is_pure_numeric_row: {e}")
#             return False

#     def _is_semi_numeric_row(self, row_list):
#         """RULE 3: Check if any cell ends with measurement units or financial markers"""
#         try:
#             # Check for measurement units
#             for cell in row_list:
#                 cell_str = str(cell).strip()
#                 if not cell_str or cell_str.lower() in ['nan', '']:
#                     continue
                
#                 # Check for measurement units at the end
#                 for unit in self.measurement_units:
#                     if cell_str.lower().endswith(unit.lower()):
#                         self.logger.debug(f"Found measurement unit '{unit}' in cell: '{cell_str}'")
#                         return True
                
#                 # Check for closing punctuation
#                 if any(cell_str.endswith(punct) for punct in ['.', ',', ';', ':']):
#                     self.logger.debug(f"Found closing punctuation in cell: '{cell_str}'")
#                     return True
            
#             return False
            
#         except Exception as e:
#             self.logger.error(f"Error in _is_semi_numeric_row: {e}")
#             return False

#     def _is_new_record_start(self, row_list):
#         """RULE 4: Check if first non-empty text cell starts like a new entity/record"""
#         try:
#             # Find first non-empty text cell (skip first column if it's serial-like)
#             first_text_cell = None
#             for i, cell in enumerate(row_list):
#                 cell_str = str(cell).strip()
#                 if not cell_str or cell_str.lower() in ['nan', '']:
#                     continue
                
#                 # Skip if first column looks like serial number
#                 if i == 0 and self._is_serial_number(cell_str):
#                     continue
                
#                 first_text_cell = cell_str
#                 break
            
#             if not first_text_cell:
#                 return False
            
#             self.logger.debug(f"Checking first text cell for new record patterns: '{first_text_cell}'")
            
#             # Check for designation patterns
#             for pattern in self.designation_patterns:
#                 if re.match(pattern, first_text_cell, re.IGNORECASE):
#                     self.logger.debug(f"Found designation pattern: {pattern}")
#                     return True
            
#             # Check for date patterns
#             for pattern in self.date_patterns:
#                 if re.match(pattern, first_text_cell):
#                     self.logger.debug(f"Found date pattern: {pattern}")
#                     return True
            
#             # Check for sequence indicators
#             for pattern in self.sequence_indicators:
#                 if re.match(pattern, first_text_cell, re.IGNORECASE):
#                     self.logger.debug(f"Found sequence indicator: {pattern}")
#                     return True
            
#             # Check for company/entity patterns
#             company_patterns = [
#                 r'.*\b(Ltd|Pvt|Limited|Private|Corporation|Inc|LLC)\b.*',
#                 r'.*\b(Company|Enterprises|Industries|Solutions)\b.*',
#                 r'.*\b(Association|Organization|Foundation|Trust)\b.*'
#             ]
#             for pattern in company_patterns:
#                 if re.search(pattern, first_text_cell, re.IGNORECASE):
#                     self.logger.debug(f"Found company pattern: {pattern}")
#                     return True
            
#             # Check for person name patterns (2+ words with capital letters)
#             name_words = first_text_cell.split()
#             if len(name_words) >= 2 and len(name_words) <= 4:  # Reasonable name length
#                 all_capitalized = all(word[0].isupper() if len(word) > 0 else False for word in name_words[:3])
#                 if all_capitalized and not any(word.lower() in ['the', 'and', 'of', 'in', 'at', 'on'] for word in name_words):
#                     self.logger.debug(f"Possible person name pattern: '{first_text_cell}'")
#                     return True
            
#             return False
            
#         except Exception as e:
#             self.logger.error(f"Error in _is_new_record_start: {e}")
#             return False

#     def _get_content_columns(self, row):
#         content_parts = []
#         # Skip first column (usually serial number) and get text from other columns
#         for i, cell in enumerate(row):
#             if i == 0:  # Skip first column
#                 continue
#             cell_str = str(cell).strip()
#             if cell_str and cell_str.lower() not in ['nan', '']:
#                 content_parts.append(cell_str)
#         return ' '.join(content_parts)

#     def _merge_boundary_rows(self, last_row, first_row):
#         merged_row = []
        
#         for i in range(max(len(last_row), len(first_row))):
#             last_cell = str(last_row.iloc[i] if hasattr(last_row, 'iloc') else last_row[i]).strip() if i < len(last_row) else ""
#             first_cell = str(first_row.iloc[i] if hasattr(first_row, 'iloc') else first_row[i]).strip() if i < len(first_row) else ""
            
#             if first_cell and first_cell.lower() not in ['nan', '']:
#                 if last_cell:
#                     merged_text = self._smart_concatenate(last_cell, first_cell)
#                     merged_row.append(merged_text)
#                 else:
#                     merged_row.append(first_cell)
#             else:
#                 merged_row.append(last_cell)
        
#         return merged_row

#     def _align_columns(self, table1, table2):
#         try:
#             col_diff = table2.shape[1] - table1.shape[1]
            
#             if col_diff == 0:
#                 return table2
#             elif col_diff < 0:
#                 # Add padding columns to table2
#                 table2_copy = table2.copy()
#                 for i in range(abs(col_diff)):
#                     table2_copy[f"_pad{i}"] = ""
#                 return table2_copy
#             else:
#                 # Table2 has more columns - try to intelligently truncate or merge
#                 if col_diff == 1:
#                     # Might be an extra column that can be merged with the last one
#                     table2_copy = table2.copy()
#                     # Merge last two columns if the last one looks like continuation
#                     if table2_copy.shape[1] >= 2:
#                         last_col = table2_copy.iloc[:, -1]
#                         second_last_col = table2_copy.iloc[:, -2]
                        
#                         # If last column is mostly empty, merge with second last
#                         if last_col.isnull().sum() > len(last_col) * 0.7:
#                             table2_copy.iloc[:, -2] = (table2_copy.iloc[:, -2].astype(str) + 
#                                                       " " + table2_copy.iloc[:, -1].astype(str))
#                             table2_copy = table2_copy.iloc[:, :-1]
#                         else:
#                             # Truncate to match table1
#                             table2_copy = table2_copy.iloc[:, :table1.shape[1]]
                    
#                     return table2_copy
#                 else:
#                     # Too many columns difference, truncate to match table1
#                     return table2.iloc[:, :table1.shape[1]].copy()
                    
#         except Exception as e:
#             self.logger.error(f"Error aligning columns: {e}")
#             return None
