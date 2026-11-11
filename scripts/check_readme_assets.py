#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

IMG_MD_RE = re.compile(r"!\[[^\]]*\]\((?P<url><[^>]+>|[^)\s]+)(?:\s+\"[^\"]*\")?\)")
IMG_HTML_RE = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*['\"](?P<url>[^'\"]+)['\"]", re.IGNORECASE)
SOURCE_RE = re.compile(r"<source\b[^>]*?\bsrcset\s*=\s*['\"](?P<url>[^'\"]+)['\"]", re.IGNORECASE)


def is_url(u: str) -> bool:
    return u.startswith("http://") or u.startswith("https://")


def extract_urls(readme_text: str):
    found = []
    for i, line in enumerate(readme_text.splitlines(), 1):
        for m in IMG_MD_RE.finditer(line):
            u = m.group("url").strip()
            if u.startswith("<") and u.endswith(">"):
                u = u[1:-1]
            found.append((i, u, "markdown"))
        for m in IMG_HTML_RE.finditer(line):
            found.append((i, m.group("url").strip(), "html-img"))
        for m in SOURCE_RE.finditer(line):
            # srcset can have multiple; take first token
            u = m.group("url").strip().split()[0]
            found.append((i, u, "html-source"))
    # de-dup preserve order
    seen = set()
    out = []
    for line_no, url, kind in found:
        key = (line_no, url, kind)
        if key in seen:
            continue
        seen.add(key)
        out.append({"line": line_no, "url": url, "kind": kind})
    return out


def check_url(url: str, timeout=20, retries=2):
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; PeyxDev-AssetChecker/1.0; +https://github.com/p3yx/p3yx)",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", 200)
                ctype = resp.headers.get("Content-Type", "")
                final_url = resp.geturl()
                body = resp.read(4096)
            is_img_ct = any(x in ctype.lower() for x in ["image/", "svg+xml"])
            # Some endpoints may return text/plain on error with 200 rarely, but we rely on status
            ok = (200 <= status < 300) and (is_img_ct or "octet-stream" in ctype.lower())
            if not ok and 200 <= status < 300 and body[:200].lstrip().startswith(b"<svg"):
                ok = True
                if not is_img_ct:
                    ctype = (ctype + "; detected=svg").strip()
            return {
                "ok": ok,
                "status": status,
                "content_type": ctype,
                "final_url": final_url,
                "error": None if ok else f"Non-image or bad response (status={status}, content-type={ctype})",
                "temporary": False,
            }
        except urllib.error.HTTPError as e:
            ctype = e.headers.get("Content-Type", "") if e.headers else ""
            last = {
                "ok": False,
                "status": e.code,
                "content_type": ctype,
                "final_url": url,
                "error": f"HTTPError {e.code}",
                "temporary": e.code in (429, 500, 502, 503, 504),
            }
        except Exception as e:
            last = {
                "ok": False,
                "status": None,
                "content_type": "",
                "final_url": url,
                "error": f"{type(e).__name__}: {e}",
                "temporary": True,
            }
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    return last


def check_local(path_str: str, repo_root: Path):
    # strip query/fragment
    p = path_str.split("?", 1)[0].split("#", 1)[0]
    fs = (repo_root / p).resolve()
    exists = fs.exists() and fs.is_file()
    ctype = ""
    if exists:
        ext = fs.suffix.lower()
        ctype = {
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }.get(ext, "unknown")
    return {
        "ok": exists,
        "status": 200 if exists else 404,
        "content_type": ctype,
        "final_url": str(fs),
        "error": None if exists else "Local file missing",
        "temporary": False,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--readme", default="README.md")
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    root = Path(args.root).resolve()
    readme_path = (root / args.readme).resolve()
    text = readme_path.read_text(encoding="utf-8")
    assets = extract_urls(text)
    results = []
    for a in assets:
        url = a["url"]
        if is_url(url):
            r = check_url(url)
            location = "external"
        else:
            r = check_local(url, root)
            location = "local"
        results.append({**a, **r, "location": location})

    ok = [r for r in results if r["ok"]]
    broken = [r for r in results if not r["ok"] and not r.get("temporary")]
    temp = [r for r in results if not r["ok"] and r.get("temporary")]
    report = {
        "total": len(results),
        "ok": len(ok),
        "broken": len(broken),
        "temporary_fail": len(temp),
        "assets": results,
    }
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    # human summary
    print(f"README: {readme_path}")
    print(f"Total assets: {report['total']}")
    print(f"OK: {report['ok']}")
    print(f"Broken: {report['broken']}")
    print(f"Temporary failures: {report['temporary_fail']}")
    if broken or temp:
        print("\nIssues:")
        for r in results:
            if not r["ok"]:
                print(f"- [L{r['line']}] {r['url']} -> status={r['status']} ctype={r['content_type']} err={r['error']} temp={r.get('temporary')}")
    else:
        print("\nNo broken assets detected.")
    return 0 if report["broken"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
