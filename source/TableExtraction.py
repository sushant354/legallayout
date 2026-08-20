import camelot
import logging
import re
import statistics
import numpy as np
import pandas as pd

class TableExtraction:
    def __init__(self,pdf_path,pg_num, pdf_type, scanned_copy):
        self.logger = logging.getLogger(__name__)
        self.pdf_type = pdf_type
        self.tables, self.table_bbox = self.get_table_and_bbox(pdf_path,pg_num, scanned_copy)
    
    # --- func to find the table contents and their coordinates ---
    def get_table_and_bbox(self,pdf_path,page_num, scanned_copy):
        table = {}
        bbox = {}
        if scanned_copy:
            return table, bbox
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

class TBItem:
    def __init__(self, tb_obj, x0, y0, x1, y1, text,
                 n_textlines=1, line_height_est=10.0, is_split_child=False):
        self.tb_obj = tb_obj
        self.x0 = x0
        self.y0 = y0
        self.x1 = x1
        self.y1 = y1
        self.text = text
        self.n_textlines = n_textlines
        self.line_height_est = line_height_est
        self.is_split_child = is_split_child

    @property
    def width(self):
        return self.x1 - self.x0

    @property
    def height(self):
        return self.y1 - self.y0


class PenalizedLogisticClassifier:
    FEATURE_ORDER = ()

    def __init__(self, weights=None, bias=0.0):
        self.weights = dict(weights) if weights else {f: 0.0 for f in self.FEATURE_ORDER}
        self.bias = bias

    @staticmethod
    def _sigmoid(z):
        z = max(-60.0, min(60.0, z))
        return 1.0 / (1.0 + np.exp(-z))

    def _vectorize(self, features):
        return np.array([features.get(f, 0.0) for f in self.FEATURE_ORDER], dtype=float)

    def predict_proba(self, features):
        x = self._vectorize(features)
        w = np.array([self.weights.get(f, 0.0) for f in self.FEATURE_ORDER], dtype=float)
        z = float(np.dot(w, x)) + self.bias
        return self._sigmoid(z)

    def predict(self, features, threshold=0.5):
        return self.predict_proba(features) >= threshold

    def fit(self, X, y, l2=0.05, lr=0.5, epochs=300):
        if not X or not y or len(X) != len(y):
            raise ValueError("X and y must be non-empty and the same length")

        n = len(X)
        feat_matrix = np.array([[fd.get(f, 0.0) for f in self.FEATURE_ORDER] for fd in X], dtype=float)
        labels = np.array(y, dtype=float)

        w = np.array([self.weights.get(f, 0.0) for f in self.FEATURE_ORDER], dtype=float)
        b = self.bias

        for _ in range(epochs):
            z = feat_matrix.dot(w) + b
            preds = 1.0 / (1.0 + np.exp(-np.clip(z, -60.0, 60.0)))
            error = preds - labels

            grad_w = feat_matrix.T.dot(error) / n + 2.0 * l2 * w
            grad_b = error.mean()

            w -= lr * grad_w
            b -= lr * grad_b

        self.weights = {f: float(w[i]) for i, f in enumerate(self.FEATURE_ORDER)}
        self.bias = float(b)
        return self

    def update_supervised(self, features, label, lr=0.15, l2=0.02):
        x = self._vectorize(features)
        w = np.array([self.weights.get(f, 0.0) for f in self.FEATURE_ORDER], dtype=float)
        z = float(np.dot(w, x)) + self.bias
        pred = self._sigmoid(z)

        error = pred - float(label)
        grad_w = error * x + 2.0 * l2 * w
        grad_b = error

        w = w - lr * grad_w
        b = self.bias - lr * grad_b

        self.weights = {f: float(w[i]) for i, f in enumerate(self.FEATURE_ORDER)}
        self.bias = float(b)
        return pred

    def reinforce(self, features, action, reward, lr=0.1, l2=0.02):
        x = self._vectorize(features)
        w = np.array([self.weights.get(f, 0.0) for f in self.FEATURE_ORDER], dtype=float)
        z = float(np.dot(w, x)) + self.bias
        p = self._sigmoid(z)
        a = 1.0 if action else 0.0

        score = (a - p) * x
        grad_w = reward * score - 2.0 * l2 * w
        grad_b = reward * (a - p)

        w = w + lr * grad_w
        b = self.bias + lr * grad_b

        self.weights = {f: float(w[i]) for i, f in enumerate(self.FEATURE_ORDER)}
        self.bias = float(b)
        return p

class HeaderRowClassifier(PenalizedLogisticClassifier):
    FEATURE_ORDER = ("fill_diff", "len_ratio", "lines_diff", "gap_ratio")

    @classmethod
    def default(cls):
        return cls(
            weights={
                "fill_diff": 3.2,
                "len_ratio": 2.6,
                "lines_diff": 1.6,
                "gap_ratio": 1.1,
            },
            bias=-1.6,
        )


class RegionMergeClassifier(PenalizedLogisticClassifier):
    FEATURE_ORDER = ("col_overlap_ratio", "gap_norm", "xspan_overlap", "col_count_diff")

    @classmethod
    def default(cls):
        return cls(
            weights={
                "col_overlap_ratio": 3.0,
                "gap_norm": -1.3,
                "xspan_overlap": 2.0,
                "col_count_diff": -2.2,
            },
            bias=-0.6,
        )


class ContinuationClassifier(PenalizedLogisticClassifier):
    FEATURE_ORDER = ("col_coverage", "xspan_overlap", "top_closeness", "row_support", "align_quality")

    @classmethod
    def default(cls):
        return cls(
            weights={
                "col_coverage": 3.0,
                "xspan_overlap": 2.0,
                "top_closeness": 1.6,
                "row_support": 1.0,
                "align_quality": 2.2,
            },
            bias=-2.6,
        )


class ColumnTracker:
    def __init__(self, cols):
        self.cols = [
            {"x0": c0, "x1": c1, "half_width": max((c1 - c0) / 2.0, 1e-6), "n": 1}
            for c0, c1 in cols
        ]

    def bounds(self):
        return [(c["x0"], c["x1"]) for c in self.cols]

    def match(self, item, tol=0.0, max_overrun_ratio=1.6):
        center = (item.x0 + item.x1) / 2.0
        best, best_dist, best_overrun = None, None, None
        for ci, c in enumerate(self.cols):
            col_center = (c["x0"] + c["x1"]) / 2.0
            half_width = max(c["half_width"], tol)
            dist = abs(center - col_center)
            if dist <= half_width * max_overrun_ratio and (best_dist is None or dist < best_dist):
                best, best_dist = ci, dist
                best_overrun = max(0.0, dist - half_width) / half_width
        return best, best_overrun

    def update(self, idx, item):
        c = self.cols[idx]
        n = c["n"]
        c["x0"] = (c["x0"] * n + item.x0) / (n + 1)
        c["x1"] = (c["x1"] * n + item.x1) / (n + 1)
        c["half_width"] = max(c["half_width"], (item.x1 - item.x0) / 2.0)
        c["n"] = n + 1

    def add_column(self, x0, x1):
        self.cols.append({"x0": x0, "x1": x1, "half_width": max((x1 - x0) / 2.0, 1e-6), "n": 1})
        return len(self.cols) - 1


