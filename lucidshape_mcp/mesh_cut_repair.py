"""Corefine a bounded self-intersection component and retain its outer envelope.

Original triangle planes are retained. New vertices are intersections on those
planes. Positive oriented winding classifies each side; uncertain decisions
fail instead of silently dropping a patch. Output still requires independent
closure, embedding, CAD-side and native optical checks.
"""
import math
from collections import defaultdict
from .mesh_repair import normal

def sub(a,b):return tuple(x-y for x,y in zip(a,b))
def dot(a,b):return sum(x*y for x,y in zip(a,b))
def cross(a,b):return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
def box(tri):return tuple(min(p[j] for p in tri) for j in range(3))+tuple(max(p[j] for p in tri) for j in range(3))
def overlaps(a,b,tolerance=1e-7):return all(a[k]<=b[k+3]+tolerance and b[k]<=a[k+3]+tolerance for k in range(3))

class TriangleIndex:
    def __init__(self,triangles):
        self.triangles=triangles
        self.bounds=[box(t) for t in triangles]
        def build(indices):
            extent=tuple(min(self.bounds[i][j] for i in indices) for j in range(3))+tuple(max(self.bounds[i][j+3] for i in indices) for j in range(3))
            if len(indices)<=12:
                return extent,indices
            axis=max(range(3),key=lambda j:extent[j+3]-extent[j])
            indices.sort(key=lambda i:self.bounds[i][axis]+self.bounds[i][axis+3])
            half=len(indices)//2
            return extent,(build(indices[:half]),build(indices[half:]))
        self.tree=build(list(range(len(triangles))))

    def candidates(self,bounds):
        def visit(node):
            extent,children=node
            if not overlaps(extent,bounds):return
            if isinstance(children,list):
                yield from (i for i in children if overlaps(self.bounds[i],bounds))
            else:
                for child in children:yield from visit(child)
        return list(visit(self.tree))

    def winding(self,p,direction):
        def ray_box(extent):
            lo,hi=1e-9,math.inf
            for j in range(3):
                if abs(direction[j])<1e-15:
                    if not extent[j]-1e-9<=p[j]<=extent[j+3]+1e-9:return False
                else:
                    a,b=(extent[j]-p[j])/direction[j],(extent[j+3]-p[j])/direction[j]
                    lo=max(lo,min(a,b));hi=min(hi,max(a,b))
                    if lo>hi:return False
            return True
        def visit(node):
            extent,children=node
            if not ray_box(extent):return
            if isinstance(children,list):yield from children
            else:
                for child in children:yield from visit(child)
        hits=[]
        for i in visit(self.tree):
            a,b,c=self.triangles[i]
            e1,e2=sub(b,a),sub(c,a)
            h=cross(direction,e2)
            det=dot(e1,h)
            if abs(det)<1e-15:continue
            s=sub(p,a);u=dot(s,h)/det
            q=cross(s,e1);v=dot(direction,q)/det
            distance=dot(e2,q)/det
            if u>=-1e-12 and v>=-1e-12 and u+v<=1+1e-12 and distance>1e-9:
                hits.append((distance,1 if dot(cross(e1,e2),direction)>0 else -1))
        hits.sort()
        distinct=[]
        for h in hits:
            if not distinct or abs(h[0]-distinct[-1][0])>1e-9:distinct.append(h)
        return sum(h[1] for h in distinct)

    def stable_winding(self,p):
        states=[]
        for direction in ((.891123,.327913,.413731),(.123456,.93817,.3211),(.643,.261,.902)):
            length=math.sqrt(dot(direction,direction))
            states.append(self.winding(p,tuple(v/length for v in direction)))
        if len(set(states))!=1:
            # Grazing a shared edge can make ray counting ambiguous. The
            # oriented solid-angle integral is an independent closed-mesh
            # winding calculation; accept only an unambiguous integer.
            value=self.solid_angle_winding(p)
            if abs(value-round(value))>1e-5:
                raise ValueError(f'Mesh winding remains ambiguous: ray states={states}, solid angle={value}')
            return round(value)
        return states[0]

    def solid_angle_winding(self,p):
        angles=[]
        for triangle in self.triangles:
            a,b,c=(sub(q,p) for q in triangle)
            la,lb,lc=(math.sqrt(dot(v,v)) for v in (a,b,c))
            if min(la,lb,lc)<1e-15:raise ValueError('Winding probe is on a mesh vertex')
            numerator=dot(a,cross(b,c))
            denominator=la*lb*lc+dot(a,b)*lc+dot(b,c)*la+dot(c,a)*lb
            angles.append(2*math.atan2(numerator,denominator))
        return math.fsum(angles)/(4*math.pi)


