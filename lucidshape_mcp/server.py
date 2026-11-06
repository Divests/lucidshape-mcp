"""MCP entry point using the official Python SDK, v1.x."""
import os
from contextlib import asynccontextmanager
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .engine import BatchEngine
from .studio import open_file as open_studio_file
from .live_studio import live_state,inspect_current,import_step
from .dde_session import DDESession
from .tool_api import registrar
from .studio_queries import get_api_catalog, inspect_objects, get_simulation_settings
from .config import executable_path, work_directory


def create_server(engine: BatchEngine) -> FastMCP:
    live_session=DDESession(engine.root)
    @asynccontextmanager
    async def lifespan(app):
        try:
            yield {}
        finally:
            await live_session.close()
            await engine.close()
    server = FastMCP("LucidShape", lifespan=lifespan, instructions="Runs fixed CATIA and LucidShape tasks through MCP. CATIA tools attach to running CATIA via COM; catia_show_all requires explicit source-write policy. Batch jobs return job_id; poll get_job, use list_jobs for summaries and cancel_job for owner-mediated cancellation. Policy selects registered tools and allowed input roots. Experimental scene tools require explicit opt-in. For existing Studio, use studio_get_live_state, studio_inspect_current and studio_import_step (direct results). Read-only extensions studio_get_api_catalog, studio_inspect_objects and studio_get_simulation_settings return direct versioned query receipts; unavailable is not a successful data query. These tools share a persistent native DDE session, verify the executable and pin PID; no application is launched or closed. Prefer studio_import_step to import geometry into an already opened Studio. The legacy studio_open_file can launch an instance and provides no later responsiveness guarantee. No computer-use, mouse or keyboard input is used. Live import must report status=succeeded with post-load receipts; never automatically resubmit unverified import commands.")

    tool = registrar(server, engine)

    @tool()
    def get_capabilities() -> dict:
        """Report executable configuration and available batch templates without starting LucidShape."""
        return engine.capabilities()

    @tool()
    def list_jobs(status: str | None = None, offset: int = 0, limit: int = 50) -> dict:
        """List bounded task summaries in this work directory, newest first. Observation never rewrites another owner's state."""
        return engine.list_jobs(status, offset, limit)

    @tool()
    def cancel_job(job_id: str) -> dict:
        """Request cooperative cancellation from the owning service. Poll get_job for acknowledgement; never terminates CAD applications or arbitrary PIDs. Native application work may remain unverified after its helper stops."""
        return engine.cancel_job(job_id)

    @tool()
    async def studio_open_file(file_path: str, timeout_seconds: int = 120) -> dict:
        """Open a STEP or native .lue/.lug snapshot in a LucidStudio GUI instance using a fixed .lus startup script and Application.openFile. No mouse/keyboard or computer-use. Waits for a native API receipt proving loaded surface count and bounds; launch alone is unverified. May start a new Studio instance; leaves it open, including on failure. Does not merge into an existing document or run simulation. Returns the result directly, not a batch job_id."""
        if not engine.command:
            raise ValueError("Configure LUCIDSHAPE_EXE first")
        async with engine.native_session():
            return await open_studio_file(file_path, Path(engine.command[0]).parent / "LucidShape.exe", engine.root, timeout_seconds)

    @tool()
    async def studio_get_live_state(connect: bool = True) -> dict:
        """Read-only DDE connection probe for existing LucidStudio. Reports partner PID. connect=False reads only OS process metadata, without a DDE handshake or application command, for observation during a pending load. Does not start an application, open a file or issue GUI input."""
        if not connect:
            return await live_state(engine.root,live_session.request,connect)
        async with engine.native_session():
            return await live_state(engine.root,live_session.request,connect)

    @tool()
    async def studio_inspect_current(process_id: int | None = None, include_optics: bool = False) -> dict:
        """Inspect the current model in existing LucidStudio via native DDE/API. Returns model identity, geometry and PID. include_optics=True also lists emitter, sensor and ActorMaterial object counts and up to 100 names/types per group; counts alone do not certify correct optical assignments or real parameters. Does not start an application, open/modify/save models or run simulation. process_id pins the selected instance. Returns result directly."""
        async with engine.native_session():
            return await inspect_current(engine.root,process_id,live_session.request,include_optics)

    @tool()
    async def studio_get_api_catalog(process_id: int | None = None, type_name: str | None = None,
                                     filter_text: str = "", offset: int = 0, limit: int = 200,
                                     max_documentation_chars: int = 12000, timeout_seconds: int = 30) -> dict:
        """Read the existing Studio's native command/type help catalog, optionally including methods for a simple type_name. Filter and paginate entries. Help may omit inherited methods and uses incomplete (...) signatures; it does not authorize arbitrary calls. Returns a direct correlated result with executable SHA-256. Requires existing DDE; never launches Studio, creates a model or runs simulation."""
        async with engine.native_session():
            return await get_api_catalog(engine.root,process_id=process_id,type_name=type_name,filter_text=filter_text,
                offset=offset,limit=limit,max_documentation_chars=max_documentation_chars,
                timeout_seconds=timeout_seconds,transport=live_session.request)

    @tool()
    async def studio_inspect_objects(process_id: int | None = None, expected_model_name: str | None = None,
                                     object_type: str | None = None, selected_only: bool = False,
                                     offset: int = 0, limit: int = 100, timeout_seconds: int = 30) -> dict:
        """Read a bounded page of current-model references and direct material/exit-material/surface relationships for supported concrete classes. object_type: null, Shape, Surface, Geometry, EmitterMaterial, SensorMaterial or ActorMaterial. expected_model_name checks the returned name, normalizing path slashes only. Indices and names are not persistent unique IDs; pagination is a fresh observation. Unsupported relationships are not_queried, never assumed absent. Returns unavailable for no model or running simulation. Does not edit, select, open, save or simulate."""
        async with engine.native_session():
            return await inspect_objects(engine.root,process_id=process_id,expected_model_name=expected_model_name,
                object_type=object_type,selected_only=selected_only,offset=offset,limit=limit,
                timeout_seconds=timeout_seconds,transport=live_session.request)

    @tool()
    async def studio_get_simulation_settings(process_id: int | None = None, expected_model_name: str | None = None,
                                             timeout_seconds: int = 30) -> dict:
        """Read numeric Monte Carlo, light-mapping and multiprocessing settings from the existing current model. Uses and hashes the installed raytracemapping.hdo; unsupported mappings fail. Ray counts are individual rays, not GUI thousands. Returns raw numeric fields and mapping provenance. Wavelength arrays, filenames and other groups are omitted. Does not change parameters or run simulation; no model/busy/unavailable native settings are explicit unavailable results."""
        async with engine.native_session():
            return await get_simulation_settings(engine.root,process_id=process_id,expected_model_name=expected_model_name,
                timeout_seconds=timeout_seconds,transport=live_session.request)

    @tool()
    async def studio_import_step(cad_path: str, process_id: int | None = None, timeout_seconds: int = 180, observe_seconds: int = 15) -> dict:
        """Import a STEP snapshot as a new document in an EXISTING LucidStudio instance via native DDE/API. Never launches/closes Studio or edits an existing model; no computer-use or GUI input. Verifies PID, nonce, requested model identity, geometry, source hash and two later API roundtrips. Adds no optics and does not simulate. Returns result directly; only status=succeeded means verified within the stated observation period, not future stability. Unverified/timeouts may still be executing: do not automatically resubmit."""
        async with engine.native_session():
            return await import_step(engine.root,cad_path,process_id,timeout_seconds,observe_seconds,live_session.request)

    @tool()
    async def catia_get_state(timeout_seconds: int = 30) -> dict:
        """Submit a read-only CATIA Automation connection check. Requires running CATIA and pywin32. Poll get_job for open documents and active document. Does not launch or control CATIA through GUI input."""
        return await engine.submit("catia_get_state", timeout_seconds=timeout_seconds)

    @tool()
    async def catia_inspect_part(catia_path: str, timeout_seconds: int = 120) -> dict:
        """Read an ALREADY OPEN CATPart at the exact requested path through CATIA COM. Lists body/feature names, user parameters and product metadata, axis-system origins/directions, and assigned part/body material names. Records unavailable sections as errors rather than claiming absent data. No document is opened, activated, edited, saved or closed. Fixed read-only VBScript via SystemService handles Automation output arrays. Returns job_id; poll get_job."""
        return await engine.submit("catia_inspect_part",catia_path=catia_path,timeout_seconds=timeout_seconds)

    @tool()
    async def catia_export_step(catia_path: str, timeout_seconds: int = 300, include_unsaved: bool = False) -> dict:
        """Export a CATPart to STEP through running CATIA, keeping a private disk backup. Reuses the document at the exact source path. Default refuses unsaved changes; include_unsaved=True exports its current in-memory geometry without saving or closing it, with explicit provenance in the result. Otherwise opens the snapshot and closes only a proven new, clean private document. Known byte-identical open copies cause a refusal to avoid CATIA identity collisions. Does not save the source. Poll get_job for step_file, source hash, body information and document_mode."""
        return await engine.submit("catia_export_step", catia_path=catia_path, timeout_seconds=timeout_seconds,include_unsaved=include_unsaved)

    @tool()
    async def catia_show_all(catia_path: str, timeout_seconds: int = 300, save_as_source: bool = False) -> dict:
        """Show ALL searchable hidden items in a CATPart using CATIA COM, preserving a byte-for-byte disk backup first. Opens/reuses the document and leaves it open; verifies zero hidden items. Default saves only a previously clean document whose FullName matches the source. For a dirty document or a CATIA identity alias, save_as_source=True requires explicit user authorization to SaveAs the whole current document (including all its unsaved changes) to the requested source; alerts are suppressed only for that authorized overwrite and restored afterward. Returns job_id; poll get_job."""
        return await engine.submit("catia_show_all",catia_path=catia_path,timeout_seconds=timeout_seconds,save_as_source=save_as_source)

    @tool()
    async def inspect_cad_geometry(cad_path: str, max_objects: int = 100, timeout_seconds: int = 300) -> dict:
        """Read a STEP or STL snapshot in an isolated native batch job and report native surface counts, bounds, object names/types and candidate optical names. An STL mesh can be one native surface with many triangles; these counts are not CAD face counts. Does not save/reload LUE, change Studio, assign materials or trace rays. Returns job_id; poll get_job."""
        return await engine.submit("inspect_cad_geometry",cad_path=cad_path,max_objects=max_objects,timeout_seconds=timeout_seconds)

    @tool()
    async def preflight_optical_scene(scene_path: str, region_manifest_path: str, timeout_seconds: int = 600) -> dict:
        """Validate configured region/material bindings and source frames in an isolated native batch. scene_path accepts optical-scene/1 JSON or the strict historical requirements adapter. Supports 1..64 source frames sharing one inspected non-spectral RAY, named operating modes and a configured Type-B sensor. Performs diagnostic axial paths only; traversal absorption is intentionally absent. No regulatory or mounting certification. Poll get_job."""
        return await engine.submit('preflight_optical_scene',requirements_path=scene_path,region_manifest_path=region_manifest_path,timeout_seconds=timeout_seconds)

    @tool()
    async def validate_traversal_absorption(timeout_seconds: int = 120) -> dict:
        """Run a FIXED analytic n=1.5 slab fixture with 20,000 native RayPathFinder trials and one TIR case. Checks whether stochastic Fresnel paths support post-weighting exactly once per completed entry/exit, independent of internal reflections. This is backend validation only; never a project simulation or regulatory result. Returns job_id; poll get_job."""
        return await engine.submit('validate_traversal_absorption',timeout_seconds=timeout_seconds)

    @tool()
    async def trace_optical_paths(scene_path: str, region_manifest_path: str, rays_per_source: int = 2000, sampling_seed: int = 20261020, timeout_seconds: int = 900) -> dict:
        """Trace sampled CPU stochastic paths for configured sources and materials. Per-material loss is applied once per complete air-medium-air visit, retaining native Fresnel/TIR. Requires distinct refractive indices between distinct media; direct touching-medium transitions fail. Emits a weighted RAY for each named operating mode. 100..10000 samples per source. New general scene combinations require native validation and convergence checks. No CAD window changes. Poll get_job."""
        return await engine.submit('trace_optical_paths',requirements_path=scene_path,region_manifest_path=region_manifest_path,rays=rays_per_source,sampling_seed=sampling_seed,timeout_seconds=timeout_seconds)

    @tool()
    async def merge_optical_batches(batch_report_path: str, timeout_seconds: int = 300) -> dict:
        """Validate contiguous disjoint full source coverage, matching sources/materials/modes/sensor, verified GPU logs and immutable LID hashes before summing each operating mode. Rejects sampled, overlapping, missing or mixed-configuration batches. Historical low/additional batches retain their explicit low/high composition through compatibility handling. Poll get_job."""
        return await engine.submit('merge_optical_batches',batch_report_path=batch_report_path,timeout_seconds=timeout_seconds)

    @tool()
    async def trace_optical_gpu(scene_path: str, region_manifest_path: str, rays_per_source: int = 10000, source_start: int = 0, sample: bool = False, sampling_seed: int = 20261021, cpu_reference: bool = False, timeout_seconds: int = 1800) -> dict:
        """Disabled fixed-traversal-loss GPU candidate: outgoing-air mixtures also attenuate entry Fresnel reflections. Use CPU path histories for the requested loss model or explicitly choose trace_optical_gpu_control. Renaming/generalization does not validate this material approximation."""
        return await engine.submit('trace_optical_gpu',requirements_path=scene_path,region_manifest_path=region_manifest_path,rays=rays_per_source,source_start=source_start,sample=sample,sampling_seed=sampling_seed,cpu_reference=cpu_reference,timeout_seconds=timeout_seconds)

    @tool()
    async def trace_optical_gpu_control(scene_path: str, region_manifest_path: str, rays_per_source: int = 10000, source_start: int = 0, sample: bool = False, sampling_seed: int = 20261021, cpu_reference: bool = False, timeout_seconds: int = 1800) -> dict:
        """Run a zero-extra-absorption GPU control for the configured scene. Explicitly omits configured per-traversal losses while retaining indices, Fresnel/TIR, reflectances, sources and operating modes. One inspected equal-weight non-spectral RAY is shared by all sources; arbitrary orthonormal frames and mode membership are supported. cpu_reference selects mode 20 instead of GPU mode 13. Results remain a different control model with no regulatory certification. Poll get_job."""
        return await engine.submit('trace_optical_gpu',requirements_path=scene_path,region_manifest_path=region_manifest_path,rays=rays_per_source,source_start=source_start,sample=sample,sampling_seed=sampling_seed,cpu_reference=cpu_reference,timeout_seconds=timeout_seconds,zero_extra_absorption_control=True)

    @tool()
    async def replay_optical_boundary_paths(scene_path: str, region_manifest_path: str, failure_path: str, start_segment_indices: list[int] | None = None, replay_attempts: int = 8, include_neighbor_directions: bool = False, timeout_seconds: int = 300, boundary_region: str = 'main_lens', coordinate_scale: float = 1, region_labels: list[str] | None = None) -> dict:
        """Replay saved failed-path checkpoints in one explicitly selected refractive region and optional region_labels such as its coated surfaces. General scenes use boundary_region as a region label; historical inputs retain main_lens/inner_lens names. Optional coordinate scaling is diagnostic only. Starting medium requires independent geometry validation. New stochastic traces do not reproduce previous Fresnel choices. No photometric acceptance. Poll get_job."""
        return await engine.submit('replay_optical_boundary_paths',requirements_path=scene_path,
            region_manifest_path=region_manifest_path,failure_path=failure_path,
            start_segment_indices=start_segment_indices,replay_attempts=replay_attempts,include_neighbor_directions=include_neighbor_directions,
            boundary_region=boundary_region,coordinate_scale=coordinate_scale,timeout_seconds=timeout_seconds,region_labels=region_labels)

    @tool()
    async def replay_rayfile_batch(rayfile_path: str, source_start: int, ray_count: int, flux_scale: float = 1, pitch_deg: float = 0, timeout_seconds: int = 300, sensor_config: dict | None = None) -> dict:
        """Replay a contiguous 1..4000000 record range of nonspectral escaped/output RAY into a Type-A far-field sensor using native CPU mode 20. No lamp geometry or material retrace, no GPU claim. Source frame +Z forward, +X left, +Y up; negative pitch tilts down. Stage only the range, scale absolute weights and rotate positions/directions explicitly, preserve original. sensor_config optionally sets bounds_deg, grid_uv and a right-handed frame; omitted settings retain H[-60,60],V[-20.6,19.4],0.1 deg. Rays are transformed into that basis before pitch; Type-A horizontal is negative local X. Validate every cell against independent binning. Caller must check coverage across batches. Does not establish original launch count, materials, aim or regulatory compliance. Poll get_job."""
        return await engine.submit('replay_rayfile_batch',rayfile_path=rayfile_path,source_start=source_start,rays=ray_count,flux_scale=flux_scale,pitch_deg=pitch_deg,timeout_seconds=timeout_seconds,sensor_config=sensor_config)

    @tool()
    async def bin_rayfile(rayfile_path: str, timeout_seconds: int = 120, angular_resolution_deg: float = .1, sensor_config: dict | None = None) -> dict:
        """Bin escaped weighted non-spectral rays once into a Type-B far-field sensor. sensor_config may explicitly set bounds_deg, grid_uv and right-handed x/y/z frame; copy the scene sensor for CPU outputs. Omitting it retains the historical X-forward default. No geometry retracing, flux rescaling or smoothing. Poll get_job."""
        return await engine.submit('bin_rayfile',rayfile_path=rayfile_path,timeout_seconds=timeout_seconds,angular_resolution_deg=angular_resolution_deg,sensor_config=sensor_config)

    @tool()
    async def inspect_optical_region_topology(region_manifest_path: str, probe_points_mm: list[list[float]] | None = None, additional_solid_regions: list[str] | None = None, timeout_seconds: int = 300, local_boundary_checks: bool = False, reference_mesh_path: str | None = None, reference_triangle_indices: list[int] | None = None, region_labels: list[str] | None = None, region_roles: dict[str,str] | None = None, target_region: str | None = None) -> dict:
        """Inspect selected STEP region_labels using an isolated OCCT worker. Optional region_roles maps reference/coating/transmitting regions for partition diagnostics. Local boundary checks use an explicit target_region and bounded probe points; optional reference STL checks remain local diagnostics, not global certification. No source geometry changes. Poll get_job."""
        return await engine.submit('inspect_optical_region_topology',region_manifest_path=region_manifest_path,probe_points_mm=probe_points_mm,additional_solid_regions=additional_solid_regions,timeout_seconds=timeout_seconds,local_boundary_checks=local_boundary_checks,reference_mesh_path=reference_mesh_path,reference_triangle_indices=reference_triangle_indices,region_labels=region_labels,region_roles=region_roles,target_region=target_region)

    @tool()
    async def repair_optical_mesh(region_manifest_path: str, boundary_report_path: str, max_vertex_displacement_mm: float = .00005, max_normal_change_deg: float = .05, repair_radius_mm: float = 5, repair_method: str = 'split_intersections', timeout_seconds: int = 600, target_region: str | None = None) -> dict:
        """Create a conservative derived STL for target_region using a completed local topology/intersection report tied to the same STEP and mesh hashes. Bounds limit displacement, normal change and repair radius. Acceptance remains separate from completion; rejected candidates are not promoted. Native revalidation and optical checkpoint checks remain required. Poll get_job."""
        if target_region is None:
            raise ValueError('Specify target_region explicitly for generic mesh repair')
        return await engine.submit('repair_optical_mesh',region_manifest_path=region_manifest_path,
            boundary_report_path=boundary_report_path,max_vertex_displacement_mm=max_vertex_displacement_mm,
            max_normal_change_deg=max_normal_change_deg,repair_radius_mm=repair_radius_mm,repair_method=repair_method,timeout_seconds=timeout_seconds,target_region=target_region)

    @tool()
    async def prepare_optical_regions(region_manifest_path: str, geometry_representation: str = 'step', mesh_deflection_mm: float = .001, mesh_angle_deg: float = .05, include_main_lens: bool = False, timeout_seconds: int = 300, normal_policy: str = 'analytic', region_roles: dict[str,str] | None = None, additional_solid_regions: list[str] | None = None) -> dict:
        """Prepare a configured reference/coating/transmitting partition while preserving source-face roles. region_roles supplies actual manifest labels. STEP, STL and analytic-normal LUG outputs are supported; normal_policy controls the transmitting partition. Additional closed solid regions require normal_mesh. Sources stay unchanged; derived geometry requires native import and optical validation. include_main_lens is a deprecated compatibility switch. Poll get_job."""
        return await engine.submit('prepare_optical_regions',region_manifest_path=region_manifest_path,
            geometry_representation=geometry_representation,mesh_deflection_mm=mesh_deflection_mm,
            mesh_angle_deg=mesh_angle_deg,include_main_lens=include_main_lens,pc_normal_policy=normal_policy,timeout_seconds=timeout_seconds,region_roles=region_roles,additional_solid_regions=additional_solid_regions)

    @tool()
    def cancel_geometry_job(job_record_path: str) -> dict:
        """Compatibility alias for owner-mediated cancellation of a geometry job in the configured work directory. No WMI/PID termination. Poll get_job for acknowledgement."""
        path = Path(job_record_path).resolve(strict=True)
        directory = engine._directory(path.parent.name)
        if path != directory / 'job.json':
            raise ValueError('Expected a job.json inside the configured work directory')
        record = engine.get_job(path.parent.name)
        from .compatibility import operation
        if operation(record['template']) not in {'inspect_optical_region_topology', 'prepare_optical_regions', 'repair_optical_mesh'}:
            raise ValueError('Not a geometry job')
        return engine.cancel_job(path.parent.name)

    @tool()
    async def import_cad_geometry(cad_path: str, timeout_seconds: int = 300) -> dict:
        """Import a multiple-surface standalone STEP .stp/.step into native LucidShape .lue. Preserves imported coordinates and adds no source, material or sensor. Verifies root surface count and bounds after native-file roundtrip. Returns a job_id; get_job.result.model_file is the ready-to-open geometry file. Does not open a LucidStudio GUI window or run simulation."""
        return await engine.submit("import_cad_geometry", cad_path=cad_path, timeout_seconds=timeout_seconds)

    @tool()
    async def catia_create_plane_mirror(width_mm: float = 100, height_mm: float = 80, tilt_deg: float = 0, timeout_seconds: int = 90) -> dict:
        """Create a NEW rectangular surface CATPart via CATIA COM and export STEP into a private job directory. Width/height: 1..1000 mm; tilt: -15..15 deg about Y. Requires running CATIA with surface creation and STEP export support. Existing documents are not edited or closed. Poll get_job for native_part, step_file and expected bounding box. On timeout a partially created new CATIA document may remain."""
        return await engine.submit("catia_create_plane_mirror", width_mm=width_mm, height_mm=height_mm, tilt_deg=tilt_deg, timeout_seconds=timeout_seconds)

    @tool()
    async def catia_convert_step_to_stl(cad_path: str, timeout_seconds: int = 600) -> dict:
        """Use the running CATIA's native STEP importer and STL exporter on a private STEP snapshot. Opens exactly one new private document, saves it only inside the job, preserves all preexisting documents and restores the active document. Closes only the proven clean private import. Uses current tessellation defaults; no mesh/optical accuracy guarantee. No GUI input, no original CATPart edits. Returns job_id; poll get_job."""
        return await engine.submit('catia_convert_step_to_stl',cad_path=cad_path,timeout_seconds=timeout_seconds)

    @tool()
    async def catia_export_body_stl(catia_path: str, body_name: str, include_unsaved: bool = False, timeout_seconds: int = 600) -> dict:
        """Copy an explicitly named body from the exact already-open CATPart to a new private Part and export STL. Restores source selection and preexisting documents; saves/closes only the new clean private Part. include_unsaved must be explicitly true for a dirty source. Native STL defaults require independent mesh/optical validation. No GUI input. Returns job_id."""
        return await engine.submit('catia_export_body_stl',catia_path=catia_path,body_name=body_name,include_unsaved=include_unsaved,timeout_seconds=timeout_seconds)

    @tool()
    async def prepare_cad_mirror_scene(cad_path: str, flux_lm: float = 1000, reflection_factor: float = 1, source_z_mm: float = 100, timeout_seconds: int = 90) -> dict:
        """Import one standalone STEP .stp/.step root surface and prepare a fixed mirror experiment. Source is at (0,0,source_z_mm), emitting parallel to -Z. Adds specular material and a 160x80 far-field sensor over [-40,40]x[-20,20] degrees. Returns job_id; get_job.result.scene_file can be passed to inspect_model and simulate_model. Does not launch a GUI or run simulation itself."""
        return await engine.submit("prepare_cad_mirror_scene", cad_path=cad_path, flux_lm=flux_lm, reflection_factor=reflection_factor, source_z_mm=source_z_mm, timeout_seconds=timeout_seconds)

    @tool()
    async def health_check(timeout_seconds: int = 30, check_gpu: bool = False, gpu_material_probe: bool = False, gpu_interface_probe: bool = False) -> dict:
        """Submit a fixed engine smoke test. Default saves Point3. check_gpu runs a 10000-ray GPU mirror fixture. gpu_material_probe=True selects 34 CPU/GPU material cases (131072 rays/case), including backward Fresnel, plus 1024-ray CPU/GPU history controls. Alternatively gpu_interface_probe=True selects 40 directional RefractiveN and bulk-absorption controls (65536 rays/case). Neither interface nor process success certifies project physics. No user-model load or Studio/CATIA connection. Poll get_job."""
        if gpu_interface_probe:
            if not check_gpu or gpu_material_probe:
                raise ValueError('GPU interface probe requires check_gpu=True and gpu_material_probe=False')
            return await engine.submit('probe_gpu_interfaces',timeout_seconds=timeout_seconds)
        if gpu_material_probe and not check_gpu:
            raise ValueError('GPU material probe requires check_gpu=True')
        return await engine.submit("validate_gpu_loss" if gpu_material_probe else ("gpu_probe" if check_gpu else "healthcheck"), timeout_seconds=timeout_seconds)

    @tool()
    async def submit_demo_simulation(rays: int = 10000, flux_lm: float = 1000, timeout_seconds: int = 120) -> dict:
        """Submit a fixed point-source demo with a far-field candela sensor. Saves demo.lid and demo.lue; this is not a vehicle lighting design. Returns a job_id."""
        return await engine.submit("demo_simulation", rays, flux_lm, timeout_seconds)

    @tool()
    async def inspect_rayfile(rayfile_path: str, timeout_seconds: int = 120) -> dict:
        """Read/check a .ray light-source file through native RayFile APIs in an isolated batch job. Preserves a byte snapshot, identifies format and spectral flag, and counts/checks all rays. Does not edit the original, change the Studio model or run an optical simulation. Poll get_job and read_job_log for native diagnostics including flux information."""
        return await engine.submit("inspect_rayfile",rayfile_path=rayfile_path,timeout_seconds=timeout_seconds)

    @tool()
    async def inspect_model(model_path: str, max_objects: int = 500, timeout_seconds: int = 60) -> dict:
        """Inspect a saved native .lue/.lug model by absolute path. Snapshots the model and referenced files into the job directory. Poll get_job for object names/types, counts and emitter/sensor/geometry/material groups. Does not inspect the open GUI document."""
        return await engine.submit("inspect_model", model_path=model_path, max_objects=max_objects, timeout_seconds=timeout_seconds)

    @tool()
    async def simulate_model(model_path: str, rays: int = 10000, intensity_threshold: float = 0.1, recursion_limit: int = 16, timeout_seconds: int = 120) -> dict:
        """Trace a saved .lue/.lug model in a private file snapshot. Requires emitters and Candela/Lux/Lumen/Cdm2 sensors. Exports each sensor to a separate .lid; does not modify source files or use GUI simulation settings. Poll get_job for results. Missing external files fail before launch."""
        return await engine.submit("simulate_model", model_path=model_path, rays=rays, intensity_threshold=intensity_threshold, recursion_limit=recursion_limit, timeout_seconds=timeout_seconds)

    @tool()
    async def export_ies(lid_path: str, timeout_seconds: int = 120) -> dict:
        """Export a saved 2D non-spectral CANDELA .lid to IES using native cloneData/writeFile, without angle conversion or resampling. Snapshots the input, reopens the IES, compares grid directions and all intensities, and independently validates the file. Does not run simulation or certify regulatory compliance. Returns job_id; poll get_job for a verified IES path. Lux, lumen, spectral and 4D input are refused."""
        return await engine.submit("export_ies",lid_path=lid_path,timeout_seconds=timeout_seconds)

    @tool()
    async def analyze_lid(lid_path: str, region_deg: list[float] | None = None, timeout_seconds: int = 60) -> dict:
        """Analyze one saved .lid or .ies by absolute path using LucidShape. Supports 2D non-spectral candela data. Returns peak cd and angular position, grid, angular range, and flux within that range (not necessarily all emitted flux). Optional region_deg=[u_min,u_max,v_min,v_max] must lie within the data range. Poll get_job for structured results."""
        return await engine.submit("analyze_lid", lid_path=lid_path, region_deg=region_deg, timeout_seconds=timeout_seconds)

    @tool()
    def get_job(job_id: str) -> dict:
        """Read task status, input snapshot manifest, structured result (when available), and verified local artifact paths."""
        return engine.get_job(job_id)

    @tool()
    async def compare_photometry(reference_path: str, candidate_path: str, timeout_seconds: int = 120) -> dict:
        """Compare two saved LID/IES files in a private native job. Requires 2D non-spectral candela, matching angle type and actual sample directions; no implicit interpolation. Returns signed candidate-reference metrics, domain flux/peak changes and a difference CSV (not a physical IES). Relative errors at zero reference are undefined. Up to one million cells. Does not simulate or modify source files. Poll get_job."""
        return await engine.submit_extension("compare_photometry",reference_path=reference_path,candidate_path=candidate_path,timeout_seconds=timeout_seconds)

    @tool()
    async def inspect_optical_parameters(model_path: str, max_objects: int = 200, timeout_seconds: int = 60) -> dict:
        """Inspect a saved .lue/.lug and its dependency snapshots, not the open Studio document. Decode supported PointLight coordinates/efficacy and non-spectral LambertEmitter flux/angles/medium; partial Medium fields, surface material relationships and sensor metadata. Unsupported types/profiles stay explicit. max_objects limits each group. Does not simulate. Poll get_job."""
        return await engine.submit_extension("inspect_optical_parameters",model_path=model_path,max_objects=max_objects,timeout_seconds=timeout_seconds)

    @tool()
    async def set_emitter_parameters(model_path: str, source_name: str, flux_lm: float | None = None,
                                     half_angle_deg: float | None = None, trim_angle_deg: float | None = None,
                                     timeout_seconds: int = 120) -> dict:
        """Create a modified private model copy for exactly one named PointLight with a supported non-spectral LambertEmitter. Clone its emitter before rebinding, preserve full parameters and original shared material, then save/reload to verify the selected source. Specify one or more flux_lm, half_angle_deg (HWHM), trim_angle_deg; flux_lm refuses watt emitters. Original files and Studio stay untouched. Keep output model with its job-directory assets. This verifies selected-source parameters, not full CAD geometry equivalence. Poll get_job."""
        change={k:v for k,v in dict(flux_lm=flux_lm,half_angle_deg=half_angle_deg,trim_angle_deg=trim_angle_deg).items() if v is not None}
        return await engine.submit_extension("set_emitter_parameters",model_path=model_path,source_name=source_name,changes=change,timeout_seconds=timeout_seconds)

    @tool()
    async def run_parameter_sweep(model_path: str, source_name: str, cases: list[dict[str, float]],
                                   rays: int = 10000, seed: int = 12345, intensity_threshold: float = 0.1,
                                   recursion_limit: int = 16, timeout_seconds: int = 300) -> dict:
        """Run 1..16 independent CPU trials of a saved model using the same fixed seed. Each case contains only flux_lm, half_angle_deg and/or trim_angle_deg for one uniquely named PointLight/LambertEmitter. Reload the same baseline each case; preserve other source parameters and shared materials. Requires 1..64 non-spectral 2D CandelaSensors; exports per-case LID and summary CSV. At most 1M rays/case and 4M requested rays total. This is CPU (not GPU), not optimization or convergence/compliance certification. Poll get_job."""
        return await engine.submit_extension("run_parameter_sweep",model_path=model_path,source_name=source_name,cases=cases,
            rays=rays,seed=seed,intensity_threshold=intensity_threshold,recursion_limit=recursion_limit,timeout_seconds=timeout_seconds)

    @tool()
    def read_job_log(job_id: str, tail_bytes: int = 8000) -> dict:
        """Read up to 64000 trailing bytes of the batch log for diagnosis."""
        return engine.read_log(job_id, tail_bytes)

    return server


def main() -> None:
    executable = executable_path()
    command = (str(executable),) if executable else ()
    work = work_directory()
    server = create_server(BatchEngine(work, command))
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
