from __future__ import annotations

import _string
import argparse
import gettext
import io
import re
import string
import struct
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
LOCALE_DIR = ROOT_DIR / "backend" / "locale"


class PoError(ValueError):
    """A catalog error with a source location."""

    def __init__(self, filename: str, line: int, message: str) -> None:
        super().__init__(f"{filename}:{line}: {message}")


_PRINTF = re.compile(
    r"%(?:\((?P<name>[^()]+)\)|(?P<position>[1-9][0-9]*)\$)?"
    r"[-+#0 ]*(?P<width>\*(?:[1-9][0-9]*\$)?|[0-9]+)?"
    r"(?:\.(?P<precision>\*(?:[1-9][0-9]*\$)?|[0-9]*))?"
    r"(?P<length>[hlL]?)(?P<type>[diouxXeEfFgGcrsa])"
)


def placeholder_signature(
    text: str, *, strict_printf: bool = False,
) -> tuple[tuple[str, ...], ...]:
    """Return sorted placeholder tokens, preserving multiplicity and argument types.

    Brace tokens contain field, conversion and exact format spec. Printf tokens
    contain argument name/index and conversion type (including star arguments).
    Named fields may be reordered; implicit positional arguments retain order.
    Escaped braces and percent signs do not contribute tokens. By default, bare
    percent signs and percent followed by whitespace are prose, not formatting;
    supported printf conversions are recognized otherwise. Malformed named or
    numbered printf forms always raise ValueError. Set strict_printf=True for
    explicitly formatted text (including the ambiguous printf space flag).
    This helper has no catalog/source-location policy.
    """
    tokens: list[tuple[str, ...]] = []
    automatic = 0
    brace_numbering: set[str] = set()

    def braces(value: str, depth: int = 0) -> None:
        nonlocal automatic
        if depth > 2:
            raise ValueError("brace format specification is nested too deeply")
        for _, field, spec, conversion in string.Formatter().parse(value):
            if field is None:
                continue
            if conversion not in (None, "s", "r", "a"):
                raise ValueError("unsupported brace conversion")
            if field == "" or field.startswith((".", "[")):
                brace_numbering.add("automatic")
                field = str(automatic) + field
                automatic += 1
            elif field.split(".", 1)[0].split("[", 1)[0].isdigit():
                brace_numbering.add("manual")
            # Validate attribute/index syntax without resolving application data.
            _, accessors = _string.formatter_field_name_split(field)
            list(accessors)
            tokens.append(("brace", field, conversion or "", spec))
            braces(spec, depth + 1)

    braces(text)
    if len(brace_numbering) > 1:
        raise ValueError("cannot mix automatic and manual brace numbering")

    index = 0
    argument = 0
    while index < len(text):
        if text[index] != "%":
            index += 1
            continue
        if text[index:index + 2] == "%%":
            index += 2
            continue
        if not strict_printf and (
            index + 1 == len(text) or text[index + 1].isspace()
        ):
            index += 1
            continue
        match = _PRINTF.match(text, index)
        if match is None:
            explicit = text[index + 1:index + 2] == "(" or re.match(
                r"[0-9]+\$", text[index + 1:]
            )
            if strict_printf or explicit:
                raise ValueError("invalid printf placeholder (use %% for a literal percent)")
            index += 1
            continue
        name, position = match.group("name", "position")
        for size in (match.group("width"), match.group("precision")):
            if size and size.startswith("*"):
                if name:
                    raise ValueError("named printf placeholders cannot use star widths")
                if size.endswith("$"):
                    key = str(int(size[1:-1]) - 1)
                else:
                    key = str(argument)
                    argument += 1
                tokens.append(("printf-position", key, "int"))
        if name is not None:
            kind, key = "printf-name", name
        else:
            kind = "printf-position"
            key = str(int(position) - 1) if position else str(argument)
            if position is None:
                argument += 1
        tokens.append((kind, key, match.group("length") + match.group("type")))
        index = match.end()
    printf_kinds = {token[0] for token in tokens if token[0].startswith("printf-")}
    if len(printf_kinds) > 1:
        raise ValueError("cannot mix named and positional printf placeholders")
    return tuple(sorted(tokens))


