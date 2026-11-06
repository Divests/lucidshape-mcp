"""Read-only finite STL bounds, oriented edge closure and enclosed-volume checks."""
import hashlib
import math
from pathlib import Path
import struct

def write_explicit_facet_stl(path,vertices,facets):
    """Preserve native float32 triangle positions and store actual facet normals."""
    collapsed=0
    with Path(path).open('wb') as stream:
        stream.write(b'Explicit facet normals; diagnostic only'.ljust(80,b' '))
        stream.write(struct.pack('<I',len(facets)))
        for ids in facets:
            original=[vertices[i] for i in ids]
            points=[struct.unpack('<3f',struct.pack('<3f',*p)) for p in original]
            def cross_of(points):
                a=[points[1][j]-points[0][j] for j in range(3)]
                b=[points[2][j]-points[0][j] for j in range(3)]
                n=(a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
                size=math.sqrt(sum(v*v for v in n))
                return n,size
            normal,size=cross_of(points)
            if size==0:
                # Retain the exact native geometry, including preexisting collapsed
                # facets; record them rather than silently dropping or healing them.
                collapsed+=1
                normal,size=cross_of(original)
            if size==0:raise ValueError('Cannot define a facet normal')
            stream.write(struct.pack('<12fH',*[v/size for v in normal],*[v for p in points for v in p],0))
    return collapsed

def inspect_stl(path):
    path=Path(path)
    size=path.stat().st_size
    if size>200*1024*1024:
        raise ValueError('STL exceeds diagnostic size limit')
    with path.open('rb') as stream:
        header=stream.read(84)
    count=struct.unpack('<I',header[80:84])[0] if len(header)==84 else 0
    binary=size==84+50*count and count>0
    def triangles():
        if binary:
            with path.open('rb') as stream:
                stream.seek(84)
                for _ in range(count):
                    record=struct.unpack('<12fH',stream.read(50))
                    yield [record[3:6],record[6:9],record[9:12]]
        else:
            points=[]
            with path.open(encoding='ascii') as stream:
                for line in stream:
                    words=line.split()
                    if words and words[0].lower()=='vertex':
                        if len(words)!=4:
                            raise ValueError('Invalid ASCII STL vertex')
                        points.append(tuple(float(v) for v in words[1:]))
                        if len(points)==3:
                            yield points
                            points=[]
            if points:
                raise ValueError('Incomplete ASCII STL triangle')
    ids={}
    edges={}
    bounds=[math.inf,-math.inf]*3
    volume=0.
    total=degenerate=0
    for points in triangles():
        total+=1
        if total>2000000 or any(not math.isfinite(v) for p in points for v in p):
            raise ValueError('STL has excessive triangles or nonfinite vertices')
        for p in points:
            for j in range(3):
                bounds[2*j]=min(bounds[2*j],p[j])
                bounds[2*j+1]=max(bounds[2*j+1],p[j])
        a=[points[1][j]-points[0][j] for j in range(3)]
        b=[points[2][j]-points[0][j] for j in range(3)]
        cross=(a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
        if sum(v*v for v in cross)<=1e-40:
            degenerate+=1
            continue
        volume+=sum(points[0][j]*cross[j] for j in range(3))/6
        indexes=[]
        for p in points:
            p=tuple(p)
            if p not in ids:
                ids[p]=len(ids)
            indexes.append(ids[p])
        for start,end in zip(indexes,indexes[1:]+indexes[:1]):
            key=(min(start,end),max(start,end))
            row=edges.setdefault(key,[0,0])
            row[0]+=1
            row[1]+=1 if start<end else -1
    if total<4 or total==degenerate:
        raise ValueError('STL has no bounded solid triangle set')
    boundary=sum(n==1 for n,balance in edges.values())
    multiple=sum(n>2 for n,balance in edges.values())
    misoriented=sum(n==2 and balance!=0 for n,balance in edges.values())
    return {'format':'binary' if binary else 'ascii','triangle_count':total,'unique_vertices':len(ids),
        'bounding_box_mm':bounds,'signed_volume_mm3':volume,'boundary_edges':boundary,
        'nonmanifold_edges':multiple,'misoriented_edges':misoriented,'degenerate_triangles':degenerate,
        'closed_oriented_two_manifold':boundary==multiple==misoriented==0,
        'coordinate_welding_applied':False,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'optical_accuracy_verified':False,'mesh_convergence_verified':False}
