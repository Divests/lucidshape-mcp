"""Bounded read-only OCCT checks of original trimmed faces near supplied points.

Called only inside the isolated MCP kernel worker. Face indices belong to this
STEP traversal, never to a live CATIA document. A local result is not a global
self-intersection or optical-accuracy certificate.
"""
import math


def inspect_local_boundary(shape,points):
    from OCP.TopAbs import TopAbs_FACE,TopAbs_EDGE,TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS,TopoDS_Compound
    from OCP.TopTools import TopTools_IndexedMapOfShape
    from OCP.BRep import BRep_Tool,BRep_Builder
    from OCP.BRepBndLib import BRepBndLib
    from OCP.Bnd import Bnd_Box
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.BRepAdaptor import BRepAdaptor_Curve,BRepAdaptor_Curve2d,BRepAdaptor_Surface
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Check
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.gp import gp_Pnt

    def entities(value,kind):
        explorer=TopExp_Explorer(value,kind)
        result=[]
        while explorer.More():
            result.append(explorer.Current())
            explorer.Next()
        return result

    faces=entities(shape,TopAbs_FACE)
    face_map=TopTools_IndexedMapOfShape()
    edge_map=TopTools_IndexedMapOfShape()
    edge_owners={}
    face_edges=[]
    selected=[]
    radius=.25
    for index,face in enumerate(faces):
        face_map.Add(face)
        box=Bnd_Box()
        BRepBndLib.AddOptimal_s(face,box,False,False)
        bounds=box.Get()
        nearby=any(sum(max(bounds[j]-xyz[j],0,xyz[j]-bounds[j+3])**2 for j in range(3))<=radius**2 for xyz in points)
        indices=[]
        for edge in entities(face,TopAbs_EDGE):
            edge_id=edge_map.Add(edge)-1
            indices.append(edge_id)
            edge_owners.setdefault(edge_id,set()).add(index)
        face_edges.append(indices)
        if nearby:
            selected.append(index)
    if len(selected)>128:
        raise ValueError('Local boundary face limit exceeded; narrow the probe set')
    compound=TopoDS_Compound()
    builder=BRep_Builder()
    builder.MakeCompound(compound)
    selected_edges=sorted({i for index in selected for i in face_edges[index]})
    rows=[]
    sample_errors=[]
    for index in selected:
        face=TopoDS.Face_s(faces[index])
        builder.Add(compound,face)
        props=GProp_GProps()
        BRepGProp.SurfaceProperties_s(face,props)
        edges=[]
        surface=BRepAdaptor_Surface(face,False)
        for edge_id in face_edges[index]:
            edge=TopoDS.Edge_s(edge_map.FindKey(edge_id+1))
            item={'edge_index_zero_based':edge_id,'owners_face_indices':sorted(edge_owners[edge_id]),
                'tolerance_mm':BRep_Tool.Tolerance_s(edge),'degenerated':BRep_Tool.Degenerated_s(edge),
                'same_parameter_flag':BRep_Tool.SameParameter_s(edge)}
            if not item['degenerated']:
                try:
                    curve=BRepAdaptor_Curve(edge)
                    uv_curve=BRepAdaptor_Curve2d(edge,face)
                    first,last=curve.FirstParameter(),curve.LastParameter()
                    samples=[]
                    for sample in range(17):
                        t=first+(last-first)*sample/16
                        uv=uv_curve.Value(t)
                        samples.append(curve.Value(t).Distance(surface.Value(uv.X(),uv.Y())))
                    item['max_sampled_curve_pcurve_deviation_mm']=max(samples)
                    item['samples_exceed_edge_tolerance']=max(samples)>item['tolerance_mm']+1e-9
                    length_props=GProp_GProps()
                    BRepGProp.LinearProperties_s(edge,length_props)
                    item['length_mm']=length_props.Mass()
                except Exception as error:
                    item['sampling_error']=str(error)
                    sample_errors.append({'face_index':index,'edge_index':edge_id,'error':str(error)})
            edges.append(item)
        rows.append({'face_index_zero_based':index,'area_mm2':props.Mass(),
            'face_tolerance_mm':BRep_Tool.Tolerance_s(face),'edges':edges})
    print('LOCAL_BOUNDARY_SELECTED',len(selected),'faces',len(selected_edges),'edges',flush=True)
    check=BRepAlgoAPI_Check(compound,True,True)
    problems=[]
    for problem in check.Result():
        groups=[]
        for faulty in (problem.GetFaultyShapes1(),problem.GetFaultyShapes2()):
            group=[]
            for value in faulty:
                face_id=face_map.FindIndex(value)-1
                edge_id=edge_map.FindIndex(value)-1
                group.append({'type':str(value.ShapeType()),'face_index_zero_based':face_id if face_id>=0 else None,
                    'edge_index_zero_based':edge_id if edge_id>=0 else None})
            groups.append(group)
        problems.append({'status':str(problem.GetCheckStatus()),'faulty_shapes':groups})
    nearest_edges=[]
    for xyz in points:
        vertex=BRepBuilderAPI_MakeVertex(gp_Pnt(*xyz)).Vertex()
        distances=[]
        for edge_id in selected_edges:
            edge=edge_map.FindKey(edge_id+1)
            check_distance=BRepExtrema_DistShapeShape(vertex,edge)
            if check_distance.IsDone() and check_distance.NbSolution()>0:
                p=check_distance.PointOnShape2(1)
                distances.append({'edge_index_zero_based':edge_id,'distance_mm':check_distance.Value(),
                    'closest_point_mm':[p.X(),p.Y(),p.Z()],'owners_face_indices':sorted(edge_owners[edge_id])})
        distances.sort(key=lambda row:row['distance_mm'])
        nearest_edges.append({'point_mm':xyz,'nearest_edges':distances[:8]})
    return {'scope':'read-only local original-STEP checks; no geometry repair; not a global certificate',
        'selection':'trimmed face bounding box within radius of any input point; full selected faces checked',
        'selection_radius_mm':radius,'selected_face_indices_zero_based':selected,
        'global_face_reference_count':len(faces),'global_unique_topological_face_count':face_map.Extent(),
        'global_unique_edge_count':edge_map.Extent(),
        'global_non_degenerate_edges_not_owned_by_two_distinct_faces':[
            {'edge_index_zero_based':i,'owners_face_indices':sorted(owners)} for i,owners in edge_owners.items()
            if len(owners)!=2 and not BRep_Tool.Degenerated_s(TopoDS.Edge_s(edge_map.FindKey(i+1)))],
        'local_BRepAlgoAPI_Check_valid':check.IsValid(),'local_check_problems':problems,
        'local_faces':rows,'curve_pcurve_sample_count_per_edge':17,'sampling_errors':sample_errors,
        'nearest_trim_edges':nearest_edges,
        'limitations':['Local checks do not certify unselected faces or geometrically identical distinct faces.',
            'Seam edges can have two pcurves; sampled first pcurve is not an exhaustive trim proof.',
            'Sampled deviations alone do not bound the maximum deviation between samples.']}
