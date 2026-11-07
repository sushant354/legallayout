import cv2
import numpy as np

SKEW_MAX_ANGLE = 5.0
SKEW_COARSE_STEP = 0.5
SKEW_FINE_STEP = 0.05
SKEW_WORKING_SIZE = 1000
SKEW_MIN_ANGLE = 0.25
SKEW_MIN_GAIN = 1.2


def line_sharpness(ink, angle):
    height, width = ink.shape
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    rotated = cv2.warpAffine(ink, matrix, (width, height), flags=cv2.INTER_NEAREST, borderValue=0)
    profile = rotated.sum(axis=1).astype(np.float64)
    return float(np.sum(np.diff(profile) ** 2))


def estimate_skew(gray):
    height, width = gray.shape
    factor = SKEW_WORKING_SIZE / max(height, width)
    small = cv2.resize(gray, (max(1, int(width * factor)), max(1, int(height * factor))),
                       interpolation=cv2.INTER_AREA)
    _, ink = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    upright = line_sharpness(ink, 0.0)
    if upright <= 0:
        return 0.0

    coarse = np.arange(-SKEW_MAX_ANGLE, SKEW_MAX_ANGLE + 1e-9, SKEW_COARSE_STEP)
    coarse_best = float(coarse[int(np.argmax([line_sharpness(ink, a) for a in coarse]))])

    fine = np.arange(coarse_best - SKEW_COARSE_STEP, coarse_best + SKEW_COARSE_STEP + 1e-9,
                     SKEW_FINE_STEP)
    fine_scores = [line_sharpness(ink, a) for a in fine]
    best = int(np.argmax(fine_scores))

    if abs(fine[best]) < SKEW_MIN_ANGLE or fine_scores[best] < upright * SKEW_MIN_GAIN:
        return 0.0

    return float(fine[best])


def rotate_image(image, angle):
    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    return cv2.warpAffine(image, matrix, (width, height), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))


def straighten_pixmap(pix, logger, page_number):
    try:
        image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        gray = image[:, :, 0] if pix.n == 1 else cv2.cvtColor(image[:, :, :3], cv2.COLOR_RGB2GRAY)
        angle = estimate_skew(gray)
        if not angle:
            return 0.0, None
        logger.debug(f"Page {page_number} is skewed by {angle:.2f} degrees, straightening it before OCR")
        return angle, rotate_image(image, angle)
    except Exception as e:
        logger.warning(f"Skew correction failed on page {page_number}: {e}")
        return 0.0, None
