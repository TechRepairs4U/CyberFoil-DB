# CyberFoil / Magic Monkei metadata packs

Game artwork and metadata for the application's on-demand, SD-cached database.
The four files in the latest GitHub release are the update set:
`titles.pack`, `icons.pack`, `manifest.json`, and `offline_db_manifest.json`.
Source JSON and loose images are also maintained on the main branch.

## Artwork and metadata enrichment

This is an incremental enrichment pass, **not a claim that every entry is now
correct or complete**. See `enrichment-report.json` for exact counts and
`metadata-review-needed.json` for titles whose game identity or platform still
needs review. Existing unverified artwork is retained in this pass; absence from
the review list does not constitute a manual review of every pixel or edition.

Replacement artwork is selected using a normalized exact title and an
unambiguous platform match. Sequels, numbered editions, hacks, beta labels and
meaningful title suffixes are not fuzzy-matched to another game. Console-specific
box-art scans are preferred, with catalogue covers as a fallback. Full covers
are stretched to fill a 256 x 256 WebP tile without cropping their edges or
adding black padding. This is the app owner's selected presentation policy.

`catalogue-artwork.json` associates each shop system and catalogue identifier
with a pack artwork ID. It prevents identical names on different consoles from
selecting each other's covers. It is versioned and hash-checked alongside the
packs. `07`-prefixed additions are artwork/metadata keys, not install title IDs.
Ports can use verified artwork for their original game, not a claimed official
Switch release; such manually selected editions are recorded in provenance.

Sources:

- [Libretro thumbnails](https://github.com/libretro-thumbnails/libretro-thumbnails):
  `Named_Boxarts` scans, with original repository path and Git blob hash recorded.
- [IGDB](https://www.igdb.com/): exact-title/platform catalogue covers and structured
  facts. [API documentation and usage terms](https://api-docs.igdb.com/).

`metadata-provenance.json` records the public source URL, match method, source
hash and packed image hash for every replaced image. A catalogue's cover is not
necessarily the box from a particular region or edition. No image is labelled as
publisher-authenticated merely because it appeared in a catalogue.

Existing descriptions are preserved except for the individually documented,
reviewed corrections in `manual-metadata-corrections.json`. Existing pack synopses are copied into
empty description fields. New short descriptions are original factual overviews
from structured genre, developer, platform, release-year and play-mode data;
missing plot details are not invented. Unresolved records are left for review.
No generative-AI cover artwork is included in this pass.

Artwork remains the property of its respective rights holders. This repository
does not grant ownership of game artwork or change the source services' terms.
No Google tokens, IGDB credentials, private ROM URLs, or server artwork URLs are
included in the published packs or provenance.

## Reproducible maintenance

Requires Python 3, Pillow, authenticated GitHub CLI, and Git LFS. Network cache
is deliberately stored in a sibling `db-enrichment-cache` directory, not Git.
Only run enrichment in a dedicated checkout. Preserve the old packs/release.

1. Run `python tools/enrich_database.py catalogues` and `audit`.
2. Run `python tools/enrich_database.py igdb --settings /path/to/local/settings.json`.
   The local settings contain `IgdbClientId` and `IgdbClientSecret`; never commit
   that file or paste credentials into command arguments.
3. Run the `boxart-indexes`, `plan`, `boxart-plan`, `download-art`, and `metadata`
   actions. Review their cached plans and errors before applying changes.
4. Run `build --original-icons /path/to/previous/icons.pack`.
   This regenerates the source files, both binary packs and both manifests.
   Then run `python tools/repair_catalogue_artwork.py plan`, `download`, and
   `apply` in that order to retain/add the system-scoped catalogue associations.
   Review the source plan before downloading. The optional `ports_metadata`
   action refreshes the separately cached Ports candidates. Run
   `python tools/enrich_database.py validate` and `python tools/test_cover_layout.py`.
5. Run `python tools/test_enrichment.py`. Review all changes, provenance,
   unmatched entries, and the obsolete-extension list in the local cache.
6. Commit the source changes and LFS packs. Upload both packs, both manifests,
   and `catalogue-artwork.json` to a
   new draft release, verify their hashes, then publish it as latest. Never
   overwrite an old release or publish only one half of the pack pair.

The cache is a snapshot. Use a fresh sibling cache for a later catalogue update;
do not mix partial pages from different source generations. Credentials are used
only in memory against the provider's official authentication/API endpoints.
