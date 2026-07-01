# Safe Push/Pull Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build two safe Windows commands that synchronize the project while always versioning durable football data and never committing caches, logs, or temporary artifacts.

**Architecture:** `scripts/project_sync.py` owns path policy, SQLite validation, Git state checks, and push/pull orchestration. Two small PowerShell launchers resolve the repository Python and delegate to that tested core. Tests use temporary repositories and local bare remotes, so no network or real project history is touched.

**Tech Stack:** Python 3.12 standard library (`argparse`, `pathlib`, `sqlite3`, `subprocess`), PowerShell, Git, `unittest`.

## Global Constraints

- Push only from `main` to `origin/main`; never force-push, merge, rebase, reset, or delete files.
- Pull aborts on any tracked or non-ignored untracked local change and updates only by fast-forward.
- Durable data includes `data/worldcup.sqlite`, `data/models/**`, `data/fixtures/**`, `data/evidence/reviewed-json/**`, and `data/precomputed/**`.
- Disposable data includes `data/cache/**`, `output/**`, `.codex-remote-attachments/**`, logs, SQLite WAL/SHM/journals, backups, and tool caches.
- Push runs a SQLite WAL checkpoint, integrity check, and the complete unit-test suite before commit.
- No third-party dependency is added.

---

### Task 1: Path policy and SQLite safety

**Files:**
- Create: `scripts/project_sync.py`
- Create: `tests/test_project_sync.py`

**Interfaces:**
- Produces: `DURABLE_PATHS: tuple[str, ...]`, `FORBIDDEN_PREFIXES: tuple[str, ...]`, `is_forbidden_path(path: str) -> bool`, `checkpoint_and_validate_sqlite(db_path: Path) -> None`.
- Consumes: Python standard library only.

- [ ] **Step 1: Write failing policy and SQLite tests**

Create tests that assert durable paths are not forbidden, cache/output/attachment/log/WAL paths are forbidden, and a temporary WAL database is checkpointed and returns `ok` from `PRAGMA integrity_check`.

```python
def test_durable_data_is_allowed_and_disposable_data_is_forbidden(self):
    for path in (
        "data/worldcup.sqlite", "data/models/model.joblib",
        "data/fixtures/stats.csv", "data/evidence/reviewed-json/a.json",
        "data/precomputed/penalties/a.json",
    ):
        self.assertFalse(project_sync.is_forbidden_path(path))
    for path in (
        "data/cache/a.html", "output/a.csv", ".codex-remote-attachments/a.png",
        "server.log", "data/worldcup.sqlite-wal",
    ):
        self.assertTrue(project_sync.is_forbidden_path(path))

def test_checkpoint_moves_wal_data_into_valid_database(self):
    db = self.root / "worldcup.sqlite"
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE stats(value INTEGER)")
    con.execute("INSERT INTO stats VALUES(7)")
    con.commit()
    project_sync.checkpoint_and_validate_sqlite(db)
    self.assertEqual("ok", sqlite3.connect(db).execute("PRAGMA integrity_check").fetchone()[0])
```

- [ ] **Step 2: Run tests and verify RED**

Run: `$env:PYTHONPATH='scripts'; python -m unittest tests.test_project_sync -v`

Expected: import failure for missing `project_sync`.

- [ ] **Step 3: Implement path policy and SQLite validation**

Implement normalized `/` path matching, suffix checks for logs and SQLite auxiliaries, `PRAGMA wal_checkpoint(TRUNCATE)`, and `PRAGMA integrity_check`. Raise `SyncError` with a Spanish actionable message when the database is missing, locked, or corrupt.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `$env:PYTHONPATH='scripts'; python -m unittest tests.test_project_sync -v`

Expected: policy and SQLite tests pass.

- [ ] **Step 5: Commit**

```powershell
git add scripts/project_sync.py tests/test_project_sync.py
git commit -m "feat(sync): validate durable project data"
```

### Task 2: Git state inspection and staging guard

