# NMPA Manual-Assisted Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the standard NMPA automatic collection path with a traceable human-assisted search and import workflow that never treats missing user action or missing uploads as `no_results`.

**Architecture:** Add a focused `nmpa_manual` module that generates the official search plan, records user-completed searches, validates evidence-backed import manifests, writes NMPA materials, and updates a nested manual-collection state. Standard `run-scenario` and delivery pipelines route NMPA to this manual plan while legacy HTTP/browser collectors remain available only as diagnostic code. Existing top-level scenario statuses remain compatible: `needs_manual_review` while open, `completed`/`completed_with_warnings` after valid imports, and `no_results` only when the nested phase is `verified_no_results`.

**Tech Stack:** Python 3.11, Pydantic, Typer, pytest, existing JSONL/material/evidence-card APIs.

## Global Constraints

- No automated CAPTCHA bypass, credential capture, cookie persistence, or session cloning.
- No user action and no imported evidence must never become `no_results` or `completed`.
- A zero-result closure requires a structured search record plus at least one screenshot or official export for every required attempt.
- Completion covers only the search categories and query levels confirmed in the generated plan.
- Imported evidence cards remain `needs_review=true` until the normal human review workflow closes them.
- Do not modify or delete the user-owned untracked `备份/` directory.

---

### Task 1: Manual Collection State And Search Plan

**Files:**
- Modify: `scripts/ivd_research/models.py`
- Create: `scripts/ivd_research/nmpa_manual.py`
- Test: `scripts/tests/test_nmpa_manual.py`

**Interfaces:**
- Produces: `ManualCollectionState`, `prepare_nmpa_manual_plan(task_dir: Path) -> dict[str, Any]`, and `collect(task_id, task_dir, params) -> ScenarioResult`.
- Consumes: `scenario_query_plans(state)`, `load_task`, `save_task`, `write_json`, and the NMPA source-site URL.

- [ ] **Step 1: Write failing plan-generation tests**

```python
def test_prepare_nmpa_manual_plan_sets_awaiting_user_search(tmp_path):
    task_dir = confirmed_task(tmp_path, competitor_scope="仅境内产品")
    result = prepare_nmpa_manual_plan(task_dir)
    state = load_task(task_dir)
    manual = state.scenario_statuses["nmpa_competitor"].manual_collection
    assert result["status"] == "needs_manual_review"
    assert manual.phase == "awaiting_user_search"
    assert manual.required_attempt_ids
    assert {row["registration_type"] for row in result["plan"]["attempts"]} == {
        "境内医疗器械（注册）"
    }


def test_standard_nmpa_collector_returns_manual_plan_without_materials(tmp_path):
    task_dir = confirmed_task(tmp_path)
    result = collect("TASK-1", task_dir, {"query": "CRP"})
    assert result.status == "needs_manual_review"
    assert result.materials == []
    assert "人工" in result.message_zh
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `python3 -m pytest -q scripts/tests/test_nmpa_manual.py -k "prepare or collector"`

Expected: import/attribute failures because the module and nested state do not exist.

- [ ] **Step 3: Implement state and plan generation**

```python
class ManualCollectionState(BaseModel):
    phase: str = "not_started"
    plan_path: str = ""
    search_record_path: str = ""
    manifest_path: str = ""
    required_attempt_ids: list[str] = Field(default_factory=list)
    recorded_attempt_ids: list[str] = Field(default_factory=list)
    validated_attempt_ids: list[str] = Field(default_factory=list)
    imported_material_ids: list[str] = Field(default_factory=list)
    observed_result_count: int = 0
    zero_results_verified: bool = False
    last_updated: str = ""


class ScenarioStatus(BaseModel):
    scenario_id: str
    label_zh: str
    status: str = "not_started"
    material_count: int = 0
    failure_count: int = 0
    last_message: str = ""
    manual_collection: ManualCollectionState | None = None
