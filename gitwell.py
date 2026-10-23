"""GitWell - understand any GitHub repo and generate the docs it is missing.

Scans a public GitHub repo, scores its health and contributor-readiness,
explains how the code is organised, tells a contributor which files to change
for a given task, and uses an open-weight model (Gemma via Ollama) or Gemini
to write the missing docs.

CLI:
  python gitwell.py owner/repo --backend gemma
  python gitwell.py owner/repo --plan "add retry to the http client"
  python gitwell.py owner/repo --plan https://github.com/owner/repo/issues/42
  python gitwell.py owner/repo --write-to ../my-clone
"""
import argparse
import hashlib
import json
import os
import posixpath
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote

import requests
from dotenv import load_dotenv

load_dotenv()

GH_API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
MAX_CONTEXT = 14000        # chars sent to the model for the largest docs
SCAN_TTL = 3600            # seconds a cached scan or code index stays valid
CACHE_DIR = os.getenv("GITWELL_CACHE", ".gitwell_cache")
KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")
NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
MAX_INDEX_FILES = 40

SKIP = ("node_modules/", "vendor/", "dist/", "build/", ".git/", "__pycache__/",
        ".venv/", "venv/", ".next/")
SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".lock", ".min.js",
            ".map", ".pdf", ".zip", ".woff", ".woff2", ".ttf", ".mp4", ".mp3")
MANIFESTS = ("package.json", "requirements.txt", "pyproject.toml", "setup.py",
             "Cargo.toml", "go.mod", "pom.xml", "Gemfile", "composer.json",
             "Dockerfile", "docker-compose.yml", "Makefile")
ENTRY_NAMES = ("main.py", "app.py", "index.js", "index.ts", "server.js",
               "main.go", "main.rs", "manage.py", "cli.py", "index.html")
CODE_EXT = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".go", ".rs", ".java",
            ".rb", ".php", ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".kt",
            ".swift", ".sh", ".vue", ".svelte")

DIR_HINTS = {
    "src": "Main source code", "lib": "Library code", "app": "Application code",
    "tests": "Test suite", "test": "Test suite", "__tests__": "Test suite",
    "docs": "Documentation", "doc": "Documentation", "examples": "Usage examples",
    "scripts": "Helper scripts", "bin": "Executables / CLI entry points",
    ".github": "CI workflows and templates", "assets": "Static assets",
    "public": "Static files served as-is", "static": "Static files",
    "config": "Configuration", "cmd": "Go entry points", "pkg": "Reusable packages",
    "internal": "Private packages", "components": "UI components",
    "pages": "Routes / pages", "api": "API layer", "utils": "Shared helpers",
    "models": "Data models", "templates": "HTML templates",
    "migrations": "Database migrations", "data": "Data files",
    "backend": "Server code", "frontend": "Client code", "server": "Server code",
    "client": "Client code", "packages": "Monorepo packages",
    ".streamlit": "Streamlit config",
}


# --------------------------------------------------------------------------
# Disk cache (scan results, code index, generated docs)
# --------------------------------------------------------------------------
def _cache_path(kind, key):
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    return os.path.join(CACHE_DIR, f"{kind}-{h}.json")


def cache_get(kind, key, ttl=None):
    p = _cache_path(kind, key)
    try:
        if ttl and time.time() - os.path.getmtime(p) > ttl:
            return None
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def cache_put(kind, key, value):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_cache_path(kind, key), "w", encoding="utf-8") as f:
            json.dump(value, f)
    except (OSError, TypeError):
        pass


def clear_cache():
    n = 0
    try:
        for name in os.listdir(CACHE_DIR):
            if name.endswith(".json"):
                os.remove(os.path.join(CACHE_DIR, name))
                n += 1
    except OSError:
        pass
    return n


# --------------------------------------------------------------------------
# GitHub fetching
# --------------------------------------------------------------------------
def parse_repo(text):
    text = text.strip()
    m = re.search(r"github\.com/([\w.-]+)/([\w.-]+)", text)
    if not m:
        m = re.fullmatch(r"([\w.-]+)/([\w.-]+)", text)
    if not m:
        raise ValueError("Enter a GitHub URL like https://github.com/owner/repo")
    owner, repo = m.group(1), m.group(2)
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo


def gh_get(path):
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.get(f"{GH_API}{path}", headers=headers, timeout=20)
    if r.status_code == 404:
        raise RuntimeError("Not found on GitHub. Is it public and spelled correctly?")
    if r.status_code in (403, 429):
        raise RuntimeError("GitHub rate limit hit. Add GITHUB_TOKEN to your .env and retry.")
    r.raise_for_status()
    return r.json()


def gh_try(path, default):
    """Optional data: never fail the whole scan if one endpoint is unavailable."""
    try:
        return gh_get(path)
    except Exception:
        return default


def fetch_file(owner, repo, branch, path, limit=3000):
    try:
        r = requests.get(f"{RAW}/{owner}/{repo}/{branch}/{path}", timeout=20)
        if r.status_code == 200:
            return r.text[:limit]
    except requests.RequestException:
        pass
    return ""


def _plain(text, n=90):
    """Strip characters that would break markdown links."""
    return re.sub(r"[\[\]`]", "", text or "").strip()[:n]


