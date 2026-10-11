"""Static extraction for the reviewed VAL-1 recipe only; never executes Python.

This checks installed source/config consistency, not host observation or arbitrary
Python semantics. The dispatcher must independently enforce the exact code pin.
"""
import ast
import json


def derive_configuration(code):
    tree = ast.parse(code)
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name == "case1_plane_wave_conservation"]
    if len(functions) != 1:
        raise ValueError("unsupported_recipe_structure")
    statements = tree.body + functions[0].body
    env = {}

    def value(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float, bool, str):
            return node.value
        if isinstance(node, ast.Name) and node.id in env:
            return env[node.id]
        if isinstance(node, (ast.Tuple, ast.List)):
            return [value(n) for n in node.elts]
        if isinstance(node, ast.BinOp):
            a, b = value(node.left), value(node.right)
            if type(a) not in (int, float) or type(b) not in (int, float):
                raise ValueError("unsupported_recipe_expression")
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.FloorDiv):
                return a // b
        raise ValueError("unsupported_recipe_expression")

    wanted = {"CFL", "dx", "Nx", "Ny", "A", "x0", "sigma", "yc", "x1", "x2"}
    seen = set()
    calls = {}
    for statement in statements:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
            continue
        target = statement.targets[0]
        names = ([target.id] if isinstance(target, ast.Name) else
                 [n.id for n in target.elts if isinstance(n, ast.Name)]
                 if isinstance(target, ast.Tuple) else [])
        if any(n in wanted for n in names):
            if not set(names) <= wanted or seen.intersection(names):
                raise ValueError("ambiguous_recipe_assignment")
            values = value(statement.value)
            if len(names) == 1:
                values = [values]
            if len(names) != len(values):
                raise ValueError("recipe_assignment_shape")
            env.update(zip(names, values)); seen.update(names)
        if isinstance(target, ast.Name) and target.id in {"domain", "medium", "time_axis", "SETTINGS"}:
            if target.id in calls:
                raise ValueError("ambiguous_recipe_call")
            calls[target.id] = statement.value
    if seen != wanted:
        raise ValueError("missing_recipe_assignment")

    def call(name, spelling, positional, keys):
        node = calls.get(name)
        if not isinstance(node, ast.Call) or ast.unparse(node.func) != spelling:
            raise ValueError("unsupported_recipe_call")
        if len(node.args) != positional or {k.arg for k in node.keywords} != set(keys) or len(node.keywords) != len(keys):
            raise ValueError("unsupported_recipe_arguments")
        return node, {k.arg: value(k.value) for k in node.keywords}

    domain, _ = call("domain", "jw.Domain", 2, [])
    if value(domain.args[0]) != [env["Nx"], env["Ny"]] or value(domain.args[1]) != [env["dx"], env["dx"]]:
        raise ValueError("domain_configuration_mismatch")
    medium, med = call("medium", "jw.Medium", 1, ["sound_speed", "density", "pml_size"])
    axis, timing = call("time_axis", "jw.TimeAxis.from_medium", 1, ["cfl", "t_end"])
    _, settings = call("SETTINGS", "TimeWavePropagationSettings", 0, ["smooth_initial"])
    if ast.unparse(medium.args[0]) != "domain" or ast.unparse(axis.args[0]) != "medium":
        raise ValueError("recipe_context_mismatch")
    return {"domain_N": [env["Nx"], env["Ny"]], "domain_dx_m": [env["dx"], env["dx"]],
            "sound_speed_m_s": med["sound_speed"], "density_kg_m3": med["density"],
            "pml_size": med["pml_size"], "cfl": timing["cfl"], "t_end_s": timing["t_end"],
            "initial_pressure_pa": env["A"], "initial_velocity": "zero",
            "source_x_index": env["x0"], "sigma_grid": env["sigma"],
            "sensor_indices": [[env["x1"], env["yc"]], [env["x2"], env["yc"]]],
            "smooth_initial": settings["smooth_initial"]}


def verify_configuration(code, expected):
    try:
        actual = derive_configuration(code)
        matched = json.dumps(actual, sort_keys=True, allow_nan=False) == json.dumps(expected, sort_keys=True, allow_nan=False)
    except (ValueError, TypeError, SyntaxError, KeyError, ZeroDivisionError, OverflowError):
        matched = False
    return {"static_configuration_matched": matched, "parameters_bound": False,
            "trusted_run": False}
