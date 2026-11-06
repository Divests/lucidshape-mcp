// Fixed CAD mirror experiment: one imported root surface, -Z collimated source.
object null;
object imported=ObjectVector("MCP STEP import",());
double ok=imported.readFileIntoGroup("input.stp",$,1);
if(!ok) {
    cout << "LUCIDSHAPE_MCP_FAILED: STEP import failed" << endl;
    return;
}
object surfaces=imported.getObjects("Surface");
surfaces.onlyRoots();
if(surfaces.size()!=1) {
    cout << "LUCIDSHAPE_MCP_FAILED: require exactly one root STEP surface" << endl;
    return;
}
double xmin=1e100,xmax=-1e100,ymin=1e100,ymax=-1e100,zmin=1e100,zmax=-1e100;
if(!surfaces.checkBoundingBox(xmin,xmax,ymin,ymax,zmin,zmax)) {
    cout << "LUCIDSHAPE_MCP_FAILED: bounding box unavailable" << endl;
    return;
}
object surface=surfaces[0];
object mirror=SurfaceShape("Imported CAD mirror",surface,Specular("MCP mirror material",@@REFLECTION@@));
object source=PointLight("MCP collimated source",AxisSystem("source axis",$,(0,0,@@SOURCE_Z@@),(0,0,-1),(1,0,0)),DirectionalEmitter("MCP directional emitter",@@FLUX@@));
object lid=LIDData("MCP reflected far field",(),0,160,80,(-40,40,-20,20),1.0,$);
object detector=SurfaceShape("MCP far field",InfinitySurface(""),CandelaSensor("MCP reflection sensor",1,lid));
object scene=ObjectVector("MCP imported CAD mirror scene",(mirror,source,detector));
object output=Store("scene.lue");
output << scene;
output=null;
object report=JSONStore("engine-result.json",0,1);
report << (ok,surfaces.size(),(xmin,xmax,ymin,ymax,zmin,zmax));
report=null;
cout << "LUCIDSHAPE_MCP_OK" << endl;
