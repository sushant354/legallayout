import numpy as np
from pdfminer.high_level import extract_pages
from pdfminer.layout import LTChar
from sklearn.mixture import GaussianMixture

def compute_optimal_char_margin(pdf_path):

    char_gaps = []

    # ---------------------------------------------------------
    # Recursively collect LTChar
    # ---------------------------------------------------------
    def walk(obj, chars):
        if isinstance(obj, LTChar):
            chars.append(obj)
        if hasattr(obj, "_objs"):
            for child in obj._objs:
                walk(child, chars)

    def baseline(c):
        return (c.y0 + c.y1) / 2

    # ---------------------------------------------------------
    # Extract char gaps
    # ---------------------------------------------------------
    try:
        for layout in extract_pages(pdf_path):

            chars = []
            for obj in layout:
                walk(obj, chars)

            if not chars:
                continue

            chars.sort(key=lambda c: (-baseline(c), c.x0))
            prev = None

            for c in chars:
                if prev:
                    # adaptive baseline threshold
                    if abs(baseline(prev) - baseline(c)) < c.height * 0.65:
                        gap = c.x0 - prev.x1
                        if gap > 0:
                            char_gaps.append(gap)
                prev = c

            if len(char_gaps) > 6000:
                break

    except Exception:
        return 2.0   # safe fallback

    if len(char_gaps) < 10:
        return 2.0   # insufficient data

    # ---------------------------------------------------------
    # Fit Gaussian Mixture to find small-gap cluster = char spacing
    # ---------------------------------------------------------
    data = np.array(char_gaps).reshape(-1, 1)

    gmm = GaussianMixture(n_components=2, random_state=0)
    gmm.fit(data)

    means = gmm.means_.flatten()
    char_spacing_mean = min(means)

    # scale down slightly for pdfminer
    char_margin = char_spacing_mean * 0.75

    # clamp reasonable bounds
    char_margin = max(2.0, min(3.5, round(char_margin, 2)))

    return char_margin
