import os
import logging
import numpy as np
from .Utils import *
from PIL import Image
from typing import Tuple

from pdfminer.high_level import extract_pages
from pdfminer.layout import LTImage
from pdfminer.image import ImageWriter
from pdfminer import pdftypes, utils as pdfminer_utils
from pdfminer.pdfexceptions import PDFValueError


def apply_png_predictor(pred, colors, columns, bitspercomponent, data):
    """PNG predictor reversal, with the scanline stride rounded up.

    pdfminer floors it (colors * columns * bitspercomponent // 8), so a
    sub-byte image - a 1-bit image mask, which is how a scanned stamp or
    signature is usually stored - whose width is not a multiple of 8 drops the
    last, partial byte of every row. From the second scanline on it then reads
    a byte of pixel data where the row's filter type should be and the whole
    image is lost to "Unsupported predictor value: 255". The PNG spec's stride
    is ceil(width * colors * bpc / 8), which is the only thing this differs in;
    bpp rounds up the same way and never falls below 1, so Sub/Average/Paeth
    look a whole pixel back as they must.
    """
    nbytes = (colors * columns * bitspercomponent + 7) // 8
    bpp = max(1, (colors * bitspercomponent + 7) // 8)

    buf = bytearray()
    line_above = bytearray(nbytes)

    for i in range(0, len(data), nbytes + 1):
        filter_type = data[i]
        line = data[i + 1: i + 1 + nbytes]
        raw = bytearray()

        if filter_type == 0:
            raw += line

        elif filter_type == 1:
            for j, x in enumerate(line):
                left = raw[j - bpp] if j >= bpp else 0
                raw.append((x + left) & 0xFF)

        elif filter_type == 2:
            for j, x in enumerate(line):
                raw.append((x + line_above[j]) & 0xFF)

        elif filter_type == 3:
            for j, x in enumerate(line):
                left = raw[j - bpp] if j >= bpp else 0
                raw.append((x + (left + line_above[j]) // 2) & 0xFF)

        elif filter_type == 4:
            for j, x in enumerate(line):
                left = raw[j - bpp] if j >= bpp else 0
                upleft = line_above[j - bpp] if j >= bpp else 0
                paeth = pdfminer_utils.paeth_predictor(
                    left, line_above[j], upleft
                )
                raw.append((x + paeth) & 0xFF)

        else:
            raise PDFValueError(f"Unsupported predictor value: {filter_type}")

        buf += raw
        # a truncated final row must not leave line_above short of nbytes
        line_above = raw + bytearray(nbytes - len(raw))

    return bytes(buf)


def patch_png_predictor():
    """Install apply_png_predictor() over pdfminer's, if pdfminer needs it.

    pdftypes binds the name at import, so that is the copy the decoder
    actually calls. The probe is four rows of a 13-pixel-wide 1-bit image: a
    stride that floors reads them as 1 byte a row instead of 2 and either
    raises or returns the wrong number of bytes, so a pdfminer that ever fixes
    this keeps its own - very likely faster - implementation.
    """
    probe = b"\x00\xaa\xf8" * 4

    try:
        decoded = pdftypes.apply_png_predictor(15, 1, 13, 1, probe)
        if len(decoded) == 8:
            return False
    except Exception:
        pass

    pdftypes.apply_png_predictor = apply_png_predictor
    pdfminer_utils.apply_png_predictor = apply_png_predictor

    logging.getLogger(__name__).debug(
        "patched pdfminer's apply_png_predictor (floored scanline stride)"
    )

    return True


patch_png_predictor()

class StableImageWriter(ImageWriter):
    def _create_unique_image_name(self, image, ext):
        name = image.name + ext
        path = os.path.join(self.outdir, name)
        return name, path


class Figure:
    def __init__(self, fig):
        self.logger = logging.getLogger(__name__)

        # Must match LTImage.name
        self.figname = fig.attrib["name"]

        self.coords = tuple(
            map(float, fig.attrib["bbox"].split(","))
        )

        self.height = self.coords[3] - self.coords[1]
        self.width = self.coords[2] - self.coords[0]

        self.has_fig = self.has_figure(fig)

    def has_figure(self, fig):
        return fig.find("image") is not None

class Pictures:
    
    def __init__(
        self,
        pdf_path,
        pg_num,
        base_name_of_file,
        output_dir,
        unique_images,
        min_img_pixels,
        ocr_language,
        scanned_copy,
        figure_text=False,
        image_base_dir="manifest",
        pdf_type=None,
        ocr_engine="tesseract"
    ):
        self.logger = logging.getLogger(__name__)

        self.pg_num = pg_num
        self.ocr_language = ocr_language
        self.ocr_engine = ocr_engine
        self.unique_images = unique_images
        self.figure_text = figure_text
        self.pdf_type = pdf_type

        try:
            self.pics = self.get_images(
                pdf_path,
                pg_num,
                base_name_of_file,
                output_dir,
                min_img_pixels,
                scanned_copy,
                image_base_dir
            )

        except Exception:
            self.logger.exception(
                f"Unexpected failure in image extraction "
                f"for page {pg_num} of {base_name_of_file}"
            )
            self.pics = {}

    def walk_layout(self, obj):
        if isinstance(obj, LTImage):
            yield obj

        elif hasattr(obj, "__iter__"):
            for child in obj:
                yield from self.walk_layout(child)

    def get_images_from_page(self, page_layout):
        return [
            img
            for element in page_layout
            for img in self.walk_layout(element)
        ]

    def register_global(self, img_name, path, text_content = None, text_language = None, width = None, height = None):
        reg = self.unique_images.setdefault(
            img_name,
            {
                "count": 0,
                "path": path,
                "text": text_content if text_content else "",
                "language": text_language,
                "width": width,
                "height": height,
                "pages": set()
            }
        )

        reg["count"] += 1
        reg["pages"].add(self.pg_num)


    def remove_hash(self, img_name):

        if img_name in self.pics:
            del self.pics[img_name]

    def remove_empty_dirs_up_to(self, start_dir, stop_dir):
        current = start_dir
        while current and current != stop_dir and os.path.isdir(current):
            try:
                os.rmdir(current)
            except OSError:
                break
            current = os.path.dirname(current)

    def has_visual_content(self, image_path):
        try:
            with Image.open(image_path) as img:
                img_array = np.array(img.convert("RGB"))
                height, width = img_array.shape[:2]

                tile_size = 32

                max_contrast_pct = 0.0
                for y in range(0, height, tile_size):
                    for x in range(0, width, tile_size):
                        tile = img_array[y:y + tile_size, x:x + tile_size]
                        if tile.size == 0:
                            continue
                        channel_std = tile.reshape(-1, tile.shape[-1]).std(axis=0)
                        tile_contrast_pct = (channel_std.max() / 255.0) * 100
                        max_contrast_pct = max(max_contrast_pct, tile_contrast_pct)

                return max_contrast_pct > 2
        except Exception as e:
            self.logger.warning(f"Failed to analyze image {image_path}: {e}")
            return True

    def extract_text_content(self, image_path):
        try:
            with Image.open(image_path) as img:
                # Image for OCR (keep original colors)
                ocr_img = img.convert("RGB") if img.mode != "RGB" else img

                # Image for heuristic analysis
                img_gray = img.convert("L")
                img_array = np.array(img_gray)

                dark_pixels = np.sum(img_array < 200)
                total_pixels = img_array.size
                dark_ratio = dark_pixels / total_pixels if total_pixels > 0 else 0

                variance = np.var(img_array) if img_array.size > 100 else 0

                looks_like_text = (
                    0.05 < dark_ratio < 0.95
                    and variance > 100
                )

                self.logger.debug(
                    f"{image_path} | "
                    f"dark_ratio={dark_ratio:.2%}, "
                    f"variance={variance:.1f}, "
                    f"looks_like_text={looks_like_text}"
                )

                if not looks_like_text:
                    self.logger.info(
                        f"Skipping {image_path}: no meaningful text-like content detected."
                    )
                    return None, None


                try:
                    # config = "--oem 3 --psm 6"

                    # ocr_text = pytesseract.image_to_string(
                    #     ocr_img,
                    #     config=config
                    # ).strip()

                    ocr_text = extract_text(image_path, self.ocr_language, self.ocr_engine)

                    if not ocr_text:
                        self.logger.info(f"OCR found no text in {image_path}.")
                        return None, None

                    lang, confidence = detect_language(ocr_text)

                    if confidence >= 0.3:
                        return ocr_text, lang

                    self.logger.info(
                        f"Rejected OCR text due to low language confidence "
                        f"({confidence:.3f}) for {image_path}"
                    )
                    return None, None

                except Exception as e:
                    self.logger.debug(f"OCR failed for {image_path}: {e}")
                    return None, None

        except Exception as e:
            self.logger.warning(f"Failed to analyze image {image_path}: {e}")
            return None, None
    
    def should_skip(self, lt_image, min_img_pixels):
        try:
            attrs = lt_image.stream.attrs

            if attrs.get("ImageMask") is True:
                return True

            w, h = lt_image.srcsize

            min_area = min_img_pixels * min_img_pixels
            total_area = w * h
            
            if total_area < min_area:
                self.logger.info(
                    f"Skipping small image with dimensions {w}x{h} pixels "
                    f"(area: {total_area}, minimum: {min_area})"
                )
                return True

        except Exception:
            pass

        return False

    def get_images(
        self,
        pdf_path,
        page_num,
        file_basename,
        output_dir,
        min_img_pixels,
        scanned_copy,
        image_base_dir="manifest"
    ):
        if scanned_copy:
            return
        saved_images = {}

        page_layouts = extract_pages(
            pdf_path,
            page_numbers=[int(page_num) - 1]
        )

        file_dir = os.path.join(
            output_dir,
            image_base_dir,
            file_basename,
            'images'
        )

        iw = None

        for page_layout in page_layouts:

            images = self.get_images_from_page(
                page_layout
            )

            for lt_image in images:

                try:

                    if self.should_skip(lt_image, min_img_pixels):
                        continue
                
                    if iw is None:
                        os.makedirs(file_dir, exist_ok=True)
                        iw = StableImageWriter(file_dir)

                    img_saved = iw.export_image(lt_image)

                    if not img_saved:
                        continue
                    
                    temp_path = os.path.join(
                        file_dir,
                        img_saved
                    )

                    if not os.path.exists(temp_path):
                        continue

                    img_name = lt_image.name

                    if self.pdf_type in ('egazette', 'sebi'):
                        canonical_dir = os.path.join(
                            file_dir, img_name, "full", "max", "0"
                        )
                        os.makedirs(canonical_dir, exist_ok=True)
                        final_path = os.path.join(canonical_dir, "default.png")
                    else:
                        canonical_dir = None
                        final_path = os.path.join(file_dir, f"{img_name}.png")

                    with Image.open(temp_path) as img:
                        converted = img
                        if img.mode == "P":
                            converted = img.convert("RGBA")
                        if img.mode in ("RGBA", "LA"):
                            background = Image.new("RGB", img.size, (255, 255, 255))
                            alpha = img.getchannel("A")
                            background.paste(img.convert("RGB"), mask=alpha)
                            converted = background
                        elif img.mode != "RGB":
                            converted = img.convert("RGB")

                        converted.save(final_path, "PNG")
                        img_width, img_height = converted.size

                    if os.path.exists(temp_path):
                        os.remove(temp_path)

                    if self.figure_text and not self.has_visual_content(final_path):
                        os.remove(final_path)
                        if canonical_dir:
                            self.remove_empty_dirs_up_to(canonical_dir, file_dir)
                        continue

                    if self.figure_text:
                        text_content, text_language = self.extract_text_content(final_path)
                    else:
                        text_content, text_language = None, None

                    saved_images[img_name] = {
                        "name": img_name,
                        "path": final_path,
                        "text": text_content,
                        "language": text_language,
                        "width": img_width,
                        "height": img_height
                    }

                    self.register_global(
                        img_name,
                        final_path,
                        text_content,
                        text_language,
                        img_width,
                        img_height
                    )

                except Exception:
                    self.logger.exception(
                        f"Failed image "
                        f"{getattr(lt_image, 'name', '<unnamed>')}"
                    )

                    continue
        
        if iw is not None and not saved_images:
            try:
                os.rmdir(file_dir)
            except OSError:
                pass

        return saved_images