**Files:**
- Modify: `scripts/project_sync.py`
- Modify: `tests/test_project_sync.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `run_git(root: Path, *args: str) -> str`, `porcelain_entries(root: Path) -> list[StatusEntry]`, `validate_repository(root: Path) -> None`, `stage_and_validate(root: Path) -> list[str]`.
- Consumes: `is_forbidden_path`, `DURABLE_PATHS`, `SyncError` from Task 1.

- [ ] **Step 1: Write failing temporary-repository tests**

Create a Git repository in `TemporaryDirectory`, configure a local test identity, add durable files plus ignored cache/log/output files, then assert:

```python
staged = project_sync.stage_and_validate(repo)
self.assertIn("data/worldcup.sqlite", staged)
self.assertIn("data/evidence/reviewed-json/new.json", staged)
self.assertNotIn("data/cache/page.html", staged)
self.assertNotIn("output/report.csv", staged)
```

Add tests that force-add `output/tracked.log` and expect `SyncError`, and that verify every durable root exists and is not ignored using `git check-ignore`.

- [ ] **Step 2: Run tests and verify RED**

Run: `$env:PYTHONPATH='scripts'; python -m unittest tests.test_project_sync -v`

Expected: failures because Git inspection functions do not exist.

- [ ] **Step 3: Implement Git inspection and ignore rules**

Use `git status --porcelain=v1 -z --untracked-files=all`, `git diff --cached --name-only -z`, `git ls-files --others --exclude-standard -z`, and `git check-ignore`. Expand `.gitignore` with:

```gitignore
data/cache/
output/
.codex-remote-attachments/
```

`stage_and_validate` runs `git add -A`, rejects any staged forbidden path, rejects remaining non-ignored changes, and returns the staged path list.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `$env:PYTHONPATH='scripts'; python -m unittest tests.test_project_sync -v`

Expected: all path, SQLite, and staging tests pass.

- [ ] **Step 5: Commit**

```powershell
git add .gitignore scripts/project_sync.py tests/test_project_sync.py
git commit -m "feat(sync): guard project staging"
```

### Task 3: Safe pull orchestration

**Files:**
- Modify: `scripts/project_sync.py`
- Modify: `tests/test_project_sync.py`
- Create: `scripts/pull_project.ps1`

**Interfaces:**
- Produces: `pull_project(root: Path, remote: str = "origin", branch: str = "main") -> str` and CLI subcommand `pull`.
- Consumes: repository validation, porcelain parsing, SQLite validation, and `run_git`.

- [ ] **Step 1: Write failing pull integration tests**

Create a local bare remote with two clones. Assert pull aborts when one clone has a modified tracked fixture or a non-ignored reviewed JSON, succeeds when the only local file is ignored cache data, and fast-forwards to the other clone's commit.

```python
with self.assertRaisesRegex(project_sync.SyncError, "push_project.ps1"):
    project_sync.pull_project(clone)
