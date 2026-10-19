"""Set fork-specific metadata before publishing: python scripts/configure_repository.py OWNER/REPO"""
import argparse
import json
from pathlib import Path
import re

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('repository')
args = parser.parse_args()
if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+', args.repository):
    parser.error('Expected GitHub OWNER/REPO')
root = Path(__file__).resolve().parents[1]
p = root / 'custom_components/kocom_wallpad/manifest.json'
data = json.loads(p.read_text())
owner = args.repository.split('/')[0]
data['documentation'] = 'https://github.com/' + args.repository
data['issue_tracker'] = data['documentation'] + '/issues'
data['codeowners'] = ['@' + owner]
p.write_text(json.dumps(data, ensure_ascii=False, indent=4) + '\n')
print('Updated fork metadata:', args.repository)
