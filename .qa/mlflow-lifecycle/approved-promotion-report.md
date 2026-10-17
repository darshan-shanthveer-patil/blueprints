# MLflow model evidence review

Verdict: **ELIGIBLE_FOR_REVIEW**

Human approval is still required. No alias or deployment was changed.

Model: cpu-lifecycle-demo
Candidate v2; champion v1

| Metric | Candidate | Champion |
| --- | --- | --- |
| accuracy | 0.9555555555555556 | 0.9333333333333333 |
| p95_latency_ms | 1.2092650999999999 | 1.2270257500000001 |
| peak_rss_mib | 274.890625 | 275.90234375 |

| Gate | Observed | Required | Result |
| --- | --- | --- | --- |
| Accuracy floor | 0.9555555555555556 | >= 0.9 | PASS |
| Accuracy regression | 0.9555555555555556 | >= 0.9233333333333333 | PASS |
| p95 latency ceiling | 1.2092650999999999 | <= 100.0 | PASS |
| Peak RSS ceiling | 274.890625 | <= 512.0 | PASS |
| p95 latency regression | 1.2092650999999999 | <= 1.3497283250000003 | PASS |
| Peak RSS regression | 274.890625 | <= 303.492578125 | PASS |

## Findings

- All six gates passed on comparable evidence.

## Evaluation provenance

- candidate run: 7507b636252143bab49b6a020aceffbe
  - dataset_digest: 145515be518a3f20694fbb5849b60a7cba084db9f7da8cba2a1ff77a74f9001f
  - hardware_id: 95d3314fb78c-x86_64-102421b1572e4ec83525d2a54ae2ceed454ff31ad67012a72a85d1fbbaa7e03d-affinity:0,1,2,3,4,5,6,7,8
  - batch_size: 16
  - benchmark_protocol: {"features": ["sepal length (cm)", "sepal width (cm)", "petal length (cm)", "petal width (cm)"], "iterations": 300, "method": "pyfunc-predict-v1", "mlflow": "3.17.1", "numpy": "2.5.4", "pandas": "3.0.6", "python": "3.12.14", "rss": "linux-process-peak-mib", "sklearn": "1.9.1", "target_column": "target", "threads": 1, "warmup": 50}
  - device: cpu
- champion run: 654466a98cd3463bbb4c36d338302099
  - dataset_digest: 145515be518a3f20694fbb5849b60a7cba084db9f7da8cba2a1ff77a74f9001f
  - hardware_id: 95d3314fb78c-x86_64-102421b1572e4ec83525d2a54ae2ceed454ff31ad67012a72a85d1fbbaa7e03d-affinity:0,1,2,3,4,5,6,7,8
  - batch_size: 16
  - benchmark_protocol: {"features": ["sepal length (cm)", "sepal width (cm)", "petal length (cm)", "petal width (cm)"], "iterations": 300, "method": "pyfunc-predict-v1", "mlflow": "3.17.1", "numpy": "2.5.4", "pandas": "3.0.6", "python": "3.12.14", "rss": "linux-process-peak-mib", "sklearn": "1.9.1", "target_column": "target", "threads": 1, "warmup": 50}
  - device: cpu

Checked at: 2026-10-17T23:30:13.821724+00:00

See report.json for machine-readable limits, gates and provenance.