def _parse_po_string(value: str) -> bytes:
    """Decode one GNU PO quoted string, not a Python string literal."""
    if len(value) < 2 or value[0] != '"' or value[-1] != '"':
        raise ValueError("expected a double-quoted PO string")
    escapes = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r",
               "t": "\t", "v": "\v", "\\": "\\", '"': '"', "'": "'", "?": "?"}
    output = bytearray()
    index = 1
    end = len(value) - 1
    while index < end:
        char = value[index]
        index += 1
        if char == '"' or ord(char) < 32:
            raise ValueError("unescaped quote or control character in PO string")
        if char != "\\":
            output.extend(char.encode("utf-8"))
            continue
        if index >= end:
            raise ValueError("unfinished PO escape")
        escape = value[index]
        index += 1
        if escape in escapes:
            output.extend(escapes[escape].encode("ascii"))
        elif escape in "01234567" or escape == "x":
            start = index - 1 if escape != "x" else index
            digits = "01234567" if escape != "x" else "0123456789abcdefABCDEF"
            limit = min(start + 3, end) if escape != "x" else end
            while index < limit and value[index] in digits:
                index += 1
            number = value[start:index]
            if not number:
                raise ValueError("empty hexadecimal PO escape")
            code = int(number, 8 if escape != "x" else 16)
            if code > 255:
                raise ValueError("PO numeric escape exceeds one byte")
            output.append(code)
        else:
            raise ValueError(f"unsupported PO escape \\{escape}")
    return bytes(output)


def parse_po_text(text: str, filename: str = "<string>") -> dict[str, str]:
    """Parse the supported singular PO grammar, failing closed on other syntax."""
    messages: dict[str, str] = {}
    section: str | None = None
    msgid: bytes | None = None
    msgstr: bytes | None = None
    entry_line = 1
    translation_line = 1
    pending_flags: set[str] = set()
    entry_flags: set[str] = set()

    def fail(line: int, message: str) -> None:
        raise PoError(filename, line, message)

    def flush() -> None:
        nonlocal msgid, msgstr, section
        if msgid is None:
            return
        if msgstr is None:
            fail(entry_line, "msgid has no msgstr")
        try:
            source = msgid.decode("utf-8")
        except UnicodeDecodeError:
            fail(entry_line, "msgid escapes do not form valid UTF-8")
        try:
            translated = msgstr.decode("utf-8")
        except UnicodeDecodeError:
            fail(translation_line, "msgstr escapes do not form valid UTF-8")
        if source in messages:
            fail(entry_line, f"duplicate msgid {source!r}")
        if source == "" and messages:
            fail(entry_line, "catalog header must be the first entry")
        for value, line in ((source, entry_line), (translated, translation_line)):
            if "\0" in value:
                fail(line, "NUL is not allowed")
            if source != "":
                if not value.strip():
                    fail(line, "empty or whitespace-only user text")
                if value != value.strip():
                    fail(line, "leading or trailing whitespace in user text")
                if "·" in value:
                    fail(line, "middle dot is not allowed in user text")
        if source != "":
            strict_printf = bool(entry_flags & {"python-format", "c-format"})
            try:
                source_signature = placeholder_signature(source, strict_printf=strict_printf)
            except ValueError as exc:
                fail(entry_line, str(exc))
            try:
                translated_signature = placeholder_signature(translated, strict_printf=strict_printf)
            except ValueError as exc:
                fail(translation_line, str(exc))
            if source_signature != translated_signature:
                fail(translation_line, "translation placeholders do not match msgid")
        messages[source] = translated
        msgid = msgstr = section = None

    # Only LF/CRLF are physical line separators; escaped controls belong to text.
    for line_number, raw_line in enumerate(text.split("\n"), 1):
        line = raw_line.removesuffix("\r").strip(" \t")
        if "\0" in raw_line:
            fail(line_number, "NUL is not allowed")
        if not line:
            flush()
            continue
        if line.startswith("#"):
            if line.startswith(("#~", "#|")):
                fail(line_number, "obsolete/previous entries are not supported")
            flags = set(line[2:].replace(",", " ").split()) if line.startswith("#,") else set()
            if "fuzzy" in flags:
                fail(line_number, "fuzzy entries are not supported")
            if section is not None:
                flush()
            pending_flags.update(flags)
            continue
        directive = re.fullmatch(r'(msgid|msgstr)[ \t]+(.*)', line)
        if directive:
            kind, value = directive.groups()
            if kind == "msgid":
                flush()
                entry_line = line_number
                entry_flags, pending_flags = pending_flags, set()
            elif msgid is None or section != "msgid":
                fail(line_number, "msgstr must follow exactly one msgid")
            else:
                translation_line = line_number
            section = kind
        elif line.startswith('"'):
            if section is None:
                fail(line_number, "orphan string continuation")
            value = line
        else:
            fail(line_number, "unsupported or unrecognized PO syntax")
        try:
            decoded = _parse_po_string(value)
        except ValueError as exc:
            fail(line_number, str(exc))
        if section == "msgid":
            msgid = (msgid or b"") + decoded
        else:
            msgstr = (msgstr or b"") + decoded
    flush()
    return messages


