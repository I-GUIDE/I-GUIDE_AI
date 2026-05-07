"""
Flask API wrapper around validator.py.
"""

import os
import uuid
import shutil
import threading
import subprocess
import requests
from pathlib import Path
from flask import Flask, request, jsonify
from validator import NotebookValidator, load_cvmfs_environments

app = Flask(__name__)

jobs = {}
jobs_lock = threading.Lock()

BACKEND_URL = os.environ.get('BACKEND_URL', 'http://localhost:3501')
AUTH_API_KEY = os.environ.get('AUTH_API_KEY', 'x-auth-key')
AUTH_API_KEY_VALUE = os.environ.get('AUTH_API_KEY_VALUE', 'dev-auth-key')
PORT = int(os.environ.get('PORT', 5003))

supported_envs = load_cvmfs_environments()
validator = NotebookValidator()

# CWD lock: validate_notebook() uses Path.cwd() to build Docker volume mounts,
# so we must change directory to the job work_dir before calling it.
_cwd_lock = threading.Lock()


def _validate_from_workdir(work_dir, nb_relative, cvmfs_env):
    with _cwd_lock:
        orig = os.getcwd()
        os.chdir(work_dir)
        try:
            return validator.validate_notebook(nb_relative, cvmfs_env=cvmfs_env)
        finally:
            os.chdir(orig)


def _run_validation_job(job_id, repo_url, notebook_path, notebook_id):
    work_dir = Path(f'/tmp/validator-jobs/{job_id}')
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        with jobs_lock:
            jobs[job_id]['status'] = 'RUNNING'

        repo_name = repo_url.rstrip('/').split('/')[-1].replace('.git', '')
        dest = work_dir / repo_name

        clone = subprocess.run(
            ['git', 'clone', '--depth=1', repo_url, str(dest)],
            capture_output=True, text=True,
            env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'}
        )
        if clone.returncode != 0:
            job_result = {
                'status': 'ERROR',
                'error_type': 'CloneError',
                'message': f'git clone failed: {clone.stderr.strip()[:200]}'
            }
        else:
            nb_path = dest / notebook_path
            if not nb_path.exists():
                job_result = {
                    'status': 'ERROR',
                    'error_type': 'FileNotFoundError',
                    'message': f'Notebook not found in repo: {notebook_path}'
                }
            else:
                env_info = validator._extract_environment(nb_path)
                cvmfs_env, match_type = validator._match_cvmfs_environment(env_info, supported_envs)

                if supported_envs and match_type == 'none':
                    job_result = {
                        'status': 'NO_ENV',
                        'error_type': 'NO_ENV',
                        'message': 'No matching CVMFS environment found on the I-GUIDE platform'
                    }
                elif cvmfs_env and not Path(cvmfs_env['cvmfs_path']).exists():
                    job_result = {
                        'status': 'ENV_UNAVAILABLE',
                        'error_type': 'ENV_UNAVAILABLE',
                        'message': f'CVMFS repo not mounted: {cvmfs_env["cvmfs_path"]}'
                    }
                else:
                    nb_relative = Path(repo_name) / notebook_path
                    job_result = _validate_from_workdir(work_dir, nb_relative, cvmfs_env)
                    job_result['matched_environment'] = cvmfs_env['name'] if cvmfs_env else None

        with jobs_lock:
            jobs[job_id]['status'] = 'DONE'
            jobs[job_id]['result'] = job_result

        # Callback to backend
        try:
            requests.put(
                f'{BACKEND_URL}/api/notebooks/{notebook_id}/validation',
                json=job_result,
                headers={'Content-Type': 'application/json', AUTH_API_KEY: AUTH_API_KEY_VALUE},
                timeout=10
            )
            print(f'Callback sent for {notebook_id}: {job_result["status"]}')
        except Exception as e:
            print(f'Callback failed for job {job_id}: {e}')

    except Exception as e:
        with jobs_lock:
            jobs[job_id]['status'] = 'DONE'
            jobs[job_id]['result'] = {'status': 'ERROR', 'error_type': type(e).__name__, 'message': str(e)}
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@app.route('/validate/notebook', methods=['POST'])
def validate_notebook():
    data = request.get_json()
    repo_url = data.get('repo_url')
    notebook_path = data.get('notebook_path')
    notebook_id = data.get('notebook_id')

    if not all([repo_url, notebook_path, notebook_id]):
        return jsonify({'error': 'Missing required fields: repo_url, notebook_path, notebook_id'}), 400

    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {'status': 'PENDING', 'result': None}

    threading.Thread(
        target=_run_validation_job,
        args=(job_id, repo_url, notebook_path, notebook_id),
        daemon=True
    ).start()

    return jsonify({'job_id': job_id}), 202


@app.route('/jobs/<job_id>', methods=['GET'])
def get_job(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(job)


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})


if __name__ == '__main__':
    print(f'Starting validator API on port {PORT}')
    print(f'Backend URL: {BACKEND_URL}')
    print(f'CVMFS environments loaded: {len(supported_envs)}')
    app.run(host='0.0.0.0', port=PORT)
