# Catalogue artwork repair — 13 October 2026

- Rebuilt the 11,939 previously replaced covers to fill square tiles: no added
  black bars, and no cropped-off artwork. Rectangular covers are stretched.
- Added system-scoped catalogue associations, including explicit Ports aliases
  and manually reviewed original-game editions where appropriate.
- Added 8,446 missing catalogue covers from platform-specific box-art scans and
  matched IGDB artwork, plus 10,934 system-scoped associations. Ports now has
  verified artwork for 78 of 82 catalogue entries. Counts and unresolved entries
  are in `catalogue-artwork-report.json`.
- Existing title IDs, descriptions and original unmatched images are retained.
  New `07` artwork IDs are metadata-only and must never be used for installation.
- No server-supplied images, generic image-search results, or AI substitutes.
  Entries without a verified match still use the app's system placeholder.

## Previous enrichment pass

This is a checked, incremental improvement to the existing 20,288-title database,
not a completed manual review of every game.

- Replaced 11,939 covers using exact title plus unambiguous platform matching:
  2,611 console-specific box-art scans and 9,328 IGDB catalogue covers.
- Covers retain their full aspect ratio in 256 x 256 WebP tiles; no cropped-off
  box edges or generic web-image searches.
- Filled 5,179 description fields: 4,640 from existing pack synopses and 539 new
  factual overviews from structured metadata. Preserved other existing prose.
- Filled 402 missing/generic publisher fields.
- Corrected two manually reviewed metadata errors: a Carnage 2 NES hack confused
  with a Borderlands expansion, and Busytown text describing the wrong version.
- All title IDs retained. Both binary packs, manifests, loose replacement images,
  provenance and a review report are included in the repository.

## What remains

8,349 existing images were left unchanged. Many entries are hacks, prototypes,
homebrew or have ambiguous/corrupted names. 4,756 titles still lack a description.
Nine preferred box-art files could not be decoded and had no usable exact-match
fallback; their existing icons were preserved. These unresolved items have not
been passed off as verified official artwork. No AI-generated covers are included.

See `enrichment-report.json`, `metadata-review-needed.json`,
`manual-metadata-corrections.json` and `metadata-provenance.json` for details.

Artwork sources: [Libretro box-art scans](https://github.com/libretro-thumbnails/libretro-thumbnails)
and [IGDB](https://www.igdb.com/). Original rights remain with their respective
owners; catalogue availability does not authenticate every regional edition.

The prior release is preserved for rollback. Magic Monkei builds with the live
pack updater can discover this release without an app rebuild. Existing offline
caches remain usable until the app next connects and refreshes its indexes.
