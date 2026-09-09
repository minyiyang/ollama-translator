"""Create an isolated EPUB pilot containing selected spine documents."""

from __future__ import annotations

import argparse
import os
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree


CONTAINER_PATH = "META-INF/container.xml"
CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
OPF_NS = "http://www.idpf.org/2007/opf"


def create_spine_subset(source: Path, output: Path, selected_idrefs: list[str]) -> Path:
    """Copy an EPUB while retaining only the selected OPF spine itemrefs."""
    source = source.resolve()
    output = output.resolve()
    if source == output:
        raise ValueError("source and output EPUB paths must differ")
    if not source.is_file():
        raise FileNotFoundError(source)
    selected = list(dict.fromkeys(selected_idrefs))
    if not selected:
        raise ValueError("at least one spine idref is required")

    with zipfile.ZipFile(source, "r") as archive:
        container = ElementTree.fromstring(archive.read(CONTAINER_PATH))
        rootfile = container.find(f".//{{{CONTAINER_NS}}}rootfile")
        if rootfile is None or not rootfile.get("full-path"):
            raise ValueError("EPUB container does not identify an OPF package")
        opf_path = rootfile.get("full-path")
        package = ElementTree.fromstring(archive.read(opf_path))
        spine = package.find(f"{{{OPF_NS}}}spine")
        if spine is None:
            raise ValueError("EPUB package has no spine")
        itemrefs = list(spine.findall(f"{{{OPF_NS}}}itemref"))
        existing = [item.get("idref", "") for item in itemrefs]
        missing = [item for item in selected if item not in existing]
        if missing:
            raise ValueError(f"spine idref(s) not found: {', '.join(missing)}")
        by_id = {item.get("idref", ""): item for item in itemrefs}
        for item in itemrefs:
            spine.remove(item)
        for idref in selected:
            spine.append(by_id[idref])
        opf_bytes = ElementTree.tostring(
            package, encoding="utf-8", xml_declaration=True
        )
        entries = [(info, archive.read(info.filename)) for info in archive.infolist()]

    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{output.stem}-", suffix=".epub.tmp", dir=output.parent
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w") as target:
            mimetype = next((item for item in entries if item[0].filename == "mimetype"), None)
            if mimetype is None:
                raise ValueError("EPUB has no mimetype entry")
            mimetype[0].compress_type = zipfile.ZIP_STORED
            target.writestr(mimetype[0], mimetype[1])
            for info, data in entries:
                if info.filename == "mimetype":
                    continue
                target.writestr(info, opf_bytes if info.filename == opf_path else data)
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--idref", action="append", required=True)
    args = parser.parse_args(argv)
    result = create_spine_subset(args.source, args.output, args.idref)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
