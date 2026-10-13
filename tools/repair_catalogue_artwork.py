"""Bridge public shop labels to platform-specific pack art; never use server images."""
import argparse
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import re
import urllib.parse
from PIL import Image
import enrich_database as db

CACHE=db.CACHE/'catalogue-repair'
# Explicit aliases are scoped to Ports, never used for fuzzy/sequel matching.
PORT_ALIASES={
 'Bully - Anniversary Edition (Port)': 'Bully',
 'Simpsons Hit Run Port': 'The Simpsons: Hit & Run',
 'Simpsons Hit & Run (Port) BR': 'The Simpsons: Hit & Run',
 'Majoras Mask': "The Legend of Zelda: Majora's Mask",
 'Fallout 2 CE': 'Fallout 2',
 'Fallout 1 CE': 'Fallout',
 'AM2R': 'AM2R',
 'Final Fantasy IV 3D Remake (Port)': 'Final Fantasy IV (3D Remake)',
 'Final Fantasy Dimensions 2 (Port)': 'Final Fantasy Dimensions II',
 'Wipeout Rewrite (Port)': 'Wipeout',
 'Castle of Illusion (Android Port)': 'Castle of Illusion Starring Mickey Mouse',
 'Jak and Daxter': 'Jak and Daxter: The Precursor Legacy',
 'Chaos Ring 3 (Port)': 'Chaos Rings III',
 'Captain Claw': 'Claw',
 'Outrun 2006': 'OutRun 2006: Coast 2 Coast',
 'Jazz 2 Ressurection': 'Jazz Jackrabbit 2',
 '0AD (Port)': '0 A.D.',
 'Mercenaries 2': 'Mercenaries 2: World in Flames',
}
# Select the original PC/mobile edition when a title also names a remake.
PORT_IGDB_PLATFORMS={
 'Grand Theft Auto V':6, 'Half-Life 2':6, 'Silent Hill 2':8,
 'Castle of Illusion (Android Port)':34, 'Need For Speed Most Wanted':6,
 'Grand Theft Auto - San Andreas (Android Port)':34,
}
# Manually reviewed original-game editions in the cached IGDB results. Ports
# may not have a retail Switch cover; use the source game's art, not a remake.
PORT_GAME_IDS={
 'Need For Speed Underground 2':97,
 'Need for Speed Underground':96,
 'Need For Speed Most Wanted':98,
 'Final Fantasy IV 3D Remake (Port)':399,
 'Bully - Anniversary Edition (Port)':88952,
 'Simpsons Hit Run Port':2844,
 'Simpsons Hit & Run (Port) BR':2844,
 'The Legend of Zelda - Ocarina of Time (Port)':1029,
 'Angry Birds Classic (Android Port)':2035,
 'NBA Jam (Port)':88923,
 'Geometry Dash (Port)':11642,
 'Majoras Mask':1030,
 'Crazy Taxi (Android Port)':39191,
 'Super Smash Bros. (BattleShip Port)':1626,
 'Grand Theft Auto - Chinatown Wars (Port)':3270,
 'Mario Kart 64 (Port)':2342,
 'Castle of Illusion (Android Port)':11495,
 'Ship of Harkinian':194694,
 'Sonic the Hedgehog 4 Episode II (Port)':9474,
 'LEGO Batman 3 Beyond Gotham (Port)':346951,
 'Sonic Time Twisted':125154,
 'Final Fantasy Dimensions (Port)':19080,
 'LEGO Ninjago Shadow of Ronin (Port)':19006,
 'Professor Layton and the Unwound Future (Port)':141948,
 'Outrun 2006':2054,
 'Chaos Ring 3 (Port)':39228,
 'LEGO Star Wars The Force Awakens (Port)':17030,
 'Final Fantasy IV The After Years (Port)':146022,
 'Layton Brothers - Mystery Room (Port)':20912,
 'FreeSpace 2 (Port)':722,
 'Grand Theft Auto V':1020,
 'The Legend of Zelda Twilight Princess':1036,
 'The Legend of Zelda - Twilight Princess (Port)':1036,
 'Castlevania Symphony of the Night (Android Port)':223449,
 'Lego Star Wars - The Complete Saga (Port)':2682,
 'Super Mario War':87815,
 'Grand Theft Auto - Liberty City Stories':309110,
 'Sonic Unleashed':5169,
 'Call of Duty 4 Modern Warfare':623,
 'Heart of Darkness (Port)':6433,
 'Jak and Daxter':1528,
 'Sonic Adventure 2 Battle':192194,
 'Captain Claw':2474,
 'Mass Effect':73,
 'The Legend of Zelda A Link to the Past':1026,
 'Sly Cooper and the Thievius Raccoonus':1798,
}

