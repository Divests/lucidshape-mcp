// Fixed profile for the verified LS2.0 BIN64GPU interpreter.
object mcpNull;
object mcpUnique(object scene,string kind){
    object found=scene.getObjects(kind),unique=();
    for(double i=0;i<found.size();i++){
        object item=found[i]; double seen=0;
        for(double j=0;j<unique.size();j++){object other=unique[j]; if(item.isSameObject(other)) seen=1;}
        if(!seen) unique << item;
    }
    return unique;
}
object mcpNumeric(object p,double index){
    if(index>=p.size()) return (-1,0);
    if(p.typeAt(index)==1) return (1,p[index]);
    if(p.typeAt(index)==22) return (22,0);
    return (p.typeAt(index),0);
}
object mcpEmitter(object item){
    if(!item) return (0,"","",());
    string kind=item.nameOfClass();
    if(kind!="LambertEmitter") return (0,mcpJsonString(item.getName()),mcpJsonString(kind),());
    object p=item.getDataVector();
    if(p.size()!=9) return (0,mcpJsonString(item.getName()),mcpJsonString(kind),());
    double unit=0,flux=0;
    if(p.typeAt(1)==1) flux=p[1];
    else {
        if(p.typeAt(1)!=3 && p.typeAt(1)!=30 && p.typeAt(1)!=10) return (0,mcpJsonString(item.getName()),kind,());
        object f=p[1];
        if(f.size()!=2 || f.typeAt(0)!=1 || f.typeAt(1)!=1) return (0,mcpJsonString(item.getName()),kind,());
        unit=f[0]; flux=f[1];
    }
    if(p.typeAt(5)!=1 || p.typeAt(6)!=1 || (p.typeAt(7)!=3 && p.typeAt(7)!=30 && p.typeAt(7)!=10) || p.typeAt(8)!=1) return (0,mcpJsonString(item.getName()),kind,());
    object spectrum=p[7];
    if(p[5]!=0 || p[6]!=0 || spectrum.size()!=0 || p[8]!=0 || (unit!=0 && unit!=1)) return (0,mcpJsonString(item.getName()),kind,());
    object medium=mcpNull;
    if(p.typeAt(2)==3) medium=p[2];
    return (1,mcpJsonString(item.getName()),kind,(unit,flux,mcpNumeric(p,3),mcpNumeric(p,4),mcpDescribe(medium)));
}
object mcpSource(object item){
    object axis=item.getAxisSystem(),origin=axis.getOrigin(),z=axis.getZAxis(),x=axis.getXAxis();
    object p=item.getDataVector();
    return (mcpJsonString(item.getName()),mcpJsonString(item.nameOfClass()),origin.getDataVector(),z.getDataVector(),x.getDataVector(),mcpNumeric(p,3),mcpEmitter(item.getMaterial()));
}
object mcpSelectedSource(object scene,string name){
    object all=mcpUnique(scene,"Shape"),selected=mcpNull;
    double matches=0;
    for(double i=0;i<all.size();i++){
        object item=all[i];
        string itemName=item.getName();
        if(strcmp(itemName,name)==0){matches=matches+1;selected=item;}
    }
    cout << "MCP_SOURCE_MATCH_COUNT " << matches << endl;
    if(matches!=1) return mcpNull;
    string kind=selected.nameOfClass();
    if(kind!="PointLight" && kind!="PointLightSource") return mcpNull;
    return selected;
}
object mcpEdit(object source,object change){
    object material=source.getMaterial(),info=mcpEmitter(material);
    if(info[0]!=1) return mcpNull;
    object fields=info[3];
    if(change[0]>=0 && fields[0]!=0) return mcpNull;
    object replacement=material.deepClone();
    if(!replacement || replacement.isSameObject(material)) return mcpNull;
    object parameters=replacement.getDataVector();
    if(change[0]>=0) parameters[1]=change[0];
    if(change[1]>=0) parameters[3]=change[1];
    if(change[2]>=0) parameters[4]=change[2];
    double modified=replacement.doubleInvoke("modify",parameters);
    if(!modified) return mcpNull;
    if(!source.setMaterial(replacement)) return mcpNull;
    object actual=source.getMaterial();
    if(!actual.isSameObject(replacement)) return mcpNull;
    return replacement;
}
object mcpLoad(string filename){
    object scene=ObjectVector("MCP isolated model",());
    if(!scene.readFileIntoGroup(filename,$,1)) return mcpNull;
    return scene;
}
