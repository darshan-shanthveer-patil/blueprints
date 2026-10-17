import copy
import json
import math
import pathlib
import subprocess
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
FLOW = yaml.safe_load((ROOT / 'flows/mlflow-candidate-champion-evidence.yaml').read_text())
TASKS = {task['id']: task for task in FLOW['tasks']}
SNAPSHOT = json.loads((pathlib.Path(__file__).resolve().parent / 'registry-snapshot.json').read_text())
DEFAULTS = {item['id']: item['defaults'] for item in FLOW['inputs'] if 'defaults' in item}
DEFAULTS['evaluation_data'] = 'fixture.csv'


def run_script(task, files, tmp_path):
    for name, value in files.items():
        (tmp_path / name).write_text(json.dumps(value))
    import os
    return subprocess.run([sys.executable, '-c', TASKS[task]['script']], cwd=tmp_path, env=dict(os.environ, KESTRA_EXECUTION_ID='unit-test'),
                          text=True, capture_output=True)


def pin(tmp_path):
    result = run_script('pin_versions', {
        'candidate.json': SNAPSHOT['candidate_version'],
        'champion.json': SNAPSHOT['champion_version'], 'config.json': DEFAULTS,
    }, tmp_path)
    assert result.returncode == 0, result.stderr
    pins = json.loads(result.stdout.strip()[2:-2])['outputs']
    for role in ('candidate', 'champion'):
        pins[role]['run_id'] = SNAPSHOT[role]['run']['info']['run_id']
    return pins


def files(tmp_path):
    docs = {
        'pins.json': pin(tmp_path), 'config.json': copy.deepcopy(DEFAULTS),
        'candidate.json': copy.deepcopy(SNAPSHOT['candidate']),
        'champion.json': copy.deepcopy(SNAPSHOT['champion']),
    }
    # Deterministic branch fixtures; real benchmark values are tested separately.
    for role, values in [('champion', (0.95, 20.0, 100.0)), ('candidate', (0.96, 21.0, 105.0))]:
        docs[role + '.json']['run']['data']['metrics'] = [
            {'key': key, 'value': value} for key, value in
            zip(('accuracy', 'p95_latency_ms', 'peak_rss_mib'), values)
        ]
    return docs


def assess(docs, tmp_path):
    result = run_script('assess', docs, tmp_path)
    assert result.returncode == 0, result.stderr
    report_text = (tmp_path / 'report.json').read_text()
    assert 'NaN' not in report_text and 'Infinity' not in report_text
    return json.loads(report_text)


def metric(docs, key, value, role='candidate'):
    entry = next(x for x in docs[role + '.json']['run']['data']['metrics'] if x['key'] == key)
    entry['value'] = value


def tag(docs, key, value, role='candidate'):
    entry = next(x for x in docs[role + '.json']['run']['data']['tags'] if x['key'] == 'evaluation.' + key)
    entry['value'] = value


def test_accepts_comparable_evidence(tmp_path):
    report = assess(files(tmp_path), tmp_path)
    assert report['eligible'] and report['approval_required']
    assert report['verdict'] == 'ELIGIBLE_FOR_REVIEW'
    assert report['reasons'] == []


@pytest.mark.parametrize('key,value,reason', [
    ('accuracy', 0.8, 'absolute floor'),
    ('accuracy', 0.93, 'accuracy regressed'),
    ('p95_latency_ms', 101.0, 'absolute limit'),
    ('p95_latency_ms', 23.0, 'latency regressed'),
    ('peak_rss_mib', 513.0, 'absolute limit'),
    ('peak_rss_mib', 111.0, 'RSS regressed'),
])
def test_metric_regressions(tmp_path, key, value, reason):
    docs = files(tmp_path)
    metric(docs, key, value)
    report = assess(docs, tmp_path)
    assert not report['eligible']
    assert any(reason in r for r in report['reasons'])


@pytest.mark.parametrize('value', [None, math.nan, math.inf, -math.inf, '20', True, 0, -1])
@pytest.mark.parametrize('role', ['candidate', 'champion'])
def test_invalid_resource_metric(tmp_path, value, role):
    docs = files(tmp_path)
    metric(docs, 'p95_latency_ms', value, role)
    report = assess(docs, tmp_path)
    assert not report['eligible']
    assert report['measurements'][role]['p95_latency_ms'] is None


@pytest.mark.parametrize('key', ['dataset_digest', 'hardware_id', 'batch_size', 'benchmark_protocol', 'device'])
def test_contract_mismatch(tmp_path, key):
    docs = files(tmp_path)
    tag(docs, key, 'different')
    report = assess(docs, tmp_path)
    assert not report['eligible']
    assert 'Benchmark mismatch: ' + key in report['reasons']


def test_missing_contract(tmp_path):
    docs = files(tmp_path)
    tag(docs, 'hardware_id', '')
    assert not assess(docs, tmp_path)['eligible']


@pytest.mark.parametrize('key,value', [('model_version', '999'), ('model_name', 'other')])
def test_unrelated_evaluation(tmp_path, key, value):
    docs = files(tmp_path)
    tag(docs, key, value)
    report = assess(docs, tmp_path)
    assert not report['eligible']
    assert any('not linked' in r for r in report['reasons'])


