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

    def _ensure_capacity(self, index):
        while len(self.valnum) <= index:
            self.valnum.append(None)
            self.depthTypes.append(-1)

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
        self.logger.debug(f"Checking Roman: {number}")

        try:
            s = str(number).upper().strip()

            roman_pattern = (
                r"(X{0,3})"
                r"(IX|IV|V?I{0,3})$"
            )

            if not re.match(roman_pattern, s):
                return False

            return self._roman_to_int(s) > 0

        except Exception as e:
            self.logger.warning(f"Roman check failed for {number}: {e}")
            return False
    
    def _roman_to_int(self, s: str) -> int:
        roman = {
            'I': 1, 'V': 5, 'X': 10, 'L': 50,
            'C': 100, 'D': 500, 'M': 1000
        }

        total = 0
        prev = 0

        for ch in reversed(s):
            val = roman.get(ch, 0)

            if val < prev:
                total -= val
            else:
                total += val
                prev = val

        return total

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
    
    # a single lowercase i/v/x is ambiguous - it is both a valid roman numeral
    # and a valid alphabetic list marker. Resolve it using the actual type of
    # the previous item (the strongest signal available) and, failing that,
    # plain alphabetical adjacency, instead of an incomplete hardcoded table.
    ROMAN_ALPHA_AMBIGUOUS = ('i', 'v', 'x')
    LEGACY_ALPHA_TO_I = ('h', 'hh', 'ha')

    def classify_value2(self, value1, valueType1, value2):
        if not self.is_roman(value2):
            return self.value_type(value2)

        v2l = str(value2).strip().lower()
        if len(v2l) != 1 or v2l not in self.ROMAN_ALPHA_AMBIGUOUS:
            return ROMAN

        if valueType1 == ROMAN:
            return ROMAN

        v1l = str(value1).strip().lower() if value1 is not None else ""
        if v2l == 'i' and v1l in self.LEGACY_ALPHA_TO_I:
            return SMALLSTRING

        if self._is_adjacent_alpha(v1l, v2l):
            return SMALLSTRING

        return ROMAN

    def _is_adjacent_alpha(self, value1, value2):
        v1 = str(value1).strip().lower() if value1 is not None else ""
        v2 = str(value2).strip().lower()
        return (
            len(v1) == 1 and len(v2) == 1
            and v1.isalpha() and v2.isalpha()
            and ord(v2) == ord(v1) + 1
        )

    # compares two section numbers and returns
    # 0 if value1 and value2 are at the same level
    # 1 if value2 is higher in hierarchy that value1
    # -1 if value2 is lower in hierarchy than value1
    # Example: (1,a) = -1
    #          (a,2) = 1
    #          (a,b) = 0
    def comp_nums(self, depth, value1, value2, valueType1):
        self.logger.debug(f"Comparing at depth {depth}: {value1} ({valueType1}) vs {value2}")
        try:
            self._ensure_capacity(depth)
            if valueType1 == ARTICLE:
                valueType2 = self.value_type(value2)
                compval = -1
            else:
                if valueType1 is None:
                    valueType1 = self.value_type(value1)

                valueType2 = self.classify_value2(value1, valueType1, value2)
                compval    = self.comp_level(depth, value1, value2, valueType1, valueType2)

            self._ensure_capacity(depth - compval)
            i = compval
            while i < 0:
                self.depthTypes[depth-i] = -1
                self.valnum    [depth-i] = -1
                i += 1
            # store the state
            self.valnum    [depth - compval] = value2
            self.depthTypes[depth - compval] = valueType2
            return (valueType2, compval)
        except Exception as e:
            self.logger.exception(f"comp_nums failed for '{value1}' -> '{value2}': {e}")
            return GENSTRING, 0


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
        elif self._is_adjacent_alpha(value1, value2):
            # value1/value2 are literally consecutive letters (e.g. 'h'->'i') -
            # they must be siblings even if one of them was earlier typed as
            # ROMAN due to lack of context, so trust the letters over the type
            compval = 0
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

    def __init__(self, val=None, depthType=None):

        self.logger = logging.getLogger(__name__)

        self.depthTypes = [depthType, -1, -1, -1, -1, -1]
        self.valnum = [val, None, None, None, None, None]

        self.roman_order = [
            "i", "ii", "iii", "iv", "v",
            "vi", "vii", "viii", "ix", "x",
            "xi", "xii", "xiii", "xiv", "xv",
            "xvi", "xvii", "xviii", "xix", "xx",
            "xxi", "xxii", "xxiii", "xxiv", "xxv",
            "xxvi", "xxvii", "xxviii", "xxix", "xxx"
        ]

        self.roman_index = {
            value: index
            for index, value in enumerate(self.roman_order)
        }

    def _ensure_capacity(self, index):
        while len(self.valnum) <= index:
            self.valnum.append(None)
            self.depthTypes.append(-1)

    def _normalize(self, token: str) -> str:

        if token is None:
            return ""

        t = str(token).strip()

        t = re.sub(r'^[\s\(\[]+', '', t)
        t = re.sub(r'[\s\.\)\]\:]+$', '', t)

        return t.strip()

    def is_decimal(self, value: str) -> bool:

        value = self._normalize(value)

        return re.fullmatch(
            r'\d+(?:\.\d+)*',
            value
        ) is not None

    def is_roman(self, value: str) -> bool:

        value = self._normalize(value)

        if not value:
            return False

        roman_re = (
            r'^(M{0,4}'
            r'(CM|CD|D?C{0,3})'
            r'(XC|XL|L?X{0,3})'
            r'(IX|IV|V?I{0,3}))$'
        )

        return re.fullmatch(
            roman_re,
            value,
            re.IGNORECASE
        ) is not None

    def is_alpha(self, value: str) -> bool:

        value = self._normalize(value)

        return re.fullmatch(
            r'[A-Za-z]+',
            value
        ) is not None

    def value_type(self, value):

        v = self._normalize(value)

        if self.is_decimal(v):
            return DECIMAL

        if self.is_roman(v):
            return ROMAN

        if self.is_alpha(v):
            return SMALLSTRING

        return GENSTRING

    def resolve_alpha_vs_roman(self, prev, curr, prev_type=None):

        prev = self._normalize(prev).lower()
        curr = self._normalize(curr).lower()

        if curr not in self.roman_index:
            return SMALLSTRING

        # multi-char => almost always roman
        if len(curr) > 1:
            return ROMAN

        ambiguous = {"i", "v", "x"}

        if curr not in ambiguous:
            return SMALLSTRING

        # trust the actual recorded type of the previous item first - it's a
        # stronger signal than re-deriving family membership from characters
        if prev_type == ROMAN and prev in self.roman_index:
            if self.roman_index[curr] == self.roman_index[prev] + 1:
                return ROMAN

        if (
            prev_type == SMALLSTRING and
            len(prev) == 1 and prev.isalpha() and
            ord(curr) == ord(prev) + 1
        ):
            return SMALLSTRING

        # no reliable prior type - fall back to raw adjacency heuristics
        # h -> i -> j
        if (
            len(prev) == 1 and
            len(curr) == 1 and
            prev.isalpha()
        ):

            if ord(curr) == ord(prev) + 1:
                return SMALLSTRING

        # roman continuation
        if prev in self.roman_index:

            if (
                self.roman_index[curr]
                ==
                self.roman_index[prev] + 1
            ):
                return ROMAN

        return ROMAN

    def is_same_family(self, v1, v2, t1, t2):

        if t1 != t2:
            return False

        # case-sensitive alpha families
        if t1 == SMALLSTRING:

            if v1.islower() != v2.islower():
                return False

        # case-sensitive roman families
        if t1 == ROMAN:

            if v1.islower() != v2.islower():
                return False

        return True

    def get_decimal_depth(self, token):

        token = self._normalize(token)

        parts = [
            p for p in token.split('.')
            if p.strip()
        ]

        return max(0, len(parts) - 1)

    def comp_nums(self, depth, value1, value2, valueType1):

        try:

            self._ensure_capacity(depth)

            v1 = self._normalize(value1)
            v2 = self._normalize(value2)

            # -----------------------------------------
            # DECIMAL
            # -----------------------------------------

            if self.is_decimal(v2):

                valueType2 = DECIMAL

                new_depth = self.get_decimal_depth(v2)

            # -----------------------------------------
            # ALPHA / ROMAN
            # -----------------------------------------

            elif self.is_alpha(v2) or self.is_roman(v2):

                valueType2 = self.resolve_alpha_vs_roman(
                    v1,
                    v2,
                    valueType1
                )

                # same family continuation
                if self.is_same_family(
                    v1,
                    v2,
                    valueType1,
                    valueType2
                ):

                    new_depth = depth

                else:

                    # sibling restoration
                    found = False

                    for i in range(depth, -1, -1):

                        prev_type = self.depthTypes[i]
                        prev_val = self.valnum[i]

                        if prev_val is None:
                            continue

                        prev_val = self._normalize(prev_val)

                        if self.is_same_family(
                            prev_val,
                            v2,
                            prev_type,
                            valueType2
                        ):

                            new_depth = i
                            found = True
                            break

                    if not found:
                        new_depth = depth + 1

            # -----------------------------------------
            # FALLBACK
            # -----------------------------------------

            else:

                valueType2 = GENSTRING
                new_depth = depth

            compval = depth - new_depth

            store_index = max(0, new_depth)
            self._ensure_capacity(store_index)

            self.valnum[store_index] = v2
            self.depthTypes[store_index] = valueType2

            return valueType2, compval

        except Exception as e:

            self.logger.exception(
                f"comp_nums failed for '{value1}' -> '{value2}': {e}"
            )

            return GENSTRING, 0