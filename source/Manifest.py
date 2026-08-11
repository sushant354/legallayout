import json
import logging
import os
import warnings
from pathlib import Path

import iiif_prezi3 as p3
from PIL import Image

IIIF_PRESENTATION_3_CONTEXT = "http://iiif.io/api/presentation/3/context.json"
IIIF_IMAGE_3_CONTEXT = "http://iiif.io/api/image/3/context.json"


class IIIFManifest:

    def __init__(self, output_dir, label, base_prez_uri=None, server_root=None,
                 rights=None, provider_id=None, provider_name=None, attribution=None):
        self.logger = logging.getLogger(__name__)
        # Optional, purely additive manifest-level metadata 
        self.rights = rights
        self.provider_id = provider_id
        self.provider_name = provider_name
        self.attribution = attribution

        self.output_dir = Path(output_dir).resolve()
        self.label = label

        self.manifest_dir = self.output_dir / "manifest" / label
        self.manifest_path = self.manifest_dir / "manifest.json"
        self.images_dir = self.manifest_dir / "images"

        self.base_prez_uri = self._resolve_base_uri(base_prez_uri)
        self.url_path_prefix = self._resolve_url_path_prefix(server_root)
        self.manifest_base_uri = "/".join(
            p for p in (self.base_prez_uri, self.url_path_prefix, "manifest", self.label) if p
        )

    def _resolve_base_uri(self, base_prez_uri=None):

        if base_prez_uri:
            return base_prez_uri.rstrip("/")

        return os.environ.get(
            "PUBLIC_BASE_URL",
            "http://localhost:8000"
        ).rstrip("/")

    def _resolve_url_path_prefix(self, server_root):
        if not server_root:
            return ""

        server_root_resolved = Path(server_root).resolve()

        try:
            rel = self.output_dir.relative_to(server_root_resolved)
        except ValueError:
            raise ValueError(
                f"output_dir ('{self.output_dir}') is not located within "
                f"server_root ('{server_root_resolved}') - IIIF manifest URLs can't be "
                f"expressed relative to a server root that doesn't contain the output "
                f"directory."
            )

        rel_posix = rel.as_posix()
        return "" if rel_posix == "." else rel_posix

    def ensure_dirs(self):
        self.manifest_dir.mkdir(parents=True, exist_ok=True)

    def build_uri(self, resource_path, sub_dir="images"):

        resource_path = Path(resource_path).resolve()
        resource_dir = self.manifest_dir / sub_dir
        base_uri = f"{self.manifest_base_uri}/{sub_dir}"

        try:
            rel = resource_path.relative_to(resource_dir)
            return f"{base_uri}/{rel.as_posix()}"
        except ValueError:
            return f"{base_uri}/{resource_path.name}"

    def _manifest_uri(self, *parts):
        return "/".join([self.manifest_base_uri, *parts])

    def _write_level0_image_service(self, image_path):
        image_path = Path(image_path)
        with Image.open(image_path) as im:
            width, height = im.size

        format_token = image_path.suffix.lstrip(".").lower() or "png"
        if format_token == "jpeg":
            format_token = "jpg"

        # image_path is already written by Figure.py directly at the IIIF Level 0
        # compliant location (images/<img_name>/full/max/0/default.<ext>) - there is
        # exactly one physical copy of the image, shared by both the HTML <img> tag and
        # this Image API service. parents[3] walks back up past 0/max/full to the
        # per-image service directory.
        service_dir = image_path.parents[3]
        service_id = self.build_uri(service_dir, sub_dir="images")

        info = {
            "@context": IIIF_IMAGE_3_CONTEXT,
            "id": service_id,
            "type": "ImageService3",
            "protocol": "http://iiif.io/api/image",
            "profile": "level0",
            "width": width,
            "height": height,
        }
        (service_dir / "info.json").write_text(
            json.dumps(info, indent=2), encoding="utf-8"
        )

        canonical_image_uri = self.build_uri(image_path, sub_dir="images")
        return service_id, canonical_image_uri, format_token

    def get_manifest_uri(self):
        """Public URI of the generated manifest.json, for linking to it (e.g. from the HTML output)."""
        return self._manifest_uri("manifest.json")

    def generate_iiif(self, image_entries, metadata=None):
        self.ensure_dirs()

        manifest = p3.Manifest(
            id=self._manifest_uri("manifest.json"),
            label=f"IIIF Manifest: {self.label}",
            context=IIIF_PRESENTATION_3_CONTEXT,
            items=[],
        )

        manifest.summary = f"IIIF Manifest with images for {self.label}"
        manifest.viewingDirection = "left-to-right"
        manifest.behavior = ["paged"]

        if metadata:
            for key, value in metadata.items():
                manifest.add_metadata(str(key), str(value))

        if self.rights:
            manifest.rights = self.rights
        if self.attribution:
            manifest.requiredStatement = p3.KeyValueString(
                label="Attribution", value=self.attribution
            )
        if self.provider_id and self.provider_name:
            manifest.provider = [
                p3.Provider(id=self.provider_id, label=self.provider_name)
            ]
        elif self.provider_id or self.provider_name:
            self.logger.warning(
                "IIIF provider needs both an id (URI) and a name to be spec-valid - "
                "only one was supplied, so no provider was added to the manifest."
            )

        for idx, entry in enumerate(image_entries):

            try:

                image_path = Path(entry["path"]).resolve()

                if not image_path.exists():
                    self.logger.warning(
                        "Image not found: %s",
                        image_path
                    )
                    continue

                page_id = f"p{idx + 1}"
                image_url = self.build_uri(image_path)
                image_format = self.get_image_format(image_path)

                pages = entry.get("pages") or []
                if len(pages) == 1:
                    label = f"Page {pages[0]}"
                elif len(pages) > 1:
                    label = f"Pages {', '.join(str(p) for p in pages)}"
                else:
                    label = f"Image {idx + 1}"

                canvas = manifest.make_canvas(
                    id=self._manifest_uri("canvas", page_id),
                    label=label
                )
                canvas.set_hwd_from_file(str(image_path))

                service_id, canonical_image_url, _fmt_token = self._write_level0_image_service(
                    image_path
                )

                anno_page = canvas.add_image(
                    canonical_image_url,
                    anno_id=self._manifest_uri("annotation", page_id),
                    anno_page_id=self._manifest_uri("annotationpage", page_id),
                    format=image_format,
                )
                image_body = anno_page.items[0].body
                image_body.add_service(
                    p3.ServiceV3(id=service_id, type="ImageService3", profile="level0")
                )


                canvas.add_thumbnail(image_url, format=image_format)

                text = entry.get("text")
                if text:
                    ocr_body = p3.TextualBody(
                        value=text,
                        format="text/plain",
                        language=entry.get("language") or None,
                    )
                    canvas.make_annotation(
                        anno_page_id=self._manifest_uri("annotationpage", f"{page_id}-ocr"),
                        id=self._manifest_uri("annotation", f"{page_id}-ocr"),
                        motivation="supplementing",
                        body=ocr_body,
                        target=canvas.id,
                    )

            except Exception:
                self.logger.exception(
                    "Failed processing image %s",
                    entry.get("path")
                )


        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")
            content = manifest.json()

        self.manifest_path.write_text(content, encoding="utf-8")


    def get_image_format(self, image_path):
        suffix = Path(image_path).suffix.lower()

        if suffix == ".png":
            return "image/png"

        if suffix in (".jpg", ".jpeg"):
            return "image/jpeg"

        if suffix == ".tif":
            return "image/tiff"

        if suffix == ".tiff":
            return "image/tiff"

        return f"image/{suffix.lstrip('.')}"

    def create_from_images(self, image_entries, metadata=None):

        try:
            self.generate_iiif(image_entries, metadata)
            return self.manifest_path

        except Exception:
            self.logger.exception(
                "Failed generating IIIF manifest for %s",
                self.label
            )
            return None
