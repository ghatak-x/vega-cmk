"""Offline smoke tests (no Fire TV / Checkmk access required)."""
from __future__ import annotations
import ast
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "src/bin"
MKP = ROOT / "dist" / "firetv_vega_vda-0.0.1.mkp"

MOCK = r'''#!/usr/bin/env python3
import pathlib,sys
args=sys.argv[1:]
if args[0]=='devices': print('List of devices attached\n192.0.2.10:5555\tdevice'); sys.exit(0)
if args[0]=='connect': sys.exit(0)
if args[0]=='-e': args=args[1:]
if args[0]=='-s': args=args[2:]
if args[0]=='push': sys.exit(0)
if args[0]=='pull':
    pathlib.Path(args[2]).write_bytes(bytes.fromhex('89504e470d0a1a0a')+b'fake-png');sys.exit(0)
if args[:2]==['shell','bash']:
    print('<<<check_mk>>>\nAgentOS: linux\nOSName: OS\n<<<mem>>>\nMemTotal: 1234 kB');sys.exit(0)
if args[:3]==['shell','vpm','list']:
    print('com.netflix.kepler\ncom.amazon.firetv.youtube');sys.exit(0)
if args[:3]==['shell','vpm','info']:
    pkg=args[3]
    print(f'id: {pkg}\nversion: 1.0.0\nbuild number: 1\nenabled: true');sys.exit(0)
if args[:3]==['shell','cat','/proc/pressure/memory']:
    print('some avg10=0.00 avg60=0.12 avg300=0.35 total=19986643\nfull avg10=0.00 avg60=0.00 avg300=0.01 total=15454518');sys.exit(0)
if args[:3]==['shell','cat','/proc/pressure/cpu']:
    print('some avg10=7.82 avg60=8.08 avg300=8.29 total=1164887556\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0');sys.exit(0)
if args[:3]==['shell','cat','/proc/pressure/io']:
    print('some avg10=0.05 avg60=0.18 avg300=0.39 total=60171658\nfull avg10=0.04 avg60=0.10 avg300=0.14 total=37210073');sys.exit(0)
if args[:3]==['shell','vlcm','list']:
    print('com.netflix.kepler.main I 123 1 VISIBLE PERMANENT x\ncom.amazon.minerva S 456 2 BACKGROUND PERMANENT x\ncom.amazon.kcp.component.service S 789 3 BACKGROUND PERMANENT x');sys.exit(0)
if args[:3]==['shell','cat','/etc/os-release']:
    print('OS_VERSION="2.0"\nVERSION_NUMBER="2602000000000"\nBUILD_DESC="OS 2.0"');sys.exit(0)
if args[:3]==['shell','loggingctl','log']: sys.exit(0)
if args[:2]==['shell','gwsi-tool-screenshooter']: sys.exit(0)
if args[:2]==['shell','rm']: sys.exit(0)
print('unknown fake vda',args,file=sys.stderr);sys.exit(1)
'''


