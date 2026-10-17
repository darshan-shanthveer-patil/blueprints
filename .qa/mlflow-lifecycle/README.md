# MLflow CPU release lifecycle: reproducible demo and tests

Validation support for [Kestra blueprint PR #3229](https://github.com/kestra-io/blueprints/pull/3229). This QA branch contains the submitted flow plus its demo and tests; the upstream PR changes only the flow under `flows/`.

The flow itself performs fresh evaluations and CPU benchmarks, logs MLflow evidence, compares six gates, pauses for report-bound approval, rechecks mutable state, and verifies an opted-in champion alias promotion. It does not deploy serving processes.

## Reproduce

Requirements: Linux, Java 25+, Python 3.12, `uv` (or an equivalent virtual environment), free loopback ports 5000/5001/8080, network access to official releases/Python packages, and roughly 3 GiB available RAM. Use an idle CPU worker; the demo timing is illustrative and thresholds may need tuning on another host.

From this repository checkout:

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r .qa/mlflow-lifecycle/requirements.txt
.venv/bin/python .qa/mlflow-lifecycle/demo.py --test
```

If Java 25 is not the default, append `--java /absolute/path/to/java`. The launcher downloads official Kestra 2.0.6, Python/script 1.17.0 and Slack 2.1.1 artifacts, verifies their published SHA256 checksums, starts isolated loopback services, seeds public-data models, imports the submitted YAML after removing catalog `extend`, and runs ten cases. It stops services afterward and retains evidence in the temporary directory printed at completion. No company services, data, or credentials are used.

The fresh registry contains:

- Champion v1: StandardScaler, degree-3 PolynomialFeatures, LogisticRegression(C=0.1).
- Candidate v2: LogisticRegression(C=1.0).
- Public Iris data, stratified 70/30 train/holdout split with random_state=73. The held-out CSV is uploaded to Kestra.
- Sequential fresh benchmark processes, one numerical thread, batch=16, 50 warm-up iterations, 300 measured iterations. p95 is measured batch prediction latency; peak RSS is Linux process high-water RSS including imports/model loading.

## Manual approval walkthrough

Run the launcher without `--test`:

```sh
.venv/bin/python .qa/mlflow-lifecycle/demo.py
```

It creates a new disposable registry and starts a promotion-enabled execution. Open the local Kestra execution URL it prints; local-only login credentials are in the generated application.yaml (mode 0600). Review `assess/report.md`, the pinned versions, metrics, gates and report SHA256. Click **Resume**, choose `approved=true`, copy the exact `report_sha256`, and provide a reason. Inspect `promote/promotion.json` and the champion alias in MLflow. Choosing false rejects the release. Ctrl-C stops the services; demo state remains in its temporary directory.

The automated suite submits API approval/rejection decisions solely for testing this newly seeded registry. Those are simulated reviewer decisions, not claims of a real human release review. The manual mode supports an actual UI review.

## Regression tests

```sh
.venv/bin/python -m pytest .qa/mlflow-lifecycle/test_blueprint.py .qa/mlflow-lifecycle/test_promotion.py -q
```

72 cases passed. Tests execute the submitted Python scripts. The comparator fixtures start from captured real MLflow responses and deliberately change metrics/contracts for deterministic branches. Promotion unit tests use fault-injected clients to assert that rejected/stale inputs never write, and lost responses or failed verification are not reported as verified promotions. These unit fault injections are separate from the ten real Kestra/MLflow integration cases.

## Captured integration evidence

See [evidence/cases.json](cases.json), [the evaluation report](dry-run-report.md), and [the verified promotion receipt](approved-promotion-promotion.json). The full clean launcher run was executed, not just syntax-checked.

| Case | Expected outcome |
| --- | --- |
| Dry run | SUCCESS, ELIGIBLE_FOR_REVIEW, champion unchanged |
| Overlapping execution while paused | FAILED before evaluation |
| Approval rejected | SUCCESS, APPROVAL_REJECTED, champion unchanged |
| Accuracy floor 0.99 | SUCCESS, BLOCKED; no approval task |
| Latency ceiling 0.000001 ms | SUCCESS, BLOCKED; no approval task |
| RSS ceiling 1 MiB | SUCCESS, BLOCKED; no approval task |
| Wrong report SHA256 | FAILED before alias write |
| Evaluation metric changed during pause | FAILED before alias write |
| Candidate alias moved during pause | FAILED before champion write |
| Approved promotion | SUCCESS, PROMOTED; champion 1 -> 2, artifact and predictions verified |

The test harness restores champion=1 after checking the successful promotion. This fixture cleanup is not automatic rollback by the blueprint. Extra development runs verified the specific negative-test exception causes; final clean-run execution IDs and measurements are in cases.json.

## Boundaries

Only trusted scikit-learn pyfunc classifiers with deterministic one-label-per-row predictions are supported. This does not evaluate YOLO/detection or LLM judges. CSV/model size and task-duration bounds are explicit in the flow. Model code executes on the trusted Process worker.

Promotion is disabled by default. Enabling it requires exclusive registry-writer access for the entire execution, including approval. Kestra serializes this flow, but MLflow alias updates do not provide a compare-and-swap predicate and other clients are not locked. Revalidation detects changed aliases, artifacts and evaluation evidence; it cannot replace external writer exclusion. Restrict resume permissions to release approvers.

There is no automatic write retry or rollback. A lost response or failed post-write verification can leave the alias changed; inspect the receipt and live alias before recovery. Worker loss may prevent receipt collection. Check execution state and receipt, not `eligible` alone. Registry promotion does not reload or deploy model-serving processes.

The real Slack plugin delivered notifications to a local HTTP receiver, not a live Slack workspace. Production bearer authentication, TLS, workload isolation, external locking and production datasets were not validated by this local demo.
