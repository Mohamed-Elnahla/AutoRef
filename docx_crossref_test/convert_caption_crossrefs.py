from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from lxml import etree
import re, tempfile

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}
qn = lambda name: f"{{{W}}}{name}"

def run_text(text, bold=False):
    r = etree.Element(qn("r"))
    if bold:
        rp = etree.SubElement(r, qn("rPr")); etree.SubElement(rp, qn("b"))
    t = etree.SubElement(r, qn("t")); t.text = text
    if text.startswith(" ") or text.endswith(" "):
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return r

def field(instruction, result, bookmark=None, bookmark_id=None):
    out = []
    if bookmark:
        x = etree.Element(qn("bookmarkStart")); x.set(qn("id"), str(bookmark_id)); x.set(qn("name"), bookmark); out.append(x)
    r = etree.Element(qn("r")); x = etree.SubElement(r, qn("fldChar")); x.set(qn("fldCharType"), "begin"); out.append(r)
    r = etree.Element(qn("r")); x = etree.SubElement(r, qn("instrText")); x.set("{http://www.w3.org/XML/1998/namespace}space", "preserve"); x.text = instruction; out.append(r)
    r = etree.Element(qn("r")); x = etree.SubElement(r, qn("fldChar")); x.set(qn("fldCharType"), "separate"); out.append(r)
    out.append(run_text(result))
    r = etree.Element(qn("r")); x = etree.SubElement(r, qn("fldChar")); x.set(qn("fldCharType"), "end"); out.append(r)
    if bookmark:
        x = etree.Element(qn("bookmarkEnd")); x.set(qn("id"), str(bookmark_id)); out.append(x)
    return out

def paragraph_text(p): return "".join(p.xpath(".//w:t/text()", namespaces=NS))

def make_caption(p, label, number, title, bookmark, bookmark_id):
    ppr = p.find("w:pPr", NS)
    if ppr is None:
        ppr = etree.Element(qn("pPr")); p.insert(0, ppr)
    style = ppr.find("w:pStyle", NS)
    if style is None:
        style = etree.Element(qn("pStyle")); ppr.insert(0, style)
    style.set(qn("val"), "Caption")
    for child in list(p):
        if child is not ppr: p.remove(child)
    p.append(run_text(label + " ", bold=True))
    p.extend(field(f" SEQ {label} \\* ARABIC ", str(number), bookmark, bookmark_id))
    p.append(run_text(". " + title))

def replace_refs(p, labels):
    old = paragraph_text(p); matches = list(re.finditer(r"\b(Figure|Table) ([0-9]+)\b", old))
    known = [m for m in matches if (m.group(1), int(m.group(2))) in labels]
    if not known: return 0
    ppr = p.find("w:pPr", NS)
    for child in list(p):
        if child is not ppr: p.remove(child)
    pos = 0
    for m in known:
        label, num = m.groups()
        p.append(run_text(old[pos:m.start()] + label + " "))
        p.extend(field(f" REF {labels[(label, int(num))]} \\h ", num))
        pos = m.end()
    p.append(run_text(old[pos:]))
    return len(known)

def main(source, output):
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / "unzipped"; base.mkdir()
        with ZipFile(source) as z: z.extractall(base)
        doc = base / "word/document.xml"; tree = etree.parse(str(doc)); body = tree.getroot().find("w:body", NS)
        labels, captions = {}, []
        for p in body.xpath(".//w:p", namespaces=NS):
            m = re.fullmatch(r"(Figure|Table) ([0-9]+)", paragraph_text(p).strip())
            if m:
                label, number = m.group(1), int(m.group(2)); captions.append((p, label, number)); labels[(label, number)] = f"{label}{number}"
        if not captions: raise RuntimeError("No standalone Figure/Table labels found.")
        for i, (p, label, number) in enumerate(captions, start=1):
            title_p = p.getnext()
            if title_p is None or title_p.tag != qn("p") or not paragraph_text(title_p).strip():
                raise RuntimeError(f"No title paragraph follows {label} {number}.")
            make_caption(p, label, number, paragraph_text(title_p).strip(), labels[(label, number)], 100 + i)
            title_p.getparent().remove(title_p)
        refs = sum(replace_refs(p, labels) for p in body.xpath(".//w:p", namespaces=NS) if p not in [x[0] for x in captions])
        settings = base / "word/settings.xml"; stree = etree.parse(str(settings)); root = stree.getroot()
        update = root.find("w:updateFields", NS)
        if update is None: update = etree.SubElement(root, qn("updateFields"))
        update.set(qn("val"), "true")
        tree.write(str(doc), xml_declaration=True, encoding="UTF-8", standalone=True)
        stree.write(str(settings), xml_declaration=True, encoding="UTF-8", standalone=True)
        with ZipFile(output, "w", ZIP_DEFLATED) as z:
            for f in base.rglob("*"):
                if f.is_file(): z.write(f, f.relative_to(base).as_posix())
    print(f"Converted {len(captions)} captions and {refs} in-text references.")

if __name__ == "__main__":
    import sys
    main(Path(sys.argv[1]), Path(sys.argv[2]))
