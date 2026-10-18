"""Verify local Fluent catalogs and actual animation frames (no network/model)."""
import json
import os
from hashlib import sha256
from pathlib import Path

from PIL import Image

root = Path(__file__).resolve().parents[2]
assets = Path(os.environ.get('TWINTEAMS_EMOJI_DIR', str(root / 'frontend/public/emoji')))
catalog = json.loads((assets / 'catalog.json').read_text())
tokens = set(json.loads((root / 'backend/app/data/reaction_emojis.json').read_text()))
manifest = json.loads((assets / 'animated-manifest.json').read_text())
assert len(catalog) >= 1500 and len(tokens) >= 3000
variants = [variant for entry in catalog for variant in entry['variants']]
assert all(variant['emoji'] in tokens for variant in variants)
for variant in variants:
    assert (assets / Path(variant['image']).name).is_file()
assert manifest['converted'] == manifest['total_upstream'] == 2241
static_manifest = json.loads((assets / 'manifest.json').read_text())
for name, expected in static_manifest['files'].items():
    assert sha256((assets / name).read_bytes()).hexdigest() == expected, name
for record in manifest['files'].values():
    assert sha256((assets / record['file']).read_bytes()).hexdigest() == record['sha256'], record['file']
report = []
for emoji in ('👍', '❤️', '😆', '😮'):
    entry = next(entry for entry in catalog if entry['emoji'] == emoji)
    variant = entry['variants'][0]
    path = assets / Path(variant['animated']).name
    record = next(row for row in manifest['files'].values() if row['file'] == path.name)
    assert sha256(path.read_bytes()).hexdigest() == record['sha256']
    with Image.open(path) as image:
        assert image.n_frames > 1 and image.info.get('loop') == record['loop']
        durations = []
        for frame in range(image.n_frames):
            image.seek(frame)
            image.load()
            durations.append(image.info['duration'])
        actual = sum(durations)
        # WebP stores integer-ms frame durations and may combine identical frames.
        assert abs(actual - record['duration_ms']) <= record['frames']
        assert variant['duration_ms'] == record['duration_ms']
        report.append({'emoji':emoji, 'frames':image.n_frames, 'duration_ms':actual,
                       'loop':image.info['loop'], 'hash_matches':True})
result = {'catalog_entries':len(catalog),'tokens':len(tokens),'animated_converted':manifest['converted'],
          'animated_total':manifest['total_upstream'],'all_manifest_hashes_verified':True,'defaults':report}
Path('/tmp/twinteams-reaction-assets-report.json').write_text(json.dumps(result, indent=2, ensure_ascii=False))
print(json.dumps(result, indent=2, ensure_ascii=False))
