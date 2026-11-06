"""Installer output regressions; dependency installation is mocked."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import bootstrap


class BootstrapOutputTests(unittest.TestCase):
    def exercise(self, encoding, doctor_code=0):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / '下载 with spaces'
            config = root / 'config'
            config.mkdir(parents=True)
            stdout_bytes, stderr_bytes = io.BytesIO(), io.BytesIO()
            stdout = io.TextIOWrapper(stdout_bytes, encoding=encoding, errors='strict')
            stderr = io.TextIOWrapper(stderr_bytes, encoding=encoding, errors='strict')

            def run(command, **kwargs):
                if 'doctor' in command:
                    return subprocess.CompletedProcess(command, doctor_code,
                        json.dumps({'warnings': ['路径提示']}, ensure_ascii=False), '检查失败' if doctor_code else '')
                return subprocess.CompletedProcess(command, 0, 'fixture==1\n', '')

            argv = ['bootstrap.py', '--minimal', '--venv', str(root / 'venv'),
                    '--config-dir', str(config), '--work-dir', str(root / 'jobs')]
            with patch.dict(os.environ, {}, clear=True), patch.object(sys, 'argv', argv), \
                    patch.object(sys, 'stdout', stdout), patch.object(sys, 'stderr', stderr), \
                    patch.object(bootstrap.venv.EnvBuilder, 'create'), \
                    patch.object(bootstrap.subprocess, 'run', side_effect=run):
                code = bootstrap.main()
            stdout.flush(); stderr.flush()
            return code, stdout_bytes.getvalue(), stderr_bytes.getvalue()

    def test_success_under_cp1252_and_utf8_with_unicode_paths(self):
        for encoding in ('cp1252', 'utf-8'):
            with self.subTest(encoding=encoding):
                code, stdout, stderr = self.exercise(encoding)
                self.assertEqual(code, 0)
                self.assertIn('下载 with spaces'.encode(encoding, 'backslashreplace'), stdout)
                self.assertIn('路径提示'.encode(encoding, 'backslashreplace'), stdout)
                self.assertIn(b'Setup complete.', stdout)
                self.assertEqual(stderr, b'')

    def test_doctor_error_survives_cp1252_stderr(self):
        code, _, stderr = self.exercise('cp1252', doctor_code=7)
        self.assertEqual(code, 7)
        self.assertIn('检查失败'.encode('cp1252', 'backslashreplace'), stderr)


if __name__ == '__main__':
    unittest.main()
