"""CCA-only source import bootstrap; execute directly, never with -m.

Required startup: python -I -S -B -X pycache_prefix=/dev/null cca_eob_runtime.py
This is not a sandbox against modifications to trusted source/native code.
"""

import sys

CONTRACT = "cca_eob_source_only_v1"


class RuntimeStop(BaseException):
    """Do not let site.py swallow a security refusal as an optional hook failure."""


def install():
    """Install before importing site or any non-frozen helper modules."""
    if (sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 13)
            or not sys.flags.isolated or not sys.flags.no_site
            or not sys.flags.dont_write_bytecode
            or sys._xoptions.get("pycache_prefix") != "/dev/null"):
        raise RuntimeStop("STOP: require CPython 3.13 -I -S -B -X pycache_prefix=/dev/null")
    external = sys.modules["_frozen_importlib_external"]
    bootstrap = sys.modules["_frozen_importlib"]
    import zipimport  # frozen; disable both cached and directly constructed ZIP loaders

    events = {"opened": set(), "listed": set(), "executed": set()}
    state = {"site_initialized": False, "violation": False}

    def reject(message):
        state["violation"] = True
        raise RuntimeStop("STOP: " + message)

    def audit(event, args):
        if event in ("marshal.load", "marshal.loads"):
            # CPython also unmarshals its own built-in frozen modules. Permit
            # only that exact internal call, never file/cache deserialization.
            caller = sys._getframe(1)
            frozen_args = caller.f_locals.get("args", ())
            if not (caller.f_code is bootstrap._call_with_frames_removed.__code__
                    and caller.f_locals.get("f") is bootstrap._imp.get_frozen_object
                    and len(frozen_args) == 1 and isinstance(frozen_args[0], str)):
                reject("unmarshalling Python bytecode is forbidden")
        if event == "open" and args and isinstance(args[0], (str, bytes)):
            events["opened"].add(args[0])
        elif event in ("os.listdir", "os.scandir") and args and isinstance(args[0], (str, bytes)):
            events["listed"].add(args[0])
        elif event == "subprocess.Popen":
            events["executed"].add(str(args[0]))

    sys.addaudithook(audit)

    def source_code(loader, fullname):
        if type(loader) is not external.SourceFileLoader:
            reject("unsupported source loader subclass")
        try:
            filename = loader.get_filename(fullname)
            return loader.source_to_code(loader.get_data(filename), filename)
        except Exception:
            reject("cannot compile source for " + fullname)

    def denied(*args, **kwargs):
        reject("sourceless bytecode and ZIP imports are forbidden")

    external.SourceFileLoader.get_code = source_code
    external.SourcelessFileLoader.get_code = denied
    zipimport.zipimporter.get_code = denied
    zipimport.zipimporter.exec_module = denied
    zipimport.zipimporter.load_module = denied
    find_spec = bootstrap._find_spec

    def checked_spec(name, path, target=None):
        spec = find_spec(name, path, target)
        if spec is not None:
            loader = spec.loader
            if (loader not in (bootstrap.BuiltinImporter, bootstrap.FrozenImporter, None)
                    and type(loader) not in (external.SourceFileLoader, external.ExtensionFileLoader,
                                             external.NamespaceLoader)):
                reject("unsupported import loader for " + name)
        return spec

    bootstrap._find_spec = checked_spec

    def evidence():
        if (state["violation"] or external.SourceFileLoader.get_code is not source_code
                or external.SourcelessFileLoader.get_code is not denied
                or zipimport.zipimporter.get_code is not denied
                or zipimport.zipimporter.exec_module is not denied
                or zipimport.zipimporter.load_module is not denied
                or bootstrap._find_spec is not checked_spec
                or not sys.dont_write_bytecode
                or sys.pycache_prefix != "/dev/null"):
            raise RuntimeStop("STOP: runtime import policy drift")
        return {"contract": CONTRACT, "isolated": sys.flags.isolated,
                "no_site": sys.flags.no_site, "dont_write_bytecode": sys.dont_write_bytecode,
                "pycache_prefix": sys.pycache_prefix, "site_initialized": state["site_initialized"],
                "implementation": sys.implementation.name, "version": list(sys.version_info[:2])}

    def initialize_site(expected_prefix):
        import os
        import site
        # CPython 3.13 establishes the venv prefix here, not during -S startup.
        site.main()
        if (os.path.realpath(sys.prefix) != os.path.realpath(expected_prefix)
                or sys.prefix == sys.base_prefix or site.ENABLE_USER_SITE is not False):
            raise RuntimeStop("STOP: expected the application's isolated venv")
        state["site_initialized"] = True
        evidence()

    sys._cca_eob_runtime = {"evidence": evidence, "events": events,
                            "initialize_site": initialize_site}
    return evidence


