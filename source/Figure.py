import os
import logging

from PIL import Image

from pdfminer.high_level import extract_pages
from pdfminer.layout import LTImage
from pdfminer.image import ImageWriter


# ==========================================================
# IMAGE WRITER
# ==========================================================

class StableImageWriter(ImageWriter):
    """
    Disable pdfminer auto-versioning:

        image9.jpg
        image9.0.jpg
        image9.1.jpg

    Always returns:

        image9.jpg
    """

    def _create_unique_image_name(self, image, ext):
        name = image.name + ext
        path = os.path.join(self.outdir, name)
        return name, path


# ==========================================================
# FIGURE
# ==========================================================

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


# ==========================================================
# PICTURES
# ==========================================================

class Pictures:
    """
    Uses image name as global key.

    Example:
        image9
        image10

    If same image name appears >1 time across pages:
        remove globally.
    """

    def __init__(
        self,
        pdf_path,
        pg_num,
        base_name_of_file,
        output_dir,
        unique_images,
        image_base_dir="images"
    ):
        self.logger = logging.getLogger(__name__)

        self.pg_num = pg_num
        self.unique_images = unique_images

        try:
            self.pics = self.get_images(
                pdf_path,
                pg_num,
                base_name_of_file,
                output_dir,
                image_base_dir
            )

        except Exception:
            self.logger.exception(
                f"Unexpected failure in image extraction "
                f"for page {pg_num} of {base_name_of_file}"
            )
            self.pics = {}

    # ======================================================
    # WALK PDF TREE
    # ======================================================

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

    # ======================================================
    # VALIDATE REAL IMAGE
    # ======================================================

    def valid_image(self, path):
        try:
            with Image.open(path) as img:
                img.load()

                w, h = img.size

                if w < 2 or h < 2:
                    return False

            return True

        except Exception:
            return False

    # ======================================================
    # GLOBAL REGISTRY
    # ======================================================

    def register_global(self, img_name, path):
        reg = self.unique_images.setdefault(
            img_name,
            {
                "count": 0,
                "path": path,
                "pages": set()
            }
        )

        reg["count"] += 1
        reg["pages"].add(self.pg_num)

    # ======================================================
    # REMOVE IMAGE FROM LOCAL PAGE
    # ======================================================

    def remove_hash(self, img_name):
        """
        Keep same method name for compatibility.
        """

        if img_name in self.pics:
            del self.pics[img_name]

    # ======================================================
    # SKIP USELESS PDF OBJECTS
    # ======================================================

    def should_skip(self, lt_image):
        try:
            attrs = lt_image.stream.attrs

            # transparency mask
            if attrs.get("ImageMask") is True:
                return True

            w, h = lt_image.srcsize

            # tiny bullets/icons
            if w < 20 or h < 20:
                return True

        except Exception:
            pass

        return False

    # ======================================================
    # MAIN EXTRACTION
    # ======================================================

    def get_images(
        self,
        pdf_path,
        page_num,
        file_basename,
        output_dir,
        image_base_dir="images"
    ):
        saved_images = {}

        page_layouts = extract_pages(
            pdf_path,
            page_numbers=[int(page_num) - 1]
        )

        file_dir = os.path.join(
            output_dir,
            image_base_dir,
            file_basename
        )

        os.makedirs(file_dir, exist_ok=True)

        for page_layout in page_layouts:

            images = self.get_images_from_page(
                page_layout
            )

            iw = StableImageWriter(file_dir)

            for lt_image in images:

                try:
                    # --------------------------------
                    # skip junk objects
                    # --------------------------------
                    if self.should_skip(lt_image):
                        continue

                    # --------------------------------
                    # export
                    # --------------------------------
                    img_saved = iw.export_image(
                        lt_image
                    )

                    if not img_saved:
                        continue

                    temp_path = os.path.join(
                        file_dir,
                        img_saved
                    )

                    # --------------------------------
                    # file exists
                    # --------------------------------
                    if not os.path.exists(temp_path):
                        continue

                    # --------------------------------
                    # zero bytes
                    # --------------------------------
                    if os.path.getsize(temp_path) == 0:
                        os.remove(temp_path)
                        continue

                    # --------------------------------
                    # validate actual image
                    # --------------------------------
                    if not self.valid_image(temp_path):
                        os.remove(temp_path)
                        continue

                    # --------------------------------
                    # final name = image name itself
                    # --------------------------------
                    img_name = lt_image.name

                    ext = os.path.splitext(
                        temp_path
                    )[1].lower()

                    final_name = img_name + ext

                    final_path = os.path.join(
                        file_dir,
                        final_name
                    )

                    # --------------------------------
                    # move if not exists
                    # --------------------------------
                    if temp_path != final_path:

                        if not os.path.exists(final_path):
                            os.replace(
                                temp_path,
                                final_path
                            )
                        else:
                            os.remove(temp_path)

                    # --------------------------------
                    # local page refs
                    # --------------------------------
                    saved_images[img_name] = {
                        "name": img_name,
                        "path": final_path
                    }

                    # --------------------------------
                    # global tracking
                    # --------------------------------
                    self.register_global(
                        img_name,
                        final_path
                    )

                except Exception:
                    self.logger.exception(
                        f"Failed image "
                        f"{getattr(lt_image, 'name', '<unnamed>')}"
                    )

                    continue

        return saved_images
    

