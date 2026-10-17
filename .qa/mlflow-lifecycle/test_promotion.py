import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import mlflow
import importlib
PYFUNC = importlib.import_module('mlflow.pyfunc')
ARTIFACTS = importlib.import_module('mlflow.artifacts')
import numpy as np
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
FLOW = yaml.safe_load((ROOT/'flows/mlflow-candidate-champion-evidence.yaml').read_text())

def find(tasks, task_id):
    for task in tasks:
        if task['id'] == task_id:
            return task
        for key in ('tasks', 'then', 'else'):
            if isinstance(task.get(key), list):
                try:
                    return find(task[key], task_id)
                except KeyError:
                    pass
    raise KeyError(task_id)

PROMOTE = find(FLOW['tasks'], 'promote')['script']
spec = importlib.util.spec_from_loader('fixture_integrity', loader=None)
INTEGRITY = importlib.util.module_from_spec(spec)
exec(FLOW['variables']['integrity_script'], INTEGRITY.__dict__)


@pytest.fixture
def release(tmp_path, monkeypatch):
    import sys
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('KESTRA_EXECUTION_ID', 'test-execution')
    monkeypatch.setitem(sys.modules, 'integrity', INTEGRITY)
    (tmp_path/'evaluation.csv').write_text('x,target\n1,0\n2,1\n')
    directories = {}
    versions, runs, pins = {}, {}, {}
    for role, version in [('champion', '1'), ('candidate', '2')]:
        directory = tmp_path/role
        directory.mkdir()
        (directory/'MLmodel').write_text('fixture artifact ' + role)
        directories[role] = directory
        versions[version] = SimpleNamespace(version=version, source='fixture://' + role,
            run_id='training-' + role, status='READY')
        run = SimpleNamespace(info=SimpleNamespace(run_id='evaluation-' + role,
            status='FINISHED', lifecycle_stage='active'), data=SimpleNamespace(metrics={'accuracy': .95}, tags={'role': role}))
        runs[run.info.run_id] = run
        pins[role] = dict(INTEGRITY.version_identity(versions[version]), run_id=run.info.run_id,
            model_sha256=INTEGRITY.artifact_digest(directory),
            evidence_sha256=INTEGRITY.evidence_digest(run),
            predictions_sha256=INTEGRITY.prediction_digest(np.array([0,1])))
    pins['model_name'] = 'fixture'
    report = {'schema_version':3,'eligible':True,'comparable':True,'pinned':pins,
        'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'benchmark_contracts':{'candidate':{'dataset_digest':hashlib.sha256((tmp_path/'evaluation.csv').read_bytes()).hexdigest()}}}
    config = {'enable_promotion':True,'exclusive_registry_access':True,
        'candidate_alias':'candidate','champion_alias':'champion','max_review_age_minutes':60,'target_column':'target'}
    approval = {'approved':True,'reason':'Fixture approval'}

    class Registry:
        aliases = {'candidate':'2', 'champion':'1'}
        writes = []
        def get_model_version_by_alias(self, name, alias):
            return versions[self.aliases[alias]]
        def get_run(self, run_id):
            return runs[run_id]
        def set_registered_model_alias(self, name, alias, version):
            self.writes.append((name, alias, version))
            self.aliases[alias] = version
    client = Registry()
    monkeypatch.setattr(mlflow, 'MlflowClient', lambda:client)
    def download(artifact_uri, dst_path):
        if '@' in artifact_uri:
            version = client.aliases[artifact_uri.split('@')[-1]]
        else:
            version = artifact_uri.rsplit('/',1)[-1]
        return str(directories['candidate' if version=='2' else 'champion'])
    monkeypatch.setattr(ARTIFACTS, 'download_artifacts', download)
    monkeypatch.setattr(PYFUNC, 'load_model', lambda path:SimpleNamespace(predict=lambda data:np.array([0,1])))
    def execute():
        report_bytes = json.dumps(report).encode()
        (tmp_path/'report.json').write_bytes(report_bytes)
        approval.setdefault('report_sha256', hashlib.sha256(report_bytes).hexdigest())
        (tmp_path/'approval.json').write_text(json.dumps(approval))
        (tmp_path/'config.json').write_text(json.dumps(config))
        exec(compile(PROMOTE, 'submitted-promote.py', 'exec'), {})
    return SimpleNamespace(execute=execute, report=report, config=config, approval=approval,
        client=client, versions=versions, runs=runs, directories=directories, path=tmp_path)


def test_approved_release_promotes_once_and_verifies(release):
    release.execute()
    assert release.client.writes == [('fixture','champion','2')]
    assert release.client.aliases['champion'] == '2'
    receipt = json.loads((release.path/'promotion.json').read_text())
    assert receipt['promoted'] and receipt['verdict'] == 'PROMOTED'
    assert receipt['previous_version'] == '1'


@pytest.mark.parametrize('case', ['rejected','wrong_report','no_reason','blocked','not_comparable',
    'not_opted_in','no_exclusivity','expired','future_report','dataset_changed',
    'candidate_moved','champion_moved','version_source_changed','evaluation_changed','artifact_changed'])
def test_invalid_or_stale_approval_never_writes(release, case):
    if case=='rejected': release.approval['approved']=False
    elif case=='wrong_report': release.approval['report_sha256']='0'*64
    elif case=='no_reason': release.approval['reason']=' '
    elif case=='blocked': release.report['eligible']=False
    elif case=='not_comparable': release.report['comparable']=False
    elif case=='not_opted_in': release.config['enable_promotion']=False
    elif case=='no_exclusivity': release.config['exclusive_registry_access']=False
    elif case=='expired': release.report['checked_at']=(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(hours=2)).isoformat()
    elif case=='future_report': release.report['checked_at']=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(hours=2)).isoformat()
    elif case=='dataset_changed': (release.path/'evaluation.csv').write_text('x,target\n8,0\n9,1\n')
    elif case=='candidate_moved': release.client.aliases['candidate']='1'
    elif case=='champion_moved': release.client.aliases['champion']='2'
    elif case=='version_source_changed': release.versions['2'].source='fixture://changed'
    elif case=='evaluation_changed': release.runs['evaluation-candidate'].data.metrics['accuracy']=.1
    elif case=='artifact_changed': (release.directories['candidate']/'MLmodel').write_text('changed model')
    with pytest.raises(ValueError): release.execute()
    assert release.client.writes == []
    assert not (release.path/'promotion.json').exists()


