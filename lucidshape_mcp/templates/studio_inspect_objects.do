if(mcpHasModel){
    object mcpItems=mcpModel.getObjects(@@SELECTED@@,$,@@TYPES@@,());
    object mcpRows=();
    double mcpI;
    for(mcpI=@@OFFSET@@; mcpI<mcpItems.size() && mcpI<@@OFFSET@@+@@LIMIT@@; mcpI++){
        object mcpItem=mcpItems[mcpI];
        string mcpClass="";
        if(mcpItem) mcpClass=mcpItem.nameOfClass();
        object mcpMaterial=mcpNull,mcpExit=mcpNull,mcpSurface=mcpNull;
        double mcpMaterialQueried=0,mcpExitQueried=0,mcpSurfaceQueried=0;
        // Only concrete classes with documented native getters. Avoid probing
        // unknown members: LS2.0 may try to dynamically load a class for them.
        if(mcpClass=="SurfaceShape" || mcpClass=="MultiSurfaceShape" || mcpClass=="PointLight" || mcpClass=="CurveLight" || mcpClass=="VolumeLight"){
            mcpMaterial=mcpItem.getMaterial(); mcpMaterialQueried=1;
        }
        if(mcpClass=="SurfaceShape" || mcpClass=="MultiSurfaceShape"){
            mcpExit=mcpItem.getExitMaterial(); mcpExitQueried=1;
        }
        if(mcpClass=="SurfaceShape"){
            mcpSurface=mcpItem.getSurface(); mcpSurfaceQueried=1;
        }
        object mcpEdges=(("material",mcpMaterialQueried,mcpDescribe(mcpMaterial)),
            ("exit_material",mcpExitQueried,mcpDescribe(mcpExit)),
            ("surface",mcpSurfaceQueried,mcpDescribe(mcpSurface)));
        mcpRows << (mcpI,mcpDescribe(mcpItem),mcpEdges);
    }
    mcpPayload << mcpItems.size() << mcpRows;
}
