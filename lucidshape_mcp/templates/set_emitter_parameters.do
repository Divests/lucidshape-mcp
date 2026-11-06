object scene=mcpLoad("input.lue");
if(!scene){cout << "LUCIDSHAPE_MCP_FAILED: model load failed" << endl;return;}
object source=mcpSelectedSource(scene,@@SOURCE@@);
if(!source){cout << "LUCIDSHAPE_MCP_FAILED: require exactly one named PointLight" << endl;return;}
object original=source.getMaterial(),before=mcpSource(source);
object change=@@CHANGE@@;
object replacement=mcpEdit(source,change);
if(!replacement){cout << "LUCIDSHAPE_MCP_FAILED: unsupported emitter or modification failed" << endl;return;}
object after=mcpSource(source);
object out=Store("modified.lue");out << scene;out=mcpNull;
object loaded=mcpLoad("modified.lue");
if(!loaded){cout << "LUCIDSHAPE_MCP_FAILED: modified model reload failed" << endl;return;}
object readSource=mcpSelectedSource(loaded,@@SOURCE@@);
if(!readSource){cout << "LUCIDSHAPE_MCP_FAILED: modified source reload failed" << endl;return;}
object reread=mcpSource(readSource);
out=JSONStore("engine-result.json",0,1);
out << (before,after,reread,mcpEmitter(original));out=mcpNull;
cout << "LUCIDSHAPE_MCP_OK" << endl;
