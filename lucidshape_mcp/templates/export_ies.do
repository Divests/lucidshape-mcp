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
@@WRITE_CHECKED_IES@@
