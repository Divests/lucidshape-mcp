// Full native Monte Carlo paths; loss is applied only at completed medium visits.
object inputs=ObjectVector("sample input",());
if(!inputs.readFileIntoGroup("sampled-rays.lue",$,1)) {
    cout << "LUCIDSHAPE_MCP_FAILED: sampled ray input root unavailable" << endl;
    return;
}
object sampled=inputs;
if(inputs.size()==1) {
    object root=inputs[0];
    // A single source is also a one-item container; unwrap only the scene root.
    if(root.isOfClassName("ObjectVector") && root.size()==@@SOURCE_COUNT@@) sampled=root;
}
cout << "SAMPLED_GROUPS " << sampled.size() << endl;
object sourceIds=@@SOURCE_IDS@@;
if(sampled.size()!=@@SOURCE_COUNT@@) {
    cout << "LUCIDSHAPE_MCP_FAILED: sampled source groups differ" << endl;
    return;
}
@@SCENE_SETUP@@
object finder=RayPathFinder("native stochastic paths",0.0000000001,256,1,scene);
object terminal=();
object statistics=();
for(double led=0;led<@@SOURCE_COUNT@@;led=led+1) {
    object samples=sampled[led];
    if(samples.size()!=@@RAYS_PER_LED@@) {
        cout << "LUCIDSHAPE_MCP_FAILED: sampled ray count differs" << endl;
        return;
    }
    double escaped=0,stopped=0,steps=0;
    @@TOTAL_DECLARATIONS@@
    for(double i=0;i<samples.size();i=i+1) {
        object inputRay=samples[i];
        object path=finder.traceRayPath(inputRay);
        if(path.size()<1 || path.size()>256) {
            cout << "LUCIDSHAPE_MCP_FAILED: empty or excessive ray history" << endl;
            return;
        }
        double previous=1;
        @@VISIT_DECLARATIONS@@
        object last;
        for(double j=0;j<path.size();j=j+1) {
            object ray=path[j];
            object d=ray.getDataVector();
            object medium=d[5];
            double n=1;
            if(medium) n=medium.getRefractionIndex();
            @@VISIT_UPDATES@@
            previous=n;
            last=d;
        }
        steps=steps+path.size();
        if(last[3]>1e30) {
            if(@@UNPAIRED@@) {
                object failureRows=();
                for(double f=0;f<path.size();f=f+1) {
                    object fr=path[f];
                    object fd=fr.getDataVector();
                    object fp=fd[1],fv=fd[2],fm=fd[5];
                    double fn=1;
                    if(fm) fn=fm.getRefractionIndex();
                    double originalLength=fd[3];
                    if(originalLength<1e30) originalLength=originalLength/@@COORDINATE_SCALE@@;
                    failureRows << ((fp[0]/@@COORDINATE_SCALE@@,fp[1]/@@COORDINATE_SCALE@@,fp[2]/@@COORDINATE_SCALE@@),(fv[0],fv[1],fv[2]),originalLength,fd[4],fn);
                }
                object failed=JSONStore("incomplete-path.json",0,1);
                failed << (sourceIds[led],i,0,0,0,0,previous,failureRows,"original model mm",@@COORDINATE_SCALE@@);
                failed=null;
                cout << "LUCIDSHAPE_MCP_FAILED: escaping path has an incomplete or ambiguous lens visit" << endl;
                return;
            }
            object p=last[1],v=last[2];
            double weight=last[4];
            @@APPLY_LOSSES@@
            terminal << (sourceIds[led],p[0],p[1],p[2],v[0],v[1],v[2],last[4]@@EXIT_FIELDS@@,weight);
            escaped=escaped+1;
            @@ACCUMULATE@@
        } else {
            stopped=stopped+1;
        }
    }
    statistics << (sourceIds[led],samples.size(),escaped,stopped@@COUNT_FIELDS@@,steps);
    cout << "SOURCE_TRACED " << sourceIds[led] << " escaped=" << escaped << " stopped=" << stopped << endl;
}
object terminals=JSONStore("terminal-rays.json",0,1);
terminals << (terminal,terminal.size());
terminals=null;
object out=JSONStore("engine-result.json",0,1);
out << (regions,statistics,terminal.size(),geometryBounds);
out=null;
cout << "LUCIDSHAPE_MCP_OK" << endl;
