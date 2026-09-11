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


# filters pdfminer's get_data() undoes completely, leaving raw samples behind -
# DCT/JPX/JBIG2 streams are left encoded and CCITT is 1-bit, which pdfminer
# already writes out itself
RAW_SAMPLE_FILTERS = (
    pdftypes.LITERALS_FLATE_DECODE + pdftypes.LITERALS_LZW_DECODE +
    pdftypes.LITERALS_ASCII85_DECODE + pdftypes.LITERALS_ASCIIHEX_DECODE +
    pdftypes.LITERALS_RUNLENGTH_DECODE
)

COLORSPACE_COMPONENTS = {
    'DeviceGray': 1, 'G': 1, 'CalGray': 1,
    'DeviceRGB': 3, 'RGB': 3, 'CalRGB': 3,
    'DeviceCMYK': 4, 'CMYK': 4,
}

PIL_MODES = {1: 'L', 3: 'RGB', 4: 'CMYK'}


def literal_name(obj):
    name = getattr(pdftypes.resolve1(obj), 'name', None)
    return name.decode('latin-1') if isinstance(name, bytes) else name


def get_colorspace(image):
    """The image's colorspace as a flat list, with its references resolved.

    LTImage wraps a bare name in a list but leaves an indirect array as it is,
    so an /Indexed colorspace can arrive as [/Indexed, /DeviceRGB, 3, lut] or
    as [<PDFObjRef>] pointing at that array.
    """
    colorspace = [pdftypes.resolve1(c) for c in image.colorspace]
    if len(colorspace) == 1 and isinstance(colorspace[0], list):
        colorspace = [pdftypes.resolve1(c) for c in colorspace[0]]
    return colorspace


def get_components(colorspace):
    """Number of components of a device-like colorspace, None for any other."""
    if isinstance(colorspace, list):
        if not colorspace:
            return None
        name = literal_name(colorspace[0])
        if name == 'ICCBased' and len(colorspace) > 1:
            profile = pdftypes.resolve1(colorspace[1])
            ncomp = pdftypes.resolve1(profile.get('N')) if isinstance(profile, pdftypes.PDFStream) else None
            return ncomp if ncomp in PIL_MODES else None
        return COLORSPACE_COMPONENTS.get(name)
    return COLORSPACE_COMPONENTS.get(literal_name(colorspace))


def unpack_samples(data, width, height, ncomp, bpc):
    """(height, width, ncomp) array of the image's samples as integers.

    Each row starts on a byte boundary, as PDF stores them. A stream that runs
    short is padded out with zeros rather than refused.
    """
    row_bytes = (width * ncomp * bpc + 7) // 8
    size = row_bytes * height
    buf = np.frombuffer(data[:size], dtype=np.uint8)
    if len(buf) < size:
        buf = np.concatenate([buf, np.zeros(size - len(buf), dtype=np.uint8)])
    rows = buf.reshape(height, row_bytes)

    nsamples = width * ncomp
    if bpc == 8:
        samples = rows[:, :nsamples]
    elif bpc == 16:
        samples = np.ascontiguousarray(rows[:, :nsamples * 2]).view('>u2')
    else:
        bits = np.unpackbits(rows, axis=1)[:, :nsamples * bpc]
        bits = bits.reshape(height, nsamples, bpc).astype(np.uint16)
        weights = 1 << np.arange(bpc - 1, -1, -1, dtype=np.uint16)
        samples = (bits * weights).sum(axis=2)

    return samples.reshape(height, width, ncomp)


