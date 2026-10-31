"""Create a GitHub-ready ZIP without an extra enclosing folder."""
from pathlib import Path
import zipfile, sys

ROOT = Path(__file__).resolve().parents[1]
destination = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / 'bci-docs-site.zip'
files = []
for folder in ['docs', 'content', 'scripts']:
    files.extend(p for p in (ROOT / folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
files.extend(ROOT / name for name in ['README.md', '.gitignore'])
with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for path in sorted(files): archive.write(path, path.relative_to(ROOT).as_posix())
with zipfile.ZipFile(destination) as archive:
    assert archive.testzip() is None
    assert 'docs/index.html' in archive.namelist()
    assert 'docs/.nojekyll' in archive.namelist()
print(f'{destination}\n{len(files)} files; {destination.stat().st_size:,} bytes; archive integrity verified.')
