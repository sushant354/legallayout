IMAGE_BODY_RATIO = 0.4
TEXT_INSIDE_RATIO = 0.5
EDGE_ZONE_RATIO = 0.15
BLOCK_GAP_HEIGHTS = 2.0
MIN_BODY_LINES = 3
RATIO_SPREAD_TOLERANCE = 0.1
STRIP_PAGE_SHARE = 0.95

OVERLAY = "overlay"
STRIPS = "strips"


def box_area(box):
    return max(box[2] - box[0], 0.0) * max(box[3] - box[1], 0.0)


def vertical_blocks(lines):
    heights = sorted(line[3] - line[1] for line in lines)
    gap_limit = BLOCK_GAP_HEIGHTS * heights[len(heights) // 2]
    ordered = sorted(lines, key=lambda line: line[1])
    blocks = [[ordered[0]]]
    reach = ordered[0][3]
    for line in ordered[1:]:
        if line[1] - reach > gap_limit:
            blocks.append([line])
            reach = line[3]
        else:
            blocks[-1].append(line)
            reach = max(reach, line[3])
    return blocks


def body_line_count(lines, image_box):
    zone = EDGE_ZONE_RATIO * (image_box[3] - image_box[1])
    count = 0
    for block in vertical_blocks(lines):
        low = min(line[1] for line in block)
        high = max(line[3] for line in block)
        if high <= image_box[1] + zone or low >= image_box[3] - zone:
            continue
        count += len(block)
    return count


def classify_image_page(page_box, image_box, lines):
    page_area = box_area(page_box)
    image_area = box_area(image_box)
    if page_area <= 0 or image_area / page_area < IMAGE_BODY_RATIO:
        return None
    weighted = [line for line in lines if line[4] > 0]
    total = sum(line[4] for line in weighted)
    if not total:
        return None
    inside = sum(
        line[4] for line in weighted
        if image_box[0] <= (line[0] + line[2]) / 2 <= image_box[2]
        and image_box[1] <= (line[1] + line[3]) / 2 <= image_box[3]
    )
    if inside / total < TEXT_INSIDE_RATIO:
        return STRIPS
    if body_line_count(weighted, image_box) < MIN_BODY_LINES:
        return STRIPS
    return OVERLAY


def image_text_page(page_box, image_boxes, lines):
    if not image_boxes:
        return None, 0.0
    image_box = max(image_boxes, key=box_area)
    page_area = box_area(page_box)
    ratio = box_area(image_box) / page_area if page_area > 0 else 0.0
    return classify_image_page(page_box, image_box, lines), ratio


def strips_share_is_uniform(page_results):
    if not page_results:
        return False
    strip_ratios = [ratio for kind, ratio in page_results if kind == STRIPS]
    if len(strip_ratios) / len(page_results) < STRIP_PAGE_SHARE:
        return False
    return max(strip_ratios) - min(strip_ratios) <= RATIO_SPREAD_TOLERANCE
