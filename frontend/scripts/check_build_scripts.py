"""Reject executable inline script tags in the generated HTML."""

import argparse
from html.parser import HTMLParser
from pathlib import Path
import sys


# Browsers also recognize these legacy JavaScript MIME types.
JAVASCRIPT_TYPES = {
    "application/ecmascript", "application/javascript",
    "application/x-ecmascript", "application/x-javascript",
    "text/ecmascript", "text/javascript", "text/jscript", "text/livescript",
    "text/x-ecmascript", "text/x-javascript",
    *(f"text/javascript1.{minor}" for minor in range(6)),
}


class ScriptScanner(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inline_lines = []

    def handle_starttag(self, tag, attrs):
        if tag != "script":
            return
        attributes = {}
        for name, value in attrs:
            attributes.setdefault(name, value or "")
        if attributes.get("src"):
            return
        if "type" in attributes:
            kind = attributes["type"].strip().lower()
        elif attributes.get("language"):
            kind = "text/" + attributes["language"].lower()
        else:
            kind = ""
        if kind in {"", "module"} or kind in JAVASCRIPT_TYPES:
            self.inline_lines.append(self.getpos()[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    directory = parser.parse_args().directory
    pages = sorted(directory.rglob("*.html"))
    if not pages:
        print(f"{directory}: no build HTML found", file=sys.stderr)
        return 1
    failed = False
    for page in pages:
        scanner = ScriptScanner()
        scanner.feed(page.read_text(encoding="utf-8"))
        scanner.close()
        for line in scanner.inline_lines:
            print(f"{page}:{line}: executable inline script", file=sys.stderr)
            failed = True
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