def keys(name):
    name=re.sub(r'(?i)(?<![0-9a-f])[0-9a-f]{16}(?![0-9a-f])','',name).strip()
    result=[db.key(name)]
    # A fallback only: never discard "World" from an exact title match.
    flat=re.sub(r'(?i)\s*\((?:NA|EU|JP|U|E|J)(?:,\s*(?:Rev\s+[0-9A-Z.]+|NA|EU|JP|U|E|J))*\)\s*$','',name)
    flat=re.sub(r'(?i)(?:\s+(?:USA|Europe|Japan|JAP|World|U|E|J|JU|UE|JUE|Rev\s+[A-Z0-9.]+))+$','',flat)
    for value in (flat, re.sub(r'(?i)\s*\((?:android |pc |offline |battleship )?port\)\s*$','',flat)):
        if db.key(value) not in result: result.append(db.key(value))
    return result

def port_keys(name):
    # Known packaging labels only; do not strip arbitrary edition/remake text.
    trimmed=re.sub(r'(?i)\s*\((?:port|homebrew|ocarina of time port)\)\s*$','',name)
    return list(dict.fromkeys(keys(name)+keys(trimmed)))

def plan():
    CACHE.mkdir(exist_ok=True)
    titles=db.read_json(db.ROOT/'titles.json')
    icons=db.read_json(db.ROOT/'icons.index.json')
    sources=db.read_json(db.CACHE/'art-sources.json')
    oldnames=defaultdict(list); matched=defaultdict(list)
    for tid,t in titles.items():
        for k in port_keys(t['name']): oldnames[k].append(tid)
    for tid,s in sources.items():
        for k in keys(s['name']): matched[s['platform'],k].append(tid)
    games={}
    for path in (db.CACHE/'igdb').glob('*.json'):
        games.update({g['id']:g for g in db.read_json(path)})
    if (CACHE/'ports-igdb.json').exists():
        games.update({g['id']:g for g in db.read_json(CACHE/'ports-igdb.json')})
    igdb=defaultdict(set)
    for gid,g in games.items():
        if not g.get('cover',{}).get('image_id'): continue
        for name in [g['name']]+[a['name'] for a in g.get('alternative_names',[])]:
            igdb[db.key(name)].add(gid)
    scans={}
    for path in (db.CACHE/'boxart-index').glob('*.json'):
        data=db.read_json(path); idx=defaultdict(list)
        for row in data['rows']:
            for k in keys(row['name']): idx[k].append(row)
        scans[path.stem]=(data,idx)
    result=[]; additions={}; missing=[]
    for path in sorted((db.CACHE/'catalogue').glob('*.json')):
        platform=path.stem
        if platform in ('switch','wiiu','ps2'): continue
        for row in db.read_json(path):
            tid=None; source=None; fallback_tid=None
            candidates=re.findall(r'(?i)(?<![0-9a-f])[0-9a-f]{16}(?![0-9a-f])',row['id']+' '+row['name'])
            for candidate in candidates:
                if candidate.upper() in icons: tid=candidate.upper(); break
            name=PORT_ALIASES.get(row['name'],row['name']) if platform=='ports' else row['name']
            for k in keys(name):
                if tid: break
                ids=matched.get((platform,k),[])
                if not ids and platform=='ports': ids=oldnames.get(k,[])
                # Prefer actual homebrew pack artwork over a console's edition.
                if ids:
                    ids=sorted(set(ids),key=lambda x:(not x.startswith('01'),x))
                    if platform=='ports': fallback_tid=ids[0]
                    else: tid=ids[0]; break
                if platform in scans:
                    data,idx=scans[platform]; rows=idx.get(k,[])
                    if rows:
                        art=sorted(rows,key=lambda a:('USA' not in a['name'],'Europe' not in a['name'],a['name']))[0]
                        source={'source':'libretro-boxart','source_name':art['name'],'repo':data['repo'],
                         'blob_sha':art['sha'],'url':'https://raw.githubusercontent.com/'+data['repo']+'/master/'+urllib.parse.quote(art['path'])}
                        break
                ids=igdb.get(k,set())
                if platform=='ports':
                    if row['name'] in PORT_IGDB_PLATFORMS:
                        ids={gid for gid in ids if PORT_IGDB_PLATFORMS[row['name']] in games[gid].get('platforms',[])}
                else:
                    ids={gid for gid in ids if db.PLATFORMS[platform][0] in games[gid].get('platforms',[])}
                primary={gid for gid in ids if db.key(games[gid]['name'])==k}
                if primary: ids=primary
                if len(ids)==1:
                    g=games[next(iter(ids))]
                    source={'source':'igdb-cover','source_name':g['name'],'game_id':g['id'],'game_url':g.get('url'),
                     'url':'https://images.igdb.com/igdb/image/upload/t_cover_big/'+g['cover']['image_id']+'.jpg'}
                    break
            if not tid and platform=='ports' and row['name'] in PORT_GAME_IDS:
                g=games[PORT_GAME_IDS[row['name']]]
                source={'source':'igdb-cover','source_name':g['name'],'game_id':g['id'],'game_url':g.get('url'),
                 'url':'https://images.igdb.com/igdb/image/upload/t_cover_big/'+g['cover']['image_id']+'.jpg',
                 'review':'original game edition for the named port; not retail Switch artwork'}
            if source:
                tid='07'+hashlib.sha256((platform+'\0'+row['id']).encode()).hexdigest()[:14].upper()
                assert tid not in titles or titles[tid]['name']==row['name'], 'Generated artwork ID collision'
                additions[tid]=dict(source,name=row['name'],platform=platform,catalogue_id=row['id'])
            elif not tid: tid=fallback_tid
            if tid: result.append([platform,row['id'],row['name'],tid])
            else: missing.append(row)
    db.write_json(CACHE/'plan.json',{'mappings':result,'additions':additions,'missing':missing})
    print('Mappings',len(result),'new artwork',len(additions),'unresolved',len(missing),flush=True)
    print('Ports unresolved:',[r['name'] for r in missing if r['platform']=='ports'])
    print('Coverage',dict(Counter(r[0] for r in result)))

