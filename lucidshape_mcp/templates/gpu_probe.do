// Fixed isolated device/smoke probe. No GUI, external scene, upload or settings file.
object null;
object infos=getGPUTraceDeviceInfo();
object devices=List();
double count=0;
if(infos!=null) {
    count=infos.size();
    double i;
    for(i=0;i<count;++i) {
        object info=infos[i];
        // LS2.0 JSONStore does not escape embedded newlines in device details.
        devices << (info[0],"Native device details recorded in engine.log");
        cout << info[0] << endl << info[1] << endl;
    }
}
double selected=-1;
if(count>0) selected=getGPUTraceDeviceID();
object early=JSONStore("gpu-devices.json",0,1);
early << (count,selected,devices);
early=null;
cout << "GPU_DEVICE_COUNT " << count << endl;
double attempted=0,ok=0,flux=0,lo=0,hi=0;
if(count>0) {
    setGPUTraceDeviceID(0);
    selected=0;
    object lid=LIDData("GPU smoke fixture",(),0,60,60,(-60,60,-60,60),1,$);
    object detector=SurfaceShape("detector",InfinitySurface(""),CandelaSensor("sensor",1,lid));
    object emitter=PointLight("fixture source",AxisSystem("",$,(0,0,10),(0,0,-1),(1,0,0)),DirectionalEmitter("",1000));
    object mesh=MeshSurface("fixed triangle",(1,1,1),((-10,-10,0),(10,-10,0),(0,10,0)),((0,0,1),(0,0,1),(0,0,1)),(),((0,1,2)));
    object mirror=SurfaceShape("GPU triangle mirror",mesh,Specular("mirror",1));
    object scene=ObjectVector("fixed GPU smoke fixture",(mirror,emitter,detector));
    object tracer=ForwardRayTrace("GPU fixture",0,16,1,scene);
    attempted=1;
    cout << "GPU_MODE_13_BEGIN" << endl;
    ok=tracer.traceRays(10000,1,0,0,13);
    cout << "GPU_MODE_13_RETURN " << ok << endl;
    double u0,u1,v0,v1;
    lid.range(u0,u1,v0,v1,lo,hi);
    flux=lid.integral();
}
object out=JSONStore("engine-result.json",0,1);
out << (count,selected,devices,attempted,ok,(lo,hi,flux));
out=null;
cout << "LUCIDSHAPE_MCP_OK" << endl;
