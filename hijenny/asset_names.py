"""Verified names for public drug searches."""

ASSET_NAMES = {
    "ivonescimab": (
        ("依沃西单抗", "依沃西单抗注射液", "依达方"),
        "https://akesobio.com/cn/rd-and-science/products-center/ivonescimab/",
    ),
    "osimertinib": (
        ("奥希替尼", "甲磺酸奥希替尼片", "泰瑞沙", "Tagrisso"),
        "https://www.astrazeneca.com.cn/zh/media/press-releases/2025/01-02-01.html",
    ),
}


def verified_asset(query):
    normalized = query.strip().removesuffix("®").strip().casefold()
    for name, (aliases, source) in ASSET_NAMES.items():
        if normalized in {alias.casefold() for alias in (name, *aliases)}:
            return {"name": name, "source": source}
    return None