def days_since(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days
    except (ValueError, AttributeError):
        return None


def labeled_issues(owner, repo, label):
    data = gh_try(f"/repos/{owner}/{repo}/issues?state=open&per_page=10&labels={quote(label)}", [])
    if not isinstance(data, list):
        return []
    return [{"number": i["number"], "title": _plain(i["title"]),
             "url": i["html_url"], "comments": i["comments"]}
            for i in data if "pull_request" not in i][:6]


def recent_commits(owner, repo):
    data = gh_try(f"/repos/{owner}/{repo}/commits?per_page=5", [])
    if not isinstance(data, list):
        return []
    out = []
    for c in data:
        commit = c.get("commit", {})
        out.append({
            "sha": c.get("sha", "")[:7],
            "message": _plain(commit.get("message", "").split("\n")[0]),
            "author": (commit.get("author") or {}).get("name", "unknown"),
            "date": (commit.get("author") or {}).get("date", "")[:10],
            "url": c.get("html_url", ""),
        })
    return out


def top_contributors(owner, repo):
    data = gh_try(f"/repos/{owner}/{repo}/contributors?per_page=5", [])
    if not isinstance(data, list):
        return []
    return [{"login": c["login"], "commits": c["contributions"], "url": c["html_url"]}
            for c in data]


# --------------------------------------------------------------------------
# Repo structure helpers (no LLM needed)
# --------------------------------------------------------------------------
def project_map(paths):
    counts = {}
    for p in paths:
        head = p.split("/")[0] + "/" if "/" in p else "(root)"
        counts[head] = counts.get(head, 0) + 1
    rows = []
    for name, n in sorted(counts.items(), key=lambda kv: (kv[0] != "(root)", -kv[1], kv[0])):
        purpose = ("Top-level files (config, docs, entry points)" if name == "(root)"
                   else DIR_HINTS.get(name.rstrip("/").lower(), ""))
        rows.append({"Path": name, "Files": n, "Purpose": purpose})
    return rows[:15]


def key_files(paths):
    top = [p for p in paths if "/" not in p]
    out, seen = [], set()

    def add(p, why):
        if p and p not in seen:
            seen.add(p)
            out.append({"path": p, "why": why})

    add(next((p for p in top if p.lower().startswith("readme")), None),
        "Project overview and usage")
    add(next((p for p in top if p.lower().startswith("contributing")), None),
        "Contribution rules")
    entries = sorted((p for p in paths if p.split("/")[-1] in ENTRY_NAMES),
                     key=lambda p: (p.count("/"), p))[:3]
    for p in entries:
        add(p, "Likely entry point")
    for p in top:
        if p in MANIFESTS:
            add(p, "Dependencies and build config")
    add(next((p for p in paths if re.search(r"(^|/)(tests?|__tests__)/", p.lower())), None),
        "Example of how the code is tested")
    return out[:8]


def detect_commands(owner, repo, branch, paths):
    """Best-effort install / run / test commands from the repo's manifests."""
    top = {p for p in paths if "/" not in p}
    cmds = []

    env_file = next((p for p in sorted(top)
                     if p.lower() in (".env.example", "env.example", ".env.sample")), None)
    if "requirements.txt" in top:
        cmds.append(("Install", "pip install -r requirements.txt"))
    elif "pyproject.toml" in top or "setup.py" in top:
        cmds.append(("Install", "pip install -e ."))
    if "package.json" in top:
        cmds.append(("Install", "npm install"))
        try:
            scripts = json.loads(fetch_file(owner, repo, branch, "package.json", 30000)).get("scripts", {})
        except ValueError:
            scripts = {}
        for key, label, cmd in (("dev", "Run (dev)", "npm run dev"),
                                ("start", "Run", "npm start"),
                                ("build", "Build", "npm run build"),
                                ("test", "Test", "npm test"),
                                ("lint", "Lint", "npm run lint")):
            if key in scripts:
                cmds.append((label, cmd))
    if "Cargo.toml" in top:
        cmds += [("Build", "cargo build"), ("Test", "cargo test")]
    if "go.mod" in top:
        cmds += [("Build", "go build ./..."), ("Test", "go test ./...")]
    if "Gemfile" in top:
        cmds.append(("Install", "bundle install"))
    if "Makefile" in top:
        cmds.append(("Make targets", "make"))
    if "Dockerfile" in top:
        cmds.append(("Docker build", f"docker build -t {repo.lower()} ."))
    if env_file:
        cmds.insert(0, ("Configure", f"cp {env_file} .env"))
    has_py_tests = (any(p.endswith(".py") for p in paths)
                    and any(re.search(r"(^|/)tests?/|(^|/)test_[^/]*\.py$", p) for p in paths))
    if has_py_tests:
        cmds.append(("Test", "pytest"))
    return cmds


# --------------------------------------------------------------------------
# Code index: symbols, imports, import graph, test map
# --------------------------------------------------------------------------
SYMBOL_RES = [
    re.compile(r"^\s*(?:async\s+)?def\s+(\w+)", re.M),
    re.compile(r"^\s*class\s+(\w+)", re.M),
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(\w+)", re.M),
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>", re.M),
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(\w+)", re.M),
    re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)", re.M),
    re.compile(r"^\s*(?:export\s+)?(?:pub\s+)?(?:interface|type|struct|enum|trait)\s+(\w+)", re.M),
]
PY_IMPORT = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.M)
JS_IMPORT = re.compile(r"""(?:from\s+|require\(\s*|import\s*\(\s*|import\s+)['"]([^'"]+)['"]""")
JS_EXT = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".vue", ".svelte")


