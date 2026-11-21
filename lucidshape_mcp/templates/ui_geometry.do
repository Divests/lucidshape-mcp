object null;
double a=@@A@@, b=@@B@@, c=@@C@@;
object axis=AxisSystem("axis",(1,1,1,1,1,1),(@@CENTER@@),(0,0,1),(1,0,0),10);
object geo=@@CONSTRUCT@@;
if(!geo){cout << "LUCIDSHAPE_MCP_FAILED: construction failed" << endl; return;}
object item=geo;
if(@@WRAP@@) item=SurfaceShape("surface shape",geo,Specular("Ideal Specular",1.0));
object scene=ObjectVector("private geometry",(item));
object out=Store("primitive.lue");
out << scene;
out=null;
object loaded=ObjectVector("reread",());
double ok=loaded.readFileIntoGroup("primitive.lue",$,1);
object found=loaded.getObjects("@@CLASS@@");
if(!ok || found.size()!=1){cout << "LUCIDSHAPE_MCP_FAILED: cannot reload primitive" << endl; return;}
double xmin=1e100, xmax=-1e100, ymin=1e100, ymax=-1e100, zmin=1e100, zmax=-1e100;
double bounded=found.checkBoundingBox(xmin,xmax,ymin,ymax,zmin,zmax);
object readPrimitive=found[0];
object shapes=loaded.getObjects("Shape");
string shapeClass="", materialClass="";
double reflectance=-1;
if(shapes.size()==1){
    object shape=shapes[0], material=shape.getMaterial();
    shapeClass=shape.nameOfClass();
    if(material){
        materialClass=material.nameOfClass();
        if(strcmp(materialClass,"Specular")==0){
            object data=material.getDataVector();
            reflectance=data[1];
        }
    }
}
object radial=();
if(@@CONE@@){
    object data=readPrimitive.getDataVector();
    radial=(data[3],data[4],data[5],data[6]);
}
out=JSONStore("engine-result.json",0,1);
out << (ok,found.size(),readPrimitive.getName(),readPrimitive.nameOfClass(),bounded,(xmin,xmax,ymin,ymax,zmin,zmax),
       (shapes.size(),shapeClass,materialClass,reflectance),radial);
out=null;
cout << "LUCIDSHAPE_MCP_OK" << endl;