def ports_metadata():
    # Same previously configured IGDB client. Credentials never enter output or repo.
    settings=db.read_json(Path('C:/Users/phons/AppData/Local/CyberfoilOfflineDB/settings.json'))
    body=urllib.parse.urlencode({'client_id':settings['IgdbClientId'],'client_secret':settings['IgdbClientSecret'],'grant_type':'client_credentials'}).encode()
    token=json.loads(db.get('https://id.twitch.tv/oauth2/token',body,{'Content-Type':'application/x-www-form-urlencoded'}))['access_token']
    headers={'Client-ID':settings['IgdbClientId'],'Authorization':'Bearer '+token,'Content-Type':'text/plain'}
    games={g['id']:g for g in db.read_json(CACHE/'ports-igdb.json')} if (CACHE/'ports-igdb.json').exists() else {}
    rows_to_fetch=db.read_json(db.CACHE/'catalogue/ports.json')
    if games and (CACHE/'plan.json').exists():
        current=db.read_json(CACHE/'plan.json')
        needed={r[2] for r in current['mappings'] if r[0]=='ports' and not r[3].startswith('07')}
        needed.update(r['name'] for r in current['missing'] if r['platform']=='ports')
        rows_to_fetch=[r for r in rows_to_fetch if r['name'] in needed]
    for row in rows_to_fetch:
        name=PORT_ALIASES.get(row['name'],row['name'])
        name=re.sub(r'(?i)\s*\((?:android |pc |offline |battleship )?port\)\s*$','',name)
        query='search '+json.dumps(name)+'; fields id,name,url,alternative_names.name,platforms,cover.image_id; limit 30;'
        rows=json.loads(db.get('https://api.igdb.com/v4/games',query.encode(),headers))
        assert isinstance(rows,list)
        games.update({g['id']:g for g in rows})
        db.time.sleep(.3)
    db.write_json(CACHE/'ports-igdb.json',list(games.values()))
    print('Cached Ports metadata candidates',len(games))

