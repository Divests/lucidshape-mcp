object null;
object scene=ObjectVector("MCP input",());
if (!scene.readFileIntoGroup("input.lue",$,1)) {
    cout << "LUCIDSHAPE_MCP_FAILED: model load failed" << endl;
    return;
}
object emitters=scene.getObjects("EmitterMaterial");
object sensors=scene.getObjects("SensorMaterial");
if (emitters.size()==0 || sensors.size()==0 || sensors.size()>64) {
    cout << "LUCIDSHAPE_MCP_FAILED: require emitters and 1 to 64 sensors" << endl;
    return;
}
double i;
for(i=0; i<sensors.size(); i++) {
    object sensor=sensors[i];
    if (!sensor.isOfClassName("CandelaSensor") && !sensor.isOfClassName("LuxSensor") && !sensor.isOfClassName("LumenSensor") && !sensor.isOfClassName("Cdm2Sensor")) {
        cout << "LUCIDSHAPE_MCP_FAILED: unsupported sensor " << sensor.nameOfClass() << endl;
        return;
    }
    object data=sensor.getUVData();
    if (!data) {
        cout << "LUCIDSHAPE_MCP_FAILED: sensor has no light data" << endl;
        return;
    }
}
object tracer=ForwardRayTrace("MCP model trace",@@INTENSITY@@,@@RECURSION@@,1.0,scene);
if (!tracer.traceRays(@@RAYS@@,1,0,0)) {
    cout << "LUCIDSHAPE_MCP_FAILED: traceRays returned false" << endl;
    return;
}
object rows=();
for(i=0; i<sensors.size(); i++) {
    object sensor=sensors[i];
    object data=sensor.getUVData();
    object snapshot=data.cloneData(0);
    string file;
    file << "sensor_" << i << ".lid";
    object output=Store(file);
    output << snapshot;
    output=null;
    rows << (sensor.getName(),sensor.nameOfClass(),file);
}
object out=JSONStore("engine-result.json",0,1);
out << (@@RAYS@@,emitters.size(),rows);
out=null;
cout << "LUCIDSHAPE_MCP_OK" << endl;
