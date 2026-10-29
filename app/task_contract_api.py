"""No-model task preview and source-bound single-use confirmation API."""
import asyncio
import json
import secrets

from starlette.responses import JSONResponse

from app.task_contract import ConfirmationDrafts, room_contract

DRAFTS = ConfirmationDrafts()


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError('duplicate_request_field')
        result[key] = value
    return result


async def _body(request, allowed):
    async def read():
        pieces, size = [], 0
        async for piece in request.stream():
            size += len(piece)
            if size > 131072:
                raise ValueError('task_request_too_large')
            pieces.append(piece)
        return b''.join(pieces)
    value = json.loads(await asyncio.wait_for(read(), 3), object_pairs_hook=_pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite_request')))
    if not isinstance(value, dict) or set(value)-allowed:
        raise ValueError('invalid_task_request')
    for name in ('message', 'requirement'):
        if name in value and (not isinstance(value[name], str) or len(value[name].encode()) > 65536):
            raise ValueError('invalid_task_request')
    session, profile = value.get('session_id'), value.get('model_profile')
    if not isinstance(session, str) or not 1 <= len(session) <= 128:
        raise ValueError('task_session_required')
    if profile is not None and (not isinstance(profile, str) or len(profile) > 64):
        raise ValueError('invalid_model_profile')
    return value


def _response(value, status=200):
    return JSONResponse(value, status_code=status, headers={'Cache-Control': 'no-store'})


def register(mcp, runner=None, service=None):
    @mcp.custom_route('/api/task-intake', methods=['POST'])
    async def prepare(request):
        try:
            body = await _body(request, {'message', 'requirement', 'session_id', 'model_profile', 'previous_task_id'})
            query = body.get('message', '').strip()
            if not query:
                raise ValueError('task_input_invalid')
            previous = body.get('requirement', '').strip()
            # Follow-up material is combined literally, never rewritten by a model.
            contract = room_contract(query)
            if contract is None and previous and room_contract(previous) is not None:
                query = previous+'\n'+query
                contract = room_contract(query)
            if contract is None:
                return _response(dict(handled=False))
            task_id = DRAFTS.put(contract, body['session_id'], body.get('model_profile'), body.get('previous_task_id'))
            return _response(dict(handled=True, task_id=task_id, contract=contract, execution_available=runner is not None or service is not None))
        except (ValueError, TypeError, UnicodeError, asyncio.TimeoutError) as error:
            code = str(error) if isinstance(error, ValueError) else 'invalid_task_request'
            if code not in {'task_request_too_large', 'task_input_invalid', 'task_session_required', 'invalid_model_profile', 'task_draft_capacity', 'duplicate_request_field'}:
                code = 'invalid_task_request'
            return _response(dict(error=code), 429 if code == 'task_draft_capacity' else 400)

    @mcp.custom_route('/api/task-intake/cancel', methods=['POST'])
    async def cancel(request):
        try:
            body = await _body(request, {'session_id', 'model_profile', 'task_id'})
            removed = DRAFTS.cancel(body.get('task_id'), body['session_id'], body.get('model_profile'))
            return _response(dict(cancelled=removed))
        except (ValueError, TypeError, UnicodeError, asyncio.TimeoutError):
            return _response(dict(error='invalid_task_request'), 400)

    @mcp.custom_route('/api/task-intake/confirm', methods=['POST'])
    async def confirm(request):
        try:
            body = await _body(request, {'message', 'session_id', 'model_profile', 'task_id', 'contract_sha256', 'accepted_proposals'})
            if runner is None and service is None:
                return _response(dict(error='task_capability_not_ready'), 409)
            contract = DRAFTS.consume(body.get('task_id'), body.get('contract_sha256'), body['session_id'],
                                      body.get('model_profile'), body.get('message'), body.get('accepted_proposals'))
            # A fresh id identifies this request; the runner owns its real run id.
            if service is not None:
                result = await asyncio.to_thread(service.submit, contract, body['task_id'], body['session_id'], body.get('model_profile'))
            else:
                result = await asyncio.to_thread(runner, contract)
            return _response(dict(result, request_id=secrets.token_hex(16)))
        except (ValueError, TypeError, UnicodeError, asyncio.TimeoutError):
            return _response(dict(error='task_confirmation_invalid_or_expired'), 400)

    @mcp.custom_route('/api/task-intake/status', methods=['POST'])
    async def status(request):
        try:
            body = await _body(request, {'session_id', 'model_profile', 'task_id'})
            key = body.get('task_id')
            if not isinstance(key, str) or not 1 <= len(key) <= 128:
                raise ValueError('task_id_invalid')
            if service is None:
                return _response(dict(error='task_capability_not_ready'), 409)
            result = await asyncio.to_thread(service.status, key, body['session_id'], body.get('model_profile'))
            return _response(result)
        except (ValueError, TypeError, UnicodeError, asyncio.TimeoutError):
            return _response(dict(error='invalid_task_request'), 400)