def download():
    plan=db.read_json(CACHE/'plan.json'); dest=CACHE/'art'; dest.mkdir(exist_ok=True)
    previous=db.read_json(CACHE/'verified.json') if (CACHE/'verified.json').exists() else {}
    # Reuse cached verified source images by URL, not an unrelated title ID.
    existing={s['url']:(db.CACHE/'art'/f'{tid}.image') for tid,s in db.read_json(db.CACHE/'art-sources.json').items()}
    def fetch(item):
        tid,s=item; path=dest/(tid+'.image')
        try:
            reusable=path.exists() and previous.get(tid,{}).get('url')==s['url']
            raw=path.read_bytes() if reusable else existing[s['url']].read_bytes() if s['url'] in existing else db.get(s['url'])
            if s.get('blob_sha'): assert hashlib.sha1(f'blob {len(raw)}\0'.encode()+raw).hexdigest()==s['blob_sha']
            with Image.open(io.BytesIO(raw)) as im: im.verify()
            with Image.open(io.BytesIO(raw)) as im: assert im.width>=32 and im.height>=32
            path.write_bytes(raw)
            return tid,dict(s,sha256=hashlib.sha256(raw).hexdigest()),None
        except Exception as e: return tid,None,type(e).__name__
    good={}; errors={}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for i,(tid,s,error) in enumerate(pool.map(fetch,plan['additions'].items()),1):
            if error: errors[tid]=error
            else: good[tid]=s
            if i%100==0: print('Checked artwork',i,'/',len(plan['additions']),'errors',len(errors),flush=True)
    db.write_json(CACHE/'verified.json',good); db.write_json(CACHE/'errors.json',errors)

def apply():
    plan=db.read_json(CACHE/'plan.json'); good=db.read_json(CACHE/'verified.json')
    records=db.unpack_icons(db.ROOT/'icons.pack'); titles=db.read_json(db.ROOT/'titles.json')
    provenance=db.read_json(db.ROOT/'metadata-provenance.json')
    for i,(tid,s) in enumerate(good.items(),1):
        raw=(CACHE/'art'/(tid+'.image')).read_bytes(); assert hashlib.sha256(raw).hexdigest()==s['sha256']
        previous=provenance['artwork'].get(tid,{})
        if tid in records and previous.get('sha256')==s['sha256'] and previous.get('layout')==db.ARTWORK_LAYOUT:
            data=records[tid][1]
        else: data=db.normalize_cover(raw)
        (db.ROOT/'icons'/(tid+'.webp')).write_bytes(data)
        records[tid]=('webp',data)
        # Catalogue-only metadata IDs are not install/forwarder title IDs.
        titles[tid]={'name':s['name']}
        provenance['artwork'][tid]=dict(s,packed_sha256=hashlib.sha256(data).hexdigest(),layout=db.ARTWORK_LAYOUT,generated=False,match='explicit catalogue platform and exact source title')
        if i%1000==0: print('Packed catalogue artwork',i,'/',len(good),flush=True)
    mappings=[r for r in plan['mappings'] if r[3] in records]
    for row in mappings: assert row[3] in titles
    db.write_json(db.ROOT/'catalogue-artwork.json',{'schema':1,'entries':mappings})
    db.write_json(db.ROOT/'titles.json',titles); db.write_titles_pack(db.ROOT/'titles.pack',titles)
    db.write_icons_pack(db.ROOT/'icons.pack',records)
    db.write_json(db.ROOT/'icons.index.json',{tid:ext for tid,(ext,_) in sorted(records.items())})
    db.write_json(db.ROOT/'metadata-provenance.json',provenance)
    manifest=db.read_json(db.ROOT/'manifest.json')
    now=db.dt.datetime.now(db.dt.timezone.utc)
    manifest.update(db_version=now.strftime('%Y%m%d%H%M%S'),generated_at_utc=now.strftime('%Y-%m-%dT%H:%M:%SZ'))
    for name in ('titles.pack','icons.pack','catalogue-artwork.json'):
        raw=(db.ROOT/name).read_bytes()
        manifest['files'][name]={'url':name,'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
    for name in ('manifest.json','offline_db_manifest.json'): db.write_json(db.ROOT/name,manifest)
    report={'new_artwork':len(good),'system_scoped_mappings':len(mappings),'by_system':dict(Counter(r[0] for r in mappings)),
      'unresolved':plan['missing'],'failed_sources':db.read_json(CACHE/'errors.json'),'layout':db.ARTWORK_LAYOUT}
    db.write_json(db.ROOT/'catalogue-artwork-report.json',report)
    print({k:v for k,v in report.items() if k not in ('unresolved','failed_sources')},flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('action',choices=['plan','download','apply','ports_metadata'])
    globals()[parser.parse_args().action]()
