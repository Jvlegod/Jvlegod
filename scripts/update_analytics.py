#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "assets" / "analytics"
CURRENT_YEAR = datetime.now(timezone.utc).year
GITHUB_API = "https://api.github.com"
GITEE_API = "https://gitee.com/api/v5"
LANG_BY_EXT = {
    ".rs": "Rust", ".c": "C", ".h": "C/C++", ".cc": "C++", ".cpp": "C++", ".hpp": "C++",
    ".py": "Python", ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell", ".go": "Go",
    ".js": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript", ".jsx": "JavaScript",
    ".java": "Java", ".lua": "Lua", ".md": "Markdown", ".yml": "YAML", ".yaml": "YAML",
    ".toml": "TOML", ".json": "JSON",
}
COLORS = ["#2563eb", "#16a34a", "#dc2626", "#9333ea", "#ea580c", "#0891b2", "#4f46e5", "#65a30d"]


def request_json(url: str, headers: dict[str, str] | None = None) -> Any:
    req = Request(url, headers=headers or {})
    for attempt in range(3):
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            if attempt == 2:
                print(f"warning: failed to fetch {url}: {exc}", file=sys.stderr)
                return None
            time.sleep(1 + attempt)
    return None


def paged(url: str, headers: dict[str, str] | None = None, page_key: str = "page") -> list[Any]:
    items: list[Any] = []
    for page in range(1, 101):
        sep = "&" if "?" in url else "?"
        data = request_json(f"{url}{sep}{page_key}={page}&per_page=100", headers)
        if not data:
            break
        if isinstance(data, dict) and "items" in data:
            data = data["items"]
        if not isinstance(data, list):
            break
        items.extend(data)
        if len(data) < 100:
            break
    return items


def github_headers() -> dict[str, str]:
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def github_search_count(query: str, headers: dict[str, str]) -> int:
    data = request_json(f"{GITHUB_API}/search/issues?{urlencode({'q': query, 'per_page': 1})}", headers)
    return int(data.get("total_count", 0)) if isinstance(data, dict) else 0


def github_star_year_count(owner: str, repo: str, headers: dict[str, str]) -> int:
    star_headers = dict(headers)
    star_headers["Accept"] = "application/vnd.github.star+json"
    total = 0
    for star in paged(f"{GITHUB_API}/repos/{owner}/{repo}/stargazers", star_headers):
        if str(star.get("starred_at", "")).startswith(str(CURRENT_YEAR)):
            total += 1
    return total


def list_github_repos(username: str, headers: dict[str, str]) -> list[dict[str, Any]]:
    repos = paged(f"{GITHUB_API}/users/{username}/repos?type=owner&sort=updated", headers)
    return [repo for repo in repos if isinstance(repo, dict) and not repo.get("fork")]


def github_commit_data(username: str, repos: list[dict[str, Any]], headers: dict[str, str], file_limit: int) -> tuple[int, int, Counter[str]]:
    all_time = 0
    this_year = 0
    languages: Counter[str] = Counter()
    inspected = 0
    year_start = f"{CURRENT_YEAR}-01-01T00:00:00Z"
    for repo in repos:
        owner = repo["owner"]["login"]
        name = repo["name"]
        commits = paged(f"{GITHUB_API}/repos/{owner}/{name}/commits?author={quote(username)}", headers)
        year_commits = paged(f"{GITHUB_API}/repos/{owner}/{name}/commits?author={quote(username)}&since={year_start}", headers)
        all_time += len(commits)
        this_year += len(year_commits)
        for commit in commits:
            if inspected >= file_limit:
                break
            sha = commit.get("sha")
            detail = request_json(f"{GITHUB_API}/repos/{owner}/{name}/commits/{sha}", headers) if sha else None
            inspected += 1
            for file_info in (detail or {}).get("files", []):
                lang = LANG_BY_EXT.get(Path(file_info.get("filename", "")).suffix.lower())
                if lang:
                    languages[lang] += max(int(file_info.get("changes", 1)), 1)
    return all_time, this_year, languages


def gitee_token_query() -> str:
    token = os.getenv("GITEE_TOKEN")
    return f"access_token={quote(token)}" if token else ""


def gitee_url(path: str, params: dict[str, Any] | None = None) -> str:
    query = gitee_token_query()
    extra = urlencode(params or {})
    parts = [part for part in (query, extra) if part]
    return f"{GITEE_API}{path}" + (f"?{'&'.join(parts)}" if parts else "")


def list_gitee_repos(username: str) -> list[dict[str, Any]]:
    repos = paged(gitee_url(f"/users/{username}/repos"))
    return [repo for repo in repos if isinstance(repo, dict) and not repo.get("fork")]


