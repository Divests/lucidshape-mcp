// Fixed read-only selected-lens checkpoint diagnostic; no photometric result.
@@SCENE_SETUP@@
object starts=@@REPLAY_STARTS@@;
object finder=RayPathFinder("project boundary checkpoint diagnostic",0.0000000001,256,1,scene);
object records=();
for(double k=0;k<starts.size();k=k+1) {
    object start=starts[k];
    object medium=air;
    if(start[3]>1.1) medium=replayLens;
    for(double attempt=0;attempt<@@REPLAY_ATTEMPTS@@;attempt=attempt+1) {
        object path=finder.traceRayPath(LRay("checkpoint",start[1],start[2],1000000,1,medium));
        if(path.size()<1 || path.size()>256) {
            cout << "LUCIDSHAPE_MCP_FAILED: invalid checkpoint path length" << endl;
            return;
        }
        object rows=();
        for(double j=0;j<path.size();j=j+1) {
            object ray=path[j],d=ray.getDataVector();
            object p=d[1],v=d[2],m=d[5];
            double index=1;
            if(m) index=m.getRefractionIndex();
            rows << ((p[0],p[1],p[2]),(v[0],v[1],v[2]),d[3],d[4],index);
        }
        records << (start[0],attempt,rows);
        cout << "CHECKPOINT_TRACED " << start[0] << " " << attempt << " " << path.size() << endl;
    }
}
object out=JSONStore("engine-result.json",0,1);
out << (regions,records,geometryBounds);
out=null;
cout << "LUCIDSHAPE_MCP_OK" << endl;