def _test_target(path):
    """Return the module stem a test file is named after, or None."""
    base = path.split("/")[-1]
    m = (re.match(r"test_(.+)\.\w+$", base)
         or re.match(r"(.+)_test\.\w+$", base)
         or re.match(r"(.+)\.(?:test|spec)\.\w+$", base))
    return m.group(1) if m else None


def _is_test_path(path):
    return (_test_target(path) is not None
            or bool(re.search(r"(^|/)(tests?|__tests__|spec)/", path.lower())))


def pick_code_files(paths):
    code = [p for p in paths
            if p.lower().endswith(CODE_EXT) and not p.lower().endswith(".d.ts")
            and not _is_test_path(p)]
    pref = ("src", "lib", "app", "pkg", "cmd", "internal")
    code.sort(key=lambda p: (0 if p.split("/")[0] in pref else 1, p.count("/"), p))
    return code[:MAX_INDEX_FILES]


def extract_symbols(text):
    seen, out = set(), []
    for rx in SYMBOL_RES:
        for name in rx.findall(text):
            if name not in seen and not name.startswith("__"):
                seen.add(name)
                out.append(name)
    return out[:40]


def _resolve_py(src, mod, pathset):
    cands = []
    if mod.startswith("."):
        dots = len(mod) - len(mod.lstrip("."))
        base = posixpath.dirname(src)
        for _ in range(dots - 1):
            base = posixpath.dirname(base)
        rel = mod.lstrip(".").replace(".", "/")
        cands.append(posixpath.join(base, rel) if rel else base)
    else:
        rel = mod.replace(".", "/")
        cands += [rel, "src/" + rel, posixpath.join(posixpath.dirname(src), rel)]
    for c in cands:
        c = posixpath.normpath(c)
        for hit in (c + ".py", c + "/__init__.py"):
            if hit in pathset and hit != src:
                return hit
    return None


def _resolve_js(src, spec, pathset):
    if spec.startswith("@/") or spec.startswith("~/"):
        base = posixpath.normpath("src/" + spec[2:])
    elif spec.startswith("."):
        base = posixpath.normpath(posixpath.join(posixpath.dirname(src), spec))
    else:
        return None
    options = [base] + [base + e for e in JS_EXT] + [f"{base}/index{e}" for e in JS_EXT]
    for hit in options:
        if hit in pathset and hit != src:
            return hit
    return None


def extract_imports(path, text, pathset):
    out = []
    if path.endswith(".py"):
        for a, b in PY_IMPORT.findall(text):
            hit = _resolve_py(path, a or b, pathset)
            if hit and hit not in out:
                out.append(hit)
    elif path.lower().endswith(JS_EXT):
        for spec in JS_IMPORT.findall(text):
            hit = _resolve_js(path, spec, pathset)
            if hit and hit not in out:
                out.append(hit)
    return out


def get_index(info, use_cache=True):
    """Fetch the main source files in parallel and index symbols and imports."""
    key = f"{info['full_name']}|{info['branch']}".lower()
    if use_cache:
        hit = cache_get("index", key, SCAN_TTL)
        if hit is not None:
            return hit
    owner, repo, branch = info["owner"], info["repo"], info["branch"]
    pathset = set(info["paths"])
    files = pick_code_files(info["clean_paths"])
    with ThreadPoolExecutor(max_workers=8) as ex:
        texts = list(ex.map(lambda p: fetch_file(owner, repo, branch, p, 6000), files))
    index = {}
    for p, text in zip(files, texts):
        if text:
            index[p] = {"symbols": extract_symbols(text),
                        "imports": extract_imports(p, text, pathset),
                        "text": text}
    cache_put("index", key, index)
    return index


def import_map(index):
    dependents = {}
    edges = 0
    for p, d in index.items():
        for t in d["imports"]:
            dependents.setdefault(t, []).append(p)
            edges += 1
    rows = [{"File": p, "Imported by": len(v),
             "Imports": len(index[p]["imports"]) if p in index else 0,
             "Used by": ", ".join(v[:4])}
            for p, v in sorted(dependents.items(), key=lambda kv: -len(kv[1]))][:12]
    return {"rows": rows, "edges": edges, "dependents": dependents}


def import_dot(index, limit=60):
    lines = ["digraph G {", "rankdir=LR;", 'bgcolor="transparent";',
             'node [shape=box, style="rounded,filled", fillcolor="#161b22", '
             'color="#30363d", fontcolor="#e6edf3", fontsize=10];',
             'edge [color="#6e7681"];']
    n = 0
    for p, d in index.items():
        for t in d["imports"]:
            if n >= limit:
                break
            lines.append(f'"{p.replace(chr(34), "")}" -> "{t.replace(chr(34), "")}";')
            n += 1
    lines.append("}")
    return "\n".join(lines) if n else ""


def test_map(paths):
    """Which tests are named after which source files, and what has no matching test."""
    code = [p for p in paths if p.lower().endswith(CODE_EXT) and not _is_test_path(p)
            and not p.lower().endswith(".d.ts")]
    stem = lambda p: posixpath.splitext(p.split("/")[-1])[0]
    by_stem = {}
    for p in code:
        by_stem.setdefault(stem(p), []).append(p)
    pairs, covered = [], set()
    tests = [p for p in paths if _test_target(p)]
    for t in tests:
        srcs = by_stem.get(_test_target(t), [])
        if srcs:
            pairs.append({"test": t, "covers": srcs[:3]})
            covered.update(srcs)
    skip = {"__init__", "setup", "conftest", "config", "settings", "index"}
    untested = sorted((p for p in code if p not in covered and stem(p) not in skip),
                      key=lambda p: (p.count("/"), p))[:15]
    return {"pairs": pairs[:20], "untested": untested, "test_count": len(tests)}


