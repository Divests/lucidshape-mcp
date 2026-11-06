"""Durable job ownership, read-only observation and cooperative cancellation."""
import asyncio
from datetime import datetime, timezone
import json
import os
import re
import time
import uuid

from .coordination import FileLease, lease_held

ACTIVE = {'queued', 'running'}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, data):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class JobControl:
    def initialize_jobs(self):
        self.instance_id = uuid.uuid4().hex
        self._leases = {}
        self._records = {}
        self._monitors = {}
        self._cancel_reasons = {}

    def _save(self, record):
        now = utc_now()
        record.setdefault('schema_version', '1.0')
        record.setdefault('owner', {'instance_id': self.instance_id, 'pid': os.getpid()})
        record.setdefault('created_at', now)
        record.setdefault('phase', record['status'])
        record['updated_at'] = now
        record['heartbeat_at'] = now
        if record['status'] not in ACTIVE:
            record.setdefault('finished_at', now)
            record['phase'] = 'finished'
        if record.get('error') and not record.get('error_details'):
            code = {'timed_out': 'JOB_TIMEOUT', 'cancelled': 'JOB_CANCELLED'}.get(record['status'], 'JOB_FAILED')
            record['error_details'] = {'code': code, 'message': record['error'], 'retryable': False}
        atomic_json(self._directory(record['job_id']) / 'job.json', record)

    def _enqueue(self, record, expected):
        job_id = record['job_id']
        lease = FileLease(self._directory(job_id) / 'owner.lock')
        if not lease.acquire():
            raise RuntimeError('Job already has an owner')
        self._leases[job_id] = lease
        self._records[job_id] = record
        try:
            self._save(record)
        except Exception:
            self._leases.pop(job_id).close()
            self._records.pop(job_id)
            raise
        task = asyncio.create_task(self._run(record, expected))
        self._tasks[job_id] = task
        self._monitors[job_id] = asyncio.create_task(self._monitor(job_id, task))
        task.add_done_callback(lambda finished: self._job_finished(job_id, finished))
        return dict(record)

    async def _monitor(self, job_id, task):
        last_save = time.monotonic()
        request = self._directory(job_id) / 'cancel.json'
        try:
            while not task.done():
                await asyncio.sleep(.2)
                if request.is_file():
                    try:
                        data = json.loads(request.read_text(encoding='utf-8'))
                    except (OSError, ValueError):
                        data = {}
                    if not isinstance(data, dict):
                        data = {}
                    if data.get('job_id') == job_id and data.get('owner_instance_id') == self.instance_id:
                        self._cancel_reasons[job_id] = 'Cancellation requested through MCP'
                        task.cancel()
                        return
                if time.monotonic() - last_save >= 2 and not task.done():
                    self._save(self._records[job_id])
                    last_save = time.monotonic()
        except asyncio.CancelledError:
            pass

    def _job_finished(self, job_id, task):
        record = self._records.pop(job_id)
        try:
            # A task cancelled before its coroutine starts has no finally block.
            if task.cancelled() and record['status'] in ACTIVE:
                record.update(status='cancelled', error=self._cancel_reasons.get(job_id, 'MCP server shut down'))
                self._save(record)
        finally:
            self._tasks.pop(job_id, None)
            monitor = self._monitors.pop(job_id, None)
            if monitor:
                monitor.cancel()
            self._leases.pop(job_id).close()
            self._cancel_reasons.pop(job_id, None)

    def get_job(self, job_id):
        path = self._directory(job_id) / 'job.json'
        if not path.is_file():
            raise ValueError('Unknown job_id')
        record = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(record, dict) or record.get('job_id') != job_id or record.get('status') not in ACTIVE | {
                'succeeded', 'failed', 'timed_out', 'cancelled', 'interrupted'}:
            raise ValueError('Invalid persisted job record')
        held = lease_held(path.with_name('owner.lock')) if record['status'] in ACTIVE else False
        record['owner_active'] = held
        record['recorded_status'] = record['status']
        if record['status'] in ACTIVE and not held:
            # A derived observation, NEVER a write to another owner's record.
            message = 'Job owner is unavailable; native completion is unverified; do not automatically resubmit'
            record.update(status='interrupted', error=message,
                          error_details={'code': 'OWNER_UNAVAILABLE', 'message': message, 'retryable': False})
        created = record.get('created_at')
        if created:
            end = datetime.fromisoformat(record.get('finished_at') or utc_now())
            record['elapsed_seconds'] = max(0, (end - datetime.fromisoformat(created)).total_seconds())
        record['poll_interval_ms'] = 1000 if record['status'] in ACTIVE else None
        return record

    def list_jobs(self, status=None, offset=0, limit=50):
        if status is not None and status not in ACTIVE | {'succeeded', 'failed', 'timed_out', 'cancelled', 'interrupted'}:
            raise ValueError('Unknown job status')
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError('offset must be a nonnegative integer')
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError('limit must be between 1 and 200')
        records, unreadable = [], 0
        for path in self.root.glob('*/job.json'):
            if not re.fullmatch('[0-9a-f]{32}', path.parent.name):
                continue
            try:
                record = self.get_job(path.parent.name)
            except (OSError, ValueError):
                unreadable += 1
                continue
            if status is None or record['status'] == status:
                records.append({key: record[key] for key in ('job_id', 'template', 'status', 'phase', 'created_at',
                    'updated_at', 'elapsed_seconds', 'owner', 'owner_active', 'error_details') if key in record})
        records.sort(key=lambda r: (r.get('created_at', ''), r['job_id']), reverse=True)
        return {'jobs': records[offset:offset+limit], 'total': len(records), 'offset': offset,
                'limit': limit, 'unreadable_records': unreadable}

    def cancel_job(self, job_id):
        record = self.get_job(job_id)
        if record['status'] not in ACTIVE:
            return {'job_id': job_id, 'status': record['status'], 'cancel_requested': False,
                    'message': 'Job is terminal or its owner is unavailable; no process was terminated'}
        owner = record.get('owner', {}).get('instance_id')
        if not owner:
            raise ValueError('Legacy job has no verified owner; cancellation refused')
        atomic_json(self._directory(job_id) / 'cancel.json',
                    {'job_id': job_id, 'owner_instance_id': owner, 'requested_at': utc_now()})
        return {'job_id': job_id, 'status': 'cancel_requested', 'cancel_requested': True,
                'message': 'Owner will stop its worker; poll get_job. CAD application completion may remain unverified.'}
