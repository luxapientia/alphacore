#!/usr/bin/env python3

from __future__ import annotations

import zipfile
from pathlib import Path


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    src_dir = (script_dir / ".." / "test").resolve()
    out_zip = (script_dir / "miner-result.zip").resolve()
    out_bad_zip = (script_dir / "miner-bad.zip").resolve()

    include = ["main.tf", "miner.json", "terraform.tfstate"]
    for name in include:
        path = src_dir / name
        if not path.exists():
            raise SystemExit(f"Missing required test file: {path}")

    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in include:
            zf.write(src_dir / name, arcname=name)

    # Create a deterministic failure bundle: invalid Terraform/HCL syntax.
    bad_main_tf = 'terraform { required_version = ">= 1.0.0" }\n\nresource "random_id" "oops" { byte_length = }\n'
    with zipfile.ZipFile(out_bad_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("main.tf", bad_main_tf)
        zf.write(src_dir / "miner.json", arcname="miner.json")
        zf.writestr("terraform.tfstate", "{}\n")

    print(f"Wrote {out_zip}")
    print(f"Wrote {out_bad_zip} (expected to fail terraform)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
