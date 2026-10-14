"""Independent d'Alembert diagnostic for the reviewed planar initial recipe.

Uniform lossless medium, periodic 32mm domain, Gaussian initial pressure
(center 12mm, sigma 2mm, peak 1Pa), zero initial particle velocity, c=1500m/s.
The solution is half of the periodic initial field shifted in each direction.
Reference: UQ Applied Mathematical Analysis, d'Alembert's formula.
https://teaching.smp.uq.edu.au/scims/Appl_analysis/dalembert.html
The periodic Gaussian image extension is our stated model choice. Images beyond
the nearest three periods contribute below floating-point precision for this
16-sigma domain. It is not a driven/heterogeneous/point-source validator or
provenance/VAL-1 five-class acceptance. All trust remains false.
"""
import math
from app import physics_consistency as gate


def reference_profile(size,sample_time_s):
    if type(size) is not int or size not in {64,128}:
        raise ValueError("reviewed_grid_required")
    time=gate._number(sample_time_s,True)
    if time>2.2e-6:raise ValueError("reviewed_time_window")
    dx=.032/size
    def initial(x):
        x=x%.032
        return sum(math.exp(-.5*((x-.012+image*.032)/.002)**2) for image in (-1,0,1))
    distance=1500*time
    return [.5*(initial(i*dx-distance)+initial(i*dx+distance)) for i in range(size)]


def assess(*,pressure,sample_time_s):
    def measure():
        shape,values=gate._array(pressure)
        if len(shape)!=2 or shape[0]!=shape[1] or shape[0] not in {64,128}:
            raise ValueError("reviewed_planar_shape")
        expected=reference_profile(shape[0],sample_time_s)
        reference=[value for value in expected for _ in range(shape[1])]
        return gate._difference(reference,values),dict(
            model="uniform_periodic_planar_gaussian_zero_initial_velocity",
            metric="max_full_field_absolute_error_over_reference_peak",
            reference="independent_dalembert",sample_time_s=sample_time_s)
    return gate._receipt("planar_initial_dalembert",measure)
