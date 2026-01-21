import re
from html.parser import HTMLParser

IMG_RE = re.compile(r'<img[^>]+src="([^"]+)"', re.IGNORECASE)

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
            # reconstruct minimal html for image detection
            attr_str = " ".join(f'{k}="{v}"' for k, v in attrs)
            self.current_cell_html.append(f"<{tag} {attr_str}>")

    def handle_endtag(self, tag):
        if self.in_td:
            self.current_cell_html.append(f"</{tag}>")
        if tag in ("td", "th") and self.in_td:
            self.in_td = False
            cell_html = "".join(self.current_cell_html)
            # Also store plain text-ish version
            self.current_row.append(cell_html)
        if tag == "tr" and self.in_tr:
            self.in_tr = False
            if self.current_row:
                self.rows.append(self.current_row)

    def handle_data(self, data):
        if self.in_td:
            self.current_cell_html.append(data)

def extract_images_from_html(html: str) -> dict[str, str]:
    p = _TableParser()
    p.feed(html)

    # rows[0] likely header. We'll scan all rows and use col B as name.
    out = {}
    for row in p.rows[1:]:
        if len(row) < 2:
            continue
        col_a = row[0]
        col_b = re.sub(r"<[^>]+>", "", row[1]).strip()  # strip tags for name
        if not col_b:
            continue
        m = IMG_RE.search(col_a)
        if m:
            out[col_b.lower()] = m.group(1)
    return out