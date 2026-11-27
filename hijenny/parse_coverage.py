"""Page ranges that need source review, derived from stored PDF parser metadata."""


def _ranges(pages, limit):
    ranges = []
    for page in sorted({p for p in pages if type(p) is int and 0 < p <= limit}):
        if ranges and ranges[-1][1] + 1 == page:
            ranges[-1][1] = page
        else:
            ranges.append([page, page])
    return ranges


def pdf_review_ranges(document):
    meta = document.get("meta") or {}
    if meta.get("format") != "pdf":
        return {}
    limit = document.get("page_count") or 0
    errors = [row.get("page") for row in meta.get("page_errors", []) if isinstance(row, dict)]
    result = {key: _ranges(pages, limit) for key, pages in (
        ("scan", meta.get("scanned_pages", [])), ("empty", meta.get("empty_pages", [])), ("error", errors)
    ) if pages}
    total = meta.get("page_count_raw")
    if meta.get("truncated") and type(total) is int and total > limit:
        result["unparsed"] = [[limit + 1, total]]
    return {key: ranges for key, ranges in result.items() if ranges}
