"""Build only the two reviewed first-party dependencies from immutable public Git commits."""

import argparse
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

EXPECTED = {'byteforge-mcp-file-friend-models', 'byteforge-mcp-file-friend-core'}


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
            r'(byteforge-mcp-file-friend-(?:models|core)) @ '
            r'git\+https://github.com/jmazzahacks/\1\.git@([0-9a-f]{40})', dependency
        )
        if match is None:
            raise ValueError('Unexpected internal dependency; review its URL and immutable revision')
        dependencies.append(match.groups())
    if {name for name, _ in dependencies} != EXPECTED or len(dependencies) != 2:
        raise ValueError('Expected exactly models and core dependencies')
    output = args.out_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ff-internal-build-') as temporary:
        for name, revision in dependencies:
            checkout = Path(temporary) / name
            subprocess.run(['git', 'init', '--quiet', str(checkout)], check=True)
            subprocess.run(
                ['git', '-C', str(checkout), 'fetch', '--quiet', '--depth=1',
                 f'https://github.com/jmazzahacks/{name}.git', revision], check=True
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
