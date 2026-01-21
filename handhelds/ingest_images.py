import re
from html.parser import HTMLParser

# Image cells in gviz HTML are links like: <a href="https://lh7-rt.googleusercontent.com/...">Image</a>
HREF_RE = re.compile(r'href="([^"]+)"', re.IGNORECASE)

class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_td = False
        self.current_cell_html = []
        self.current_row = []
        self.rows = []
        self.in_tr = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.in_tr = True
            self.current_row = []
        if tag in ("td", "th") and self.in_tr:
            self.in_td = True
            self.current_cell_html = []
        if self.in_td:
            attr_str = " ".join(f'{k}="{v}"' for k, v in attrs)
            self.current_cell_html.append(f"<{tag} {attr_str}>")

    def handle_endtag(self, tag):
        if self.in_td:
            self.current_cell_html.append(f"</{tag}>")
        if tag in ("td", "th") and self.in_td:
            self.in_td = False
            self.current_row.append("".join(self.current_cell_html))
        if tag == "tr" and self.in_tr:
            self.in_tr = False
            if self.current_row:
                self.rows.append(self.current_row)

    def handle_data(self, data):
        if self.in_td:
            self.current_cell_html.append(data)


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def _pick_best_href(cell_html: str) -> str | None:
    """
    Prefer the googleusercontent thumbnail links from Sheets.
    Example: https://lh7-rt.googleusercontent.com/sheetsz/...=w120-h64?key=...
    """
    hrefs = HREF_RE.findall(cell_html or "")
    if not hrefs:
        return None

    # Prefer googleusercontent thumbnails
    for h in hrefs:
        if "googleusercontent.com" in h:
            return h

    # Otherwise fall back to first href
    return hrefs[0]


def extract_images_from_html(html: str) -> dict[str, str]:
    p = _TableParser()
    p.feed(html)

    out: dict[str, str] = {}

    # rows[0] is header. col A = image link, col B = handheld name.
    for row in p.rows[1:]:
        if len(row) < 2:
            continue

        col_a = row[0]
        name = _strip_tags(row[1])
        if not name:
            continue

        href = _pick_best_href(col_a)
        if href and href.startswith("http"):
            out[name.lower()] = href

    return out