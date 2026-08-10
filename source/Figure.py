import os
import logging
import numpy as np
from .Utils import *
from PIL import Image
from typing import Tuple

from pdfminer.high_level import extract_pages
from pdfminer.layout import LTImage
from pdfminer.image import ImageWriter

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
        image_base_dir="manifest"
    ):
        self.logger = logging.getLogger(__name__)

        self.pg_num = pg_num
        self.ocr_language = ocr_language
        self.unique_images = unique_images
        self.figure_text = figure_text

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

    def register_global(self, img_name, path, text_content = None, text_language = None):
        reg = self.unique_images.setdefault(
            img_name,
            {
                "count": 0,
                "path": path,
                "text": text_content if text_content else "",
                "language": text_language,
                "pages": set()
            }
        )

        reg["count"] += 1
        reg["pages"].add(self.pg_num)


    def remove_hash(self, img_name):
    
        if img_name in self.pics:
            del self.pics[img_name]

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

                    ocr_text = extract_text(image_path, self.ocr_language)

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

                    final_path = os.path.join(
                        file_dir,
                        f"{img_name}.png"
                    )

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
                    
                    if temp_path != final_path:
                        if not os.path.exists(final_path):
                            os.replace(
                                temp_path,
                                final_path
                            )
                        else:
                            os.remove(temp_path)
                    
                    if self.figure_text:
                        text_content, text_language = self.extract_text_content(final_path)
                        if not text_content:
                            os.remove(final_path)
                            continue
                    else:
                        text_content, text_language = None, None

                    saved_images[img_name] = {
                        "name": img_name,
                        "path": final_path,
                        "text": text_content,
                        "language": text_language
                    }

                    self.register_global(
                        img_name,
                        final_path,
                        text_content,
                        text_language
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