def test_lost_write_response_is_recorded_without_retry_or_rollback(release, monkeypatch):
    def lost_response(name, alias, version):
        release.client.writes.append((name,alias,version))
        release.client.aliases[alias]=version
        raise ConnectionError('Response lost after write')
    monkeypatch.setattr(release.client, 'set_registered_model_alias', lost_response)
    with pytest.raises(ConnectionError): release.execute()
    assert len(release.client.writes)==1
    assert release.client.aliases['champion']=='2'
    receipt=json.loads((release.path/'promotion.json').read_text())
    assert not receipt['promoted'] and receipt['verdict']=='PROMOTION_UNVERIFIED'


def test_post_promotion_inference_failure_is_not_reported_as_success(release, monkeypatch):
    calls = []
    def load(path):
        calls.append(path)
        return SimpleNamespace(predict=lambda data:np.array([0,1] if len(calls)==1 else [1,1]))
    monkeypatch.setattr(PYFUNC,'load_model',load)
    with pytest.raises(ValueError): release.execute()
    assert len(release.client.writes)==1 and release.client.aliases['champion']=='2'
    receipt=json.loads((release.path/'promotion.json').read_text())
    assert receipt['verdict']=='PROMOTION_UNVERIFIED' and not receipt['promoted']


def test_artifact_digest_detects_content_and_name_changes(tmp_path):
    (tmp_path/'one').write_bytes(b'same')
    first=INTEGRITY.artifact_digest(tmp_path)
    (tmp_path/'one').rename(tmp_path/'two')
    assert INTEGRITY.artifact_digest(tmp_path)!=first
    second=INTEGRITY.artifact_digest(tmp_path)
    (tmp_path/'two').write_bytes(b'different')
    assert INTEGRITY.artifact_digest(tmp_path)!=second


def test_artifact_digest_rejects_symlinks(tmp_path):
    (tmp_path/'real').write_bytes(b'content')
    (tmp_path/'link').symlink_to(tmp_path/'real')
    with pytest.raises(ValueError): INTEGRITY.artifact_digest(tmp_path)


def test_changed_candidate_predictions_are_rejected_before_write(release, monkeypatch):
    monkeypatch.setattr(PYFUNC,'load_model',lambda path:SimpleNamespace(predict=lambda data:np.array([1,1])))
    with pytest.raises(ValueError): release.execute()
    assert release.client.writes == [] and release.client.aliases['champion']=='1'
