double g_luxType=0;
#include <lidmeasure/lidmeasure1.hdo>
lidMeasureSetGlobals();
object nil, data;
if (@@IES@@) data=IESData("MCP photometry",@@INPUT@@);
else {
    object group=ObjectVector("photometry",());
    if (!group.readFileIntoGroup(@@INPUT@@,$,1)) return;
    object items=group.getObjects("UVData");
    if (items.size()==1) data=items[0];
}
if (!data || !data.isInCandela() || data.isSpectral() || data.angleType()!=1 ||
    data.sizeU()<2 || data.sizeV()<2 || data.sizeW()!=1 || data.sizeT()!=1) {
    cout << "LUCIDSHAPE_MCP_FAILED: require one Type A two-dimensional candela distribution" << endl;
    return;
}
double h0,h1,v0,v1,w,t;
data.positionOf(0,0,0,0,h0,v0,w,t);
data.positionOf(data.sizeU()-1,data.sizeV()-1,0,0,h1,v1,w,t);
double mode=@@MODE@@, u0=@@U0@@, u1=@@U1@@, y0=@@V0@@, y1=@@V1@@;
if ((mode==0 || mode==1 || mode==4 || mode==6) &&
    (u0<h0 || u1>h1 || y0<v0 || y1>v1)) {
    cout << "LUCIDSHAPE_MCP_FAILED: coordinates outside the sampled grid" << endl;
    return;
}
object values=(),pos=();
double lo,hi,lu,lv,hu,hv;
if (mode==0) {
    values << data.lidMeasureValueAt(u0,y0,1);
    pos << (u0,y0);
} else if (mode==1) {
    if (!data.zoneMinMaxValue(u0,u1,y0,y1,lo,hi,lu,lv,hu,hv)) {
        cout << "LUCIDSHAPE_MCP_FAILED: no native zone extrema" << endl;
        return;
    }
    values << lo << hi;
    pos << (lu,lv) << (hu,hv);
} else if (mode==2) {
    values << data.lidMeasureFindMinAndPos(1,lu,lv);
    pos << (lu,lv);
} else if (mode==3) {
    values << data.lidMeasureFindMaxAndPos(1,hu,hv);
    pos << (hu,hv);
} else if (mode==4) {
    double du=data.deltaU(),dv=data.deltaV();
    if (du<=0 || dv<=0) return;
    double nu=round((u1-u0)/du+1),nv=round((y1-y0)/dv+1);
    if (nu<1 || nv<1 || nu*nv>4000000) {
        cout << "LUCIDSHAPE_MCP_FAILED: unsupported zone sample count" << endl;
        return;
    }
    du=0; dv=0;
    if(nu>1) du=(u1-u0)/(nu-1);
    if(nv>1) dv=(y1-y0)/(nv-1);
    double sumValue=0;
    double i,j;
    for(i=0;i<nu;i++){
        for(j=0;j<nv;j++){
            sumValue=sumValue+data.lidMeasureValueAt(u0+i*du,y0+j*dv,1);
        }
    }
    double meanValue=sumValue/(nu*nv);
    values << meanValue;
} else if (mode==5) {
    values << data.integral();
} else {
    values << data.zoneIntegral(u0,u1,y0,y1);
}
object output=JSONStore("engine-result.json",0,1);
output << (mode,(h0,h1,v0,v1),values,pos);
output=nil;
cout << "LUCIDSHAPE_MCP_OK" << endl;
