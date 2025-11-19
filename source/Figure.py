import logging
from pdfminer.high_level import extract_pages
from pdfminer.layout import LTImage
import os, imghdr
from pdfminer.image import ImageWriter

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
   
    def walk_layout(self, obj):
        if isinstance(obj, LTImage):
            yield obj
        elif hasattr(obj, "__iter__"):
            for child in obj:
                yield from self.walk_layout(child)


    def get_images_from_page(self, page_layout):
        return [img for element in page_layout for img in self.walk_layout(element)]


    def get_images(self, pdf_path, page_num, file_basename, output_dir, image_base_dir="images"):
        saved_images = {}
        page_layouts = extract_pages(pdf_path, page_numbers=[int(page_num) - 1])
        file_dir = os.path.join(output_dir, image_base_dir)
        os.makedirs(file_dir, exist_ok=True)
        for page_layout in page_layouts:
            images = self.get_images_from_page(page_layout)
            iw = ImageWriter(file_dir)

            for lt_image in images:
                try:
                   img_saved = iw.export_image(lt_image)
                   if img_saved:
                       saved_images[lt_image.name] = os.path.join(file_dir, img_saved)
                except Exception as e:
                    self.logger.warning(f"Failed to extract image {getattr(lt_image, 'name', '<unnamed>')}: {e}")
                    continue
            
        return saved_images