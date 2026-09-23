---
name: docx
description: "Use this skill whenever the user wants to create, read, edit, or manipulate Word documents (.docx files). Triggers include: any mention of 'Word doc', 'word document', '.docx', or requests to produce professional documents with formatting like tables of contents, headings, page numbers, or letterheads. Also use when extracting or reorganizing content from .docx files, inserting or replacing images in documents, performing find-and-replace in Word files, working with tracked changes or comments, or converting content into a polished Word document. If the user asks for a 'report', 'memo', 'letter', 'template', or similar deliverable as a Word or .docx file, use this skill. Do NOT use for PDFs, spreadsheets, Google Docs, or general coding tasks unrelated to document generation."
license: Proprietary. LICENSE.txt has complete terms
---

# DOCX creation, editing, and analysis

## Overview

A .docx file is a ZIP archive containing XML files.

## Quick Reference

| Task | Approach |
|------|----------|
| Read/analyze content | `pandoc` or unpack for raw XML |
| Create new document | Use `python-docx` - see Creating New Documents below |
| Edit existing document | Unpack → edit XML → repack - see Editing Existing Documents below |

### Converting .doc to .docx

Legacy `.doc` files must be converted before editing:

```bash
python scripts/office/soffice.py --headless --convert-to docx document.doc
```

### Reading Content

```bash
# Text extraction with tracked changes
pandoc --track-changes=all document.docx -o output.md

# Raw XML access
python scripts/office/unpack.py document.docx unpacked/
```

### Converting to Images

```bash
python scripts/office/soffice.py --headless --convert-to pdf document.docx
pdftoppm -jpeg -r 150 document.pdf page
```

### Accepting Tracked Changes

To produce a clean document with all tracked changes accepted (requires LibreOffice):

```bash
python scripts/accept_changes.py input.docx output.docx
```

---

## Creating New Documents

Generate .docx files with **python-docx**, then validate. Install: `pip install python-docx`

### Setup
```python
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

doc = Document()
doc.add_paragraph("Hello world")
doc.save("doc.docx")
```

`Document()` starts from python-docx's built-in template (already US-Letter-ish defaults vary by platform — set page size explicitly, see below). Pass a path to start from your own template: `Document("template.docx")`.

### Validation
After creating the file, validate it. If validation fails, unpack, fix the XML, and repack.
```bash
python scripts/office/validate.py doc.docx
```

### Page Size

```python
from docx.shared import Inches

section = doc.sections[0]
# CRITICAL: set page size explicitly for consistent results
section.page_width = Inches(8.5)     # US Letter
section.page_height = Inches(11)
section.left_margin = Inches(1)
section.right_margin = Inches(1)
section.top_margin = Inches(1)
section.bottom_margin = Inches(1)
```

**Common page sizes:**

