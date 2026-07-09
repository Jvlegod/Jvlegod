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
LANGUAGE_ALIASES = {"C": "C/C++", "C++": "C/C++"}
IGNORED_LANGUAGES = {
    "CSV", "Dockerfile", "EditorConfig", "Git Attributes", "Git Config", "Git Revision List",
    "Ignore List", "INI", "JSON", "Markdown", "ReStructuredText", "SVG", "Text",
    "TOML", "XML", "YAML",
}


def normalize_language(language: object) -> str:
    name = str(language or "Unknown")
    return LANGUAGE_ALIASES.get(name, name)


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


def github_commit_search_count(query: str, headers: dict[str, str]) -> int:
    data = request_json(f"{GITHUB_API}/search/commits?{urlencode({'q': query, 'per_page': 1})}", headers)
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


def add_language(counter: Counter[str], language: object, amount: int) -> None:
    name = normalize_language(language)
    if name not in IGNORED_LANGUAGES:
        counter[name] += amount


def github_repo_languages(repos: list[dict[str, Any]], headers: dict[str, str]) -> Counter[str]:
    languages: Counter[str] = Counter()
    for repo in repos:
        owner = repo["owner"]["login"]
        name = repo["name"]
        data = request_json(f"{GITHUB_API}/repos/{owner}/{name}/languages", headers)
        if isinstance(data, dict):
            for language, bytes_count in data.items():
                add_language(languages, language, int(bytes_count))
    return languages


def github_commit_data(username: str, repos: list[dict[str, Any]], headers: dict[str, str]) -> tuple[int, int]:
    all_time = 0
    this_year = 0
    year_start = f"{CURRENT_YEAR}-01-01T00:00:00Z"
    for repo in repos:
        owner = repo["owner"]["login"]
        name = repo["name"]
        commits = paged(f"{GITHUB_API}/repos/{owner}/{name}/commits?author={quote(username)}", headers)
        year_commits = paged(f"{GITHUB_API}/repos/{owner}/{name}/commits?author={quote(username)}&since={year_start}", headers)
        all_time += len(commits)
        this_year += len(year_commits)
    return all_time, this_year


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


def gitee_repo_languages(username: str, repos: list[dict[str, Any]]) -> Counter[str]:
    languages: Counter[str] = Counter()
    for repo in repos:
        owner = repo.get("namespace", {}).get("path") or username
        name = repo.get("path") or repo.get("name")
        data = request_json(gitee_url(f"/repos/{owner}/{name}/languages"))
        if isinstance(data, dict) and data:
            for language, bytes_count in data.items():
                add_language(languages, language, int(bytes_count))
        else:
            add_language(languages, repo.get("language"), 1)
    return languages


def gitee_commit_data(username: str, repos: list[dict[str, Any]]) -> tuple[int, int]:
    all_time = 0
    this_year = 0
    for repo in repos:
        owner = repo.get("namespace", {}).get("path") or username
        name = repo.get("path") or repo.get("name")
        commits = paged(gitee_url(f"/repos/{owner}/{name}/commits"))
        all_time += len(commits)
        for commit in commits:
            created = commit.get("created_at") or commit.get("commit", {}).get("author", {}).get("date", "")
            if str(created).startswith(str(CURRENT_YEAR)):
                this_year += 1
    return all_time, this_year


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


def chart_style() -> str:
    return '''<style>
.title{font:700 18px Arial,sans-serif;fill:#111827}.sub{font:600 12px Arial,sans-serif;fill:#6b7280}.text{font:500 13px Arial,sans-serif;fill:#374151}.label{font:600 12px Arial,sans-serif;fill:#6b7280}.value{font:700 24px Arial,sans-serif;fill:#111827}.small{font:500 11px Arial,sans-serif;fill:#6b7280}
@media (prefers-color-scheme: dark){.title{fill:#f9fafb}.sub{fill:#9ca3af}.text{fill:#d1d5db}.label{fill:#9ca3af}.value{fill:#f9fafb}.small{fill:#9ca3af}}
</style>'''


def write_language_overview(path: Path, repo_counter: Counter[str]) -> None:
    combined = repo_counter
    items = top_items(combined)
    total = sum(value for _, value in items) or 1
    bars = []
    y = 92
    for index, (name, value) in enumerate(items[:7]):
        pct = value / total * 100
        width = max(pct / 100 * 330, 3)
        color = COLORS[index % len(COLORS)]
        bars.append(f'<text x="32" y="{y}" class="text">{esc(name)}</text>')
        bars.append(f'<rect x="150" y="{y - 12}" width="330" height="10" rx="5" fill="#e5e7eb" opacity="0.55"/>')
        bars.append(f'<rect x="150" y="{y - 12}" width="{width:.1f}" height="10" rx="5" fill="{color}"/>')
        bars.append(f'<text x="498" y="{y}" class="small">{pct:.1f}%</text>')
        y += 28
    if not combined:
        bars.append('<text x="32" y="108" class="text">No language data yet</text>')
    path.write_text(f'''<svg width="760" height="280" viewBox="0 0 760 280" fill="none" xmlns="http://www.w3.org/2000/svg">
{chart_style()}
<rect x="0.5" y="0.5" width="759" height="279" rx="8" fill="transparent" stroke="#e5e7eb"/>
<text x="32" y="40" class="title">Language Overview</text>
<text x="32" y="62" class="sub">Repository language byte totals, excluding docs and config</text>
{''.join(bars)}
<text x="560" y="94" class="label">Code bytes</text>
<text x="560" y="126" class="value">{sum(repo_counter.values())}</text>
<text x="560" y="170" class="label">Languages</text>
<text x="560" y="202" class="value">{len(repo_counter)}</text>
</svg>
''', encoding="utf-8")


