#!/usr/bin/env python3
"""Replace blocked CyberFoil icon-pack entries with a transparent WebP."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import struct
from pathlib import Path


HEADER_SIZE = 32
HEADER_STRUCT = struct.Struct("<8sIIIIQ")
ENTRY_STRUCT = struct.Struct("<QQI8sI")
PACK_MAGIC = b"CFICONP1"
TRANSPARENT_WEBP = base64.b64decode(
    "UklGRhoAAABXRUJQVlA4TA0AAAAvAAAAEAcQERGIiP4HAA=="
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Source icons.pack")
    parser.add_argument("output", type=Path, help="Sanitized icons.pack")
    parser.add_argument("blocklist", type=Path, help="Blocked title-ID JSON")
    return parser.parse_args()


def load_blocklist(path: Path) -> set[int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = payload.get("titles", [])
    blocked = {int(str(item["id"]), 16) for item in values}
    if not blocked:
        raise ValueError("The blocklist is empty.")
    return blocked


def copy_exact(source, target, size: int) -> None:
    remaining = size
    while remaining:
        chunk = source.read(min(1024 * 1024, remaining))
        if not chunk:
            raise EOFError(f"Source pack ended with {remaining} bytes left to copy.")
        target.write(chunk)
        remaining -= len(chunk)


def sanitize(input_path: Path, output_path: Path, blocked: set[int]) -> tuple[int, int]:
    if input_path.resolve() == output_path.resolve():
        raise ValueError("Input and output paths must be different.")

    with input_path.open("rb") as source:
        header = source.read(HEADER_SIZE)
        if len(header) != HEADER_SIZE:
            raise ValueError("Icon pack header is truncated.")
        magic, version, entry_size, entry_count, flags, data_offset = HEADER_STRUCT.unpack(header)
        if magic != PACK_MAGIC or version != 1 or entry_size != ENTRY_STRUCT.size:
            raise ValueError("Unsupported CyberFoil icon-pack format.")
        expected_data_offset = HEADER_SIZE + entry_count * entry_size
        if data_offset != expected_data_offset:
            raise ValueError("Unexpected icon data offset.")

        table = bytearray(source.read(entry_count * entry_size))
        if len(table) != entry_count * entry_size:
            raise ValueError("Icon pack table is truncated.")

        records: list[tuple[int, int, int, bytes, int]] = []
        found: set[int] = set()
        for index in range(entry_count):
            position = index * entry_size
            title_id, offset, size, extension, reserved = ENTRY_STRUCT.unpack_from(table, position)
            records.append((title_id, offset, size, extension, reserved))
            if title_id in blocked:
                extension_name = extension.split(b"\0", 1)[0].lower()
                if extension_name != b"webp":
                    raise ValueError(f"Blocked title {title_id:016X} is not stored as WebP.")
                found.add(title_id)

        missing = blocked - found
        if missing:
            missing_text = ", ".join(f"{title_id:016X}" for title_id in sorted(missing))
            raise ValueError(f"Blocked title IDs missing from pack: {missing_text}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("wb") as target:
            target.write(header)
            target.write(table)
            next_offset = 0
            replaced = 0

            for index, (title_id, old_offset, old_size, extension, reserved) in enumerate(records):
                position = index * entry_size
                if title_id in blocked:
                    payload = TRANSPARENT_WEBP
                    replaced += 1
                    target.write(payload)
                    new_size = len(payload)
                else:
                    source.seek(data_offset + old_offset)
                    copy_exact(source, target, old_size)
                    new_size = old_size

                ENTRY_STRUCT.pack_into(
                    table,
                    position,
                    title_id,
                    next_offset,
                    new_size,
                    extension,
                    reserved,
                )
                next_offset += new_size

            target.seek(HEADER_SIZE)
            target.write(table)

    return replaced, flags


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    blocked = load_blocklist(args.blocklist)
    replaced, flags = sanitize(args.input, args.output, blocked)
    print(f"Replaced entries: {replaced}")
    print(f"Pack flags: {flags}")
    print(f"Output size: {args.output.stat().st_size}")
    print(f"SHA-256: {sha256(args.output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
