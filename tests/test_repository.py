"""Checks for accidental engineering-data and local-configuration publication."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import check_repository


class RepositoryChecksTests(unittest.TestCase):
    def test_forced_staging_cannot_bypass_data_exclusions(self):
        names = [
            'catia/customer.CATPart', 'tests/fixtures/customer.RAY',
            'docs/customer.pdf', 'examples/mcp.local.json',
            'work/results.py', 'lucidshape_mcp/../work/config.py',
            'workspace/scripts/private_experiment.py',
            'local-data/scripts/private_experiment.py',
            'scripts/private_experiment.py', 'LICENSE.CATPart',
        ]
        records, issues = check_repository.inspect(names)
        self.assertEqual(records, [])
        self.assertEqual({name for name, _ in issues}, set(names))
        self.assertTrue(all(reason == 'outside source allowlist' for _, reason in issues))

    def test_screening_detects_local_paths_and_tokens_without_printing_them(self):
        samples = [
            ('C:' + '/Users/' + 'example/config.json', 'personal absolute path'),
            ('/' + 'home' + '/example/config.json', 'personal absolute path'),
            ('ghp_' + 'x' * 30, 'access token'),
            ('-' * 5 + 'BEGIN PRIVATE KEY' + '-' * 5, 'private key'),
        ]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'docs' / 'example.md'
            source.parent.mkdir()
            with patch.object(check_repository, 'ROOT', root):
                for text, reason in samples:
                    with self.subTest(reason=reason):
                        source.write_text(text, encoding='utf-8')
                        _, issues = check_repository.inspect(['docs/example.md'])
                        self.assertEqual(issues, [('docs/example.md', reason)])
                source.write_text('C:/tools/python.exe', encoding='utf-8')
                _, issues = check_repository.inspect(['docs/example.md'])
                self.assertEqual(issues, [])

    def test_checker_and_synthetic_fixtures_are_publishable(self):
        names = ['scripts/check_repository.py', *sorted(check_repository.FIXTURES)]
        records, issues = check_repository.inspect(names)
        self.assertEqual(issues, [])
        self.assertEqual(len(records), len(names))


if __name__ == '__main__':
    unittest.main()