| Paper | Width | Height | Content Width (1" margins) |
|-------|-------|--------|---------------------------|
| US Letter | 8.5" | 11" | 6.5" |
| A4 | 8.27" | 11.69" | 6.27" |

**Landscape orientation:** swap width/height and set the orientation flag:
```python
from docx.enum.section import WD_ORIENT
section.orientation = WD_ORIENT.LANDSCAPE
section.page_width, section.page_height = Inches(11), Inches(8.5)  # set explicitly; setting orientation alone does NOT swap dimensions
```

### Styles (Override Built-in Headings)

Use Arial as the default font (universally supported). Keep titles black for readability.

```python
from docx.shared import Pt, RGBColor

# Default document font
normal = doc.styles['Normal']
normal.font.name = 'Arial'
normal.font.size = Pt(12)

# Override built-in heading styles (edit, don't recreate — the IDs must stay 'Heading 1' etc.)
h1 = doc.styles['Heading 1']
h1.font.name = 'Arial'; h1.font.size = Pt(16); h1.font.bold = True
h1.font.color.rgb = RGBColor(0x00, 0x00, 0x00)
h1.paragraph_format.space_before = Pt(12); h1.paragraph_format.space_after = Pt(12)

h2 = doc.styles['Heading 2']
h2.font.name = 'Arial'; h2.font.size = Pt(14); h2.font.bold = True

# Headings created this way carry the outline level needed for a TOC
doc.add_heading("Title", level=1)
```

`add_heading(text, level=N)` applies the built-in `Heading N` style (which has the `outlineLevel` a TOC requires). Don't fake headings with bold body text.

### Lists (NEVER use unicode bullets)

```python
# ✅ CORRECT - use the built-in list styles
doc.add_paragraph("First bullet",  style='List Bullet')
doc.add_paragraph("Second bullet", style='List Bullet')
doc.add_paragraph("First numbered",  style='List Number')
doc.add_paragraph("Second numbered", style='List Number')

# Nested levels: 'List Bullet 2', 'List Bullet 3', 'List Number 2', ...
doc.add_paragraph("Sub-item", style='List Bullet 2')

# ❌ WRONG - never type bullet characters yourself
doc.add_paragraph("• Item")          # BAD: creates literal bullets, no list semantics
```

To restart or continue numbering precisely you must manipulate `w:numbering` XML; for most documents the built-in styles are sufficient.

### Tables

**CRITICAL: Tables need explicit widths** — set `autofit = False`, the table's column widths, AND each cell's width. Without all three, tables render inconsistently across platforms.

```python
from docx.shared import Inches, Pt
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

table = doc.add_table(rows=2, cols=2)
table.style = 'Table Grid'          # gives all cells single borders
table.alignment = WD_TABLE_ALIGNMENT.CENTER
table.autofit = False               # CRITICAL: required for fixed widths

col_w = Inches(3.25)
for col in table.columns:
    col.width = col_w
for row in table.rows:
    for cell in row.cells:
        cell.width = col_w          # CRITICAL: also set on every cell

table.rows[0].cells[0].text = "Header"
table.rows[1].cells[0].text = "Cell"

# Cell background shading (use 'clear' fill, never a solid black default)
def shade_cell(cell, hex_fill):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:fill'), hex_fill)
    tcPr.append(shd)

shade_cell(table.rows[0].cells[0], "D5E8F0")
```

**Width rules:**
- Set `table.autofit = False`, column widths, AND cell widths — all three.
- Table width = sum of column widths = content width (page width minus left+right margins). For US Letter with 1" margins that's 6.5".
- Cell padding is internal; it does not add to cell width.
- **Never use tables as horizontal rules/dividers** — cells have a minimum height and render as empty boxes. Use a paragraph bottom border instead:
  ```python
  p = doc.add_paragraph()
  pPr = p._p.get_or_add_pPr(); pbdr = OxmlElement('w:pBdr')
  bottom = OxmlElement('w:bottom')
  for k, v in {'w:val':'single','w:sz':'6','w:space':'1','w:color':'2E75B6'}.items():
      bottom.set(qn(k), v)
  pbdr.append(bottom); pPr.append(pbdr)
  ```

### Images

> **Use local images only — never download.** `add_picture` takes a local file path or file-like
> object; it does not fetch URLs. The environment has no internet access, so do not write
> `requests`/`urllib`/`curl` code to pull images from the web. Use images the user provided, or
> generate them locally (e.g. a chart rendered to PNG with matplotlib/Pillow).

```python
from docx.shared import Inches

# Inline image (pass one dimension to preserve aspect ratio)
doc.add_picture("image.png", width=Inches(2))

# To control placement, add to a paragraph run
p = doc.add_paragraph()
run = p.add_run()
run.add_picture("image.png", width=Inches(2), height=Inches(1.5))
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
```

### Page Breaks

```python
doc.add_page_break()

# Or break before a specific paragraph
p = doc.add_paragraph("New page")
p.paragraph_format.page_break_before = True
```

### Hyperlinks

python-docx has no high-level hyperlink API; use this helper:

```python
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

def add_hyperlink(paragraph, url, text):
    part = paragraph.part
    r_id = part.relate_to(
        url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True)
    hyperlink = OxmlElement('w:hyperlink'); hyperlink.set(qn('r:id'), r_id)
    run = OxmlElement('w:r'); rPr = OxmlElement('w:rPr')
    style = OxmlElement('w:rStyle'); style.set(qn('w:val'), 'Hyperlink'); rPr.append(style)
    run.append(rPr)
    t = OxmlElement('w:t'); t.text = text; run.append(t)
    hyperlink.append(run); paragraph._p.append(hyperlink)

p = doc.add_paragraph()
add_hyperlink(p, "https://example.com", "Click here")
```

For internal links (bookmarks + `w:hyperlink w:anchor=...`), edit the XML via the unpack/pack workflow.

### Footnotes

python-docx does not expose footnotes through its API. To add real footnotes, create the document body with python-docx, then use the **Editing Existing Documents** unpack → edit XML → pack workflow below (add `word/footnotes.xml`, the relationship, and `<w:footnoteReference>` runs). For simple sourcing, inline parenthetical citations are often sufficient.

### Tab Stops

```python
from docx.shared import Inches
from docx.enum.text import WD_TAB_ALIGNMENT, WD_TAB_LEADER

# Right-align text on the same line (e.g., date opposite a title)
p = doc.add_paragraph()
p.paragraph_format.tab_stops.add_tab_stop(Inches(6.5), WD_TAB_ALIGNMENT.RIGHT)
p.add_run("Company Name\tJanuary 2025")

# Dot leader (e.g., TOC-style line)
p = doc.add_paragraph()
p.paragraph_format.tab_stops.add_tab_stop(Inches(6.5), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS)
p.add_run("Introduction\t3")
```

### Multi-Column Layouts

Column layout lives on the section's `w:cols` element:

```python
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

def set_columns(section, num, space_twips=720):   # 720 twips = 0.5"
    sectPr = section._sectPr
    cols = sectPr.find(qn('w:cols'))
    if cols is None:
        cols = OxmlElement('w:cols'); sectPr.append(cols)
    cols.set(qn('w:num'), str(num))
    cols.set(qn('w:space'), str(space_twips))

set_columns(doc.sections[0], 2)
```

To start a new section (e.g., to switch column counts mid-document), use `doc.add_section()` and configure its `w:cols`.

### Table of Contents

A TOC is a Word field that the user refreshes on open. Insert the field via XML:

```python
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

def add_toc(doc):
    p = doc.add_paragraph()
    run = p.add_run()
    begin = OxmlElement('w:fldChar'); begin.set(qn('w:fldCharType'), 'begin')
    instr = OxmlElement('w:instrText'); instr.set(qn('xml:space'), 'preserve')
    instr.text = r'TOC \o "1-3" \h \z \u'
    sep = OxmlElement('w:fldChar'); sep.set(qn('w:fldCharType'), 'separate')
    end = OxmlElement('w:fldChar'); end.set(qn('w:fldCharType'), 'end')
    for el in (begin, instr, sep, end):
        run._r.append(el)

add_toc(doc)
```

Headings must use the built-in `Heading 1/2/3` styles (via `add_heading`) for the TOC to pick them up.

### Headers/Footers (with page numbers)

```python
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

def add_field(paragraph, instruction):
    run = paragraph.add_run()
    begin = OxmlElement('w:fldChar'); begin.set(qn('w:fldCharType'), 'begin')
    instr = OxmlElement('w:instrText'); instr.set(qn('xml:space'), 'preserve'); instr.text = instruction
    end = OxmlElement('w:fldChar'); end.set(qn('w:fldCharType'), 'end')
    for el in (begin, instr, end):
        run._r.append(el)

section = doc.sections[0]
header = section.header
header.paragraphs[0].text = "Header text"

footer = section.footer
fp = footer.paragraphs[0]
fp.add_run("Page ")
add_field(fp, "PAGE")            # current page number
fp.add_run(" of ")
add_field(fp, "NUMPAGES")        # total pages
```

### Critical Rules for python-docx

- **Set page size explicitly** — defaults vary; set US Letter (8.5" × 11") for US documents.
- **Landscape: set orientation AND swap dimensions** — `WD_ORIENT.LANDSCAPE` alone does not change page_width/page_height.
- **Never embed `\n` in a run** — use separate `add_paragraph()` calls (or `run.add_break()` for a soft line break).
- **Never type unicode bullets** — use `style='List Bullet'` / `'List Number'`.
- **Images**: use `add_picture`; pass a single dimension to preserve aspect ratio.
- **Tables need fixed widths** — `autofit = False` plus column widths plus cell widths, all matching.
- **Table width = sum of column widths** — make them add up to the content width exactly.
- **Use `w:shd` with `val='clear'`** for cell shading — never a solid fill that turns cells black.
- **Never use tables as dividers** — use a paragraph bottom border instead.
- **TOC requires built-in heading styles** — use `add_heading`, not custom-styled paragraphs.
- **Override built-in styles in place** — edit `doc.styles['Heading 1']`; don't invent new style IDs for headings you want in the TOC.
- **Colors are `RGBColor(r, g, b)` integers** — e.g. `RGBColor(0x2E, 0x75, 0xB6)`, never `"#2E75B6"`.

---

## Editing Existing Documents

**Follow all 3 steps in order.**

### Step 1: Unpack
```bash
python scripts/office/unpack.py document.docx unpacked/
```
Extracts XML, pretty-prints, merges adjacent runs, and converts smart quotes to XML entities (`&#x201C;` etc.) so they survive editing. Use `--merge-runs false` to skip run merging.

### Step 2: Edit XML

Edit files in `unpacked/word/`. See XML Reference below for patterns.

**Use "Claude" as the author** for tracked changes and comments, unless the user explicitly requests use of a different name.

**Use the Edit tool directly for string replacement. Do not write Python scripts.** Scripts introduce unnecessary complexity. The Edit tool shows exactly what is being replaced.

**CRITICAL: Use smart quotes for new content.** When adding text with apostrophes or quotes, use XML entities to produce smart quotes:
```xml
<!-- Use these entities for professional typography -->
<w:t>Here&#x2019;s a quote: &#x201C;Hello&#x201D;</w:t>
```
| Entity | Character |
|--------|-----------|
| `&#x2018;` | ‘ (left single) |
| `&#x2019;` | ’ (right single / apostrophe) |
| `&#x201C;` | “ (left double) |
| `&#x201D;` | ” (right double) |

**Adding comments:** Use `comment.py` to handle boilerplate across multiple XML files (text must be pre-escaped XML):
```bash
python scripts/comment.py unpacked/ 0 "Comment text with &amp; and &#x2019;"
python scripts/comment.py unpacked/ 1 "Reply text" --parent 0  # reply to comment 0
python scripts/comment.py unpacked/ 0 "Text" --author "Custom Author"  # custom author name
```
Then add markers to document.xml (see Comments in XML Reference).

### Step 3: Pack
```bash
python scripts/office/pack.py unpacked/ output.docx --original document.docx
```
Validates with auto-repair, condenses XML, and creates DOCX. Use `--validate false` to skip.

**Auto-repair will fix:**
- `durableId` >= 0x7FFFFFFF (regenerates valid ID)
- Missing `xml:space="preserve"` on `<w:t>` with whitespace

**Auto-repair won't fix:**
- Malformed XML, invalid element nesting, missing relationships, schema violations

### Common Pitfalls

- **Replace entire `<w:r>` elements**: When adding tracked changes, replace the whole `<w:r>...</w:r>` block with `<w:del>...<w:ins>...` as siblings. Don't inject tracked change tags inside a run.
- **Preserve `<w:rPr>` formatting**: Copy the original run's `<w:rPr>` block into your tracked change runs to maintain bold, font size, etc.

---

## XML Reference

### Schema Compliance

- **Element order in `<w:pPr>`**: `<w:pStyle>`, `<w:numPr>`, `<w:spacing>`, `<w:ind>`, `<w:jc>`, `<w:rPr>` last
- **Whitespace**: Add `xml:space="preserve"` to `<w:t>` with leading/trailing spaces
- **RSIDs**: Must be 8-digit hex (e.g., `00AB1234`)

### Tracked Changes

**Insertion:**
```xml
<w:ins w:id="1" w:author="Claude" w:date="2025-01-01T00:00:00Z">
  <w:r><w:t>inserted text</w:t></w:r>
</w:ins>
```

**Deletion:**
```xml
<w:del w:id="2" w:author="Claude" w:date="2025-01-01T00:00:00Z">
  <w:r><w:delText>deleted text</w:delText></w:r>
</w:del>
```

**Inside `<w:del>`**: Use `<w:delText>` instead of `<w:t>`, and `<w:delInstrText>` instead of `<w:instrText>`.

**Minimal edits** - only mark what changes:
```xml
<!-- Change "30 days" to "60 days" -->
<w:r><w:t>The term is </w:t></w:r>
<w:del w:id="1" w:author="Claude" w:date="...">
  <w:r><w:delText>30</w:delText></w:r>
</w:del>
<w:ins w:id="2" w:author="Claude" w:date="...">
  <w:r><w:t>60</w:t></w:r>
</w:ins>
<w:r><w:t> days.</w:t></w:r>
```

**Deleting entire paragraphs/list items** - when removing ALL content from a paragraph, also mark the paragraph mark as deleted so it merges with the next paragraph. Add `<w:del/>` inside `<w:pPr><w:rPr>`:
```xml
<w:p>
  <w:pPr>
    <w:numPr>...</w:numPr>  <!-- list numbering if present -->
    <w:rPr>
      <w:del w:id="1" w:author="Claude" w:date="2025-01-01T00:00:00Z"/>
    </w:rPr>
  </w:pPr>
  <w:del w:id="2" w:author="Claude" w:date="2025-01-01T00:00:00Z">
    <w:r><w:delText>Entire paragraph content being deleted...</w:delText></w:r>
  </w:del>
</w:p>
```
Without the `<w:del/>` in `<w:pPr><w:rPr>`, accepting changes leaves an empty paragraph/list item.

**Rejecting another author's insertion** - nest deletion inside their insertion:
```xml
<w:ins w:author="Jane" w:id="5">
  <w:del w:author="Claude" w:id="10">
    <w:r><w:delText>their inserted text</w:delText></w:r>
  </w:del>
</w:ins>
```

**Restoring another author's deletion** - add insertion after (don't modify their deletion):
```xml
<w:del w:author="Jane" w:id="5">
  <w:r><w:delText>deleted text</w:delText></w:r>
</w:del>
<w:ins w:author="Claude" w:id="10">
  <w:r><w:t>deleted text</w:t></w:r>
</w:ins>
```

