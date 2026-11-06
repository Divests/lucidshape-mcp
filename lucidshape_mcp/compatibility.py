"""Deprecated public names, isolated from the project-independent implementation."""
TOOL_ALIASES = {
    'preflight_s9_optics':'preflight_optical_scene',
    'trace_s9_paths':'trace_optical_paths',
    'trace_s9_gpu':'trace_optical_gpu',
    'trace_s9_gpu_control':'trace_optical_gpu_control',
    'merge_s9_gpu_batches':'merge_optical_batches',
    'replay_s9_boundary_paths':'replay_optical_boundary_paths',
    'repair_s9_main_lens_mesh':'repair_optical_mesh',
    'prepare_inner_lens_regions':'prepare_optical_regions',
}
PARAMETER_ALIASES = {'scene_path':'requirements_path', 'rays_per_source':'rays_per_led',
                     'normal_policy':'pc_normal_policy'}


def operation(name):
    return TOOL_ALIASES.get(name,name)