class TestPackage(unittest.TestCase):
    def test_archive_manifest(self):
        with tarfile.open(MKP) as f:
            names=set(f.getnames())
            self.assertEqual(names, {'info','info.json','bin.tar','web.tar'})
            manifest=ast.literal_eval(f.extractfile('info').read().decode())
            self.assertEqual(manifest['author'], 'Sudhir Chauhan')
            self.assertEqual(manifest['download_url'], 'https://github.com/ghatak-x/vega-cmk')
            self.assertEqual(manifest['files']['bin'].count('firetv_docs/docs/SETUP.md'), 1)
            self.assertIn('firetv_docs/LICENSE', manifest['files']['bin'])
            self.assertEqual(manifest['version'], '0.0.1')
            self.assertEqual(manifest['version.min_required'], '2.5.0')
            self.assertIsNone(manifest['version.usable_until'])
            self.assertEqual(manifest,json.loads(f.extractfile('info.json').read()))
            for part in ('bin','web'):
                with tarfile.open(fileobj=io.BytesIO(f.extractfile(part+'.tar').read())) as sub:
                    self.assertEqual(set(sub.getnames()),set(manifest['files'][part]))
                    self.assertTrue(all(not x.endswith(('.pyc','.bak','.json')) for x in sub.getnames()))
            import re
            # Ensure package manifest does not accidentally name a private LAN host
            # or a real developer's home directory. Documentation IPs use TEST-NET.
            self.assertIsNone(re.search(r'\b192\.168\.\d+\.\d+\b', str(manifest)))
            self.assertNotIn('/home/', str(manifest))
            self.assertEqual(json.loads((ROOT/'manifest.json').read_text())['version'], manifest['version'])
            self.assertFalse((ROOT/'src/bin/firetv_docs').exists())

    def test_capture_troubleshooting_preserves_privacy_and_nonintrusive_behavior(self):
        page=(ROOT/'src/web/plugins/pages/firetv_checkmk_screen_page.py').read_text()
        known=(ROOT/'docs/KNOWN_ISSUES.md').read_text()
        self.assertIn('Try pausing the video and capturing again', page)
        self.assertIn('previous image preserved', page)
        self.assertIn('pausing the', known)
        self.assertIn('must not automatically pause', known)
        self.assertNotRegex(known, r'\b192\.168\.\d+\.\d+\b')

    def test_deployment_support_is_documented_correctly(self):
        readme=(ROOT/'README.md').read_text()
        setup=(ROOT/'docs/SETUP.md').read_text()
        known=(ROOT/'docs/KNOWN_ISSUES.md').read_text()
        self.assertIn('same Linux host', readme)
        self.assertIn('TCP 6556', setup)
        self.assertIn('Unsupported', setup)
        self.assertIn('Not implemented in v0.0.1', known)
        with tarfile.open(MKP) as package:
            with tarfile.open(fileobj=io.BytesIO(package.extractfile('bin.tar').read())) as binaries:
                embedded=binaries.extractfile('firetv_docs/docs/SETUP.md').read().decode()
        self.assertIn('## Supported deployment models for v0.0.1', embedded)
        self.assertIn('TCP 6556', embedded)

    def test_public_screenshots_in_readme_and_mkp(self):
        images = [
            'vega-services.png',
            'vega-cpu-pressure.png',
            'linux-services.png',
        ]
        readme = (ROOT/'README.md').read_text()
        ignore = (ROOT/'.gitignore').read_text()
        with tarfile.open(MKP) as package:
            with tarfile.open(fileobj=io.BytesIO(package.extractfile('bin.tar').read())) as binaries:
                for image in images:
                    source = (ROOT / 'docs/images' / image).read_bytes()
                    self.assertTrue(source.startswith(bytes.fromhex('89504e470d0a1a0a')))
                    self.assertIn(f'docs/images/{image}', readme)
                    self.assertIn(f'!docs/images/{image}', ignore)
                    embedded = binaries.extractfile(f'firetv_docs/docs/images/{image}').read()
                    self.assertEqual(source, embedded)
                self.assertEqual(readme.encode(), binaries.extractfile('firetv_docs/README.md').read())

    def test_embedded_documentation_links(self):
        # The installed docs must retain the source relative path structure,
        # otherwise README.md links that work on GitHub break inside an MKP.
        import re
        import posixpath
        from urllib.parse import unquote
        with tarfile.open(MKP) as package:
            with tarfile.open(fileobj=io.BytesIO(package.extractfile('bin.tar').read())) as binaries:
                members = set(binaries.getnames())
                doc_names = [n for n in members if n.startswith('firetv_docs/') and n.endswith('.md')]
                for name in doc_names:
                    content = binaries.extractfile(name).read().decode('utf-8')
                    targets = re.findall(r'\]\(([^)]+)\)', content)
                    targets += re.findall(r'<img\s+[^>]*src="([^"]+)"', content)
                    for target in targets:
                        target = target.split('#', 1)[0].strip()
                        if not target or '://' in target or target.startswith(('mailto:', '#')):
                            continue
                        relative = unquote(target)
                        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), relative))
                        self.assertIn(resolved, members, f'Broken embedded link in {name}: {target}')

    def test_fake_device_integration(self):
        with tempfile.TemporaryDirectory(prefix='vega-smoke-') as tmp:
            home=Path(tmp)
            (home/'etc').mkdir()
            (home/'share/check_mk/agents').mkdir(parents=True)
            (home/'share/check_mk/agents/check_mk_agent.linux').write_text('#!/bin/bash\n')
            mock=home/'vda'
            mock.write_text(MOCK)
            mock.chmod(0o700)
            cfg={'device':'192.0.2.10:5555','vda_path':str(mock),
                 'apps':{'Netflix':'com.netflix.kepler','YouTube':'com.amazon.firetv.youtube'}}
            setting=home/'etc/firetv_vega.json'
            setting.write_text(json.dumps(cfg))
            setting.chmod(0o600)
            local=home/'local/bin'
            local.mkdir(parents=True)
            for src in BIN.iterdir():
                if src.is_file():
                    (local/src.name).write_bytes(src.read_bytes())
            env={**os.environ,'OMD_ROOT':str(home),'HOME':str(home)}
            cmd=[sys.executable,str(local/'firetv_agent')]
            proc=subprocess.run(cmd,env=env,capture_output=True,text=True,timeout=20)
            self.assertEqual(proc.returncode,0,proc.stderr)
            self.assertIn('OSName: Vega OS',proc.stdout)
            self.assertEqual(proc.stdout.count('<<<local>>>'),1)
            self.assertIn('"Vega screen activity"',proc.stdout)
            self.assertIn('"Vega package inventory"',proc.stdout)
            self.assertIn('"Vega OS build"',proc.stdout)
            self.assertIn('"Vega memory pressure"',proc.stdout)
            self.assertIn('vega_mem_psi_some_avg10_pct=0.00', proc.stdout)
            self.assertIn('"Vega CPU pressure"', proc.stdout)
            self.assertIn('"Vega I/O pressure"', proc.stdout)
            self.assertIn('vega_cpu_psi_some_avg10_pct=7.82', proc.stdout)
            self.assertIn('vega_io_psi_full_avg300_pct=0.14', proc.stdout)
            self.assertNotIn('vega_cpu_psi_full', proc.stdout)
            self.assertNotIn('192.0.2.10',proc.stdout)
            shot=subprocess.run([sys.executable,str(local/'firetv_snapshot_v2.py')],env=env,capture_output=True,text=True,timeout=20)
            self.assertEqual(shot.returncode,0,shot.stderr)
            private=home/'var/firetv_private/now.png'
            self.assertEqual(private.read_bytes()[:8],bytes.fromhex('89504e470d0a1a0a'))
            self.assertEqual(private.stat().st_mode & 0o777,0o600)
            # A publicly readable private configuration must fail closed.
            setting.chmod(0o644)
            bad=subprocess.run(cmd,env=env,capture_output=True,text=True,timeout=10)
            self.assertNotEqual(bad.returncode,0)
            self.assertNotIn('192.0.2.10',bad.stderr)

if __name__=='__main__': unittest.main()
