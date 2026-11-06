object null;
object group=ObjectVector("MCP LID input",());
object lid=null;
if(@@IS_IES@@) {
    lid=IESData("MCP IES analysis","@@DATA_FILENAME@@");
} else {
    if (!group.readFileIntoGroup("@@DATA_FILENAME@@",$,1)) {
        cout << "LUCIDSHAPE_MCP_FAILED: LID load failed" << endl;
        return;
    }
    object found=group.getObjects("UVData");
    if(found.size()!=1) {
        cout << "LUCIDSHAPE_MCP_FAILED: require exactly one light-data object" << endl;
        return;
    }
    lid=found[0];
}
if(!lid.isInCandela() || lid.isSpectral() || lid.sizeU()<1 || lid.sizeV()<1 || lid.sizeW()!=1 || lid.sizeT()!=1) {
    cout << "LUCIDSHAPE_MCP_FAILED: only 2D non-spectral candela LID is supported" << endl;
    return;
}
double u0,u1,v0,v1,minVal,maxVal,peakU,peakV;
lid.range(u0,u1,v0,v1,minVal,maxVal);
double peak=lid.maxValue(peakU,peakV);
double regionFlux=0;
if(@@HAS_REGION@@) {
    if(@@U0@@<u0 || @@U1@@>u1 || @@V0@@<v0 || @@V1@@>v1) {
        cout << "LUCIDSHAPE_MCP_FAILED: requested region is outside the LID angular range" << endl;
        return;
    }
    regionFlux=lid.zoneIntegral(@@U0@@,@@U1@@,@@V0@@,@@V1@@);
}
object out=JSONStore("engine-result.json",0,1);
out << (lid.getName(),lid.angleType(),(lid.sizeU(),lid.sizeV(),lid.sizeW(),lid.sizeT()),(u0,u1,v0,v1),(peak,peakU,peakV),minVal,lid.integral(),regionFlux);
out=null;
cout << "LUCIDSHAPE_MCP_OK" << endl;
