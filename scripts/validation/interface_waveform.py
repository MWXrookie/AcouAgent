"""Signed full-waveform normal-incidence interface diagnostic, never a grant.

Fixed two homogeneous lossless half-spaces, 1D plane Gaussian initial pressure,
zero initial velocity. The jump separates cells 191/192, hence interface at
191.5*dx. This explicit geometric assumption must agree with solver discretization.
Not a general heterogeneous-medium or energy-field acceptance rule.
"""
import json,math
from scripts.validation.interface_evidence import CONFIGURATION,PROFILE,validate as peaks
from scripts.validation.cylindrical_evidence import unique,number

WIDE_CONFIGURATION=dict(CONFIGURATION,domain_N=[384,320],domain_dx_m=[.00025,.0005],sensor_indices=[[160,160],[224,160]])
WIDE_PROFILE="val1-interface-wide-v2"

def analytic_traces(times,configuration=CONFIGURATION):
    cfg=configuration;dx=cfg["domain_dx_m"][0]
    source=cfg["source_x_index"]*dx
    boundary=(cfg["interface_x_index"]-.5)*dx
    xleft,xright=[p[0]*dx for p in cfg["sensor_indices"]]
    sigma=cfg["sigma_grid"]*dx;c1,c2=cfg["sound_speed_m_s"]
    z1,z2=[c*rho for c,rho in zip(cfg["sound_speed_m_s"],cfg["density_kg_m3"])]
    r=(z2-z1)/(z2+z1);t=2*z2/(z1+z2)
    gaussian=lambda distance:math.exp(-.5*(distance/sigma)**2)
    left=[.5*(gaussian(xleft-source-c1*at)+gaussian(xleft-source+c1*at)
              +r*gaussian(2*boundary-xleft-source-c1*at)) for at in times]
    right=[.5*t*gaussian(boundary-source+(xright-boundary)*c1/c2-c1*at) for at in times]
    return left,right

def _validate(document,configuration,profile):
    denied=dict(numeric_pass=False,quality_level="Q0",trusted_run=False,
        runtime_approved=False,run_bound=False,parameters_bound=False,
        profile=profile+".signed-waveform",reason="invalid_interface_waveform",
        source_authenticated=False)
    try:
        from scripts.validation.interface_evidence import _validate as coefficients
        old=coefficients(document,configuration,profile)
        if old.get("numeric_pass") is not True:return dict(denied,reason="interface_coefficient_failed")
        raw=json.loads(document,object_pairs_hook=unique)["raw_evidence"]
        times=[number(t) for t in raw["time"]]
        measured=[[number(x) for x in raw[name]] for name in ("pressure_left","pressure_right")]
        expected=analytic_traces(times,configuration)
        # Full covered traces, preserving sign and arrival timing. A small ratio
        # error cannot hide a shifted, inverted or otherwise wrong waveform.
        errors=[];maximum_errors=[]
        for actual,theory in zip(measured,expected):
            denominator=sum(p*p for p in theory)
            if denominator<=0:raise ValueError("missing_reference")
            errors.append(math.sqrt(sum((a-b)**2 for a,b in zip(actual,theory))/denominator))
            maximum_errors.append(max(abs(a-b) for a,b in zip(actual,theory))/max(abs(p) for p in theory))
        passed=all(error<.01 for error in errors+maximum_errors)
        return dict(denied,numeric_pass=passed,quality_level="Q2" if passed else "Q1",
            reason="fixed_signed_waveform_diagnostic_only",waveform_relative_errors=errors,
            maximum_sample_relative_errors=maximum_errors,
            coefficient_error_pct=old["error_pct"],tolerance_relative=.01,
            interface_position_m=(configuration["interface_x_index"]-.5)*configuration["domain_dx_m"][0])
    except (ValueError,TypeError,KeyError,OverflowError,RecursionError,UnicodeError):return denied


def validate(document):
    return _validate(document,CONFIGURATION,PROFILE)

def validate_wide(document):
    """Separate explicit wide transverse domain; never silently alters the old case."""
    return _validate(document,WIDE_CONFIGURATION,WIDE_PROFILE)
