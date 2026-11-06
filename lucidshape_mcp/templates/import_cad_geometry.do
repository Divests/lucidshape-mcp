// Geometry-only STEP import. No optical assumptions, transforms or ray tracing.
object null;
object imported=ObjectVector("MCP imported CAD geometry",());
if(!imported.readFileIntoGroup("input.stp",$,1)) {
    cout << "LUCIDSHAPE_MCP_FAILED: STEP import failed" << endl;
    return;
}
object all=imported.getObjects();
object surfaces=imported.getObjects("Surface");
double surfaceCount=surfaces.size();
surfaces.onlyRoots();
if(surfaces.size()==0) {
    cout << "LUCIDSHAPE_MCP_FAILED: STEP contains no surfaces" << endl;
    return;
}
double xmin=1e100,xmax=-1e100,ymin=1e100,ymax=-1e100,zmin=1e100,zmax=-1e100;
if(!surfaces.checkBoundingBox(xmin,xmax,ymin,ymax,zmin,zmax)) {
    cout << "LUCIDSHAPE_MCP_FAILED: geometry bounding box unavailable" << endl;
    return;
}
object output=Store("geometry.lue",0,1);
output << imported;
output=null;
object check=ObjectVector("MCP native file check",());
if(!check.readFileIntoGroup("geometry.lue",$,1)) {
    cout << "LUCIDSHAPE_MCP_FAILED: saved native model cannot be read" << endl;
    return;
}
object checkSurfaces=check.getObjects("Surface");
cout << "MCP_IMPORT_SURFACES before=" << surfaceCount << " roots=" << surfaces.size() << " saved=" << checkSurfaces.size() << endl;
object beforeBasic=imported.getObjects("NSurface");
object afterBasic=check.getObjects("NSurface");
object beforeTrimmed=imported.getObjects("TrimmedSurface");
object afterTrimmed=check.getObjects("TrimmedSurface");
cout << "MCP_NURBS before=" << beforeBasic.size() << " after=" << afterBasic.size() << endl;
cout << "MCP_TRIMMED before=" << beforeTrimmed.size() << " after=" << afterTrimmed.size() << endl;
checkSurfaces.onlyRoots();
cout << "MCP_IMPORT_ROOTS saved=" << checkSurfaces.size() << endl;
if(checkSurfaces.size()!=surfaces.size()) {
    cout << "LUCIDSHAPE_MCP_FAILED: root surface count changed after saving" << endl;
    return;
}
double cxmin=1e100,cxmax=-1e100,cymin=1e100,cymax=-1e100,czmin=1e100,czmax=-1e100;
if(!checkSurfaces.checkBoundingBox(cxmin,cxmax,cymin,cymax,czmin,czmax)) {
    cout << "LUCIDSHAPE_MCP_FAILED: saved geometry bounding box unavailable" << endl;
    return;
}
object report=JSONStore("engine-result.json",0,1);
report << (all.size(),surfaceCount,surfaces.size(),(xmin,xmax,ymin,ymax,zmin,zmax),(cxmin,cxmax,cymin,cymax,czmin,czmax));
report=null;
cout << "LUCIDSHAPE_MCP_OK" << endl;