# --------------------------------------------------------------------------
# Change locator: which files to touch for a task
# --------------------------------------------------------------------------
STOP = set("""a an the and or to of in on for with is are be this that it as at by from fix add
update bug issue make when not should would can could use using new support feature please
need want also but all any some more than then into out up about how why what which does
doesn have has had was were will just like get set""".split())


def keywords(text):
    out = []
    for w in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", text):
        parts = [x for x in re.split(r"_|(?<=[a-z0-9])(?=[A-Z])", w) if x]
        for x in [w] + parts:
            x = x.lower()
            if len(x) > 4 and x.endswith("s"):
                x = x[:-1]
            if len(x) > 2 and x not in STOP and x not in out:
                out.append(x)
    return out[:25]


def locate(task, info, index=None, top=6):
    """Rank repo files for a task. Path matches always count; with an index,
    defined symbols and file contents count too."""
    kws = keywords(task)
    if not kws:
        return []
    scored = {}

    def add(p, pts, why):
        s = scored.setdefault(p, {"path": p, "score": 0, "reasons": []})
        s["score"] += pts
        if why not in s["reasons"]:
            s["reasons"].append(why)

    for p in info["clean_paths"]:
        low = p.lower()
        name = low.split("/")[-1]
        for k in kws:
            if k in name:
                add(p, 4, f"file name matches '{k}'")
            elif k in low:
                add(p, 2, f"path matches '{k}'")
    if index:
        for p, d in index.items():
            tl = d["text"].lower()
            syms = [s for s in d["symbols"] if any(k in s.lower() for k in kws)]
            if syms:
                add(p, 3 * min(len(syms), 3), "defines " + ", ".join(syms[:3]))
            hits = [k for k in kws if k in tl]
            if hits:
                weight = sum(min(tl.count(k), 3) for k in hits)
                add(p, min(weight, 6), "mentions " + ", ".join(hits[:4]))
    results = sorted(scored.values(), key=lambda s: (-s["score"], s["path"]))[:top]

    deps = import_map(index)["dependents"] if index else {}
    tests_for = {}
    for pair in test_map(info["clean_paths"])["pairs"]:
        for src in pair["covers"]:
            tests_for.setdefault(src, []).append(pair["test"])
    for r in results:
        r["dependents"] = deps.get(r["path"], [])[:5]
        r["tests"] = tests_for.get(r["path"], [])[:3]
        r["symbols"] = index[r["path"]]["symbols"][:8] if index and r["path"] in index else []
    return results


ISSUE_RE = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)/(?:issues|pull)/(\d+)")


def fetch_issue(text):
    """Return issue details if text contains a GitHub issue URL, else None."""
    m = ISSUE_RE.search(text or "")
    if not m:
        return None
    owner, repo, num = m.groups()
    d = gh_get(f"/repos/{owner}/{repo}/issues/{num}")
    return {"owner": owner, "repo": repo, "number": int(num), "title": d["title"],
            "body": (d.get("body") or "")[:3000], "url": d["html_url"],
            "labels": [x["name"] for x in d.get("labels", [])]}


def branch_name(title, number=None):
    low = title.lower()
    if re.search(r"\b(bug|fix|crash|error|broken|fail)", low):
        kind = "fix"
    elif re.search(r"\b(docs?|readme|typo)", low):
        kind = "docs"
    else:
        kind = "feat"
    slug = re.sub(r"[^a-z0-9]+", "-", low).strip("-")[:40].strip("-") or "change"
    return f"{kind}/{number}-{slug}" if number else f"{kind}/{slug}"


def test_commands(info):
    return [cmd for label, cmd in info["commands"] if label in ("Test", "Lint")]


# --------------------------------------------------------------------------
# Scores
# --------------------------------------------------------------------------
def run_checks(meta, paths, readme):
    low = [p.lower() for p in paths]

    def has(fn):
        return any(fn(p) for p in low)

    top = lambda p: "/" not in p
    return [
        {"name": "README", "weight": 15,
         "ok": has(lambda p: top(p) and p.startswith("readme")),
         "fix": "Add a README that explains what the project does and how to run it."},
        {"name": "Detailed README", "weight": 10,
         "ok": len(readme) > 800,
         "fix": "Expand the README with install steps, usage examples and screenshots."},
        {"name": "License", "weight": 15,
         "ok": has(lambda p: top(p) and (p.startswith("license") or p.startswith("copying"))),
         "fix": "Add a LICENSE file (MIT is a safe default) so others can legally use it."},
        {"name": "CONTRIBUTING guide", "weight": 15,
         "ok": has(lambda p: p in ("contributing.md", ".github/contributing.md", "docs/contributing.md")),
         "fix": "Add CONTRIBUTING.md so newcomers know how to set up and send a PR."},
        {"name": "Code of Conduct", "weight": 5,
         "ok": has(lambda p: p.endswith("code_of_conduct.md")),
         "fix": "Add a CODE_OF_CONDUCT.md to set community expectations."},
        {"name": ".gitignore", "weight": 5,
         "ok": ".gitignore" in low,
         "fix": "Add a .gitignore so build files and secrets are not committed."},
        {"name": "Tests", "weight": 10,
         "ok": has(lambda p: re.search(r"(^|/)(tests?|__tests__|spec)(/|$)|test_|_test\.|\.test\.|\.spec\.", p)),
         "fix": "Add at least a few automated tests."},
        {"name": "CI workflow", "weight": 10,
         "ok": has(lambda p: p.startswith(".github/workflows/")),
         "fix": "Add a GitHub Actions workflow that runs tests on every PR."},
        {"name": "Issue/PR templates", "weight": 5,
         "ok": has(lambda p: "issue_template" in p or "pull_request_template" in p),
         "fix": "Add issue and pull request templates in .github/."},
        {"name": "Repo description", "weight": 5,
         "ok": bool(meta.get("description")),
         "fix": "Add a one-line description in the GitHub repo settings."},
        {"name": "Topics", "weight": 5,
         "ok": bool(meta.get("topics")),
         "fix": "Add topics (e.g. python, ai) so people can discover the repo."},
    ]


