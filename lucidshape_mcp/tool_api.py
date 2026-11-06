"""One registration boundary for schema, policy, errors and annotations."""
from functools import wraps
import inspect
import json
from typing import Annotated

from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from .contracts import output_type
from .policy import ACTIVE_POLICY, ALL_TOOLS, READ_ONLY
from .compatibility import TOOL_ALIASES, PARAMETER_ALIASES


def parameter_type(name, annotation):
    constraints = {
        'timeout_seconds': dict(ge=1, le=3600, description='Worker execution timeout, in seconds'),
        'offset': dict(ge=0), 'limit': dict(ge=1, le=200),
        'tail_bytes': dict(ge=1, le=64000), 'rays': dict(ge=1, le=1000000, description='Number of individual rays'),
        'flux_lm': dict(ge=0, description='Luminous flux in lumens'),
        'recursion_limit': dict(ge=1, le=256),
    }
    # Existing backend validators retain operation-specific, tighter constraints.
    if name in constraints:
        return Annotated[annotation, Field(strict=True, **constraints[name])]
    if name in ('model_path', 'cad_path', 'catia_path', 'lid_path', 'rayfile_path', 'file_path'):
        return Annotated[annotation, Field(description='Absolute path on the Windows machine running the server')]
    return annotation


def registrar(server, engine):
    def tool():
        def decorate(fn):
            name = fn.__name__
            if name not in ALL_TOOLS:
                raise ValueError('Tool has no policy registration: ' + name)
            result_type = output_type(name)
            signature = inspect.signature(fn)

            @wraps(fn)
            async def invoke(*args, **kwargs):
                token = ACTIVE_POLICY.set(engine.policy)
                try:
                    arguments = signature.bind(*args, **kwargs)
                    arguments.apply_defaults()
                    engine.policy.authorize(name, arguments.arguments)
                    result = fn(*args, **kwargs)
                    if inspect.isawaitable(result):
                        result = await result
                    return result_type.model_validate(result)
                except (ValueError, OSError, RuntimeError) as error:
                    code = ('ACCESS_DENIED' if isinstance(error, PermissionError) else
                            'FILE_NOT_FOUND' if isinstance(error, FileNotFoundError) else
                            'INVALID_ARGUMENT' if isinstance(error, ValueError) else 'OPERATION_FAILED')
                    body = result_type.model_validate({'status': 'failed', 'error_details': {
                        'code': code, 'message': str(error), 'retryable': False}}).model_dump(mode='json')
                    return CallToolResult(isError=True, structuredContent=body,
                        content=[TextContent(type='text', text=json.dumps(body, ensure_ascii=True))])
                finally:
                    ACTIVE_POLICY.reset(token)

            invoke.__signature__ = signature.replace(
                parameters=[p.replace(annotation=parameter_type(p.name, p.annotation)) for p in signature.parameters.values()],
                return_annotation=result_type)
            invoke.__annotations__ = {**fn.__annotations__, 'return': result_type}
            readonly = name in READ_ONLY
            annotations=ToolAnnotations(readOnlyHint=readonly, destructiveHint=name == 'catia_show_all',
                idempotentHint=name in {'get_capabilities', 'get_job', 'list_jobs', 'read_job_log'}, openWorldHint=False)
            if name in engine.policy.enabled_tools:
                server.add_tool(invoke, name=name, structured_output=True, annotations=annotations)
            for alias,canonical in TOOL_ALIASES.items():
                if canonical!=name or alias not in engine.policy.enabled_tools:
                    continue
                @wraps(invoke)
                async def legacy_call(*args, _target=invoke, _alias=alias, **kwargs):
                    kwargs={next((new for new,old in PARAMETER_ALIASES.items() if old==key),key):value for key,value in kwargs.items()}
                    if _alias=='repair_s9_main_lens_mesh' and kwargs.get('target_region') is None:
                        kwargs['target_region']='main_lens'
                    if _alias=='replay_s9_boundary_paths' and kwargs.get('start_segment_indices') is None:
                        kwargs['start_segment_indices']=[12]
                    return await _target(*args, **kwargs)
                legacy_call.__signature__=invoke.__signature__.replace(parameters=[
                    p.replace(name=PARAMETER_ALIASES.get(p.name,p.name)) for p in invoke.__signature__.parameters.values()])
                server.add_tool(legacy_call, name=alias, structured_output=True, annotations=annotations,
                                description=f'Deprecated alias of {name}. '+(fn.__doc__ or ''))
            return fn
        return decorate
    return tool
