"""Build reviewed Git dependencies from explicit immutable public commits."""

import argparse
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

OWNERS = {'byteforge-mcp-file-friend-models': 'jmazzahacks',
          'byteforge-mcp-file-friend-core': 'jmazzahacks',
          'byteforge-loki-logging': 'jmazzahacks', 'python-logging-loki': 'waylayio'}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', type=Path, default=Path('dist'))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((root / 'pyproject.toml').read_text())
    dependencies = []
    for dependency in metadata['project']['dependencies']:
        if ' @ ' not in dependency:
            continue
        match = re.fullmatch(
            r'([a-z-]+) @ git\+https://github.com/([a-z]+)/\1\.git@([0-9a-f]{40})', dependency
        )
        if match is None or OWNERS.get(match[1]) != match[2]:
            raise ValueError('Unexpected internal dependency; review its URL and immutable revision')
        dependencies.append((match[1], match[3]))
    if {name for name, _ in dependencies} != set(OWNERS) or len(dependencies) != len(OWNERS):
        raise ValueError('Expected exactly the reviewed dependency set')
    output = args.out_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ff-internal-build-') as temporary:
        for name, revision in dependencies:
            checkout = Path(temporary) / name
            subprocess.run(['git', 'init', '--quiet', str(checkout)], check=True)
            subprocess.run(
                ['git', '-C', str(checkout), 'fetch', '--quiet', '--depth=1',
                 f'https://github.com/{OWNERS[name]}/{name}.git', revision], check=True
            )
            subprocess.run(['git', '-C', str(checkout), 'checkout', '--quiet', '--detach', 'FETCH_HEAD'], check=True)
            actual = subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], text=True).strip()
            if actual != revision:
                raise RuntimeError('Fetched revision differs from the reviewed dependency')
            subprocess.run(
                ['uv', 'build', str(checkout), '--wheel', '--no-build-isolation', '--python', sys.executable,
                 '--out-dir', str(output)], check=True
            )


if __name__ == '__main__':
    main()
