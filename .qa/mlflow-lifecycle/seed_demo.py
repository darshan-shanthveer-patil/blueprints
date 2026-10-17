"""Create a disposable public-data registry fixture and held-out CSV."""
import argparse
import json
from pathlib import Path

import mlflow
import mlflow.sklearn
from mlflow.models import infer_signature
from sklearn.datasets import load_iris
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import PolynomialFeatures, StandardScaler

parser = argparse.ArgumentParser()
parser.add_argument('--tracking-uri', default='http://127.0.0.1:5000')
parser.add_argument('--output', type=Path, default=Path('demo'))
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
mlflow.set_tracking_uri(args.tracking_uri)
mlflow.set_experiment('kestra-model-validation')
client = mlflow.MlflowClient()
x, y = load_iris(return_X_y=True, as_frame=True)
train_x, test_x, train_y, test_y = train_test_split(x, y, test_size=.3, random_state=73, stratify=y)
evaluation = test_x.copy()
evaluation['target'] = test_y
evaluation.to_csv(args.output / 'evaluation.csv', index=False)
models = {
    'champion': make_pipeline(StandardScaler(), PolynomialFeatures(3), LogisticRegression(C=.1, max_iter=2000)),
    'candidate': LogisticRegression(C=1, max_iter=1000),
}
versions = {}
for role, model in models.items():
    model.fit(train_x, train_y)
    with mlflow.start_run(run_name='public-iris-' + role):
        info = mlflow.sklearn.log_model(model, name='classifier',
            signature=infer_signature(train_x, model.predict(train_x)),
            registered_model_name='cpu-lifecycle-demo')
    versions[role] = str(info.registered_model_version)
    client.set_registered_model_alias('cpu-lifecycle-demo', role, versions[role])
(args.output / 'versions.json').write_text(json.dumps(versions, indent=2))
print('Model: cpu-lifecycle-demo; versions:', versions)
print('Held-out CSV:', args.output / 'evaluation.csv')
