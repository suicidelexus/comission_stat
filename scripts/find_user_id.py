"""Грузим главную страницу — user_id обычно вшит в HTML/JS bundle."""
from __future__ import annotations
import sys, re
from pathlib import Path
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_auth import load_env  # type: ignore


def main() -> int:
    env = load_env()
    host = env["HYBRID_HOST"]
    cookies = env["HYBRID_COOKIES"]
    headers = {
        "Accept": "text/html,application/xhtml+xml,*/*",
        "Referer": f"https://{host}/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/148.0 Safari/537.36",
        "Cookie": cookies,
    }
    with httpx.Client(timeout=20.0, follow_redirects=True) as c:
        r = c.get(f"https://{host}/", headers=headers)
    print(f"status: {r.status_code}, length: {len(r.content)}, ct: {r.headers.get('content-type')}")
    text = r.text
    patterns = [
        r'userId["\']?\s*[:=]\s*["\']?(\d+)',
        r'user_id["\']?\s*[:=]\s*["\']?(\d+)',
        r'UserId["\']?\s*[:=]\s*["\']?(\d+)',
        r'"id"\s*:\s*(\d+)',
        r'currentUser[^}]*?id["\']?\s*[:=]\s*["\']?(\d+)',
    ]
    for pat in patterns:
        m = re.findall(pat, text)
        if m:
            uniq = list(dict.fromkeys(m))[:10]
            print(f"  pattern {pat!r}: {uniq}")
    snippets = [m.start() for m in re.finditer(r"userId", text, re.IGNORECASE)]
    print(f"'userId' occurrences in HTML: {len(snippets)}")
    for pos in snippets[:5]:
        print(f"  ...{text[max(0,pos-40):pos+80]}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