### Comments

After running `comment.py` (see Step 2), add markers to document.xml. For replies, use `--parent` flag and nest markers inside the parent's.

**CRITICAL: `<w:commentRangeStart>` and `<w:commentRangeEnd>` are siblings of `<w:r>`, never inside `<w:r>`.**

```xml
<!-- Comment markers are direct children of w:p, never inside w:r -->
<w:commentRangeStart w:id="0"/>
<w:del w:id="1" w:author="Claude" w:date="2025-01-01T00:00:00Z">
  <w:r><w:delText>deleted</w:delText></w:r>
</w:del>
<w:r><w:t> more text</w:t></w:r>
<w:commentRangeEnd w:id="0"/>
<w:r><w:rPr><w:rStyle w:val="CommentReference"/></w:rPr><w:commentReference w:id="0"/></w:r>

<!-- Comment 0 with reply 1 nested inside -->
<w:commentRangeStart w:id="0"/>
  <w:commentRangeStart w:id="1"/>
  <w:r><w:t>text</w:t></w:r>
  <w:commentRangeEnd w:id="1"/>
<w:commentRangeEnd w:id="0"/>
<w:r><w:rPr><w:rStyle w:val="CommentReference"/></w:rPr><w:commentReference w:id="0"/></w:r>
<w:r><w:rPr><w:rStyle w:val="CommentReference"/></w:rPr><w:commentReference w:id="1"/></w:r>
```

### Images

1. Add image file to `word/media/`
2. Add relationship to `word/_rels/document.xml.rels`:
```xml
<Relationship Id="rId5" Type=".../image" Target="media/image1.png"/>
```
3. Add content type to `[Content_Types].xml`:
```xml
<Default Extension="png" ContentType="image/png"/>
```
4. Reference in document.xml:
```xml
<w:drawing>
  <wp:inline>
    <wp:extent cx="914400" cy="914400"/>  <!-- EMUs: 914400 = 1 inch -->
    <a:graphic>
      <a:graphicData uri=".../picture">
        <pic:pic>
          <pic:blipFill><a:blip r:embed="rId5"/></pic:blipFill>
        </pic:pic>
      </a:graphicData>
    </a:graphic>
  </wp:inline>
</w:drawing>
```

---

## Dependencies

- **pandoc**: Text extraction
- **python-docx**: `pip install python-docx` (new documents)
- **LibreOffice**: PDF conversion (auto-configured for sandboxed environments via `scripts/office/soffice.py`)
- **Poppler**: `pdftoppm` for images