def _header_fields(header: str) -> dict[str, str]:
    fields = {}
    for line in header.splitlines():
        if ":" not in line:
            raise ValueError("malformed catalog header field")
        name, value = line.split(":", 1)
        name = name.strip().lower()
        if not name or name in fields:
            raise ValueError("empty or duplicate catalog header field")
        fields[name] = value.strip()
    if not re.fullmatch(r"[A-Za-z]{2,3}(?:[_-][A-Za-z0-9]+)*", fields.get("language", "")):
        raise ValueError("catalog header requires a valid Language")
    if not re.fullmatch(r"text/plain\s*;\s*charset=utf-8", fields.get("content-type", ""), re.I):
        raise ValueError("catalog header requires Content-Type: text/plain; charset=UTF-8")
    return fields


def parse_po_file(path: Path) -> dict[str, str]:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        line = exc.object[:exc.start].count(b"\n") + 1
        raise PoError(str(path), line, "catalog is not valid UTF-8") from exc
    messages = parse_po_text(text, filename=str(path))
    try:
        if "" not in messages:
            raise ValueError("catalog is missing its header")
        fields = _header_fields(messages[""])
        if len(messages) == 1:
            raise ValueError("catalog has no translated entries")
        gettext.GNUTranslations(io.BytesIO(compile_messages(messages)))
    except (ValueError, OSError, LookupError) as exc:
        raise PoError(str(path), 1, str(exc)) from exc
    if path.name == "django.po" and path.parent.name == "LC_MESSAGES":
        language = fields["language"]
        if language.lower().replace("-", "_") != path.parent.parent.name.lower().replace("-", "_"):
            raise PoError(str(path), 1, "header Language does not match catalog directory")
    return messages


def compile_messages(messages: dict[str, str]) -> bytes:
    """Serialize parsed messages using the existing deterministic MO layout."""
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
    return b"".join(output)


def write_mo_file(messages: dict[str, str], path: Path) -> None:
    path.write_bytes(compile_messages(messages))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and compile Django PO catalogs.")
    parser.add_argument("--check", action="store_true", help="check committed MO bytes without writing")
    args = parser.parse_args(argv)
    catalogs: list[tuple[Path, bytes]] = []
    errors: list[str] = []
    po_files = sorted(LOCALE_DIR.glob("*/LC_MESSAGES/django.po"))
    if not po_files:
        print(f"{LOCALE_DIR}: no Django PO catalogs found", file=sys.stderr)
        return 1
    for po_file in po_files:
        try:
            catalogs.append((po_file, compile_messages(parse_po_file(po_file))))
        except (ValueError, OSError) as exc:
            errors.append(str(exc))
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    for po_file, output in catalogs:
        mo_file = po_file.with_suffix(".mo")
        if args.check:
            try:
                if mo_file.read_bytes() != output:
                    errors.append(f"{mo_file}: generated MO bytes differ")
            except OSError as exc:
                errors.append(f"{mo_file}: cannot read MO: {exc}")
        else:
            try:
                mo_file.write_bytes(output)
            except OSError as exc:
                print(f"{mo_file}: {exc}", file=sys.stderr)
                return 1
            print(f"Compiled {po_file} -> {mo_file}")
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
