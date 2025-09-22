import re
import logging


ARTICLE      = 4
DECIMAL      = 3
SMALLSTRING  = 2
GENSTRING    = 1
ROMAN        = 0

class CompareLevel:
    def __init__(self, val, depthType):
        self.logger = logging.getLogger(__name__)
        self.depthTypes = [depthType, -1, -1, -1, -1,-1]
        self.valnum     = [val, None, None, None, None,None]   
        self.nextvals =  self.get_next_vals()

    def get_next_vals(self):
        nextvals = {}

        try:
            nextvals[DECIMAL] = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10',\
                                '11', '12', '13', '14', '15', '16', '17', '18', '19', '20']
            nextvals[ROMAN]   = ['i', 'ii', 'iii', 'iv', 'v', 'vi', 'vii', 'viii', 'ix', 'x',\
                                'xi', 'xii', 'xiii', 'xiv', 'xv', 'xvi', 'xvii', 'xviii', 'xix', 'xx']
            nextvals[SMALLSTRING]  = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l',\
                                    'm', 'n', 'o', 'p', 'q', 'r', 's', 't', 'u', 'v', 'w', 'x', 'y', 'z']
            nextvals[GENSTRING] = []
            for valueType in list(nextvals.keys()):
                i = 0
                x = {}
                for a in nextvals[valueType]:
                    x[a] = i
                    i+= 1
                nextvals[valueType] = x
        except Exception as e:
            self.logger.error(f"Failed in get_next_vals: {e}")
        return nextvals

    def is_next_val(self, nextval, value1, value2):
        self.logger.debug(f"Comparing: {value1} -> {value2} in nextval[{type}]")
        if value1 in nextval and value2 in nextval and nextval[value2] == nextval[value1] + 1:
            return True
        else:
            return False

    def is_roman(self, number):
        #check if a number is a roman numeral
        #reg = '^M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$'
        self.logger.debug(f"Checking if value is Roman: {number}")

        if number in ['iia', 'iib', 'iic', 'iid', 'iiia', 'iiib', 'iva', 'va', 'vb', 'vc', 'vd', 'via', 'viia']:
            return True

        reg = '^(X|IX|IV|V?I{0,3})$'
        try:
            tmp = str(number)
            if re.search(reg, tmp.upper()):
                return True
            else:
                return False
        except Exception as e:
            self.logger.warning(f"Failed Roman check on {number}: {e}")
            return False

    def is_decimal(self, value):
        if re.match(r'\d+[a-zA-Z]*$', value) != None:
            return True
        else:
            return False
    
    def value_type(self, value):
        try:
            isDecimal  = self.is_decimal(value)
            if isDecimal == True:
                return DECIMAL 
            isRoman = self.is_roman(value)
            if isRoman == True:
                return ROMAN 
            elif re.match('[a-z]+$', value) != None:
                return SMALLSTRING
            else:
                return GENSTRING
        except Exception as e:
            self.logger.error(f"Failed to determine value type for {value}: {e}")
            return GENSTRING  # Fallback 
    
    # compares two section numbers and returns 
    # 0 if value1 and value2 are at the same level
    # 1 if value2 is higher in hierarchy that value1
    # -1 if value2 is lower in hierarchy than value1
    # Example: (1,a) = -1
    #          (a,2) = 1
    #          (a,b) = 0 
    def comp_special_nums(self, value1, value2):
        self.logger.debug(f"Checking special comparison: {value1} vs {value2}")
        if value1 == 'i' and value2 == 'j':
            retval = (SMALLSTRING, 0) 
        elif value2 == 'i' and (value1 == 'h' or value1 == 'hh' or value1 == 'ha'):
            retval = (SMALLSTRING, 0) 
        elif value2 == 'x' and value1 == 'w':
            retval = (SMALLSTRING, 0) 
        elif value2 == 'y' and value1 == 'x':
            retval = (SMALLSTRING, 0) 
        elif value2 == 'x' and value1 == 'ix':
            retval = (ROMAN, 0) 
        elif value2 == 'xi' and value1 == 'x':
            retval = (ROMAN, 0) 
        elif value2 == 'v' and value1 == 'u':
            retval = (SMALLSTRING, 0) 
        elif value2 == 'w' and value1 == 'v':
            retval = (SMALLSTRING, 0) 
        else:
            retval = None

        return retval

    def comp_nums(self, depth, value1, value2, valueType1):
        #print 'value1: %s type:%d value2: %s type: %d' % (value1, valueType1, value2, valueType2)
        # handle the special case of i

        self.logger.debug(f"Comparing at depth {depth}: {value1} ({valueType1}) vs {value2}")
        valueType2 = self.value_type(value2)
        if valueType1 == ARTICLE:
            compval = -1
        else:
            retval = self.comp_special_nums(value1, value2)
            if retval != None:
                (valueType2, compval) = retval
            else:
                if valueType1 == None:
                    valueType1 = self.value_type(value1)

                compval    = self.comp_level(depth, value1, value2, valueType1, valueType2)

        i = compval 
        while i < 0:
            self.depthTypes[depth-i] = -1
            self.valnum    [depth-i] = -1
            i += 1
        # store the state
        self.valnum    [depth - compval] = value2
        self.depthTypes[depth - compval] = valueType2
        return (valueType2, compval)
        

    def prev_level_match(self, value, valueType, depth):
        self.logger.debug(f"Searching previous match for: {value} of type {valueType} at depth {depth}")

        matches = []
        for i in range(0, depth):
            if valueType == self.depthTypes[i]:
                matches.append(i)

        if len(matches) <= 0:
            depthmatch = None
        else:
            finalmatch = []
            nextval    = self.nextvals[valueType]
            for match in matches:    
               if self.is_next_val(nextval, self.valnum[match], value):
                  finalmatch.append(match)
            if len(finalmatch) <= 0:
                matches.sort(reverse=True)
                depthmatch = matches[0]
            else:
                finalmatch.sort(reverse=True)
                depthmatch = finalmatch[0]
        if depthmatch == None:
            compval = None
        else:
            compval = depth - depthmatch
        return compval

    def comp_level(self, depth, value1, value2, valueType1, valueType2):
        if valueType1 == valueType2:
            compval =  0
        else:
            # its a new level if it starts with the starting of each type
            if value2 in ['A', '1', 'a']:
                compval = -1
            else:
                compval = self.prev_level_match(value2, valueType2, depth)
                if compval == None: 
                    # move up one level
                    compval = -1

        return compval


