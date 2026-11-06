// MCP native IES export; no simulation, angle conversion, filtering or scaling.
object null;
object group=ObjectVector("MCP IES input",());
if(!group.readFileIntoGroup("input.lid",$,1)) {
    cout << "LUCIDSHAPE_MCP_FAILED: LID load failed" << endl;
    return;
}
object found=group.getObjects("UVData");
if(found.size()!=1) {
    cout << "LUCIDSHAPE_MCP_FAILED: require exactly one light-data object" << endl;
    return;
}
object lid=found[0];
if(!lid.isInCandela() || lid.isSpectral() || lid.sizeU()<2 || lid.sizeV()<2 || lid.sizeW()!=1 || lid.sizeT()!=1 || lid.angleType()>2) {
    cout << "LUCIDSHAPE_MCP_FAILED: require 2D non-spectral candela data with at least 2x2 cells" << endl;
    return;
}
object ies=lid.cloneData(1);
if(!ies.writeFile("photometry.ies")) {
    cout << "LUCIDSHAPE_MCP_FAILED: IES write failed" << endl;
    return;
}
ies=null;
object reread=IESData("MCP IES readback","photometry.ies");
double u0,u1,v0,v1,lo,hi,ru0,ru1,rv0,rv1,rlo,rhi;
lid.range(u0,u1,v0,v1,lo,hi);
reread.range(ru0,ru1,rv0,rv1,rlo,rhi);
double largestError=0;
double largestRoundingUnits=0;
if(lid.sizeU()==reread.sizeU() && lid.sizeV()==reread.sizeV()) {
    for(double j=0;j<lid.sizeV();j=j+1) {
        for(double i=0;i<lid.sizeU();i=i+1) {
            double original=lid.value(i,j);
            double returned=reread.value(i,j);
            double difference=abs(original-returned);
            if(difference>largestError) largestError=difference;
            double roundingBound=0.000000000001;
            if(original>0) roundingBound=0.50001*pow(10,floor(log10(original))-5)+original*0.00000012;
            double roundingUnits=difference/roundingBound;
            if(roundingUnits>largestRoundingUnits) largestRoundingUnits=roundingUnits;
        }
    }
} else {
    largestError=-1;
        largestRoundingUnits=-1;
}
object out=JSONStore("engine-result.json",0,1);
out << (lid.angleType(),(lid.sizeU(),lid.sizeV()),(u0,u1,v0,v1),lid.borderIsOnCellCenter(),(lo,hi,lid.integral()),reread.angleType(),(reread.sizeU(),reread.sizeV()),(ru0,ru1,rv0,rv1),reread.borderIsOnCellCenter(),(rlo,rhi,reread.integral()),largestError,largestRoundingUnits);
out=null;
cout << "LUCIDSHAPE_MCP_OK" << endl;
