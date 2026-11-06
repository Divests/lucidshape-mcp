"""Fixed read-only CATIA part inspection. No selection, activation, open or save."""
from pathlib import Path
import re


AXES_SCRIPT = '''
Function ReadPartAxes(part)
    Dim rows(), n, i, ax
    n = part.AxisSystems.Count
    If n = 0 Then
        ReadPartAxes = Array()
        Exit Function
    End If
    ReDim rows(n-1)
    For i = 1 To n
        Set ax = part.AxisSystems.Item(i)
        rows(i-1) = ReadOneAxis(ax)
    Next
    ReadPartAxes = rows
End Function
Function ReadOneAxis(ax)
    Dim o(2), x(2), y(2), z(2)
    On Error Resume Next
    ax.GetOrigin o
    ax.GetXAxis x
    ax.GetYAxis y
    ax.GetZAxis z
    If Err.Number <> 0 Then
        ReadOneAxis = Array(ax.Name, Err.Description)
        Err.Clear
    Else
        ReadOneAxis = Array(ax.Name, Array(o(0),o(1),o(2)), Array(x(0),x(1),x(2)), Array(y(0),y(1),y(2)), Array(z(0),z(1),z(2)))
    End If
End Function
'''

MATERIALS_SCRIPT = '''
Function ReadPartMaterials(part)
    Dim manager, rows(), i, body
    On Error Resume Next
    Set manager = part.GetItem("CATMatManagerVBExt")
    If Err.Number <> 0 Then
        ReadPartMaterials = Array(Array("error",part.Name,"",Err.Description))
        Exit Function
    End If
    ReDim rows(part.Bodies.Count)
    rows(0) = ReadOneMaterial(manager,part,"part")
    For i = 1 To part.Bodies.Count
        Set body = part.Bodies.Item(i)
        rows(i) = ReadOneMaterial(manager,body,"body")
    Next
    ReadPartMaterials = rows
End Function
Function ReadOneMaterial(manager, target, kind)
    Dim mat, materialName, message
    Set mat = Nothing
    materialName = ""
    message = ""
    On Error Resume Next
    If kind = "part" Then
        manager.GetMaterialOnPart target, mat
    Else
        manager.GetMaterialOnBody target, mat
    End If
    If Err.Number <> 0 Then
        message = Err.Description
        Err.Clear
    ElseIf Not (mat Is Nothing) Then
        materialName = mat.Name
    End If
    ReadOneMaterial = Array(kind,target.Name,materialName,message)
End Function
'''


def properties(value, names):
    result={}
    for name in names:
        try:
            item=getattr(value,name)
            if isinstance(item,(str,int,float,bool)) or item is None:
                result[name]=item
        except Exception:
            pass
    return result


def parameter_rows(collection, limit=2000):
    total=int(collection.Count)
    rows=[]
    errors=[]
    for i in range(1,min(total,limit)+1):
        try:
            item=collection.Item(i)
            row=properties(item,("Name","Comment","IsTrueParameter","OptionalRelation"))
            try:
                row["value"]=str(item.ValueAsString())
            except Exception:
                pass
            row.update(properties(item,("Value",)))
            rows.append(row)
        except Exception as error:
            errors.append({"index":i,"error":str(error)})
    return {"count":total,"rows":rows,"truncated":total>limit,"errors":errors}


def inspect_part(application, documents, arguments, describe):
    source=Path(arguments["catia_path"])
    canonical=lambda value:str(value).replace("/","\\").casefold()
    matches=[documents.Item(i) for i in range(1,documents.Count+1)
             if canonical(describe(documents.Item(i))["full_name"])==canonical(source)]
    if len(matches)!=1:
        raise RuntimeError("The exact CATPart must already be uniquely open for read-only inspection; no document was opened")
    document=matches[0]
    part=document.Part
    result={"backend":"CATIA COM Automation","read_only":True,"source_path":str(source),
            "native_document":describe(document),"part":properties(part,("Name",)),"computer_use_calls":0,"errors":{}}
    try:
        product=document.Product
        result["product"]=properties(product,("Name","PartNumber","Revision","Definition","Nomenclature","DescriptionRef","DescriptionInst","Source"))
        result["user_properties"]=parameter_rows(product.UserRefProperties)
    except Exception as error:
        result["errors"]["product_properties"]=str(error)
    result["bodies"]=[]
    for i in range(1,part.Bodies.Count+1):
        body=part.Bodies.Item(i)
        row={"name":str(body.Name),"feature_count":int(body.Shapes.Count),"features":[]}
        for j in range(1,min(body.Shapes.Count,200)+1):
            row["features"].append(properties(body.Shapes.Item(j),("Name",)))
        try:
            row["associated_parameters"]=parameter_rows(part.Parameters.SubList(body,True),200)
        except Exception as error:
            row["parameter_query_error"]=str(error)
        result["bodies"].append(row)
    result["body_count"]=len(result["bodies"])
    try:
        result["parameters"]=parameter_rows(part.Parameters)
    except Exception as error:
        result["errors"]["parameters"]=str(error)
    try:
        result["axis_count"]=int(part.AxisSystems.Count)
        native=application.SystemService.Evaluate(AXES_SCRIPT,1,"ReadPartAxes",(part,))
        result["axes"]=[]
        for row in native:
            if len(row)!=5:
                result["axes"].append({"name":str(row[0]),"error":str(row[1])})
                continue
            result["axes"].append({"name":str(row[0]),"origin_mm":[float(v) for v in row[1]],
                                   "x_direction":[float(v) for v in row[2]],"y_direction":[float(v) for v in row[3]],
                                   "z_direction":[float(v) for v in row[4]]})
    except Exception as error:
        result["errors"]["axes"]=str(error)
    result["root_axis_collection_count"]=result.get("axis_count")
    result["axis_parameters"]=[{"name":row["Name"],"description":row["value"]}
        for row in result.get("parameters",{}).get("rows",[])
        if len(re.findall(r"(?:Origin[XYZ]|[XYZ]Axis[XYZ])\s*=",row.get("value","")))==12]
    result["parameter_axis_count"]=len(result["axis_parameters"])
    try:
        native=application.SystemService.Evaluate(MATERIALS_SCRIPT,1,"ReadPartMaterials",(part,))
        result["materials"]=[{"scope":str(row[0]),"target":str(row[1]),"material_name":str(row[2]) or None,
                              "query_error":str(row[3]) or None} for row in native]
    except Exception as error:
        result["errors"]["materials"]=str(error)
    result["hybrid_sets"]=[]
    def hybrid_rows(collection,depth=0):
        for i in range(1,min(collection.Count,200)+1):
            group=collection.Item(i)
            result["hybrid_sets"].append({"name":str(group.Name),"depth":depth,"shape_count":int(group.HybridShapes.Count),
                "shape_names":[str(group.HybridShapes.Item(j).Name) for j in range(1,min(group.HybridShapes.Count,200)+1)]})
            if depth<8:
                hybrid_rows(group.HybridBodies,depth+1)
    try:
        hybrid_rows(part.HybridBodies)
    except Exception as error:
        result["errors"]["hybrid_sets"]=str(error)
    result["document_after_inspection"]=describe(document)
    return result
