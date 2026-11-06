// Recorded whole-lamp output rays -> sensor only; no optical geometry.
object null;
object source=RayFile("manual output rays","input.ray",0);
double n=source.countRays();
if(n!=@@COUNT@@ || source.isSpectral()) {
    cout << "LUCIDSHAPE_MCP_FAILED: unexpected recorded ray count or spectrum" << endl; return;
}
object lid=LIDData("manual output ray replay",(),1,@@GRID_U@@,@@GRID_V@@,@@BOUNDS@@,1,$);
object emitter=PointLight("recorded whole lamp",AxisSystem("identity",$,(0,0,0),(0,0,1),(1,0,0)),RayFileEmitter("recorded whole lamp","input.ray",0,AirMedium("air")));
object detector=SurfaceShape("far field",InfinitySurface("",1),CandelaSensor("sensor",1,lid));
object scene=ObjectVector("output ray replay, no optical geometry",(emitter,detector));
object trace=ForwardRayTrace("every recorded ray exactly once",0,1,1,scene);
if(!trace.traceRays(n,1,0,0,20,0,0,380,780,0,0,0,n)) {
    cout << "LUCIDSHAPE_MCP_FAILED: output ray replay failed" << endl; return;
}
object saved=Store("photometry.lid");saved << lid.cloneData(0);saved=null;
object ies=lid.cloneData(1);
if(!ies.writeFile("photometry.ies")) {
    cout << "LUCIDSHAPE_MCP_FAILED: output IES export failed" << endl; return;
}
double u0,u1,v0,v1,lo,hi;lid.range(u0,u1,v0,v1,lo,hi);
object out=JSONStore("engine-result.json",0,1);
out << (n,lid.angleType(),(lid.sizeU(),lid.sizeV()),(u0,u1,v0,v1),(lo,hi,lid.integral()),20);
out=null;
cout << "LUCIDSHAPE_MCP_OK" << endl;
