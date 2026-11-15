"""Recompute desktop UAT comparisons from receipt-bound fields, without a model.

Operator-only evidence collection. Reads the existing desktop gateway's data;
never activates skills, dispatches solvers, exports credentials or reference arrays.
"""
import argparse
import json
from pathlib import Path
import subprocess


PROBE = r'''
import json,sqlite3,sys
from app.skill_general_library import get_library
from app import skill_general_profiles as profiles
from app.skill_auto_source import verify_source
library=get_library()
db=sqlite3.connect('file:/app/data/skill_store.db?mode=ro',uri=True)
db.row_factory=sqlite3.Row
out=[]
for item in json.loads(sys.argv[1]):
    run=item['run_id']
    if item['arm']=='first':
        rows=db.execute("""SELECT j.job_id,a.output_manifest_sha256 FROM skill_draft_requests r
            JOIN skill_validation_jobs j ON j.draft_id=r.draft_id
            JOIN skill_validation_attempts a ON a.job_id=j.job_id AND a.attempt_no=j.attempt_no
            WHERE r.actor_id='internal-auto-learning' AND r.operation='auto-source'
            AND r.idempotency_key=? AND j.state='admitted'""",(run,)).fetchall()
        assert len(rows)==1,'unique admitted automatic job required'
        job=library._job(db,rows[0]['job_id'])
        manifest=rows[0]['output_manifest_sha256']
        assert job['attempt']['run_id']!=run,'admission must use a separate validation run'
    else:
        rows=db.execute("""SELECT a.job_id,r.request_nonce,r.request_sha256,c.capsule_sha256
            FROM skill_local_reference_dispatches r
            JOIN skill_local_reference_completions c ON c.run_id=r.run_id
            JOIN skill_validation_admissions a ON a.skill_id=r.skill_id AND a.skill_version=r.version
            WHERE r.run_id=? AND c.outcome='verified'""",(run,)).fetchall()
        assert len(rows)==1,'unique verified new reuse required'
        job=library._job(db,rows[0]['job_id'])
        job['attempt']=dict(job['attempt'],run_id=run,request_nonce=rows[0]['request_nonce'],request_sha256=rows[0]['request_sha256'])
        manifest=rows[0]['capsule_sha256']
    proof=library._comparison(job,manifest)
    assert proof['comparison']['case']==item['case'] and proof['comparison']['numeric_pass']
    assert proof['comparison']['phase_compared'] is False
    comparison=proof['comparison']
    actual_field=proof['field_asset_sha256']
    if item['arm']=='first':
        source=verify_source(run,library=library,_for_presentation=True)
        actual_field=source['manifest']['field_asset_sha256']
        actual=library.assets.read_field(actual_field,source['spec']['result_spec'])
        reference=profiles.load_reference(item['case'],library.assets._path(proof['reference_sha256']+'.npy'))
        comparison=profiles.compare(item['case'],actual,reference)
        assert comparison['numeric_pass'],'first displayed source field must also pass'
    out.append(dict(item,validation_run_id=proof['run_id'],comparison=comparison,
        admission_comparison=proof['comparison'],
        runtime_sha256=proof['runtime_sha256'],reference_sha256=proof['reference_sha256'],
        validator_sha256=proof['validator_sha256'],field_asset_sha256=actual_field))
print(json.dumps(out))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--docker', required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding='utf-8'))
    if report['errors'] or len(report['results']) != 4:
        raise ValueError('four successful GUI model runs required')
    runs = [dict(case=row['case'], arm=row['arm'], run_id=row['result']['run_id'])
            for row in report['results']]
    command = [args.docker, '--context', 'desktop-linux', 'exec',
               'acouagent-desktop-gateway-1', 'python', '-c', PROBE, json.dumps(runs)]
    response = subprocess.run(command, check=True, capture_output=True, text=True,
                              encoding='utf-8', timeout=90)
    evidence = json.loads(response.stdout)
    if len(evidence) != 4 or len({r['run_id'] for r in evidence}) != 4:
        raise ValueError('unique execution evidence required')
    destination = args.report.parent / 'numeric-evidence.json'
    destination.write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    for row in evidence:
        print(row['case'], row['arm'], row['comparison']['relative_magnitude_linf'],
              '<', row['comparison']['threshold_strict'])


if __name__ == '__main__':
    main()
