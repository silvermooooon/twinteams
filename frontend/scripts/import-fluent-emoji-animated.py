"""Pinned MIT Fluent APNG -> local 96px animated WebP, no repository LFS clone.
Downloads are bounded and originals are released after conversion. Reruns reuse
verified converted files. Static import must run first. All source frames keep
their durations and loop value; the optional APNG poster is not an animation frame.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import time
import urllib.parse
import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'frontend/public/emoji'
COMMIT = 'daa0365c09795789ed2bc6e8b228c97736cb6669'
CLIENT = httpx.Client(follow_redirects=True, timeout=120, limits=httpx.Limits(max_connections=8, max_keepalive_connections=8))

def fetch(url):
    for attempt in range(8):
        try:
            response = CLIENT.get(url); response.raise_for_status(); return response.content
        except Exception:
            if attempt == 7: raise
            time.sleep(min(2 ** attempt, 8))

tree_file = Path('/tmp/twinteams-fluent-animated-tree.json')
tree = json.loads(tree_file.read_text()) if tree_file.exists() else json.loads(fetch(f'https://api.github.com/repos/microsoft/fluentui-emoji-animated/git/trees/{COMMIT}?recursive=1'))
assert tree['sha'] == COMMIT and not tree.get('truncated')
rows = [row for row in tree['tree'] if row['path'].endswith('.png') and '/animated/' in row['path']]
entries = json.loads((OUT / 'catalog.json').read_text())
by_source = {entry['source']: entry for entry in entries}
# Upstream repositories use two different English folder labels for the same emoji.
for animated, static in {'Black bird': 'Blackbird', 'Phoenix': 'Phoenix bird'}.items():
    by_source[animated] = by_source[static]
priority = {'Thumbs up', 'Red heart', 'Grinning squinting face', 'Face with open mouth', 'Face with tears of joy', 'Crying face', 'Angry face', 'Clapping hands', 'Party popper'}
rows.sort(key=lambda row: (row['path'].split('/')[1] not in priority, '/Default/' not in row['path'], row['path']))
manifest_file = OUT / 'animated-manifest.json'
manifest = json.loads(manifest_file.read_text()) if manifest_file.exists() else {'repository': 'https://github.com/microsoft/fluentui-emoji-animated', 'commit': COMMIT, 'files': {}}
assert manifest['commit'] == COMMIT

def job(row):
    name = 'animated-' + row['sha'] + '.webp'; destination = OUT / name
    old = manifest['files'].get(row['path'])
    if old and destination.exists() and sha256(destination.read_bytes()).hexdigest() == old['sha256']: return row, old
    raw = fetch('https://media.githubusercontent.com/media/microsoft/fluentui-emoji-animated/' + COMMIT + '/' + urllib.parse.quote(row['path']))
    original_hash = sha256(raw).hexdigest(); frames = []; durations = []
    with Image.open(BytesIO(raw)) as source:
        loop = source.info.get('loop', 0)
        start = 1 if source.info.get('default_image') else 0
        for index in range(start, source.n_frames):
            source.seek(index); frame = source.convert('RGBA'); frame.thumbnail((96, 96), Image.Resampling.LANCZOS)
            frames.append(frame.copy()); durations.append(source.info.get('duration', 100))
    del raw
    if len(frames) < 2: raise RuntimeError('Expected animation: ' + row['path'])
    frames[0].save(destination, 'WEBP', save_all=True, append_images=frames[1:], duration=durations, loop=loop, quality=70, method=3, minimize_size=True, allow_mixed=True)
    result = {'file': name, 'source_blob': row['sha'], 'source_sha256': original_hash, 'sha256': sha256(destination.read_bytes()).hexdigest(), 'frames': len(frames), 'duration_ms': sum(durations), 'loop': loop, 'frame_durations_ms': durations}
    return row, result

def publish():
    # Only known, fully converted local files become renderable catalog animations.
    mapped = 0; unmapped = []
    tones = {'Default': 0, 'Light': 1, 'Medium-Light': 2, 'Medium': 3, 'Medium-Dark': 4, 'Dark': 5}
    for path, record in manifest['files'].items():
        parts = path.split('/'); entry = by_source.get(parts[1])
        if not entry:
            unmapped.append({"source": path, "reason": "missing_static_entry"}); continue
        index = tones.get(parts[2], 0)
        if index >= len(entry['variants']):
            unmapped.append({"source": path, "reason": "skin_tone_not_in_static_unicode_metadata"}); continue
        entry['variants'][index]['animated'] = '/emoji/' + record['file']
        entry['variants'][index]['duration_ms'] = record['duration_ms']; mapped += 1
    manifest['total_upstream'] = len(rows); manifest['converted'] = len(manifest['files']); manifest['mapped'] = mapped; manifest['unmapped'] = unmapped
    def atomic_json(path, value):
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n')
        temporary.replace(path)
    atomic_json(manifest_file, manifest)
    atomic_json(OUT / 'catalog.json', entries)
    static_manifest = OUT / 'manifest.json'
    if static_manifest.exists():
        value = json.loads(static_manifest.read_text())
        value['files'] = {name: digest for name, digest in value['files'].items() if not name.startswith('animated')}
        value['files']['catalog.json'] = sha256((OUT / 'catalog.json').read_bytes()).hexdigest()
        atomic_json(static_manifest, value)

(OUT / 'LICENSE-MICROSOFT-ANIMATED.txt').write_bytes(fetch(f'https://raw.githubusercontent.com/microsoft/fluentui-emoji-animated/{COMMIT}/LICENSE'))
print('Animated pipeline', len(rows), 'files', flush=True)
with ThreadPoolExecutor(max_workers=6) as pool:
    for index, future in enumerate(as_completed([pool.submit(job, row) for row in rows])):
        row, result = future.result(); manifest['files'][row['path']] = result
        if index % 24 == 0:
            publish(); print('Animated', len(manifest['files']), '/', len(rows), flush=True)
publish()
print('Animated complete', manifest['converted'], 'mapped', manifest['mapped'], 'bytes', sum((OUT / row['file']).stat().st_size for row in manifest['files'].values()), flush=True)