```

`prepare_nmpa_manual_plan` writes:

- `manual/nmpa/search_plan.json`
- `manual/nmpa/search_plan.md`
- `manual/nmpa/search_record_template.json`
- `manual/nmpa/import_manifest_template.json`

It updates the NMPA status to `needs_manual_review` and phase to `awaiting_user_search`. Domestic-only and import-only wording in `competitor_scope` narrows registration categories; otherwise both categories are included.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `python3 -m pytest -q scripts/tests/test_nmpa_manual.py -k "prepare or collector"`

- [ ] **Step 5: Commit task 1**

```bash
git add scripts/ivd_research/models.py scripts/ivd_research/nmpa_manual.py scripts/tests/test_nmpa_manual.py
git commit -m "feat: add NMPA manual search plans"
```

---

### Task 2: Search Recording And Evidence-Backed Import

**Files:**
- Modify: `scripts/ivd_research/nmpa_manual.py`
- Test: `scripts/tests/test_nmpa_manual.py`

**Interfaces:**
- Produces: `record_nmpa_manual_search(task_dir: Path, record_path: Path) -> dict[str, Any]` and `import_nmpa_manual(task_dir: Path, manifest_path: Path) -> dict[str, Any]`.
- Consumes: `record_materials`, `find_duplicate_material`, `next_material_id`, `generate_draft_evidence_cards`, `DownloadFile`, and `Material`.

- [ ] **Step 1: Write failing workflow tests**

```python
def test_record_search_sets_awaiting_import_without_closing_source(tmp_path):
    task_dir, plan = prepared_task(tmp_path)
    record = write_search_record(task_dir, plan, include_all=True, result_count=0)
    record_nmpa_manual_search(task_dir, record)
    scenario = load_task(task_dir).scenario_statuses["nmpa_competitor"]
    assert scenario.status == "needs_manual_review"
    assert scenario.manual_collection.phase == "awaiting_import"


def test_zero_results_require_visible_evidence(tmp_path):
    task_dir, plan = prepared_task(tmp_path)
    manifest = write_zero_result_manifest(task_dir, plan, evidence_files=[])
    with pytest.raises(ValueError, match="evidence"):
        import_nmpa_manual(task_dir, manifest)
    assert load_task(task_dir).scenario_statuses["nmpa_competitor"].status != "no_results"


def test_verified_zero_results_close_only_after_all_attempts(tmp_path):
    task_dir, plan = prepared_task(tmp_path)
    manifest = write_zero_result_manifest(task_dir, plan, evidence_files_per_attempt=True)
    result = import_nmpa_manual(task_dir, manifest)
    scenario = load_task(task_dir).scenario_statuses["nmpa_competitor"]
    assert result["status"] == "no_results"
    assert scenario.manual_collection.phase == "verified_no_results"
    assert scenario.manual_collection.zero_results_verified is True


def test_result_import_creates_traceable_material_and_card(tmp_path):
    task_dir, plan = prepared_task(tmp_path, one_attempt=True)
    manifest = write_result_manifest(task_dir, plan, registration_number="国械注准20261234567")
    result = import_nmpa_manual(task_dir, manifest)
    assert result["status"] == "completed"
    material = list(read_jsonl(task_dir / "data/materials.jsonl"))[0]
    assert material["source_scenario"] == "nmpa_competitor"
    assert material["adapter_id"] == "nmpa_manual_import"
    assert material["raw_fields"]["registration_certificate_number"] == "国械注准20261234567"
    assert list(read_jsonl(task_dir / "data/evidence_cards.jsonl"))[0]["needs_review"] is True
```

Also cover partial attempts, idempotent duplicate imports, mismatched task IDs, non-NMPA URLs, forbidden credential keys, missing files, result-count mismatch, and incomplete optional fields.

- [ ] **Step 2: Run tests and confirm RED**

Run: `python3 -m pytest -q scripts/tests/test_nmpa_manual.py -k "record or zero or import or credential or duplicate"`

- [ ] **Step 3: Implement validation and import**

The record schema requires exact plan attempt IDs, queries, registration types, search time, official NMPA URL, non-negative result count, and `operator_confirmed=true`. Recording never creates materials or closes the scenario.

The import manifest adds `capture_complete`, `evidence_files`, and `results`. Every evidence path is copied under `downloads/competitors/nmpa_manual/{search_session_id}/` with SHA256. Result records create `Material` rows with `source_scenario=nmpa_competitor`, `adapter_id=nmpa_manual_import`, official URLs, search trace, copied evidence paths, registration-number duplicate keys, and extracted structured text. The normal draft-card generator runs after material recording.

Top-level closure rules:

```python
if not all_required_attempts_validated:
    status, phase = "needs_manual_review", "awaiting_import"
