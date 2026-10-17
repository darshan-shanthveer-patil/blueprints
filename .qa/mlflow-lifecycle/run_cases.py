"""Integration tests use API decisions only against a freshly seeded disposable registry."""
import json
import hashlib
from pathlib import Path


def run_suite(driver, data, client):
    model = 'cpu-lifecycle-demo'
    records = []

    def champion():
        return str(client.get_model_version_by_alias(model,'champion').version)


    def record(name, execution, expected, verdict=None):
        assert execution['state']['current'] == expected, (name, execution['state'])
        output = driver.outputs(execution)
        if verdict is not None:
            assert output['verdict'] == verdict, (name, output)
        item = {'case':name, 'id':execution['id'], 'state':expected,
                'verdict':output.get('verdict'), 'champion_after':champion()}
        if output.get('report'):
            path=driver.download(execution, output['report'],name+'-report.json')
            driver.download(execution, output['review_report'],name+'-report.md')
            item['measurements']=json.loads(path.read_text())['measurements']
        if output.get('promotion_receipt'):
            driver.download(execution,output['promotion_receipt'],name+'-promotion.json')
        records.append(item)
        (driver.output/'cases.json').write_text(json.dumps(records,indent=2))


    def pause(name, **inputs):
        execution=driver.start(name,data,enable_promotion=True,exclusive_registry_access=True,**inputs)
        assert execution['state']['current']=='PAUSED', (name,execution['state'])
        output=driver.task(execution,'assess')
        path=driver.download(execution,output['outputFiles']['report.json'],name+'-paused-report.json')
        return execution,output['vars']['report_sha256'],json.loads(path.read_text())

    dry = driver.start('dry-run', data)
    record('dry-run',dry,'SUCCESS','ELIGIBLE_FOR_REVIEW')
    paused = driver.start('reject-approval',data,enable_promotion=True,exclusive_registry_access=True)
    assert champion()=='1'
    # Existing paused execution proves a second execution cannot enter evaluation.
    concurrent=driver.start('concurrency-rejected',data)
    record('concurrency-rejected',concurrent,'FAILED')
    assert not concurrent.get('taskRunList')
    rejected=driver.resume('reject-approval',paused,approved=False)
    record('reject-approval',rejected,'SUCCESS','APPROVAL_REJECTED')
    assert champion()=='1'
    for name,inputs in [('quality-blocked',{'min_accuracy':.99}),
                        ('latency-blocked',{'max_p95_latency_ms':.000001}),
                        ('memory-blocked',{'max_peak_rss_mib':1})]:
        execution=driver.start(name,data,enable_promotion=True,exclusive_registry_access=True,**inputs)
        record(name,execution,'SUCCESS','BLOCKED')
        assert champion()=='1'
        assert not any(t['taskId']=='approval' for t in execution['taskRunList'])
    wrong,sha,report=pause('wrong-report')
    wrong=driver.resume('wrong-report',wrong,approved=True,report_sha256='0'*64)
    record('wrong-report',wrong,'FAILED')
    assert champion()=='1'
    stale,sha,report=pause('stale-evidence')
    client.log_metric(report['pinned']['candidate']['run_id'],'accuracy',.1)
    stale=driver.resume('stale-evidence',stale,approved=True,report_sha256=sha)
    record('stale-evidence',stale,'FAILED')
    assert champion()=='1'
    moved,sha,report=pause('stale-alias')
    client.set_registered_model_alias(model,'candidate','1')
    try:
        moved=driver.resume('stale-alias',moved,approved=True,report_sha256=sha)
        record('stale-alias',moved,'FAILED')
        assert champion()=='1'
    finally:
        client.set_registered_model_alias(model,'candidate','2')
    approved,sha,report=pause('approved-promotion')
    approved=driver.resume('approved-promotion',approved,approved=True,report_sha256=sha,reason='Public-data integration-test approval')
    record('approved-promotion',approved,'SUCCESS','PROMOTED')
    assert champion()=='2'
    print('VERIFIED PROMOTION: champion alias moved from 1 to 2',flush=True)
    # Fixture cleanup; this is not a rollback performed by the blueprint.
    client.set_registered_model_alias(model,'champion','1')
    print('Disposable fixture restored to champion=1',flush=True)

    return records