def split_union_candidate(triangles,seed_indices,center,radius,max_faces=128,construction_tolerance=5e-7):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon,BRepBuilderAPI_MakeFace
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section,BRepAlgoAPI_Splitter
    from OCP.TopTools import TopTools_ListOfShape
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_VERTEX,TopAbs_FACE,TopAbs_REVERSED
    from OCP.BRep import BRep_Tool
    from OCP.TopoDS import TopoDS
    from OCP.gp import gp_Pnt
    from OCP.TopLoc import TopLoc_Location
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    index=TriangleIndex(triangles)
    faces={}
    def face(i):
        if i not in faces:
            polygon=BRepBuilderAPI_MakePolygon()
            for p in triangles[i]:polygon.Add(gp_Pnt(*p))
            polygon.Close()
            faces[i]=BRepBuilderAPI_MakeFace(polygon.Wire()).Face()
        return faces[i]
    selected=set(seed_indices)
    queue=list(selected)
    pairs={}
    compared=set()
    def grow_intersections():
        while queue:
            i=queue.pop()
            for j in index.candidates(index.bounds[i]):
                pair=tuple(sorted((i,j)))
                if i==j or pair in compared or len(set(triangles[i])&set(triangles[j]))>=2:continue
                compared.add(pair)
                shared=set(triangles[i])&set(triangles[j])
                section=BRepAlgoAPI_Section(face(i),face(j),False)
                section.Build()
                if not section.IsDone():raise ValueError('Intersection component scan did not complete')
                explorer=TopExp_Explorer(section.Shape(),TopAbs_VERTEX)
                points=[]
                while explorer.More():
                    p=BRep_Tool.Pnt_s(TopoDS.Vertex_s(explorer.Current()))
                    xyz=(p.X(),p.Y(),p.Z())
                    if not shared or min(math.dist(xyz,v) for v in shared)>1e-6:
                        if not any(math.dist(xyz,v)<1e-8 for v in points):points.append(xyz)
                    explorer.Next()
                if points:
                    pairs[pair]=points
                    if j not in selected:
                        selected.add(j);queue.append(j)
                        if len(selected)>max_faces:raise ValueError('Intersection component exceeds local face limit')
    grow_intersections()
    print('CUT_COMPONENT',len(selected),'triangles',len(pairs),'intersection pairs',flush=True)
    required_radius=max((math.dist(p,center) for points in pairs.values() for p in points),default=0)
    print('CUT_REQUIRED_RADIUS_MM',required_radius,flush=True)
    if required_radius>radius:
        raise ValueError(f'Connected intersection requires radius {required_radius:.9g} mm, above requested {radius} mm')
    # Parallel faces in a folded strip can be internal without themselves
    # intersecting. Include one topological ring so the envelope closes.
    owners=defaultdict(set)
    vertex_owners=defaultdict(set)
    for i,triangle in enumerate(triangles):
        for a,b in zip(triangle,triangle[1:]+triangle[:1]):owners[tuple(sorted((a,b)))].add(i)
        for p in triangle:vertex_owners[p].add(i)
    initial_selected=sorted(selected)
    for i in initial_selected:
        for a,b in zip(triangles[i],triangles[i][1:]+triangles[i][:1]):
            selected.update(owners[tuple(sorted((a,b)))])
        for p in triangles[i]:
            if math.dist(p,center)<=radius:selected.update(vertex_owners[p])
    queue.extend(selected-set(initial_selected))
    grow_intersections()
    required_radius=max((math.dist(p,center) for points in pairs.values() for p in points),default=0)
    if required_radius>radius:
        raise ValueError(f'Seam intersection requires radius {required_radius:.9g} mm, above requested {radius} mm')
    if len(selected)>max_faces:raise ValueError('Intersection plus seam ring exceeds local face limit')
    print('CUT_WITH_SEAM_RING',len(selected),'triangles',flush=True)
    splitter=BRepAlgoAPI_Splitter()
    arguments=TopTools_ListOfShape()
    for i in sorted(selected):arguments.Append(face(i))
    splitter.SetArguments(arguments)
    splitter.SetNonDestructive(True)
    splitter.Build()
    if not splitter.IsDone():raise ValueError('Local triangle corefinement failed')
    registry=defaultdict(list)
    tolerance=min(1e-7,construction_tolerance)
    cap_tolerance=construction_tolerance
    def key(p):return tuple(math.floor(v/tolerance) for v in p)
    for p in {p for t in triangles for p in t}:registry[key(p)].append(p)
    added=[]
    max_weld=0.
    def canonical(p):
        nonlocal max_weld
        q=key(p)
        candidates=[]
        for dx in (-1,0,1):
            for dy in (-1,0,1):
                for dz in (-1,0,1):candidates.extend(registry.get((q[0]+dx,q[1]+dy,q[2]+dz),[]))
        near=[v for v in candidates if math.dist(v,p)<=tolerance]
        if near:
            result=min(near,key=lambda v:math.dist(v,p));max_weld=max(max_weld,math.dist(result,p))
            return result
        result=tuple(p);registry[q].append(result);added.append(result)
        return result
    replacement={}
    removed=[]
    preclassification_caps=[]
    decisions=[]
    orientation_changes=0
    max_normal_error=0.
    def triangle_area(t):
        return math.sqrt(dot(cross(sub(t[1],t[0]),sub(t[2],t[0])),cross(sub(t[1],t[0]),sub(t[2],t[0]))))/2
    for i in sorted(selected):
        modified=list(splitter.Modified(face(i)))
        pieces=modified or [face(i)]
        output=[]
        original_normal=normal(triangles[i])
        for piece in pieces:
            explorer=TopExp_Explorer(piece,TopAbs_FACE) if piece.ShapeType()!=TopAbs_FACE else None
            subfaces=[]
            if explorer is None:subfaces=[TopoDS.Face_s(piece)]
            else:
                while explorer.More():subfaces.append(TopoDS.Face_s(explorer.Current()));explorer.Next()
            for surface in subfaces:
                mesher=BRepMesh_IncrementalMesh(surface,1e-6,False,.01,False)
                location=TopLoc_Location()
                tri=BRep_Tool.Triangulation_s(surface,location)
                if tri is None or tri.NbTriangles()<1:
                    raise ValueError('A corefined surface piece could not be triangulated; refusing to discard it')
                for t in range(1,tri.NbTriangles()+1):
                    ids=tri.Triangle(t).Get()
                    raw=tuple(tuple(tri.Node(k).Transformed(location.Transformation()).Coord(j+1) for j in range(3)) for k in ids)
                    n=normal(raw)
                    if n is None:raise ValueError('Corefinement generated a degenerate triangle')
                    if dot(n,original_normal)<0:raw=(raw[0],raw[2],raw[1]);n=normal(raw)
                    longest=max(math.dist(raw[k],raw[(k+1)%3]) for k in range(3))
                    altitude=2*triangle_area(raw)/longest if longest else 0
                    if altitude<=cap_tolerance and triangle_area(raw)<1e-7:
                        preclassification_caps.append({'source_triangle_index':i,'vertices_mm':raw,'altitude_mm':altitude})
                        continue
                    center_point=tuple(sum(p[j] for p in raw)/3 for j in range(3))
                    # Probe less than the diagnosed nanometre-scale layer thickness.
                    eps=1e-7
                    negative=index.stable_winding(tuple(center_point[j]-eps*n[j] for j in range(3)))
                    positive=index.stable_winding(tuple(center_point[j]+eps*n[j] for j in range(3)))
                    keep=negative>0 and positive<=0
                    reverse=positive>0 and negative<=0
                    decisions.append({'source_triangle_index':i,'centroid_mm':center_point,
                        'negative_winding':negative,'positive_winding':positive,'kept':keep or reverse})
                    if keep or reverse:
                        if reverse:
                            raw=(raw[0],raw[2],raw[1]);orientation_changes+=1
                        value=tuple(canonical(p) for p in raw)
                        if normal(value) is None:raise ValueError('Welding collapsed a retained surface piece')
                        error=math.degrees(math.acos(max(-1,min(1,dot(normal(value),original_normal)))))
                        max_normal_error=max(max_normal_error,error)
                        output.append(value)
                    else:
                        removed.append({'source_triangle_index':i,'area_mm2':triangle_area(raw),
                            'negative_winding':negative,'positive_winding':positive,'vertices_mm':raw})
        replacement[i]=output
    # Insert intersection nodes into every incident original triangle to avoid
    # T-junctions. Fan centers stay on the unchanged original triangle plane.
    edge_nodes=defaultdict(set)
    intersection_nodes=list(added)
    for p in intersection_nodes:
        bounds=tuple(v-tolerance for v in p)+tuple(v+tolerance for v in p)
        for i in index.candidates(bounds):
            for a,b in zip(triangles[i],triangles[i][1:]+triangles[i][:1]):
                vector=sub(b,a);length2=dot(vector,vector)
                if not length2:continue
                parameter=dot(sub(p,a),vector)/length2
                if 1e-9<parameter<1-1e-9:
                    projected=tuple(a[j]+parameter*vector[j] for j in range(3))
                    if math.dist(projected,p)<=tolerance:
                        edge_nodes[tuple(sorted((a,b)))].add(p)
    propagated=[]
    for i,triangle in enumerate(triangles):
        if i in replacement:continue
        boundary=[]
        for a,b in zip(triangle,triangle[1:]+triangle[:1]):
            boundary.append(a)
            vector=sub(b,a);length2=dot(vector,vector)
            boundary.extend(sorted(edge_nodes.get(tuple(sorted((a,b))),()),key=lambda p:dot(sub(p,a),vector)/length2))
        if len(boundary)>3:
            midpoint=canonical(tuple(sum(p[j] for p in triangle)/3 for j in range(3)))
            output=[]
            for a,b in zip(boundary,boundary[1:]+boundary[:1]):
                value=(midpoint,a,b)
                if normal(value) is None:raise ValueError('Propagated boundary node generated a degenerate fan')
                output.append(value)
            replacement[i]=output;propagated.append(i)
    candidate=[]
    parents=[]
    for i,t in enumerate(triangles):
        values=replacement.get(i,[t]);candidate.extend(values);parents.extend([i]*len(values))
    # OCCT can emit near-collinear caps on split polygon boundaries. Remove
    # only numerically zero-width pieces, then make both sides of every cut
    # use the same subdivided edge. This is bounded far below the input mesh
    # coordinate precision and the caller's displacement limit.
    zero_width=[]
    valid=[]
    valid_parents=[]
    for triangle,parent in zip(candidate,parents):
        longest=max(math.dist(triangle[k],triangle[(k+1)%3]) for k in range(3))
        altitude=2*triangle_area(triangle)/longest if longest else 0
        if parent in selected and altitude<=cap_tolerance and triangle_area(triangle)<1e-7:
            zero_width.append({'source_triangle_index':parent,'vertices_mm':triangle,'altitude_mm':altitude})
        else:
            valid.append(triangle);valid_parents.append(parent)
    candidate,parents=valid,valid_parents
    candidate_index=TriangleIndex(candidate)
    conforming_nodes=defaultdict(set)
    max_conforming_distance=0.
    for p in list(added):
        bounds=tuple(v-cap_tolerance for v in p)+tuple(v+cap_tolerance for v in p)
        for i in candidate_index.candidates(bounds):
            triangle=candidate[i]
            if p in triangle:continue
            for a,b in zip(triangle,triangle[1:]+triangle[:1]):
                v=sub(b,a);length2=dot(v,v)
                if not length2:continue
                t=dot(sub(p,a),v)/length2
                if not 0<t<1 or min(math.dist(p,a),math.dist(p,b))<=tolerance:continue
                distance=math.dist(tuple(a[j]+t*v[j] for j in range(3)),p)
                if distance<=cap_tolerance:
                    conforming_nodes[tuple(sorted((a,b)))].add(p)
                    max_conforming_distance=max(max_conforming_distance,distance)
    conforming=[]
    conforming_parents=[]
    for triangle,parent in zip(candidate,parents):
        boundary=[]
        for a,b in zip(triangle,triangle[1:]+triangle[:1]):
            boundary.append(a);v=sub(b,a);length2=dot(v,v)
            boundary.extend(sorted(conforming_nodes.get(tuple(sorted((a,b))),()),key=lambda p:dot(sub(p,a),v)/length2))
        if len(boundary)==3:
            conforming.append(triangle);conforming_parents.append(parent)
        else:
            midpoint=tuple(sum(p[j] for p in triangle)/3 for j in range(3))
            for a,b in zip(boundary,boundary[1:]+boundary[:1]):
                value=(midpoint,a,b)
                if normal(value) is None:raise ValueError('Conforming subdivision produced a zero-area triangle')
                conforming.append(value);conforming_parents.append(parent)
    candidate,parents=conforming,conforming_parents
    for triangle,parent in zip(candidate,parents):
        if parent in replacement:
            angle=math.degrees(math.acos(max(-1,min(1,dot(normal(triangle),normal(triangles[parent]))))))
            max_normal_error=max(max_normal_error,angle)
    unique=[]
    seen=set()
    duplicates=0
    for triangle in candidate:
        signature=tuple(sorted(triangle))
        if signature in seen:
            duplicates+=1
        else:
            seen.add(signature);unique.append(triangle)
    candidate=unique
    print('CUT_RESULT',len(candidate),'triangles',len(removed),'internal pieces removed',flush=True)
    return candidate,{'moved_vertices':[],'added_vertices_count':len(added),
        'changed_triangle_indices_zero_based':sorted(replacement),'propagated_triangle_indices_zero_based':propagated,
        'max_vertex_displacement_mm':max_weld,'max_geometric_normal_change_deg':max_normal_error,
        'degenerate_changed_triangles':[],'reoriented_piece_count':orientation_changes,
        'removed_classified_internal_pieces':removed,'classification_decisions':decisions,
        'split_component_triangle_indices':sorted(selected),'split_intersection_pair_count':len(pairs),
        'intersection_required_radius_mm':required_radius,'duplicate_corefined_triangles_removed':duplicates,
        'numerically_zero_width_caps_removed':zero_width,'max_conforming_edge_distance_mm':max_conforming_distance,
        'preclassification_numerical_caps_removed':preclassification_caps,
        'construction_tolerance_mm':construction_tolerance,
        'point_welding_tolerance_mm':tolerance,'source_facet_planes_preserved':True}