self.assertEqual(remote_head, project_sync.run_git(clone, "rev-parse", "HEAD"))
```

- [ ] **Step 2: Run tests and verify RED**

Run: `$env:PYTHONPATH='scripts'; python -m unittest tests.test_project_sync.ProjectPullTests -v`

Expected: failure because `pull_project` is absent.

- [ ] **Step 3: Implement safe pull and PowerShell launcher**

Require `main`, a configured `origin`, and a clean versionable working tree. Run `git fetch origin`, then `git merge --ff-only origin/main`, then validate SQLite when present. The launcher executes the repository's configured Python or `python`, calls `project_sync.py pull`, and exits with the same status.

- [ ] **Step 4: Run tests and launcher smoke test**

Run:

```powershell
$env:PYTHONPATH='scripts'
python -m unittest tests.test_project_sync.ProjectPullTests -v
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/pull_project.ps1 -WhatIf
```

Expected: integration tests pass; `-WhatIf` reports checks without fetching or merging.

- [ ] **Step 5: Commit**

```powershell
git add scripts/project_sync.py scripts/pull_project.ps1 tests/test_project_sync.py
git commit -m "feat(sync): add safe pull command"
```

### Task 4: Safe push orchestration

**Files:**
- Modify: `scripts/project_sync.py`
- Modify: `tests/test_project_sync.py`
- Create: `scripts/push_project.ps1`

**Interfaces:**
- Produces: `push_project(root: Path, message: str, remote: str = "origin", branch: str = "main", run_tests: bool = True) -> str` and CLI subcommand `push`.
- Consumes: SQLite validation, staging guard, repository validation, and `run_git`.

- [ ] **Step 1: Write failing push integration tests**

Use a bare local remote. Assert empty messages fail, a remote-ahead branch fails before commit, and a successful push commits durable data while excluded files remain untracked/ignored and absent from `git ls-tree -r HEAD`.

```python
head = project_sync.push_project(clone, "data: update match statistics", run_tests=False)
tree = project_sync.run_git(clone, "ls-tree", "-r", "--name-only", head).splitlines()
self.assertIn("data/worldcup.sqlite", tree)
self.assertIn("data/precomputed/match.json", tree)
self.assertNotIn("data/cache/page.html", tree)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `$env:PYTHONPATH='scripts'; python -m unittest tests.test_project_sync.ProjectPushTests -v`

Expected: failure because `push_project` is absent.

- [ ] **Step 3: Implement safe push and PowerShell launcher**

Validate message, branch, remote, SQLite, durable paths, and tests. Fetch before staging; use `git merge-base --is-ancestor origin/main HEAD` to reject a remote-ahead local branch. Stage and validate, commit only if the index is non-empty, push `origin main`, and return the published SHA. Add CLI flags `--message`, `--skip-tests`, and `--what-if`; PowerShell exposes `-Message`, `-SkipTests`, and `-WhatIf`.

- [ ] **Step 4: Run tests and launcher dry-run**

Run:

```powershell
$env:PYTHONPATH='scripts'
python -m unittest tests.test_project_sync.ProjectPushTests -v
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/push_project.ps1 -Message "test" -WhatIf
```

Expected: integration tests pass; dry-run lists durable changes and excluded paths without committing or pushing.

- [ ] **Step 5: Commit**

```powershell
git add scripts/project_sync.py scripts/push_project.ps1 tests/test_project_sync.py
git commit -m "feat(sync): add safe push command"
```

### Task 5: Documentation and full verification

**Files:**
- Modify: `README.md`
- Modify: `tests/test_project_sync.py`

**Interfaces:**
- Consumes: final CLI behavior from Tasks 3 and 4.
- Produces: user-facing usage instructions and complete regression coverage.

- [ ] **Step 1: Add CLI contract tests**

Test `python scripts/project_sync.py --help`, `push --help`, and `pull --help`; assert exit code `0` and that required flags appear.

- [ ] **Step 2: Run contract tests and verify RED if documentation/flags are incomplete**

Run: `$env:PYTHONPATH='scripts'; python -m unittest tests.test_project_sync.ProjectSyncCliTests -v`

Expected: all documented CLI forms must be accepted.

- [ ] **Step 3: Document normal operation and recovery**

Add a README section showing:

```powershell
.\scripts\push_project.ps1 -Message "data: update deep stats and results"
.\scripts\pull_project.ps1
```

Explain that pull stops on local durable changes, push includes the database/models/evidence/precomputed data, ignored caches are never uploaded, and the application should be closed during synchronization.

- [ ] **Step 4: Run complete verification**

Run:

```powershell
$env:PYTHONPATH='scripts;src'
python -m unittest tests.test_project_sync -v
python -m unittest discover -s tests -v
git diff --check
git status --short
```

Expected: sync tests and full project suite pass, diff check is clean, and status contains only intentionally ignored local artifacts.

- [ ] **Step 5: Commit**

```powershell
git add README.md tests/test_project_sync.py
git commit -m "docs: explain safe project synchronization"
```
