"""Conservative, resumable catalogue enrichment. Network cache stays outside Git.

Only unique normalized title + confirmed platform matches may be applied.
No edit-distance, substring, franchise or first-search-result matching.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT.parent / 'db-enrichment-cache'
GIT = 'C:/Program Files/Git/cmd/git.exe' if os.name == 'nt' else 'git'
PLATFORMS = {
    'switch': (130, 'Nintendo Switch'), 'wii': (5, 'Wii'), 'gc': (21, 'GameCube'),
    '3ds': (37, 'Nintendo 3DS'), 'nds': (20, 'Nintendo DS'), 'n64': (4, 'Nintendo 64'),
    'snes': (19, 'Super Nintendo'), 'nes': (18, 'Nintendo NES'),
    'gba': (24, 'Game Boy Advance'), 'gbc': (22, 'Game Boy Color'), 'gb': (33, 'Game Boy'),
    'sms': (64, 'Master System'), 'sg': (29, 'Mega Drive'), 'sgg': (35, 'Game Gear'),
    'scd': (78, 'Sega CD'), '32x': (30, 'Sega 32X'), 'psx': (7, 'PlayStation'),
    'psp': (38, 'PSP'), 'pspm': (38, 'PSP Minis'), 'a26': (59, 'Atari 2600'),
    'a78': (60, 'Atari 7800'), 'jag': (62, 'Atari Jaguar'), 'tg16': (86, 'TurboGrafx-16'),
    'c64': (15, 'Commodore 64'), 'wiiu': (41, 'Wii U'), 'ps2': (8, 'PlayStation 2'),
    'ports': (None, 'Ports'),
}


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    temp.replace(path)


def get(url, data=None, headers=None):
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, data=data, headers=headers or {'User-Agent': 'CyberFoil-DB-metadata/1.0'})
            with urllib.request.urlopen(req, timeout=40) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 3:
                # Never expose authenticated request headers or token responses.
                raise RuntimeError(f'HTTP {exc.code} at {urllib.parse.urlsplit(url).hostname}') from None
            time.sleep(min(30, 2 ** (attempt + 1)))
        except (TimeoutError, urllib.error.URLError):
            if attempt == 3:
                raise RuntimeError(f'Network failure at {urllib.parse.urlsplit(url).hostname}') from None
            time.sleep(2 ** attempt)


REGION_TAG = re.compile(r'\s*\((?:USA|Europe|World|Japan|Japan, USA|USA, Europe|U|E|J|JU|UE|JUE|En(?:,[A-Za-z]{2})*|Rev [\dA-Z.]+|Disc \d+|Disk \d+|v[\d.]+|SGB Enhanced|GB Compatible)\)', re.I)


def key(name):
    name = unicodedata.normalize('NFKC', name.replace('™', '').replace('®', '')).casefold()
    name = REGION_TAG.sub('', name).strip()
    # Some shop labels flatten (USA) to a bare suffix. Never strip "World"
    # by itself: Super Mario World is a different game from Super Mario.
    name = re.sub(r'\s+(?:usa|europe|japan)(?:\s+(?:usa|europe|japan))*$', '', name)
    article = re.match(r'^(.*),\s*(the|a|an)$', name)
    if article:
        name = article[2] + ' ' + article[1]
    return ''.join(c for c in name.replace('&', 'and') if c.isalnum() or c in '+#')


THUMB_REPOS = {
    'wii': 'Nintendo - Wii', 'gc': 'Nintendo - GameCube', '3ds': 'Nintendo - Nintendo 3DS',
    'nds': 'Nintendo - Nintendo DS', 'n64': 'Nintendo - Nintendo 64',
    'snes': 'Nintendo - Super Nintendo Entertainment System', 'nes': 'Nintendo - Nintendo Entertainment System',
    'gba': 'Nintendo - Game Boy Advance', 'gbc': 'Nintendo - Game Boy Color', 'gb': 'Nintendo - Game Boy',
    'sms': 'Sega - Master System - Mark III', 'sg': 'Sega - Mega Drive - Genesis',
    'sgg': 'Sega - Game Gear', 'scd': 'Sega - Mega-CD - Sega CD', '32x': 'Sega - 32X',
    'psx': 'Sony - PlayStation', 'psp': 'Sony - PlayStation Portable', 'pspm': 'Sony - PlayStation Portable',
    'a26': 'Atari - 2600', 'a78': 'Atari - 7800', 'jag': 'Atari - Jaguar',
    'tg16': 'NEC - PC Engine - TurboGrafx 16', 'c64': 'Commodore - 64',
    'ps2': 'Sony - PlayStation 2', 'wiiu': 'Nintendo - Wii U',
}


def github_json(endpoint):
    cli = 'gh' if os.name != 'nt' else 'C:/Program Files/GitHub CLI/gh.exe'
    for attempt in range(4):
        result = subprocess.run([cli, 'api', endpoint], capture_output=True)
        if result.returncode == 0: return json.loads(result.stdout)
        if attempt < 3: time.sleep(2 ** attempt)
    raise RuntimeError('GitHub API request failed: ' + endpoint)


def fetch_boxart_indexes():
    # Resolve repository names from upstream rather than inventing image URLs.
    modules = get('https://raw.githubusercontent.com/libretro-thumbnails/libretro-thumbnails/master/.gitmodules').decode()
    repos = dict(re.findall(r'path = ([^\n]+)\n\s*url = https://github.com/([^\n]+)\.git', modules))
    for platform, display in THUMB_REPOS.items():
        path = CACHE / 'boxart-index' / f'{platform}.json'
        if path.exists(): continue
        repo = repos[display]
        tree = github_json(f'repos/{repo}/git/trees/master?recursive=1')
        if tree.get('truncated'):
            root = github_json(f'repos/{repo}/git/trees/master')
            box = next(x for x in root['tree'] if x['path'] == 'Named_Boxarts')
            tree = github_json(f'repos/{repo}/git/trees/{box["sha"]}')
            if tree.get('truncated'): raise ValueError(f'Truncated box-art tree for {repo}')
            for x in tree['tree']: x['path'] = 'Named_Boxarts/' + x['path']
        rows = [{'name': Path(x['path']).stem, 'path': x['path'], 'sha': x['sha']} for x in tree['tree'] if x['path'].startswith('Named_Boxarts/') and x['path'].endswith('.png')]
        write_json(path, {'repo': repo, 'commit_tree': tree['sha'], 'rows': rows})
        print('Box-art index', platform, len(rows), flush=True)
        time.sleep(.3)


def boxart_plan():
    titles = read_json(ROOT / 'titles.json')
    mapped = mapping(titles)
    indexes = {}
    for path in (CACHE / 'boxart-index').glob('*.json'):
        raw = read_json(path)
        index = defaultdict(list)
        for row in raw['rows']:
            index[key(row['name'])].append(row)
        indexes[path.stem] = (raw, index)
    result = {}
    for tid, title in titles.items():
        platforms = mapped[tid]
        if len(platforms) != 1 or platforms[0] not in indexes: continue
        platform = platforms[0]
        raw, index = indexes[platform]
        candidates = index.get(key(title['name']), [])
        if not candidates: continue
        # Only region/revision variants of this exact game; choose English cover
        # unless the library explicitly requests a Japanese/European edition.
        target = title['name'].casefold()
        preferred = 'Japan' if '(j)' in target or '(japan)' in target else 'Europe' if '(e)' in target or '(europe)' in target else 'USA'
        ranked = sorted(candidates, key=lambda r: (preferred not in r['name'], 'World' not in r['name'], 'Europe' not in r['name'], r['name']))
        row = ranked[0]
        result[tid] = {'platform': platform, 'name': title['name'], 'source': 'libretro-boxart', 'repo': raw['repo'], 'file': row['path'], 'blob_sha': row['sha'], 'source_name': row['name'], 'url': 'https://raw.githubusercontent.com/' + raw['repo'] + '/master/' + urllib.parse.quote(row['path'])}
    write_json(CACHE / 'boxart-plan.json', result)
    print('Exact game + platform box-art matches:', len(result))
    print(dict(Counter(x['platform'] for x in result.values())))


def download_art():
    from PIL import Image
    plans = read_json(CACHE / 'plan.json')
    sources = read_json(CACHE / 'boxart-plan.json')
    for tid, item in plans.items():
        cover = item['game'].get('cover', {}).get('image_id')
        if tid not in sources and cover:
            sources[tid] = {'name': item['name'], 'platform': item['platform'], 'source': 'igdb-cover', 'game_id': item['game']['id'], 'game_url': item['game'].get('url'), 'url': f'https://images.igdb.com/igdb/image/upload/t_cover_big/{cover}.jpg'}
    def fetch(item):
        tid, source = item
        # Shared images can be cached once, but writing unique title files avoids
        # cross-worker partial writes. All downloads are validated before apply.
        path = CACHE / 'art' / f'{tid}.image'
        try:
            data = path.read_bytes() if path.exists() else get(source['url'])
            if len(data) > 16 * 1024 * 1024: raise ValueError('Artwork too large')
            if source.get('blob_sha'):
                blob = hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest()
                if blob != source['blob_sha']: raise ValueError('Source changed since index; refresh required')
            with Image.open(io.BytesIO(data)) as im:
                im.verify()
            with Image.open(io.BytesIO(data)) as im:
                if im.width < 32 or im.height < 32: raise ValueError('Image too small')
                size = list(im.size)
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            return tid, dict(source, sha256=hashlib.sha256(data).hexdigest(), dimensions=size), None
        except Exception as exc:
            # A corrupt/unsupported scan must not block an independently
            # matched catalogue cover for the exact same title/platform.
            cover = plans.get(tid, {}).get('game', {}).get('cover', {}).get('image_id')
            if source['source'] == 'libretro-boxart' and cover:
                game = plans[tid]['game']
                fallback = {'name': source['name'], 'platform': source['platform'], 'source':'igdb-cover', 'game_id':game['id'], 'game_url':game.get('url'), 'url':f'https://images.igdb.com/igdb/image/upload/t_cover_big/{cover}.jpg', 'fallback_reason':'Box-art scan could not be decoded by the build image library'}
                return fetch((tid, fallback))
            return tid, None, type(exc).__name__ + ': artwork validation/download failed'
    good, errors = {}, {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for i, (tid, source, error) in enumerate(pool.map(fetch, sources.items()), 1):
            if error: errors[tid] = error
            else: good[tid] = source
            if i % 100 == 0: print('Validated artwork', i, '/', len(sources), 'errors', len(errors), flush=True)
    write_json(CACHE / 'art-sources.json', good)
    write_json(CACHE / 'art-errors.json', errors)
    print('Artwork validated:', len(good), 'failed:', len(errors), flush=True)


def unpack_icons(path):
    data = Path(path).read_bytes()
    magic, version, stride, count, flags, start = struct.unpack_from('<8sIIIIQ', data)
    assert (magic, version, stride) == (b'CFICONP1', 1, 32)
    assert start == 32 + count * stride
    records = {}
    for i in range(count):
        tid, offset, length, ext, _ = struct.unpack_from('<QQI8sI', data, 32 + i * stride)
        assert length > 0 and start + offset + length <= len(data)
        name = f'{tid:016X}'
        assert name not in records
        records[name] = (ext.rstrip(b'\0').decode(), data[start+offset:start+offset+length])
    return records


def inspect_original(pack):
    from PIL import Image, ImageDraw, ImageFont
    records = unpack_icons(pack)
    titles = read_json(ROOT / 'titles.json')
    plans = read_json(CACHE / 'plan.json')
    duplicates = defaultdict(list)
    invalid = []
    for tid, (_, data) in records.items():
        duplicates[hashlib.sha256(data).hexdigest()].append(tid)
        try:
            with Image.open(io.BytesIO(data)) as im: im.verify()
        except Exception: invalid.append(tid)
    info = {'icons': len(records), 'invalid': invalid, 'duplicate_groups': sorted((v for v in duplicates.values() if len(v) > 1), key=len, reverse=True)}
    write_json(CACHE / 'original-icons-audit.json', info)
    print('All original images decoded; invalid:', len(invalid), 'duplicate groups:', len(info['duplicate_groups']))
    selected = [tid for tid in titles if tid.startswith('05') and tid not in plans][:24]
    # Contact sheet is diagnostic only; never used as artwork in the pack.
    sheet = Image.new('RGB', (1000, 6*220), '#1b1b1b')
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 13)
    for n, tid in enumerate(selected):
        im = Image.open(io.BytesIO(records[tid][1])).convert('RGB')
        im.thumbnail((180, 160))
        x,y = (n%4)*250, (n//4)*220
        sheet.paste(im,(x,y))
        draw.text((x,y+164),tid,font=font,fill='white')
        name = titles[tid]['name']
        draw.text((x,y+181),name[:32]+'\n'+name[32:64],font=font,fill='white')
    sheet.save(CACHE/'original-unresolved-preview.jpg')


def factual_description(item):
    """Original factual overview, not a bulk copy of third-party prose."""
    game = item['game']
    platform_id, platform_name = PLATFORMS[item['platform']]
    genres = [x['name'] for x in game.get('genres', []) if x['name'] != 'Indie']
    genres = genres[:3]
    developers = [x['company']['name'] for x in game.get('involved_companies', []) if x.get('developer')]
    modes = [x['name'] for x in game.get('game_modes', [])]
    years = sorted({r['y'] for r in game.get('release_dates', []) if r.get('platform') == platform_id and r.get('y')})
    parts = []
    if genres:
        genre_text=' / '.join(genres).lower()
        article='an' if genre_text[0] in 'aeiou' else 'a'
        parts.append(f"{item['name']} is {article} {genre_text} game for {platform_name}.")
    if developers: parts.append('Developed by ' + ', '.join(dict.fromkeys(developers)) + '.')
    if years: parts.append(f'This platform version was released in {years[0]}.')
    if modes: parts.append('Play modes: ' + ', '.join(modes) + '.')
    # No invented plot/gameplay; leave unresolved instead of meaningless filler.
    return ' '.join(parts) if genres else ''


def prepare_metadata():
    baseline = CACHE/'baseline-titles.json'
    if not baseline.exists():
        write_json(baseline, json.loads(subprocess.check_output([GIT,'-C',str(ROOT),'show','HEAD:titles.json'])))
    titles = read_json(baseline)
    plans = read_json(CACHE / 'plan.json')
    sources, changes, text_review = {}, Counter(), []
    # Detect old importer text that is an exact copy from a different IGDB
    # entry (e.g. a PC remake used for a Mega Drive game).
    prose_ids = defaultdict(set)
    for path in (CACHE / 'igdb').glob('*.json'):
        for game in read_json(path):
            for field in ('summary', 'storyline'):
                if game.get(field): prose_ids[game[field].strip()].add(game['id'])
    for tid, title in titles.items():
        item = plans.get(tid)
        origin = {}
        mismatched = []
        if item:
            for field in ('intro', 'description'):
                old = title.get(field, '').strip()
                owners = prose_ids.get(old, set())
                if owners and item['game']['id'] not in owners:
                    mismatched.append(field)
            if mismatched:
                # Shared marketing copy is not proof of a bad match. Flag it,
                # do not replace a rich existing synopsis with a short overview.
                text_review.append({'title_id': tid, 'name': title['name'], 'fields': mismatched, 'candidate': item['game'].get('url')})
        if not title.get('description'):
            if title.get('intro'):
                title['description'] = title['intro']
                origin['description'] = 'Existing pack synopsis promoted to description'
                changes['existing_synopsis_promoted'] += 1
            elif item:
                fresh = factual_description(item)
                if fresh:
                    title['description'] = fresh
                    origin['description'] = 'Original factual overview from verified structured metadata'
                    changes['new_factual_descriptions'] += 1
        if item:
            publishers = [c['company']['name'] for c in item['game'].get('involved_companies', []) if c.get('publisher')]
            if publishers and title.get('publisher', '') in ('', 'MM'):
                title['publisher'] = ', '.join(dict.fromkeys(publishers))
                changes['publisher_filled'] += 1
                origin['publisher'] = 'IGDB publisher field'
        if origin:
            if item:
                origin.update(game_id=item['game']['id'], source_url=item['game'].get('url'), platform=item['platform'])
            sources[tid] = origin
    manual_path=ROOT/'manual-metadata-corrections.json'
    if manual_path.exists():
        for tid, correction in read_json(manual_path).items():
            for field, value in correction['fields'].items():
                if value is None: titles[tid].pop(field, None)
                else: titles[tid][field]=value
            sources.setdefault(tid, {})['manual_review']=correction
            changes['manually_corrected_records'] += 1
    write_json(CACHE / 'titles-enriched.json', titles)
    write_json(CACHE / 'metadata-sources.json', sources)
    write_json(CACHE / 'metadata-counts.json', dict(changes))
    write_json(CACHE / 'text-review.json', text_review)
    print(json.dumps(dict(changes), indent=2))
    print('Remaining without description:', sum(not x.get('description') for x in titles.values()))


def write_titles_pack(path, titles):
    strings = bytearray(b'\0')
    entries = bytearray()
    for tid, title in sorted(titles.items()):
        offsets, flags = [], 0
        for i, name in enumerate(('name', 'publisher', 'intro', 'description')):
            value = title.get(name, '')
            if value:
                if '\0' in value: raise ValueError('NUL in text')
                offsets.append(len(strings))
                strings.extend(value.encode('utf-8') + b'\0')
                flags |= 1 << i
            else: offsets.append(0)
        values = []
        for i, name in enumerate(('size', 'version', 'releaseDate', 'isDemo'), 4):
            value = title.get(name)
            values.append(int(value) if value is not None else 0)
            if value is not None: flags |= 1 << i
        entries.extend(struct.pack('<QIIIIQIIiI', int(tid,16), *offsets, *values, flags))
    Path(path).write_bytes(struct.pack('<8sIIIIQ', b'CFTITLE1', 1, 48, len(titles), 0, 32+len(entries)) + entries + strings)


def write_icons_pack(path, records):
    offset = 0
    with Path(path).open('wb') as out:
        out.write(struct.pack('<8sIIIIQ', b'CFICONP1', 1, 32, len(records), 0, 32+len(records)*32))
        for tid, (ext, data) in sorted(records.items()):
            assert 0 < len(data) <= 16*1024*1024 and len(ext) <= 7
            out.write(struct.pack('<QQI8sI', int(tid,16), offset, len(data), ext.encode(), 0))
            offset += len(data)
        for tid, (_, data) in sorted(records.items()): out.write(data)


ARTWORK_LAYOUT = 'stretch-full-tile-v2'


def normalize_cover(raw):
    """Fill the square app tile without baking letterbox bars into the pack."""
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(raw)) as im:
        face = ImageOps.exif_transpose(im).convert('RGB').resize((256, 256), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        face.save(buf, format='WEBP', quality=88, method=4)
        return buf.getvalue()


def build(original_icons):
    titles = read_json(CACHE / 'titles-enriched.json')
    records = unpack_icons(original_icons)
    sources = read_json(CACHE / 'art-sources.json')
    provenance = {'schema': 1, 'artwork': {}, 'metadata': read_json(CACHE/'metadata-sources.json')}
    previous = read_json(ROOT/'metadata-provenance.json').get('artwork', {}) if (ROOT/'metadata-provenance.json').exists() else {}
    for i, (tid, source) in enumerate(sources.items(), 1):
        raw = (CACHE/'art'/f'{tid}.image').read_bytes()
        assert hashlib.sha256(raw).hexdigest() == source['sha256']
        # User-selected full-tile fit: retain the complete cover, stretching
        # rectangular artwork rather than cropping or adding black side bars.
        path = ROOT/'icons'/f'{tid}.webp'
        if (path.exists() and previous.get(tid, {}).get('sha256') == source['sha256']
                and previous.get(tid, {}).get('layout') == ARTWORK_LAYOUT):
            data = path.read_bytes()
            assert hashlib.sha256(data).hexdigest() == previous[tid]['packed_sha256']
        else:
            data = normalize_cover(raw)
        records[tid] = ('webp', data)
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
        provenance['artwork'][tid] = dict(source, packed_sha256=hashlib.sha256(data).hexdigest(), match='normalized exact title plus unique catalogue platform', generated=False, layout=ARTWORK_LAYOUT)
        if i % 1000 == 0: print('Normalized covers:', i, flush=True)
    write_json(ROOT/'titles.json', titles)
    write_titles_pack(ROOT/'titles.pack', titles)
    write_icons_pack(ROOT/'icons.pack', records)
    write_json(ROOT/'icons.index.json', {tid: ext for tid,(ext,_) in sorted(records.items())})
    write_json(ROOT/'metadata-provenance.json', provenance)
    now = dt.datetime.now(dt.timezone.utc)
    files = {}
    for name in ('titles.pack','icons.pack'):
        data = (ROOT/name).read_bytes()
        files[name] = {'url':name, 'size':len(data), 'sha256':hashlib.sha256(data).hexdigest()}
    manifest = {'schema':1, 'db_version':now.strftime('%Y%m%d%H%M%S'), 'generated_at_utc':now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'files':files}
    write_json(ROOT/'manifest.json',manifest)
    write_json(ROOT/'offline_db_manifest.json',manifest)
    report = dict(read_json(CACHE/'metadata-counts.json'), titles=len(titles), replaced_artwork=len(sources), boxart_scans=sum(x['source']=='libretro-boxart' for x in sources.values()), catalogue_covers=sum(x['source']=='igdb-cover' for x in sources.values()), remaining_without_description=sum(not t.get('description') for t in titles.values()), unchanged_artwork=len(records)-len(sources), failed_artwork_downloads=read_json(CACHE/'art-errors.json'))
    write_json(ROOT/'enrichment-report.json', report)
    review=read_json(CACHE/'unresolved.json')
    for item in review: item['name']=titles[item['title_id']]['name']
    write_json(ROOT/'metadata-review-needed.json', review)
    # Build stages new/updated source images; obsolete extension paths are
    # reported for explicit review before Git staging, never bulk-deleted.
    tracked = subprocess.check_output([GIT,'-C',str(ROOT),'ls-files','icons'], text=True).splitlines()
    obsolete = [p for p in tracked if Path(p).stem in sources and Path(p).suffix != '.webp']
    write_json(CACHE/'obsolete-icon-paths.json', obsolete)
    print(json.dumps(report, indent=2), flush=True)


def validate():
    from PIL import Image
    titles = read_json(ROOT/'titles.json')
    original = json.loads(subprocess.check_output([GIT,'-C',str(ROOT),'show','HEAD:titles.json']))
    assert original.keys() <= titles.keys(), 'Existing title IDs removed'
    additions=titles.keys()-original.keys()
    assert all(tid.startswith('07') for tid in additions), 'Unexpected non-catalogue title ID'
    manual=read_json(ROOT/'manual-metadata-corrections.json') if (ROOT/'manual-metadata-corrections.json').exists() else {}
    for tid in original:
        exceptions=manual.get(tid,{}).get('fields',{})
        for field in ('name','size','version','releaseDate','isDemo','intro'):
            if field in exceptions:
                assert titles[tid].get(field)==exceptions[field]
                continue
            assert titles[tid].get(field) == original[tid].get(field), (tid,field)
        if original[tid].get('description') and 'description' not in exceptions:
            assert titles[tid]['description'] == original[tid]['description'], tid
    manifest = read_json(ROOT/'manifest.json')
    assert manifest == read_json(ROOT/'offline_db_manifest.json')
    for name, info in manifest['files'].items():
        data = (ROOT/name).read_bytes()
        assert len(data)==info['size'] and hashlib.sha256(data).hexdigest()==info['sha256']
    packed = (ROOT/'titles.pack').read_bytes()
    magic,version,stride,count,_,start = struct.unpack_from('<8sIIIIQ',packed)
    assert (magic,version,stride,count,start)==(b'CFTITLE1',1,48,len(titles),32+len(titles)*48)
    for i,tid in enumerate(sorted(titles)):
        entry = struct.unpack_from('<QIIIIQIIiI',packed,32+i*48)
        assert entry[0]==int(tid,16)
        flags=entry[-1]
        for j,name in enumerate(('name','publisher','intro','description')):
            offset=entry[j+1]
            actual=packed[start+offset:packed.index(0,start+offset)].decode('utf-8') if flags & (1<<j) else ''
            assert actual==titles[tid].get(name,''), (tid,name)
        for j,name in enumerate(('size','version','releaseDate','isDemo')):
            actual=entry[j+5] if flags & (1<<(j+4)) else None
            assert actual==titles[tid].get(name), (tid,name)
    records=unpack_icons(ROOT/'icons.pack')
    assert records.keys()==titles.keys()
    index=read_json(ROOT/'icons.index.json')
    for tid,(ext,data) in records.items():
        assert index[tid]==ext
        with Image.open(io.BytesIO(data)) as im: im.verify()
    provenance=read_json(ROOT/'metadata-provenance.json')
    for tid,source in provenance['artwork'].items():
        assert hashlib.sha256(records[tid][1]).hexdigest()==source['packed_sha256']
        assert records[tid][1] == (ROOT/'icons'/f'{tid}.webp').read_bytes()
    if (ROOT/'catalogue-artwork.json').exists():
        catalogue=read_json(ROOT/'catalogue-artwork.json')
        assert catalogue['schema']==1
        keys=set()
        for platform,ident,name,tid in catalogue['entries']:
            assert (platform,ident) not in keys
            keys.add((platform,ident))
            assert tid in records and tid in titles and name
        assert additions <= {r[3] for r in catalogue['entries']}
    print(f'PASS: all {len(titles)} title records round-trip; all {len(records)} images decode; manifests and source hashes agree; existing descriptions and names preserved except documented manual corrections; IDs preserved.')


def preview():
    from PIL import Image, ImageDraw, ImageFont
    sources = read_json(CACHE/'art-sources.json')
    counts = Counter()
    selected = []
    for tid, source in sources.items():
        p = source['platform']
        if counts[p] >= 2 or not (ROOT/'icons'/f'{tid}.webp').exists(): continue
        counts[p] += 1
        selected.append((tid,source))
    cols=5
    rows=(len(selected)+cols-1)//cols
    sheet=Image.new('RGB',(cols*220, rows*240),'#1b1b1b')
    draw=ImageDraw.Draw(sheet)
    font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',12)
    for n,(tid,source) in enumerate(selected):
        x,y=(n%cols)*220,(n//cols)*240
        im=Image.open(ROOT/'icons'/f'{tid}.webp').convert('RGB')
        im.thumbnail((195,195))
        sheet.paste(im,(x,y))
        draw.text((x,y+198),source['platform']+' | '+source['name'][:30]+'\n'+source['name'][30:60],font=font,fill='white')
    sheet.save(CACHE/'replacement-art-preview.jpg')


def fetch_catalogues():
    for platform in PLATFORMS:
        path = CACHE / 'catalogue' / (platform + '.json')
        if path.exists():
            continue
        url = 'https://magicmonkei.com/tinfoil/roms/games/index?' + urllib.parse.urlencode({'search': '', 'consoleCatalog': platform})
        payload = json.loads(get(url, headers={'User-Agent': 'MonkeiFoil/3.5.0', 'Accept': 'application/json'}))
        rows = payload.get('results', [])
        if not isinstance(rows, list):
            raise ValueError('Invalid catalogue response')
        # Do not cache or publish download links, auth fields or server artwork.
        safe = [{'id': x.get('id'), 'name': x.get('name'), 'platform': x.get('platform_key', platform)} for x in rows]
        write_json(path, safe)
        print('Catalogue', platform, len(safe), flush=True)
        time.sleep(.3)


def fetch_igdb(settings_path):
    settings = read_json(settings_path)
    body = urllib.parse.urlencode({'client_id': settings['IgdbClientId'], 'client_secret': settings['IgdbClientSecret'], 'grant_type': 'client_credentials'}).encode()
    token = json.loads(get('https://id.twitch.tv/oauth2/token', body, {'Content-Type': 'application/x-www-form-urlencoded'}))['access_token']
    headers = {'Client-ID': settings['IgdbClientId'], 'Authorization': 'Bearer ' + token, 'Content-Type': 'text/plain'}
    ids = ','.join(str(i) for i in sorted({v[0] for v in PLATFORMS.values() if v[0]}))
    # Keyset pagination, immutable cached pages; no raw credentials persisted.
    after = 0
    total = 0
    while True:
        path = CACHE / 'igdb' / f'{after}.json'
        if path.exists():
            rows = read_json(path)
        else:
            query = f'fields id,name,url,alternative_names.name,platforms,cover.image_id,summary,storyline,genres.name,game_modes.name,involved_companies.publisher,involved_companies.developer,involved_companies.company.name,release_dates.platform,release_dates.y,release_dates.date; where platforms = ({ids}) & id > {after}; sort id asc; limit 500;'
            rows = json.loads(get('https://api.igdb.com/v4/games', query.encode(), headers))
            if not isinstance(rows, list):
                raise ValueError('Invalid IGDB response')
            write_json(path, rows)
            time.sleep(.4)
        if not rows:
            break
        after = rows[-1]['id']
        total += len(rows)
        print('IGDB metadata records cached:', total, flush=True)


def mapping(titles):
    by_name = defaultdict(set)
    by_id = defaultdict(set)
    for path in (CACHE / 'catalogue').glob('*.json'):
        for item in read_json(path):
            platform = item['platform']
            if platform not in PLATFORMS:
                platform = path.stem
            if item.get('name'):
                by_name[key(item['name'])].add(platform)
            if item.get('id'):
                by_id[str(item['id']).upper()].add(platform)
    result = {}
    for tid, title in titles.items():
        if tid.startswith('01'):
            result[tid] = ['switch']
        else:
            result[tid] = sorted(by_id.get(tid, set()) or by_name.get(key(title.get('name', '')), set()))
    return result


def audit():
    titles = read_json(ROOT / 'titles.json')
    icons = read_json(ROOT / 'icons.index.json')
    mapped = mapping(titles)
    out = {
        'titles': len(titles), 'icons': len(icons),
        'missing_description': sum(not x.get('description') for x in titles.values()),
        'missing_any_synopsis': sum(not(x.get('description') or x.get('intro')) for x in titles.values()),
        'missing_icon': sum(tid not in icons for tid in titles),
        'platform_mapping': dict(Counter('unique' if len(x) == 1 else 'ambiguous' if x else 'unknown' for x in mapped.values())),
        'platforms': dict(Counter(x[0] for x in mapped.values() if len(x) == 1)),
    }
    write_json(CACHE / 'audit-before.json', out)
    write_json(CACHE / 'platform-map.json', mapped)
    print(json.dumps(out, indent=2), flush=True)


def plan():
    titles = read_json(ROOT / 'titles.json')
    mapped = mapping(titles)
    games = {}
    for path in (CACHE / 'igdb').glob('*.json'):
        games.update({g['id']: g for g in read_json(path)})
    lookup = defaultdict(set)
    for gid, game in games.items():
        for name in [game['name']] + [x['name'] for x in game.get('alternative_names', [])]:
            for platform in game.get('platforms', []):
                lookup[(platform, key(name))].add(gid)
    changes, unresolved = {}, []
    for tid, title in titles.items():
        platforms = mapped[tid]
        if len(platforms) != 1 or PLATFORMS[platforms[0]][0] is None:
            unresolved.append({'title_id': tid, 'name': title.get('name'), 'reason': 'platform_unknown_or_ambiguous', 'platforms': platforms})
            continue
        platform = platforms[0]
        ids = lookup.get((PLATFORMS[platform][0], key(title.get('name', ''))), set())
        if len(ids) != 1:
            unresolved.append({'title_id': tid, 'name': title.get('name'), 'reason': 'no_exact_match' if not ids else 'ambiguous_game', 'platforms': platforms})
            continue
        game = games[next(iter(ids))]
        changes[tid] = {'platform': platform, 'name': title['name'], 'game': game}
    write_json(CACHE / 'plan.json', changes)
    write_json(CACHE / 'unresolved.json', unresolved)
    print(json.dumps({'exact_platform_matches': len(changes), 'unresolved': len(unresolved), 'matched_with_cover': sum(bool(x['game'].get('cover')) for x in changes.values()), 'matched_with_summary': sum(bool(x['game'].get('summary')) for x in changes.values()), 'platforms': dict(Counter(x['platform'] for x in changes.values()))}, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['catalogues', 'igdb', 'audit', 'plan', 'boxart-indexes', 'boxart-plan', 'download-art', 'inspect', 'metadata', 'build', 'validate', 'preview'])
    parser.add_argument('--settings', type=Path)
    parser.add_argument('--original-icons', type=Path)
    args = parser.parse_args()
    if args.action == 'catalogues': fetch_catalogues()
    elif args.action == 'igdb':
        if not args.settings: parser.error('--settings is required; do not put credentials in command arguments')
        fetch_igdb(args.settings)
    elif args.action == 'audit': audit()
    elif args.action == 'plan': plan()
    elif args.action == 'boxart-indexes': fetch_boxart_indexes()
    elif args.action == 'boxart-plan': boxart_plan()
    elif args.action == 'download-art': download_art()
    elif args.action == 'inspect': inspect_original(args.original_icons)
    elif args.action == 'metadata': prepare_metadata()
    elif args.action == 'build': build(args.original_icons)
    elif args.action == 'validate': validate()
    elif args.action == 'preview': preview()


if __name__ == '__main__':
    main()
