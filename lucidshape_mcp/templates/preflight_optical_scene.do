// Native scene preparation and a few geometric diagnostic paths only.
// Extra per-traversal absorption is intentionally absent, never a final simulation.
object null;
object air=AirMedium("air");
object scene=ObjectVector("optical preflight",());
object regions=();
object sources=();
object paths=();

double addRegion(string filename,string label,double expected,object material,double boundaryMode) {
    object imported=ObjectVector(label,());
    if(!imported.readFileIntoGroup(filename,$,1)) {
        cout << "LUCIDSHAPE_MCP_FAILED: region import failed " << label << endl;
        return 0;
    }
    double count=0,skipped=0;
    object boundarySurfaces=();
    for(double i=0;i<imported.size();i=i+1) {
        object face=imported[i];
        if(face.isOfClassName("Surface")) {
            string name;
            name << label << " face " << count;
            if(boundaryMode>0) boundarySurfaces << face;
            else scene << SurfaceShape(name,face,material);
            count=count+1;
        } else {
            if(!face.isOfClassName("AxisSystem")) {
                cout << "LUCIDSHAPE_MCP_FAILED: unexpected top-level geometry " << face.nameOfClass() << endl;
                return 0;
            }
            skipped=skipped+1;
        }
    }
    if(count!=expected) {
        cout << "LUCIDSHAPE_MCP_FAILED: physical face count differs for " << label << ": " << count << endl;
        return 0;
    }
    if(boundaryMode>0) {
        double local=0;
        if(boundaryMode==2) local=1;
        object volume=MultiSurfaceShape("main lens closed volume diagnostic",boundarySurfaces,material,local);
        volume.setClosedBoundary(1);
        object checkedSurfaces=volume.getSurfaces();
        if(!volume.getClosedBoundary() || checkedSurfaces.size()!=expected) {
            cout << "LUCIDSHAPE_MCP_FAILED: closed-volume grouping differs" << endl;
            return 0;
        }
        scene << volume;
        cout << "MAIN_CLOSED_BOUNDARY " << checkedSurfaces.size() << " local=" << local << endl;
    }
    regions << (label,count,skipped,material.nameOfClass());
    cout << "REGION_READY " << label << " " << count << endl;
    return 1;
}

double addSource(double id,object position,object forward,object right) {
    string name;
    name << "LED axis " << id;
    object axis=AxisSystem(name,$,position,forward,right);
    scene << PointLight(name,axis,RayFileEmitter(name,"source.ray",0,air));
    sources << (id,position,forward,right);
    return 1;
}

@@REGIONS@@
@@SOURCES@@
double physicalFaceCount=0;
for(double r=0;r<regions.size();r=r+1) {
    object region=regions[r];
    physicalFaceCount=physicalFaceCount+region[1];
}
object data=LIDData("diagnostic sensor",(),0,@@GRID_U@@,@@GRID_V@@,@@SENSOR_BOUNDS@@,1,$);
object sensorAxis=AxisSystem("configured far field",$,(0,0,0),@@SENSOR_Z@@,@@SENSOR_X@@);
scene << SurfaceShape("far field",RotatedInfinitySurface("far field",1,sensorAxis),CandelaSensor("sensor",1,data));

object finder=RayPathFinder("axis paths",0.00001,64,1,scene);
for(double a=0;a<sources.size();a=a+1) {
    object source=sources[a];
    object start=source[1];
    object path=finder.traceRayPath(LRay("axis diagnostic",start,source[2],1000,1,air));
    object segments=();
    for(double k=0;k<path.size() && k<128;k=k+1) {
        object ray=path[k];
        object d=ray.getDataVector();
        object p=d[1];
        object v=d[2];
        object medium=d[5];
        double index=1;
        if(medium) index=medium.getRefractionIndex();
        segments << ((p[0],p[1],p[2]),(v[0],v[1],v[2]),d[3],d[4],index);
    }
    paths << (source[0],path.size(),segments);
}
object rf=RayFile("approved source","source.ray",0);
object shapes=scene.getObjects("SurfaceShape");
object out=JSONStore("engine-result.json",0,1);
out << (regions,sources,physicalFaceCount+1,rf.countRays(),paths);
out=null;
cout << "LUCIDSHAPE_MCP_OK" << endl;
