// Based on the LS2.0 Store and Point3 examples. No preprocessor required.
object null;
object output = Store("point.lug");
output << Point3(0,0,1);
output = null;
cout << "LUCIDSHAPE_MCP_OK" << endl;
