"""Validate the small JSONStore reports and expose named MCP result fields."""
from __future__ import annotations

import json
import hashlib
import math
from pathlib import Path

from .inputs import decode_text
from .compatibility import operation
from . import optical_scene


def sequence(value, length=None):
    if not isinstance(value, list) or (length is not None and len(value) != length):
        raise ValueError("Unexpected engine report structure")
    return value


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Engine returned a non-finite or invalid number")
    return value


def count(value):
    number(value)
    if value < 0 or int(value) != value:
        raise ValueError("Invalid engine count")
    return int(value)


def string(value):
    if not isinstance(value, str):
        raise ValueError("Invalid engine string")
    return value


def objects(rows):
    return [{"name": string(sequence(row, 2)[0]), "type": string(row[1])} for row in sequence(rows)]


def read_report(directory: Path, template: str, parameters: dict) -> tuple[dict, list[str]]:
    template = operation(template)
    path = directory / "engine-result.json"
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Engine report is too large")
    raw = json.loads(decode_text(path.read_bytes())[0])
    if template in ("catia_get_state", "catia_create_plane_mirror", "catia_export_step", "catia_show_all", "catia_inspect_part", "catia_convert_step_to_stl", "catia_export_body_stl"):
        if not isinstance(raw, dict) or raw.get("backend") != "CATIA COM Automation":
            raise ValueError("Expected a CATIA worker report")
        if template=='catia_convert_step_to_stl':
            if raw.get('source_step')!=parameters['cad_path'] or raw.get('source_geometry_modified') is not False or raw.get('preexisting_documents_preserved') is not True:
                raise ValueError('CATIA conversion source or preservation scope differs')
            if raw.get('stl_file')!=str(directory/'converted.stl') or raw.get('private_native_file')!=str(directory/'imported.CATPart'):
                raise ValueError('CATIA conversion output escaped the job')
            if raw.get('source_sha256')!=hashlib.sha256((directory/'input.stp').read_bytes()).hexdigest() or raw.get('stl_sha256')!=hashlib.sha256((directory/'converted.stl').read_bytes()).hexdigest():
                raise ValueError('CATIA conversion hash differs')
            return raw,[]
        if template=='catia_export_body_stl':
            if raw.get('source_path')!=parameters['catia_path'] or raw.get('body_name')!=parameters['body_name'] or raw.get('source_geometry_modified') is not False or raw.get('preexisting_documents_preserved') is not True:
                raise ValueError('CATIA native body export source or scope differs')
            if raw.get('stl_file')!=str(directory/'converted.stl') or raw.get('private_native_file')!=str(directory/'body.CATPart'):
                raise ValueError('CATIA body output escaped the job')
            if raw.get('source_sha256')!=hashlib.sha256((directory/'input.CATPart').read_bytes()).hexdigest() or raw.get('stl_sha256')!=hashlib.sha256((directory/'converted.stl').read_bytes()).hexdigest():
                raise ValueError('CATIA native body export hash differs')
            return raw,[]
        if template == "catia_inspect_part":
            if raw.get("read_only") is not True or raw.get("source_path")!=parameters["catia_path"]:
                raise ValueError("Invalid CATIA inspection scope")
            if raw["native_document"]!=raw["document_after_inspection"]:
                raise ValueError("CATIA document state changed during inspection")
            count(raw["body_count"])
            sequence(raw["bodies"])
            return raw,[]
        if template == "catia_get_state":
            if raw.get("connected") is not True or len(sequence(raw.get("documents"))) > min(count(raw.get("document_count")), 100):
                raise ValueError("Invalid CATIA state report")
            for document in raw["documents"]:
                string(document["name"])
                string(document["full_name"])
            return raw, []
        if template=="catia_show_all":
            if raw.get("source_path")!=parameters["catia_path"] or raw.get("backup_file")!=str(directory/"input.CATPart"):
                raise ValueError("CATIA visibility source or backup path differs from the request")
            if raw.get("source_sha256_before")!=hashlib.sha256((directory/"input.CATPart").read_bytes()).hexdigest():
                raise ValueError("CATIA visibility backup hash mismatch")
            if raw.get("source_sha256_after")!=hashlib.sha256(Path(parameters["catia_path"]).read_bytes()).hexdigest():
                raise ValueError("CATIA visibility result source hash mismatch")
            count(raw.get("hidden_before"))
            if count(raw.get("hidden_after"))!=0 or count(raw.get("selected_object_count"))<1:
                raise ValueError("CATIA did not verify all searched objects as visible")
            if raw.get("saved_to_source") is not True and raw.get("save_deferred_for_preexisting_changes") is not True:
                raise ValueError("Missing CATIA save disposition")
            return raw,["input.CATPart"]
        if template == "catia_export_step":
            if raw.get("source_path") != parameters["catia_path"] or raw.get("step_file") != str(directory / "export.stp"):
                raise ValueError("CATIA export source or output path differs from the request")
            if raw.get("step_sha256") != hashlib.sha256((directory / "export.stp").read_bytes()).hexdigest():
                raise ValueError("Exported STEP hash mismatch")
            if raw.get("source_sha256") != hashlib.sha256((directory / "input.CATPart").read_bytes()).hexdigest():
                raise ValueError("Private CATPart snapshot hash mismatch")
            count(raw.get("body_count"))
            count(raw.get("hybrid_body_count"))
            return raw, []
        for key in ("width_mm", "height_mm", "tilt_deg"):
            if number(raw.get(key)) != parameters[key]:
                raise ValueError("CATIA result parameters differ from the request")
        if raw.get("native_part") != str(directory / "mirror.CATPart") or raw.get("step_file") != str(directory / "mirror.stp"):
            raise ValueError("CATIA output path is outside this job")
        if raw.get("step_sha256") != hashlib.sha256((directory / "mirror.stp").read_bytes()).hexdigest():
            raise ValueError("CATIA STEP hash mismatch")
        sequence(raw.get("expected_bounding_box_mm"), 6)
        for value in raw["expected_bounding_box_mm"]:
            number(value)
        return raw, []
    if template=='repair_optical_mesh':
        if not isinstance(raw,dict) or raw.get('backend')!='independent OCCT conservative mesh repair' or raw.get('source_geometry_modified') is not False or raw.get('canonical_requirements_modified') is not False or raw.get('original_design_surfaces_modified') is not False:
            raise ValueError('Invalid conservative mesh repair report')
        candidate=directory/'candidate.stl'
        source=next(r for r in parameters['regions'] if r['label']==parameters.get('target_region','main_lens'))
        if raw.get('candidate_file')!=str(candidate) or raw.get('candidate_sha256')!=hashlib.sha256(candidate.read_bytes()).hexdigest() or raw.get('source_STEP_sha256')!=source['sha256'] or raw.get('source_mesh_sha256')!=parameters['reference_mesh']['sha256']:
            raise ValueError('Mesh repair paths or hashes differ')
        if not isinstance(raw.get('geometry_candidate_accepted'),bool) or not isinstance(raw.get('rejection_reasons'),list) or raw['geometry_candidate_accepted'] is bool(raw['rejection_reasons']):
            raise ValueError('Mesh repair acceptance state is inconsistent')
        if raw.get('native_revalidation_required') is not True or raw.get('optical_checkpoint_verified') is not False:
            raise ValueError('Mesh repair cannot certify native optics')
        if raw.get('boundary_evidence_sha256')!=parameters['boundary_report']['sha256']:
            raise ValueError('Mesh repair evidence hash differs')
        if raw['geometry_candidate_accepted']:
            metrics=raw.get('metrics',{})
            checked=raw.get('candidate_checks',{})
            if number(metrics.get('max_vertex_displacement_mm'))>parameters['max_vertex_displacement_mm']+1e-12 or number(metrics.get('max_geometric_normal_change_deg'))>parameters['max_normal_change_deg'] or metrics.get('degenerate_changed_triangles')!=[] or checked.get('intersections')!=[] or checked.get('pair_check_errors')!=[] or checked.get('basic_topology',{}).get('closed_oriented_two_manifold') is not True:
                raise ValueError('Accepted mesh candidate fails geometric constraints')
        return raw,[]
    if template in ('inspect_optical_region_topology','prepare_optical_regions'):
        prepare=template=='prepare_optical_regions'
        if not isinstance(raw,dict) or raw.get('backend')!='independent OCCT topology inspection' or raw.get('read_only') is not (not prepare) or raw.get('source_geometry_modified') is not False:
            raise ValueError('Invalid independent topology report')
        if set(raw.get('regions',{}))!={r['label'] for r in parameters['regions']}:
            raise ValueError('Topology region set differs')
        for spec in parameters['regions']:
            row=raw['regions'][spec['label']]
            if row.get('source_sha256')!=spec['sha256'] or count(row.get('faces'))<1 or not isinstance(row.get('valid'),bool):
                raise ValueError('Topology hash, count or validity field differs')
            count(row.get('solids'))
            count(row.get('shells'))
        for key in (('IN_sewing','AL_IN_sewing') if parameters.get('region_roles',True) else ()):
            for field in ('free_edges','multiple_edges','faces'):
                count(raw[key][field])
        if parameters.get('local_boundary_checks'):
            checked=raw['regions'][parameters.get('target_region','main_lens')].get('local_boundary_checks',{})
            if not isinstance(checked.get('local_BRepAlgoAPI_Check_valid'),bool) or not checked.get('selected_face_indices_zero_based') or not isinstance(checked.get('local_check_problems'),list) or not isinstance(checked.get('sampling_errors'),list):
                raise ValueError('Local boundary check result is missing or incomplete')
            if parameters.get('reference_mesh'):
                mesh=raw['regions'][parameters.get('target_region','main_lens')].get('local_mesh_checks',{})
                if mesh.get('sha256')!=parameters['reference_mesh']['sha256'] or not isinstance(mesh.get('intersections'),list) or not isinstance(mesh.get('pair_check_errors'),list):
                    raise ValueError('Reference mesh check result is missing or differs')
        if prepare:
            if raw.get('optical_roles_preserved') is not True or raw.get('native_revalidation_required') is not True or raw.get('prepared_manifest')!=str(directory/'prepared-region-manifest.json'):
                raise ValueError('Prepared region scope differs')
            partition=parameters.get('region_roles') or {'coating':'AL','transmitting':'IN'}
            roles=(partition['coating'],partition['transmitting'],*parameters.get('additional_solid_regions',['main_lens'] if parameters.get('include_main_lens') else []))
            prepared_artifacts=['prepared-region-manifest.json']
            for role in roles:
                row=raw['prepared_regions'][role]
                ext='.lug' if parameters.get('geometry_representation')=='normal_mesh' else '.stl' if parameters.get('geometry_representation')=='mesh' else '.stp'
                filename=role+'-prepared'+ext
                if role==partition['transmitting'] and parameters.get('geometry_representation')=='normal_mesh' and parameters.get('pc_normal_policy')=='geometric':
                    filename=role+'-explicit-facet-normals.stl'
                if row['path']!=str(directory/filename) or row['faces']!=raw['regions'][role]['faces'] or hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()!=row['sha256']:
                    raise ValueError('Prepared region path, face count or hash differs')
                prepared_artifacts.append(filename)
            return raw,prepared_artifacts
        return raw,[]
    fields = raw.get("List") if isinstance(raw, dict) else None
    if not isinstance(fields, dict) or set(fields) != {f"p{i}" for i in range(len(fields))}:
        raise ValueError("Expected a JSONStore List report")
    values = [fields[f"p{i}"] for i in range(len(fields))]
    if template=='replay_rayfile_batch':
        from . import ray_replay
        return ray_replay.result(directory,parameters,sequence(values,6))

    if template=='merge_optical_batches':
        from .gpu_merge import read_result
        return read_result(directory,parameters,sequence(values))

    if template=='trace_optical_gpu':
        from .gpu_trace import read_result
        return read_result(directory,parameters,sequence(values,5))

    if template=='probe_gpu_interfaces':
        from .gpu_interfaces import read_result
        return read_result(directory,values)

    if template=='validate_gpu_loss':
        rows=sequence(sequence(values,2)[0],34)
        expected=[1000,0,750,562.5,1000,1000*.96/1.04,750*.96/1.04,998,996.004,1000,1000,960,998,998,
            1000*2*.04/1.04,1000*(.04+.75*.04*.96/1.04),40]
        output=[]
        for i,row in enumerate(rows):
            mode,variant,ok,flux=sequence(row,4)
            if mode!=(20 if i<17 else 13) or variant!=i%17 or ok!=1 or not 0<=number(flux)<=1001:
                raise ValueError('Invalid GPU material fixture output')
            target=expected[i%17];p=target/1000
            tolerance=.05+6*1000*math.sqrt(p*(1-p)/131072)
            output.append({'mode':mode,'variant':variant,'flux_lm':flux,'expected_lm':target,
                'tolerance_lm':tolerance,'passed':abs(flux-target)<=tolerance})
        log=(directory/'engine.log').read_text(encoding='utf-8',errors='replace').lower()
        material_log=log.split('history_probe_begin')[0]
        native_gpu_verified=material_log.count('nurbs intersection type is 13 (graphics card)')==17 and 'switching to' not in material_log
        history=sequence(values[1],2)
        for row,mode in zip(history,(20,13)):
            m,ok,items=sequence(row,3)
            if m!=mode or ok not in (0,1) or not 0<=count(items)<=1024:
                raise ValueError('Invalid native GPU history probe')
        if history[0]!=[20,1,1024]:raise ValueError('CPU history control fixture did not produce the expected paths')
        return {'cases':output,'candidate_accepted':all(r['passed'] for r in output) and native_gpu_verified,
            'native_GPU_execution_verified':native_gpu_verified,'S9_simulation_run':False,'history_probe':history,
            'gpu_ray_history_supported_in_fixture':history[1]==[13,1,1024],
            'scope':'fixed slabs, entry-only, forward/backward Fresnel and TIR validation; S9 comparison cannot exclude common material bias',
            'mechanism_under_test':'ordinary/outgoing-air-absorbing mixture; entry-reflection bias must pass before any acceptance'},[]

    if template=='gpu_probe':
        log_path=directory/'engine.log'
        log_text=log_path.read_text(encoding='utf-8',errors='replace').lower() if log_path.is_file() else ''
        if 'switching to' in log_text:
            raise ValueError('Requested GPU fixture fell back to another trace mode')
        n,selected,devices,attempted,ok,stats=sequence(values,6)
        n=count(n);number(selected)
        if len(sequence(devices))!=n or int(selected)!=selected or selected < -1 or (n and not 0<=selected<n):
            raise ValueError('Invalid native GPU device selection')
        if attempted not in (0,1) or ok not in (0,1) or attempted!=int(n>0) or (ok and not attempted):
            raise ValueError('Invalid GPU smoke state')
        entries=[{'name':string(sequence(row,2)[0]),'details':string(row[1])} for row in devices]
        lo,hi,flux=map(number,sequence(stats,3))
        if min(lo,hi,flux)<0 or lo>hi or flux>1000.1 or (ok and (hi<=0 or flux<=0)):
            raise ValueError('GPU smoke returned invalid detector data')
        if ok and abs(flux-1000)>.01:
            raise ValueError('GPU triangle mirror fixture did not preserve its known 1000 lm')
        mode_evidence='nurbs intersection type is 13 (graphics card)' in log_text
        timing_evidence='gpu intersection time' in log_text
        return {'native_device_count':n,'devices':entries,'selected_device':int(selected),
            'requested_trace_mode':13,'fixture_rays':10000,'gpu_trace_attempted':bool(attempted),
            'native_trace_returned_success':bool(ok),'fixture_sensor_flux_lm':flux,
            'native_GPU_smoke_verified':bool(ok and mode_evidence and timing_evidence),
            'native_GPU_mode_log_evidence':mode_evidence,'GPU_intersection_timing_reported':timing_evidence,
            'legacy_SM_core_mapping_warning':'mapsmtocores undefined smversion' in log_text,
            'GPU_SM_utilization_verified':False,'S9_GPU_backend_validated':False,
            'S9_simulation_run':False,'scope':'device query and fixed triangle-mirror mode-13 smoke test only'},[]

    if template=='replay_optical_boundary_paths':
        from .boundary_paths import read_result
        if len(values) not in (2,3):
            raise ValueError('Unexpected checkpoint report structure')
        return read_result(values,parameters),[]

    if template == "export_ies":
        from .photometry import inspect_ies,sample_bounds
        angle,grid,bounds,center,stats,rangle,rgrid,rbounds,rcenter,rstats,error,rounding_units=sequence(values,12)
        angle,rangle=count(angle),count(rangle)
        grid=[count(v) for v in sequence(grid,2)]
        rgrid=[count(v) for v in sequence(rgrid,2)]
        for row in (sequence(bounds,4),sequence(rbounds,4),sequence(stats,3),sequence(rstats,3)):
            for value in row:
                number(value)
        error=number(error)
        rounding_units=number(rounding_units)
        if angle not in (0,1,2) or rangle!=angle or grid!=rgrid or center not in (0,1) or rcenter not in (0,1):
            raise ValueError('IES readback changed angular type or grid')
        expected_bounds=sample_bounds(bounds,grid)
        actual_bounds=sample_bounds(rbounds,rgrid)
        if max(abs(a-b) for a,b in zip(expected_bounds,actual_bounds))>1e-7:
            raise ValueError('IES readback changed physical sampling directions')
        tolerance=max(1e-5,abs(stats[1])*5.2e-6)
        if stats[0]<0 or error<0 or not 0<=rounding_units<=1 or error>tolerance or abs(stats[1]-rstats[1])>tolerance:
            raise ValueError('IES readback changed candela values')
        parsed=inspect_ies(directory/'photometry.ies')
        if parsed['photometric_type']!={0:'B',1:'A',2:'C'}[angle] or [parsed['horizontal_angles'],parsed['vertical_angles']]!=grid:
            raise ValueError('IES file angular metadata disagrees with native data')
        file_bounds=parsed['horizontal_range_deg']+parsed['vertical_range_deg']
        if max(abs(a-b) for a,b in zip(expected_bounds,file_bounds))>1e-6:
            raise ValueError('IES file sampling directions disagree with native data')
        if abs(parsed['peak_cd']-stats[1])>tolerance:
            raise ValueError('IES file candela scale differs from the source')
        return {'source_lid':parameters['lid_path'],'ies_file':str(directory/'photometry.ies'),
                'native_readback_verified':True,'all_cells_compared':grid[0]*grid[1],
                'maximum_candela_error':error,'tolerance_cd':tolerance,
                'maximum_per_cell_rounding_units':rounding_units,
                'rounding_check':'each cell within native six-significant-digit decimal rounding plus float32 readback error; black cells remain zero',
                'source_sample_bounds_deg':expected_bounds,'source_grid_uv':grid,
                'source_flux_in_data_domain_lm':stats[2],'readback_flux_in_data_domain_lm':rstats[2],
                'file_inspection':parsed,'simulation_run':False,'regulatory_compliance_evaluated':False},[]

    if template == "inspect_rayfile":
        format_id,spectral,rays,checked=sequence(values,4)
        format_id=count(format_id)
        rays,checked=count(rays),count(checked)
        if format_id not in (0,1,3,5,6) or spectral not in (0,1) or bool(spectral)!=(format_id in (5,6)):
            raise ValueError("Invalid native ray file format report")
        if rays<1 or checked!=rays:
            raise ValueError("Native RayFile check failed")
        return {"rayfile_path":parameters["rayfile_path"],"format_id":format_id,
                "format":{0:"ASCII",1:"Binary",3:"TechnoTeam",5:"ASCII spectral",6:"Binary spectral"}[format_id],
                "is_spectral":bool(spectral),"ray_count":rays,"native_check_ray_count":checked,
                "check_verified":True,"original_file_modified":False,"simulation_run":False},[]

    if template == "trace_optical_paths":
        from .path_sampling import read_terminals
        from .optical_scene import validate_conditioned_bounds
        if len(values) not in (3,4):
            raise ValueError('Unexpected S9 trace report structure')
        region_rows,statistics,escaped=values[:3]
        bounds=values[3] if len(values)>3 else []
        if bounds:
            validate_conditioned_bounds(bounds,parameters)
        elif parameters.get('coordinate_scale',1)!=1:
            raise ValueError('Conditioned tracing requires native geometry bounds')
        frames=optical_scene.sources(parameters)
        media=optical_scene.media(parameters)
        if len(sequence(region_rows))!=len(parameters['regions']) or len(sequence(statistics))!=len(frames):
            raise ValueError('Unexpected S9 trace regions or source count')
        for row,spec in zip(region_rows,parameters['regions']):
            label,n,skipped,kind=sequence(row,4)
            if label!=spec['label'] or count(n)!=spec['faces'] or skipped not in (0,1) or kind!=optical_scene.MATERIAL_CLASSES[parameters['materials'][spec['label']]['kind']]:
                raise ValueError('S9 trace geometry differs from validated regions')
            if 'native_top_level_object_count' in spec and n+skipped!=spec['native_top_level_object_count']:
                raise ValueError('Native top-level geometry count differs')
        escaped_sum=0
        native_counts={m['id']:0 for m in media}
        for i,row in enumerate(statistics):
            numbers=list(map(count,sequence(row,5+len(media))))
            axis,n,e,s=numbers[:4];visits=numbers[4:-1];steps=numbers[-1]
            if axis!=frames[i]['native_id'] or n!=parameters['rays_per_led'] or e+s!=n or steps<n:
                raise ValueError('Invalid S9 sampled path counts')
            escaped_sum+=e
            for m,value in zip(media,visits):
                native_counts[m['id']]+=value
        beams,counts,sampling,rows=read_terminals(directory,parameters)
        if rows!=count(escaped) or escaped_sum!=rows or counts!=native_counts:
            raise ValueError('Terminal rays differ from native path statistics')
        return {'simulation_run':True,'native_backend':'RayPathFinder stochastic nonsequential CPU paths',
            'coordinate_scale':parameters.get('coordinate_scale',1),'native_geometry_bounds':bounds,
            'weighted_rayfile_position_unit':'original model mm','coordinate_conditioning_acceptance':'diagnostic only' if parameters.get('coordinate_scale',1)!=1 else 'not applied',
            'main_lens_boundary_mode':parameters.get('main_lens_boundary_mode','individual_surfaces'),
            'main_lens_boundary_evidence':parameters.get('main_lens_boundary_evidence'),
            'total_input_rays':len(frames)*parameters['rays_per_led'],'rays_per_led':parameters['rays_per_led'],
            'source_count':len(frames),'rays_per_source':parameters['rays_per_led'],'operating_modes':optical_scene.modes(parameters),
            'beams':beams,'sampling':sampling,'source_statistics':statistics,
            'completed_lens_visits_for_escaped_rays':counts,
            'geometry_approximations':[{'region':r['label'],**r['mesh']} for r in parameters['regions'] if r.get('mesh')],
            'mesh_convergence_verified':False if any(r.get('mesh') for r in parameters['regions']) else None,
            'per_complete_traversal_absorption_fraction':None if parameters.get('scene_schema_version') else .002,
            'material_traversal_absorption':{m['id']:m['loss'] for m in media},'history_weights_independently_verified':True,
            'source_frame_transform_validated':True,'source_frame_geometric_preflight_passed':False,
            'source_frame_mounting_accuracy_certified':False,
            'generalized_native_backend_validated':False if parameters.get('scene_schema_version') else None,
            'convergence_verified':False,'regulatory_compliance_evaluated':False,'studio_modified':False},[name+'-weighted.ray' for name in beams]

    if template == "bin_rayfile":
        rays,angle,grid,bounds,stats=sequence(values,5)
        expected_grid=parameters.get('grid_uv',[1200,300])
        settings=optical_scene.sensor(parameters)
        if not 1<=count(rays)<=2000000 or angle!=0 or grid!=expected_grid or bounds!=settings['bounds_deg']:
            raise ValueError('Weighted ray binning grid or count differs')
        for v in sequence(stats,3):
            number(v)
        if stats[0]<0 or stats[1]<=0 or stats[2]<=0:
            raise ValueError('Weighted ray field is empty or invalid')
        return {'source_rayfile':parameters['rayfile_path'],'ray_count':rays,'lid_file':str(directory/'photometry.lid'),
            'angle_type':'B','native_angle_type':0,'grid_uv':grid,'cell_edge_range_deg':bounds,
            'angular_resolution_deg':parameters.get('angular_resolution_deg',.1),
            'minimum_cd':stats[0],'maximum_cd':stats[1],'flux_in_sensor_domain_lm':stats[2],
            'sensor':settings,'input_coordinate_convention':'ray positions/directions in model coordinates; far-field frame explicitly recorded',
            'regulatory_compliance_evaluated':False,'smoothing_applied':False},[]

    if template == "validate_traversal_absorption":
        if len(sequence(values)) not in (11,12):
            raise ValueError('Unexpected absorption fixture report length')
        n,forward,backward,weighted,weighted_back,entered,internal,incomplete,error,tir,normal_mesh=values[:11]
        boundary_fixture=None
        if len(values)==12:
            boundary_fixture=[]
            for i,row in enumerate(sequence(values[11],8)):
                local,closed,blocked,size,length,intensity,index,dz=sequence(row,8)
                for value in row:
                    number(value)
                if [local,closed,blocked]!=[i//4,(i//2)%2,i%2] or count(size)<1:
                    raise ValueError('Closed-boundary fixture cases differ')
                if size<(2 if blocked else 3):
                    raise ValueError('Closed-boundary fixture did not intersect the glass surfaces')
                escaped=length>1e30 and intensity>0
                if not blocked and (not escaped or abs(index-1)>1e-6 or abs(dz-1)>1e-6):
                    raise ValueError('Closed-boundary fixture failed a clear glass-to-air exit')
                if blocked and not local and escaped:
                    raise ValueError('Global tracing silently ignored the embedded absorber')
                boundary_fixture.append({'local_trace':bool(local),'closed_boundary':bool(closed),
                    'embedded_absorber':bool(blocked),'escaped':escaped,'last_medium_index':index,
                    'embedded_absorber_ignored':bool(blocked and escaped),'path_segments':count(size)})
        n,forward,backward,entered,internal,incomplete=map(count,(n,forward,backward,entered,internal,incomplete))
        for v in (weighted,weighted_back,error):
            number(v)
        sequence(tir,4)
        for v in tir:
            number(v)
        if n!=20000 or forward+backward!=n or incomplete or internal<1 or error>1e-6:
            raise ValueError('Native paths do not match complete stochastic Fresnel slab histories')
        expected_forward=(1-.04)/(1+.04)
        tolerance=6*math.sqrt(expected_forward*(1-expected_forward)/n)
        if abs(forward/n-expected_forward)>tolerance:
            raise ValueError('Native path sampling differs from analytic Fresnel slab transmission')
        if abs(weighted-forward*.998)>1e-6 or abs(weighted+weighted_back-(n-.002*entered))>1e-6:
            raise ValueError('Per-traversal weighting differs from the analytic event count')
        if count(tir[0])!=2 or tir[1]>=0 or abs(tir[2]-1)>1e-9 or abs(tir[3]-1.5)>1e-9:
            raise ValueError('Internal reflection fixture changed intensity or medium')
        sequence(normal_mesh,4)
        for v in normal_mesh:
            number(v)
        normal_verified=normal_mesh[0]==2 and max(abs(a-b) for a,b in zip(normal_mesh[1:],[math.sin(math.radians(20)),0,math.cos(math.radians(20))]))<1e-6
        return {'fixture':'20,000 normal-incidence rays through lossless uncoated n=1.5 slab; one TIR case',
            'trials':n,'forward_trials':forward,'backward_trials':backward,'internal_reflections_observed':internal,
            'expected_forward_fraction':expected_forward,'observed_forward_fraction':forward/n,
            'six_sigma_sampling_tolerance':tolerance,'weighted_forward_sum':weighted,
            'weighted_backward_sum':weighted_back,'entered_trials':entered,
            'per_complete_traversal_factor':.998,'tir_preserves_medium_and_intensity':True,
            'native_stochastic_path_weighting_fixture_verified':True,
            'normal_mesh_raytracing_verified':normal_verified,'normal_mesh_direction':normal_mesh,
            'closed_boundary_fixture':boundary_fixture,
            's9_simulation_run':False},[]

    if template == "preflight_optical_scene":
        region_rows,source_rows,shapes,rays,path_rows=sequence(values,5)
        expected=parameters['regions']
        if len(sequence(region_rows))!=len(expected) or count(shapes)!=1+sum(r['faces'] for r in expected):
            raise ValueError('Optical scene physical surface count differs')
        material_types={'refractive':'Refractive','specular':'Specular','Lambertian':'LambertianReflective','absorber':'Absorber'}
        regions=[]
        for raw_row,spec in zip(region_rows,expected):
            label,faces,skipped,kind=sequence(raw_row,4)
            if label!=spec['label'] or count(faces)!=spec['faces'] or count(skipped)>1 or kind!=material_types[parameters['materials'][label]['kind']]:
                raise ValueError('Native optical region assignment differs')
            regions.append({'label':label,'physical_faces':faces,'skipped_axis_systems':skipped,'material_type':kind})
        frames=optical_scene.sources(parameters)
        if count(rays)!=parameters['ray_count'] or len(sequence(source_rows))!=len(frames) or len(sequence(path_rows))!=len(frames):
            raise ValueError('Source count or ray-file count differs')
        for i,row in enumerate(source_rows):
            axis,origin,z,x=sequence(row,4)
            if axis!=frames[i]['native_id'] or any(abs(number(a)-b)>1e-7 for a,b in zip(sequence(z,3)+sequence(x,3),frames[i]['frame']['z']+frames[i]['frame']['x'])) or len(sequence(origin,3))!=3:
                raise ValueError('Native source frame differs')
            if max(abs(number(a)-b) for a,b in zip(origin,parameters['positions_mm'][i]))>1e-9:
                raise ValueError('Native source origin differs')
        paths=[]
        for i,row in enumerate(path_rows):
            axis,n,segments=sequence(row,3)
            if axis!=frames[i]['native_id'] or count(n)<1 or len(sequence(segments))!=min(n,128):
                raise ValueError('Invalid diagnostic path list')
            for segment in segments:
                p,d,length,intensity,index=sequence(segment,5)
                for value in sequence(p,3)+sequence(d,3)+[length,intensity,index]:
                    number(value)
                if length<0 or intensity<0 or index<1:
                    raise ValueError('Invalid diagnostic ray data')
            paths.append({'axis_number':axis,'segment_count':n,'segments':segments,'truncated':n>len(segments)})
        return {'regions':regions,'physical_geometry_faces':shapes-1,'rayfile_ray_count':rays,
                'candidate_source_count':len(frames),'candidate_source_axes':source_rows,'diagnostic_paths':paths,
                'extra_per_traversal_absorption_applied':False,'photometric_simulation_run':False,
                'regulatory_compliance_evaluated':False,'source_alignment_verified':False,
                'studio_modified':False,'diagnostic_only':True},[]

    if template == "inspect_cad_geometry":
        total,surfaces,roots,bounds,rows,matched,matches,top_count,top_rows=sequence(values,9)
        total,surfaces,roots,matched,top_count=map(count,(total,surfaces,roots,matched,top_count))
        if min(total,surfaces,roots)<1 or roots>surfaces or matched>total:
            raise ValueError('Invalid STEP geometry counts')
        sequence(bounds,6)
        for value in bounds:
            number(value)
        if any(bounds[i]>bounds[i+1] for i in (0,2,4)):
            raise ValueError('Invalid STEP geometry bounds')
        if len(sequence(rows))>min(total,parameters['max_objects']) or len(sequence(matches))>min(matched,parameters['max_objects']):
            raise ValueError('Invalid STEP object list length')
        named=objects(matches)
        if not 1<=top_count<=total or len(sequence(top_rows))>min(top_count,parameters['max_objects']):
            raise ValueError('Invalid STEP top-level object list')
        return {'cad_path':parameters['cad_path'],'object_count':total,'surface_count_including_references':surfaces,
                'root_filter_surface_count':roots,'counts_are_not_unique_cad_faces':True,'bounding_box_mm':bounds,
                'objects':objects(rows),'objects_truncated':total>len(rows),
                'top_level_object_count':top_count,'top_level_objects':objects(top_rows),
                'top_level_objects_truncated':top_count>len(top_rows),
                'named_region_match_count':matched,'named_region_matches':named,
                'named_matches_truncated':matched>len(matches),'optical_region_mapping_verified':False,
                'simulation_run':False,'studio_modified':False},[]

    if template == "import_cad_geometry":
        total, surface_count, root_count, bounds, roundtrip_bounds = sequence(values, 5)
        total, surface_count, root_count = count(total), count(surface_count), count(root_count)
        if not 1 <= root_count <= surface_count <= total:
            raise ValueError("Invalid imported geometry counts")
        sequence(bounds, 6)
        sequence(roundtrip_bounds, 6)
        for value in bounds + roundtrip_bounds:
            number(value)
        if any(bounds[i] > bounds[i+1] for i in (0,2,4)):
            raise ValueError("Invalid imported bounding box")
        error = max(abs(a-b) for a,b in zip(bounds,roundtrip_bounds))
        if error > 0.000001:
            raise ValueError("Geometry bounds changed after saving the native model")
        return {"cad_path": parameters["cad_path"], "model_file": str(directory / "geometry.lue"),
                "object_count": total, "surface_count": surface_count, "root_surface_count": root_count,
                "bounding_box_mm": bounds, "dimensions_mm": [bounds[i+1]-bounds[i] for i in (0,2,4)],
                "native_roundtrip_verified": True, "native_roundtrip_bounds_error_mm": error,
                "optical_objects_added": False, "coordinate_transform_applied": False}, []

    if template == "prepare_cad_mirror_scene":
        ok, total, bounds = sequence(values, 3)
        if ok != 1 or count(total) != 1:
            raise ValueError("Expected exactly one imported root surface")
        sequence(bounds, 6)
        for value in bounds:
            number(value)
        if any(bounds[i] > bounds[i+1] for i in (0, 2, 4)) or sum(bounds[i+1] > bounds[i] for i in (0, 2, 4)) < 2:
            raise ValueError("Invalid CAD surface bounding box")
        return {"cad_path": parameters["cad_path"], "root_surface_count": 1,
                "bounding_box_mm": bounds, "scene_file": str(directory / "scene.lue"),
                "reflection_factor": parameters["reflection_factor"], "source_flux_lm": parameters["flux_lm"],
                "source_position_mm": [0, 0, parameters["source_z_mm"]], "source_direction_xyz": [0, 0, -1],
                "sensor_range_deg": [-40, 40, -20, 20], "sensor_grid": [160, 80]}, []

    if template == "inspect_model":
        total, rows, groups = sequence(values, 3)
        result = {"object_count": count(total), "objects": objects(rows), "groups": {}}
        if len(rows) > min(total, parameters["max_objects"]):
            raise ValueError("Invalid object list length")
        result["truncated"] = total > len(rows)
        expected_groups = {"emitters", "sensors", "geometry", "actors", "light_data"}
        for group in sequence(groups, 5):
            label, size, members = sequence(group, 3)
            if label not in expected_groups or label in result["groups"]:
                raise ValueError("Unexpected object group")
            result["groups"][label] = {"count": count(size), "objects": objects(members), "truncated": size > len(members)}
        return result, []

    if template == "simulate_model":
        rays, emitter_count, rows = sequence(values, 3)
        if count(rays) != parameters["rays"] or count(emitter_count) < 1:
            raise ValueError("Invalid simulation summary")
        sensors = []
        files = []
        for index, row in enumerate(sequence(rows)):
            name, kind, filename = sequence(row, 3)
            if filename != f"sensor_{index}.lid":
                raise ValueError("Invalid sensor result filename")
            files.append(filename)
            sensors.append({"name": string(name), "type": string(kind), "result_file": str(directory / filename)})
        if not 1 <= len(sensors) <= 64:
            raise ValueError("Invalid sensor count")
        return {"rays": count(rays), "emitter_material_count": count(emitter_count), "sensors": sensors}, files

    if template == "analyze_lid":
        name, angle, sizes, bounds, peak, minimum, integral, zone = sequence(values, 8)
        sequence(sizes, 4)
        sequence(bounds, 4)
        sequence(peak, 3)
        if count(angle) not in (0, 1, 2) or sizes[2:] != [1, 1] or min(sizes[:2]) <= 0:
            raise ValueError("Only two-dimensional candela distributions are supported")
        for value in sizes + bounds + peak + [minimum, integral]:
            number(value)
        result = {"name": string(name), "unit": "cd", "angle_type": {0: "B", 1: "A", 2: "C"}[angle],
                  "grid": {"u_cells": count(sizes[0]), "v_cells": count(sizes[1])},
                  "angular_range_deg": dict(zip(("u_min", "u_max", "v_min", "v_max"), bounds)),
                  "peak": {"intensity_cd": peak[0], "u_deg": peak[1], "v_deg": peak[2]},
                  "minimum_cd": minimum, "flux_in_data_domain_lm": number(integral)}
        if parameters["region_deg"] is not None:
            result["region"] = {"bounds_deg": parameters["region_deg"], "flux_lm": number(zone)}
        return result, []
    raise ValueError("Unknown report template")