def check_command_arguments(arguments):
    """Django argparse accepts unique abbreviations of its global options."""
    for argument in arguments:
        option = argument.split("=", 1)[0]
        if option.startswith("--") and any(
                forbidden.startswith(option) for forbidden in ("--settings", "--pythonpath")):
            raise RuntimeStop("STOP: settings and import paths cannot be overridden")


def main():
    install()
    import os  # frozen on the supported interpreter
    import stat

    if sys.platform != "linux":
        raise RuntimeStop("STOP: the CCA runtime requires Linux")
    null = os.lstat("/dev/null")
    if (not stat.S_ISCHR(null.st_mode) or null.st_uid != 0
            or os.major(null.st_rdev) != 1 or os.minor(null.st_rdev) != 3):
        raise RuntimeStop("STOP: /dev/null must be the root-owned Linux null device")
    if any(os.path.isfile(path) for path in sys.path):
        raise RuntimeStop("STOP: startup import archives are unsupported")
    if len(sys.argv) < 2 or sys.argv[1] not in ("trace", "plan", "apply"):
        raise RuntimeStop("STOP: expected trace, plan or apply")
    action = sys.argv[1]
    if action in ("plan", "apply") and any(
            argument.split("=", 1)[0] == "--app" for argument in sys.argv[2:]):
        raise RuntimeStop("STOP: --app is only permitted for trace")
    backend = os.path.dirname(os.path.abspath(__file__))
    app = os.path.dirname(backend)
    # Initial evidence may use an independently reviewed bootstrap outside the
    # live checkout. This override is trace-only and never dispatches a command.
    if action == "trace" and sys.argv[2:3] == ["--app"]:
        if len(sys.argv) < 4 or not os.path.isabs(sys.argv[3]):
            raise RuntimeStop("STOP: trace --app requires an absolute checkout path")
        app = os.path.normpath(sys.argv[3])
        backend = os.path.join(app, "backend")
        del sys.argv[2:4]
    sys._cca_eob_runtime["initialize_site"](os.path.join(app, ".venv"))
    sys.path.insert(0, backend)  # -I deliberately omits script and current directories
    os.chdir(backend)
    if action == "trace":
        sys.argv = ["<cca-eob-trace>", "trace", "--app", app, *sys.argv[2:]]
        # The reviewed collector is supplied as source; no deploy directory ACL grant.
        exec(compile(sys.stdin.read(), "<cca-eob-trace>", "exec"), {"__name__": "__main__"})
    else:
        check_command_arguments(sys.argv[2:])
        os.environ["DJANGO_SETTINGS_MODULE"] = "config.cca_eob_production_settings"
        sys.argv = [os.path.join(backend, "manage.py"), "cca_eob_production", action,
                    *sys.argv[2:]]
        from django.core.management import execute_from_command_line
        execute_from_command_line(sys.argv)


if __name__ == "__main__":
    try:
        main()
    except RuntimeStop as error:
        sys.stderr.write(str(error) + "\n")
        raise SystemExit(4)
