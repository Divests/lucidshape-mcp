object null;
object outputNames=@@OUTPUT_NAMES@@;
object outputs=(), components=();
for(double g=0;g<outputNames.size();g=g+1) {
    outputs << LIDData("merged far field",(),0,@@GRID_U@@,@@GRID_V@@,@@SENSOR_BOUNDS@@,1,$);
    components << ObjectVector("batch components",());
}
double addData(string filename,object targets) {
    object loaded=ObjectVector("batch",());
    if(!loaded.readFileIntoGroup(filename,$,1)) return 0;
    object found=loaded.getObjects("UVData");
    if(found.size()!=1) return 0;
    object data=found[0];
    double u0,u1,v0,v1,lo,hi;
    data.range(u0,u1,v0,v1,lo,hi);
    object bounds=@@SENSOR_BOUNDS@@;
    if(data.sizeU()!=@@GRID_U@@ || data.sizeV()!=@@GRID_V@@ || data.angleType()!=0 || !data.isInCandela() || data.isSpectral() || u0!=bounds[0] || u1!=bounds[1] || v0!=bounds[2] || v1!=bounds[3]) return 0;
    for(double t=0;t<targets.size();t=t+1) {
        object target=outputs[targets[t]], items=components[targets[t]];
        if(!target.plus(data)) return 0;
        items << data;
    }
    return 1;
}
@@INPUTS@@
double maxError=0;
object results=();
for(double group=0;group<outputs.size();group=group+1) {
    object output=outputs[group], items=components[group];
    for(double j=0;j<@@GRID_V@@;j=j+1) {
        for(double i=0;i<@@GRID_U@@;i=i+1) {
            double sum=0;
            for(double k=0;k<items.size();k=k+1) { object item=items[k]; sum=sum+item.value(i,j); }
            double e=abs(output.value(i,j)-sum)/(1+sum);
            if(e>maxError) maxError=e;
        }
    }
    string filename; filename << outputNames[group] << ".lid";
    object saved=Store(filename);saved << output.cloneData(0);saved=null;
    results << (outputNames[group],output.integral());
}
object out=JSONStore("engine-result.json",0,1);
out << (results,maxError);out=null;
cout << "LUCIDSHAPE_MCP_OK" << endl;
