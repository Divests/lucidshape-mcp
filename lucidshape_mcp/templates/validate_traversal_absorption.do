// Fixed analytic fixture: uncoated n=1.5 slab, normal incidence; NOT the project model.
object null;
object air=AirMedium("air");
object glass=Medium("fixture glass",1.5,0);
object material=Refractive("physical Fresnel",air,glass,1,1);
object front=SurfaceShape("entry",PlanarSurface("entry",$,AxisSystem("",$,(0,0,0),(0,0,1),(1,0,0)),(-10000,10000,-10000,10000)),material);
object back=SurfaceShape("exit",PlanarSurface("exit",$,AxisSystem("",$,(0,0,10),(0,0,1),(1,0,0)),(-10000,10000,-10000,10000)),material);
object scene=ObjectVector("analytic slab fixture",(front,back));
object finder=RayPathFinder("fixture paths",0.000001,128,1,scene);
double forward=0,backward=0,weighted=0,weightedBack=0,enteredCount=0,internalReflections=0;
double incomplete=0,intensityError=0;
for(double i=0;i<20000;i=i+1) {
    object path=finder.traceRayPath(LRay("fixture",(0,0,-1),(0,0,1),1000,1,air));
    double previous=1,entries=0,exits=0,internal=0;
    object last;
    for(double j=0;j<path.size();j=j+1) {
        object ray=path[j];
        object d=ray.getDataVector();
        object medium=d[5];
        double n=1;
        if(medium) n=medium.getRefractionIndex();
        if(previous<1.1 && n>1.4) entries=entries+1;
        if(previous>1.4 && n<1.1) exits=exits+1;
        if(previous>1.4 && n>1.4 && j>0) internal=internal+1;
        previous=n;
        last=d;
    }
    if(entries!=exits || path.size()<1 || last[3]<1e30) incomplete=incomplete+1;
    if(entries>0) enteredCount=enteredCount+1;
    internalReflections=internalReflections+internal;
    double weight=last[4];
    if(abs(weight-1)>intensityError) intensityError=abs(weight-1);
    for(double q=0;q<exits;q=q+1) weight=weight*0.998;
    object direction=last[2];
    if(direction[2]>0) {
        forward=forward+1;
        weighted=weighted+weight;
    } else {
        backward=backward+1;
        weightedBack=weightedBack+weight;
    }
}
// Single TIR bounce, launched within glass: no complete entry/exit, no fixed loss.
object singleScene=ObjectVector("single interface",(front));
object tirFinder=RayPathFinder("TIR fixture",0.000001,128,1,singleScene);
object tir=tirFinder.traceRayPath(LRay("TIR",(0,0,-1),(0.8660254037844386,0,0.5),1000,1,glass));
object tail=tir[tir.size()-1];
object td=tail.getDataVector();
object tv=td[2];
object tm=td[5];
double tn=1;
if(tm) tn=tm.getRefractionIndex();
object normalMesh=MeshSurface("analytic normal probe",(1,1,1),((-10,-10,0),(10,-10,0),(0,10,0)),((0.1736481776669303,0,0.984807753012208),(0.1736481776669303,0,0.984807753012208),(0.1736481776669303,0,0.984807753012208)),(),((0,1,2)));
object normalScene=ObjectVector("mesh normal fixture",(SurfaceShape("mirror",normalMesh,Specular("mirror",1))));
object normalFinder=RayPathFinder("normal probe",0.000001,128,1,normalScene);
object normalPath=normalFinder.traceRayPath(LRay("normal probe",(0,0,1),(0,0,-1),100,1,air));
object normalLast=normalPath[normalPath.size()-1];
object normalData=normalLast.getDataVector();
object normalDirection=normalData[2];
// Compare closed-volume modes. An embedded absorber detects whether local
// tracing hides other geometry; that behavior must not be silently enabled.
object boundaryRows=();
object plain=Refractive("boundary fixture without Fresnel",air,glass,0,1);
object boxSurfaces=(
    PlanarSurface("z minus",$,AxisSystem("",$,(0,0,-10),(0,0,-1),(1,0,0)),(-10,10,-10,10)),
    PlanarSurface("z plus",$,AxisSystem("",$,(0,0,10),(0,0,1),(1,0,0)),(-10,10,-10,10)),
    PlanarSurface("x minus",$,AxisSystem("",$,(-10,0,0),(-1,0,0),(0,1,0)),(-10,10,-10,10)),
    PlanarSurface("x plus",$,AxisSystem("",$,(10,0,0),(1,0,0),(0,1,0)),(-10,10,-10,10)),
    PlanarSurface("y minus",$,AxisSystem("",$,(0,-10,0),(0,-1,0),(1,0,0)),(-10,10,-10,10)),
    PlanarSurface("y plus",$,AxisSystem("",$,(0,10,0),(0,1,0),(1,0,0)),(-10,10,-10,10)));
for(double local=0;local<2;local=local+1) {
    for(double closed=0;closed<2;closed=closed+1) {
        for(double blocked=0;blocked<2;blocked=blocked+1) {
            object volume=MultiSurfaceShape("boundary fixture",boxSurfaces,plain,local);
            volume.setClosedBoundary(closed);
            object boundaryScene=ObjectVector("boundary fixture",(volume));
            if(blocked) boundaryScene << SurfaceShape("embedded absorber",PlanarSurface("blocker",$,AxisSystem("",$,(0,0,0),(0,0,1),(1,0,0)),(-9,9,-9,9)),Absorber("blocker"));
            object boundaryFinder=RayPathFinder("boundary fixture",0.000001,128,1,boundaryScene);
            object boundaryPath=boundaryFinder.traceRayPath(LRay("boundary probe",(0,0,-11),(0,0,1),1000,1,air));
            object boundaryLast=boundaryPath[boundaryPath.size()-1];
            object boundaryData=boundaryLast.getDataVector();
            object boundaryMedium=boundaryData[5],boundaryDirection=boundaryData[2];
            double boundaryIndex=1;
            if(boundaryMedium) boundaryIndex=boundaryMedium.getRefractionIndex();
            boundaryRows << (local,closed,blocked,boundaryPath.size(),boundaryData[3],boundaryData[4],boundaryIndex,boundaryDirection[2]);
        }
    }
}
object out=JSONStore("engine-result.json",0,1);
out << (20000,forward,backward,weighted,weightedBack,enteredCount,internalReflections,incomplete,intensityError,(tir.size(),tv[2],td[4],tn),(normalPath.size(),normalDirection[0],normalDirection[1],normalDirection[2]),boundaryRows);
out=null;
cout << "LUCIDSHAPE_MCP_OK" << endl;
