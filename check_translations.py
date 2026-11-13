import re
from pathlib import Path

source = Path('static/i18n.js').read_text(encoding='utf-8')
# Keys are quoted consistently in both language objects in this small dictionary.
parts = re.search(r"const dict=\{en:\{(.*?)\},hi:\{(.*?)\}\};", source, re.S)
if not parts:
    raise SystemExit('Could not find English/Hindi dictionaries in static/i18n.js')
keys = [set(a or b for a, b in re.findall(r"(?:'([^']+)'|\"([^\"]+)\")\s*:", part)) for part in parts.groups()]
missing_en, missing_hi = keys[1] - keys[0], keys[0] - keys[1]
print(f'English keys: {len(keys[0])}; Hindi keys: {len(keys[1])}')
print('Missing English:', sorted(missing_en) or 'none')
print('Missing Hindi:', sorted(missing_hi) or 'none')
raise SystemExit(bool(missing_en or missing_hi))
