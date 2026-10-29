"""Reuse an authorized gateway model in memory for isolated acceptance only.

Credentials travel through a captured SSH pipe into process memory only;
they are never logged or returned to the caller. No profile files are created.
"""
import json
import os
import re
import shlex
import subprocess


def load(host,container,legacy_model=None):
    for value in (host,container):
        if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_.@-]{1,128}',value) or value.startswith('-'):
            raise ValueError('invalid_gateway_target')
    script = 'legacy_model='+repr(legacy_model)+'''\nimport json,os
from app.model_profiles import resolve,load_profiles,ModelProfileError
try:
 p,_=resolve(None,'parameter_extraction')
 value=dict(base_url=p.base_url,model=p.model,provider=p.provider,key=p.api_key(),
   capabilities=sorted(p.capabilities),tasks={k:vars(v) for k,v in p.tasks.items()})
except ModelProfileError:
 if not legacy_model or load_profiles(): raise
 value=dict(base_url='https://api.deepseek.com',model=legacy_model,provider='openai-compatible',
   key=os.environ['DEEPSEEK_API_KEY'],capabilities=['chat','json','code'])
print(json.dumps(value))
'''
    child = subprocess.run(['ssh','-o','BatchMode=yes','-o','ClearAllForwardings=yes',
        '-o','ConnectTimeout=8',host,'docker exec '+shlex.quote(container)+' python -c '+shlex.quote(script)],
        capture_output=True,text=True,encoding='utf-8',timeout=20)
    if child.returncode:
        raise RuntimeError('authorized_gateway_model_unavailable')
    value = json.loads(child.stdout)
    os.environ['QA_GATEWAY_MODEL_KEY'] = value.pop('key')
    value.update(label='验收模型（沿用已授权配置）',api_key_env='QA_GATEWAY_MODEL_KEY')
    os.environ['MODEL_PROFILES_JSON'] = json.dumps({'qa-gateway-profile':value})
    os.environ['DEFAULT_MODEL_PROFILE'] = 'qa-gateway-profile'
