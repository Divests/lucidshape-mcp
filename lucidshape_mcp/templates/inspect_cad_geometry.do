// MCP STEP geometry preflight. No Studio changes, native-file roundtrip or optical assumptions.
object null;
object imported=ObjectVector("MCP STEP inspection",());
if(!imported.readFileIntoGroup("@@CAD_FILENAME@@",$,1)) {
    cout << "LUCIDSHAPE_MCP_FAILED: STEP import failed" << endl;
    return;
}
object all=imported.getObjects();
object surfaces=imported.getObjects("Surface");
double surfaceCount=surfaces.size();
surfaces.onlyRoots();
if(surfaces.size()==0) {
    cout << "LUCIDSHAPE_MCP_FAILED: STEP contains no root surfaces" << endl;
    return;
}
double xmin=1e100,xmax=-1e100,ymin=1e100,ymax=-1e100,zmin=1e100,zmax=-1e100;
if(!surfaces.checkBoundingBox(xmin,xmax,ymin,ymax,zmin,zmax)) {
    cout << "LUCIDSHAPE_MCP_FAILED: geometry bounds unavailable" << endl;
    return;
}
object rows=();
object matches=();
double matchCount=0;
for(double i=0;i<all.size();i=i+1) {
    object item=all[i];
    string name=item.getName();
    string cleanName;
    regex_replace(name,"[[:cntrl:]]"," ",cleanName);
    string normalized;
    regex_replace(cleanName,"\\\\","/",normalized);
    if(i<@@MAX_OBJECTS@@) rows << (normalized,item.nameOfClass());
    if(normalized!="") {
        matchCount=matchCount+1;
        if(matches.size()<@@MAX_OBJECTS@@) matches << (normalized,item.nameOfClass());
    }
}
object topRows=();
for(double k=0;k<imported.size() && k<@@MAX_OBJECTS@@;k=k+1) {
    object top=imported[k];
    string cleanTopName;
    regex_replace(top.getName(),"[[:cntrl:]]"," ",cleanTopName);
    string topName;
    regex_replace(cleanTopName,"\\\\","/",topName);
    topRows << (topName,top.nameOfClass());
}
object out=JSONStore("engine-result.json",0,1);
out << (all.size(),surfaceCount,surfaces.size(),(xmin,xmax,ymin,ymax,zmin,zmax),rows,matchCount,matches,imported.size(),topRows);
out=null;
cout << "LUCIDSHAPE_MCP_OK" << endl;
