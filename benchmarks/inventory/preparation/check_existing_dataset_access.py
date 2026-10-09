"""Check existing Hugging Face grants, without accepting terms or changing access."""
from pathlib import Path
import json
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone

class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new and urllib.parse.urlparse(newurl).netloc != 'huggingface.co':
            new.remove_header('Authorization')
        return new

token = (Path.home() / '.cache/huggingface/token').read_text().strip()
opener = urllib.request.build_opener(SafeRedirect())
out = Path('/private/tmp/agenttime-inventory-20261002/access-check')
out.mkdir(exist_ok=True)
checks = []
for family, repo, revision in [
    ('osworld', 'xlangai/osworld_v2_tasks', '0a1aadad95aa79b00b3783e717d865089ab06e26'),
    ('hle', 'cais/hle-diamond', '04eeb7efa7e3e4f83a00cbd5ce436a38fd5dda23'),
]:
    url = f'https://huggingface.co/datasets/{repo}/resolve/{revision}/README.md'
    request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token})
    try:
        with opener.open(request, timeout=30) as response:
            body = response.read(1024 * 1024 + 1)
            assert len(body) <= 1024 * 1024
            (out / (family + '-README.md')).write_bytes(body)
            checks.append({'family': family, 'url': url, 'status': response.status, 'bytes': len(body), 'existing_grant_works': True})
    except urllib.error.HTTPError as exc:
        checks.append({'family': family, 'url': url, 'status': exc.code, 'error_code': exc.headers.get('X-Error-Code'), 'existing_grant_works': False})
result = {'checked_at_utc': datetime.now(timezone.utc).isoformat(), 'used_existing_local_hf_login': True, 'terms_accepted': False, 'permissions_changed': False, 'checks': checks}
(out / 'access-receipt.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result))
