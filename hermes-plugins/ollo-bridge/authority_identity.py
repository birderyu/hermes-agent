"""Read the host's live canonical claim; no session-key-to-run guesses or cached agents."""
import sqlite3


def current_claim(adapter, home, session_id, task_id, turn_id):
    from agent.delegation_context import is_delegated_child_context
    from gateway.config import Platform
    from gateway.session_api_turn import api_execution
    from gateway.session_authorities import authority_for_home
    from hermes_constants import get_hermes_home
    from tools.approval_context import _approval_session_key

    if (adapter is None or get_hermes_home() != home or is_delegated_child_context()
            or not session_id or not task_id or not turn_id):
        return None
    api = api_execution.get()
    authority = authority_for_home(adapter.gateway_runner, home)
    if api is None or api['adapter'] is not adapter or authority is None:
        return None
    # The route only selects a live owner. Generation + adopted agent + exact
    # turn fence that selection; the route itself is never exported as run_id.
    for owner, live in tuple(authority.sessions.items()):
        if live.route != _approval_session_key.get() or live.source.platform != Platform.API_SERVER:
            continue
        with live.event_stream.lock:
            stamp = dict(live.event_stream.execution)
            generation, agent = authority.adopted.get(owner, (None, None))
            if (generation is not None and authority.executing.get(owner) == generation
                    and stamp.get('authority_epoch') == authority.epoch
                    and stamp.get('execution_generation') == generation
                    and agent is not None and agent.session_id == session_id
                    and getattr(agent, '_current_task_id', None) == task_id
                    and getattr(agent, '_current_turn_id', None) == turn_id):
                return authority, owner, stamp
    return None


def claim_run_id(claim, adapter):
    if claim is None:
        return None
    from hermes_state_runtime import get_session_admission
    authority, owner, stamp = claim
    try:
        row = get_session_admission(authority.db, admission_id=stamp['admission_id'])
    except (sqlite3.Error, OSError):
        return None
    owner_scope = (row.get('payload', {}).get('api_turn_v1', {}).get('run_owner_scope') if row else None)
    if (row and owner_scope and adapter._run_owners.get(row['request_id']) == owner_scope
            and row['principal_id'] == 'api' and row['target_session_id'] == owner
            and row['status'] == 'started' and row['owner_epoch'] == authority.epoch
            and row['generation'] == stamp['execution_generation']):
        # POST /v1/runs passes its minted run_id as active_run_id; the host
        # commits that as this admission's request_id, not the HTTP UUID.
        return row['request_id']
    return None
