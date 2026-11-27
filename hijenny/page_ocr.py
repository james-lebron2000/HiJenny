"""Local, single-page OCR for review of image-based source documents."""
import math
import sys

import fitz


def recognize(path, page_number, language, tessdata):
    with fitz.open(path) as pdf:
        page = pdf[page_number - 1]
        area = page.rect.width * page.rect.height
        if not math.isfinite(area) or area <= 0:
            raise ValueError("Invalid PDF page size")
        dpi = min(240, int(72 * math.sqrt(10_000_000 / area)))
        if dpi < 36:
            raise ValueError("PDF page exceeds raster size limit")
        text_page = page.get_textpage_ocr(language=language, dpi=dpi, full=True, tessdata=tessdata)
        return page.get_text("text", textpage=text_page).strip()


if __name__ == "__main__":
    print(recognize(sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]))
