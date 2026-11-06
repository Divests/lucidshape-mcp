"""Independent geometry diagnosis. Only called by the fixed MCP topology tool."""
from pathlib import Path
import hashlib
import json
import math
import struct
import sys

from .config import configure_geometry_runtime

def main():
    configure_geometry_runtime()
    from OCP.STEPControl import STEPControl_Reader
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.TopAbs import TopAbs_FACE,TopAbs_EDGE,TopAbs_VERTEX,TopAbs_WIRE,TopAbs_SHELL,TopAbs_SOLID
    from OCP.TopAbs import TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.ShapeFix import ShapeFix_Shell
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.gp import gp_Pnt
    from OCP.GeomLProp import GeomLProp_SLProps
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    from OCP.gp import gp_Pnt2d
    from OCP.BRepTools import BRepTools
    from OCP.BRepTools import BRepTools_WireExplorer
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Line
    from OCP.GeomLib import GeomLib_IsPlanarSurface
    from OCP.Poly import Poly_Triangulation,Poly_Triangle
    from OCP.BRep import BRep_Builder
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepBndLib import BRepBndLib
    from OCP.Bnd import Bnd_Box
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS_Compound
    from OCP.TopTools import TopTools_IndexedMapOfShape
    from OCP.STEPControl import STEPControl_Writer,STEPControl_AsIs
    from OCP.Interface import Interface_Static
    import OCP
    request=Path(sys.argv[1]).resolve(strict=True)
    job_request=json.loads(request.read_text(encoding='utf-8'))
    parameters=job_request['parameters']
    if job_request['action']=='repair_optical_mesh':
        from .mesh_repair import run
        run(request)
        return
    prepare=job_request['action']=='prepare_optical_regions'
    mesh=prepare and parameters.get('geometry_representation') in ('mesh','normal_mesh')
    normal_mesh=prepare and parameters.get('geometry_representation')=='normal_mesh'
    def entities(shape,kind):
        explorer=TopExp_Explorer(shape,kind)
        rows=[]
        while explorer.More():
            rows.append(explorer.Current())
            explorer.Next()
        return rows
    partition=parameters.get('region_roles',{'reference':'inner_lens_solid_reference','coating':'AL','transmitting':'IN'})
    reference_label=partition['reference'] if partition else None
    coating_label=partition['coating'] if partition else None
    transmitting_label=partition['transmitting'] if partition else None
    boundary_label=parameters.get('target_region','main_lens')
    extra_solids=parameters.get('additional_solid_regions', ['main_lens'] if parameters.get('include_main_lens') else [])
    shapes={}
    report={'backend':'independent OCCT topology inspection','version':OCP.__version__,
        'read_only':not prepare,'source_geometry_modified':False,'regions':{},'probe_points':[]}
    for region in parameters['regions']:
        path=request.parent/region['staged_name']
        if hashlib.sha256(path.read_bytes()).hexdigest()!=region['sha256']:
            raise ValueError('Staged topology input hash differs')
        reader=STEPControl_Reader()
        if reader.ReadFile(str(path))!=IFSelect_RetDone or reader.TransferRoots()<1:
            raise ValueError('Independent STEP read failed')
        shape=reader.OneShape()
        shapes[region['label']]=shape
        solids=entities(shape,TopAbs_SOLID)
        shells=entities(shape,TopAbs_SHELL)
        row={'faces':len(entities(shape,TopAbs_FACE)),'edges_with_references':len(entities(shape,TopAbs_EDGE)),
            'solids':len(solids),'shells':len(shells),'shell_closed_flags':[s.Closed() for s in shells],
            'valid':BRepCheck_Analyzer(shape).IsValid(),'source_sha256':region['sha256']}
        trimmed_box=Bnd_Box()
        BRepBndLib.AddOptimal_s(shape,trimmed_box,False,False)
        tx0,ty0,tz0,tx1,ty1,tz1=trimmed_box.Get()
        row['trimmed_bounds_mm']=[tx0,tx1,ty0,ty1,tz0,tz1]
        if solids:
            props=GProp_GProps()
            BRepGProp.VolumeProperties_s(shape,props)
            row['volume_mm3']=props.Mass()
            classifiers=[BRepClass3d_SolidClassifier(TopoDS.Solid_s(solid)) for solid in solids]
            row['probe_classifications']=[]
            for xyz in parameters['probe_points_mm']:
                states=[]
                for classifier in classifiers:
                    classifier.Perform(gp_Pnt(*xyz),1e-6)
                    states.append(str(classifier.State()))
                row['probe_classifications'].append({'point_mm':xyz,'states':states})
            if region['label']==boundary_label and 0<len(parameters['probe_points_mm'])<=8:
                faces=entities(shape,TopAbs_FACE)
                bounds=[]
                for face in faces:
                    box=Bnd_Box()
                    BRepBndLib.AddOptimal_s(face,box,False,False)
                    bounds.append(box.Get())
                row['nearby_boundary_faces']=[]
                for xyz in parameters['probe_points_mm']:
                    probe=gp_Pnt(*xyz)
                    vertex=BRepBuilderAPI_MakeVertex(probe).Vertex()
                    candidates=[]
                    for face_index,(face,box) in enumerate(zip(faces,bounds)):
                        box_distance=sum(max(box[j]-xyz[j],0,xyz[j]-box[j+3])**2 for j in range(3))**.5
                        if box_distance>.1:
                            continue
                        distance=BRepExtrema_DistShapeShape(vertex,face)
                        if not distance.IsDone() or distance.NbSolution()<1 or distance.Value()>.1:
                            continue
                        closest=distance.PointOnShape2(1)
                        candidate={'face_index_zero_based':face_index,'distance_mm':distance.Value(),
                            'closest_point_mm':[closest.X(),closest.Y(),closest.Z()],
                            'surface_type':str(BRepAdaptor_Surface(TopoDS.Face_s(face)).GetType()),
                            'face_orientation':str(face.Orientation())}
                        try:
                            model=reader.WS().Model()
                            candidate['source_STEP_model_rank']=None
                            candidate['source_STEP_entity_number']=None
                            for mapping_mode in (1,0,2,3):
                                entity=reader.WS().TransferReader().EntityFromShapeResult(face,mapping_mode)
                                rank=model.Number(entity) if entity is not None else 0
                                if rank>0:
                                    candidate['source_STEP_model_rank']=rank
                                    label=model.IdentLabel(entity)
                                    candidate['source_STEP_entity_number']=label if label>0 else None
                                    candidate['STEP_reverse_mapping_mode']=mapping_mode
                                    break
                        except Exception as error:
                            candidate['STEP_entity_mapping_error']=str(error)
                        try:
                            surface=BRep_Tool.Surface_s(TopoDS.Face_s(face))
                            projection=GeomAPI_ProjectPointOnSurf(closest,surface)
                            u,v=projection.LowerDistanceParameters()
                            normal_info=GeomLProp_SLProps(surface,u,v,1,1e-8)
                            if normal_info.IsNormalDefined():
                                normal=normal_info.Normal()
                                sign=-1 if face.Orientation()==TopAbs_REVERSED else 1
                                direction=[sign*normal.X(),sign*normal.Y(),sign*normal.Z()]
                                candidate['oriented_normal']=direction
                                sides=[]
                                for offset in (.001,.01,.1):
                                    states=[]
                                    for side in (-1,1):
                                        point=gp_Pnt(*[candidate['closest_point_mm'][j]+side*offset*direction[j] for j in range(3)])
                                        classifiers[0].Perform(point,1e-6)
                                        states.append(str(classifiers[0].State()))
                                    sides.append({'offset_mm':offset,'negative_normal_side':states[0],'positive_normal_side':states[1]})
                                candidate['normal_side_classifications']=sides
                        except Exception as error:
                            candidate['normal_inspection_error']=str(error)
                        candidates.append(candidate)
                    candidates.sort(key=lambda r:(r['distance_mm'],r['face_index_zero_based']))
                    row['nearby_boundary_faces'].append({'point_mm':xyz,'candidate_distance_limit_mm':.1,
                        'candidate_count_within_0_01mm':sum(r['distance_mm']<=.01 for r in candidates),
                        'candidate_count':len(candidates),'candidates':candidates[:30],
                        'candidates_truncated':len(candidates)>30,
                        'scope':'nearest original trimmed boundary faces; not an identity mapping to CATIA live BRep names'})
        if region['label']==boundary_label and parameters.get('local_boundary_checks'):
            from .boundary_geometry import inspect_local_boundary
            row['local_boundary_checks']=inspect_local_boundary(shape,parameters['probe_points_mm'])
            if parameters.get('reference_mesh'):
                from .boundary_mesh import inspect_local_mesh
                spec=parameters['reference_mesh']
                mesh_path=request.parent/spec['staged_name']
                if hashlib.sha256(mesh_path.read_bytes()).hexdigest()!=spec['sha256']:
                    raise ValueError('Reference mesh snapshot hash differs')
                row['local_mesh_checks']=inspect_local_mesh(mesh_path,parameters['probe_points_mm'],parameters.get('reference_triangle_indices'))
        report['regions'][region['label']]=row
        print('READ_REGION',region['label'],json.dumps(row),flush=True)
    report['region_roles']=partition
    report['target_region']=boundary_label
    if not partition:
        if prepare:
            raise ValueError('Partition preparation requires explicit region roles')
        (request.parent/'engine-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print('LUCIDSHAPE_MCP_OK',flush=True)
        return
    reference=entities(shapes[reference_label],TopAbs_SOLID)
    if len(reference)==1:
        solid=TopoDS.Solid_s(reference[0])
        for xyz in parameters['probe_points_mm']:
            classifier=BRepClass3d_SolidClassifier(solid,gp_Pnt(*xyz),1e-6)
            report['probe_points'].append({'point_mm':xyz,'state':str(classifier.State())})
    def face_metrics(shape):
        rows=[]
        for index,face in enumerate(entities(shape,TopAbs_FACE)):
            props=GProp_GProps()
            BRepGProp.SurfaceProperties_s(face,props)
            center=props.CentreOfMass()
            rows.append({'index':index,'area_mm2':props.Mass(),'center_mm':[center.X(),center.Y(),center.Z()]})
        return rows
    original=face_metrics(shapes[reference_label])
    compared=face_metrics(shapes[transmitting_label])
    unmatched=[]
    matched=set()
    for row in compared:
        candidates=[r for r in original if sum((a-b)**2 for a,b in zip(row['center_mm'],r['center_mm']))<1e-10 and abs(row['area_mm2']-r['area_mm2'])<max(1e-6,r['area_mm2']*1e-6)]
        if len(candidates)==1:
            matched.add(candidates[0]['index'])
        else:
            unmatched.append(row)
    report['face_metric_comparison']={'method':'surface area and centroid only; not proof of geometric equality',
        'IN_matching_reference_face_count':len(compared)-len(unmatched),'distinct_reference_faces_matched':len(matched),
        'IN_unmatched_faces':unmatched,'reference_faces_not_matched':[r for r in original if r['index'] not in matched]}
    for key,names in (('IN_sewing',[transmitting_label]),('AL_IN_sewing',[coating_label,transmitting_label])):
        sewing=BRepBuilderAPI_Sewing(1e-6)
        for name in names:
            sewing.Add(shapes[name])
        sewing.Perform()
        sewed=sewing.SewedShape()
        report[key]={'tolerance_mm':1e-6,'free_edges':sewing.NbFreeEdges(),
            'multiple_edges':sewing.NbMultipleEdges(),'faces':len(entities(sewed,TopAbs_FACE)),
            'shell_closed_flags':[s.Closed() for s in entities(sewed,TopAbs_SHELL)],
            'valid':BRepCheck_Analyzer(sewed).IsValid()}
        if key=='AL_IN_sewing' and sewing.NbFreeEdges()==0:
            candidate_shells=entities(sewed,TopAbs_SHELL)
            if len(candidate_shells)==1:
                fixer=ShapeFix_Shell()
                fixer.FixFaceOrientation(TopoDS.Shell_s(candidate_shells[0]),True,False)
                fixed=fixer.Shell()
                candidate=BRepBuilderAPI_MakeSolid(fixed).Solid()
                props=GProp_GProps()
                BRepGProp.VolumeProperties_s(candidate,props)
                if props.Mass()<0:
                    candidate.Reverse()
                    BRepGProp.VolumeProperties_s(candidate,props)
                report['oriented_AL_IN_candidate']={'valid_solid':BRepCheck_Analyzer(candidate).IsValid(),
                    'closed_shell_flag':fixed.Closed(),'signed_volume_mm3':props.Mass(),'probe_points':[]}
                for xyz in parameters['probe_points_mm']:
                    classifier=BRepClass3d_SolidClassifier(candidate,gp_Pnt(*xyz),1e-6)
                    report['oriented_AL_IN_candidate']['probe_points'].append({'point_mm':xyz,'state':str(classifier.State())})
                if prepare:
                    if not BRepCheck_Analyzer(candidate).IsValid() or not fixed.Closed() or props.Mass()<=0:
                        raise ValueError('Cannot prepare an oriented valid closed inner-lens shell')
                    fixed_faces=entities(candidate,TopAbs_FACE)
                    if mesh:
                        mesher=BRepMesh_IncrementalMesh(candidate,parameters['mesh_deflection_mm'],False,
                            math.radians(parameters['mesh_angle_deg']),True)
                        if not mesher.IsDone():
                            raise ValueError('Inner-lens meshing did not finish')
                        print('INNER_MESH_READY',flush=True)
                    assigned=set()
                    exports={}
                    Interface_Static.SetCVal_s('write.step.schema','AP214IS')
                    roles=(coating_label,transmitting_label,*extra_solids)
                    for role in roles:
                        selected=[]
                        original_faces=entities(shapes[role],TopAbs_FACE)
                        if role in extra_solids:
                            solids=entities(shapes[role],TopAbs_SOLID)
                            shells=entities(shapes[role],TopAbs_SHELL)
                            if len(solids)!=1 or len(shells)!=1 or not shells[0].Closed() or not BRepCheck_Analyzer(shapes[role]).IsValid():
                                raise ValueError('Main-lens mesh requires one valid closed source solid')
                            main_fixer=ShapeFix_Shell()
                            main_fixer.FixFaceOrientation(TopoDS.Shell_s(shells[0]),True,False)
                            main_solid=BRepBuilderAPI_MakeSolid(main_fixer.Shell()).Solid()
                            main_props=GProp_GProps()
                            BRepGProp.VolumeProperties_s(main_solid,main_props)
                            if main_props.Mass()<0:
                                main_solid.Reverse()
                            selected=entities(main_solid,TopAbs_FACE)
                            face_map=TopTools_IndexedMapOfShape()
                            for face in selected:
                                face_map.Add(face)
                            if len(selected)!=len(original_faces) or face_map.Extent()!=len(original_faces) or any(face_map.FindIndex(o)==0 for o in original_faces):
                                raise ValueError('Main-lens preparation changed source face identities')
                            main_mesher=BRepMesh_IncrementalMesh(main_solid,parameters['mesh_deflection_mm'],False,
                                math.radians(parameters['mesh_angle_deg']),True)
                            if not main_mesher.IsDone():
                                raise ValueError('Main-lens mesh did not finish')
                            print('MAIN_LENS_MESH_READY',flush=True)
                        for original_face in original_faces if role not in extra_solids else []:
                            mapped=sewing.Modified(original_face) if sewing.IsModified(original_face) else original_face
                            if sewing.IsModifiedSubShape(mapped):
                                mapped=sewing.ModifiedSubShape(mapped)
                            matches=[(i,face) for i,face in enumerate(fixed_faces) if face.IsSame(mapped)]
                            if len(matches)!=1 or matches[0][0] in assigned:
                                raise ValueError('Cannot preserve unique AL/IN face identity while orienting the shell')
                            assigned.add(matches[0][0])
                            selected.append(matches[0][1])
                        builder=BRep_Builder()
                        compound=TopoDS_Compound()
                        builder.MakeCompound(compound)
                        for face in selected:
                            builder.Add(compound,face)
                        triangle_count=0
                        path=request.parent/(role+('-prepared.lug' if normal_mesh else '-prepared.stl' if mesh else '-prepared.stp'))
                        sampled_normal_error=0
                        normal_limits=0
                        max_node_surface_distance=0
                        max_source_boundary_tolerance=0
                        normal_orientation_corrections=0
                        reconstructed_uv_vertices=0
                        locally_remeshed_faces=0
                        exact_boundary_triangles=0
                        degenerate_triangles=0
                        native_float32_degenerate_triangles=0
                        source_bounds=Bnd_Box()
                        BRepBndLib.AddOptimal_s(compound,source_bounds,False,False)
                        bx0,by0,bz0,bx1,by1,bz1=source_bounds.Get()
                        source_trimmed_bounds=[bx0,bx1,by0,by1,bz0,bz1]
                        if normal_mesh:
                            vertices=[]
                            normals=[]
                            facets=[]
                            for face in selected:
                                location=TopLoc_Location()
                                tri=BRep_Tool.Triangulation_s(TopoDS.Face_s(face),location)
                                if tri is None or tri.NbTriangles()<1:
                                    local_mesh=BRepMesh_IncrementalMesh(face,min(parameters['mesh_deflection_mm'],1e-5),False,
                                        math.radians(parameters['mesh_angle_deg']),False)
                                    tri=BRep_Tool.Triangulation_s(TopoDS.Face_s(face),location)
                                    locally_remeshed_faces+=1
                                    if tri is None or tri.NbTriangles()<1:
                                        face_props=GProp_GProps()
                                        BRepGProp.SurfaceProperties_s(face,face_props)
                                        boundary=BRepTools_WireExplorer(BRepTools.OuterWire_s(TopoDS.Face_s(face)))
                                        boundary_points=[]
                                        line_edges=True
                                        while boundary.More():
                                            line_edges &= BRepAdaptor_Curve(boundary.Current()).GetType()==GeomAbs_Line
                                            boundary_points.append(BRep_Tool.Pnt_s(boundary.CurrentVertex()))
                                            boundary.Next()
                                        surface=BRep_Tool.Surface_s(TopoDS.Face_s(face))
                                        plane=GeomLib_IsPlanarSurface(surface,1e-9)
                                        if len(entities(face,TopAbs_WIRE))!=1 or len(boundary_points)!=3 or not line_edges or not plane.IsPlanar():
                                            raise ValueError(f'Cannot preserve unmeshed face by exact triangular boundary: role={role}, face_index={selected.index(face)}, area_mm2={face_props.Mass()}, boundary_vertices={len(boundary_points)}, line_edges={line_edges}, planar={plane.IsPlanar()}')
                                        tri=Poly_Triangulation(3,1,False)
                                        for index,point in enumerate(boundary_points,1):
                                            tri.SetNode(index,point)
                                        a=[boundary_points[1].Coord(i)-boundary_points[0].Coord(i) for i in (1,2,3)]
                                        b=[boundary_points[2].Coord(i)-boundary_points[0].Coord(i) for i in (1,2,3)]
                                        cross=(a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
                                        center=gp_Pnt(*[sum(p.Coord(i) for p in boundary_points)/3 for i in (1,2,3)])
                                        orientation_projection=GeomAPI_ProjectPointOnSurf(center,surface)
                                        if orientation_projection.NbPoints()<1:
                                            raise ValueError('Cannot establish source parametric orientation for exact boundary triangle')
                                        pu,pv=orientation_projection.LowerDistanceParameters()
                                        orientation=GeomLProp_SLProps(surface,pu,pv,1,1e-18)
                                        if not orientation.IsNormalDefined():
                                            raise ValueError('Source parametric normal is undefined on an exact boundary triangle')
                                        normal=orientation.Normal()
                                        opposite=sum(v*normal.Coord(i+1) for i,v in enumerate(cross))<0
                                        tri.SetTriangle(1,Poly_Triangle(1,3,2) if opposite else Poly_Triangle(1,2,3))
                                        location=TopLoc_Location()
                                        exact_boundary_triangles+=1
                                surface=BRep_Tool.Surface_s(TopoDS.Face_s(face))
                                face_tolerance=BRep_Tool.Tolerance_s(TopoDS.Face_s(face))
                                boundary_tolerance=max([face_tolerance]+[
                                    BRep_Tool.Tolerance_s(TopoDS.Edge_s(edge)) for edge in entities(face,TopAbs_EDGE)]+[
                                    BRep_Tool.Tolerance_s(TopoDS.Vertex_s(vertex)) for vertex in entities(face,TopAbs_VERTEX)])
                                max_source_boundary_tolerance=max(max_source_boundary_tolerance,boundary_tolerance)
                                umin,umax,vmin,vmax=BRepTools.UVBounds_s(TopoDS.Face_s(face))
                                transform=location.Transformation()
                                uv_nodes={}
                                if not tri.HasUVNodes():
                                    for index in range(1,tri.NbNodes()+1):
                                        point=tri.Node(index).Transformed(transform)
                                        projection=GeomAPI_ProjectPointOnSurf(point,surface)
                                        if projection.NbPoints()<1:
                                            raise ValueError('No source-surface projection for a mesh node without UV')
                                        u,v=projection.LowerDistanceParameters()
                                        uv_nodes[index]=gp_Pnt2d(u,v)
                                        reconstructed_uv_vertices+=1
                                get_node_uv=lambda index:uv_nodes[index] if uv_nodes else tri.UVNode(index)
                                offset=len(vertices)
                                reversed_face=face.Orientation()==TopAbs_REVERSED
                                singular_nodes={}
                                for node_index in range(1,tri.NbNodes()+1):
                                    point=tri.Node(node_index).Transformed(transform)
                                    uv=get_node_uv(node_index)
                                    surface_point=surface.Value(uv.X(),uv.Y())
                                    max_node_surface_distance=max(max_node_surface_distance,surface_point.Distance(point))
                                    if surface_point.Distance(point)>parameters['mesh_deflection_mm']+boundary_tolerance:
                                        raise ValueError(f'Triangulation node exceeds mesh deflection and source boundary tolerance: distance_mm={surface_point.Distance(point)}, boundary_tolerance_mm={boundary_tolerance}')
                                    prop=GeomLProp_SLProps(surface,uv.X(),uv.Y(),1,1e-12)
                                    sign=-1 if reversed_face else 1
                                    vertices.append((point.X(),point.Y(),point.Z()))
                                    if prop.IsNormalDefined():
                                        n=prop.Normal()
                                        normals.append((sign*n.X(),sign*n.Y(),sign*n.Z()))
                                    else:
                                        # A pole can have different directional normal limits.
                                        # Duplicate that corner per incident triangle below.
                                        singular_nodes[node_index]=(uv,point,surface_point)
                                        normals.append(None)
                                for k in range(1,tri.NbTriangles()+1):
                                    ids=list(tri.Triangle(k).Get())
                                    if reversed_face:
                                        ids[1],ids[2]=ids[2],ids[1]
                                    points=[vertices[offset+i-1] for i in ids]
                                    a=[points[1][j]-points[0][j] for j in range(3)]
                                    b=[points[2][j]-points[0][j] for j in range(3)]
                                    geometric_normal=(a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
                                    if sum(v*v for v in geometric_normal)<1e-40:
                                        degenerate_triangles+=1
                                        continue
                                    uv=[get_node_uv(i) for i in ids]
                                    u=sum(p.X() for p in uv)/3
                                    v=sum(p.Y() for p in uv)/3
                                    corner_indices=[]
                                    for node_index in ids:
                                        if node_index not in singular_nodes:
                                            corner_indices.append(offset+node_index-1)
                                            continue
                                        node_uv,point,surface_point=singular_nodes[node_index]
                                        limit=None
                                        for delta in (1e-10,1e-9,1e-8,1e-7,1e-6,1e-5,1e-4,1e-3,.01,.1):
                                            pu=node_uv.X()+(u-node_uv.X())*delta
                                            pv=node_uv.Y()+(v-node_uv.Y())*delta
                                            trial=GeomLProp_SLProps(surface,pu,pv,1,1e-18)
                                            if trial.IsNormalDefined() and surface.Value(pu,pv).Distance(surface_point)<=parameters['mesh_deflection_mm']/100:
                                                limit=trial.Normal()
                                                break
                                        if limit is None:
                                            raise ValueError(f'No bounded triangle-direction normal limit: role={role}, node={node_index}, uv={(node_uv.X(),node_uv.Y())}, centroid_uv={(u,v)}, bounds={(umin,umax,vmin,vmax)}, exact_surface_distance_mm={surface.Value(node_uv.X(),node_uv.Y()).Distance(point)}, last_trial_distance_mm={surface.Value(pu,pv).Distance(point)}, last_normal_defined={trial.IsNormalDefined()}, surface_type={type(surface).__name__}')
                                        corner_indices.append(len(vertices))
                                        vertices.append((point.X(),point.Y(),point.Z()))
                                        normals.append((sign*limit.X(),sign*limit.Y(),sign*limit.Z()))
                                        normal_limits+=1
                                    for c,index in enumerate(corner_indices):
                                        if sum(a*b for a,b in zip(normals[index],geometric_normal))<0:
                                            corner_indices[c]=len(vertices)
                                            vertices.append(vertices[index])
                                            normals.append(tuple(-v for v in normals[index]))
                                            normal_orientation_corrections+=1
                                    facets.append(tuple(corner_indices))
                                    if k%max(1,tri.NbTriangles()//100)==0:
                                        prop=GeomLProp_SLProps(surface,u,v,1,1e-12)
                                        if prop.IsNormalDefined():
                                            n=prop.Normal()
                                            sign=-1 if reversed_face else 1
                                            true=(sign*n.X(),sign*n.Y(),sign*n.Z())
                                            if sum(a*b for a,b in zip(true,geometric_normal))<0:
                                                true=tuple(-v for v in true)
                                            interp=[sum(normals[i][j] for i in corner_indices) for j in range(3)]
                                            length=math.sqrt(sum(x*x for x in interp))
                                            if length>1e-12:
                                                cosine=max(-1,min(1,sum(a*b/length for a,b in zip(true,interp))))
                                                sampled_normal_error=max(sampled_normal_error,math.degrees(math.acos(cosine)))
                            # Unreferenced pole slots need a finite placeholder in the file.
                            normals=[n if n is not None else (0,0,1) for n in normals]
                            triangle_count=len(facets)
                            vertex_bounds=[f(p[j] for p in vertices) for j in range(3) for f in (min,max)]
                            if triangle_count>2000000:
                                raise ValueError('Normal mesh exceeds two million triangles')
                            fmt=lambda row:'('+','.join(format(v,'.15g') for v in row)+')'
                            if role==transmitting_label and parameters.get('pc_normal_policy','analytic')=='geometric':
                                from .stl_geometry import write_explicit_facet_stl
                                path=request.parent/(transmitting_label+'-explicit-facet-normals.stl')
                                native_float32_degenerate_triangles=write_explicit_facet_stl(path,vertices,facets)
                            else:
                                with path.open('w',encoding='ascii',newline='\n') as stream:
                                    stream.write('#pragma NOPP\n@1=MeshSurface("'+role+' analytic-normal mesh",(1,1,1),(\n')
                                    stream.write(',\n'.join(fmt(row) for row in vertices))
                                    stream.write('\n),(\n')
                                    stream.write(',\n'.join(fmt(row) for row in normals))
                                    stream.write('\n),(),(\n')
                                    stream.write(',\n'.join(fmt(row) for row in facets))
                                    stream.write('\n));\n')
                            print('NORMAL_MESH_WRITTEN',role,len(vertices),triangle_count,path.stat().st_size,flush=True)
                        elif mesh:
                            with path.open('wb') as stream:
                                stream.write(b'Derived optical region; source role retained'.ljust(80,b' '))
                                stream.write(struct.pack('<I',0))
                                for face in selected:
                                    location=TopLoc_Location()
                                    tri=BRep_Tool.Triangulation_s(TopoDS.Face_s(face),location)
                                    if tri is None or tri.NbTriangles()<1:
                                        raise ValueError('A source face has no verified triangulation')
                                    transform=location.Transformation()
                                    for k in range(1,tri.NbTriangles()+1):
                                        ids=list(tri.Triangle(k).Get())
                                        if face.Orientation()==TopAbs_REVERSED:
                                            ids[1],ids[2]=ids[2],ids[1]
                                        points=[tri.Node(index).Transformed(transform) for index in ids]
                                        xyz=[(p.X(),p.Y(),p.Z()) for p in points]
                                        a=[xyz[1][i]-xyz[0][i] for i in range(3)]
                                        b=[xyz[2][i]-xyz[0][i] for i in range(3)]
                                        normal=[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]
                                        length=math.sqrt(sum(v*v for v in normal))
                                        if length<1e-20:
                                            continue
                                        values=[v/length for v in normal]+[v for p in xyz for v in p]
                                        stream.write(struct.pack('<12fH',*values,0))
                                        triangle_count+=1
                                        if triangle_count>4000000:
                                            raise ValueError('Derived region exceeds four million triangles')
                                stream.seek(80)
                                stream.write(struct.pack('<I',triangle_count))
                        else:
                            writer=STEPControl_Writer()
                            if writer.Transfer(compound,STEPControl_AsIs)!=IFSelect_RetDone:
                                raise ValueError('Prepared STEP transfer failed')
                            if writer.Write(str(path))!=IFSelect_RetDone:
                                raise ValueError('Prepared STEP write failed')
                        exports[role]={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                            'faces':len(selected),'bytes':path.stat().st_size,'triangles':triangle_count,
                            'singular_normal_limits':normal_limits,'sampled_centroid_normal_error_deg':sampled_normal_error,
                            'max_node_surface_distance_mm':max_node_surface_distance,
                            'max_source_boundary_tolerance_mm':max_source_boundary_tolerance,
                            'source_trimmed_bounds_mm':source_trimmed_bounds,
                            'vertex_bounds_mm':vertex_bounds if normal_mesh else None,
                            'normal_orientation_corrections':normal_orientation_corrections,
                            'reconstructed_uv_vertices':reconstructed_uv_vertices,
                            'locally_remeshed_faces':locally_remeshed_faces,
                            'exact_boundary_triangles':exact_boundary_triangles,
                            'degenerate_triangles_skipped':degenerate_triangles,
                            'native_float32_degenerate_triangles':native_float32_degenerate_triangles}
                    if len(assigned)!=len(fixed_faces):
                        raise ValueError('Prepared shell contains faces without a verified optical role')
                    source_manifest=json.loads((request.parent/'source-manifest.json').read_text(encoding='utf-8'))
                    for row in source_manifest['regions']:
                        if row['label'] in exports:
                            derived=exports[row['label']]
                            row.update(path=derived['path'],sha256=derived['sha256'],bytes=derived['bytes'],
                                native_import_verified=False,all_geometry_entities_preserved_verbatim=False,
                                preparation='OCCT sewing at 1e-6 mm and face orientation; verified AL/IN role identity; AP214 export')
                            if mesh:
                                row['preparation']='Triangulation of sewn oriented AL+IN boundary, all source-face roles retained; approximation requires convergence checks'
                                row['mesh']={'triangles':derived['triangles'],
                                    'deflection_mm':parameters['mesh_deflection_mm'],'angular_deflection_deg':parameters['mesh_angle_deg'],
                                    'has_analytic_vertex_normals':normal_mesh and (row['label']!=transmitting_label or parameters.get('pc_normal_policy','analytic')!='geometric'),
                                    'sampled_centroid_normal_error_deg':derived['sampled_centroid_normal_error_deg'] if row['label']!=transmitting_label or parameters.get('pc_normal_policy','analytic')!='geometric' else None}
                                row['mesh']['normal_policy']='geometric facet normals, diagnostic approximation' if row['label']==transmitting_label and parameters.get('pc_normal_policy','analytic')=='geometric' else 'analytic vertex normals' if normal_mesh else 'geometric facet normals'
                                row['mesh']['native_float32_degenerate_triangles']=derived['native_float32_degenerate_triangles']
                                row['mesh']['singular_normal_limits']=derived['singular_normal_limits']
                                row['mesh']['max_node_surface_distance_mm']=derived['max_node_surface_distance_mm']
                                row['mesh']['max_source_boundary_tolerance_mm']=derived['max_source_boundary_tolerance_mm']
                                row['mesh']['node_distance_acceptance']='mesh deflection plus original source face/edge/vertex tolerance; not a global optical error bound'
                                for field in ('source_trimmed_bounds_mm','vertex_bounds_mm','normal_orientation_corrections','degenerate_triangles_skipped','reconstructed_uv_vertices','locally_remeshed_faces','exact_boundary_triangles'):
                                    row['mesh'][field]=derived[field]
                    source_manifest.update(parent_manifest=parameters['region_manifest_path'],
                        derived_geometry_requires_native_revalidation=True,
                        preparation_report=str(request.parent/'engine-result.json'))
                    manifest_path=request.parent/'prepared-region-manifest.json'
                    manifest_path.write_text(json.dumps(source_manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                    report['prepared_regions']=exports
                    report['prepared_manifest']=str(manifest_path)
                    report['preparation_tolerance_mm']=1e-6
                    report['optical_roles_preserved']=True
                    report['native_revalidation_required']=True
        print(key,json.dumps(report[key]),flush=True)
    (request.parent/'engine-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('LUCIDSHAPE_MCP_OK',flush=True)

if __name__=='__main__':
    main()
