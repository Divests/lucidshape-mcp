// Fixed API investigation only. No project scene, no Studio/CATIA, no approved input edits.
object null;
object devices=getGPUTraceDeviceInfo();
if(devices==null || devices.size()<1) { cout << "LUCIDSHAPE_MCP_FAILED: no GPU" << endl; return; }
setGPUTraceDeviceID(0);
object rows=();
object air=AirMedium("air");
object glass=Medium("glass",1.5,0);
for(double mi=0;mi<2;mi=mi+1) {
    double mode=20;
    if(mi==1) mode=13;
    for(double profile=0;profile<4;profile=profile+1) {
        object material=Refractive("control",air,glass,0,1);
        if(profile==1) material=Refractive("scale control",air,glass,0,.75);
        if(profile==2) material=RefractiveN("signed theta candidate",air,glass,((0,1,0),(89.999,1,0),(90.001,.25,0),(180,.25,0)));
        if(profile==3) material=RefractiveN("angle control",air,glass,((0,1,0),(90,.25,0)));
        if(material==null) { cout << "LUCIDSHAPE_MCP_FAILED: material constructor" << endl; return; }
        for(double side=0;side<2;side=side+1) {
            for(double angleIndex=0;angleIndex<2;angleIndex=angleIndex+1) {
                double angle=30*angleIndex;
                double dx=0, dz=1;
                if(angleIndex==1) { dx=.5; dz=.8660254037844386; }
                object incoming=air, outgoing=glass;
                object start=(0,0,-1), direction=(dx,0,dz);
                double targetX=dx/1.5;
                object detectorDirection=(targetX,0,sqrt(1-targetX*targetX));
                if(side==1) {
                    incoming=glass; outgoing=air;
                    start=(0,0,1); direction=(dx,0,-dz);
                    targetX=dx*1.5;
                    detectorDirection=(targetX,0,-sqrt(1-targetX*targetX));
                }
                object detectorAxis=AxisSystem("detector",$,(0,0,0),detectorDirection,(0,1,0));
                object lid=LIDData("interface",(),0,256,256,(-.05,.05,-.05,.05),1,$);
                object scene=ObjectVector("interface fixture",(
                    SurfaceShape("boundary",PlanarSurface("boundary",$,AxisSystem("",$,(0,0,0),(0,0,-1),(1,0,0)),(-100,100,-100,100)),material),
                    SurfaceShape("detector",RotatedInfinitySurface("",0,detectorAxis),CandelaSensor("sensor",1,lid,(outgoing,1,0,0))),
                    PointLight("source",AxisSystem("source",$,start,direction,(0,1,0)),RayFileEmitter("rays","interface.ray",0,incoming))));
                object tracer=ForwardRayTrace("interface probe",0,16,1,scene);
                double ok=tracer.traceRays(65536,1,0,0,mode);
                rows << (mode,0,profile,side,angle,0,ok,lid.integral());
                cout << "INTERFACE_RESULT " << mode << " " << profile << " " << side << " " << angle << " " << lid.integral() << endl;
            }
        }
    }
    for(double bi=0;bi<2;bi=bi+1) {
        double alpha=.02876820724517809;
        if(bi==1) alpha=.00020020026706730793;
        object bulk=Medium("bulk absorption control",1.5,alpha);
        object bmat=Refractive("bulk control",air,bulk,0,1);
        for(double ti=0;ti<2;ti=ti+1) {
            double thickness=10*(ti+1);
            object data=LIDData("bulk",(),0,256,256,(-.05,.05,-.05,.05),1,$);
            object bscene=ObjectVector("bulk fixture",(
                SurfaceShape("entry",PlanarSurface("entry",$,AxisSystem("",$,(0,0,0),(0,0,-1),(1,0,0)),(-100,100,-100,100)),bmat),
                SurfaceShape("exit",PlanarSurface("exit",$,AxisSystem("",$,(0,0,thickness),(0,0,1),(1,0,0)),(-100,100,-100,100)),bmat),
                SurfaceShape("detector",InfinitySurface(""),CandelaSensor("sensor",1,data,(air,1,0,0))),
                PointLight("source",AxisSystem("",$,(0,0,-1),(0,0,1),(1,0,0)),RayFileEmitter("rays","interface.ray",0,air))));
            object btracer=ForwardRayTrace("bulk probe",0,16,1,bscene);
            double bok=btracer.traceRays(65536,1,0,0,mode);
            rows << (mode,1,bi,0,0,thickness,bok,data.integral());
            cout << "BULK_RESULT " << mode << " " << bi << " " << thickness << " " << data.integral() << endl;
        }
    }
}
object out=JSONStore("engine-result.json",0,1);
out << (rows,0);
out=null;
cout << "LUCIDSHAPE_MCP_OK" << endl;
