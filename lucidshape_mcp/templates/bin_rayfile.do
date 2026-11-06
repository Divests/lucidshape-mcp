// Re-bin all supplied escaped weighted rays exactly once, without optical surfaces.
object null;
object rayfile=RayFile("weighted escaped rays","input.ray",0);
double rays=rayfile.countRays();
if(rays<1 || rays>2000000 || rayfile.isSpectral()) {
    cout << "LUCIDSHAPE_MCP_FAILED: require 1..2000000 non-spectral escaped rays" << endl;
    return;
}
object lid=LIDData("provisional far field",(),0,@@GRID_U@@,@@GRID_V@@,@@SENSOR_BOUNDS@@,1,$);
object sensorAxis=AxisSystem("configured far field",$,(0,0,0),@@SENSOR_Z@@,@@SENSOR_X@@);
object detector=SurfaceShape("far field",RotatedInfinitySurface("",0,sensorAxis),CandelaSensor("sensor",1,lid));
object emitter=PointLight("weighted escaped rays",AxisSystem("identity",$,(0,0,0),(0,0,1),(1,0,0)),RayFileEmitter("weighted escaped rays","input.ray",0,AirMedium("air")));
object scene=ObjectVector("weighted ray binning",(emitter,detector));
object tracer=ForwardRayTrace("all escaped rays",0,1,1,scene);
double traceOK=tracer.traceRays(rays,1,0,0);
if(!traceOK) {
    cout << "LUCIDSHAPE_MCP_FAILED: weighted ray binning failed" << endl;
    return;
}
object snapshot=lid.cloneData(0);
object saved=Store("photometry.lid");
saved << snapshot;
saved=null;
double u0,u1,v0,v1,lo,hi;
lid.range(u0,u1,v0,v1,lo,hi);
object out=JSONStore("engine-result.json",0,1);
out << (rays,lid.angleType(),(lid.sizeU(),lid.sizeV()),(u0,u1,v0,v1),(lo,hi,lid.integral()));
out=null;
cout << "LUCIDSHAPE_MCP_OK" << endl;
