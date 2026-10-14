"""Raw numerical checks for future bound experiments; never grants Q3/Q4.

All arrays must describe instantaneous pressure at the same physical time.
This module computes diagnostics, not experiment provenance or model validity.
The coordinator must separately bind actual configurations, source/medium,
boundary conditions and sampling locations before using these measurements.
"""
import math
from app.skill_store import normalize_parameters

TOLERANCE = 0.01
MAX_VALUES = 100000
FALSE_TRUST = dict(trusted_run=False, runtime_approved=False, run_bound=False,
                   parameters_bound=False, eligible_for_trusted_activation=False)

def _number(value, positive=False):
    if type(value) not in (int,float):
        raise ValueError("numeric_value_required")
    result=float(value)
    if not math.isfinite(result) or positive and result<=0:
        raise ValueError("invalid_numeric_value")
    return result

def _array(value):
    count=[0]
    def walk(node,depth=0):
        if depth>3:raise ValueError("array_dimensions")
        if type(node) is not list:
            count[0]+=1
            if count[0]>MAX_VALUES:raise ValueError("array_budget")
            return (),[_number(node)]
        if not node:raise ValueError("empty_array")
        children=[walk(item,depth+1) for item in node]
        shape=children[0][0]
        if any(child[0]!=shape for child in children):raise ValueError("ragged_array")
        return (len(node),)+shape,[v for _,values in children for v in values]
    shape,values=walk(value)
    if len(shape) not in (2,3):raise ValueError("spatial_field_required")
    return shape,values

def _same_time(first,second):
    a,b=_number(first,True),_number(second,True)
    if not math.isclose(a,b,rel_tol=1e-9,abs_tol=0):
        raise ValueError("different_physical_times")

def _difference(reference,measured):
    scale=max(abs(v) for v in reference)
    if scale==0:raise ValueError("zero_reference_field")
    error=max(abs(a/scale-b/scale) for a,b in zip(reference,measured))
    return error

def _receipt(check_id,action):
    result=dict(FALSE_TRUST,check_id=check_id,passed=False,reason="raw_evidence_unavailable",
                tolerance_relative=TOLERANCE)
    try:
        measured=action()
        error,details=measured if type(measured) is tuple else (measured,{})
        if not math.isfinite(error):raise ValueError("nonfinite_metric")
        return dict(result,**details,passed=error<TOLERANCE,relative_error=error,
                    reason="numerical_check_passed" if error<TOLERANCE else "numerical_mismatch")
    except (ValueError,TypeError,OverflowError,RecursionError,IndexError,KeyError):
        return result

def grid_convergence(*,coarse_pressure,refined_pressure,coarse_dx_m,refined_dx_m,
                     coarse_time_s,refined_time_s):
    """Compare collocated nodal samples at 2x spatial refinement.

    This common-node diagnostic cannot exclude fine-grid subcell oscillations;
    it is not a full resampling/geometry or stability certificate.
    """
    def measure():
        _same_time(coarse_time_s,refined_time_s)
        shape,coarse=_array(coarse_pressure)
        fine_shape,fine=_array(refined_pressure)
        if len(shape)!=len(fine_shape) or any(b!=2*a for a,b in zip(shape,fine_shape)):
            raise ValueError("grid_not_refined_twice")
        if type(coarse_dx_m) is not list or type(refined_dx_m) is not list or len(coarse_dx_m)!=len(shape) or len(refined_dx_m)!=len(shape):
            raise ValueError("spacing_dimensions")
        if any(not math.isclose(_number(b,True)*2,_number(a,True),rel_tol=1e-9,abs_tol=0)
               for a,b in zip(coarse_dx_m,refined_dx_m)):
            raise ValueError("spacing_not_halved")
        import itertools
        selected=[]
        for coordinate in itertools.product(*(range(size) for size in shape)):
            offset=0
            for index,size in zip(coordinate,fine_shape):
                offset=offset*size+2*index
            selected.append(fine[offset])
        return _difference(coarse,selected)
    return _receipt("grid_convergence_common_nodes",measure)

def time_step_convergence(*,base_pressure,refined_pressure,base_dt_s,refined_dt_s,
                          base_time_s,refined_time_s):
    def measure():
        _same_time(base_time_s,refined_time_s)
        if not math.isclose(_number(refined_dt_s,True)*2,_number(base_dt_s,True),rel_tol=1e-9,abs_tol=0):
            raise ValueError("time_step_not_halved")
        shape,base=_array(base_pressure);other_shape,other=_array(refined_pressure)
        if shape!=other_shape:raise ValueError("different_spatial_samples")
        return _difference(base,other)
    return _receipt("time_step_convergence",measure)