elif all_attempts_have_zero_results:
    status, phase = "no_results", "verified_no_results"
elif optional_field_warnings:
    status, phase = "completed_with_warnings", "completed_with_warnings"
else:
    status, phase = "completed", "completed"
```

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `python3 -m pytest -q scripts/tests/test_nmpa_manual.py`

- [ ] **Step 5: Commit task 2**

```bash
git add scripts/ivd_research/nmpa_manual.py scripts/tests/test_nmpa_manual.py
git commit -m "feat: import verified NMPA manual evidence"
```

---

### Task 3: CLI And Standard Pipeline Routing

**Files:**
- Modify: `scripts/ivd_research/cli.py`
- Modify: `scripts/ivd_research/import_finding.py`
- Test: `scripts/tests/test_nmpa_manual.py`
- Test: `scripts/tests/test_material_recording.py`

**Interfaces:**
- Produces CLI commands `nmpa-manual-plan`, `record-nmpa-manual-search`, and `import-nmpa-manual`.
- Replaces the `SCENARIO_COLLECTORS["nmpa_competitor"]` production mapping with `nmpa_manual.collect` while retaining legacy automatic modules for explicit diagnostics only.

- [ ] **Step 1: Write failing CLI and generic-import guard tests**

```python
def test_run_scenario_nmpa_prepares_manual_plan_without_network(tmp_path):
    output_root = tmp_path / "runs"
    state = confirmed_task(output_root)
    result = runner.invoke(app, [
        "run-scenario", "--task-id", state.task_id,
        "--scenario", "nmpa_competitor",
        "--output-root", str(output_root), "--json",
    ])
    assert result.exit_code == 0
    assert "awaiting_user_search" in result.output
    assert list(read_jsonl(task_dir / "data/materials.jsonl")) == []


def test_generic_import_finding_cannot_close_nmpa_scenario(tmp_path):
    import_finding(
        task_dir,
        title="NMPA manual clue",
        source="nmpa_competitor",
        source_url="https://www.nmpa.gov.cn/datasearch/home-index.html#category=ylqx",
        content="国械注准20261234567 CRP 检测试剂盒",
        material_type="competitor",
    )
    scenario = load_task(task_dir).scenario_statuses["nmpa_competitor"]
    assert scenario.status == "needs_manual_review"
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `python3 -m pytest -q scripts/tests/test_nmpa_manual.py scripts/tests/test_material_recording.py -k "nmpa"`

- [ ] **Step 3: Add commands and route the collector**

```python
@app.command("nmpa-manual-plan")
def nmpa_manual_plan_command(
    task_id: str = typer.Option(..., "--task-id"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    task_dir = find_task(output_root or default_output_root(), task_id)
    emit(prepare_nmpa_manual_plan(task_dir), json_output)

@app.command("record-nmpa-manual-search")
def record_nmpa_manual_search_command(
    task_id: str = typer.Option(..., "--task-id"),
    record: Path = typer.Option(..., "--record"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    task_dir = find_task(output_root or default_output_root(), task_id)
    emit(record_nmpa_manual_search(task_dir, record), json_output)

@app.command("import-nmpa-manual")
def import_nmpa_manual_command(
    task_id: str = typer.Option(..., "--task-id"),
    manifest: Path = typer.Option(..., "--manifest"),
    output_root: Optional[Path] = typer.Option(None, "--output-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    task_dir = find_task(output_root or default_output_root(), task_id)
    emit(import_nmpa_manual(task_dir, manifest), json_output)
```

