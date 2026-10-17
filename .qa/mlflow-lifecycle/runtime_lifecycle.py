"""HTTP driver; callers supply an authenticated requests.Session."""
import json
import time
from pathlib import Path


class Driver:
    def __init__(self, api, base_url, output):
        self.api, self.base_url, self.output = api, base_url.rstrip('/'), Path(output)
        self.output.mkdir(parents=True, exist_ok=True)

    def start(self, name, dataset, **inputs):
        with open(dataset, 'rb') as data:
            files = {'evaluation_data': ('evaluation.csv', data, 'text/csv')}
            for key, value in inputs.items():
                files[key] = (None, json.dumps(value) if isinstance(value, bool) else str(value))
            response = self.api.post(self.base_url + '/executions/company.mlops/mlflow-candidate-champion-evidence', files=files, timeout=30)
        response.raise_for_status()
        execution = response.json()
        print(name, execution['id'], flush=True)
        return self.wait(name, execution['id'])

    def wait(self, name, execution_id):
        for _ in range(360):
            response = self.api.get(self.base_url + '/executions/' + execution_id, timeout=10)
            response.raise_for_status()
            execution = response.json()
            if execution['state']['current'] in ('PAUSED', 'SUCCESS', 'FAILED', 'WARNING', 'KILLED', 'CANCELLED'):
                break
            time.sleep(.5)
        (self.output / (name + '-execution.json')).write_text(json.dumps(execution, indent=2))
        print(name, execution['state']['current'], flush=True)
        return execution

    def task_outputs(self, execution):
        response = self.api.get(self.base_url + '/outputs/tasks/' + execution['id'], timeout=10)
        response.raise_for_status()
        return response.json()

    def task(self, execution, task_id):
        task_run = next(t for t in execution['taskRunList'] if t['taskId'] == task_id)
        response = self.api.get(self.base_url + '/outputs/tasks/' + execution['id'] + '/' + task_run['id'], timeout=10)
        response.raise_for_status()
        return response.json()

    def outputs(self, execution):
        response = self.api.get(self.base_url + '/outputs/executions/' + execution['id'], timeout=10)
        response.raise_for_status()
        return response.json()

    def resume(self, name, execution, approved=False, report_sha256='', reason='Local test decision'):
        response = self.api.post(self.base_url + '/executions/' + execution['id'] + '/actions/resume',
            files={'approved': (None, json.dumps(approved)), 'report_sha256': (None, report_sha256),
                   'reason': (None, reason)}, timeout=30)
        response.raise_for_status()
        return self.wait(name, execution['id'])

    def download(self, execution, uri, filename):
        response = self.api.get(self.base_url + '/executions/' + execution['id'] + '/file',
                                params={'path': uri}, timeout=30)
        response.raise_for_status()
        target = self.output / filename
        target.write_bytes(response.content)
        return target
