"""Raw work/flux balance for uniform, lossless, stationary linear acoustics.

Derivation: p_t=-rho*c^2*div(u)+q, u_t=-grad(p)/rho+a gives
dE/dt + integral_boundary(p*u.n) = integral_volume(p*q/(rho*c^2)+rho*u.a).
Hongbin Ju, Acoustic Energy and Intensity, equations 24-25:
https://www.math.fsu.edu/~hju/cht6.pdf
The source terms above are derived by multiplying these explicit equations;
they are not jwave's source amplitude API. No source mapping is assumed.

Inputs are collocated cell-volume and face-area quadrature samples at each
listed instant. Staggered fields must be independently time/space centered
before this diagnostic. Trapezoidal time integration needs separate refinement
evidence; sampled balance is neither a PDE test nor provenance certification.
"""
import math
from app.physics_consistency import _array, _number, _receipt

MAX_FRAMES=256
MAX_TOTAL_VALUES=100000


def _face(value, shape):
    if not shape:
        return [_number(value)]
    if type(value) is not list or len(value)!=shape[0]:
        raise ValueError("boundary_face_shape")
    return [v for item in value for v in _face(item,shape[1:])]


def assess_energy_balance(*,times_s,pressure_pa,velocity_m_s,pressure_rate_pa_s,
                          acceleration_m_s2,boundary_faces,spacing_m,
                          density_kg_m3,sound_speed_m_s,model,sampling):
    """Every interval must balance to <1%, without cancellation across intervals.

    model must be uniform_lossless_stationary_linear.
    sampling must be collocated_cell_volume_and_face_area.
    Every bounding face is mandatory (even zero flux): keys 0-/0+/1-/1+/...
    Each frame has pressure_pa and outward_velocity_m_s fields; negative
    outward velocity means inflow. Two dimensions report energy per unit depth.
    No caller-supplied energy, work, verdict or tolerance is accepted.
    """
    def measure():
        if model!="uniform_lossless_stationary_linear" or sampling!="collocated_cell_volume_and_face_area":
            raise ValueError("unsupported_energy_model_or_sampling")
        if type(times_s) is not list or not 2<=len(times_s)<=MAX_FRAMES:
            raise ValueError("time_frame_budget")
        times=[_number(t) for t in times_s]
        if times[0]<0 or any(b<=a for a,b in zip(times,times[1:])):
            raise ValueError("nonmonotone_time")
        series=(pressure_pa,velocity_m_s,pressure_rate_pa_s,acceleration_m_s2)
        if any(type(s) is not list or len(s)!=len(times) for s in series):
            raise ValueError("incomplete_time_series")
        shape,_=_array(pressure_pa[0]);dims=len(shape);cells=math.prod(shape)
        face_keys={str(axis)+sign for axis in range(dims) for sign in ("-","+")}
        if type(boundary_faces) is not dict or set(boundary_faces)!=face_keys:
            raise ValueError("complete_outward_boundary_required")
        face_cells=sum(2*math.prod(shape[:axis]+shape[axis+1:]) for axis in range(dims))
        if len(times)*(cells*(2+2*dims)+2*face_cells)>MAX_TOTAL_VALUES:
            raise ValueError("aggregate_raw_value_budget")
        if type(spacing_m) is not list or len(spacing_m)!=dims:
            raise ValueError("spacing_dimensions")
        dx=[_number(v,True) for v in spacing_m];volume=math.prod(dx)
        rho=_number(density_kg_m3,True);c=_number(sound_speed_m_s,True)
        bulk=rho*c*c
        if not math.isfinite(bulk) or bulk<=0:
            raise ValueError("invalid_bulk_modulus")
        if not math.isfinite(volume) or volume<=0:
            raise ValueError("cell_volume")
        energies=[];source_power=[];outward_power=[0.0]*len(times)
        def field(raw):
            actual,values=_array(raw)
            if actual!=shape:raise ValueError("different_volume_grids")
            return values
        def vector(raw):
            if type(raw) is not list or len(raw)!=dims:
                raise ValueError("vector_components")
            return [field(v) for v in raw]
        for p_raw,u_raw,q_raw,a_raw in zip(*series):
            p,u,q,a=field(p_raw),vector(u_raw),field(q_raw),vector(a_raw)
            energies.append(volume*math.fsum(
                [(v/c)**2/(2*rho) for v in p]+
                [rho*v*v/2 for component in u for v in component]))
            source_power.append(volume*math.fsum(
                [v/bulk*rate for v,rate in zip(p,q)]+
                [rho*vel*acc for component,force in zip(u,a) for vel,acc in zip(component,force)]))
        for axis in range(dims):
            face_shape=shape[:axis]+shape[axis+1:]
            area=math.prod(dx[:axis]+dx[axis+1:])
            for sign in ("-","+"):
                frames=boundary_faces[str(axis)+sign]
                if type(frames) is not list or len(frames)!=len(times):
                    raise ValueError("incomplete_face_time_series")
                for i,frame in enumerate(frames):
                    if type(frame) is not dict or set(frame)!={"pressure_pa","outward_velocity_m_s"}:
                        raise ValueError("boundary_fields")
                    p=_face(frame["pressure_pa"],face_shape)
                    un=_face(frame["outward_velocity_m_s"],face_shape)
                    outward_power[i]+=area*math.fsum(v*w for v,w in zip(p,un))
        if any(not math.isfinite(v) for seq in (energies,source_power,outward_power) for v in seq):
            raise ValueError("nonfinite_energy_budget")
        errors=[];work=[];flux=[];residuals=[]
        for i,(start,end) in enumerate(zip(times,times[1:])):
            dt=end-start
            w=dt*(source_power[i]/2+source_power[i+1]/2)
            f=dt*(outward_power[i]/2+outward_power[i+1]/2)
            delta=energies[i+1]-energies[i]
            scale=max(energies[i],energies[i+1],abs(w),abs(f))
            if scale<=0 or not math.isfinite(scale):
                raise ValueError("unobservable_zero_energy")
            residual=delta-w+f
            errors.append(abs(residual)/scale)
            work.append(w);flux.append(f);residuals.append(residual)
        return max(errors),dict(
            energy_unit="J" if dims==3 else "J_per_m_depth",
            energy_samples=energies,source_work_intervals=work,
            outward_flux_intervals=flux,residual_intervals=residuals,
            interval_relative_errors=errors,interval_count=len(errors),
            integration="trapezoidal_raw_power_each_interval",
            refinement_authenticated=False)
    return _receipt("driven_lossless_work_flux_balance",measure)
