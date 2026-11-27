"""Local, single-page OCR for review of image-based source documents."""
import base64
import json
import math
import sys

import fitz


def recognize(path, page_number, language, tessdata, preview=False):
    with fitz.open(path) as pdf:
        page = pdf[page_number - 1]
        area = page.rect.width * page.rect.height
        if not math.isfinite(area) or area <= 0:
            raise ValueError("Invalid PDF page size")
        dpi = min(240, int(72 * math.sqrt(10_000_000 / area)))
        if dpi < 36:
            raise ValueError("PDF page exceeds raster size limit")
        text_page = page.get_textpage_ocr(language=language, dpi=dpi, full=True, tessdata=tessdata)
        result = {"text": page.get_text("text", textpage=text_page).strip()}
        if preview:
            scale = min(2, 1500 / max(page.rect.width, page.rect.height))
            image = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False)
            result["preview_jpeg"] = base64.b64encode(image.tobytes("jpeg", jpg_quality=85)).decode("ascii")
        return result


if __name__ == "__main__":
    print(json.dumps(recognize(sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4], "--preview" in sys.argv[5:])))
