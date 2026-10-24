"""Run only the directed RNG seam, with explicit model guards and software counts."""

from collections import Counter, defaultdict
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import sys
import time
import traceback


def main():
    """Snapshot inputs before import, block models, and retain every run result."""
    root = Path(__file__).resolve().parents[2]
    docs = Path(__file__).resolve().parent
    run_id = sys.argv[1]
    selection = sys.argv[2] if len(sys.argv) > 2 else ""
    assert Path(run_id).name == run_id
    destination = docs / "runs" / run_id
    destination.mkdir(parents=True, exist_ok=False)
    source = root / "src/pyibisami/ami/model.py"
    test = root / "tests/ami/test_response_rng.py"
    files = {"source.py.txt": source, "test.py.txt": test, "runner.py.txt": Path(__file__)}
    files.update({name: docs / name for name in ("fixtures.json", "environment.json")})
    hashes = {}
    for name, path in files.items():
        raw = path.read_bytes()
        (destination / name).write_bytes(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    plan = {
        "run_id": run_id, "selection": selection, "input_sha256": hashes,
        "boundary": "actual unbound get_responses, plain fake self, Python wave stub",
        "load_order": "preload ordinary NumPy/ctypes/pytest, then guards, then product import",
        "preload_limit": "general platform/system-library internals not fully traced",
        "allowed": "ordinary NumPy arrays/RNG and response NumPy calculations on frozen arrays",
        "forbidden": "model constructors/loaders, production Init/GW/Close and DSP/parser/engine helpers",
    }
    (destination / "plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    started = time.perf_counter()
    stdout, stderr = io.StringIO(), io.StringIO()
    counts = Counter()
    numpy_calls = defaultdict(Counter)
    blocked = Counter()
    active = stub_depth = factory_depth = 0
    exit_code = 1
    receipt = dict(plan)

    def forbid(reason):
        blocked[reason] += 1
        raise RuntimeError("RNG seam guard: " + reason)

    def forbidden(*args, **kwargs):
        forbid("model/library/helper entry")

    class ImportGuard:
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split(".")[0] in {
                "pybert", "pychopmarg", "parsec", "scipy", "matplotlib", "PySide6",
            }:
                forbid("import " + fullname)
            if fullname.startswith("pyibisami.") and fullname not in {
                "pyibisami.ami", "pyibisami.ami.model", "pyibisami.common",
            }:
                forbid("import " + fullname)
            return None

    def audit(event, args):
        if event in ("ctypes.dlopen", "ctypes.dlsym"):
            forbid(event)

    def profile(frame, event, arg):
        nonlocal active, stub_depth, factory_depth
        name = frame.f_code.co_qualname
        filename = Path(frame.f_code.co_filename)
        is_response = filename == source and name == "AMIModel.get_responses"
        is_stub = filename == test and name == "FakeResponseSelf.getWave"
        is_factory = filename == test and name.endswith("controlled_default_rng")
        if event == "call":
            if is_response:
                counts["production_get_responses"] += 1
                active += 1
            elif is_stub:
                counts["Python_getWave_stub"] += 1
                stub_depth += 1
            elif is_factory:
                counts["controlled_default_factory"] += 1
                factory_depth += 1
            if filename == source and name == "AmiModelResponseKey.__post_init__":
                counts["response_key_post_init"] += 1
            elif filename == source and name not in {
                "<module>", "AmiModelResponseKey", "AMIModelError", "AMIModelInitializer",
                "AMIModel", "_InitData", "AMIModel.get_responses",
            }:
                forbid("production method " + name)
            owner = frame.f_locals.get("self")
            if type(owner).__name__ == "AmiModelResponseKey" and filename.name == "<string>":
                counts["response_key_generated_" + frame.f_code.co_name] += 1
            if frame.f_globals.get("__name__") == "pyibisami.common" and frame.f_code.co_name != "<module>":
                forbid("common helper " + name)
        phase = "outside_response"
        if active:
            phase = "wave_stub" if stub_depth else "controlled_factory" if factory_depth else "production"
        if event == "call" and frame.f_globals.get("__name__", "").startswith("numpy"):
            numpy_calls[phase]["Python:" + frame.f_globals["__name__"] + "." + name] += 1
        elif event == "c_call":
            owner = getattr(arg, "__self__", None)
            module = getattr(arg, "__module__", "") or type(owner).__module__
            if module.startswith("numpy"):
                numpy_calls[phase]["C:" + module + "." + getattr(arg, "__qualname__", str(arg))] += 1
        if event == "return":
            if is_response:
                active -= 1
            elif is_stub:
                stub_depth -= 1
            elif is_factory:
                factory_depth -= 1

    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
            sys.dont_write_bytecode = True
            preload = time.perf_counter()
            import ctypes  # pylint: disable=import-outside-toplevel
            import numpy as np  # pylint: disable=import-outside-toplevel
            import pytest  # pylint: disable=import-outside-toplevel

            receipt["preload_elapsed_s"] = time.perf_counter() - preload
            receipt.update({"executable": sys.executable, "python": sys.version,
                            "numpy": np.__version__, "pytest": pytest.__version__})
            for name in ("CDLL", "PyDLL", "WinDLL", "OleDLL"):
                if hasattr(ctypes, name):
                    setattr(ctypes, name, forbidden)
            for name in ("cdll", "pydll", "windll", "oledll"):
                loader = getattr(ctypes, name, None)
                if loader is not None:
                    loader._dlltype = forbidden
                    loader.LoadLibrary = forbidden
            sys.addaudithook(audit)
            sys.meta_path.insert(0, ImportGuard())
            sys.setprofile(profile)
            sys.path.insert(0, str(root / "src"))
            assert "pyibisami.ami.model" not in sys.modules
            model = importlib.import_module("pyibisami.ami.model")
            common = importlib.import_module("pyibisami.common")
            assert Path(model.__file__).resolve() == source
            assert Path(common.__file__).resolve() == root / "src/pyibisami/common.py"
            receipt["actual_module_file"] = model.__file__
            receipt["common_module_file"] = common.__file__
            for cls in (model.AMIModel, model.AMIModelInitializer):
                cls.__new__ = staticmethod(forbidden)
            for name in ("initialize", "_initialize", "getWave", "close", "__del__"):
                setattr(model.AMIModel, name, forbidden)
            model.deconv_same = common.deconv_same = common.raised_cosine = forbidden
            model._debug_plot = forbidden
            legacy_before = np.random.get_state()
            args = [str(test), "--noconftest", "-p", "no:cacheprovider", "-p", "no:capture", "--color=no"]
            if selection:
                args.extend(["-k", selection])
            exit_code = int(pytest.main(args))
            legacy_after = np.random.get_state()
            assert legacy_before[0] == legacy_after[0] and legacy_before[2:] == legacy_after[2:]
            assert np.array_equal(legacy_before[1], legacy_after[1])
            receipt["legacy_global_rng_unchanged_whole_run"] = True
            assert counts["response_key_post_init"] == 4
            assert counts["response_key_generated___init__"] == 4
        except Exception:
            traceback.print_exc()
            exit_code = 1
        finally:
            sys.setprofile(None)
    for name, path in files.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == hashes[name]
    for name, value in (("stdout.txt", stdout.getvalue()), ("stderr.txt", stderr.getvalue())):
        (destination / name).write_text(value, encoding="utf-8", newline="\n")
    receipt.update({
        "exit_code": exit_code, "elapsed_s": time.perf_counter() - started,
        "software_calls": dict(counts), "numpy_profile_observed_events": dict(numpy_calls),
        "numpy_profile_limit": "observed Python/C entry events, not exhaustive low-level operations",
        "blocked_attempts": dict(blocked), "total_task_wall_peak_RSS": "UNKNOWN",
        "restricted_actual_calls": dict.fromkeys([
            "Main", "COM", "coopt", "AMIModel_or_Initializer_construction", "production_initialize",
            "production_getWave", "production_close", "C_Init", "C_GetWave", "C_Close",
            "model_CDLL", "engine", "deconv_same", "other_DSP_helper", "AMI_grammar_parser",
            "model_download", "model_compile",
        ], 0),
        "output_sha256": {name: hashlib.sha256((destination / name).read_bytes()).hexdigest()
                          for name in ("stdout.txt", "stderr.txt")},
    })
    (destination / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"run": run_id, "exit_code": exit_code, "software_calls": dict(counts),
                      "blocked_attempts": dict(blocked), "elapsed_s": receipt["elapsed_s"]}))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
