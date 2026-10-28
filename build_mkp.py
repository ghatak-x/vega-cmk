#!/usr/bin/env python3
"""Build a deterministic Checkmk MKP from this repository's public files.

Keep site-local ~/etc/firetv_vega.json, ~/var/*, SDK binaries, and screenshots
out of src/, docs/, and dist/. Public docs are embedded without duplicate
on-disk source files. Standard MKP parts are bin.tar and web.tar.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
from pathlib import Path
import pprint
import tarfile

ROOT = Path(__file__).resolve().parent
DIST = ROOT / 'dist'
METADATA = ROOT / 'manifest.json'
PUBLIC_DOCS = [ROOT / 'README.md', ROOT / 'CHANGELOG.md', ROOT / 'LICENSE', *sorted((ROOT / 'docs').glob('*.md'))]
PUBLIC_IMAGES = sorted((ROOT / 'docs/images').glob('*.png'))


def payload(part: str) -> dict[str, tuple[bytes, int]]:
    folder = ROOT / 'src' / part
    result = {}
    for item in sorted(folder.rglob('*')):
        if not item.is_file() or '__pycache__' in item.parts or item.suffix == '.pyc':
            continue
        name = item.relative_to(folder).as_posix()
        if part == 'bin' and name.startswith('firetv_docs/'):
            raise ValueError('Documentation is generated, do not duplicate it in src/bin')
        mode = 0o755 if part == 'bin' and (name == 'firetv_agent' or name.endswith('.py')) else 0o644
        result[name] = (item.read_bytes(), mode)
    if part == 'bin':
        for item in PUBLIC_DOCS:
            # Unambiguous installed filenames, including docs/SETUP.md, etc.
            name = 'firetv_docs/' + item.relative_to(ROOT).as_posix()
            if name in result:
                raise ValueError('Duplicate embedded documentation filename: '+name)
            result[name] = (item.read_bytes(), 0o644)
        for item in PUBLIC_IMAGES:
            # Public example screenshots are documented in the README; never include site-private captures.
            if item.name not in ('vega-services.png', 'vega-cpu-pressure.png', 'linux-services.png'):
                raise ValueError('Unexpected documentation screenshot: ' + item.name)
            name = 'firetv_docs/' + item.relative_to(ROOT).as_posix()
            result[name] = (item.read_bytes(), 0o644)
    return dict(sorted(result.items()))


def tar_bytes(files: dict[str, tuple[bytes, int]]) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for name, (data, mode) in sorted(files.items()):
            obj = tarfile.TarInfo(name)
            obj.mode, obj.mtime, obj.uid, obj.gid = mode, 0, 0, 0
            obj.uname, obj.gname, obj.size = '', '', len(data)
            archive.addfile(obj, io.BytesIO(data))
    return out.getvalue()


def main() -> None:
    meta = json.loads(METADATA.read_text(encoding='utf-8'))
    assert meta['name'] == 'firetv_vega_vda'
    assert meta['version'] == '0.0.1'
    assert meta['version.usable_until'] is None
    assert meta['version.min_required'] == '2.5.0'
    DIST.mkdir(parents=True, exist_ok=True)
    name = f"{meta['name']}-{meta['version']}.mkp"
    dest = DIST / name
    parts = {part: payload(part) for part in ('bin', 'web')}
    info = {key: value for key, value in meta.items() if key != 'license'}
    info['files'] = {part: list(files) for part, files in parts.items()}
    inner = {
        'info': pprint.pformat(info, sort_dicts=True, width=100).encode() + b'\n',
        'info.json': json.dumps(info, indent=2, sort_keys=True).encode() + b'\n',
        'bin.tar': tar_bytes(parts['bin']),
        'web.tar': tar_bytes(parts['web']),
    }
    # gzip mtime=0 keeps byte-for-byte reproducible output across rebuilds.
    with dest.open('wb') as f:
        with gzip.GzipFile(fileobj=f, filename='', mode='wb', mtime=0, compresslevel=9) as gz:
            gz.write(tar_bytes({item: (content, 0o644) for item, content in inner.items()}))
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    (DIST / 'SHA256SUMS').write_text(f'{digest}  {dest.name}\n', encoding='ascii')
    print(f'Built {dest} ({dest.stat().st_size} bytes); SHA-256 {digest}')
    for part, files in parts.items():
        print(f'  {part}: {len(files)} files')

if __name__ == '__main__':
    main()
