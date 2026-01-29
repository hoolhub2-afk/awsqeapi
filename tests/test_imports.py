def test_import_core_modules():
    import importlib

    modules = [
        "src.core.config",
        "src.security.auth",
        "src.api.schemas",
        "sitecustomize",
    ]

    for mod in modules:
        importlib.import_module(mod)
