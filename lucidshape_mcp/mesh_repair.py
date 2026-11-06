"""Conservative, evidence-bound derived main-lens mesh repair through MCP.

Never edits a source file or the canonical simulation requirements. Candidate
geometry and acceptance decisions are separate artifacts. Native revalidation
and optical checkpoint tests remain mandatory after geometric acceptance.
"""
import hashlib
import json
import math
from pathlib import Path
from . import kernel_regions
from .inputs import input_file


def prepare(manifest_path,boundary_report_path,max_vertex_displacement_mm,max_normal_change_deg,repair_radius_mm,repair_method='split_intersections',target_region='main_lens'):
    if repair_method not in ('project_plane','split_intersections'):
        raise ValueError('Unsupported conservative mesh repair method')
    for value,low,high in ((max_vertex_displacement_mm,1e-7,.001),(max_normal_change_deg,.0001,.5),(repair_radius_mm,.1,20)):
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:
            raise ValueError('Repair bound is outside the supported conservative range')
    report_path=input_file(boundary_report_path,{'.json'})
    data=report_path.read_bytes()
    if len(data)>2*1024*1024:
        raise ValueError('Boundary evidence exceeds two MiB')
    report=json.loads(data)
    job=report.get('job',{})
    if report.get('status')!='succeeded' or job.get('status')!='succeeded' or job.get('template')!='inspect_optical_region_topology':
        raise ValueError('A completed read-only MCP topology diagnosis is required')
    main=job['result']['regions'][target_region]
    mesh=main.get('local_mesh_checks',{})
    if not mesh.get('intersections') or mesh.get('pair_check_errors')!=[] or len(mesh.get('local_incoming_line_hits',[]))<2:
        raise ValueError('Evidence must contain verified local mesh intersections and the incoming line')
    reference=job['parameters']['reference_mesh']['source']
    points=job['parameters']['probe_points_mm']
    params=kernel_regions.prepare(manifest_path,points,[target_region],True,reference,target_region=target_region)
    source=next(r for r in params['regions'] if r['label']==target_region)
    if source['sha256']!=main['source_sha256'] or params['reference_mesh']['sha256']!=mesh['sha256']:
        raise ValueError('Repair inputs differ from diagnosed geometry snapshots')
    params.update(boundary_report={'source':str(report_path),'sha256':hashlib.sha256(data).hexdigest(),'staged_name':'boundary-evidence.json'},
        target_point_mm=mesh['local_incoming_line_hits'][0]['point_mm'],
        max_vertex_displacement_mm=max_vertex_displacement_mm,max_normal_change_deg=max_normal_change_deg,
        repair_radius_mm=repair_radius_mm,repair_method=repair_method)
    return params


def stage(directory,parameters):
    rows=kernel_regions.stage(directory,parameters)
    spec=parameters['boundary_report']
    data=Path(spec['source']).read_bytes()
    if hashlib.sha256(data).hexdigest()!=spec['sha256']:
        raise ValueError('Boundary evidence changed before staging')
    (directory/spec['staged_name']).write_bytes(data)
    rows.append({'source':spec['source'],'source_sha256':spec['sha256'],'staged_name':spec['staged_name'],'source_bytes':len(data)})
    return rows


def read_triangles(path):
    import struct
    data=path.read_bytes()
    count=struct.unpack_from('<I',data,80)[0] if len(data)>=84 else 0
    if count and len(data)==84+50*count:
        return [tuple(tuple(values[j:j+3]) for j in (3,6,9)) for values in
            (struct.unpack_from('<12fH',data,84+50*i) for i in range(count))]
    result=[]
    corners=[]
    for line in data.decode('ascii').splitlines():
        words=line.split()
        if words and words[0].lower()=='vertex':
            corners.append(tuple(map(float,words[1:])))
            if len(corners)==3:
                result.append(tuple(corners))
                corners=[]
    if corners or not result:
        raise ValueError('Incomplete STL')
    return result


