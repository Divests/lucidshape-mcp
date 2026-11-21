// Isolated UV action. Only fixed native operations and validated numbers are inserted.
object null;
object lid=null;
if (@@SOURCE_IS_IES@@) {
    lid=IESData("MCP source photometry","source.ies");
} else {
    object group=ObjectVector("MCP source photometry",());
    if (!group.readFileIntoGroup("source.lid",$,1)) {
        cout << "LUCIDSHAPE_MCP_FAILED: LID load failed" << endl;
        return;
    }
    object data=group.getObjects("UVData");
    if (data.size()!=1) {
        cout << "LUCIDSHAPE_MCP_FAILED: require exactly one UVData" << endl;
        return;
    }
    lid=data[0];
}
if (!lid || !lid.isInCandela() || lid.isSpectral() || lid.sizeW()!=1 || lid.sizeT()!=1 ||
    lid.sizeU()<2 || lid.sizeV()<2 || lid.sizeU()*lid.sizeV()>1000000 || lid.angleType()>2) {
    cout << "LUCIDSHAPE_MCP_FAILED: require 2D nonspectral candela photometry with 4..1000000 cells" << endl;
    return;
}
double pu,pv;
object before=(lid.sizeU(),lid.sizeV(),lid.maxValue(pu,pv),lid.integral());
@@NATIVE_ACTION@@
object after=(lid.sizeU(),lid.sizeV(),lid.maxValue(pu,pv),lid.integral());
object report=JSONStore("action-result.json",0,1);
report << (before,after);
report=null;
@@WRITE_CHECKED_IES@@
