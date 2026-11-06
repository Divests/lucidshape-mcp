// Native RayFile inspection only; no scene edit or optical simulation.
object mcpNull;
object mcpRayFile=RayFile("MCP input ray file","input.ray",0);
double mcpFormat=mcpRayFile.getFormat();
double mcpSpectral=mcpRayFile.isSpectral();
double mcpCount=mcpRayFile.countRays();
double mcpChecked=mcpRayFile.checkRayFile("input.ray");
object mcpReport=JSONStore("engine-result.json",0,1);
mcpReport << (mcpFormat,mcpSpectral,mcpCount,mcpChecked);
mcpReport=mcpNull;
cout << "LUCIDSHAPE_MCP_OK" << endl;
