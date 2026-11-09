from plan2cad.preflight import doctor, library_versions


def test_doctor_required_all_ok():
    checks = doctor()
    assert all(c.ok for c in checks if c.required), [c for c in checks if c.required and not c.ok]
    assert any(c.name == "smoke test" and c.ok for c in checks)


def test_library_versions_listing():
    v = library_versions()
    assert {"python", "numpy", "Pillow", "PyYAML", "shapely", "geopandas", "pyogrio", "ezdxf", "matplotlib"} <= set(v)
    assert all(x != "not installed" for k, x in v.items() if k != "rapidocr-onnxruntime")
