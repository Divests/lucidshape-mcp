object cases=(),rows=();
double s;
@@CASE_ROWS@@
for(double caseIndex=0;caseIndex<cases.size();caseIndex++){
    string baseline;baseline << "case_" << caseIndex << "_input.lue";
    object scene=mcpLoad(baseline);
    if(!scene){cout << "LUCIDSHAPE_MCP_FAILED: model load failed" << endl;return;}
    object source=mcpSelectedSource(scene,@@SOURCE@@);
    if(!source){cout << "LUCIDSHAPE_MCP_FAILED: require exactly one named PointLight" << endl;return;}
    object before=mcpSource(source),change=cases[caseIndex];
    object replacement=mcpEdit(source,change);
    if(!replacement){cout << "LUCIDSHAPE_MCP_FAILED: unsupported emitter or modification failed" << endl;return;}
    object sensors=mcpUnique(scene,"SensorMaterial");
    if(sensors.size()<1 || sensors.size()>64){cout << "LUCIDSHAPE_MCP_FAILED: require 1..64 sensors" << endl;return;}
    for(s=0;s<sensors.size();s++){
        object sensor=sensors[s];string cls=sensor.nameOfClass();
        if(cls!="CandelaSensor"){cout << "LUCIDSHAPE_MCP_FAILED: sweep requires CandelaSensor results" << endl;return;}
        object data=sensor.getUVData();
        if(!data || !data.isInCandela() || data.isSpectral() || data.sizeW()!=1 || data.sizeT()!=1){cout << "LUCIDSHAPE_MCP_FAILED: unsupported sensor data" << endl;return;}
    }
    object tracer=ForwardRayTrace("MCP parameter trial",@@INTENSITY@@,@@RECURSION@@,1,scene);
    tracer.setRandomSeed(@@SEED@@);
    if(!tracer.traceRays(@@RAYS@@,1,0,0,0)){cout << "LUCIDSHAPE_MCP_FAILED: CPU trace failed" << endl;return;}
    object outputs=();
    for(s=0;s<sensors.size();s++){
        object sensor=sensors[s],data=sensor.getUVData();
        object copy=data.deepClone();
        if(!copy || copy.isSameObject(data)){cout << "LUCIDSHAPE_MCP_FAILED: independent result copy failed" << endl;return;}
        string filename;filename << "case_" << caseIndex << "_sensor_" << s << ".lid";
        object output=Store(filename);output << copy;output=mcpNull;
        double pu,pv,peak=copy.maxValue(pu,pv);
        outputs << (mcpJsonString(sensor.getName()),filename,copy.angleType(),copy.sizeU(),copy.sizeV(),copy.integral(),peak,pu,pv);
    }
    rows << (caseIndex,before,mcpSource(source),outputs);
}
object out=JSONStore("engine-result.json",0,1);
out << (@@SEED@@,@@RAYS@@,rows);out=mcpNull;
cout << "LUCIDSHAPE_MCP_OK" << endl;
