#pragma NOPP
#include "native-road.hdo"
object mcpNull;
object mcpRoadGrid(object data,string name){
    double u0,u1,v0,v1,lo,hi,pu,pv,u,v,w,t;
    data.range(u0,u1,v0,v1,lo,hi);
    double peak=data.maxValue(pu,pv);
    object ua=(),va=();
    for(double i=0;i<data.sizeU();i++){data.positionOf(i,0,0,0,u,v,w,t);ua << u;}
    for(double j=0;j<data.sizeV();j++){data.positionOf(0,j,0,0,u,v,w,t);va << v;}
    string fn; fn << name << ".csv";
    object grid=OStream("MCP road grid",fn);grid.precision(17);
    grid << "u_index,v_index,u,v,value" << endl;
    for(j=0;j<data.sizeV();j++)for(i=0;i<data.sizeU();i++){
        data.positionOf(i,j,0,0,u,v,w,t);
        grid << i << "," << j << "," << u << "," << v << "," << data.value(i,j) << endl;
    }
    grid=mcpNull;
    return (data.sizeU(),data.sizeV(),data.dataType(),data.angleType(),
            (data.isInLux(),data.isInCDM2()),(u0,u1,v0,v1),ua,va,(peak,pu,pv));
}
object input;
if(@@IS_IES@@) input=IESData("MCP road input",@@INPUT@@);
else {
    object group=ObjectVector("input",());
    if(!group.readFileIntoGroup(@@INPUT@@,$,1)){cout<<"LUCIDSHAPE_MCP_FAILED: input read failed"<<endl;return;}
    object found=group.getObjects("UVData");
    if(found.size()!=1){cout<<"LUCIDSHAPE_MCP_FAILED: require one input LID"<<endl;return;}
    input=found[0];
}
if(!input || !input.isInCandela() || input.isSpectral() || input.sizeW()!=1 || input.sizeT()!=1 || input.angleType()>1){
    cout<<"LUCIDSHAPE_MCP_FAILED: require Type A/B nonspectral candela"<<endl;return;
}
object parameter=@@PARAMETERS@@;
object roadTracer,road=roadIllum_createRoadIllumination(input,input,"native road illuminance",parameter,roadTracer);
if(!road){cout<<"LUCIDSHAPE_MCP_FAILED: road generation failed"<<endl;return;}
object out=Store("road.lid");out << road;out=mcpNull;
object roadMeta=mcpRoadGrid(road,"road");
parameter[LIDTOOLS_ROAD_SENSOR_TYPE]=1;
parameter[LIDTOOLS_ROAD_METER_FACTOR]=1;
object luminanceRoad;
object driverTracer=roadIllum_makeRoadTracer(input,input,"native diffuse road",parameter,luminanceRoad,0);
if(!driverTracer){cout<<"LUCIDSHAPE_MCP_FAILED: driver tracer failed"<<endl;return;}
object graphic=();
object driver=roadIllum_createLuminanceImage2(driverTracer,"native driver luminance",parameter,graphic);
if(!driver){cout<<"LUCIDSHAPE_MCP_FAILED: driver generation failed"<<endl;return;}
driver.scaleByFactor(@@REFLECTANCE@@);
road.saveUVAsBMP("road.png",1,2,0,0);
driver.saveUVAsBMP("driver.png",1,5,0,0);
driver.saveUVAsBMP("driver-color.png",1,2,0,0);
out=Store("driver.lid");out << driver;out=mcpNull;
object driverMeta=mcpRoadGrid(driver,"driver");
out=JSONStore("engine-result.json",0,1);
out << (1,(input.angleType(),input.sizeU(),input.sizeV(),input.integral()),roadMeta,driverMeta,@@REFLECTANCE@@);
out=mcpNull;
cout<<"LUCIDSHAPE_MCP_OK"<<endl;

