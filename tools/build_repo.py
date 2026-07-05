#!/usr/bin/env python3
import argparse
import html
import hashlib
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_MANIFEST = ROOT / "addon.xml"
DEFAULT_REPOSITORY_MANIFEST = ROOT / "repository.aiostreams" / "addon.xml"
DEFAULT_BASE_URL = "https://needforseed1.github.io/aios-kodi/"


def parse_manifest(path):
    addon = ET.parse(path).getroot()
    return addon.attrib["id"], addon.attrib["version"]


def indent(element, level=0):
    padding = "\n" + level * "  "
    child_padding = "\n" + (level + 1) * "  "
    if len(element):
        if not element.text or not element.text.strip():
            element.text = child_padding
        for child in element:
            indent(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = padding
    if level and (not element.tail or not element.tail.strip()):
        element.tail = padding


def repository_tree(base_url, repository_manifest):
    tree = ET.parse(repository_manifest)
    root = tree.getroot()
    repo_extension = root.find("./extension[@point='xbmc.addon.repository']")
    if repo_extension is None:
        raise RuntimeError("repository manifest is missing xbmc.addon.repository extension")
    directory = repo_extension.find("dir")
    if directory is None:
        raise RuntimeError("repository manifest is missing repository dir")

    base_url = base_url.rstrip("/") + "/"
    cache_suffix = "?v=" + root.attrib["version"]
    values = {
        "info": base_url + "addons.xml" + cache_suffix,
        "checksum": base_url + "addons.xml.md5" + cache_suffix,
        "datadir": base_url,
    }
    for tag, value in values.items():
        node = directory.find(tag)
        if node is None:
            raise RuntimeError("repository manifest is missing %s" % tag)
        node.text = value
    return tree


def add_file(zip_file, source, archive_name):
    zip_file.write(source, archive_name.as_posix())


def build_plugin_zip(output_dir):
    addon_id, version = parse_manifest(PLUGIN_MANIFEST)
    zip_path = output_dir / addon_id / ("%s-%s.zip" % (addon_id, version))
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    include = [
        ROOT / "addon.xml",
        ROOT / "addon.py",
        ROOT / "service.py",
        ROOT / "README.md",
        ROOT / "credentials.example.json",
        ROOT / "resources",
    ]
    files = []
    for item in include:
        if item.is_dir():
            files.extend(
                path
                for path in item.rglob("*")
                if path.is_file()
                and "__pycache__" not in path.parts
                and not path.name.endswith((".pyc", ".pyo"))
            )
        elif item.exists():
            files.append(item)

    with ZipFile(zip_path, "w", ZIP_DEFLATED, compresslevel=9) as zip_file:
        for path in sorted(files):
            add_file(zip_file, path, Path(addon_id) / path.relative_to(ROOT))
    return zip_path


def build_repository_zip(output_dir, repo_tree, repository_manifest):
    addon_id, version = parse_manifest(repository_manifest)
    zip_path = output_dir / addon_id / ("%s-%s.zip" % (addon_id, version))
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    icon = ROOT / "resources" / "media" / "icon.png"

    with tempfile.TemporaryDirectory() as temp_dir:
        manifest = Path(temp_dir) / "addon.xml"
        repo_tree.write(manifest, encoding="UTF-8", xml_declaration=True, short_empty_elements=True)
        with ZipFile(zip_path, "w", ZIP_DEFLATED, compresslevel=9) as zip_file:
            add_file(zip_file, manifest, Path(addon_id) / "addon.xml")
            if icon.exists():
                add_file(zip_file, icon, Path(addon_id) / "icon.png")
    return zip_path


def publish_source_zip(output_dir, repository_zip):
    source_zip = output_dir / repository_zip.name
    shutil.copyfile(repository_zip, source_zip)
    return source_zip


def write_addons_xml(output_dir, manifests):
    addons = ET.Element("addons")
    for manifest in manifests:
        addons.append(ET.fromstring(manifest))
    indent(addons)

    body = ET.tostring(addons, encoding="unicode", short_empty_elements=True)
    xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + body + "\n"
    addons_xml = output_dir / "addons.xml"
    addons_xml.write_text(xml, encoding="utf-8")

    checksum = hashlib.md5(xml.encode("utf-8")).hexdigest()
    (output_dir / "addons.xml.md5").write_text(checksum + "\n", encoding="utf-8")
    return addons_xml


def write_index_html(output_dir, title, links, base_url=None):
    if base_url:
        base_url = base_url.rstrip("/") + "/"
    items = "\n".join(
        '    <li><a href="%s">%s</a></li>' % (
            html.escape(path, quote=True),
            html.escape(label),
        )
        for label, path in links
    )
    body = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
</head>
<body>
  <h1>{title}</h1>
  <ul>
{items}
  </ul>
{base_url_line}
</body>
</html>
""".format(
        title=html.escape(title),
        items=items,
        base_url_line='  <p>Base URL: <code>%s</code></p>' % html.escape(base_url) if base_url else "",
    )
    index_html = output_dir / "index.html"
    index_html.write_text(body, encoding="utf-8")
    return index_html


def write_indexes(output_dir, base_url, repository_zip, source_zip):
    repository_name = repository_zip.parent.name

    return [
        write_index_html(output_dir, "AIOStreams Kodi Repository", [
            (source_zip.name, source_zip.name),
        ], base_url),
        write_index_html(repository_zip.parent, repository_name, [
            (repository_zip.name, repository_zip.name),
        ]),
    ]


def display_path(path):
    try:
        return path.relative_to(ROOT)
    except ValueError:
        return path


def main():
    parser = argparse.ArgumentParser(description="Build the Kodi repository distribution tree.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Public URL where the generated repo directory is hosted.")
    parser.add_argument("--output-dir", default=str(ROOT / "repo"), help="Directory to write repository files into.")
    parser.add_argument("--repository-manifest", default=str(DEFAULT_REPOSITORY_MANIFEST), help="Repository add-on manifest to include.")
    parser.add_argument("--clean", action="store_true", help="Remove the output directory before building.")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    if args.clean and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    repository_manifest_path = Path(args.repository_manifest)
    if not repository_manifest_path.is_absolute():
        repository_manifest_path = ROOT / repository_manifest_path

    repo_tree = repository_tree(args.base_url, repository_manifest_path)
    plugin_zip = build_plugin_zip(output_dir)
    repository_zip = build_repository_zip(output_dir, repo_tree, repository_manifest_path)
    source_zip = publish_source_zip(output_dir, repository_zip)

    plugin_manifest = PLUGIN_MANIFEST.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as temp_dir:
        repo_manifest_path = Path(temp_dir) / "addon.xml"
        repo_tree.write(repo_manifest_path, encoding="UTF-8", xml_declaration=True, short_empty_elements=True)
        repository_manifest = repo_manifest_path.read_text(encoding="utf-8")
    addons_xml = write_addons_xml(output_dir, [plugin_manifest, repository_manifest])
    index_files = write_indexes(output_dir, args.base_url, repository_zip, source_zip)

    print("Wrote %s" % display_path(addons_xml))
    print("Wrote %s" % display_path(output_dir / "addons.xml.md5"))
    for index_file in index_files:
        print("Wrote %s" % display_path(index_file))
    print("Wrote %s" % display_path(source_zip))
    print("Wrote %s" % display_path(plugin_zip))
    print("Wrote %s" % display_path(repository_zip))


if __name__ == "__main__":
    main()
