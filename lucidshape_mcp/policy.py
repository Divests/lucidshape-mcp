"""Server-side tool selection and trusted local path boundaries (not a sandbox)."""
from contextvars import ContextVar
from dataclasses import dataclass
import json
import os
from pathlib import Path
from .compatibility import TOOL_ALIASES, operation

GROUPS = {
    'core': {'get_capabilities', 'get_job', 'list_jobs', 'cancel_job', 'read_job_log', 'health_check'},
    'studio': {'studio_open_file', 'studio_get_live_state', 'studio_inspect_current', 'studio_get_api_catalog',
               'studio_inspect_objects', 'studio_get_simulation_settings', 'studio_import_step'},
    'catia': {'catia_get_state', 'catia_inspect_part', 'catia_export_step', 'catia_show_all',
              'catia_create_plane_mirror', 'catia_convert_step_to_stl', 'catia_export_body_stl'},
    'geometry': {'inspect_cad_geometry', 'inspect_optical_region_topology', 'import_cad_geometry', 'cancel_geometry_job'},
    'analysis': {'inspect_rayfile', 'analyze_lid', 'generate_road_views', 'export_ies', 'compare_photometry', 'bin_rayfile', 'replay_rayfile_batch'},
    'simulation': {'inspect_model', 'inspect_optical_parameters', 'set_emitter_parameters', 'simulate_model',
                   'run_parameter_sweep', 'submit_demo_simulation', 'prepare_cad_mirror_scene'},
    'experimental': {'preflight_optical_scene', 'validate_traversal_absorption', 'trace_optical_paths', 'merge_optical_batches',
                     'trace_optical_gpu', 'trace_optical_gpu_control', 'replay_optical_boundary_paths', 'repair_optical_mesh',
                     'prepare_optical_regions'},
    'legacy': set(TOOL_ALIASES),
}
ALL_TOOLS = set().union(*GROUPS.values())
DEFAULT_GROUPS = tuple(name for name in GROUPS if name not in ('experimental','legacy'))
READ_ONLY = {
    'get_capabilities', 'get_job', 'list_jobs', 'read_job_log', 'studio_get_live_state', 'studio_inspect_current',
    'studio_get_api_catalog', 'studio_inspect_objects', 'studio_get_simulation_settings', 'catia_get_state',
    'catia_inspect_part', 'inspect_cad_geometry', 'inspect_optical_region_topology', 'inspect_rayfile',
    'inspect_model', 'inspect_optical_parameters', 'analyze_lid', 'compare_photometry',
}
ACTIVE_POLICY = ContextVar('lucidshape_active_policy', default=None)


def boolean(name):
    value = os.environ.get(name, 'false').lower()
    if value not in ('true', 'false', '1', '0'):
        raise ValueError(name + ' must be true/false or 1/0')
    return value in ('true', '1')


