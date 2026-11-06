// Fixed material experiment: not S9, no app connection, no approved input changes.
object null;
object devices=getGPUTraceDeviceInfo();
if(devices==null || devices.size()<1) { cout << "LUCIDSHAPE_MCP_FAILED: no GPU" << endl; return; }
setGPUTraceDeviceID(0);
object rows=();
object air=AirMedium("air");
object glass=Medium("glass",1.5,0);
object blockedGlass=Medium("exit-absorbing glass",1.5,0,$,$,1);
if(glass==null || blockedGlass==null) { cout << "LUCIDSHAPE_MCP_FAILED: fixture medium construction" << endl; return; }
cout << "GLASS " << glass << endl << "BLOCKED_GLASS " << blockedGlass << endl;
for(double modeIndex=0;modeIndex<2;modeIndex=modeIndex+1) {
    double mode=20;
    if(modeIndex==1) mode=13;
    for(double variant=0;variant<17;variant=variant+1) {
        double fresnel=0;
        if(variant==5 || variant==6 || variant==11 || variant>=14) fresnel=1;
        double index=1.5;
        if(variant==12) index=1.586;
        if(variant==13) index=1.473;
        glass=Medium("glass",index,0);
        blockedGlass=Medium("exit-absorbing glass",index,0,$,$,1);
        object base=Refractive("normal",air,glass,fresnel,1);
        object blockedAir=Medium("absorbing outgoing air",1,0,$,$,1);
        object blocked=Refractive("exit absorption",blockedAir,glass,fresnel,1);
        object material=base;
        if(variant==1) material=blocked;
        if(variant==2 || variant==3 || variant==4 || variant==6 || variant==9 || variant==11 || variant==15 || variant==16) material=ComboActor("75 percent survival per exit candidate",(base,blocked),(.75,.25));
        if(variant==7 || variant==8 || variant==12 || variant==13) material=ComboActor("99.8 percent survival per exit candidate",(base,blocked),(.998,.002));
        object scene=ObjectVector("GPU loss fixture",());
        double slabs=1;
        if(variant==3 || variant==8) slabs=2;
        for(double slab=0;slab<slabs;slab=slab+1) {
            double z=slab*20;
            scene << SurfaceShape("entry",PlanarSurface("entry",$,AxisSystem("",$,(0,0,z),(0,0,-1),(1,0,0)),(-100,100,-100,100)),material);
            if(variant!=4 && variant!=9 && variant!=10 && variant!=11 && variant!=16)
                scene << SurfaceShape("exit",PlanarSurface("exit",$,AxisSystem("",$,(0,0,z+10),(0,0,1),(1,0,0)),(-100,100,-100,100)),material);
        }
        object lid=LIDData("loss fixture",(),0,200,200,(-10,10,-10,10),1,$);
        if(variant==9 || variant==10) {
            scene << SurfaceShape("detector",RotatedInfinitySurface("",0,AxisSystem("TIR output",$,(0,0,0),(.8660254037844386,0,-.5),(0,1,0))),CandelaSensor("sensor",1,lid,(glass,1,0,0)));
            scene << PointLight("TIR source",AxisSystem("",$,(0,0,0),(0,0,1),(1,0,0)),RayFileEmitter("inside glass","tir.ray",0,glass));
        } else {
            object detectedMedium=air;
            if(variant==4 || variant==11) detectedMedium=glass;
            object detectorSurface=InfinitySurface("");
            if(variant>=14) detectorSurface=RotatedInfinitySurface("backward",0,AxisSystem("backward",$,(0,0,0),(0,0,-1),(1,0,0)));
            scene << SurfaceShape("detector",detectorSurface,CandelaSensor("sensor",1,lid,(detectedMedium,1,0,0)));
            scene << PointLight("source",AxisSystem("",$,(0,0,-1),(0,0,1),(1,0,0)),DirectionalEmitter("",1000));
        }
        object tracer=ForwardRayTrace("GPU loss candidate",0,32,1,scene);
        cout << "LOSS_FIXTURE_BEGIN " << mode << " " << variant << endl;
        double ok=tracer.traceRays(131072,1,0,0,mode);
        rows << (mode,variant,ok,lid.integral());
        cout << "LOSS_FIXTURE_END " << mode << " " << variant << " " << lid.integral() << endl;
    }
}
object historyRows=();
cout << "HISTORY_PROBE_BEGIN" << endl;
for(double hm=0;hm<2;hm=hm+1) {
    double hmode=20;
    string filename="history_cpu.hst";
    if(hm==1) { hmode=13; filename="history_gpu.hst"; }
    object history=RayHistoryFile("fixed history test",$,20,20,(-10,10,-10,10),0,filename,ObjectVector("",()),());
    object hs=SurfaceShape("history",InfinitySurface("",0),RayHistorySensor("history",1,0,history,$,0));
    object he=PointLight("source",AxisSystem("",$,(0,0,10),(0,0,-1),(1,0,0)),DirectionalEmitter("source",1000));
    object ht=MeshSurface("triangle",(1,1,1),((-10,-10,0),(10,-10,0),(0,10,0)),((0,0,1),(0,0,1),(0,0,1)),(),((0,1,2)));
    object hscene=ObjectVector("history probe",(SurfaceShape("mirror",ht,Specular("mirror",1)),he,hs));
    object htrace=ForwardRayTrace("history probe",0,16,1,hscene);
    double hok=htrace.traceRays(1024,1,0,0,hmode);
    object hd=history.getDataVector();
    historyRows << (hmode,hok,hd[5]);
    cout << "HISTORY_PROBE_RESULT " << hmode << " " << hd[5] << endl;
}
object out=JSONStore("engine-result.json",0,1);
out << (rows,historyRows);
out=null;
cout << "LUCIDSHAPE_MCP_OK" << endl;