Generic `import-finding` may store an NMPA material as a clue, but it must leave the source status as `needs_manual_review` and state that the dedicated manifest workflow is still required.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `python3 -m pytest -q scripts/tests/test_nmpa_manual.py scripts/tests/test_material_recording.py -k "nmpa"`

- [ ] **Step 5: Commit task 3**

```bash
git add scripts/ivd_research/cli.py scripts/ivd_research/import_finding.py scripts/tests/test_nmpa_manual.py scripts/tests/test_material_recording.py
git commit -m "feat: route NMPA collection through manual import"
```

---

### Task 4: Business Gates And User Documentation

**Files:**
- Modify: `scripts/ivd_research/package.py`
- Modify: `SKILL.md`
- Modify: `README.md`
- Modify: `references/actions.md`
- Modify: `references/workflow.md`
- Modify: `references/cli-contract.md`
- Modify: `references/scenarios.md`
- Test: `scripts/tests/test_package_verification.py`

**Interfaces:**
- Consumes nested `manual_collection` state.
- Produces coverage warnings that reject unverified NMPA `no_results` and generic/manual clues that did not complete the dedicated manifest workflow.

- [ ] **Step 1: Write failing gate tests**

```python
def test_unverified_nmpa_no_results_blocks_scenario_coverage(tmp_path):
    set_nmpa_status(tmp_path, status="no_results", manual_phase="awaiting_import")
    warnings = scenario_coverage_warnings(tmp_path)
    assert any("未经人工证据核验" in item for item in warnings)


def test_verified_nmpa_no_results_satisfies_source_coverage(tmp_path):
    set_nmpa_status(tmp_path, status="no_results", manual_phase="verified_no_results")
    assert not any("nmpa_competitor" in item for item in scenario_coverage_warnings(tmp_path))
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `python3 -m pytest -q scripts/tests/test_package_verification.py -k "nmpa"`

- [ ] **Step 3: Implement the gate and update documentation**

Document the user experience in plain Chinese: the agent prepares the plan, tells the user early when manual browser work or login is needed, waits for saved files, performs CLI import itself, and never asks an R&D user to type commands. Remove standard-path claims that NMPA defaults to HTTP/Edge/Playwright automation; retain those modules as diagnostic tooling only.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `python3 -m pytest -q scripts/tests/test_package_verification.py -k "nmpa"`

- [ ] **Step 5: Commit task 4**

```bash
git add scripts/ivd_research/package.py SKILL.md README.md references scripts/tests/test_package_verification.py
git commit -m "docs: define NMPA manual evidence gate"
```

---

### Task 5: Full Verification And Audit Handoff

**Files:**
- Modify only files required by failing verification.

**Interfaces:**
- Produces a tested feature branch ready for external audit; does not merge, tag, or release without user approval.

- [ ] **Step 1: Run the complete deterministic suite**

```bash
python3 -m pytest -q
python3 -m ruff check scripts
python3 -m compileall -q scripts
git diff --check
nuoyan doctor --output-root /private/tmp/nuoyan-nmpa-manual-doctor --json
```

Expected: all tests pass, Ruff and compileall exit 0, diff check is clean, and doctor reports `ok=true` with only optional capability warnings allowed.

- [ ] **Step 2: Run a local CLI acceptance flow**

Create a temporary confirmed task, run `nmpa-manual-plan`, record a partial search, verify the scenario remains open, then import an evidence-backed zero-result or positive manifest and verify materials/cards/status. Do not access NMPA automatically.

- [ ] **Step 3: Review the final diff against the three approved rules**

Confirm:

1. No user action is not no-results.
2. Zero results require a structured record plus visible evidence.
3. Completion requires every attempt in the confirmed plan.

- [ ] **Step 4: Commit any final documentation/test corrections**

```bash
git add SKILL.md README.md references scripts/ivd_research scripts/tests docs/superpowers/plans/2026-07-23-nmpa-manual-import.md
git commit -m "test: verify NMPA manual import closure"
```

- [ ] **Step 5: Hand off for review**

Report branch name, commits, exact tests, current limitations, and the local acceptance task. Keep `main` and `v2.1.11` unchanged.