class BorderlessTableExtraction:
    def __init__(self, all_tbs, pdf_type,
                 page_width, page_height, adaptive_ratio=0.02,
                 min_col_support=3, min_narrow_col_support=2,
                 min_rows=3, min_cols=2, min_fill_ratio=0.4,
                 min_fill_ratio_with_spine=0.15,
                 max_col_width_ratio=0.6,
                 min_col_row_support_ratio=0.34,
                 min_multi_col_row_ratio=0.5,
                 header_classifier=None,
                 header_probability_threshold=0.5,
                 region_merge_classifier=None,
                 region_merge_probability_threshold=0.5,
                 continuation_classifier=None,
                 continuation_probability_threshold=0.5,
                 continuation_template=None,
                 continuation_page_coverage=0.30,
                 continuation_bottom_margin_ratio=0.15,
                 continuation_top_band_ratio=0.30,
                 continuation_min_fill_ratio=0.12,
                 continuation_max_skip_rows=3,
                 min_continuation_rows=2,
                 rl_enabled=True,
                 rl_lr=0.05,
                 rl_l2=0.02):
        self.logger = logging.getLogger(__name__)
        self.pdf_type = pdf_type
        self.all_tbs = all_tbs

        self.page_width = page_width
        self.page_height = page_height
        self.adaptive_ratio = adaptive_ratio

        self.min_col_support = min_col_support
        self.min_narrow_col_support = min_narrow_col_support
        self.min_rows = min_rows
        self.min_cols = min_cols
        self.min_fill_ratio = min_fill_ratio
        self.min_fill_ratio_with_spine = min_fill_ratio_with_spine
        self.max_col_width_ratio = max_col_width_ratio

        self.min_col_row_support_ratio = min_col_row_support_ratio
        self.min_multi_col_row_ratio = min_multi_col_row_ratio

        self.header_classifier = self._resolve_classifier(header_classifier, HeaderRowClassifier)
        self.header_probability_threshold = header_probability_threshold

        self.region_merge_classifier = self._resolve_classifier(region_merge_classifier, RegionMergeClassifier)
        self.region_merge_probability_threshold = region_merge_probability_threshold

        self.continuation_classifier = self._resolve_classifier(continuation_classifier, ContinuationClassifier)
        self.continuation_probability_threshold = continuation_probability_threshold
        self.continuation_template = continuation_template
        self.continuation_page_coverage = continuation_page_coverage
        self.continuation_bottom_margin_ratio = continuation_bottom_margin_ratio
        self.continuation_top_band_ratio = continuation_top_band_ratio
        self.continuation_min_fill_ratio = continuation_min_fill_ratio
        self.continuation_max_skip_rows = continuation_max_skip_rows
        self.min_continuation_rows = min_continuation_rows

        self.rl_enabled = rl_enabled
        self.rl_lr = rl_lr
        self.rl_l2 = rl_l2

        self.table_headers = {}
        self.table_header_scores = {}
        self.table_header_features = {}
        self.table_columns = {}
        self.table_y = {}
        self.table_nrows = {}
        self.table_is_continuation = {}
        self.table_item_objs = {}
        self.continuation_out = None
        self.continuation_passthrough = None
        self.header_events = []
        self.region_merge_events = []

        self.tables, self.table_bbox = self.get_table_and_bbox()

    @staticmethod
    def _resolve_classifier(classifier, classifier_cls):
        return classifier if classifier is not None else classifier_cls.default()

    def px(self, ratio=None):
        ratio = self.adaptive_ratio if ratio is None else ratio
        return self.page_width * ratio

    def py(self, ratio=None):
        ratio = self.adaptive_ratio if ratio is None else ratio
        return self.page_height * ratio

    def box_x(self, item, ratio=0.10):
        return item.width * ratio

    def box_y(self, item, ratio=0.10):
        return item.height * ratio

    def get_table_and_bbox(self):
        table, bbox = {}, {}
        try:
            items = self._collect_candidate_items()

            claimed_ids = set()
            if self.continuation_template and len(items) >= self.min_continuation_rows:
                self._detect_continuation_table(items, table, bbox, len(table), claimed_ids)

            remaining = [it for it in items if id(it) not in claimed_ids]
            if len(remaining) >= self.min_rows:
                self._detect_page_tables(remaining, table, bbox, start_idx=len(table))

            cont_indices = [i for i, c in self.table_is_continuation.items() if c and i in bbox]
            for cont_idx in cont_indices:
                self._suppress_normal_tables_within(cont_idx, table, bbox)

            if self.continuation_passthrough and not cont_indices:
                self._suppress_normal_tables_in_span(table, bbox)

            self.continuation_out = self._compute_continuation_out(table, bbox)
        except Exception as e:
            self.logger.error(
                "Exception occurred while checking for borderless table contents: %s" % (str(e))
            )

        return table, bbox

    def _detect_page_tables(self, items, table, bbox, start_idx=0):
        try:
            line_height = self._estimate_line_height(items)

            column_clusters = self._cluster_columns(items)
            table_items, item_col_id = self._flag_table_items(items, column_clusters)
            if len(table_items) < self.min_rows:
                return

            regions = self._form_regions(table_items, item_col_id, line_height)
            regions = self._merge_related_regions(regions, item_col_id, line_height)

            idx = start_idx
            for region_order_id, region_rows in enumerate(regions):
                reward = -0.5
                try:
                    region_rows = self._trim_non_tabular_edge_rows(region_rows, item_col_id)
                    if len(region_rows) < self.min_rows:
                        continue

                    region_rows = self._prune_weak_columns(region_rows, item_col_id)
                    if len(region_rows) < self.min_rows:
                        continue

                    if not self._passes_multi_col_row_check(region_rows, item_col_id):
                        self.logger.debug(
                            "Rejected borderless region: not enough multi-column rows "
                            "(likely not a real table)"
                        )
                        continue

                    df, region_bbox, fill_ratio, n_cols, has_header, header_prob, header_features, col_ranges = self._build_table(
                        region_rows, item_col_id, region_order_id
                    )
                    if df is None:
                        continue

                    n_rows = df.shape[0]
                    col_row_counts = {}
                    for row_items in region_rows:
                        for c in set(item_col_id[id(it)] for it in row_items):
                            col_row_counts[c] = col_row_counts.get(c, 0) + 1
                    fill_bar = (
                        self.min_fill_ratio_with_spine
                        if self._has_spine_column(col_row_counts, len(region_rows))
                        else self.min_fill_ratio
                    )
                    accepted = (
                        n_rows >= self.min_rows and n_cols >= self.min_cols
                        and fill_ratio >= fill_bar
                    )

                    reward = fill_ratio if accepted else (fill_ratio - fill_bar)

                    if accepted:
                        table[idx] = df
                        bbox[idx] = region_bbox
                        self.table_headers[idx] = has_header
                        self.table_header_scores[idx] = header_prob
                        self.table_columns[idx] = col_ranges
                        self.table_y[idx] = (region_bbox[1], region_bbox[3])
                        self.table_nrows[idx] = n_rows
                        self.table_is_continuation[idx] = False
                        self.table_item_objs[idx] = {
                            id(it.tb_obj) for row in region_rows for it in row if it.tb_obj is not None
                        }
                        if header_features is not None:
                            self.table_header_features[idx] = header_features
                        self.logger.debug(
                            f"Accepted borderless table idx={idx} rows={n_rows} cols={n_cols} "
                            f"fill_ratio={round(fill_ratio, 2)} header={has_header} "
                            f"header_p={round(header_prob, 3)} bbox={region_bbox}"
                        )
                        idx += 1
                finally:
                    self._apply_region_reward(region_order_id, reward)
        except Exception as e:
            self.logger.error(
                "Exception occurred while checking for borderless table contents: %s" % (str(e))
            )

    def _collect_candidate_items(self):
        items = []
        for tb_obj, label in self.all_tbs.items():
            if label is not None:
                continue

            x0, y0, x1, y1 = tb_obj.coords
            text = None
            if hasattr(tb_obj, "extract_text_from_tb"):
                text = tb_obj.extract_text_from_tb()
            if not text or not text.strip():
                continue
            text = text.strip()

            textlines = tb_obj.tbox.findall("textline")
            n_lines = max(1, len(textlines))
            line_height_est = (y1 - y0) / n_lines if n_lines else (y1 - y0)

            split_items = self._try_split_multi_column_box(
                tb_obj, x0, y0, x1, y1, textlines, line_height_est
            )
            if split_items:
                items.extend(split_items)
            else:
                items.append(TBItem(tb_obj, x0, y0, x1, y1, text, n_lines, line_height_est))

        return items

    def _try_split_multi_column_box(self, tb_obj, x0, y0, x1, y1, textlines, line_height_est):
        if not textlines:
            return None
        try:
            first_line = textlines[0]
            fl_bbox = first_line.attrib.get("bbox")
            if not fl_bbox:
                return None
            fl_x0, fl_y0, fl_x1, fl_y1 = [float(v) for v in fl_bbox.split(",")]

            runs = first_line.findall("text")
            spans = []
            for r in runs:
                rb = r.attrib.get("bbox")
                if not rb:
                    return None
                rx0, ry0, rx1, ry1 = [float(v) for v in rb.split(",")]
                spans.append((rx0, rx1, r.text or ""))

            if len(spans) < 3:
                return None

            gaps = []
            for i in range(len(spans) - 1):
                gap = spans[i + 1][0] - spans[i][1]
                if gap > 0:
                    gaps.append(gap)
            if len(gaps) < 3:
                return None

            median_gap = statistics.median(gaps)
            mad = statistics.median([abs(g - median_gap) for g in gaps]) or self.px(0.002)

            split_indices = []
            for i in range(len(spans) - 1):
                gap = spans[i + 1][0] - spans[i][1]
                if (
                    gap > median_gap + 6 * mad
                    and gap > max(
                        line_height_est * 0.5,
                        self.box_x(TBItem(tb_obj, x0, y0, x1, y1, ""), 0.08)
                    )
                ):
                    split_indices.append(i)

            if not split_indices:
                return None

            boundaries = [0] + [i + 1 for i in split_indices] + [len(spans)]
            segments = []
            for s, e in zip(boundaries[:-1], boundaries[1:]):
                seg_spans = spans[s:e]
                if not seg_spans:
                    continue
                seg_x0 = seg_spans[0][0]
                seg_x1 = seg_spans[-1][1]
                seg_text = "".join(sp[2] for sp in seg_spans).strip()
                segments.append([seg_x0, seg_x1, seg_text])

            if len(segments) < 2:
                return None

            rest_text_parts = []
            rest_x1 = segments[-1][1]
            for tl in textlines[1:]:
                tb2 = tl.attrib.get("bbox")
                if tb2:
                    _, _, tlx1, _ = [float(v) for v in tb2.split(",")]
                    rest_x1 = max(rest_x1, tlx1)
                line_text = "".join((t.text or "") for t in tl.findall("text")).strip()
                if line_text:
                    rest_text_parts.append(line_text)

            items_out = []
            for idx, (seg_x0, seg_x1, seg_text) in enumerate(segments):
                is_last = (idx == len(segments) - 1)
                if not seg_text:
                    continue
                if is_last:
                    full_text = " ".join([seg_text] + rest_text_parts).strip()
                    items_out.append(TBItem(
                        tb_obj, seg_x0, y0, max(seg_x1, rest_x1), y1, full_text,
                        n_textlines=len(textlines), line_height_est=line_height_est,
                        is_split_child=True
                    ))
                else:
                    items_out.append(TBItem(
                        tb_obj, seg_x0, fl_y0, seg_x1, fl_y1, seg_text,
                        n_textlines=1, line_height_est=line_height_est,
                        is_split_child=True
                    ))

            return items_out if len(items_out) >= 2 else None
        except Exception:
            return None

    def _estimate_line_height(self, items):
        heights = [it.line_height_est for it in items if it.line_height_est > 0]
        if not heights:
            return self.py(0.01)
        return statistics.median(heights)

    @staticmethod
    def _auto_eps(sorted_values, k=2, *, fallback):
        n = len(sorted_values)
        if n < k + 2:
            return fallback
        k_dists = []
        for i in range(n):
            lo, hi = max(0, i - k), min(n, i + k + 1)
            neighbours = sorted(abs(sorted_values[i] - sorted_values[j]) for j in range(lo, hi) if j != i)
            if len(neighbours) >= k:
                k_dists.append(neighbours[k - 1])
        if not k_dists:
            return fallback
        k_dists.sort()
        y = np.array(k_dists, dtype=float)
        x = np.arange(len(y), dtype=float)
        x_range = x.max() - x.min() if x.max() > x.min() else 1.0
        y_range = y.max() - y.min() if y.max() > y.min() else 1.0
        x_norm = (x - x.min()) / x_range
        y_norm = (y - y.min()) / y_range
        diff = y_norm - x_norm
        knee_idx = int(np.argmax(diff))
        return max(k_dists[knee_idx], fallback * 0.25)

    @staticmethod
    def _sequential_cluster_1d(values_with_ref, eps):
        if not values_with_ref:
            return []
        clusters = [[values_with_ref[0][1]]]
        cluster_vals = [[values_with_ref[0][0]]]
        for val, ref in values_with_ref[1:]:
            if val - cluster_vals[-1][-1] <= eps:
                clusters[-1].append(ref)
                cluster_vals[-1].append(val)
            else:
                clusters.append([ref])
                cluster_vals.append([val])
        return clusters

    def _cluster_columns(self, items):
        sorted_items = sorted(items, key=lambda it: it.x0)
        xs = [it.x0 for it in sorted_items]
        eps_x = self._auto_eps(xs, k=2, fallback=self.px(0.015))

        raw_clusters = self._sequential_cluster_1d(list(zip(xs, sorted_items)), eps_x)

        median_item_width = statistics.median([it.width for it in items]) if items else self.px(0.01)

        column_clusters = []
        for cluster_items in raw_clusters:
            widths = [it.width for it in cluster_items]
            median_width = statistics.median(widths)
            is_narrow = median_width < median_item_width * 0.5

            xs_i = [it.x0 for it in cluster_items]
            column_clusters.append({
                "items": cluster_items,
                "left": statistics.mean(xs_i),
                "median_width": median_width,
                "is_narrow": is_narrow,
            })

        column_clusters.sort(key=lambda c: c["left"])

        column_clusters = self._merge_column_fragments(column_clusters)

        required_filtered = []
        for cluster in column_clusters:
            required_support = (
                self.min_narrow_col_support if cluster["is_narrow"] else self.min_col_support
            )
            if len(cluster["items"]) < required_support:
                continue
            required_filtered.append(cluster)

        return required_filtered

    def _merge_column_fragments(self, column_clusters):
        if len(column_clusters) < 2:
            return column_clusters

        merged = [column_clusters[0]]
        for cluster in column_clusters[1:]:
            prev = merged[-1]
            if self._are_column_fragments(prev, cluster):
                merged[-1] = self._combine_column_clusters(prev, cluster)
            else:
                merged.append(cluster)
        return merged

    def _are_column_fragments(self, cluster_a, cluster_b):
        a_right = max(it.x1 for it in cluster_a["items"])
        b_left = min(it.x0 for it in cluster_b["items"])
        if (b_left - a_right) > self.px(0.035):
            return False

        smaller, larger = (
            (cluster_a["items"], cluster_b["items"])
            if len(cluster_a["items"]) <= len(cluster_b["items"])
            else (cluster_b["items"], cluster_a["items"])
        )
        co_occurring = 0
        for it_s in smaller:
            for it_l in larger:
                if min(it_s.y1, it_l.y1) > max(it_s.y0, it_l.y0):
                    co_occurring += 1
                    break

        co_occur_ratio = co_occurring / len(smaller) if smaller else 0.0
        return co_occur_ratio <= 0.15

    @staticmethod
    def _combine_column_clusters(cluster_a, cluster_b):
        items = cluster_a["items"] + cluster_b["items"]
        xs = [it.x0 for it in items]
        widths = [it.width for it in items]
        return {
            "items": items,
            "left": statistics.mean(xs),
            "median_width": statistics.median(widths),
            "is_narrow": cluster_a["is_narrow"] and cluster_b["is_narrow"],
        }

    def _dynamic_max_col_width_ratio(self, n_columns_detected):
        if n_columns_detected <= self.min_cols:
            reserved_ratio = 0.10
        else:
            reserved_ratio = 0.10 * (n_columns_detected - 1)
        dynamic_cap = 1.0 - min(reserved_ratio, 0.55)
        return max(self.max_col_width_ratio, dynamic_cap)

    def _flag_table_items(self, items, column_clusters):
        if not items or len(column_clusters) < self.min_cols:
            return [], {}

        x_min = min(it.x0 for it in items)
        x_max = max(it.x1 for it in items)
        total_span = max(x_max - x_min, 1.0)

        dynamic_ratio = self._dynamic_max_col_width_ratio(len(column_clusters))

        valid_clusters = [
            c for c in column_clusters
            if (c["median_width"] / total_span) <= dynamic_ratio
        ]

        if len(valid_clusters) < self.min_cols:
            return [], {}

        item_col_id = {}
        table_items = []
        assigned_ids = set()
        for col_id, cluster in enumerate(valid_clusters):
            for it in cluster["items"]:
                item_col_id[id(it)] = col_id
                table_items.append(it)
                assigned_ids.add(id(it))

        self._recover_unassigned_items(items, table_items, item_col_id, valid_clusters, assigned_ids)

        return table_items, item_col_id

    def _recover_unassigned_items(self, items, table_items, item_col_id, valid_clusters, assigned_ids):
        if not table_items:
            return

        tracker = ColumnTracker(
            [(statistics.mean(it.x0 for it in c["items"]), statistics.mean(it.x1 for it in c["items"]))
             for c in valid_clusters]
        )
        row_pad = self._estimate_line_height(table_items)

        changed = True
        while changed:
            changed = False
            y_top = max(it.y1 for it in table_items) + row_pad
            y_bottom = min(it.y0 for it in table_items) - row_pad
            for it in items:
                if id(it) in assigned_ids:
                    continue
                if it.y0 < y_bottom or it.y1 > y_top:
                    continue

                best_col, _ = tracker.match(it, tol=self.px(0.01), max_overrun_ratio=1.5)
                if best_col is not None:
                    item_col_id[id(it)] = best_col
                    table_items.append(it)
                    assigned_ids.add(id(it))
                    tracker.update(best_col, it)
                    changed = True

    def _cluster_rows(self, table_items, line_height):
        fallback_eps_row = max(self.py(0.003), line_height * 0.6)

        sorted_items = sorted(table_items, key=lambda it: it.y1)
        ys = [it.y1 for it in sorted_items]

        eps_row = self._auto_eps(ys, k=2, fallback=fallback_eps_row)
        eps_row = max(eps_row, self.py(0.0015))
        eps_row = min(eps_row, line_height * 2.5)

        raw_row_clusters = self._sequential_cluster_1d(list(zip(ys, sorted_items)), eps_row)

        raw_row_clusters.sort(key=lambda cluster: -statistics.mean(it.y1 for it in cluster))
        return raw_row_clusters

    def _segment_rows_into_regions(self, row_groups, line_height):
        if not row_groups:
            return []

        gaps = []
        for i in range(len(row_groups) - 1):
            cur_bottom = min(it.y0 for it in row_groups[i])
            nxt_top = max(it.y1 for it in row_groups[i + 1])
            gaps.append(cur_bottom - nxt_top)

        baseline = statistics.median([g for g in gaps if g > -line_height]) if gaps else line_height
        region_gap_threshold = max(
            self.py(0.015),
            line_height * 1.8,
            baseline * 2.2 if baseline > 0 else line_height * 1.8
        )

        regions = []
        current = [row_groups[0]]
        for i in range(1, len(row_groups)):
            cur_bottom = min(it.y0 for it in row_groups[i - 1])
            nxt_top = max(it.y1 for it in row_groups[i])
            gap = cur_bottom - nxt_top
            if gap > region_gap_threshold:
                regions.append(current)
                current = []
            current.append(row_groups[i])
        if current:
            regions.append(current)

        return regions

    @staticmethod
    def _columns_row_aligned(items_a, items_b, tolerance, min_ratio=0.7):
        if len(items_a) != len(items_b):
            return False
        a_sorted = sorted(items_a, key=lambda it: -it.y1)
        b_sorted = sorted(items_b, key=lambda it: -it.y1)
        aligned = 0
        for ia, ib in zip(a_sorted, b_sorted):
            center_a = (ia.y0 + ia.y1) / 2.0
            center_b = (ib.y0 + ib.y1) / 2.0
            if abs(center_a - center_b) <= tolerance:
                aligned += 1
        return (aligned / len(a_sorted)) >= min_ratio

    def _select_spine_column(self, items_by_col, line_height):
        eligible = []
        for col_id, col_items in items_by_col.items():
            if len(col_items) < self.min_rows:
                continue
            sorted_items = sorted(col_items, key=lambda it: -it.y1)
            overlaps = sum(
                1 for a, b in zip(sorted_items, sorted_items[1:]) if a.y0 < b.y1
            )
            pairs = max(len(sorted_items) - 1, 1)
            clean_ratio = 1.0 - (overlaps / pairs)
            if clean_ratio >= 0.7:
                median_width = statistics.median(it.width for it in col_items)
                eligible.append((col_id, len(col_items), clean_ratio, median_width))

        if not eligible:
            return None

        tolerance = max(line_height * 0.6, self.py(0.006))
        counts = {}
        for _, n, _, _ in eligible:
            counts[n] = counts.get(n, 0) + 1

        corroborated = set()
        for n, votes in counts.items():
            if votes < 2:
                continue
            members = [t for t in eligible if t[1] == n]
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    if self._columns_row_aligned(
                        items_by_col[members[i][0]], items_by_col[members[j][0]], tolerance
                    ):
                        corroborated.add(members[i][0])
                        corroborated.add(members[j][0])

        if corroborated:
            candidates = [t for t in eligible if t[0] in corroborated]
            candidates.sort(key=lambda t: (t[3], -t[2]))
            return candidates[0][0]

        best_col, best_score = None, -1.0
        for col_id, n, clean_ratio, _ in eligible:
            score = clean_ratio * n
            if score > best_score:
                best_col, best_score = col_id, score
        return best_col

    def _form_regions(self, table_items, item_col_id, line_height):
        items_by_col = {}
        for it in table_items:
            items_by_col.setdefault(item_col_id[id(it)], []).append(it)

        spine_col = self._select_spine_column(items_by_col, line_height)
        if spine_col is None:
            row_groups = self._cluster_rows(table_items, line_height)
            return self._segment_rows_into_regions(row_groups, line_height)

        spine_items = sorted(items_by_col[spine_col], key=lambda it: -it.y1)
        other_items = [it for it in table_items if item_col_id[id(it)] != spine_col]

        gaps = [a.y0 - b.y1 for a, b in zip(spine_items, spine_items[1:])]
        baseline = statistics.median([g for g in gaps if g > -line_height]) if gaps else line_height
        region_gap_threshold = max(
            self.py(0.015),
            line_height * 1.8,
            baseline * 2.2 if baseline > 0 else line_height * 1.8
        )

        table_bottom = min(it.y0 for it in table_items) - line_height
        bands = []
        for i, sp in enumerate(spine_items):
            bottom = spine_items[i + 1].y1 if i + 1 < len(spine_items) else table_bottom
            bands.append({
                "top": sp.y1,
                "bottom": bottom,
                "items": [sp],
                "starts_new_region": bool(i > 0 and gaps[i - 1] > region_gap_threshold),
            })

        min_overlap = -max(line_height * 0.5, self.py(0.003))
        for it in other_items:
            best_i, best_overlap = None, -1.0
            for i, band in enumerate(bands):
                overlap = min(band["top"], it.y1) - max(band["bottom"], it.y0)
                if overlap > best_overlap:
                    best_overlap, best_i = overlap, i
            if best_i is not None and best_overlap >= min_overlap:
                bands[best_i]["items"].append(it)

        regions = []
        current = []
        for band in bands:
            if band["starts_new_region"] and current:
                regions.append(current)
                current = []
            current.append(sorted(band["items"], key=lambda x: x.x0))
        if current:
            regions.append(current)

        return regions

    @staticmethod
    def _compute_region_merge_features(region_a, region_b, item_col_id, line_height):
        items_a = [it for row in region_a for it in row]
        items_b = [it for row in region_b for it in row]
        if not items_a or not items_b:
            return None

        cols_a = set(item_col_id[id(it)] for it in items_a)
        cols_b = set(item_col_id[id(it)] for it in items_b)
        union_cols = cols_a | cols_b
        col_overlap_ratio = len(cols_a & cols_b) / len(union_cols) if union_cols else 0.0
        max_col_count = max(len(cols_a), len(cols_b), 1)
        col_count_diff = abs(len(cols_a) - len(cols_b)) / max_col_count

        a_bottom = min(it.y0 for it in items_a)
        b_top = max(it.y1 for it in items_b)
        gap = a_bottom - b_top
        gap_norm = gap / line_height if line_height else gap

        a_x0, a_x1 = min(it.x0 for it in items_a), max(it.x1 for it in items_a)
        b_x0, b_x1 = min(it.x0 for it in items_b), max(it.x1 for it in items_b)
        overlap = max(0.0, min(a_x1, b_x1) - max(a_x0, b_x0))
        union_span = max(a_x1, b_x1) - min(a_x0, b_x0)
        xspan_overlap = overlap / union_span if union_span > 0 else 0.0

        return {
            "col_overlap_ratio": col_overlap_ratio,
            "gap_norm": max(-5.0, min(5.0, gap_norm)),
            "xspan_overlap": xspan_overlap,
            "col_count_diff": col_count_diff,
        }

    def _merge_related_regions(self, regions, item_col_id, line_height):
        if len(regions) < 2:
            return regions

        merged_regions = [regions[0]]
        for region in regions[1:]:
            prev_region = merged_regions[-1]
            features = self._compute_region_merge_features(prev_region, region, item_col_id, line_height)
            prob = self.region_merge_classifier.predict_proba(features) if features else 0.0
            should_merge = features is not None and prob >= self.region_merge_probability_threshold

            if should_merge:
                target_index = len(merged_regions) - 1
                merged_regions[-1] = prev_region + region
                self.logger.debug(f"Merged two borderless regions into one table (p={round(prob, 3)})")
            else:
                target_index = len(merged_regions)
                merged_regions.append(region)

            if features is not None:
                self.region_merge_events.append({
                    "features": features, "action": should_merge, "region_index": target_index
                })

        return merged_regions

    @staticmethod
    def _has_spine_column(col_row_counts, n_rows, ratio=0.8):
        return n_rows > 0 and any(cnt >= n_rows * ratio for cnt in col_row_counts.values())

    def _prune_weak_columns(self, region_rows, item_col_id):
        n_rows = len(region_rows)
        if n_rows == 0:
            return region_rows

        col_row_counts = {}
        col_centers = {}
        for row_items in region_rows:
            cols_in_row = set(item_col_id[id(it)] for it in row_items)
            for c in cols_in_row:
                col_row_counts[c] = col_row_counts.get(c, 0) + 1
            for it in row_items:
                c = item_col_id[id(it)]
                col_centers.setdefault(c, []).append((it.x0 + it.x1) / 2.0)

        min_row_floor = max(1, int(round(self.min_rows * 0.5)))

        if self._has_spine_column(col_row_counts, n_rows):
            min_support = min_row_floor
        else:
            min_support = max(min_row_floor, int(round(n_rows * self.min_col_row_support_ratio)))
        keep_cols = {c for c, cnt in col_row_counts.items() if cnt >= min_support}

        if len(keep_cols) < self.min_cols:
            return []

        keep_centers = {c: statistics.mean(col_centers[c]) for c in keep_cols}

        pruned_rows = []
        for row_items in region_rows:
            for it in row_items:
                c = item_col_id[id(it)]
                if c not in keep_cols:
                    center = (it.x0 + it.x1) / 2.0
                    item_col_id[id(it)] = min(keep_cols, key=lambda kc: abs(keep_centers[kc] - center))
            if row_items:
                pruned_rows.append(row_items)

        return pruned_rows

    def _passes_multi_col_row_check(self, region_rows, item_col_id):
        if not region_rows:
            return False

        multi_col_rows = 0
        col_row_counts = {}
        for row_items in region_rows:
            cols_in_row = set(item_col_id[id(it)] for it in row_items)
            if len(cols_in_row) >= 2:
                multi_col_rows += 1
            for c in cols_in_row:
                col_row_counts[c] = col_row_counts.get(c, 0) + 1

        ratio = multi_col_rows / len(region_rows)
        if ratio >= self.min_multi_col_row_ratio:
            return True

        has_spine = self._has_spine_column(col_row_counts, len(region_rows))
        return has_spine and multi_col_rows >= self.min_narrow_col_support

    @staticmethod
    def _trim_non_tabular_edge_rows(region_rows, item_col_id):
        if not region_rows:
            return region_rows

        all_items = [it for row in region_rows for it in row]
        table_x0 = min(it.x0 for it in all_items)
        table_x1 = max(it.x1 for it in all_items)
        table_width = max(table_x1 - table_x0, 1.0)

        def is_stray_row(row_items):
            cols = set(item_col_id[id(it)] for it in row_items)
            if len(cols) != 1:
                return False
            rx0 = min(it.x0 for it in row_items)
            rx1 = max(it.x1 for it in row_items)
            return (rx1 - rx0) / table_width >= 0.75

        trimmed = list(region_rows)
        max_strip = 2
        stripped = 0
        while trimmed and stripped < max_strip and is_stray_row(trimmed[0]):
            trimmed.pop(0)
            stripped += 1
        stripped = 0
        while trimmed and stripped < max_strip and is_stray_row(trimmed[-1]):
            trimmed.pop()
            stripped += 1
        return trimmed

    @staticmethod
    def _compute_header_features(region_rows, item_col_id, n_cols):
        if len(region_rows) < 2:
            return None

        header_items = region_rows[0]
        body_rows = region_rows[1:]
        if not header_items or not body_rows or n_cols == 0:
            return None

        header_cols = set(item_col_id[id(it)] for it in header_items)
        header_fill = len(header_cols) / n_cols
        header_avg_len = statistics.mean(len(it.text) for it in header_items)
        header_avg_lines = statistics.mean(it.n_textlines for it in header_items)

        body_fills, body_lens, body_lines = [], [], []
        for row_items in body_rows:
            cols = set(item_col_id[id(it)] for it in row_items)
            body_fills.append(len(cols) / n_cols)
            body_lens.extend(len(it.text) for it in row_items)
            body_lines.extend(it.n_textlines for it in row_items)

        if not body_fills or not body_lens or not body_lines:
            return None

        body_fill_median = statistics.median(body_fills)
        body_len_mean = statistics.mean(body_lens)
        body_lines_mean = statistics.mean(body_lines)

        fill_diff = header_fill - body_fill_median
        len_ratio = 1.0 - (header_avg_len / body_len_mean if body_len_mean else 1.0)
        lines_diff = body_lines_mean - header_avg_lines

        header_bottom = min(it.y0 for it in header_items)
        first_body_top = max(it.y1 for it in body_rows[0])
        header_to_body_gap = header_bottom - first_body_top

        body_gaps = []
        for i in range(len(body_rows) - 1):
            cur_bottom = min(it.y0 for it in body_rows[i])
            nxt_top = max(it.y1 for it in body_rows[i + 1])
            body_gaps.append(cur_bottom - nxt_top)

        if body_gaps:
            median_body_gap = statistics.median(body_gaps)
            gap_ratio = (header_to_body_gap - median_body_gap) / (abs(median_body_gap) + 1e-6)
        else:
            gap_ratio = 0.0
        gap_ratio = max(-5.0, min(5.0, gap_ratio))

        return {
            "fill_diff": fill_diff,
            "len_ratio": len_ratio,
            "lines_diff": lines_diff,
            "gap_ratio": gap_ratio,
        }

    def _detect_header_row(self, region_rows, item_col_id, n_cols, region_order_id):
        if len(region_rows) <= self.min_rows:
            return False, 0.0, None

        features = self._compute_header_features(region_rows, item_col_id, n_cols)
        if features is None:
            return False, 0.0, None

        prob = self.header_classifier.predict_proba(features)
        action = prob >= self.header_probability_threshold
        self.header_events.append({
            "features": features, "action": action, "region_index": region_order_id
        })
        return action, prob, features

    def _apply_region_reward(self, region_index, reward):
        if not self.rl_enabled:
            return
        for event in self.header_events:
            if event["region_index"] == region_index:
                self.header_classifier.reinforce(
                    event["features"], event["action"], reward, lr=self.rl_lr, l2=self.rl_l2
                )
        for event in self.region_merge_events:
            if event["region_index"] == region_index:
                self.region_merge_classifier.reinforce(
                    event["features"], event["action"], reward, lr=self.rl_lr, l2=self.rl_l2
                )

    def _build_table(self, region_rows, item_col_id, region_order_id):
        all_items = [it for row in region_rows for it in row]
        col_ids_present = sorted(set(item_col_id[id(it)] for it in all_items))
        col_index_map = {col_id: i for i, col_id in enumerate(col_ids_present)}
        n_cols = len(col_index_map)
        if n_cols == 0:
            return None, None, 0.0, 0, False, 0.0, None, None

        has_header, header_prob, header_features = self._detect_header_row(
            region_rows, item_col_id, n_cols, region_order_id
        )

        def build_row_cells(row_items):
            cells = [""] * n_cols
            occupied = [False] * n_cols
            for it in sorted(row_items, key=lambda x: x.x0):
                col_id = item_col_id[id(it)]
                c_idx = col_index_map[col_id]
                if occupied[c_idx]:
                    cells[c_idx] = (cells[c_idx] + " " + it.text).strip()
                else:
                    cells[c_idx] = it.text
                    occupied[c_idx] = True
            return cells

        rows_out = []
        filled = 0
        total_cells = 0
        for row_items in region_rows:
            cells = build_row_cells(row_items)
            rows_out.append(cells)
            filled += sum(1 for c in cells if c)
            total_cells += n_cols

        if not rows_out:
            return None, None, 0.0, 0, False, 0.0, None, None

        df = pd.DataFrame(rows_out)
        fill_ratio = filled / total_cells if total_cells else 0.0

        x0 = min(it.x0 for it in all_items)
        x1 = max(it.x1 for it in all_items)
        y0 = min(it.y0 for it in all_items)
        y1 = max(it.y1 for it in all_items)

        col_ranges = self._column_x_ranges(all_items, item_col_id, col_ids_present)

        return df, (x0, y0, x1, y1), fill_ratio, n_cols, has_header, header_prob, header_features, col_ranges

    @staticmethod
    def _column_x_ranges(all_items, item_col_id, col_ids_present):
        ranges = []
        for col_id in col_ids_present:
            col_items = [it for it in all_items if item_col_id[id(it)] == col_id]
            if not col_items:
                continue
            ranges.append((min(it.x0 for it in col_items), max(it.x1 for it in col_items)))
        return ranges

    def _merge_column_ranges(self, cols):
        if not cols:
            return []
        tol = self.px(0.01)
        ordered = sorted((tuple(c) for c in cols), key=lambda c: c[0])
        merged = [list(ordered[0])]
        for c0, c1 in ordered[1:]:
            if c0 <= merged[-1][1] + tol:
                merged[-1][1] = max(merged[-1][1], c1)
            else:
                merged.append([c0, c1])
        return [tuple(c) for c in merged]

    def _compute_continuation_out(self, table, bbox):
        result = self._continuation_out_from_tables(table, bbox)
        if result is not None:
            return result
    
        if self.continuation_passthrough:
            return {**self.continuation_passthrough, "reason": "passthrough"}
        return None

    def _continuation_out_from_tables(self, table, bbox):
        if not bbox:
            return None

        page_area = self.page_width * self.page_height
        if page_area <= 0:
            return None

        total_area = sum(
            max(0.0, (x1 - x0)) * max(0.0, (y1 - y0))
            for (x0, y0, x1, y1) in bbox.values()
        )
        coverage = total_area / page_area

        cont_ids = [i for i in bbox if self.table_is_continuation.get(i)]
        pool = cont_ids if cont_ids else list(bbox)
        bottom_idx = min(pool, key=lambda i: bbox[i][1])
        b_y0 = bbox[bottom_idx][1]

        at_page_end = b_y0 <= self.page_height * self.continuation_bottom_margin_ratio
        big_table = coverage >= self.continuation_page_coverage

        has_ruler = self._table_has_column_ruler(table.get(bottom_idx))

        if not (at_page_end or big_table or has_ruler):
            return None

        cols = list(self.table_columns.get(bottom_idx) or [])
        if len(cols) < self.min_cols:
            return None

        inherited = self.continuation_template
        if inherited and self.table_is_continuation.get(bottom_idx):
            inherited_abs = [
                (c0 * self.page_width, c1 * self.page_width)
                for c0, c1 in inherited.get("columns_norm", [])
            ]
            cols = self._merge_column_ranges(cols + inherited_abs)

        columns_norm = [(x0 / self.page_width, x1 / self.page_width) for (x0, x1) in cols]
        n_cols_out = len(cols)

        reason = "coverage" if big_table else ("page_end" if at_page_end else "ruler")
        return {
            "columns_norm": columns_norm,
            "n_cols": n_cols_out,
            "n_rows_hint": self.table_nrows.get(bottom_idx, self.min_rows),
            "had_header": self.table_headers.get(bottom_idx, False),
            "reason": reason,
        }

    _RULER_CELL_RE = re.compile(r'^\(?\s*(?:[0-9]{1,2}|[ivxIVX]{1,4})\s*\)?[.)]?$')

    def _table_has_column_ruler(self, df):
        if df is None or getattr(df, "empty", True):
            return False
        for row in df.values.tolist():
            cells = [str(c).strip() for c in row if str(c).strip()]
            if len(cells) < self.min_cols:
                continue
            matches = sum(1 for c in cells if self._RULER_CELL_RE.match(c))
            if matches >= max(self.min_cols, len(cells) - 1):
                return True
        return False

    def _detect_continuation_table(self, items, table, bbox, start_idx, claimed_ids=None):
        template = self.continuation_template
        if not template:
            return start_idx

        tmpl_cols = [
            (c0 * self.page_width, c1 * self.page_width)
            for (c0, c1) in template.get("columns_norm", [])
        ]
        n_cols_t = len(tmpl_cols)
        if n_cols_t < self.min_cols:
            return start_idx
        tmpl_x0 = min(c0 for c0, _ in tmpl_cols)
        tmpl_x1 = max(c1 for _, c1 in tmpl_cols)

        free_items = list(items)
        if len(free_items) < self.min_continuation_rows:
            return start_idx

        line_height = self._estimate_line_height(free_items)
        col_tol = self.px(0.02)
        span_tol = self.px(0.03)

        tracker = ColumnTracker(tmpl_cols)

        def in_band(it):
            center = (it.x0 + it.x1) / 2.0
            return tmpl_x0 - span_tol <= center <= tmpl_x1 + span_tol

        row_groups = self._rows_by_top(free_items, max(line_height * 0.7, self.py(0.004)))

        consumed_rows = []
        strict_cols = set()
        align_errors = []
        orphans = []
        prev_bottom = None
        consec_miss = 0
        huge_gap = line_height * 12.0
        for row in row_groups:
            row_top = max(it.y1 for it in row)
            if prev_bottom is not None and (prev_bottom - row_top) > huge_gap:
                break
            inb = [it for it in row if in_band(it)]
            if inb and len(inb) >= max(1, 0.5 * len(row)):
                consumed_rows.append(row)
                for it in row:
                    ci, ov = tracker.match(it, tol=col_tol, max_overrun_ratio=1.6)
                    if ci is not None and ci < n_cols_t:
                        strict_cols.add(ci)
                        align_errors.append(ov)
                        tracker.update(ci, it)
                    elif in_band(it):
                        orphans.append(it)
                consec_miss = 0
            else:
                consec_miss += 1
                if consec_miss >= self.continuation_max_skip_rows:
                    break
            prev_bottom = min(it.y0 for it in row)

        if len(consumed_rows) < self.min_continuation_rows:
            return start_idx

        all_block_items = [it for row in consumed_rows for it in row]
        top_of_block = max(it.y1 for it in all_block_items)
        if top_of_block < self.page_height * (1.0 - self.continuation_top_band_ratio):
            return start_idx

        block_bottom = min(it.y0 for it in all_block_items)
        if (len(consumed_rows) >= self.min_rows
                and block_bottom <= self.page_height * (self.continuation_bottom_margin_ratio + 0.03)):
            self.continuation_passthrough = dict(template)

        self._promote_orphan_columns(orphans, tracker, col_tol)

        item_col_id = {}
        for row in consumed_rows:
            for it in row:
                ci, _ = tracker.match(it, tol=col_tol, max_overrun_ratio=1.6)
                if ci is None:
                    ci = min(range(len(tracker.cols)),
                             key=lambda k: abs((it.x0 + it.x1) / 2.0 - (tracker.cols[k]["x0"] + tracker.cols[k]["x1"]) / 2.0))
                item_col_id[id(it)] = ci
        region_rows = consumed_rows

        df, region_bbox, fill_ratio, n_cols, col_ranges = self._build_simple_table(
            region_rows, item_col_id, tracker=tracker
        )
        if df is None:
            return start_idx

        features = self._compute_continuation_features(
            region_rows, strict_cols, n_cols_t, tmpl_cols, align_errors, template, line_height
        )
        prob = self.continuation_classifier.predict_proba(features)
        accepted = (
            prob >= self.continuation_probability_threshold
            and n_cols >= self.min_cols
            and fill_ratio >= self.continuation_min_fill_ratio
        )
        reward = fill_ratio if accepted else (fill_ratio - self.continuation_min_fill_ratio)
        if self.rl_enabled:
            self.continuation_classifier.reinforce(
                features, accepted, reward, lr=self.rl_lr, l2=self.rl_l2
            )

        if accepted:
            table[start_idx] = df
            bbox[start_idx] = region_bbox
            self.table_headers[start_idx] = False
            self.table_header_scores[start_idx] = 0.0
            self.table_columns[start_idx] = col_ranges
            self.table_y[start_idx] = (region_bbox[1], region_bbox[3])
            self.table_nrows[start_idx] = df.shape[0]
            self.table_is_continuation[start_idx] = True
            self.table_item_objs[start_idx] = {
                id(it.tb_obj) for row in region_rows for it in row if it.tb_obj is not None
            }
            self.logger.debug(
                f"Accepted borderless continuation table idx={start_idx} "
                f"rows={df.shape[0]} cols={n_cols} fill_ratio={round(fill_ratio, 2)} "
                f"p={round(prob, 3)} bbox={region_bbox}"
            )
            if claimed_ids is not None:
                claimed_ids.update(id(it) for row in region_rows for it in row)
            start_idx += 1

        return start_idx

    def _suppress_normal_tables_within(self, cont_idx, table, bbox):
        cx0, cy0, cx1, cy1 = bbox[cont_idx]
        for idx in list(bbox.keys()):
            if idx == cont_idx or self.table_is_continuation.get(idx):
                continue
            bx0, by0, bx1, by1 = bbox[idx]
            ox = max(0.0, min(cx1, bx1) - max(cx0, bx0))
            oy = max(0.0, min(cy1, by1) - max(cy0, by0))
            area = max(1.0, (bx1 - bx0) * (by1 - by0))
            if (ox * oy) / area >= 0.6:
                for store in (table, bbox, self.table_headers, self.table_header_scores,
                              self.table_columns, self.table_y, self.table_nrows,
                              self.table_is_continuation, self.table_header_features,
                              self.table_item_objs):
                    store.pop(idx, None)
                self.logger.debug(
                    f"Suppressed normal table idx={idx} overlapped by continuation idx={cont_idx}"
                )

    def _suppress_normal_tables_in_span(self, table, bbox):
        template = self.continuation_passthrough or self.continuation_template
        cols = template.get("columns_norm", []) if template else []
        if len(cols) < self.min_cols:
            return
        tx0 = min(c0 for c0, _ in cols) * self.page_width - self.px(0.03)
        tx1 = max(c1 for _, c1 in cols) * self.page_width + self.px(0.03)
        for idx in list(bbox.keys()):
            if self.table_is_continuation.get(idx):
                continue
            bx0, _, bx1, _ = bbox[idx]
            if bx0 >= tx0 and bx1 <= tx1:
                for store in (table, bbox, self.table_headers, self.table_header_scores,
                              self.table_columns, self.table_y, self.table_nrows,
                              self.table_is_continuation, self.table_header_features,
                              self.table_item_objs):
                    store.pop(idx, None)
                self.logger.debug(
                    f"Suppressed normal table idx={idx} inside continuation template span"
                )

    def _promote_orphan_columns(self, orphans, tracker, col_tol):
        if not orphans:
            return
        existing = tracker.bounds()
        sorted_orphans = sorted(orphans, key=lambda it: (it.x0 + it.x1) / 2.0)
        eps = max(col_tol * 2, self.px(0.02))
        clusters = self._sequential_cluster_1d(
            [((it.x0 + it.x1) / 2.0, it) for it in sorted_orphans], eps
        )
        for cluster_items in clusters:
            if len(cluster_items) < self.min_narrow_col_support:
                continue
            x0 = min(it.x0 for it in cluster_items)
            x1 = max(it.x1 for it in cluster_items)
            if any(x0 <= ex1 and ex0 <= x1 for ex0, ex1 in existing):
                continue
            tracker.add_column(x0, x1)

    @staticmethod
    def _rows_by_top(items, eps):
        if not items:
            return []
        ordered = sorted(items, key=lambda it: -it.y1)
        rows = [[ordered[0]]]
        for it in ordered[1:]:
            if (rows[-1][-1].y1 - it.y1) <= eps:
                rows[-1].append(it)
            else:
                rows.append([it])
        return rows

    def _compute_continuation_features(self, region_rows, strict_cols, n_cols_t,
                                       tmpl_cols, align_errors, template, line_height):
        col_coverage = len(strict_cols) / n_cols_t if n_cols_t else 0.0

        cons_items = [it for row in region_rows for it in row]
        cx0 = min(it.x0 for it in cons_items)
        cx1 = max(it.x1 for it in cons_items)
        tx0 = min(c0 for c0, _ in tmpl_cols)
        tx1 = max(c1 for _, c1 in tmpl_cols)
        overlap = max(0.0, min(cx1, tx1) - max(cx0, tx0))
        union = max(cx1, tx1) - min(cx0, tx0)
        xspan_overlap = overlap / union if union > 0 else 0.0

        top_gap = self.page_height - max(it.y1 for it in cons_items)
        top_window = max(self.page_height * 0.25, line_height * 6.0)
        top_closeness = max(0.0, 1.0 - top_gap / top_window)

        n_rows_hint = max(template.get("n_rows_hint", self.min_rows), self.min_rows)
        row_support = min(1.0, len(region_rows) / max(n_rows_hint, 3))

        mean_overrun = statistics.mean(align_errors) if align_errors else 1.0
        align_quality = max(0.0, 1.0 - min(1.0, mean_overrun))

        return {
            "col_coverage": col_coverage,
            "xspan_overlap": xspan_overlap,
            "top_closeness": top_closeness,
            "row_support": row_support,
            "align_quality": align_quality,
        }

    def _build_simple_table(self, region_rows, item_col_id, tracker=None):
        all_items = [it for row in region_rows for it in row]
        if not all_items:
            return None, None, 0.0, 0, None
        col_ids_present = sorted(set(item_col_id[id(it)] for it in all_items))
        col_index_map = {col_id: i for i, col_id in enumerate(col_ids_present)}
        n_cols = len(col_index_map)
        if n_cols == 0:
            return None, None, 0.0, 0, None

        rows_out = []
        filled = 0
        total_cells = 0
        for row_items in region_rows:
            cells = [""] * n_cols
            occupied = [False] * n_cols
            for it in sorted(row_items, key=lambda x: x.x0):
                c_idx = col_index_map[item_col_id[id(it)]]
                if occupied[c_idx]:
                    cells[c_idx] = (cells[c_idx] + " " + it.text).strip()
                else:
                    cells[c_idx] = it.text
                    occupied[c_idx] = True
            rows_out.append(cells)
            filled += sum(1 for c in cells if c)
            total_cells += n_cols

        if not rows_out:
            return None, None, 0.0, 0, None

        df = pd.DataFrame(rows_out)
        fill_ratio = filled / total_cells if total_cells else 0.0
        x0 = min(it.x0 for it in all_items)
        x1 = max(it.x1 for it in all_items)
        y0 = min(it.y0 for it in all_items)
        y1 = max(it.y1 for it in all_items)
        if tracker is not None:
            tb = tracker.bounds()
            col_ranges = [tb[c] if c < len(tb) else None for c in col_ids_present]
            col_ranges = [r for r in col_ranges if r is not None]
        else:
            col_ranges = self._column_x_ranges(all_items, item_col_id, col_ids_present)
        return df, (x0, y0, x1, y1), fill_ratio, n_cols, col_ranges

    def correct_header_row(self, idx, actual_is_header, lr=0.15, l2=0.02):
        features = self.table_header_features.get(idx)
        if features is None:
            return False
        label = 1.0 if actual_is_header else 0.0
        self.header_classifier.update_supervised(features, label, lr=lr, l2=l2)
        return True

    def correct_region_merge(self, event_index, actual_should_merge, lr=0.15, l2=0.02):
        if event_index < 0 or event_index >= len(self.region_merge_events):
            return False
        features = self.region_merge_events[event_index]["features"]
        label = 1.0 if actual_should_merge else 0.0
        self.region_merge_classifier.update_supervised(features, label, lr=lr, l2=l2)
        return True

    def get_table_width(self, idx):
        if idx not in self.table_bbox:
            return None
        x1, y1, x2, y2 = self.table_bbox[idx]
        return abs(x2 - x1)

    def get_table_height(self, idx):
        if idx not in self.table_bbox:
            return None
        x1, y1, x2, y2 = self.table_bbox[idx]
        return abs(y2 - y1)

    def get_table_has_header(self, idx):
        return self.table_headers.get(idx, False)

    def get_table_header_confidence(self, idx):
        return self.table_header_scores.get(idx, 0.0)