# import os
# import hashlib
# import logging

# from PIL import Image

# from pdfminer.high_level import extract_pages
# from pdfminer.layout import LTImage
# from pdfminer.image import ImageWriter


# class StableImageWriter(ImageWriter):
#     def _create_unique_image_name(self, image, ext):
#         name = image.name + ext
#         path = os.path.join(self.outdir, name)
#         return name, path

# class Figure:
#     def __init__(self, fig):
#         self.logger = logging.getLogger(__name__)

#         self.figname = fig.attrib["name"]

#         self.coords = tuple(
#             map(float, fig.attrib["bbox"].split(","))
#         )

#         self.height = self.coords[3] - self.coords[1]
#         self.width = self.coords[2] - self.coords[0]

#         self.has_fig = self.has_figure(fig)

#     def has_figure(self, fig):
#         return fig.find("image") is not None


# class Pictures:
#     def __init__(
#         self,
#         pdf_path,
#         pg_num,
#         base_name_of_file,
#         output_dir,
#         unique_images,
#         image_base_dir="images"
#     ):
#         self.logger = logging.getLogger(__name__)

#         self.pg_num = pg_num
#         self.unique_images = unique_images

#         try:
#             self.pics = self.get_images(
#                 pdf_path,
#                 pg_num,
#                 base_name_of_file,
#                 output_dir,
#                 image_base_dir
#             )

#         except Exception:
#             self.logger.exception(
#                 f"Unexpected failure in image extraction "
#                 f"for page {pg_num} of {base_name_of_file}"
#             )
#             self.pics = {}

#     def walk_layout(self, obj):
#         if isinstance(obj, LTImage):
#             yield obj

#         elif hasattr(obj, "__iter__"):
#             for child in obj:
#                 yield from self.walk_layout(child)

#     def get_images_from_page(self, page_layout):
#         return [
#             img
#             for element in page_layout
#             for img in self.walk_layout(element)
#         ]

#     def valid_image(self, path):
        
#         try:
#             with Image.open(path) as img:
#                 img.load()

#                 w, h = img.size

#                 if w < 2 or h < 2:
#                     return False

#             return True

#         except Exception:
#             return False


#     def file_hash(self, path):
#         h = hashlib.sha256()

#         with open(path, "rb") as f:
#             for chunk in iter(
#                 lambda: f.read(8192),
#                 b""
#             ):
#                 h.update(chunk)

#         return h.hexdigest()


#     def register_global(self, img_hash, path):
#         reg = self.unique_images.setdefault(
#             img_hash,
#             {
#                 "count": 0,
#                 "path": path,
#                 "pages": set()
#             }
#         )

#         reg["count"] += 1
#         reg["pages"].add(self.pg_num)


#     def remove_hash(self, img_hash):
#         delete_keys = []

#         for k, v in self.pics.items():
#             if v["hash"] == img_hash:
#                 delete_keys.append(k)

#         for k in delete_keys:
#             del self.pics[k]

#     def should_skip(self, lt_image):
#         try:
#             attrs = lt_image.stream.attrs

#             if attrs.get("ImageMask") is True:
#                 return True

#             w, h = lt_image.srcsize

#             if w < 20 or h < 20:
#                 return True

#         except Exception:
#             pass

#         return False

#     def get_images(
#         self,
#         pdf_path,
#         page_num,
#         file_basename,
#         output_dir,
#         image_base_dir="images"
#     ):
#         saved_images = {}

#         page_layouts = extract_pages(
#             pdf_path,
#             page_numbers=[int(page_num) - 1]
#         )

#         file_dir = os.path.join(
#             output_dir,
#             image_base_dir,
#             file_basename
#         )

#         os.makedirs(file_dir, exist_ok=True)

#         for page_layout in page_layouts:

#             images = self.get_images_from_page(
#                 page_layout
#             )

#             iw = StableImageWriter(file_dir)

#             for lt_image in images:

#                 try:
#                     if self.should_skip(lt_image):
#                         continue

#                     img_saved = iw.export_image(
#                         lt_image
#                     )

#                     if not img_saved:
#                         continue

#                     temp_path = os.path.join(
#                         file_dir,
#                         img_saved
#                     )

    
#                     if not os.path.exists(temp_path):
#                         continue


#                     if os.path.getsize(temp_path) == 0:
#                         os.remove(temp_path)
#                         continue

#                     if not self.valid_image(temp_path):
#                         os.remove(temp_path)
#                         continue


#                     img_hash = self.file_hash(
#                         temp_path
#                     )

#                     ext = os.path.splitext(
#                         temp_path
#                     )[1].lower()

#                     final_name = img_hash + ext

#                     final_path = os.path.join(
#                         file_dir,
#                         final_name
#                     )

#                     if not os.path.exists(final_path):
#                         os.replace(
#                             temp_path,
#                             final_path
#                         )
#                     else:
#                         os.remove(temp_path)


#                     saved_images[
#                         lt_image.name
#                     ] = {
#                         "hash": img_hash,
#                         "path": final_path
#                     }


#                     self.register_global(
#                         img_hash,
#                         final_path
#                     )

#                 except Exception:
#                     self.logger.exception(
#                         f"Failed to extract image "
#                         f"{getattr(lt_image, 'name', '<unnamed>')}"
#                     )

#                     continue

#         return saved_images