def readiness(info):
    """How easy it is for a newcomer to land a first PR. Separate from health."""
    ok = {c["name"]: bool(c["ok"]) for c in info["checks"]}
    days = info["days_since_push"]
    spec = [
        ("Setup commands detectable", 20, bool(info["commands"]),
         "Add a manifest (requirements.txt, package.json) and document the run command."),
        ("Tests to run", 15, ok.get("Tests", False),
         "Add tests so contributors can verify their changes."),
        ("CI on pull requests", 10, ok.get("CI workflow", False),
         "Add a workflow that runs tests on every PR."),
        ("CONTRIBUTING guide", 15, ok.get("CONTRIBUTING guide", False),
         "Add CONTRIBUTING.md with setup and PR steps."),
        ("Open good first issues", 15, bool(info["good_first_issues"]),
         "Label a few small issues 'good first issue'."),
        ("Active in the last 90 days", 10, days is not None and days <= 90,
         "Recent commits show maintainers will review PRs."),
        ("Issue and PR templates", 5, ok.get("Issue/PR templates", False),
         "Add templates so reports and PRs arrive with the right details."),
        ("Detailed README", 10, ok.get("Detailed README", False),
         "Expand the README with install and usage steps."),
    ]
    items = [{"name": n, "points": p, "ok": o, "hint": h} for n, p, o, h in spec]
    return {"score": sum(i["points"] for i in items if i["ok"]), "items": items}


def build_context(info):
    owner, repo, branch = info["owner"], info["repo"], info["branch"]
    paths = info["clean_paths"]
    langs = ", ".join(f"{n} {p}%" for n, p in info["language_pct"][:5]) or "unknown"
    layout = "\n".join(f"{r['Path']} ({r['Files']} files)" for r in info["project_map"])
    cmds = "\n".join(f"{label}: {cmd}" for label, cmd in info["commands"]) or "none detected"
    parts = [
        f"Repository: {info['full_name']}",
        f"Description: {info['description'] or 'none'}",
        f"Languages: {langs}",
        "",
        "Top-level layout:\n" + layout,
        "",
        "Detected commands:\n" + cmds,
        "",
        "File tree (truncated):\n" + "\n".join(paths[:120]),
    ]
    roots = [p for p in paths if "/" not in p and p in MANIFESTS][:4]
    for p in roots:
        parts.append(f"\n--- {p} ---\n{fetch_file(owner, repo, branch, p, 1500)}")
    if info["readme"]:
        parts.append(f"\n--- existing README ---\n{info['readme'][:2500]}")
    entries = sorted((p for p in paths if p.split("/")[-1] in ENTRY_NAMES),
                     key=lambda p: p.count("/"))[:3]
    for p in entries:
        parts.append(f"\n--- {p} ---\n{fetch_file(owner, repo, branch, p, 2000)}")
    return "\n".join(parts)[:MAX_CONTEXT]


def scan_repo(url, use_cache=True):
    owner, repo = parse_repo(url)
    key = f"{owner}/{repo}".lower()
    if use_cache:
        hit = cache_get("scan", key, SCAN_TTL)
        if hit is not None:
            return hit
    meta = gh_get(f"/repos/{owner}/{repo}")
    branch = meta["default_branch"]
    tree = gh_get(f"/repos/{owner}/{repo}/git/trees/{branch}?recursive=1")
    paths = [t["path"] for t in tree.get("tree", []) if t["type"] == "blob"]
    langs = gh_get(f"/repos/{owner}/{repo}/languages")
    readme_path = next((p for p in paths if "/" not in p and p.lower().startswith("readme")), None)
    readme = fetch_file(owner, repo, branch, readme_path, 4000) if readme_path else ""

    clean_paths = [p for p in paths
                   if not any(s in p for s in SKIP) and not p.lower().endswith(SKIP_EXT)]
    total_bytes = sum(langs.values()) or 1
    checks = run_checks(meta, paths, readme)
    info = {
        "owner": owner, "repo": repo, "branch": branch,
        "full_name": meta["full_name"],
        "url": meta.get("html_url", f"https://github.com/{owner}/{repo}"),
        "description": meta.get("description"),
        "stars": meta.get("stargazers_count", 0),
        "forks": meta.get("forks_count", 0),
        "open_issues": meta.get("open_issues_count", 0),
        "days_since_push": days_since(meta.get("pushed_at")),
        "languages": sorted(langs, key=langs.get, reverse=True),
        "language_pct": [(k, round(v * 100 / total_bytes, 1))
                         for k, v in sorted(langs.items(), key=lambda kv: -kv[1])],
        "paths": paths,
        "clean_paths": clean_paths,
        "readme": readme,
        "checks": checks,
        "score": sum(c["weight"] for c in checks if c["ok"]),
        "project_map": project_map(clean_paths),
        "key_files": key_files(clean_paths),
        "commands": detect_commands(owner, repo, branch, paths),
        "good_first_issues": labeled_issues(owner, repo, "good first issue"),
        "help_wanted": labeled_issues(owner, repo, "help wanted"),
        "commits": recent_commits(owner, repo),
        "contributors": top_contributors(owner, repo),
    }
    info["readiness"] = readiness(info)
    info["tests"] = test_map(clean_paths)
    info["context"] = build_context(info)
    cache_put("scan", key, info)
    return info


