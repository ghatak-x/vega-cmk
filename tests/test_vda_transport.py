"""Offline regression tests for the VDA multi-site device-selection failure."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / 'src/bin'
MOCK = '''#!/usr/bin/env python3
import os, sys
args=sys.argv[1:]
with open(os.environ['MOCK_VDA_CALLS'],'a') as f: f.write(' '.join(args)+'\\n')
if args == ['devices']:
    print('List of devices attached')
    print('192.0.2.10:5555\\tdevice')
    if os.getenv('MOCK_TWO_DEVICES') == '1':
        print('192.0.2.11:5555\\tdevice')
    sys.exit(0)
if args[0] == 'connect':
    print('connect should never happen when already listed',file=sys.stderr)
    sys.exit(66)
if args[:2] == ['-s','192.0.2.10:5555']:
    if os.getenv('MOCK_S_REJECTS') == '1':
        print("vda: device '192.0.2.10:5555' not found",file=sys.stderr)
        sys.exit(1)
    args=args[2:]
elif args[0] == '-e':
    args=args[1:]
else:
    print('unexpected target',file=sys.stderr);sys.exit(2)
if args[:2]==['shell','echo']:
    print(' '.join(args[2:]));sys.exit(0)
if args[:3]==['shell','vlcm','list']:
    print('com.netflix.kepler.main I 123 1 VISIBLE PERMANENT x');sys.exit(0)
print('unsupported',file=sys.stderr);sys.exit(3)
'''

class TestVDASelection(unittest.TestCase):
    def make_case(self, temp: Path, env: dict[str,str]) -> tuple[list[str], dict[str,str]]:
        (temp/'etc').mkdir()
        mock=temp/'vda'; mock.write_text(MOCK); mock.chmod(0o700)
        config={'device':'192.0.2.10:5555','vda_path':str(mock)}
        path=temp/'etc/firetv_vega.json';path.write_text(json.dumps(config));path.chmod(0o600)
        (temp/'local/bin').mkdir(parents=True)
        for p in BIN.iterdir():
            if p.is_file(): (temp/'local/bin'/p.name).write_bytes(p.read_bytes())
        calls=temp/'calls.log'
        env={**os.environ,**env,'OMD_ROOT':str(temp),'HOME':str(temp),'MOCK_VDA_CALLS':str(calls)}
        return [sys.executable,str(temp/'local/bin/firetv_vega_extras.py')], env

    def test_listed_device_rejects_s_uses_guarded_e(self):
        with tempfile.TemporaryDirectory() as t:
            command,env=self.make_case(Path(t),{'MOCK_S_REJECTS':'1'})
            p=subprocess.run(command+['--list-components'],env=env,capture_output=True,text=True,timeout=12)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertIn('com.netflix.kepler.main',p.stdout)
            calls=(Path(t)/'calls.log').read_text()
            self.assertIn('-e shell vlcm list',calls)
            self.assertNotIn('connect',calls)

    def test_multiple_tcp_targets_never_use_e(self):
        with tempfile.TemporaryDirectory() as t:
            command,env=self.make_case(Path(t),{'MOCK_S_REJECTS':'1','MOCK_TWO_DEVICES':'1'})
            p=subprocess.run(command+['--list-components'],env=env,capture_output=True,text=True,timeout=12)
            self.assertNotEqual(p.returncode,0)
            self.assertIn('TCP/IP devices listed: 2',p.stderr)
            self.assertNotIn('192.0.2.10',p.stderr)
            calls=(Path(t)/'calls.log').read_text()
            self.assertNotIn('-e',calls)
            self.assertNotIn('connect',calls)

    def test_diagnose_read_only_redacted(self):
        with tempfile.TemporaryDirectory() as t:
            command,env=self.make_case(Path(t),{'MOCK_S_REJECTS':'1'})
            p=subprocess.run(command+['--diagnose'],env=env,capture_output=True,text=True,timeout=12)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertIn('Target present and online: True',p.stdout)
            self.assertIn('selected shell: exit=1',p.stdout)
            self.assertIn('single TCP fallback (-e): exit=0',p.stdout)
            self.assertNotIn('192.0.2.10',p.stdout)
            self.assertNotIn('connect', (Path(t)/'calls.log').read_text())

    def test_explicit_site_port(self):
        sys.path.insert(0,str(BIN))
        from firetv_vda import VDAClient
        os.environ['VEGA_VDA_SERVER_PORT']='5038'
        try:
            self.assertNotIn('VEGA_VDA_SERVER_PORT',VDAClient('/bin/false','192.0.2.10:5555').env)
            self.assertEqual(VDAClient('/bin/false','192.0.2.10:5555',5039).env['VEGA_VDA_SERVER_PORT'],'5039')
        finally:
            os.environ.pop('VEGA_VDA_SERVER_PORT',None)

if __name__ == '__main__': unittest.main()
