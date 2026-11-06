// A small point-source scene based on ProgrammersReference, pages 387-389.
// Fixed geometry; this template is a connection demo, not a vehicle lamp design.
object lid = newLIDFile("demo LID",(),0,60,60,(-60,60,-60,60),1.0,"demo.lid");
object detector = SurfaceShape("detector", InfinitySurface(""), CandelaSensor("sensor",1.0,lid));
object emitter = PointLight("source", AxisSystem("",$,(0,0,0),(0,0,1),(1,0,0)), LambertEmitter("",@@FLUX@@));
object scene = ObjectVector("demo",(emitter,detector));
object tracer = ForwardRayTrace("demo trace",0.1,16,1.0,scene);
double traceOK = tracer.traceRays(@@RAYS@@,1,0,0);
if (traceOK) {
    double writeOK = lid.writeFile("demo.lid");
    if (writeOK) {
        object null;
        object output = Store("demo.lue");
        output << scene;
        output = null;
        cout << "LUCIDSHAPE_MCP_OK" << endl;
    } else {
        cout << "LUCIDSHAPE_MCP_FAILED: could not write LID" << endl;
    }
} else {
    cout << "LUCIDSHAPE_MCP_FAILED: traceRays returned false" << endl;
}
