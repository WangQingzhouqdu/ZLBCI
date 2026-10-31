"""Validate the generated site without third-party dependencies."""
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urlsplit, unquote
import json, hashlib, collections, zipfile

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'docs'
errors = []
class Page(HTMLParser):
    def __init__(self, text):
        super().__init__(); self.links=[]; self.ids=[]; self.h1=0; self.feed(text)
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if 'id' in a: self.ids.append(a['id'])
        if tag=='h1': self.h1+=1
        for key in ['href','src','poster']:
            if a.get(key): self.links.append(a[key])

parsed={p:Page(p.read_text(encoding='utf-8')) for p in SITE.glob('*.html')}
checked=0
for path,page in parsed.items():
    if page.h1!=1: errors.append(f'{path.name}: expected one h1')
    duplicates=[x for x,n in collections.Counter(page.ids).items() if n>1]
    if duplicates: errors.append(f'{path.name}: duplicate IDs {duplicates}')
    for link in page.links:
        url=urlsplit(link)
        if url.scheme or url.netloc: continue
        if url.path.startswith('/'): errors.append(f'{path.name}: root-relative link {link}')
        target=(path.parent/unquote(url.path)).resolve() if url.path else path
        if not target.exists(): errors.append(f'{path.name}: missing {link}')
        elif url.fragment and target in parsed and unquote(url.fragment) not in parsed[target].ids: errors.append(f'{path.name}: missing anchor {link}')
        checked+=1
manifest=json.loads((SITE/'downloads/manifest.json').read_text(encoding='utf-8'))
for item in manifest:
    p=SITE/'downloads'/item['file']
    if hashlib.sha256(p.read_bytes()).hexdigest()!=item['sha256']: errors.append(f'Hash mismatch {p.name}')
    if p.stat().st_size!=item['bytes']: errors.append(f'Size mismatch {p.name}')
for p in SITE.rglob('*'):
    if p.is_file() and p.stat().st_size>=25*1024*1024: errors.append(f'Above web upload limit {p.name}')
for p in (SITE/'downloads').glob('*.zip'):
    with zipfile.ZipFile(p) as z:
        if z.testzip(): errors.append(f'Corrupt archive {p.name}')
        for name in z.namelist():
            if name.endswith(('.jks','.keystore','.p12','.mobileprovision')) or 'signing.properties' in name or 'local.properties' in name or '/node_modules/' in name: errors.append(f'Excluded file in archive {p.name}: {name}')
if errors:
    print('\n'.join(errors)); raise SystemExit(1)
print(f'PASS: {len(parsed)} HTML pages, {checked} local references, {len(manifest)} download checksums; no duplicate IDs, broken resources or oversized files.')