def gitee_commit_data(username: str, repos: list[dict[str, Any]], file_limit: int) -> tuple[int, int, Counter[str]]:
    all_time = 0
    this_year = 0
    languages: Counter[str] = Counter()
    inspected = 0
    for repo in repos:
        owner = repo.get("namespace", {}).get("path") or username
        name = repo.get("path") or repo.get("name")
        commits = paged(gitee_url(f"/repos/{owner}/{name}/commits"))
        all_time += len(commits)
        for commit in commits:
            created = commit.get("created_at") or commit.get("commit", {}).get("author", {}).get("date", "")
            if str(created).startswith(str(CURRENT_YEAR)):
                this_year += 1
            if inspected >= file_limit:
                break
            sha = commit.get("sha")
            detail = request_json(gitee_url(f"/repos/{owner}/{name}/commits/{sha}")) if sha else None
            inspected += 1
            for file_info in (detail or {}).get("files", []):
                lang = LANG_BY_EXT.get(Path(file_info.get("filename", "")).suffix.lower())
                if lang:
                    languages[lang] += max(int(file_info.get("changes", 1)), 1)
    return all_time, this_year, languages


def gitee_issue_pr_counts(username: str, repos: list[dict[str, Any]]) -> tuple[int, int, int, int]:
    issues_all = issues_year = prs_all = prs_year = 0
    for repo in repos:
        owner = repo.get("namespace", {}).get("path") or username
        name = repo.get("path") or repo.get("name")
        issues = paged(gitee_url(f"/repos/{owner}/{name}/issues", {"state": "all"}))
        pulls = paged(gitee_url(f"/repos/{owner}/{name}/pulls", {"state": "all"}))
        issues_all += len(issues)
        prs_all += len(pulls)
        issues_year += sum(1 for item in issues if str(item.get("created_at", "")).startswith(str(CURRENT_YEAR)))
        prs_year += sum(1 for item in pulls if str(item.get("created_at", "")).startswith(str(CURRENT_YEAR)))
    return issues_all, issues_year, prs_all, prs_year


def esc(text: object) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def top_items(counter: Counter[str], limit: int = 7) -> list[tuple[str, int]]:
    common = counter.most_common(limit)
    rest = sum(counter.values()) - sum(value for _, value in common)
    if rest > 0:
        common.append(("Other", rest))
    return common


def write_pie(path: Path, title: str, counter: Counter[str]) -> None:
    items = top_items(counter)
    total = sum(value for _, value in items) or 1
    y = 92
    rows = []
    for index, (name, value) in enumerate(items):
        color = COLORS[index % len(COLORS)]
        pct = value / total * 100
        rows.append(f'<circle cx="284" cy="{y - 5}" r="5" fill="{color}"/><text x="298" y="{y}" class="text">{esc(name)} {pct:.1f}%</text>')
        y += 24
    if not counter:
        rows.append('<text x="284" y="112" class="muted">No language data yet</text>')
    offset = 0.0
    slices = []
    radius = 70
    circumference = 2 * 3.14159 * radius
    for index, (_, value) in enumerate(items):
        length = value / total * circumference
        slices.append(f'<circle r="{radius}" cx="130" cy="150" fill="transparent" stroke="{COLORS[index % len(COLORS)]}" stroke-width="38" stroke-dasharray="{length:.2f} {circumference - length:.2f}" stroke-dashoffset="{-offset:.2f}" transform="rotate(-90 130 150)"/>')
        offset += length
    path.write_text(f'''<svg width="560" height="310" viewBox="0 0 560 310" fill="none" xmlns="http://www.w3.org/2000/svg">
<style>
.title{{font:700 18px Arial,sans-serif;fill:#111827}}.text{{font:500 13px Arial,sans-serif;fill:#374151}}.muted{{font:500 13px Arial,sans-serif;fill:#6b7280}}
@media (prefers-color-scheme: dark){{.title{{fill:#f9fafb}}.text{{fill:#d1d5db}}.muted{{fill:#9ca3af}}}}
</style>
<rect x="0.5" y="0.5" width="559" height="309" rx="8" fill="transparent" stroke="#e5e7eb"/>
<text x="28" y="38" class="title">{esc(title)}</text>
<g>{''.join(slices)}</g>
<circle cx="130" cy="150" r="44" fill="transparent" stroke="#e5e7eb"/>
<text x="284" y="62" class="title">Distribution</text>
{''.join(rows)}
</svg>
''', encoding="utf-8")