def normal(triangle):
    a,b,c=triangle
    u=[b[i]-a[i] for i in range(3)]
    v=[c[i]-a[i] for i in range(3)]
    n=(u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0])
    length=math.sqrt(sum(x*x for x in n))
    return tuple(x/length for x in n) if length>1e-20 else None


def project_candidate(triangles,plane_origin,plane_normal,center,radius,max_displacement):
    """One shared vertex map ensures incident triangles remain connected."""
    mapping={}
    moved=[]
    for p in {p for triangle in triangles for p in triangle}:
        if math.dist(p,center)>radius:
            continue
        distance=sum((p[j]-plane_origin[j])*plane_normal[j] for j in range(3))
        if abs(distance)>max_displacement or abs(distance)<1e-12:
            continue
        projected=tuple(p[j]-distance*plane_normal[j] for j in range(3))
        mapping[p]=projected
        moved.append({'before_mm':p,'after_mm':projected,'distance_mm':math.dist(p,projected)})
    candidate=[tuple(mapping.get(p,p) for p in triangle) for triangle in triangles]
    changed=[i for i,(a,b) in enumerate(zip(triangles,candidate)) if a!=b]
    angles=[]
    degenerate=[]
    for i in changed:
        first,last=normal(triangles[i]),normal(candidate[i])
        if first is None or last is None:
            degenerate.append(i)
        else:
            angles.append(math.degrees(math.acos(max(-1,min(1,sum(a*b for a,b in zip(first,last)))))))
    return candidate,{'moved_vertices':moved,'changed_triangle_indices_zero_based':changed,
        'max_vertex_displacement_mm':max((r['distance_mm'] for r in moved),default=0),
        'max_geometric_normal_change_deg':max(angles,default=0),'degenerate_changed_triangles':degenerate}


def write_ascii_stl(path,triangles):
    with path.open('w',encoding='ascii',newline='\n') as stream:
        stream.write('solid derived_optical_candidate\n')
        for triangle in triangles:
            n=normal(triangle) or (0,0,0)
            stream.write('facet normal '+' '.join(format(x,'.17g') for x in n)+'\nouter loop\n')
            for p in triangle:
                stream.write('vertex '+' '.join(format(x,'.17g') for x in p)+'\n')
            stream.write('endloop\nendfacet\n')
        stream.write('endsolid derived_optical_candidate\n')


