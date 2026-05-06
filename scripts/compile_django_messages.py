from __future__ import annotations

import ast
import struct
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
LOCALE_DIR = ROOT_DIR / "backend" / "locale"


def parse_po_file(path: Path) -> dict[str, str]:
    messages: dict[str, str] = {}
    section: str | None = None
    msgid: str | None = None
    msgstr: str | None = None

    def flush() -> None:
        nonlocal msgid, msgstr
        if msgid is not None and msgstr is not None:
            messages[msgid] = msgstr
        msgid = None
        msgstr = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("msgid "):
            flush()
            section = "msgid"
            msgid = _parse_po_string(line[6:].strip())
            continue

        if line.startswith("msgstr "):
            section = "msgstr"
            msgstr = _parse_po_string(line[7:].strip())
            continue

        if line.startswith('"') and section:
            if section == "msgid":
                msgid = (msgid or "") + _parse_po_string(line)
            elif section == "msgstr":
                msgstr = (msgstr or "") + _parse_po_string(line)

    flush()
    return messages


def _parse_po_string(value: str) -> str:
    return ast.literal_eval(value)


def write_mo_file(messages: dict[str, str], path: Path) -> None:
    encoded_messages = sorted(
        (msgid.encode("utf-8"), msgstr.encode("utf-8"))
        for msgid, msgstr in messages.items()
    )
    count = len(encoded_messages)
    master_index_offset = 7 * 4
    translation_index_offset = master_index_offset + count * 8
    string_offset = translation_index_offset + count * 8

    ids = b"\0".join(msgid for msgid, _msgstr in encoded_messages)
    strings = b"\0".join(msgstr for _msgid, msgstr in encoded_messages)

    output = [
        struct.pack(
            "<Iiiiiii",
            0x950412DE,
            0,
            count,
            master_index_offset,
            translation_index_offset,
            0,
            0,
        )
    ]

    current_offset = string_offset
    for msgid, _msgstr in encoded_messages:
        output.append(struct.pack("<ii", len(msgid), current_offset))
        current_offset += len(msgid) + 1

    current_offset = string_offset + len(ids) + 1
    for _msgid, msgstr in encoded_messages:
        output.append(struct.pack("<ii", len(msgstr), current_offset))
        current_offset += len(msgstr) + 1

    output.append(ids)
    output.append(b"\0")
    output.append(strings)
    output.append(b"\0")

    path.write_bytes(b"".join(output))


def main() -> None:
    for po_file in LOCALE_DIR.glob("*/LC_MESSAGES/django.po"):
        mo_file = po_file.with_suffix(".mo")
        write_mo_file(parse_po_file(po_file), mo_file)
        print(f"Compiled {po_file.relative_to(ROOT_DIR)} -> {mo_file.relative_to(ROOT_DIR)}")


if __name__ == "__main__":
    main()