def decode_image(image):
    """An RGB/L PIL image of an LTImage pdfminer's ImageWriter cannot write.

    ImageWriter reads only 1-bit and 8-bit samples - a 2-, 4- or 16-bit image
    raises UnboundLocalError from _save_bytes - and knows nothing of /Indexed,
    writing the palette indices out as though they were the colour itself (as
    grey, inverted, or as three bytes a pixel that are really one). Returns
    None for an image it should be left to write, or one this cannot read
    either (a Lab or DeviceN image, a DCT/JPX/JBIG2/CCITT stream).
    """
    filters = image.stream.get_filters()
    if any(f not in RAW_SAMPLE_FILTERS for f, _ in filters):
        return None

    colorspace = get_colorspace(image)
    bpc = pdftypes.resolve1(image.bits)
    width, height = (pdftypes.resolve1(v) for v in image.srcsize)
    indexed = bool(colorspace) and literal_name(colorspace[0]) in ('Indexed', 'I')

    if not indexed and bpc in (1, 8):
        return None
    if bpc not in (1, 2, 4, 8, 16) or not width or not height:
        return None

    data = image.stream.get_data()

    if indexed:
        if len(colorspace) < 4:
            return None
        base_ncomp = get_components(colorspace[1])
        hival = pdftypes.resolve1(colorspace[2])
        lookup = pdftypes.resolve1(colorspace[3])
        if isinstance(lookup, pdftypes.PDFStream):
            lookup = lookup.get_data()
        if base_ncomp is None or not isinstance(hival, int) or not isinstance(lookup, bytes):
            return None

        # the palette as a one-row image in the base colorspace, so a CMYK
        # palette goes through the same conversion a CMYK image would
        ncolors = hival + 1
        lookup = lookup[:ncolors * base_ncomp].ljust(ncolors * base_ncomp, b'\x00')
        palette = Image.frombytes(PIL_MODES[base_ncomp], (ncolors, 1), lookup)
        palette = np.asarray(palette.convert('RGB')).reshape(ncolors, 3)

        indices = unpack_samples(data, width, height, 1, bpc)[:, :, 0]
        return Image.fromarray(palette[np.minimum(indices, hival)], 'RGB')

    ncomp = get_components(colorspace[0] if len(colorspace) == 1 else colorspace)
    if ncomp is None:
        return None

    samples = unpack_samples(data, width, height, ncomp, bpc)
    if bpc == 16:
        samples = samples >> 8
    else:
        samples = samples * 255 // ((1 << bpc) - 1)
    samples = samples.astype(np.uint8)

    img = Image.fromarray(samples[:, :, 0] if ncomp == 1 else samples, PIL_MODES[ncomp])
    return img.convert('RGB') if ncomp == 4 else img


class StableImageWriter(ImageWriter):
    def _create_unique_image_name(self, image, ext):
        name = image.name + ext
        path = os.path.join(self.outdir, name)
        return name, path

    def export_image(self, image):
        img = decode_image(image)
        if img is None:
            return super().export_image(image)

        name, path = self._create_unique_image_name(image, '.png')
        img.save(path, 'PNG')
        return name


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

class PageImages:
    """The image-bearing pages of one pdf, extracted in a single pass.

    pdfminer reparses the whole document to reach a page, so an
    ``extract_pages()`` call per page walks the file as many times as it has
    pages - 140 of the 297 seconds a 323-page gazette took, on a document
    where exactly one page draws an image.  The parsed xml already says which
    pages carry one (pdfminer writes an ``<image>`` for every image it lays
    out), so the rest are never extracted at all and those that are come off
    one generator advanced in page order rather than one call each.

    ``page_nums`` are the xml's own page ids, which are the pdf's 1-based page
    numbers - the same numbering ``Pictures.get_images`` already indexes by.
    """

    def __init__(self, pdf_path, page_nums):
        self.logger = logging.getLogger(__name__)
        self.pdf_path = pdf_path
        self.image_pages = set(page_nums)
        self.pending = sorted(self.image_pages)
        self.layouts = None

    def get_layout(self, page_num):
        """The layout of page_num, or None when that page draws no image."""
        if page_num not in self.image_pages:
            return None

        if page_num not in self.pending:
            # asked for out of the order the pages are built in, so the single
            # pass has already gone past it - read that one page on its own
            return self.extract_one(page_num)

        if self.layouts is None:
            self.layouts = extract_pages(
                self.pdf_path,
                page_numbers=[num - 1 for num in self.pending]
            )

        # the pages come off the generator in the order they were asked for,
        # so walk it forward to the one wanted and drop what it passes
        while self.pending:
            num = self.pending.pop(0)

            try:
                layout = next(self.layouts)
            except StopIteration:
                self.logger.warning(
                    "Ran out of pages looking for page %s of %s",
                    page_num, self.pdf_path
                )
                return self.extract_one(page_num)

            if num == page_num:
                return layout

        return self.extract_one(page_num)

    def extract_one(self, page_num):
        for layout in extract_pages(self.pdf_path, page_numbers=[page_num - 1]):
            return layout

        return None

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
        ocr_engine="tesseract",
        page_images=None
    ):
        self.logger = logging.getLogger(__name__)

        self.pg_num = pg_num
        self.page_images = page_images
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

        if self.page_images is not None:
            # one pass over the document for the whole pdf, and nothing at all
            # for a page the xml says draws no image
            page_layout = self.page_images.get_layout(int(page_num))
            page_layouts = [] if page_layout is None else [page_layout]
        else:
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

                    # StableImageWriter writes a decoded image straight to
                    # <name>.png, which outside egazette/sebi is final_path
                    # itself - removing it then deletes the image just saved
                    if temp_path != final_path and os.path.exists(temp_path):
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