def test_incomplete_evaluation(tmp_path):
    docs = files(tmp_path)
    docs['candidate.json']['run']['info']['status'] = 'RUNNING'
    assert not assess(docs, tmp_path)['eligible']


def test_wrong_run_identity(tmp_path):
    docs = files(tmp_path)
    docs['candidate.json']['run']['info']['run_id'] = '0' * 32
    assert not assess(docs, tmp_path)['eligible']


def test_deleted_evaluation(tmp_path):
    docs = files(tmp_path)
    docs['candidate.json']['run']['info']['lifecycle_stage'] = 'deleted'
    assert not assess(docs, tmp_path)['eligible']


@pytest.mark.parametrize('key,value', [
    ('min_accuracy', math.nan), ('min_accuracy', 1.1), ('max_accuracy_drop', -1),
    ('max_resource_increase', math.inf), ('max_p95_latency_ms', 0), ('max_peak_rss_mib', True),
])
def test_invalid_threshold_fails(tmp_path, key, value):
    docs = files(tmp_path)
    docs['config.json'][key] = value
    assert run_script('assess', docs, tmp_path).returncode != 0


@pytest.mark.parametrize('case', ['same_version', 'missing_training_run', 'missing_source', 'not_ready', 'wrong_model'])
def test_invalid_alias_pinning(tmp_path, case):
    candidate = copy.deepcopy(SNAPSHOT['candidate_version'])
    champion = copy.deepcopy(SNAPSHOT['champion_version'])
    c, h = candidate['model_version'], champion['model_version']
    if case == 'same_version':
        c['version'] = h['version']
    elif case == 'missing_training_run':
        c['run_id'] = ''
    elif case == 'missing_source':
        c['source'] = ''
    elif case == 'not_ready':
        c['status'] = 'PENDING_REGISTRATION'
    else:
        c['name'] = 'unrelated-model'
    assert run_script('pin_versions', {'candidate.json': candidate, 'champion.json': champion,
                                      'config.json': DEFAULTS}, tmp_path).returncode != 0


def test_catalog_requirements():
    assert FLOW['id'] == 'mlflow-candidate-champion-evidence'
    e = FLOW['extend']
    assert len(e['metaDescription']) <= 160
    assert e['ee'] is False and e['demo'] is False and e['tags'] == ['AI']
    assert len(e) == 9
    for heading in ('How it works', 'What you get', "Who it's for", 'Why orchestrate this with Kestra',
                    'Prerequisites', 'Quick start', 'How to extend', 'Links'):
        assert '## ' + heading in e['description']
    assert FLOW['triggers'][0]['disabled'] is True
    assert 'http.Request' in yaml.safe_dump(FLOW)


def test_readable_report_and_gate_results(tmp_path):
    report = assess(files(tmp_path), tmp_path)
    assert report['schema_version'] == 3 and report['comparable']
    assert len(report['gates']) == 6
    assert all(gate['passed'] is True for gate in report['gates'])
    text = (tmp_path / 'report.md').read_text()
    assert text.count('| PASS |') == 6
    assert 'Human approval is still required' in text
    assert report['pinned']['candidate']['run_id'] in text
    assert 'p95_latency_ms | 21.0 | 20.0' in text


def test_report_explains_failed_bound(tmp_path):
    docs = files(tmp_path)
    metric(docs, 'p95_latency_ms', 23.0)
    report = assess(docs, tmp_path)
    gate = next(g for g in report['gates'] if g['name'] == 'p95 latency regression')
    assert gate == {'name': 'p95 latency regression', 'metric': 'p95_latency_ms',
                    'operator': '<=', 'observed': 23.0, 'bound': 22.0, 'passed': False}
    assert '| p95 latency regression | 23.0 | <= 22.0 | FAIL |' in (tmp_path / 'report.md').read_text()


def test_incomparable_gates_are_not_evaluated(tmp_path):
    docs = files(tmp_path)
    tag(docs, 'hardware_id', 'different-cpu')
    report = assess(docs, tmp_path)
    assert not report['comparable'] and not report['eligible']
    assert all(gate['passed'] is None for gate in report['gates'])
    text = (tmp_path / 'report.md').read_text()
    assert text.count('| NOT_EVALUATED |') == 6
    assert '| PASS |' not in text
    assert 'Benchmark mismatch: hardware_id' in text


def test_readable_report_escapes_contract_content(tmp_path):
    docs = files(tmp_path)
    for role in ('candidate', 'champion'):
        tag(docs, 'hardware_id', '<script>|`cpu`\nInjected row', role)
    report = assess(docs, tmp_path)
    assert report['eligible']
    text = (tmp_path / 'report.md').read_text()
    assert '<script>' not in text and '\nInjected row' not in text
    assert '&lt;script&gt;\\|\\`cpu\\` Injected row' in text


def test_execution_bounds_and_report_output():
    for task in FLOW['tasks']:
        if task['type'].endswith('.Parallel'):
            assert all(child['timeout'] == 'PT30S' for child in task['tasks'])
        if task['type'].endswith('python.Script'):
            assert task['timeout'] == ('PT10M' if task['id'] == 'evaluate_models' else 'PT1M')
    assert TASKS['assess']['outputFiles'] == ['report.json', 'report.md']
    assert any(o['id'] == 'review_report' and o['type'] == 'FILE' for o in FLOW['outputs'])