class CompareLevelSebi:
    """
    CompareLevel: robust hierarchical comparator and section depth helper.

    - comp_nums(depth, value1, value2, valueType1) keeps the same return
      semantics you already use in get_bulletins():
        returns (valueType2, compVal)
      where compVal == old_depth - new_depth
        - compVal < 0 : go deeper (e.g. -1 = one level deeper)
        - compVal == 0: same level
        - compVal > 0 : move up (e.g. 1 = one level up)

    - get_section_level(value) returns the 0-based level index for dotted
      section numbers:
        '11'       -> 0  (level1)
        '11.1'     -> 1  (level2)
        '11.1.1'   -> 2  (level3)
      alphabets/roman => level index 3 (level4)
    """

    def __init__(self, val=None, depthType=None):
        self.logger = logging.getLogger(__name__)
        # mirror your original internal state arrays (6 entries)
        self.depthTypes = [depthType, -1, -1, -1, -1, -1]
        self.valnum     = [val, None, None, None, None, None]
        self.nextvals   = self._get_next_vals()

    # ------------------------
    # utilities / lookups
    # ------------------------
    def _get_next_vals(self):
        """Create lookup maps for "is next" checks (keeps behavior you had)."""
        nextvals = {}
        try:
            nextvals[DECIMAL] = {str(i): i - 1 for i in range(1, 201)}
            romans = [
                'i','ii','iii','iv','v','vi','vii','viii','ix','x',
                'xi','xii','xiii','xiv','xv','xvi','xvii','xviii','xix','xx'
            ]
            nextvals[ROMAN] = {v: i for i, v in enumerate(romans)}
            nextvals[SMALLSTRING] = {chr(97 + i): i for i in range(26)}  # a..z
            nextvals[GENSTRING] = {}
        except Exception as e:
            self.logger.error(f"Failed in _get_next_vals: {e}")
        return nextvals

    def _normalize(self, token: str) -> str:
        """Normalize tokens:
           - strip whitespace
           - remove surrounding parentheses like '(a)' -> 'a'
           - remove trailing '.' or ')' e.g. '11.1.' -> '11.1'
           - remove leading '(' if present
        """
        if token is None:
            return ''
        t = token.strip()

        # remove leading parentheses/spaces
        t = re.sub(r'^[\s\(]+', '', t)
        # remove trailing dots, closing paren, colons, spaces
        t = re.sub(r'[\s\.\)\:]+$', '', t)
        # if wrapped like "(...)", remove the wrapping now (safe after above)
        m = re.match(r'^\((.*)\)$', t)
        if m:
            t = m.group(1)
        return t

    def _is_dotted_decimal(self, token: str) -> bool:
        return re.fullmatch(r'\d+(?:\.\d+)*', token) is not None

    def is_decimal(self, value: str) -> bool:
        """Matches simple integers (no dots) possibly followed by letters in your original code."""
        # keep your original-ish behavior for detection
        try:
            return re.match(r'^\d+[a-zA-Z]*$', value) is not None
        except Exception:
            return False

    def is_roman(self, number: str) -> bool:
        """Roman detection (keeps your special exceptions as well)."""
        if not number:
            return False
        n = str(number).lower()
        if n in ['iia','iib','iic','iid','iiia','iiib','iva','va','vb','vc','vd','via','viia']:
            return True
        try:
            reg = r'^(M{0,4}(CM|CD|D?C{0,3})' \
                  r'(XC|XL|L?X{0,3})(IX|IV|V?I{0,3}))$'
            return re.match(reg, n.upper()) is not None
        except Exception:
            return False

    def value_type(self, value: str) -> int:
        """Return the token type constant (DECIMAL / ROMAN / SMALLSTRING / GENSTRING)"""
        try:
            if value is None:
                return GENSTRING
            v = self._normalize(str(value))
            if self._is_dotted_decimal(v):
                return DECIMAL
            if self.is_decimal(v):
                return DECIMAL
            if self.is_roman(v):
                return ROMAN
            if re.match(r'^[a-z]+$', v, re.IGNORECASE):
                return SMALLSTRING
            return GENSTRING
        except Exception as e:
            self.logger.error(f"Failed to determine value type for {value}: {e}")
            return GENSTRING

    # ------------------------
    # section-level helper
    # ------------------------
    def get_section_level(self, raw_value: str) -> int:
        """
        Return 0-based section level index for dotted numeric sections.
          '11'       -> 0 (level1)
          '11.1'     -> 1 (level2)
          '11.1.1'   -> 2 (level3)
        Alphabets / roman -> 3 (level4)
        Non-detectable -> 0 (fallback)
        """
        v = self._normalize(raw_value)
        if self._is_dotted_decimal(v):
            parts = [p for p in v.split('.') if p != '']
            return max(0, len(parts) - 1)
        # single integer
        if re.fullmatch(r'\d+', v):
            return 0
        if self.is_roman(v) or re.fullmatch(r'[A-Za-z]+', v):
            return 3  # level4 index
        return 0

    # ------------------------
    # original comparison helpers (kept & slightly hardened)
    # ------------------------
    def comp_special_nums(self, value1, value2):
        """Existing special-case mapping preserved."""
        self.logger.debug(f"Checking special comparison: {value1} vs {value2}")
        if value1 == 'i' and value2 == 'j':
            retval = (SMALLSTRING, 0)
        elif value2 == 'i' and (value1 == 'h' or value1 == 'hh' or value1 == 'ha'):
            retval = (SMALLSTRING, 0)
        elif value2 == 'x' and value1 == 'w':
            retval = (SMALLSTRING, 0)
        elif value2 == 'y' and value1 == 'x':
            retval = (SMALLSTRING, 0)
        elif value2 == 'x' and value1 == 'ix':
            retval = (ROMAN, 0)
        elif value2 == 'xi' and value1 == 'x':
            retval = (ROMAN, 0)
        elif value2 == 'v' and value1 == 'u':
            retval = (SMALLSTRING, 0)
        elif value2 == 'w' and value1 == 'v':
            retval = (SMALLSTRING, 0)
        else:
            retval = None
        return retval

    def prev_level_match(self, value, valueType, depth):
        """
        Find a previous depth that matches this valueType and is either a direct
        successor (if possible) or the most recent match.
        """
        self.logger.debug(f"Searching previous match for: {value} type {valueType} at depth {depth}")
        matches = []
        for i in range(0, depth):
            if valueType == self.depthTypes[i]:
                matches.append(i)

        if not matches:
            return None

        finalmatch = []
        nextval = self.nextvals.get(valueType, {})
        for match in matches:
            try:
                if self.is_next_val(nextval, self.valnum[match], value):
                    finalmatch.append(match)
            except Exception:
                # skip if we can't compare
                pass

        depthmatch = (finalmatch or matches)[-1]
        return depth - depthmatch

    def is_next_val(self, nextval, value1, value2):
        """same as your original helper"""
        try:
            return value1 in nextval and value2 in nextval and nextval[value2] == nextval[value1] + 1
        except Exception:
            return False

    def comp_level(self, depth, value1, value2, valueType1, valueType2):
        """Fallback comparison logic unchanged in semantics."""
        if valueType1 == valueType2:
            return 0
        # special case starting things
        if value2 in ['A', '1', 'a']:
            return -1
        compval = self.prev_level_match(value2, valueType2, depth)
        return compval if compval is not None else -1

    # ------------------------
    # MAIN: comp_nums (keeps your signature)
    # ------------------------
    def comp_nums(self, depth, value1, value2, valueType1):
        """
        Compare two section/group tokens and determine movement in hierarchy.

        - depth: current depth index (0-based; 0 == level1)
        - value1: previous token string (raw)
        - value2: current token string (raw)
        - valueType1: previous token's type (use your constants)

        Returns: (valueType2, compVal) where compVal == depth - new_depth
        """
        try:
            v1 = self._normalize(str(value1)) if value1 is not None else ''
            v2 = self._normalize(str(value2)) if value2 is not None else ''

            # 1) If the incoming token is a dotted numeric like 11.1.1 or 11.2
            if self._is_dotted_decimal(v2):
                parts = [p for p in v2.split('.') if p != '']
                new_depth = max(0, len(parts) - 1)
                valueType2 = DECIMAL
                compval = depth - new_depth

            # 2) Single integer bullet like "1" or "(1)" or "1."
            elif re.fullmatch(r'\d+', v2):
                # treat numeric bullet as one level deeper than current
                new_depth = depth + 1
                valueType2 = DECIMAL
                compval = depth - new_depth

            # 3) alphabetic bullets: a, aa, (a) etc. -> treat as deeper level
            elif re.fullmatch(r'[A-Za-z]+', v2):
                # valueType2 = SMALLSTRING
                # new_depth = depth + 1
                # compval = depth - new_depth
                valueType2 = SMALLSTRING
                if valueType1 == SMALLSTRING:
                    # Stay on the same level (siblings)
                    new_depth = depth
                else:
                    # First alphabet after a number/roman → go one level deeper
                    new_depth = depth + 1
                compval = depth - new_depth

            # 4) roman bullets: i, iv -> treat as deeper
            elif self.is_roman(v2):
                valueType2 = ROMAN
                new_depth = depth + 1
                compval = depth - new_depth

            # 5) fallback: attempt your existing special cases or prev_level logic
            else:
                retval = self.comp_special_nums(v1, v2)
                if retval is not None:
                    (valueType2, compval) = retval
                else:
                    if valueType1 is None:
                        valueType1 = self.value_type(v1)
                    valueType2 = self.value_type(v2)
                    compval = self.comp_level(depth, v1, v2, valueType1, valueType2)

            # Maintain same update semantics your code expects:
            i = compval
            while i < 0:
                idx = depth - i
                if 0 <= idx < len(self.depthTypes):
                    self.depthTypes[idx] = -1
                if 0 <= idx < len(self.valnum):
                    self.valnum[idx] = -1
                i += 1

            # store the state at the resulting depth index (depth - compval)
            store_index = depth - compval
            if 0 <= store_index < len(self.valnum):
                self.valnum[store_index] = value2
                self.depthTypes[store_index] = valueType2

            return (valueType2, compval)

        except Exception as e:
            self.logger.exception(f"comp_nums failed for '{value1}' -> '{value2}': {e}")
            # fallback: treat as same level
            vt = self.value_type(value2)
            return (vt, 0)