@dataclass(frozen=True)
class Policy:
    work_dir: Path
    toolsets: tuple[str, ...] = DEFAULT_GROUPS
    tools: tuple[str, ...] = ()
    read_only: bool = False
    allowed_roots: tuple[Path, ...] = ()
    allow_source_writes: bool = False

    def __post_init__(self):
        # Direct construction must canonicalize paths just like from_env.
        # Windows short names (e.g. RUNNER~1) identify the same physical roots.
        object.__setattr__(self, 'work_dir', Path(self.work_dir).resolve())
        object.__setattr__(self, 'allowed_roots', tuple(
            Path(root).resolve(strict=True) for root in self.allowed_roots
        ))

    @classmethod
    def from_env(cls, work_dir):
        groups = tuple(x.strip() for x in os.environ.get('LUCIDSHAPE_TOOLSETS', ','.join(DEFAULT_GROUPS)).split(',') if x.strip())
        if groups == ('all',):
            groups = tuple(GROUPS)
        if not groups or set(groups) - GROUPS.keys():
            raise ValueError('Unknown or empty LUCIDSHAPE_TOOLSETS')
        tools = tuple(x.strip() for x in os.environ.get('LUCIDSHAPE_TOOLS', '').split(',') if x.strip())
        if set(tools) - ALL_TOOLS:
            raise ValueError('Unknown LUCIDSHAPE_TOOLS')
        roots = json.loads(os.environ.get('LUCIDSHAPE_ALLOWED_ROOTS', '[]'))
        if not isinstance(roots, list) or any(not isinstance(p, str) or not Path(p).is_absolute() for p in roots):
            raise ValueError('LUCIDSHAPE_ALLOWED_ROOTS must be a JSON array of absolute directory paths')
        resolved = tuple(Path(p).resolve(strict=True) for p in roots)
        if any(not p.is_dir() for p in resolved):
            raise ValueError('Allowed roots must be existing directories')
        return cls(Path(work_dir).resolve(), groups, tools, boolean('LUCIDSHAPE_READ_ONLY'),
                   resolved, boolean('LUCIDSHAPE_ALLOW_SOURCE_WRITES'))

    @property
    def enabled_tools(self):
        names = (set(self.tools) if self.tools else set().union(*(GROUPS[g] for g in self.toolsets))) | GROUPS['core']
        return names & READ_ONLY if self.read_only else names

    def check_path(self, value):
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError('Input paths must be absolute')
        path = path.resolve(strict=True)
        if self.allowed_roots and not any(path.is_relative_to(root) for root in (*self.allowed_roots, self.work_dir)):
            raise PermissionError('Input or dependency is outside the configured allowed directories')
        return path

    def authorize(self, name, arguments):
        if name not in self.enabled_tools and name not in {operation(n) for n in self.enabled_tools}:
            raise PermissionError('Tool is disabled by server policy')
        if name == 'catia_show_all' and not self.allow_source_writes:
            raise PermissionError('catia_show_all can save the source; enable LUCIDSHAPE_ALLOW_SOURCE_WRITES explicitly')
        for key, value in arguments.items():
            if value is not None and (key.endswith('_path') or key == 'file_path'):
                self.check_path(value)

    def describe(self):
        return {'read_only': self.read_only, 'toolsets': list(self.toolsets),
                'enabled_tools': sorted(self.enabled_tools), 'tool_count': len(self.enabled_tools),
                'allowed_roots': [str(p) for p in self.allowed_roots],
                'path_scope': 'restricted' if self.allowed_roots else 'trusted local user files',
                'allow_source_writes': self.allow_source_writes,
                'scope': 'Protects source files and CAD mutations; private job reports still write to work_dir; not a native-code sandbox'}

    def environment(self):
        """Pin every policy value so clients do not accidentally inherit privileges."""
        return {'LUCIDSHAPE_TOOLSETS': ','.join(self.toolsets),
                'LUCIDSHAPE_TOOLS': ','.join(self.tools),
                'LUCIDSHAPE_READ_ONLY': str(self.read_only).lower(),
                'LUCIDSHAPE_ALLOWED_ROOTS': json.dumps([str(p) for p in self.allowed_roots], ensure_ascii=True),
                'LUCIDSHAPE_ALLOW_SOURCE_WRITES': str(self.allow_source_writes).lower()}


def check_input_path(path):
    policy = ACTIVE_POLICY.get()
    return policy.check_path(path) if policy else Path(path).resolve(strict=True)


def add_policy_arguments(parser):
    parser.add_argument('--toolsets', help='Comma-separated groups; use all to include experimental tools')
    parser.add_argument('--tools', help='Comma-separated tool names; core tools remain available')
    parser.add_argument('--read-only', action='store_true', default=None, help='Expose only source-preserving queries')
    parser.add_argument('--allow-root', action='append', help='Allowed input directory; repeat for multiple roots')
    parser.add_argument('--allow-source-writes', action='store_true', default=None,
                        help='Explicitly permit the CATIA visibility tool to save source documents')


def apply_policy_arguments(args):
    for option, key in (('toolsets', 'LUCIDSHAPE_TOOLSETS'), ('tools', 'LUCIDSHAPE_TOOLS'),
                        ('read_only', 'LUCIDSHAPE_READ_ONLY'), ('allow_root', 'LUCIDSHAPE_ALLOWED_ROOTS'),
                        ('allow_source_writes', 'LUCIDSHAPE_ALLOW_SOURCE_WRITES')):
        value = getattr(args, option, None)
        if value is not None:
            os.environ[key] = json.dumps(value) if isinstance(value, (bool, list)) else value
