import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import manage
from scripts.manage import generated_config, server_name, ssh_aliases, write_config


class ManageTests(unittest.TestCase):
    def test_ssh_aliases_ignore_patterns_and_keep_concrete_hosts(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config'
            includes = Path(directory) / 'hosts.d'
            includes.mkdir()
            (includes / 'research').write_text('Host gpu-03\n  User researcher\n')
            config.write_text('''
Host *
  ServerAliveInterval 30
Include hosts.d/*
Host gpu-01 gpu-02
  User researcher
Host jump-?
  HostName example.invalid
Host gpu-01
  ForwardAgent no
''')
            self.assertEqual(ssh_aliases(config), ['gpu-03', 'gpu-01', 'gpu-02'])

    def test_generated_configuration_needs_no_exporter_ports(self):
        settings = generated_config(['research@alpha.example', 'alpha.example'], 8765, ['researcher'])
        self.assertEqual(settings['servers'], [
            {'name': 'alpha-example', 'ssh_host': 'research@alpha.example'},
            {'name': 'alpha-example-2', 'ssh_host': 'alpha.example'},
        ])
        self.assertTrue(all('exporter_port' not in host for host in settings['servers']))
        self.assertEqual(server_name('gpu-01', {'gpu-01'}), 'gpu-01-2')

    def test_generated_config_is_private_json(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'config.json'
            write_config(target, generated_config(['gpu-01'], 8765, ['demo']))
            self.assertEqual(json.loads(target.read_text())['servers'][0]['ssh_host'], 'gpu-01')
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_init_writes_a_discovered_private_configuration_without_manual_ports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = argparse.Namespace(hosts=[], force=False, add=False, yes=True)
            results = [
                {'host': 'gpu-01', 'gpu': True, 'ps': True, 'reason': ''},
                {'host': 'not-a-gpu', 'gpu': False, 'ps': False, 'reason': 'nvidia-smi did not return a GPU'},
            ]
            with patch.object(manage, 'ROOT', root), patch('scripts.manage.shutil.which', return_value='/usr/bin/ssh'), \
                    patch.object(manage, 'ssh_aliases', return_value=['gpu-01', 'not-a-gpu']), \
                    patch.object(manage, 'probe_host', side_effect=results), \
                    patch.object(manage, 'effective_ssh_user', return_value='demo'), \
                    patch.object(manage, 'available_port', return_value=18765):
                manage.initialize(args)
            settings = json.loads((root / 'config.json').read_text())
            self.assertEqual(settings['port'], 18765)
            self.assertEqual(settings['own_users'], ['demo'])
            self.assertEqual(settings['servers'], [{'name': 'gpu-01', 'ssh_host': 'gpu-01', 'own_users': ['demo']}])

    def test_init_add_merges_only_new_discovered_hosts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_config(root / 'config.json', generated_config(['gpu-01'], 8765, ['first']))
            args = argparse.Namespace(hosts=['gpu-02'], force=False, add=True, yes=True)
            result = {'host': 'gpu-02', 'gpu': True, 'ps': True, 'reason': ''}
            with patch.object(manage, 'ROOT', root), patch.object(manage, 'read_state', return_value=None), \
                    patch('scripts.manage.shutil.which', return_value='/usr/bin/ssh'), \
                    patch.object(manage, 'probe_host', return_value=result), \
                    patch.object(manage, 'effective_ssh_user', return_value='second'):
                manage.initialize(args)
            settings = json.loads((root / 'config.json').read_text())
            self.assertEqual(settings['port'], 8765)
            self.assertEqual(settings['own_users'], ['first', 'second'])
            self.assertEqual(settings['servers'], [
                {'name': 'gpu-01', 'ssh_host': 'gpu-01'},
                {'name': 'gpu-02', 'ssh_host': 'gpu-02', 'own_users': ['second']},
            ])


if __name__ == '__main__':
    unittest.main()