def verdict(score):
    if score >= 80:
        return "Healthy"
    if score >= 50:
        return "Needs work"
    return "Poor"


def badge_markdown(info):
    color = "brightgreen" if info["score"] >= 80 else "yellow" if info["score"] >= 50 else "red"
    return (f"![Repo health](https://img.shields.io/badge/"
            f"repo%20health-{info['score']}%2F100-{color})")


# --------------------------------------------------------------------------
# LLM backends (streaming)
# --------------------------------------------------------------------------
def _model_name(backend):
    if backend == "gemma":
        return os.getenv("OLLAMA_MODEL", "gemma4:e4b")
    return os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


def stream_llm(prompt, backend="gemini", max_tokens=1200):
    """Yield text chunks as the model produces them."""
    if backend == "gemma":
        import ollama
        opts = {"num_predict": max_tokens, "num_ctx": NUM_CTX, "temperature": 0.3}
        for part in ollama.chat(model=_model_name("gemma"),
                                messages=[{"role": "user", "content": prompt}],
                                stream=True, options=opts, keep_alive=KEEP_ALIVE):
            piece = part["message"]["content"]
            if piece:
                yield piece
        return
    from google import genai
    from google.genai import types
    client = genai.Client()  # reads GEMINI_API_KEY
    model = _model_name("gemini")
    cfg = None
    if "2.5-flash" in model:  # skip hidden "thinking" tokens: faster, same quality for docs
        cfg = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=0))
    for chunk in client.models.generate_content_stream(model=model, contents=prompt, config=cfg):
        if chunk.text:
            yield chunk.text


def ask_llm(prompt, backend="gemini", max_tokens=1200):
    return "".join(stream_llm(prompt, backend, max_tokens))


def warm_up(backend):
    """Load the local model into memory so the first real request is fast."""
    if backend != "gemma":
        return False
    try:
        import ollama
        ollama.generate(model=_model_name("gemma"), prompt="", keep_alive=KEEP_ALIVE)
        return True
    except Exception:
        return False


def backend_status(backend):
    """(ok, message) so the UI can show setup problems before a request fails."""
    if backend == "gemma":
        name = _model_name("gemma")
        try:
            import ollama
            res = ollama.list()
            models = res["models"] if isinstance(res, dict) else getattr(res, "models", [])

            def model_id(m):
                if isinstance(m, dict):
                    return m.get("model") or m.get("name") or ""
                return getattr(m, "model", None) or getattr(m, "name", "") or ""
            names = [model_id(m) for m in models]
            if any(n == name or n.split(":")[0] == name for n in names):
                return True, f"Ollama running, {name} available"
            return False, f"Ollama is running but {name} is not pulled. Run: ollama pull {name}"
        except Exception:
            return False, "Ollama is not reachable. Start it with: ollama serve"
    if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
        return True, f"Gemini key found, model {_model_name('gemini')}"
    return False, "GEMINI_API_KEY is not set in .env"


RULES = (
    "You are GitWell, an open-source documentation expert writing for developers "
    "who are new to this codebase. "
    "Use ONLY facts visible in the repository context below. If something is "
    "unknown, write a clearly marked TODO instead of inventing it. "
    "Write in plain, direct developer language. No emoji, no marketing tone. "
    "Output only the requested Markdown, with no preamble and no code fence "
    "around the whole document."
)

DOCS = {
    "REPO_GUIDE.md": (
        "Write REPO_GUIDE.md for a developer who wants to contribute but has never "
        "seen this codebase. Use these sections: What it does (2-3 sentences), "
        "Tech stack, How the code is organised (each top-level folder and key file "
        "with one line on its job), How it runs (follow the entry point and describe "
        "the data/control flow step by step), Key concepts (project-specific terms), "
        "Run it locally (use the detected commands), Run the tests, Where to start "
        "reading (ordered list of files), Where to make common changes (for example "
        "'to add X, edit Y'), Known gaps (missing tests, docs, error handling)."),
    "README.md": (
        "Write a concise, complete README.md: title, one-line pitch, brief features, "
        "tech stack, installation, usage, project structure, contributing, and license "
        "section. Keep each section brief and avoid repeating information."),
    "CONTRIBUTING.md": (
        "Write a welcoming CONTRIBUTING.md for first-time contributors: how to fork "
        "and clone, set up the environment using the repo's real files, run the "
        "project, branch naming, commit and PR steps, code style, and how to ask for help."),
    "ARCHITECTURE.md": (
        "Write ARCHITECTURE.md: a short overview of how the project is organised, "
        "what each main folder or file does, how data and control flow, and where a "
        "newcomer should start reading."),
    "GOOD_FIRST_ISSUES.md": (
        "Suggest 6 good-first-issue ideas, easiest first. For each: a title, why it is "
        "beginner-friendly, the files to touch, and 2-3 acceptance-checklist items. "
        "Base them on real gaps you can see (missing docs, tests, error handling, setup)."),
}

