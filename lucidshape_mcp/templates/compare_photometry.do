object inputs=(@@REFERENCE@@,@@CANDIDATE@@),formats=(@@REF_IES@@,@@CAND_IES@@),rows=();
double i,j;
for(double k=0;k<2;k++){
    object data=mcpNull;string filename=inputs[k];
    if(formats[k]) data=IESData("MCP comparison",filename);
    else {
        object scene=mcpLoad(filename);
        if(!scene){cout << "LUCIDSHAPE_MCP_FAILED: LID load failed" << endl;return;}
        object found=mcpUnique(scene,"UVData");
        if(found.size()!=1){cout << "LUCIDSHAPE_MCP_FAILED: require exactly one UVData" << endl;return;}
        data=found[0];
    }
    if(!data || !data.isInCandela() || data.isSpectral() || data.sizeW()!=1 || data.sizeT()!=1 || data.sizeU()<2 || data.sizeV()<2 || data.sizeU()*data.sizeV()>1000000){
        cout << "LUCIDSHAPE_MCP_FAILED: require 2D nonspectral candela and at most one million cells" << endl;return;
    }
    double u0,u1,v0,v1,lo,hi,pu,pv;
    data.range(u0,u1,v0,v1,lo,hi);double peak=data.maxValue(pu,pv);
    object uaxis=(),vaxis=();double u,v,w,t;
    for(i=0;i<data.sizeU();i++){data.positionOf(i,0,0,0,u,v,w,t);uaxis << u;}
    for(j=0;j<data.sizeV();j++){data.positionOf(0,j,0,0,u,v,w,t);vaxis << v;}
    string gridfile;gridfile << "grid_" << k << ".csv";
    object grid=OStream("MCP grid",gridfile);grid.precision(17);
    grid << "u_index,v_index,intensity_cd" << endl;
    for(j=0;j<data.sizeV();j++)for(i=0;i<data.sizeU();i++) grid << i << "," << j << "," << data.value(i,j) << endl;
    grid=mcpNull;
    rows << (mcpJsonString(data.getName()),data.angleType(),data.sizeU(),data.sizeV(),(u0,u1,v0,v1),uaxis,vaxis,(peak,pu,pv),data.integral());
}
object out=JSONStore("engine-result.json",0,1);out << rows;out=mcpNull;
cout << "LUCIDSHAPE_MCP_OK" << endl;