def equivalent_parameters(*,base_pressure,equivalent_pressure,base_parameters,
                          equivalent_parameters,base_time_s,equivalent_time_s):
    def measure():
        _same_time(base_time_s,equivalent_time_s)
        if normalize_parameters(base_parameters)!=normalize_parameters(equivalent_parameters):
            raise ValueError("parameters_not_equivalent")
        shape,base=_array(base_pressure);other_shape,other=_array(equivalent_pressure)
        if shape!=other_shape:raise ValueError("different_spatial_samples")
        return _difference(base,other)
    return _receipt("equivalent_parameters",measure)

def source_free_energy(*,initial_pressure,final_pressure,initial_velocity,final_velocity,
                       density_kg_m3,sound_speed_m_s,cell_volume_m3,source_free):
    """Uniform linear acoustics: E=sum(p²/(2rho*c²)+rho*|u|²/2)*volume.

    Source-free energy may decay through absorption/outflow; it must not grow.
    Source work, heterogeneous media and driven steady sources are unsupported.
    Velocity is particle velocity in m/s, one scalar field per spatial axis.
    """
    def measure():
        if source_free is not True:raise ValueError("source_work_not_supported")
        rho,c,volume=(_number(v,True) for v in (density_kg_m3,sound_speed_m_s,cell_volume_m3))
        shape,p0=_array(initial_pressure);end_shape,p1=_array(final_pressure)
        if shape!=end_shape:raise ValueError("different_energy_grids")
        velocities=[]
        for components in (initial_velocity,final_velocity):
            if type(components) is not list or len(components)!=len(shape):
                raise ValueError("particle_velocity_components_required")
            arrays=[_array(component) for component in components]
            if any(grid!=shape for grid,_ in arrays):raise ValueError("different_velocity_grids")
            velocities.append([values for _,values in arrays])
        def energy(pressure,velocity):
            terms=[(p/c)**2/(2*rho) for p in pressure]
            terms.extend(rho*u*u/2 for component in velocity for u in component)
            value=math.fsum(terms)*volume
            if not math.isfinite(value):raise ValueError("nonfinite_energy")
            return value
        first,last=energy(p0,velocities[0]),energy(p1,velocities[1])
        if first<=0:raise ValueError("zero_initial_energy")
        return max(0,last/first-1),dict(initial_energy_j=first,final_energy_j=last,
                                       energy_change_relative=last/first-1)
    return _receipt("source_free_energy_nonincrease",measure)


def grid_convergence_full_periodic(*,coarse_pressure,refined_pressure,coarse_dx_m,
                                  refined_dx_m,coarse_time_s,refined_time_s,periodic):
    """All fine nodes versus multilinear coarse projection on a periodic grid.

    The common-node metric is retained as well. Fine nodes between coarse
    samples cannot hide oscillations. This is specific to nodal periodic data,
    not a geometry transfer rule for arbitrary boundaries or cell averages.
    """
    common=grid_convergence(coarse_pressure=coarse_pressure,refined_pressure=refined_pressure,
        coarse_dx_m=coarse_dx_m,refined_dx_m=refined_dx_m,
        coarse_time_s=coarse_time_s,refined_time_s=refined_time_s)
    def measure():
        if periodic is not True or "relative_error" not in common:
            raise ValueError("periodic_collocated_grid_required")
        shape,coarse=_array(coarse_pressure);fine_shape,fine=_array(refined_pressure)
        import itertools
        projection=[]
        for coordinate in itertools.product(*(range(size) for size in fine_shape)):
            choices=[]
            for index,size in zip(coordinate,shape):
                base=index//2
                choices.append([(base,1.0)] if index%2==0 else [(base,.5),((base+1)%size,.5)])
            total=0.0
            for combination in itertools.product(*choices):
                offset=0;weight=1.0
                for (index,w),size in zip(combination,shape):
                    offset=offset*size+index;weight*=w
                total+=coarse[offset]*weight
            projection.append(total)
        full=_difference(projection,fine)
        return max(common["relative_error"],full),dict(
            common_nodes_relative_error=common["relative_error"],
            whole_refined_field_relative_error=full,comparison="periodic_multilinear_nodal_projection")
    return _receipt("grid_convergence_whole_periodic_field",measure)