# Output token caps (local model speed) and context size per doc.
DOC_TOKENS = {"REPO_GUIDE.md": 1800, "README.md": 900, "CONTRIBUTING.md": 1200,
              "ARCHITECTURE.md": 1000, "GOOD_FIRST_ISSUES.md": 1200}
DOC_CONTEXT = {"README.md": 7000, "CONTRIBUTING.md": 7000, "GOOD_FIRST_ISSUES.md": 9000}
DOC_DEST = {"README.md": "README.md", "CONTRIBUTING.md": "CONTRIBUTING.md",
            "REPO_GUIDE.md": "docs/REPO_GUIDE.md", "ARCHITECTURE.md": "docs/ARCHITECTURE.md",
            "GOOD_FIRST_ISSUES.md": "docs/GOOD_FIRST_ISSUES.md"}

ASK_RULES = (
    "You are GitWell, answering questions from a developer who is new to this repo. "
    "Use ONLY the repository context below. Reference real file paths from the context. "
    "If the context does not contain the answer, say so and name the file most likely "
    "to hold it. Be concise and direct. No emoji."
)

PLAN_TASK = (
    "Write a contribution plan for the task below, for a developer new to this repo. "
    "Use exactly these Markdown sections: "
    "## Summary (what the task asks, 2-3 sentences); "
    "## Where to change (a list: file path, then what to change in it; prefer the "
    "candidate files provided and only add other files when clearly needed); "
    "## Steps (numbered implementation steps); "
    "## Tests (what to add or run, using the detected commands); "
    "## Risks (what could break and which dependents to re-check); "
    "## PR description (a title and a short body). "
    "Do not invent functions or files that are not shown in the context."
)


def clean(text):
    t = text.strip()
    t = re.sub(r"^```(?:markdown|md)?\s*\n", "", t)
    t = re.sub(r"\n```\s*$", "", t)
    return t.strip()


def doc_context(info, doc_name):
    return info["context"][:DOC_CONTEXT.get(doc_name, MAX_CONTEXT)]


def _doc_key(doc_name, info, backend):
    ctx = hashlib.sha1(doc_context(info, doc_name).encode("utf-8")).hexdigest()
    return f"{info['full_name']}|{doc_name}|{backend}|{_model_name(backend)}|{ctx}"


def doc_is_cached(doc_name, info, backend):
    return cache_get("doc", _doc_key(doc_name, info, backend)) is not None


def stream_doc(doc_name, info, backend="gemini", use_cache=True):
    """Yield a doc as it is written. A cache hit is yielded in one piece."""
    key = _doc_key(doc_name, info, backend)
    if use_cache:
        hit = cache_get("doc", key)
        if hit:
            yield hit["text"]
            return
    prompt = (f"{RULES}\n\nTASK: {DOCS[doc_name]}\n\n"
              f"=== REPOSITORY CONTEXT ===\n{doc_context(info, doc_name)}\n=== END CONTEXT ===")
    parts = []
    for chunk in stream_llm(prompt, backend, DOC_TOKENS.get(doc_name, 1200)):
        parts.append(chunk)
        yield chunk
    text = clean("".join(parts))
    if text:
        cache_put("doc", key, {"text": text})


def generate(doc_name, info, backend="gemini", use_cache=True):
    return clean("".join(stream_doc(doc_name, info, backend, use_cache)))


def ask_stream(question, info, backend="gemini", history=None):
    """Answer a question about the repo. Files named in the question are fetched."""
    q = question.lower()
    mentioned = [p for p in info["clean_paths"]
                 if p.lower() in q
                 or (len(p.split("/")[-1]) > 3 and p.split("/")[-1].lower() in q)][:2]
    extra = "".join(
        f"\n--- {p} ---\n{fetch_file(info['owner'], info['repo'], info['branch'], p, 3500)}"
        for p in mentioned)
    convo = "".join(
        f"{'Developer' if m['role'] == 'user' else 'GitWell'}: {m['content']}\n"
        for m in (history or [])[-4:])
    prompt = (f"{ASK_RULES}\n\n=== REPOSITORY CONTEXT ===\n{info['context']}{extra}\n"
              f"=== END CONTEXT ===\n\n{convo}Developer: {question}\nGitWell:")
    yield from stream_llm(prompt, backend, 700)


def ask(question, info, backend="gemini", history=None):
    return "".join(ask_stream(question, info, backend, history)).strip()


def plan_context(task, info, hits, index):
    parts = [f"TASK:\n{task}", "", "CANDIDATE FILES (ranked by a keyword and symbol search):"]
    for h in hits:
        line = f"- {h['path']}: {'; '.join(h['reasons'])}"
        if h.get("symbols"):
            line += f" | symbols: {', '.join(h['symbols'])}"
        if h.get("dependents"):
            line += f" | imported by: {', '.join(h['dependents'])}"
        if h.get("tests"):
            line += f" | tests: {', '.join(h['tests'])}"
        parts.append(line)
    for h in hits[:3]:
        text = (index or {}).get(h["path"], {}).get("text") or fetch_file(
            info["owner"], info["repo"], info["branch"], h["path"], 1500)
        if text:
            parts.append(f"\n--- {h['path']} (start of file) ---\n{text[:1500]}")
    parts += ["", "DETECTED COMMANDS:",
              "\n".join(f"{a}: {b}" for a, b in info["commands"]) or "none",
              "", "REPO OVERVIEW:", info["context"][:3500]]
    return "\n".join(parts)[:11000]


