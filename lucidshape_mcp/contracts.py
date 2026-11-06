"""Typed MCP response contracts with backwards-compatible top-level fields."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class ErrorInfo(BaseModel):
    code: str
    message: str
    retryable: bool = False


class Response(BaseModel):
    model_config = ConfigDict(extra='allow')
    schema_version: str = '1.0'
    error_details: ErrorInfo | None = None


class Artifact(Response):
    name: str
    path: str
    bytes: int = Field(ge=0)


class Owner(BaseModel):
    instance_id: str
    pid: int


class JobRecord(Response):
    job_id: str | None = Field(default=None, description='Present when a batch job has been created')
    status: Literal['queued', 'running', 'succeeded', 'failed', 'timed_out', 'cancelled', 'interrupted']
    template: str | None = None
    phase: str | None = None
    owner: Owner | None = None
    owner_active: bool | None = None
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    heartbeat_at: str | None = None
    elapsed_seconds: float | None = None
    poll_interval_ms: int | None = None
    artifacts: list[Artifact] = Field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None


class JobList(Response):
    jobs: list[JobRecord] = Field(default_factory=list)
    total: int = 0
    offset: int = 0
    limit: int = 50
    unreadable_records: int = 0


class Cancellation(Response):
    job_id: str | None = None
    status: str
    cancel_requested: bool = False
    message: str = ''


class LogResult(Response):
    job_id: str | None = None
    text: str = ''
    truncated: bool = False


class CapabilityResult(Response):
    backend: str | None = None
    executable: str | None = None
    executable_exists: bool = False
    maximum_parallel_jobs: int = 1
    policy: dict[str, Any] = Field(default_factory=dict)


class LiveResult(Response):
    status: str | None = None
    backend: str | None = None


def output_type(name):
    if name == 'get_capabilities':
        return CapabilityResult
    if name == 'list_jobs':
        return JobList
    if name in ('cancel_job', 'cancel_geometry_job'):
        return Cancellation
    if name == 'read_job_log':
        return LogResult
    if name.startswith('studio_'):
        return LiveResult
    return JobRecord
