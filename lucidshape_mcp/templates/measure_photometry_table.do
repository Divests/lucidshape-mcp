#include <LidTestTables/lidMeasureTask_v0.luf>

object noObject, data;
if (@@IS_IES@@) data = IESData("MCP test table", @@INPUT@@);
else {
    object group = ObjectVector("MCP LID", ());
    if (!group.readFileIntoGroup(@@INPUT@@,$,1)) {
        cout << "LUCIDSHAPE_MCP_FAILED: could not read LID" << endl;
        return;
    }
    object uv = group.getObjects("UVData");
    if (uv.size() == 1) data = uv[0];
}
if (!data || !data.isInCandela() || data.isSpectral() || data.angleType()!=1 ||
    data.sizeU()<2 || data.sizeV()<2 || data.sizeW()!=1 || data.sizeT()!=1) {
    cout << "LUCIDSHAPE_MCP_FAILED: require Type A two-dimensional nonspectral candela" << endl;
    return;
}
double x0,y0,x1,y1,w,t;
data.positionOf(0,0,0,0,x0,y0,w,t);
data.positionOf(data.sizeU()-1,data.sizeV()-1,0,0,x1,y1,w,t);
if (x0>@@XMIN@@ || x1<@@XMAX@@ || y0>@@YMIN@@ || y1<@@YMAX@@) {
    cout << "LUCIDSHAPE_MCP_FAILED: test positions outside sampled grid" << endl;
    return;
}
lidMeasureSetGlobals();
double peakH,peakV;
double peak=data.maxValue(peakH,peakV);
object table=new LidMeasureTable(@@TABLE@@,"GB 4599-2024",0,0);
object tasks=List();
@@TASKS@@
table.m_measureTasks=tasks;
object result=new LidMeasureResultTable();
table.measure(data,1,0,0,result);
object textStream=OStream("MCP photometry report","native-measurements.txt");
object printer=new LidMeasureOutput(textStream);
printer.setup(0,100,100,0);
result.print(printer);
printer=noObject;
textStream=noObject;
object rows=();
for(double i=0;i<result.size();i++){
    object row=result.at(i);
    object positions=();
    object points=row.m_pointList;
    for(double j=0;j<row.m_numPoints;j++){
        object xy=points[j];
        positions << (xy[0],xy[1]);
    }
    object found=();
    if(row.isOfClassName("LidMeasureResultUfVf")) found=(row.m_uf,row.m_vf);
    rows << (row.m_name,row.m_value,row.m_min,row.m_max,row.m_nok,positions,found);
}
object out=JSONStore("engine-result.json",0,1);
out << (@@TABLE@@,(x0,x1,y0,y1),(peak,peakH,peakV),rows);
out=noObject;
cout << "LUCIDSHAPE_MCP_OK" << endl;