def stream_plan(task, info, backend="gemini", hits=None, index=None):
    """Yield a contribution plan for a task. Cached per repo, task and model."""
    hits = hits if hits is not None else locate(task, info, index)
    key = (f"{info['full_name']}|{backend}|{_model_name(backend)}|"
           f"{hashlib.sha1(task.encode('utf-8')).hexdigest()}")
    hit = cache_get("plan", key)
    if hit:
        yield hit["text"]
        return
    prompt = (f"{RULES}\n\nTASK: {PLAN_TASK}\n\n"
              f"=== REPOSITORY CONTEXT ===\n{plan_context(task, info, hits, index)}\n=== END CONTEXT ===")
    parts = []
    for chunk in stream_llm(prompt, backend, 1800):
        parts.append(chunk)
        yield chunk
    text = clean("".join(parts))
    if text:
        cache_put("plan", key, {"text": text})


def diagnosis_markdown(info):
    failing = sorted((c for c in info["checks"] if not c["ok"]), key=lambda c: -c["weight"])
    lines = [f"# GitWell report: {info['full_name']}", "",
             f"Health score: **{info['score']}/100** ({verdict(info['score'])})",
             f"Contributor readiness: **{info['readiness']['score']}/100**", "",
             badge_markdown(info), "", "## Gaps (highest impact first)"]
    lines += [f"- {c['name']} (+{c['weight']} pts): {c['fix']}" for c in failing] or ["- None."]
    not_ready = [i for i in info["readiness"]["items"] if not i["ok"]]
    if not_ready:
        lines += ["", "## Contributor readiness gaps", ""]
        lines += [f"- {i['name']} (+{i['points']} pts): {i['hint']}" for i in not_ready]
    if info["commands"]:
        lines += ["", "## Run locally", "", "```bash"]
        for label, cmd in info["commands"]:
            lines.append(f"# {label}")
            lines.append(cmd)
        lines.append("```")
    if info["key_files"]:
        lines += ["", "## Start reading here", ""]
        lines += [f"- `{k['path']}`: {k['why']}" for k in info["key_files"]]
    if info["good_first_issues"]:
        lines += ["", "## Open good first issues", ""]
        lines += [f"- [#{i['number']} {i['title']}]({i['url']})" for i in info["good_first_issues"]]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _write(path, text, force):
    if os.path.exists(path) and not force:
        print(f"  skipped {path} (exists, use --force to overwrite)")
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(f"  wrote {path}")


def main():
    ap = argparse.ArgumentParser(description="GitWell: repo analysis, change locator and docs generator")
    ap.add_argument("repo", help="GitHub URL or owner/repo")
    ap.add_argument("--backend", choices=["gemini", "gemma"], default="gemini")
    ap.add_argument("--out", default="gitwell-output", help="folder for generated files")
    ap.add_argument("--plan", metavar="TASK_OR_ISSUE_URL",
                    help="print which files to change and a contribution plan, then exit")
    ap.add_argument("--write-to", metavar="DIR",
                    help="write generated docs into a local clone (README.md, CONTRIBUTING.md, docs/)")
    ap.add_argument("--force", action="store_true", help="overwrite existing files with --write-to")
    ap.add_argument("--no-cache", action="store_true", help="ignore cached scans and docs")
    args = ap.parse_args()
    use_cache = not args.no_cache

    try:
        info = scan_repo(args.repo, use_cache)
    except Exception as e:
        sys.exit(f"Error: {e}")

    if args.plan:
        task = args.plan
        try:
            issue = fetch_issue(task)
        except Exception as e:
            sys.exit(f"Error: {e}")
        if issue:
            task = f"{issue['title']}\n\n{issue['body']}"
            print(f"Issue #{issue['number']}: {issue['title']}")
            print(f"Suggested branch: {branch_name(issue['title'], issue['number'])}\n")
        else:
            print(f"Suggested branch: {branch_name(task[:60])}\n")
        index = get_index(info, use_cache)
        hits = locate(task, info, index)
        print("Where to change:")
        for h in hits:
            print(f"  {h['path']}  (score {h['score']}: {'; '.join(h['reasons'])})")
        print()
        try:
            for chunk in stream_plan(task, info, args.backend, hits, index):
                print(chunk, end="", flush=True)
        except Exception as e:
            sys.exit(f"\nLLM error: {e}")
        print()
        return

    report = diagnosis_markdown(info)
    print(report)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "REPORT.md"), "w", encoding="utf-8") as f:
        f.write(report)
    for name in DOCS:
        print(f"\nWriting {name} ...")
        try:
            text = generate(name, info, args.backend, use_cache)
        except Exception as e:
            sys.exit(f"LLM error: {e}")
        with open(os.path.join(args.out, name), "w", encoding="utf-8") as f:
            f.write(text)
        if args.write_to:
            _write(os.path.join(args.write_to, DOC_DEST[name]), text, args.force)
    print(f"\nDone. Files saved in ./{args.out}/")


if __name__ == "__main__":
    main()