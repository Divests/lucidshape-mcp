// Runtime-verified LS2.0 ObjectGroup.getObjects(typeName).
object null;
object scene = ObjectVector("MCP input",());
if (!scene.readFileIntoGroup("input.lue",$,1)) {
    cout << "LUCIDSHAPE_MCP_FAILED: model load failed" << endl;
    return;
}
object all = scene.getObjects();
if (all.size()==0) {
    cout << "LUCIDSHAPE_MCP_FAILED: model has no objects" << endl;
    return;
}
object rows = ();
double i,j;
for(i=0; i<all.size() && i<@@MAX_OBJECTS@@; i++) {
    object item=all[i];
    rows << (item.getName(),item.nameOfClass());
}
object types=("EmitterMaterial","SensorMaterial","Geometry","ActorMaterial","UVData");
object labels=("emitters","sensors","geometry","actors","light_data");
object groups=();
for(i=0; i<types.size(); i++) {
    string type=types[i];
    object found=scene.getObjects(type);
    object members=();
    for(j=0; j<found.size() && j<@@MAX_OBJECTS@@; j++) {
        object item=found[j];
        members << (item.getName(),item.nameOfClass());
    }
    groups << (labels[i],found.size(),members);
}
object out=JSONStore("engine-result.json",0,1);
out << (all.size(),rows,groups);
out=null;
cout << "LUCIDSHAPE_MCP_OK" << endl;