def write_status_summary(path: Path, all_time: dict[str, int | str], current_year: dict[str, int | str]) -> None:
    labels = ["Stars", "Commits", "Pull Requests", "Issues"]
    cols = []
    x = 170
    for label in labels:
        cols.append(f'<text x="{x}" y="82" text-anchor="middle" class="label">{esc(label)}</text>')
        cols.append(f'<text x="{x}" y="126" text-anchor="middle" class="value">{esc(all_time[label])}</text>')
        cols.append(f'<text x="{x}" y="188" text-anchor="middle" class="value">{esc(current_year[label])}</text>')
        x += 135
    path.write_text(f'''<svg width="760" height="240" viewBox="0 0 760 240" fill="none" xmlns="http://www.w3.org/2000/svg">
{chart_style()}
<rect x="0.5" y="0.5" width="759" height="239" rx="8" fill="transparent" stroke="#e5e7eb"/>
<text x="32" y="40" class="title">Status</text>
<text x="32" y="62" class="sub">All-time and {CURRENT_YEAR} activity</text>
<text x="32" y="126" class="label">All-time</text>
<text x="32" y="188" class="label">{CURRENT_YEAR}</text>
<line x1="32" y1="148" x2="728" y2="148" stroke="#e5e7eb"/>
{''.join(cols)}
</svg>
''', encoding="utf-8")

def main() -> None:
    github_user = os.getenv("GITHUB_USERNAME", "Jvlegod")
    gitee_user = os.getenv("GITEE_USERNAME", "Jvle")
    if os.getenv("ANALYTICS_OFFLINE") == "1":
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        write_language_overview(OUT_DIR / "language-overview.svg", Counter())
        write_status_summary(OUT_DIR / "status-summary.svg", {"Stars": "Pending", "Commits": "Pending", "Pull Requests": "Pending", "Issues": "Pending"}, {"Stars": "Pending", "Commits": "Pending", "Pull Requests": "Pending", "Issues": "Pending"})
        return
    token_available = bool(os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN"))
    enable_gitee = os.getenv("ENABLE_GITEE", "0") == "1"
    deep_commit_scan = os.getenv("DEEP_COMMIT_SCAN", "0") == "1"
    star_year_scan = os.getenv("STAR_YEAR_SCAN", "0") == "1"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gh_headers = github_headers()
    gh_repos = list_github_repos(github_user, gh_headers)
    gt_repos = list_gitee_repos(gitee_user) if enable_gitee else []
    repo_languages = github_repo_languages(gh_repos, gh_headers)
    repo_languages.update(gitee_repo_languages(gitee_user, gt_repos))

    if deep_commit_scan:
        gh_commits_all, gh_commits_year = github_commit_data(github_user, gh_repos, gh_headers)
    else:
        gh_commits_all = github_commit_search_count(f"author:{github_user}", gh_headers)
        gh_commits_year = github_commit_search_count(f"author:{github_user} author-date:{CURRENT_YEAR}-01-01..{CURRENT_YEAR}-12-31", gh_headers)


    if enable_gitee:
        gt_commits_all, gt_commits_year = gitee_commit_data(gitee_user, gt_repos)
        gitee_issue_repo_limit = int(os.getenv("GITEE_ISSUE_REPO_LIMIT", "10"))
        gt_issues_all, gt_issues_year, gt_prs_all, gt_prs_year = gitee_issue_pr_counts(gitee_user, gt_repos[:gitee_issue_repo_limit])
    else:
        gt_commits_all = gt_commits_year = gt_issues_all = gt_issues_year = gt_prs_all = gt_prs_year = 0

    gh_stars_all = sum(int(repo.get("stargazers_count", 0)) for repo in gh_repos)
    gt_stars_all = sum(int(repo.get("stargazers_count", 0) or repo.get("stars_count", 0) or 0) for repo in gt_repos)
    gh_stars_year = sum(github_star_year_count(repo["owner"]["login"], repo["name"], gh_headers) for repo in gh_repos) if star_year_scan else "N/A"
    gh_prs_all = github_search_count(f"author:{github_user} type:pr", gh_headers)
    gh_prs_year = github_search_count(f"author:{github_user} type:pr created:{CURRENT_YEAR}-01-01..{CURRENT_YEAR}-12-31", gh_headers)
    gh_issues_all = github_search_count(f"author:{github_user} type:issue", gh_headers)
    gh_issues_year = github_search_count(f"author:{github_user} type:issue created:{CURRENT_YEAR}-01-01..{CURRENT_YEAR}-12-31", gh_headers)
    all_time = {
        "Stars": gh_stars_all + gt_stars_all,
        "Commits": gh_commits_all + gt_commits_all,
        "Pull Requests": gh_prs_all + gt_prs_all,
        "Issues": gh_issues_all + gt_issues_all,
    }
    current_year = {
        "Stars": gh_stars_year,
        "Commits": gh_commits_year + gt_commits_year,
        "Pull Requests": gh_prs_year + gt_prs_year,
        "Issues": gh_issues_year + gt_issues_year,
    }
    write_language_overview(OUT_DIR / "language-overview.svg", repo_languages)
    write_status_summary(OUT_DIR / "status-summary.svg", all_time, current_year)


if __name__ == "__main__":
    main()
