"""Import pinned Microsoft MIT Fluent 3D emoji as optimized local WebP + shared tokens.
Run with backend/.venv/bin/python frontend/scripts/import-fluent-emoji.py.
Upstream PNGs stay in a temporary download cache, never in the repository.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import time
import urllib.parse
import urllib.request
from PIL import Image
import httpx

ROOT = Path(__file__).resolve().parents[2]
COMMIT = '1ffb34c752ecf5d402f04cfb4b392c77f57c54bc'
BASE = f'https://raw.githubusercontent.com/microsoft/fluentui-emoji/{COMMIT}/'
OUT = ROOT / 'frontend/public/emoji'
CACHE = Path('/tmp/twinteams-fluent-import')
OUT.mkdir(parents=True, exist_ok=True); CACHE.mkdir(parents=True, exist_ok=True)
CLIENT = httpx.Client(follow_redirects=True, timeout=45, limits=httpx.Limits(max_connections=32, max_keepalive_connections=32))

def fetch(url):
    cache = CACHE / sha256(url.encode()).hexdigest()
    if cache.exists(): return cache.read_bytes()
    for attempt in range(8):
        try:
            response = CLIENT.get(url); response.raise_for_status(); result = response.content
            cache.write_bytes(result)
            return result
        except Exception:
            if attempt == 7: raise
            time.sleep(min(attempt + 1, 3))

def encoded(path): return BASE + urllib.parse.quote(path)

tree = json.loads(fetch(f'https://api.github.com/repos/microsoft/fluentui-emoji/git/trees/{COMMIT}?recursive=1'))
assert not tree.get('truncated')
rows = tree['tree']
metadata_paths = [r['path'] for r in rows if r['path'].endswith('/metadata.json')]
metadata = []
with ThreadPoolExecutor(max_workers=24) as pool:
    for index, future in enumerate(as_completed([pool.submit(lambda path: (path, json.loads(fetch(encoded(path)))), path) for path in metadata_paths])):
        metadata.append(future.result())
        if index % 300 == 0: print('Metadata', index + 1, flush=True)
print('Metadata complete', len(metadata), flush=True)
# CLDR names are Unicode data under Unicode's permissive data license.
cldr_commit = '91c267402229a59e3ef2774544f001bf959e8809'
zh_url = f'https://raw.githubusercontent.com/unicode-org/cldr-json/{cldr_commit}/cldr-json/cldr-annotations-full/annotations/zh/annotations.json'
zh = json.loads(fetch(zh_url))['annotations']['annotations']

def localized(glyph):
    return zh.get(glyph, zh.get(glyph.replace('\ufe0f', ''), {}))

def to_glyph(value): return ''.join(chr(int(code, 16)) for code in value.split())

pngs = {r['path']: r for r in rows if '/3D/' in r['path'] and r['path'].endswith('.png')}
entries = []; tokens = set(); images = {}
tones = ['', 'Light', 'Medium-Light', 'Medium', 'Medium-Dark', 'Dark']
for path, meta in sorted(metadata):
    folder = path.rsplit('/', 1)[0]
    options = [r for path_, r in pngs.items() if path_.startswith(folder + '/')]
    if not options: raise RuntimeError('No 3D asset: ' + folder)
    variants = []
    skin = meta.get('unicodeSkintones', [meta['unicode']])
    for index, code in enumerate(skin):
        glyph = to_glyph(code); tokens.add(glyph)
        tone = tones[index] if len(skin) == 6 else ''
        candidates = [r for r in options if ('/' + (tone or 'Default') + '/3D/') in r['path']]
        if not candidates: candidates = [r for r in options if '/Default/3D/' in r['path']] or options
        row = candidates[0]; filename = row['sha'] + '.webp'; images[row['path']] = filename
        variants.append({'emoji': glyph, 'image': '/emoji/' + filename})
    base = variants[0]
    name = meta.get('cldr', meta['tts']); group = meta['group']
    if group == 'People & Body' and any(word in name for word in ('hand', 'finger', 'thumb', 'fist', 'palm')): group = 'Gestures'
    translation = localized(base['emoji'])
    entries.append({**base, 'source': folder.removeprefix('assets/'), 'name': name, 'zh': translation.get('tts', [name])[0], 'keywords': list(dict.fromkeys(meta.get('keywords', []) + meta.get('glyphAsUtfInEmoticons', []) + translation.get('default', []))), 'group': group, 'variants': variants})
    tokens.add(meta['glyph'])
# Include the exact metadata glyphs (some use explicit variation selectors).
(ROOT / 'backend/app/data/reaction_emojis.json').write_text(json.dumps(sorted(tokens), ensure_ascii=False, indent=2) + '\n')
(OUT / 'catalog.json').write_text(json.dumps(entries, ensure_ascii=False, separators=(',', ':')) + '\n')
(OUT / 'LICENSE-MICROSOFT.txt').write_bytes(fetch(encoded('LICENSE')))
(OUT / 'LICENSE-UNICODE.txt').write_bytes(fetch('https://raw.githubusercontent.com/unicode-org/cldr-json/91c267402229a59e3ef2774544f001bf959e8809/LICENSE'))
print('Shared catalog ready', len(entries), 'entries', len(tokens), 'tokens', len(images), 'images', flush=True)

def image_job(item):
    path, name = item; dest = OUT / name
    if dest.exists(): return
    with Image.open(BytesIO(fetch(encoded(path)))) as image:
        image = image.convert('RGBA'); image.thumbnail((96, 96), Image.Resampling.LANCZOS)
        image.save(dest, 'WEBP', quality=88, method=6)
with ThreadPoolExecutor(max_workers=20) as pool:
    for index, future in enumerate(as_completed([pool.submit(image_job, item) for item in images.items()])):
        future.result()
        if index % 400 == 0: print('Images', index + 1, flush=True)
files = {p.name: sha256(p.read_bytes()).hexdigest() for p in sorted(OUT.iterdir()) if p.is_file() and p.name != 'manifest.json' and not p.name.startswith('animated') and not p.name.endswith('.tmp')}
(OUT / 'manifest.json').write_text(json.dumps({'repository': 'https://github.com/microsoft/fluentui-emoji', 'commit': COMMIT, 'cldr_repository': 'https://github.com/unicode-org/cldr-json', 'cldr_commit': cldr_commit, 'max_image_size': 96, 'entries': len(entries), 'tokens': len(tokens), 'images': len(images), 'files': files}, indent=2) + '\n')
print('Complete', sum(p.stat().st_size for p in OUT.iterdir()), 'bytes', flush=True)
