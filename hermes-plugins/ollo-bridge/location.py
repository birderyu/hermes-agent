import asyncio
import time
import hashlib

from .http_client import Budget
from .protocol import BridgeError, dumps, require, validate


async def locate(client, args, runtime):
    require(isinstance(args, dict) and set(args) <= {'max_age_seconds', 'timeout_seconds'})
    payload = {'max_age_seconds': args.get('max_age_seconds', 120), 'timeout_seconds': args.get('timeout_seconds', 15)}
    validate('B_BridgeLocationRequest', payload)
    # Hash only host IDs; no raw session identifiers in a dedupe key or log.
    payload['runtime_call_id'] = hashlib.sha256(dumps(runtime, sort_keys=True).encode()).hexdigest()
    budget = Budget(payload['timeout_seconds'])
    started_at = time.time()
    original_id, requested_at = None, started_at
    try:
        result = await client.request('POST', '/internal/v1/location/requests', body=payload, timeout=budget.remaining())
        while True:
            validate('B_BridgeLocationResult', result)
            if result['status'] != 'pending':
                if result['status'] == 'ok':
                    require(result['platform'] == 'iOS')
                    point_time = result['point']['timestamp']
                    require(point_time <= time.time() + 10)
                    if payload['max_age_seconds'] == 0:
                        require(point_time >= requested_at and not result['cached'])
                    else:
                        require(result['age_seconds'] <= payload['max_age_seconds']
                                and time.time() - point_time <= payload['max_age_seconds'] + 1)
                return result
            require(0 < result['expires_at'] - result['requested_at'] <= 15)
            if original_id is None:
                original_id, requested_at = result['id'], result['requested_at']
            require(result['id'] == original_id and result['requested_at'] == requested_at)
            budget.deadline = min(budget.deadline, time.monotonic() + result['expires_at'] - time.time())
            # A merged server request can have an earlier deadline, never a later one.
            remaining = min(budget.remaining(), result['expires_at'] - time.time())
            if remaining <= 0:
                return {'status': 'timeout'}
            request_id = result['id']
            await asyncio.sleep(min(.15, remaining))
            result = await client.request('GET', '/internal/v1/location/requests/' + request_id,
                                          timeout=min(budget.remaining(1), max(.001, remaining)))
    except BridgeError as exc:
        return {'status': 'timeout' if exc.code == 'read_timeout' else 'unavailable'}
