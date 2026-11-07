import re

from .Utils import starts_with_bullet_glyph, is_bullet_glyph

BULLET_MARK_START = '﷓'
BULLET_MARK_END = '﷔'
BULLET_MARK_RE = re.compile(BULLET_MARK_START + r'([0-9.\-]+)\|([0-9.]+)' + BULLET_MARK_END)
BULLET_PARAGRAPH_RE = re.compile('<p>' + BULLET_MARK_START + r'([0-9.\-]+)\|([0-9.]+)' + BULLET_MARK_END
                                 + '(.*?)</p>\n', re.DOTALL)


class GlyphBulletLists:

    @staticmethod
    def without_leading_glyph(styled):
        out = []
        in_tag = False
        removed = False
        for char in styled:
            if char == '<':
                in_tag = True
            if not in_tag and not removed and is_bullet_glyph(char):
                removed = True
                continue
            out.append(char)
            if char == '>':
                in_tag = False
        return ''.join(out)

    def bullet_text_is_styled(self, units):
        signatures = set()
        seen_glyph = False
        for unit in units:
            if unit['kind'] != 'text' or not unit['value'].strip():
                continue
            styled = unit.get('styled', unit['value'])
            if not seen_glyph:
                styled = self.without_leading_glyph(styled)
                seen_glyph = True
            signatures.add(self.unit_style_signature(dict(unit, styled=styled)))
        return bool(signatures) and 'n' not in signatures

    def emit_paragraph(self, tag, units, is_heading=False):
        if tag == "p" and not is_heading:
            first = next((u for u in units if u['kind'] == 'text' and u['value'].strip()), None)
            if (first is not None and starts_with_bullet_glyph(first['value'])
                    and not self.bullet_text_is_styled(units)):
                height = max(first.get('y1', 0) - first.get('y0', 0), 1.0)
                mark = f"{BULLET_MARK_START}{first.get('x0', 0.0):.1f}|{height:.1f}{BULLET_MARK_END}"
                first['styled'] = mark + first.get('styled', first['value'])
        super().emit_paragraph(tag, units, is_heading)

    @staticmethod
    def bullet_depths(items):
        clusters = []
        for x0, height, _ in sorted(items, key=lambda item: item[0]):
            if clusters and x0 - clusters[-1][0] <= max(height, clusters[-1][1]):
                continue
            clusters.append((x0, height))
        depths = []
        for x0, height, _ in items:
            for rank, (cluster_x0, cluster_height) in enumerate(clusters):
                if abs(x0 - cluster_x0) <= max(height, cluster_height):
                    depths.append(rank)
                    break
            else:
                depths.append(0)
        return depths

    @classmethod
    def bullet_run_html(cls, items):
        html = ''
        stack = []
        for depth, (_, _, body) in zip(cls.bullet_depths(items), items):
            if not stack:
                html += "<ul>\n<li>" + body
                stack.append(depth)
            elif depth > stack[-1]:
                html += "\n<ul>\n<li>" + body
                stack.append(depth)
            else:
                while len(stack) > 1 and depth < stack[-1]:
                    html += "</li>\n</ul>\n"
                    stack.pop()
                html += "</li>\n<li>" + body
        return html + "</li>\n</ul>\n" * len(stack)

    @classmethod
    def convert_bullet_runs(cls, html):
        if BULLET_MARK_START not in html:
            return html
        out = []
        position = 0
        run = []
        run_start = 0
        run_end = 0

        def flush():
            if run:
                out.append(html[position:run_start])
                out.append(cls.bullet_run_html(run))

        for match in BULLET_PARAGRAPH_RE.finditer(html):
            if run and match.start() == run_end:
                run.append((float(match.group(1)), float(match.group(2)), match.group(3)))
                run_end = match.end()
                continue
            if run:
                flush()
                position = run_end
                run = []
            run = [(float(match.group(1)), float(match.group(2)), match.group(3))]
            run_start = match.start()
            run_end = match.end()
        if run:
            flush()
            position = run_end
        out.append(html[position:])
        return BULLET_MARK_RE.sub('', ''.join(out))

    def close_html(self):
        self.builder = self.convert_bullet_runs(self.builder)
        return super().close_html()
