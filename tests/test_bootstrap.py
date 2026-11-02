import hashlib
import io
import os
from pathlib import Path
import platform
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from scripts import bootstrap


def release_archive(binary=b'exporter-binary', license_text=b'MIT license'):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as archive:
        for name, data in [('nvidia_gpu_exporter', binary), ('LICENSE', license_text)]:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return stream.getvalue()


class Response:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        return False

    def read(self):
        return self.data


class BootstrapTests(unittest.TestCase):
    def test_verified_archive_repairs_a_tampered_cached_binary_without_redownloading(self):
        archive = release_archive()
        key = (platform.system(), platform.machine())
        assets = {key: ('test', hashlib.sha256(archive).hexdigest())}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(bootstrap, 'ROOT', root), patch.object(bootstrap, 'ASSETS', assets), \
                    patch('scripts.bootstrap.urllib.request.urlopen', return_value=Response(archive)) as download, \
                    patch('scripts.bootstrap.subprocess.run') as version:
                bootstrap.install()
                target = root / '.tools' / 'nvidia_gpu_exporter'
                self.assertEqual(target.read_bytes(), b'exporter-binary')
                self.assertEqual(download.call_count, 1)
                target.write_bytes(b'tampered')
                bootstrap.install()
                self.assertEqual(target.read_bytes(), b'exporter-binary')
                self.assertEqual(download.call_count, 1)
                self.assertEqual(version.call_count, 2)

    def test_explicit_custom_exporter_skips_the_download_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            supplied = Path(directory) / 'exporter'
            supplied.write_bytes(b'user build')
            with patch.dict(os.environ, {'GPU_MONITOR_EXPORTER': str(supplied)}), \
                    patch('scripts.bootstrap.subprocess.run') as version:
                self.assertEqual(bootstrap.install(), supplied)
                self.assertEqual(bootstrap.exporter_path(), supplied)
                version.assert_called_once_with([str(supplied), '--version'], check=True)


if __name__ == '__main__':
    unittest.main()
