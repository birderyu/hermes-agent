"""One immutable cron report is one HTTP delivery, retaining its GTD fences."""
from uuid import UUID

from .execution import report_identity
from .protocol import dumps, require, validate


def report_body(content, job):
    prefix = 'Cronjob Response: '
    if content.startswith(prefix):
        first, _, rest = content.partition('\n')
        name = first[len(prefix):]
        header = f'(job_id: {job})\n-------------\n\n'
        footer = '\n\nTo stop or manage this job, send me a new message ' + f'(e.g. "stop reminder {name}").'
        if rest.startswith(header) and rest.endswith(footer):
            return rest[len(header):-len(footer)]
    return content


def delivery(config, chat_id, content, metadata):
    require(config is not None and chat_id == 'main', 'not_configured', 503)
    job = metadata.get('job_id')
    require(job in config.jobs)
    try:
        execution = str(UUID(metadata['execution_id']))
    except (KeyError, ValueError, TypeError, AttributeError):
        require(False)
    identity = report_identity(job, metadata['execution_id'])
    payload = {'job_id': job, 'execution_id': execution, 'execution_started_at': identity['execution_started_at'],
               'delivery_kind': identity['delivery_kind'],
               'title': config.jobs[job]['title'], 'body': report_body(content, job)}
    if config.jobs[job]['kind'] == 'agenda':
        payload['business_date'] = identity['business_date']
    if identity.get('trigger_id'):
        payload['trigger_id'] = identity['trigger_id']
    validate('B_BridgeReportDelivery', payload)
    require(len(dumps(payload).encode('utf-8')) <= 262144, 'payload_too_large', 413)
    return payload
