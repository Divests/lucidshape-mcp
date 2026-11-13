"""Isolated batch jobs; only fixed templates are exposed to MCP clients."""
from __future__ import annotations

import asyncio
import importlib.util
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import uuid
from contextlib import asynccontextmanager, AsyncExitStack

from .catia import parameters as catia_parameters
from .inputs import input_file, snapshot
from .results import read_report
from . import optical_scene, path_sampling, kernel_regions, boundary_paths, mesh_repair, gpu_trace, gpu_merge, ray_replay
from . import extensions, road_simulation
from .config import geometry_available, geometry_runtime_path
from .coordination import session_gate
from .job_control import JobControl, utc_now
from .policy import Policy
from .compatibility import operation


SUCCESS_MARKER = "LUCIDSHAPE_MCP_OK"
TEMPLATES = Path(__file__).with_name("templates")
CATIA_TEMPLATES = {"catia_get_state", "catia_create_plane_mirror", "catia_export_step", "catia_show_all", "catia_inspect_part", "catia_convert_step_to_stl", "catia_export_body_stl"}
KERNEL_TEMPLATES={'inspect_optical_region_topology','prepare_optical_regions','repair_optical_mesh'}
REPORT_TEMPLATES = {"inspect_model", "inspect_cad_geometry", "preflight_optical_scene", "validate_traversal_absorption", "trace_optical_paths", "replay_optical_boundary_paths", "bin_rayfile", "simulate_model", "analyze_lid", "export_ies", "prepare_cad_mirror_scene", "import_cad_geometry", "inspect_rayfile"} | CATIA_TEMPLATES | KERNEL_TEMPLATES
REPORT_TEMPLATES.add('gpu_probe')
REPORT_TEMPLATES.add('validate_gpu_loss')
REPORT_TEMPLATES.add('probe_gpu_interfaces')
REPORT_TEMPLATES.add('trace_optical_gpu')
REPORT_TEMPLATES.add('merge_optical_batches')
REPORT_TEMPLATES.add('replay_rayfile_batch')
REPORT_TEMPLATES.update(extensions.OPERATIONS)
REPORT_TEMPLATES.add('generate_road_views')
ERROR_LINE = re.compile(rb"^\s*(?:error\b|compile error\b|runtime error\b|do not understand\b|LUCIDSHAPE_MCP_FAILED:)", re.IGNORECASE)


def bounded_int(value: int, minimum: int, maximum: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}")
    return value


def bounded_number(value, minimum: float, maximum: float, name: str):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be finite and between {minimum} and {maximum}")
    return value


