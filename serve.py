"""Development server for web/. Not a production one.

`python3 -m http.server` served this project until 2026-10-12, and it has one
behavior that cost a debugging cycle: it sends `Last-Modified` and no
`Cache-Control`, so a browser is free to apply heuristic freshness and serve
an edited page out of its own disk cache. A shipped change to `index.html`
then does not appear, which looks exactly like a bug in the change rather
than a bug in the reload. Every response here carries `no-store`.

What it still does not do is compress, which is deliberate: the payload
budgets in `config.wire_kb()` and the figures in `README.md` are gzipped
because that is what a CDN serves, so the map feels slower here than in
production and the wire sizes are measured rather than observed.
"""

import os
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import config as C


class NoStoreHandler(SimpleHTTPRequestHandler):
    """Serves web/ with caching turned off."""

    # Python's table maps .js to text/javascript already; .geojson is not in
    # it, and a wrong type on the data files would be a confusing way to find
    # that out.
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".geojson": "application/geo+json",
        ".json": "application/json",
    }

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    def log_message(self, fmt, *args):
        # One line per request is useful; the default also prints the date on
        # every line, which pushes the path off a narrow terminal.
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    handler = partial(NoStoreHandler, directory=str(C.WEB))
    with ThreadingHTTPServer(("", port), handler) as httpd:
        print(f"http://localhost:{port}/   (serving {C.WEB}, no-store)")
        print("Also /stats.html (figures) and /about.html (methodology)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print()


if __name__ == "__main__":
    main()
