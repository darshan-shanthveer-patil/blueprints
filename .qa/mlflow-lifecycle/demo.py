"""Start an isolated local MLflow/Kestra demo; requires Linux and Java 25.

All services bind to loopback. --test simulates approval API decisions only for
this newly seeded disposable registry; it never targets a company registry.
"""
import argparse
import base64
import hashlib
import http.server
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

import requests
import yaml
from runtime_lifecycle import Driver

WORK = Path(__file__).resolve().parent
REPO = WORK.parents[1]
FLOW = REPO/'flows/mlflow-candidate-champion-evidence.yaml'


def download(cache, repo, version, filename):
    base='https://github.com/kestra-io/' + repo + '/releases/download/v' + version + '/'
    checksums=urllib.request.urlopen(base+'checksums_sha256.txt',timeout=60).read().decode()
    expected=next(line.split()[0] for line in checksums.splitlines()
                  if len(line.split())>=2 and Path(line.split()[-1].lstrip('*')).name==filename)
    target=cache/('plugins' if filename.endswith('.jar') else '')/filename
    target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists() or hashlib.file_digest(target.open('rb'),'sha256').hexdigest()!=expected:
        temporary=target.with_suffix(target.suffix+'.part')
        with urllib.request.urlopen(base+filename,timeout=120) as response, temporary.open('wb') as output:
            shutil.copyfileobj(response,output)
        with temporary.open('rb') as handle:
            if hashlib.file_digest(handle,'sha256').hexdigest()!=expected:
                raise RuntimeError('Release checksum mismatch: '+filename)
        temporary.replace(target)
    return target


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--java',default=shutil.which('java'))
    parser.add_argument('--cache-dir',type=Path,default=WORK/'.runtime-cache')
    parser.add_argument('--test',action='store_true',help='Run test API decisions against the disposable local registry')
    args=parser.parse_args()
    if not args.java:
        parser.error('Install Java 25 or supply --java /path/to/java')
    version=subprocess.run([args.java,'-version'],capture_output=True,text=True)
    if not any('"'+str(v)+'.' in version.stderr for v in range(25,40)):
        parser.error('This pinned Kestra runtime requires Java 25 or later')
    cache=args.cache_dir.resolve()
    jar=download(cache,'kestra','2.0.6','kestra-2.0.6')
    download(cache,'plugin-scripts','1.17.0','plugin-script-1.17.0.jar')
    download(cache,'plugin-scripts','1.17.0','plugin-script-python-1.17.0.jar')
    download(cache,'plugin-slack','2.1.1','plugin-slack-2.1.1.jar')
    runtime=Path(tempfile.mkdtemp(prefix='kestra-mlflow-lifecycle-'))
    os.chmod(runtime,0o700)
    password=secrets.token_urlsafe(24)
    config={'kestra':{'server':{'basic-auth':{'username':'demo@example.invalid','password':password}},
        'plugins':{'auto-install':{'enabled':False}},'telemetry':{'enabled':False},
        'storage':{'type':'local','local':{'base-path':str(runtime/'storage')}}},
        'micronaut':{'server':{'host':'127.0.0.1'}}}
    config_path=runtime/'application.yaml'
    config_path.write_text(yaml.safe_dump(config))
    os.chmod(config_path,0o600)
    notifications=[]
    class Receiver(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            notifications.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            (runtime/'notifications.json').write_text(json.dumps(notifications,indent=2))
            self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
        def log_message(self,*args): pass
    webhook=http.server.ThreadingHTTPServer(('127.0.0.1',5001),Receiver)
    threading.Thread(target=webhook.serve_forever,daemon=True).start()
    env=dict(os.environ,PATH=str(Path(sys.executable).parent)+':'+os.environ['PATH'],MLFLOW_DISABLE_AGENT_HINT='1')
    for key,value in {'MLFLOW_TRACKING_URI':'http://127.0.0.1:5000','MLFLOW_TOKEN':'local-fixture',
                      'SLACK_WEBHOOK_URL':'http://127.0.0.1:5001/webhook'}.items():
        env['SECRET_'+key]=base64.b64encode(value.encode()).decode()
    children=[]
    def start(command,log_name):
        handle=(runtime/log_name).open('w')
        child=subprocess.Popen(command,cwd=runtime,env=env,stdout=handle,stderr=handle)
        handle.close()
        children.append(child)
    api=requests.Session();api.trust_env=False;api.auth=('demo@example.invalid',password)
    def ready(url,session):
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            if any(child.poll() is not None for child in children):
                raise RuntimeError('A service exited; inspect '+str(runtime))
            try:
                if session.get(url,timeout=2).status_code==200:return
            except requests.RequestException:pass
            time.sleep(.5)
        raise RuntimeError('Service startup deadline exceeded')
    try:
        start([sys.executable,'-m','mlflow','server','--workers','1','--host','127.0.0.1','--port','5000',
            '--backend-store-uri','sqlite:///'+str(runtime/'mlflow.db'),'--serve-artifacts',
            '--artifacts-destination',str(runtime/'artifacts')],'mlflow.log')
        start([args.java,'-Xmx1200m','-jar',str(jar),'server','local','-c',str(config_path),'-p',
            str(cache/'plugins'),'--port','8080','--worker-thread','4','--no-tutorials'],'kestra.log')
        ready('http://127.0.0.1:5000/health',requests.Session())
        ready('http://127.0.0.1:8080/api/v1/configs',api)
        subprocess.run([sys.executable,str(WORK/'seed_demo.py'),'--output',str(runtime/'demo')],env=env,check=True)
        flow=yaml.safe_load(FLOW.read_text());flow.pop('extend')
        source=yaml.safe_dump(flow,sort_keys=False)
        validated=api.post('http://127.0.0.1:8080/api/v1/flows/validate',data=source,headers={'Content-Type':'application/x-yaml'})
        validated.raise_for_status()
        if any(item.get('constraints') for item in validated.json()):
            raise RuntimeError(validated.text)
        response=api.post('http://127.0.0.1:8080/api/v1/main/flows',data=source,headers={'Content-Type':'application/x-yaml'})
        response.raise_for_status()
        driver=Driver(api,'http://127.0.0.1:8080/api/v1/main',runtime/'evidence')
        if args.test:
            import mlflow
            from run_cases import run_suite
            run_suite(driver,runtime/'demo/evaluation.csv',mlflow.MlflowClient('http://127.0.0.1:5000'))
            print('Integration cases passed. Evidence:',runtime/'evidence')
        else:
            execution=driver.start('manual-release',runtime/'demo/evaluation.csv',enable_promotion=True,exclusive_registry_access=True)
            print('Kestra:', 'http://127.0.0.1:8080/ui/executions/company.mlops/mlflow-candidate-champion-evidence/'+execution['id'])
            print('Local login credentials:',config_path)
            print('Review assess/report.md; Resume with approved=true, report SHA256 and a reason.')
            print('Ctrl-C stops the demo; registry and evidence are retained at',runtime)
            while True:time.sleep(1)
    finally:
        webhook.shutdown()
        for child in reversed(children):
            child.terminate()
            try:child.wait(timeout=10)
            except subprocess.TimeoutExpired:child.kill();child.wait()

if __name__=='__main__':
    main()
