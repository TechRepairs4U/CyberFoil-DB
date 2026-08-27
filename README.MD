# CyberFoil Offline Database

This repository publishes the title and icon packs consumed by Magic Monkei.

Explicitly adult artwork is replaced with a transparent WebP before release.
The reviewed title IDs live in `blocked_icon_title_ids.json`; rebuild an icon
pack with:

```text
python tools/sanitize_icon_pack.py icons.pack icons.pack.sanitized blocked_icon_title_ids.json
```

After rebuilding, update both manifest files with the new pack size, SHA-256,
database version, and generation time.
