object scene=mcpLoad("input.lue");
if(!scene){cout << "LUCIDSHAPE_MCP_FAILED: model load failed" << endl; return;}
object kinds=("Shape","EmitterMaterial","Medium","SensorMaterial");
object groups=();
for(double k=0;k<kinds.size();k++){
    string kind=kinds[k];
    object items=mcpUnique(scene,kind),rows=();
    for(double i=0;i<items.size() && i<@@LIMIT@@;i++){
        object item=items[i]; string cls=item.nameOfClass();
        object details=(); string status="unsupported";
        if(cls=="PointLight" || cls=="PointLightSource"){details=mcpSource(item);status="available";}
        else if(cls=="LambertEmitter"){details=mcpEmitter(item); if(details[0]==1) status="available";}
        else if(cls=="Medium"){
            object p=item.getDataVector();
            if(p.size()==6){details=(mcpNumeric(p,1),mcpNumeric(p,2));status="partial";}
        }
        else if(cls=="SurfaceShape" || cls=="MultiSurfaceShape"){
            details=(mcpDescribe(item.getMaterial()),mcpDescribe(item.getExitMaterial())); status="relationships_only";
        }
        else if(cls=="CandelaSensor" || cls=="LuxSensor" || cls=="LumenSensor" || cls=="Cdm2Sensor"){
            object data=item.getUVData();
            if(data){details=(mcpDescribe(data),data.sizeU(),data.sizeV(),data.sizeW(),data.sizeT(),data.angleType(),data.isSpectral(),data.isInCandela());status="metadata_only";}
        }
        rows << (mcpJsonString(item.getName()),mcpJsonString(cls),status,details);
    }
    groups << (kind,items.size(),rows);
}
object out=JSONStore("engine-result.json",0,1); out << groups; out=mcpNull;
cout << "LUCIDSHAPE_MCP_OK" << endl;