def write_summary(path: Path, title: str, stats: dict[str, int | str]) -> None:
    cells = []
    for index, (label, value) in enumerate(stats.items()):
        x = 30 + (index % 2) * 245
        y = 88 + (index // 2) * 82
        cells.append(f'<text x="{x}" y="{y}" class="label">{esc(label)}</text><text x="{x}" y="{y + 34}" class="value">{esc(value)}</text>')
    path.write_text(f'''<svg width="560" height="250" viewBox="0 0 560 250" fill="none" xmlns="http://www.w3.org/2000/svg">
<style>
.title{{font:700 18px Arial,sans-serif;fill:#111827}}.label{{font:600 13px Arial,sans-serif;fill:#6b7280}}.value{{font:700 28px Arial,sans-serif;fill:#111827}}
@media (prefers-color-scheme: dark){{.title{{fill:#f9fafb}}.label{{fill:#9ca3af}}.value{{fill:#f9fafb}}}}
</style>
<rect x="0.5" y="0.5" width="559" height="249" rx="8" fill="transparent" stroke="#e5e7eb"/>
<text x="30" y="38" class="title">{esc(title)}</text>
{''.join(cells)}
</svg>
''', encoding="utf-8")


def main() -> None:
    github_user = os.getenv("GITHUB_USERNAME", "Jvlegod")
    gitee_user = os.getenv("GITEE_USERNAME", "Jvle")
    if os.getenv("ANALYTICS_OFFLINE") == "1":
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        write_pie(OUT_DIR / "language-repos-pie.svg", "Repository Languages", Counter())
        write_pie(OUT_DIR / "language-commits-pie.svg", "Commit Languages", Counter())
        write_summary(OUT_DIR / "all-time-summary.svg", "All-time Activity", {"Stars": "Pending", "Commits": "Pending", "Pull Requests": "Pending", "Issues": "Pending"})
        write_summary(OUT_DIR / "current-year-summary.svg", f"{CURRENT_YEAR} Activity", {"Stars": "Pending", "Commits": "Pending", "Pull Requests": "Pending", "Issues": "Pending"})
        return
    token_available = bool(os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN"))
    default_file_limit = "300" if token_available else "0"
    file_limit = int(os.getenv("ANALYTICS_COMMIT_FILE_LIMIT", default_file_limit))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gh_headers = github_headers()
    gh_repos = list_github_repos(github_user, gh_headers)
    gt_repos = list_gitee_repos(gitee_user)
    repo_languages = Counter()
    repo_languages.update(repo.get("language") or "Unknown" for repo in gh_repos)
    repo_languages.update(repo.get("language") or "Unknown" for repo in gt_repos)
    gh_commits_all, gh_commits_year, gh_commit_langs = github_commit_data(github_user, gh_repos, gh_headers, file_limit)
    gt_commits_all, gt_commits_year, gt_commit_langs = gitee_commit_data(gitee_user, gt_repos, file_limit)
    if not gh_commit_langs and not gt_commit_langs:
        gh_commit_langs.update(repo.get("language") or "Unknown" for repo in gh_repos)
        gt_commit_langs.update(repo.get("language") or "Unknown" for repo in gt_repos)
    gitee_issue_repo_limit = int(os.getenv("GITEE_ISSUE_REPO_LIMIT", "20"))
    gt_issues_all, gt_issues_year, gt_prs_all, gt_prs_year = gitee_issue_pr_counts(gitee_user, gt_repos[:gitee_issue_repo_limit])
    gh_stars_all = sum(int(repo.get("stargazers_count", 0)) for repo in gh_repos)
    gt_stars_all = sum(int(repo.get("stargazers_count", 0) or repo.get("stars_count", 0) or 0) for repo in gt_repos)
    gh_stars_year = sum(github_star_year_count(repo["owner"]["login"], repo["name"], gh_headers) for repo in gh_repos)
    gh_prs_all = github_search_count(f"author:{github_user} type:pr", gh_headers)
    gh_prs_year = github_search_count(f"author:{github_user} type:pr created:{CURRENT_YEAR}-01-01..{CURRENT_YEAR}-12-31", gh_headers)
    gh_issues_all = github_search_count(f"author:{github_user} type:issue", gh_headers)
    gh_issues_year = github_search_count(f"author:{github_user} type:issue created:{CURRENT_YEAR}-01-01..{CURRENT_YEAR}-12-31", gh_headers)
    write_pie(OUT_DIR / "language-repos-pie.svg", "Repository Languages", repo_languages)
    write_pie(OUT_DIR / "language-commits-pie.svg", "Commit Languages", gh_commit_langs + gt_commit_langs)
    write_summary(OUT_DIR / "all-time-summary.svg", "All-time Activity", {
        "Stars": gh_stars_all + gt_stars_all,
        "Commits": gh_commits_all + gt_commits_all,
        "Pull Requests": gh_prs_all + gt_prs_all,
        "Issues": gh_issues_all + gt_issues_all,
    })
    write_summary(OUT_DIR / "current-year-summary.svg", f"{CURRENT_YEAR} Activity", {
        "Stars": f"{gh_stars_year}+",
        "Commits": gh_commits_year + gt_commits_year,
        "Pull Requests": gh_prs_year + gt_prs_year,
        "Issues": gh_issues_year + gt_issues_year,
    })


if __name__ == "__main__":
    main()