class BatchEngine(JobControl):
    def __init__(self, root: Path, command: tuple[str, ...] = ()):
        self.root = root.resolve()
        self.command = command
        self._catia_command = (sys.executable, "-m", "lucidshape_mcp.catia_worker")
        self._kernel_command=(sys.executable,'-m','lucidshape_mcp.kernel_worker')
        self._slots = asyncio.Semaphore(1)
        self._tasks: dict[str, asyncio.Task] = {}
        self.initialize_jobs()
        self.policy = Policy.from_env(self.root)

    @asynccontextmanager
    async def native_session(self):
        async with self._slots:
            async with session_gate():
                yield

    def capabilities(self) -> dict:
        executable = Path(self.command[0]) if self.command else None
        return {
            "backend": "lush-batch",
            "policy": self.policy.describe(),
            "executable": str(executable) if executable else None,
            "executable_exists": bool(executable and executable.is_file()),
            "license_status": "unverified; run health_check against your installation",
            "maximum_parallel_jobs": 1,
            "gpu_diagnostic": "Native GPU mode has historical fixture validation; current availability is unverified until health_check(check_gpu=True). S9 candidate DISABLED: backward Fresnel tests reject the fixed-traversal absorption mixture. Installed GPU returned no RayHistorySensor data in validation. A running Studio process does not prove a GPU simulation is running.",
            "scene_configuration": {"schema":"optical-scene/1", "max_sources":64, "max_modes":16,
                "source_model":"one inspected non-spectral RAY shared by explicit right-handed frames",
                "materials":["refractive with per-traversal loss", "ideal specular", "ideal Lambertian", "opaque absorber"],
                "cpu_limitations":"distinct indices for distinct media; air-separated visits only; no certified direct medium transitions",
                "geometry":"explicit region/material bindings; optional reference/coating/transmitting role map",
                "native_generalization_validation":"not independently verified on new target-engine scene combinations"},
            "GPU_fixed_traversal_loss_accepted": False,
            "S9_GPU_physics_accepted": False,
            "S9_GPU_control": "trace_optical_gpu_control runs only the explicitly user-authorized zero-extra-absorption control; retains Fresnel, never claims equivalence to approved fixed traversal loss.",
            "templates": sorted(REPORT_TEMPLATES | {"healthcheck", "demo_simulation"}),
            "optional_geometry_kernel": {"available":geometry_available(),
                "runtime_path":str(geometry_runtime_path()) if geometry_runtime_path() is not None else None,
                "purpose":"independent OCCT topology diagnosis and derived inner-lens snapshots; no GUI"},
            "photometry_export": "Native IES export from saved 2D non-spectral candela LID, with native readback and independent file checks; does not simulate or certify a lamp",
            "model_formats": [".lue", ".lug"],
            "analysis": "2D non-spectral candela LID or IES; native peak and flux within the data domain or a supplied region",
            "optical_extensions": {"tools": sorted(extensions.OPERATIONS), "scope": "saved files in isolated jobs, not the current Studio document",
                "verified_engine_sha256": extensions.VERIFIED_ENGINE, "sweep_backend": "CPU",
                "emitter_edit": "unique PointLight name; separate non-spectral LambertEmitter; preserve full constructor parameters and roundtrip selected source",
                "comparison": "same actual sampling coordinates and angle type; signed candidate-reference CSV; no implicit resampling"},
            "native_road_views": {
                "tool": "generate_road_views", "backend": "LucidShape native road and luminance APIs",
                "scope": "Saved candela photometry projected onto a flat road in an isolated job; not CAD retracing",
                "coordinates": "X forward, Y left, Z up; metres",
                "source_orientation": "Input photometry is already aimed; no additional pitch, scale or mirror",
                "verified_engine_sha256": road_simulation.VERIFIED_ENGINE,
                "live_studio_changed": False,
            },
            "native_report_views": {
                "tools": ["studio_export_photometry_view", "studio_generate_static_road_views"],
                "backend": "Task-owned LucidStudio process and fixed native API scripts",
                "source_files_changed": False, "uses_computer_input": False,
                "verification": "Native receipts and rendered artifact checks; availability must be checked by an actual export",
            },
            "simulation_sensor_types": ["CandelaSensor", "LuxSensor", "LumenSensor", "Cdm2Sensor"],
            "live_studio_session": True,
            "live_studio": {"backend":"native Windows DDE and Studio Application API",
                            "requires_running_application":True,"default_service":"LucidShape.exe",
                            "state_tool":"studio_get_live_state","inspect_tool":"studio_inspect_current","import_tool":"studio_import_step",
                            "read_only_query_tools":["studio_get_api_catalog","studio_inspect_objects","studio_get_simulation_settings"],
                            "query_verification":"Pinned PID, engine SHA-256, versioned nonce receipt and current-model identity; no automatic resubmission",
                            "connection_status":"unverified; call studio_get_live_state",
                            "verification":"PID, nonce, requested model identity, geometry and two later API responses"},
            "catia": {"backend": "COM Automation in an owned STA worker process",
                      "runtime_available": sys.platform == "win32" and importlib.util.find_spec("win32com") is not None,
                      "requires_running_application": True, "preserves_unrelated_documents": True,
                      "visibility_edit_tool":"catia_show_all","backup_before_visibility_edit":True,
                      "existing_part_export": "Reuses an open source document; include_unsaved explicitly exports its in-memory state without saving. Keeps an on-disk backup; closes only proven new clean private documents."},
            "cad_import": {"formats": [".stp", ".step"], "scene_template": "single CAD surface as mirror; collimated -Z source and far-field sensor"},
            "geometry_import": "Multiple-surface STEP to native .lue, without adding optical objects or transforms",
            "studio_open_file": "Starts LucidStudio with a fixed .lus script; native API receipt required to confirm geometry loading. Does not attach to or merge existing documents.",
        }

    def _directory(self, job_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise ValueError("Invalid job_id")
        directory = self.root / job_id
        if directory.resolve().parent != self.root:
            raise ValueError("Job directory must stay inside the configured work directory")
        return directory

    def read_log(self, job_id: str, tail_bytes: int = 8000) -> dict:
        bounded_int(tail_bytes, 1, 64000, "tail_bytes")
        self.get_job(job_id)
        log = self._directory(job_id) / "engine.log"
        if not log.is_file():
            return {"job_id": job_id, "text": "", "truncated": False}
        with log.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - tail_bytes))
            contents = stream.read(tail_bytes)
        return {"job_id": job_id, "text": contents.decode("utf-8", errors="replace"), "truncated": size > tail_bytes}

    async def submit_extension(self, operation: str, *, timeout_seconds: int = 120, **arguments) -> dict:
        if not self.command or not Path(self.command[0]).is_file():
            raise ValueError("Set LUCIDSHAPE_EXE to the installed lush.exe")
        bounded_int(timeout_seconds, 1, 3600, "timeout_seconds")
        if len(self._tasks) >= 16:
            raise ValueError("Job queue is full")
        parameters = extensions.validate(operation, arguments)
        binary_hash = hashlib.sha256(Path(self.command[0]).read_bytes()).hexdigest()
        if binary_hash != extensions.VERIFIED_ENGINE:
            raise ValueError("Optical extensions require the validated LS2.0 BIN64GPU engine profile")
        job_id = uuid.uuid4().hex
        directory = self._directory(job_id)
        directory.mkdir(parents=True)
        record = {"job_id": job_id, "template": operation, "parameters": parameters,
                  "status": "queued", "timeout_seconds": timeout_seconds, "artifacts": [], "engine_sha256": binary_hash}
        try:
            record["inputs"], expected = extensions.stage(directory, operation, parameters)
        except Exception as error:
            record.update(status="failed", error=f"Optical staging failed: {error}")
            self._save(record)
            raise
        return self._enqueue(record, expected)

    async def submit_road_views(self, *, timeout_seconds: int = 180, **arguments) -> dict:
        """Queue a fixed, source-preserving native road projection."""
        self.policy.authorize("generate_road_views", arguments)
        if not self.command or not Path(self.command[0]).is_file():
            raise ValueError("Set LUCIDSHAPE_EXE to the installed lush.exe")
        bounded_int(timeout_seconds, 1, 3600, "timeout_seconds")
        if len(self._tasks) >= 16:
            raise ValueError("Job queue is full")
        parameters = road_simulation.validate(arguments)
        executable = Path(self.command[0])
        binary_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
        if binary_hash != road_simulation.VERIFIED_ENGINE:
            raise ValueError("Native road views require the validated LS2.0 BIN64GPU engine profile")
        job_id = uuid.uuid4().hex
        directory = self._directory(job_id)
        directory.mkdir(parents=True)
        record = {"job_id": job_id, "template": "generate_road_views", "parameters": parameters,
                  "status": "queued", "timeout_seconds": timeout_seconds, "artifacts": [],
                  "engine_sha256": binary_hash}
        try:
            record["inputs"], expected = road_simulation.stage(directory, parameters, executable)
        except Exception as error:
            record.update(status="failed", error=f"Road-view staging failed: {error}")
            self._save(record)
            raise
        return self._enqueue(record, expected)

    async def submit(self, template: str, rays: int = 10000, flux_lm: float = 1000, timeout_seconds: int = 120,
                     *, model_path: str | None = None, lid_path: str | None = None,
                     max_objects: int = 500, region_deg: list[float] | None = None,
                     intensity_threshold: float = 0.1, recursion_limit: int = 16,
                     cad_path: str | None = None, reflection_factor: float = 1, source_z_mm: float = 100,
                     width_mm: float = 100, height_mm: float = 80, tilt_deg: float = 0,
                     catia_path: str | None = None, save_as_source: bool = False, include_unsaved: bool = False,
                     rayfile_path: str | None = None, requirements_path: str | None = None,
                     region_manifest_path: str | None = None, sampling_seed: int = 20261020,
                     probe_points_mm: list | None = None, geometry_representation: str = 'step',
                     mesh_deflection_mm: float = .001, mesh_angle_deg: float = .05,
                     additional_solid_regions: list | None = None, include_main_lens: bool = False,body_name: str | None = None,
                     local_boundary_checks: bool = False, failure_path: str | None = None,
                     start_segment_indices: list | None = None, replay_attempts: int = 8,
                     reference_mesh_path: str | None = None, boundary_report_path: str | None = None,
                     max_vertex_displacement_mm: float = .00005, max_normal_change_deg: float = .05,
                     repair_radius_mm: float = 5, repair_method: str = 'split_intersections',
                      include_neighbor_directions: bool = False, reference_triangle_indices: list | None = None,
                      boundary_region: str = 'main_lens', coordinate_scale: float = 1,
                       pc_normal_policy: str = 'analytic', angular_resolution_deg: float = .1,
                       source_start: int = 0, sample: bool = False, cpu_reference: bool = False,
                       batch_report_path: str | None = None, zero_extra_absorption_control: bool = False,
                       flux_scale: float = 1, pitch_deg: float = 0,
                       region_labels: list | None = None, region_roles: dict | None = None,
                       target_region: str | None = None, sensor_config: dict | None = None) -> dict:
        template = operation(template)
        is_catia = template in CATIA_TEMPLATES
        is_kernel=template in KERNEL_TEMPLATES
        if not is_catia and not is_kernel and (not self.command or not Path(self.command[0]).is_file()):
            raise ValueError("Set LUCIDSHAPE_EXE to the installed lush.exe before submitting jobs")
        if len(self._tasks) >= 16:
            raise ValueError("Job queue is full; wait for existing jobs to finish")
        bounded_int(timeout_seconds, 1, 3600, "timeout_seconds")
        source = None
        target_name = None
        replacements = {}
        if is_catia:
            parameters = catia_parameters(template, width_mm, height_mm, tilt_deg, catia_path=catia_path,save_as_source=save_as_source,include_unsaved=include_unsaved,cad_path=cad_path,body_name=body_name)
            if template in ("catia_export_step","catia_show_all"):
                source = Path(parameters["catia_path"])
                target_name = "input.CATPart"
                expected = ("engine-result.json", "export.stp") if template=="catia_export_step" else ("engine-result.json",)
            elif template=='catia_convert_step_to_stl':
                source=Path(parameters['cad_path'])
                target_name='input.stp'
                expected=('engine-result.json','converted.stl','imported.CATPart')
            elif template=='catia_export_body_stl':
                source=Path(parameters['catia_path'])
                target_name='input.CATPart'
                expected=('engine-result.json','converted.stl','body.CATPart')
            else:
                expected = ("engine-result.json",) if template in ("catia_get_state","catia_inspect_part") else ("engine-result.json", "mirror.CATPart", "mirror.stp")
        elif template=='repair_optical_mesh':
            parameters=mesh_repair.prepare(region_manifest_path,boundary_report_path,max_vertex_displacement_mm,
                max_normal_change_deg,repair_radius_mm,repair_method,target_region or 'main_lens')
            expected=('engine-result.json','candidate.stl')
        elif is_kernel:
            if not isinstance(include_main_lens,bool):
                raise ValueError('include_main_lens must be boolean')
            if include_main_lens:
                if template!='prepare_optical_regions' or geometry_representation!='normal_mesh':
                    raise ValueError('Main-lens preparation requires the analytic-normal mesh representation')
                additional_solid_regions=['main_lens']
            if template=='prepare_optical_regions' and additional_solid_regions and geometry_representation!='normal_mesh':
                raise ValueError('Additional solid preparation requires normal_mesh representation')
            if local_boundary_checks and template!='inspect_optical_region_topology':
                raise ValueError('Local boundary checks are read-only diagnosis')
            parameters=kernel_regions.prepare(region_manifest_path,probe_points_mm,additional_solid_regions,local_boundary_checks,reference_mesh_path,reference_triangle_indices,
                region_labels,region_roles,target_region)
            if template=='prepare_optical_regions':
                if not parameters['region_roles']:
                    raise ValueError('Partition preparation requires explicit reference/coating/transmitting region roles')
                if geometry_representation not in ('step','mesh','normal_mesh'):
                    raise ValueError('geometry_representation must be step, mesh or normal_mesh')
                bounded_number(mesh_deflection_mm,.0001,.01,'mesh_deflection_mm')
                bounded_number(mesh_angle_deg,.02,10 if geometry_representation=='normal_mesh' else .2,'mesh_angle_deg')
                if pc_normal_policy not in ('analytic','geometric') or (pc_normal_policy=='geometric' and geometry_representation!='normal_mesh'):
                    raise ValueError('PC geometric normals require the normal_mesh diagnostic representation')
                parameters.update(geometry_representation=geometry_representation,
                    mesh_deflection_mm=mesh_deflection_mm,mesh_angle_deg=mesh_angle_deg,include_main_lens=include_main_lens,
                    pc_normal_policy=pc_normal_policy)
            expected=('engine-result.json',)
        elif template == "healthcheck":
            parameters = {}
            expected = ("point.lug",)
        elif template == 'gpu_probe':
            parameters={'fixture_rays':10000,'requested_trace_mode':13,'S9_scene_loaded':False}
            expected=('engine-result.json',)
        elif template == 'validate_gpu_loss':
            parameters={'fixture_rays':131072,'S9_scene_loaded':False}
            expected=('engine-result.json',)
        elif template == 'probe_gpu_interfaces':
            parameters={'fixture_rays':65536,'S9_scene_loaded':False,'approved_materials_changed':False}
            expected=('engine-result.json',)
        elif template == "demo_simulation":
            bounded_int(rays, 100, 1000000, "rays")
            bounded_number(flux_lm, 0.001, 1000000, "flux_lm")
            parameters = {"rays": rays, "flux_lm": flux_lm}
            expected = ("demo.lid", "demo.lue")
        elif template=='replay_optical_boundary_paths':
            parameters=boundary_paths.prepare(requirements_path,region_manifest_path,failure_path,
                [0] if start_segment_indices is None else start_segment_indices,replay_attempts,include_neighbor_directions,
                boundary_region,coordinate_scale,region_labels)
            expected=('engine-result.json',)
        elif template=='merge_optical_batches':
            parameters=gpu_merge.prepare(batch_report_path)
            expected=('engine-result.json',*(name+'.lid' for name in parameters['expected_flux']))
        elif template=='trace_optical_gpu':
            parameters=gpu_trace.prepare(requirements_path,region_manifest_path,rays,source_start,sample,sampling_seed,cpu_reference,zero_extra_absorption_control)
            expected=('engine-result.json','coverage.json',*(name+'.lid' for name in optical_scene.gpu_groups(parameters)))
        elif template in ("preflight_optical_scene","trace_optical_paths"):
            parameters=optical_scene.prepare(requirements_path,region_manifest_path,include_rayfile_emitters=template!='trace_optical_paths')
            expected=("engine-result.json",)
            if template=='trace_optical_paths':
                bounded_int(rays,100,10000,'rays_per_led')
                bounded_int(sampling_seed,0,2147483647,'sampling_seed')
                parameters.update(rays_per_led=rays,sampling_seed=sampling_seed,
                    absorption_applied=True,diagnostic_only=parameters.get('coordinate_scale',1)!=1,include_rayfile_emitters=False)
                if not parameters.get('scene_schema_version'):
                    original=json.loads(Path(requirements_path).read_text(encoding='utf-8'))
                    if any(original[lens].get('absorption_fraction')!=.002 or original[lens].get('absorption_definition_confirmed') is not True for lens in ('inner_lens','main_lens')):
                        raise ValueError('Legacy history weighting requires confirmed 0.2 percent per complete traversal for each lens')
                optical_scene.validate_history_media(parameters)
                bounded_int(rays,100,min(10000,parameters['ray_count']),'rays_per_source')
                expected+=("terminal-rays.json","sampling.json")
        elif template == "validate_traversal_absorption":
            parameters={}
            expected=("engine-result.json",)
        elif template == "bin_rayfile":
            source=input_file(rayfile_path,{'.ray'})
            target_name='input.ray'
            if isinstance(angular_resolution_deg,bool) or angular_resolution_deg not in (.1,.5,1):
                raise ValueError('Binning angular resolution must be .1, .5 or 1 degree')
            grid=[round(120/angular_resolution_deg),round(30/angular_resolution_deg)]
            parameters={'rayfile_path':str(source),'angular_resolution_deg':angular_resolution_deg,'grid_uv':grid}
            if sensor_config is not None:
                settings=optical_scene.validate_sensor(sensor_config)
                parameters.update(sensor=settings,grid_uv=settings['grid_uv'],angular_resolution_deg=None)
            else:
                parameters['sensor']={**optical_scene.sensor({}),'grid_uv':grid}
            replacements.update(GRID_U=str(grid[0]),GRID_V=str(grid[1]))
            expected=('engine-result.json','photometry.lid')
        elif template == 'replay_rayfile_batch':
            parameters=ray_replay.prepare(rayfile_path,source_start,rays,flux_scale,pitch_deg,sensor_config)
            replacements['COUNT']=str(rays)
            expected=('engine-result.json','photometry.lid','photometry.ies','coverage.json')
        elif template == "prepare_cad_mirror_scene":
            source = input_file(cad_path, {".stp", ".step"})
            target_name = "input.stp"
            bounded_number(flux_lm, 0.001, 1000000, "flux_lm")
            bounded_number(reflection_factor, 0, 1, "reflection_factor")
            bounded_number(source_z_mm, 0.001, 1000000, "source_z_mm")
            parameters = {"cad_path": str(source), "flux_lm": flux_lm, "reflection_factor": reflection_factor, "source_z_mm": source_z_mm}
            expected = ("engine-result.json", "scene.lue")
            replacements.update(REFLECTION=format(reflection_factor, ".17g"), SOURCE_Z=format(source_z_mm, ".17g"))
        elif template == "inspect_cad_geometry":
            source = input_file(cad_path, {".stp", ".step", ".stl", ".lug"})
            target_name = 'input'+source.suffix.lower() if source.suffix.lower() in ('.stl','.lug') else 'input.stp'
            bounded_int(max_objects, 1, 5000, "max_objects")
            parameters = {"cad_path": str(source), "max_objects": max_objects}
            expected = ("engine-result.json",)
            replacements["MAX_OBJECTS"] = str(max_objects)
            replacements['CAD_FILENAME']=target_name
        elif template == "import_cad_geometry":
            source = input_file(cad_path, {".stp", ".step"})
            target_name = "input.stp"
            parameters = {"cad_path": str(source)}
            expected = ("engine-result.json", "geometry.lue")
        elif template == "inspect_rayfile":
            source = input_file(rayfile_path, {".ray"})
            target_name = "input.ray"
            parameters = {"rayfile_path": str(source)}
            expected = ("engine-result.json",)
        elif template in ("inspect_model", "simulate_model"):
            source = input_file(model_path, {".lue", ".lug"})
            target_name = "input.lue"
            parameters = {"model_path": str(source)}
            expected = ("engine-result.json",)
            if template == "inspect_model":
                bounded_int(max_objects, 1, 5000, "max_objects")
                parameters["max_objects"] = max_objects
                replacements["MAX_OBJECTS"] = str(max_objects)
            else:
                bounded_int(rays, 100, 1000000, "rays")
                bounded_int(recursion_limit, 1, 256, "recursion_limit")
                bounded_number(intensity_threshold, 0, 1, "intensity_threshold")
                parameters.update(rays=rays, intensity_threshold=intensity_threshold, recursion_limit=recursion_limit)
                replacements.update(INTENSITY=format(intensity_threshold, ".17g"), RECURSION=str(recursion_limit))
        elif template == "export_ies":
            source = input_file(lid_path, {".lid"})
            target_name = "input.lid"
            parameters = {"lid_path": str(source)}
            expected = ("engine-result.json", "photometry.ies")
        elif template == "analyze_lid":
            source = input_file(lid_path, {".lid", ".ies"})
            target_name = "input" + source.suffix.lower()
            replacements["DATA_FILENAME"] = target_name
            replacements["IS_IES"] = "1" if source.suffix.lower() == ".ies" else "0"
            if region_deg is not None:
                if not isinstance(region_deg, list) or len(region_deg) != 4:
                    raise ValueError("region_deg must be [u_min, u_max, v_min, v_max]")
                for value in region_deg:
                    bounded_number(value, -360, 360, "region_deg")
                if region_deg[0] >= region_deg[1] or region_deg[2] >= region_deg[3]:
                    raise ValueError("Region bounds must be increasing")
            parameters = {"lid_path": str(source), "region_deg": region_deg}
            expected = ("engine-result.json",)
            replacements["HAS_REGION"] = "1" if region_deg is not None else "0"
            replacements.update({key: format(value, ".17g") for key, value in zip(("U0", "U1", "V0", "V1"), region_deg or [0, 0, 0, 0])})
        else:
            raise ValueError("Unknown template")
        script = None
        if not is_catia and not is_kernel:
            script = (TEMPLATES / (template + ".do")).read_text(encoding="ascii")
            if template=='bin_rayfile':
                script=optical_scene.render(script,parameters)
            if template=='replay_rayfile_batch':
                script=ray_replay.render(script,parameters)
            if template=='merge_optical_batches':
                script=gpu_merge.render(script,parameters)
            if template=='trace_optical_gpu':
                script=gpu_trace.render(script,parameters,TEMPLATES)
            if template=='replay_optical_boundary_paths':
                script=boundary_paths.render(script,parameters)
            if template in ("preflight_optical_scene","trace_optical_paths"):
                if template=='trace_optical_paths':
                    setup=(TEMPLATES/'preflight_optical_scene.do').read_text(encoding='ascii').split('object data=LIDData')[0]
                    setup=optical_scene.condition_setup(setup,parameters.get('coordinate_scale',1))
                    script=script.replace('@@SCENE_SETUP@@',setup)
                    script=path_sampling.render(script,parameters)
                script=optical_scene.render(script,parameters)
            script = script.replace("@@RAYS@@", str(rays)).replace("@@FLUX@@", format(float(flux_lm), ".17g"))
            for key, value in replacements.items():
                script = script.replace(f"@@{key}@@", value)
            if "@@" in script:
                raise ValueError("Unresolved script parameter")
        job_id = uuid.uuid4().hex
        directory = self._directory(job_id)
        directory.mkdir(parents=True)
        if is_catia or is_kernel:
            (directory / "request.json").write_text(json.dumps({"action": template, "parameters": parameters}, allow_nan=False), encoding="utf-8")
        else:
            (directory / "job.do").write_text(script, encoding="ascii")
        if template=='validate_gpu_loss':
            import struct
            (directory/'tir.ray').write_bytes(struct.pack('<5fIf',0,0,0,0,1000,131072,-1)+
                struct.pack('<7f',0,0,-1,math.sqrt(3)/2,0,.5,1000/131072)*131072)
        if template=='probe_gpu_interfaces':
            import struct
            # Spread a tiny cone across detector cells to avoid legacy float-bin accumulation.
            payload=bytearray(struct.pack('<5fIf',0,0,0,0,1000,65536,-1))
            for iy in range(256):
                for ix in range(256):
                    dx=(ix+.5)/256*.0002-.0001;dy=(iy+.5)/256*.0002-.0001
                    payload.extend(struct.pack('<7f',0,0,0,dx,dy,math.sqrt(1-dx*dx-dy*dy),1000/65536))
            (directory/'interface.ray').write_bytes(payload)
        record = {"job_id": job_id, "template": template, "parameters": parameters, "status": "queued", "timeout_seconds": timeout_seconds, "artifacts": []}
        if template=='replay_rayfile_batch':
            try:
                record['inputs']=ray_replay.stage(directory,parameters)
            except Exception as error:
                record.update(status='failed',error=f'Ray replay staging failed: {error}')
                self._save(record)
                raise ValueError(record['error']) from error
        if template=='merge_optical_batches':
            record['inputs']=gpu_merge.stage(directory,parameters)
        if template=='trace_optical_gpu':
            try:
                record['inputs']=gpu_trace.stage(directory,parameters)
            except Exception as error:
                record.update(status='failed',error=f'GPU input staging failed: {error}')
                self._save(record)
                raise ValueError(record['error']) from error
        if source is not None:
            try:
                record["inputs"] = snapshot(source, directory, target_name)
            except Exception as error:
                record.update(status="failed", error=f"Input snapshot failed: {error}")
                self._save(record)
                raise ValueError(record["error"]) from error
        if template in ("preflight_optical_scene","trace_optical_paths","replay_optical_boundary_paths"):
            try:
                record['inputs']=boundary_paths.stage(directory,parameters) if template=='replay_optical_boundary_paths' else optical_scene.stage(directory,parameters)
                if template=='trace_optical_paths':
                    record['inputs'].append(path_sampling.stage_samples(directory,parameters))
            except Exception as error:
                record.update(status='failed',error=f'Optical input snapshot failed: {error}')
                self._save(record)
                raise ValueError(record['error']) from error
        if is_kernel:
            try:
                record['inputs']=mesh_repair.stage(directory,parameters) if template=='repair_optical_mesh' else kernel_regions.stage(directory,parameters)
            except Exception as error:
                record.update(status='failed',error=f'Topology snapshot failed: {error}')
                self._save(record)
                raise ValueError(record['error']) from error
        return self._enqueue(record, expected)

    async def _run(self, record: dict, expected: tuple[str, ...]) -> None:
        directory = self._directory(record["job_id"])
        process = None
        async with AsyncExitStack() as stack:
            try:
                record['phase'] = 'waiting_for_session'
                self._save(record)
                await stack.enter_async_context(self.native_session())
                if (record["template"] in extensions.OPERATIONS or record["template"] == "generate_road_views") and hashlib.sha256(Path(self.command[0]).read_bytes()).hexdigest() != record["engine_sha256"]:
                    raise ValueError("Native engine changed after submission")
                record["status"] = "running"
                record.update(phase='running', started_at=utc_now())
                self._save(record)
                environment = os.environ.copy()
                if record["template"] in CATIA_TEMPLATES:
                    command = (*self._catia_command, str(directory / "request.json"))
                elif record['template'] in KERNEL_TEMPLATES:
                    command=(*self._kernel_command,str(directory/'request.json'))
                else:
                    command = (*self.command, "-nopp", str(directory / "job.do"))
                environment["PATH"] = str(Path(command[0]).parent) + os.pathsep + environment.get("PATH", "")
                environment["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent) + os.pathsep + environment.get("PYTHONPATH", "")
                with (directory / "engine.log").open("wb") as log:
                    launch = asyncio.create_task(asyncio.create_subprocess_exec(*command, cwd=directory, env=environment,
                        stdin=asyncio.subprocess.DEVNULL, stdout=log, stderr=asyncio.subprocess.STDOUT))
                    try:
                        process = await asyncio.shield(launch)
                    except asyncio.CancelledError:
                        # Retain ownership even if cancellation arrives during process creation.
                        process = await launch
                        raise
                    try:
                        await asyncio.wait_for(process.wait(), record["timeout_seconds"])
                    except asyncio.TimeoutError:
                        if process.returncode is None:
                            process.kill()
                        await process.wait()
                        record.update(status="timed_out", error="Owned worker exceeded its deadline; CATIA may retain a partially created new document" if record["template"] in CATIA_TEMPLATES else "Owned batch interpreter exceeded its deadline")
                    else:
                        record["exit_code"] = process.returncode
                if record["status"] == "running":
                    record['phase'] = 'verifying'
                    self._save(record)
                    marker_found = False
                    reported_error = None
                    with (directory / "engine.log").open("rb") as stream:
                        for line in stream:
                            marker_found |= line.strip() == SUCCESS_MARKER.encode("ascii")
                            if ERROR_LINE.match(line) and reported_error is None:
                                reported_error = line[:500].decode("utf-8", errors="replace").strip()
                    missing = [name for name in expected if not (directory / name).is_file() or (directory / name).stat().st_size == 0]
                    if process.returncode == 0 and marker_found and not missing and reported_error is None:
                        if record["template"] in REPORT_TEMPLATES:
                            if record["template"] == "generate_road_views":
                                result, extra_files = road_simulation.finalize(directory, record["parameters"])
                            else:
                                reader = extensions.read_report if record["template"] in extensions.OPERATIONS else read_report
                                result, extra_files = reader(directory, record["template"], record["parameters"])
                            if record["template"] in extensions.OPERATIONS or record["template"] == "generate_road_views":
                                for item in record["inputs"]:
                                    if hashlib.sha256(Path(item["source"]).read_bytes()).hexdigest() != item["source_sha256"]:
                                        raise ValueError("Source or dependency changed during the isolated job")
                                result["source_files_unchanged"] = True
                                result["engine_sha256"] = record["engine_sha256"]
                            for name in extra_files:
                                if not (directory / name).is_file() or (directory / name).stat().st_size == 0:
                                    raise ValueError(f"Missing result artifact: {name}")
                            (directory / "result.json").write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
                            expected += ("result.json", *extra_files)
                            record["result"] = result
                        record["status"] = "succeeded"
                        record["artifacts"] = [{"name": name, "path": str(directory / name), "bytes": (directory / name).stat().st_size} for name in expected]
                    else:
                        record.update(status="failed", error=f"Batch verification failed: exit={process.returncode}, success_marker={marker_found}, missing_artifacts={missing}, engine_error={reported_error}; inspect engine.log")
            except asyncio.CancelledError:
                if process is not None and process.returncode is None:
                    process.kill()
                    await process.wait()
                record.update(status="cancelled", error=self._cancel_reasons.get(record['job_id'], "MCP server shut down"))
                raise
            except Exception as error:
                record.update(status="failed", error=f"{type(error).__name__}: {error}")
            finally:
                self._save(record)

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        monitors = list(self._monitors.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for monitor in monitors:
            monitor.cancel()
        if monitors:
            await asyncio.gather(*monitors, return_exceptions=True)
