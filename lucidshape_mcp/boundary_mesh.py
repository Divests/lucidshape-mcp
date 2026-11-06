"""Local STL embedding checks in the isolated MCP geometry worker."""
import hashlib
import math
import struct
from .stl_geometry import inspect_stl


def inspect_local_mesh(path,points,reference_triangle_indices=None):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon,BRepBuilderAPI_MakeFace,BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_VERTEX
    from OCP.BRep import BRep_Tool
    from OCP.TopoDS import TopoDS
    from OCP.gp import gp_Pnt
    basic=inspect_stl(path)
    data=path.read_bytes()
    triangles=[]
    count=struct.unpack_from('<I',data,80)[0] if len(data)>=84 else 0
    if len(data)==84+50*count and count:
        for index in range(count):
            values=struct.unpack_from('<12fH',data,84+index*50)
            triangles.append(tuple(tuple(values[j:j+3]) for j in (3,6,9)))
    else:
        corners=[]
        for line in data.decode('ascii').splitlines():
            words=line.split()
            if words and words[0].lower()=='vertex':
                corners.append(tuple(map(float,words[1:])))
                if len(corners)==3:
                    triangles.append(tuple(corners))
                    corners=[]
    selected=[]
    vertices=[BRepBuilderAPI_MakeVertex(gp_Pnt(*p)).Vertex() for p in points]
    probe_distances=[math.inf]*len(points)
    radius=.1
    def bounds(tri):
        return tuple(min(p[j] for p in tri) for j in range(3))+tuple(max(p[j] for p in tri) for j in range(3))
    seeds=reference_triangle_indices or []
    if any(i>=len(triangles) for i in seeds):
        raise ValueError('Reference triangle index is outside this exact STL')
    seed_boxes=[bounds(triangles[i]) for i in seeds]
    for index,tri in enumerate(triangles):
        box=bounds(tri)
        near_point=any(sum(max(box[j]-xyz[j],0,xyz[j]-box[j+3])**2 for j in range(3))<=radius**2 for xyz in points)
        near_seed=any(all(box[j]<=b[j+3]+1e-7 and b[j]<=box[j+3]+1e-7 for j in range(3)) for b in seed_boxes)
        if not near_point and not near_seed:
            continue
        polygon=BRepBuilderAPI_MakePolygon()
        for p in tri:
            polygon.Add(gp_Pnt(*p))
        polygon.Close()
        face=BRepBuilderAPI_MakeFace(polygon.Wire()).Face()
        distances=[BRepExtrema_DistShapeShape(vertex,face) for vertex in vertices]
        for probe_index,distance in enumerate(distances):
            if distance.IsDone() and distance.NbSolution()>0:
                probe_distances[probe_index]=min(probe_distances[probe_index],distance.Value())
        if near_seed or any(d.IsDone() and d.NbSolution()>0 and d.Value()<=radius for d in distances):
            selected.append((index,tri,face,box))
    if len(selected)>(512 if seeds else 128):
        raise ValueError('Local STL selection exceeds bounded triangle count')
    print('LOCAL_MESH_SELECTED',len(selected),'triangles',flush=True)
    intersections=[]
    errors=[]
    checked=0
    for ordinal,(i,first,face_a,box_a) in enumerate(selected):
        for j,second,face_b,box_b in selected[ordinal+1:]:
            if any(box_a[k]>box_b[k+3]+1e-7 or box_b[k]>box_a[k+3]+1e-7 for k in range(3)):
                continue
            shared=set(first)&set(second)
            if len(shared)>=2:
                continue
            checked+=1
            try:
                section=BRepAlgoAPI_Section(face_a,face_b,False)
                section.Build()
                if not section.IsDone():
                    raise ValueError('Triangle intersection algorithm did not complete')
                explorer=TopExp_Explorer(section.Shape(),TopAbs_VERTEX)
                found=[]
                while explorer.More():
                    p=BRep_Tool.Pnt_s(TopoDS.Vertex_s(explorer.Current()))
                    xyz=(p.X(),p.Y(),p.Z())
                    if not shared or min(math.dist(xyz,v) for v in shared)>1e-6:
                        if not any(math.dist(xyz,old)<1e-7 for old in found):
                            found.append(xyz)
                    explorer.Next()
                if found:
                    intersections.append({'triangle_indices_zero_based':[i,j],'intersection_points_mm':found,
                        'shared_vertex_count':len(shared)})
            except Exception as error:
                errors.append({'triangle_indices_zero_based':[i,j],'error':str(error)})
    def sub(a,b):return tuple(x-y for x,y in zip(a,b))
    def dot(a,b):return sum(x*y for x,y in zip(a,b))
    def cross(a,b):return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
    def intersections_from(start,direction,maximum=math.inf):
        hits=[]
        for index,tri in enumerate(triangles):
            a,b,c=tri
            e1,e2=sub(b,a),sub(c,a)
            h=cross(direction,e2)
            determinant=dot(e1,h)
            if abs(determinant)<1e-14:
                continue
            s=sub(start,a)
            u=dot(s,h)/determinant
            q=cross(s,e1)
            v=dot(direction,q)/determinant
            distance=dot(e2,q)/determinant
            if u>=-1e-10 and v>=-1e-10 and u+v<=1+1e-10 and 1e-7<distance<maximum:
                normal=cross(e1,e2)
                normal_length=math.sqrt(dot(normal,normal))
                hits.append({'triangle_index_zero_based':index,'distance_mm':distance,
                    'point_mm':[start[k]+direction[k]*distance for k in range(3)],
                    'outward_normal_dot_direction':dot(normal,direction)/normal_length})
        hits.sort(key=lambda r:r['distance_mm'])
        return hits
    classifications=[]
    for probe_index,point in enumerate(points):
        rays=[]
        for vector in ((.891123,.327913,.413731),(.123456,.93817,.3211),(.643,.261,.902)):
            magnitude=math.sqrt(dot(vector,vector))
            hits=intersections_from(point,tuple(v/magnitude for v in vector))
            distinct=[]
            for hit in hits:
                if not distinct or abs(hit['distance_mm']-distinct[-1]['distance_mm'])>1e-9:
                    distinct.append(hit)
            rays.append({'crossings':len(distinct),'odd_crossings':bool(len(distinct)%2),
                'oriented_crossing_sum':sum(1 if h['outward_normal_dot_direction']>0 else -1 for h in distinct)})
        state='IN' if all(r['odd_crossings'] and r['oriented_crossing_sum']==1 for r in rays) else 'OUT' if all(not r['odd_crossings'] and r['oriented_crossing_sum']==0 for r in rays) else 'AMBIGUOUS'
        if probe_distances[probe_index]<=1e-7:
            state='ON_OR_NEAR'
        classifications.append({'point_mm':point,'three_direction_state':state,'ray_checks':rays,
            'nearest_selected_surface_distance_mm':probe_distances[probe_index] if math.isfinite(probe_distances[probe_index]) else None})
    ray_hits=[]
    if len(points)>=2 and math.dist(points[0],points[1])>1e-6:
        start=points[0]
        vector=sub(points[1],start)
        length=math.sqrt(dot(vector,vector))
        direction=tuple(v/length for v in vector)
        ray_hits=intersections_from(start,direction,length+.1)
    return {'sha256':basic['sha256'],'basic_topology':basic,'selection_radius_mm':radius,
        'selected_triangle_indices_zero_based':[row[0] for row in selected],
        'checked_nonadjacent_bbox_overlapping_pairs':checked,'intersections':intersections,'pair_check_errors':errors,
        'local_incoming_line_hits':ray_hits,'probe_classifications':classifications,
        'reference_seed_triangle_indices_zero_based':seeds,
        'scope':'Local points plus all seed-overlapping triangle boxes; shared-edge and shared-vertex-only contacts excluded; not a global mesh certificate',
        'limitations':['Shared-edge coplanar overlap is not tested here.',
            'Floating-point section tolerances and grazing contacts require interpretation; originals are never modified.']}
