import logging
from pdfminer.high_level import extract_pages
from pdfminer.layout import LTImage
import os, imghdr

class Figure:
    def __init__(self, fig):
        self.logger = logging.getLogger(__name__)
        self.figname = fig.attrib['name']
        self.coords = tuple(map(float, fig.attrib["bbox"].split(",")))
        self.height = self.coords[3] - self.coords[1]
        self.width = self.coords[2] - self.coords[0]
        self.has_fig = self.has_figure(fig)
    
    def has_figure(self, fig):
        return fig.find("image") is not None

class Pictures:
    def __init__(self, pdf_path, pg_num, base_name_of_file, output_dir, image_base_dir="images"):
        self.logger = logging.getLogger(__name__)

        try:
            self.pics = self.get_images(pdf_path, pg_num, base_name_of_file, output_dir, image_base_dir)
        except Exception as e:
            self.logger.error(
                f"Unexpected failure in image extraction for page {pg_num} of {base_name_of_file}: {e}")
            self.pics = {}

    def get_images(self, pdf_path, page_num, file_basename, output_dir, image_base_dir="images"):
        saved_images = {}

        def walk_layout(obj):
            if isinstance(obj, LTImage):
                yield obj
            elif hasattr(obj, "__iter__"):
                for child in obj:
                    yield from walk_layout(child)

        for page_layout in extract_pages(pdf_path, page_numbers=[int(page_num) - 1]):
            for element in page_layout:
                for lt_image in walk_layout(element):
                    try:
                        if not hasattr(lt_image, "stream"):
                            self.logger.warning(f"LTImage without stream found on page {page_num}")
                            continue

                        raw_data = lt_image.stream.get_rawdata()

                        if not raw_data:
                            self.logger.warning(f"Empty image stream in {lt_image.name} on page {page_num}")
                            continue

                        try:
                            img_type = imghdr.what(None, h=raw_data)
                            if not img_type:
                                self.logger.info(f"Skipping non-standard image: {lt_image.name}")
                                continue
                        except Exception as e:
                            self.logger.warning(f"Image type detection failed for {lt_image.name}: {e}")
                            continue


                        page_dir = os.path.join(output_dir, image_base_dir)
                        os.makedirs(page_dir, exist_ok=True)
                        final_file = os.path.join(page_dir, f"{lt_image.name}.{img_type}")
                       
                        try:
                            with open(final_file, "wb") as f:
                                f.write(raw_data)
                        except Exception as e:
                            self.logger.error(f"Failed to save image {final_file}: {e}")
                            continue

                        saved_images[lt_image.name] = final_file

                    except Exception as e:
                        self.logger.warning(
                            f"Unexpected error extracting image {getattr(lt_image, 'name', '<unnamed>')}: {e}")
                        continue

        return saved_images