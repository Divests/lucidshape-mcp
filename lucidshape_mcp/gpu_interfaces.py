"""Interpret fixed directional-interface and Beer absorption controls."""
from .results import sequence, number

def read_result(directory, values):
    rows=sequence(sequence(values,2)[0],40)
    if values[1]!=0:raise ValueError('Invalid interface probe marker')
    expected_keys=[]
    for mode in (20,13):
        expected_keys.extend((mode,0,p,s,a,0) for p in range(4) for s in range(2) for a in (0,30))
        expected_keys.extend((mode,1,p,0,0,t) for p in range(2) for t in (10,20))
    output=[]
    for row,key in zip(rows,expected_keys):
        sequence(row,8)
        if tuple(row[:6])!=key or row[6]!=1 or not 0<=number(row[7])<=1001:
            raise ValueError('Invalid directional interface probe result')
        mode,kind,profile,side,angle,thickness=key
        target=None
        if kind==0 and profile<2:target=1000 if profile==0 else 750
        if kind==1:target=1000*(.75 if profile==0 else .998)**(thickness/10)
        output.append(dict(mode=mode,kind='interface' if kind==0 else 'bulk',profile=profile,
            side=side,angle_deg=angle,thickness=thickness,flux_lm=row[7],
            control_expected_lm=target,control_passed=None if target is None else abs(row[7]-target)<.1))
    log=(directory/'engine.log').read_text(encoding='utf-8',errors='replace').lower()
    verified=log.count('nurbs intersection type is 13 (graphics card)')==20 and 'switching to' not in log
    signed=[r for r in output if r['kind']=='interface' and r['profile']==2]
    return dict(cases=output,native_GPU_execution_verified=verified,
        controls_passed=all(r['control_passed'] for r in output if r['control_expected_lm'] is not None) and verified,
        signed_theta_side_selective_observed=all(abs(r['flux_lm']-(1000 if r['side']==0 else 250))<.1 for r in signed) and verified,
        S9_physics_accepted=False,S9_simulation_run=False,approved_materials_changed=False,
        scope='Fixed normal/30-degree interfaces, asymmetric theta table, and two slab thicknesses. Bulk controls are diagnostic parameters, not approved S9 materials.'),[]