def run(request):
    from OCP.STEPControl import STEPControl_Reader
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.TopAbs import TopAbs_FACE,TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.gp import gp_Pnt
    from .boundary_mesh import inspect_local_mesh
    from .stl_geometry import inspect_stl
    params=json.loads(request.read_text(encoding='utf-8'))['parameters']
    directory=request.parent
    for spec in params['regions']+[params['reference_mesh'],params['boundary_report']]:
        if hashlib.sha256((directory/spec['staged_name']).read_bytes()).hexdigest()!=spec['sha256']:
            raise ValueError('Repair input snapshot differs')
    target_region=params.get('target_region','main_lens')
    main=next(r for r in params['regions'] if r['label']==target_region)
    reader=STEPControl_Reader()
    if reader.ReadFile(str(directory/main['staged_name']))!=IFSelect_RetDone or reader.TransferRoots()<1:
        raise ValueError('Source STEP read failed')
    shape=reader.OneShape()
    if not BRepCheck_Analyzer(shape).IsValid():
        raise ValueError('Repair source STEP is invalid')
    planes=[]
    target=gp_Pnt(*params['target_point_mm'])
    vertex=BRepBuilderAPI_MakeVertex(target).Vertex()
    explorer=TopExp_Explorer(shape,TopAbs_FACE)
    index=0
    while explorer.More():
        face=TopoDS.Face_s(explorer.Current())
        adaptor=BRepAdaptor_Surface(face)
        if adaptor.GetType()==GeomAbs_Plane:
            plane=adaptor.Plane()
            if plane.Distance(target)<1e-4:
                distance=BRepExtrema_DistShapeShape(vertex,face)
                if distance.IsDone() and distance.NbSolution()>0 and distance.Value()<.05:
                    origin=plane.Location()
                    direction=plane.Axis().Direction()
                    planes.append({'face_index_zero_based':index,'trimmed_distance_mm':distance.Value(),
                        'origin_mm':[origin.X(),origin.Y(),origin.Z()],
                        'normal':[direction.X(),direction.Y(),direction.Z()]})
        index+=1
        explorer.Next()
    planes.sort(key=lambda r:r['trimmed_distance_mm'])
    if not planes:
        raise ValueError('No independently verified source plane near diagnosed boundary')
    plane=planes[0]
    print('REPAIR_SOURCE_PLANE',plane['face_index_zero_based'],flush=True)
    source=directory/params['reference_mesh']['staged_name']
    triangles=read_triangles(source)
    if params['repair_method']=='split_intersections':
        from .mesh_cut_repair import split_union_candidate
        evidence=json.loads((directory/params['boundary_report']['staged_name']).read_text(encoding='utf-8'))
        intersections=evidence['job']['result']['regions'][target_region]['local_mesh_checks']['intersections']
        seeds=sorted({i for pair in intersections for i in pair['triangle_indices_zero_based']})
        candidate,metrics=split_union_candidate(triangles,seeds,params['target_point_mm'],params['repair_radius_mm'],
            construction_tolerance=min(5e-7,params['max_vertex_displacement_mm']/10))
    else:
        candidate,metrics=project_candidate(triangles,plane['origin_mm'],plane['normal'],params['target_point_mm'],
            params['repair_radius_mm'],params['max_vertex_displacement_mm'])
    output=directory/'candidate.stl'
    write_ascii_stl(output,candidate)
    print('REPAIR_CANDIDATE',len(metrics['moved_vertices']),'vertices',len(metrics['changed_triangle_indices_zero_based']),'triangles',flush=True)
    checked=inspect_local_mesh(output,params['probe_points_mm'])
    rejected=[]
    if not metrics['changed_triangle_indices_zero_based']:
        rejected.append('no_vertices_changed')
    if metrics['max_geometric_normal_change_deg']>params['max_normal_change_deg']:
        rejected.append('normal_change_bound_exceeded')
    if metrics['max_vertex_displacement_mm']>params['max_vertex_displacement_mm']+1e-12:
        rejected.append('vertex_displacement_bound_exceeded')
    if metrics['degenerate_changed_triangles'] or checked['basic_topology']['degenerate_triangles']:
        rejected.append('degenerate_triangles')
    if not checked['basic_topology']['closed_oriented_two_manifold']:
        rejected.append('closed_oriented_manifold_check_failed')
    if checked['basic_topology']['signed_volume_mm3']<=0:
        rejected.append('nonpositive_enclosed_volume')
    if checked['intersections'] or checked['pair_check_errors']:
        rejected.append('local_intersections_or_uncompleted_checks')
    result={'backend':'independent OCCT conservative mesh repair','source_geometry_modified':False,
        'original_design_surfaces_modified':False,'canonical_requirements_modified':False,
        'candidate_file':str(output),'candidate_sha256':checked['sha256'],
        'source_STEP_sha256':main['sha256'],'source_mesh_sha256':params['reference_mesh']['sha256'],
        'boundary_evidence_sha256':params['boundary_report']['sha256'],
        'method':params['repair_method'],
        'source_plane':plane,'metrics':metrics,'candidate_checks':checked,
        'geometry_candidate_accepted':not rejected,'rejection_reasons':rejected,
        'native_revalidation_required':True,'optical_checkpoint_verified':False,
        'mesh_convergence_verified':False,'global_self_intersection_check_completed':False,
        'note':'A provisional local candidate only. Source curves are unchanged; moved mesh vertices are approximations bounded by the request.'}
    (directory/'engine-result.json').write_text(json.dumps(result,allow_nan=False,indent=2)+'\n',encoding='utf-8')
    print('REPAIR_GEOMETRY_ACCEPTED',not rejected,'REASONS',rejected,flush=True)
    print('LUCIDSHAPE_MCP_OK',flush=True)
