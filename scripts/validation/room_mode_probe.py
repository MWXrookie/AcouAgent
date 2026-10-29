"""Run controlled room templates via an existing gateway's internal executor.

SSH is a transport into the gateway, not a host Python solver. Credentials stay
in that container's environment. No production dashboard/skills are written.
"""
import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app import executor_evidence, room_modes
from app.task_contract import room_contract


def remote_execute(host, container, code, timeout, *, request_nonce=None, managed=False):
    """Real test transport only; preserves and verifies the executor receipt."""
    for value in (host, container):
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.@-]{1,128}', value) or value.startswith('-'):
            raise ValueError('invalid_transport_target')
    relay = '''import json,os,sys,urllib.request
p=json.load(sys.stdin)
r=urllib.request.Request('http://jwave-executor:8010/execute',data=json.dumps(p).encode(),headers={'Content-Type':'application/json','X-Executor-Token':os.environ['EXECUTOR_SHARED_TOKEN']})
with urllib.request.urlopen(r,timeout=40) as response:
    sys.stdout.write(response.read().decode())
'''
    if managed:
        relay = '''import json,sys
from app.execution import _execute_code
p=json.load(sys.stdin)
print(json.dumps(_execute_code(p['code'],p['timeout_seconds'],request_nonce=p['request_nonce'])))
'''
    command = 'docker exec -i '+shlex.quote(container)+' python -c '+shlex.quote(relay)
    nonce = request_nonce or uuid.uuid4().hex
    payload = dict(code=code, timeout_seconds=timeout, request_nonce=nonce)
    child = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ClearAllForwardings=yes',
        '-o', 'ConnectTimeout=8', host, command], input=json.dumps(payload),
        encoding='utf-8', capture_output=True, timeout=timeout+20)
    if child.returncode:
        raise RuntimeError('gateway_transport_failed')
    result = json.loads(child.stdout)
    if managed:
        evidence = result.get('execution_evidence',{})
        checked = executor_evidence.verify_executor_receipt(evidence.get('receipt'),result,code,timeout,nonce)
        if evidence.get('matched') is not True or checked['matched'] is not True:
            raise ValueError('managed_gateway_receipt_unmatched')
    else:
        receipt = result.pop('executor_receipt', None)
        result['execution_evidence'] = executor_evidence.verify_executor_receipt(receipt,result,code,timeout,nonce)
    result['execution_evidence']['test_transport'] = 'ssh_gateway_internal_executor'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ssh-host', required=True)
    parser.add_argument('--gateway-container', default='dify-mcp')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.@-]{1,128}', args.ssh_host) or args.ssh_host.startswith('-'):
        parser.error('invalid SSH host')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', args.gateway_container) or args.gateway_container.startswith('-'):
        parser.error('invalid container name')
    args.output.mkdir(parents=True, exist_ok=True)
    fixture = json.loads((Path(__file__).resolve().parents[2]/'tests/fixtures/task_intake/room_reference.json').read_text(encoding='utf-8'))
    cases = [fixture['original_query'],
        '二维矩形房间，Lx=400cm，Ly=3000mm，声速343m/s，四壁刚性，前10个模态，不包括零模态',
        '二维矩形房间，Lx=5m，Ly=4m，声速340m/s，四壁刚性，前20个模态，包含零模态',
        '二维矩形房间，Lx=3m，Ly=3m，声速343m/s，四壁刚性，前9个模态',
        '二维矩形房间，Lx=4m，Ly=3m，声速343m/s，四壁刚性，前12个模态']
    outcomes = []
    try:
        for index, query in enumerate(cases):
            contract = room_contract(query)
            code, nonce = room_modes.build_code(contract), uuid.uuid4().hex
            result = remote_execute(args.ssh_host, args.gateway_container, code, 30, request_nonce=nonce)
            (args.output/f'case-{index+1}-execution.json').write_text(json.dumps(dict(contract=contract,
                code=code, result=result), ensure_ascii=False, indent=2), encoding='utf-8')
            verified = room_modes.verify_result(contract, result)
            outcomes.append(dict(case=index+1, passed=True, code_sha256=executor_evidence.sha256(code),
                request_nonce=nonce, duration_ms=result.get('duration_ms'), validation=verified))
            print(json.dumps(dict(case=index+1, passed=True, max_relative_error=verified['max_relative_error']), ensure_ascii=False), flush=True)
    except Exception as error:
        outcomes.append(dict(passed=False, error=type(error).__name__))
        raise
    finally:
        (args.output/'report.json').write_text(json.dumps(dict(kind='real_internal_executor_room_probe',
            cases=outcomes, dashboard_writes=0, learning_writes=0, product_browser_acceptance=False),
            ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
