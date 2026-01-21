import re

TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
TD_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
HREF_RE = re.compile(r'href="([^"]+)"', re.IGNORECASE)
TAG_STRIP_RE = re.compile(r"<[^>]+>")

def _strip_tags(s: str) -> str:
    return TAG_STRIP_RE.sub("", s or "").strip()

def _best_href(cell_html: str) -> str | None:
    hrefs = HREF_RE.findall(cell_html or "")
    if not hrefs:
        return None
    # prefer googleusercontent
    for h in hrefs:
        if "googleusercontent.com" in h:
            return h
    return hrefs[0]

def extract_images_from_html(html: str) -> dict[str, str]:
    out: dict[str, str] = {}

    rows = TR_RE.findall(html or "")
    if not rows:
        return out

    # skip header row
    for tr in rows[1:]:
        cells = TD_RE.findall(tr)
        if len(cells) < 2:
            continue

        # Sometimes there is an extra first column (like row number). So try first 3 cells for image,
        # and next cells for name.
        # We'll look for the first cell that contains a useful href, and the first non-empty text cell as name.
        href = None
        for idx in range(min(3, len(cells))):
            href = _best_href(cells[idx])
            if href:
                break

        name = None
        for idx in range(min(6, len(cells))):
            t = _strip_tags(cells[idx])
            if t and t.lower() not in ("image",):
                name = t
                break

        if not name or not href:
            continue

        if href.startswith("http"):
            out[name.strip().lower()] = href

    return out