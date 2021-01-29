# -*- coding: utf-8 -*-
"""
@created: 2020-08-20 13:00:00

@author: Jorj X. McKie

Font Replacement
----------------
This script reads a PDF and outputs its pages to a new file, providing text
with a new font.
The success of this approach is highly dependent on the input PDF itself: which
fonts are present on each page, which font should be replaced at all and by
which new font, etc.
There seems to be no single solution covering everything. Therefore please
regard this script as a *template* which must be individually adapted.

Approach and features
---------------------
Iterate through the pages and ...

* Extract the text via page.getText("dict"). Then remove all existing text from
  the page that is written with a font to be replaced. Non-text page elements
  stay intact (images, links, annotations, ...) as well as text with a not
  replaced font.

* Write original text pieces to the page again, using the new font.

* Original text color is kept.

* Original text position is kept. Details however depend on subtle metrics
  differences between old and new font. If the new font does not increase the
  text length, the old font size will be accepted, otherwise decreased.

* All replacing fonts **will be embedded**.

* Using fontTools, font subsets are computed where possible. This will keep the
  resulting file size within reasonable orders of magnitude.
  However, subsetting does not work for all fonts, e.g. the embeddable
  counterparts of the Base-14 fonts cannot be subsetted. So you may want to
  consider other fonts here, e.g. some NotoSans font instead of Helvetica, etc.

TODOs, Missing Features, Limitations
------------------------------------
* Text in annotations is **not handled**.
* Running this script will always make rewritten text visible, because it will
  be inserted after other page content (images, drawings, etc.) has been drawn.
  This is inevitable and may be a drawback for using this script.
* New fonts will be subsetted based on its used unicodes. This is
  currently not reflected ("ABCDEF+"-style prefix) in the font definition.


Dependencies
------------
PyMuPDF v1.18.4
fontTools

Notes
------
The resulting PDF will often (but not always) be larger than the input.
Reasons include:

* This script enforces use of embedded fonts. This will add to the output size.
* We use fontTools to create font subsets based on the used unicodes.
  Depending on the choice of the new font, subsetting may not wordk. We know of
  no way subsetting CFF fonts like the embeddable Base-14 font variants.

License
-------
GNU GPL 3.x (this script)
GNU AFFERO GPL 3.0 (MuPDF components)
MIT license (fontTools)

Copyright
---------
(c) 2020 Jorj X. McKie

Changes
-------
* Version 2020-09-02:
- Now also supporting text in so-called "Form XObjects", i.e. text not encoded
  in the page's /Contents.
- The intermediate CSV file containing mappings between old and new font names
  is now handled as a binary file (read / write options "rb", resp. "wb") to
  support fontnames encoded as general UTF-8.

* Version 2020-09-10:
- Change the CSV parameter file to JSON format. This hopefully covers more
  peculiarities for fontname specifications.

* Version 2020-11-27:
- The fontname to replace ("old" fontname) is now a list to account for
  potentially different name variants in the various entangled PDF objects
  like /FontName, /BaseName, etc.

"""
import os
import sys
import time
import json
from pprint import pprint
import re

import fitz

if fitz.VersionBind.split(".") < ["1", "18", "4"]:
    sys.exit("Need at least PyMuPDF v1.18.4")

# Contains sets of unicodes in use by font.
# "new fontname": unicode-list
font_subsets = {}

# Contains the binary buffers of each replacement font.
# "new fontname": buffer
font_buffers = {}

# Maps old fontname to new fontname.
# "old fontname": new fontname
new_fontnames = {}


def display_tables():
    """For debugging purposes."""
    print("\nnew_fontnames:")
    pprint(new_fontnames)
    print("\nfont_subsets:")
    pprint(font_subsets)
    print("\nfont_buffers.keys")
    pprint(font_buffers.keys())


def error_exit(searchname, name):
    print("Error occurred for '%s' ==> '%s'" % (searchname, name))
    display_tables()
    sys.exit()


def get_new_fontname(old_fontname):
    """Determine new fontname for a given old one.

    Return None if not found. The complex logic part is required because font
    name length is restricted to 32 bytes (by MuPDF).
    So we check instead, whether a dict key "almost" matches.
    """
    new_fontname = new_fontnames.get(old_fontname, None)
    if new_fontname:  # the simple case.
        return new_fontname
    fontlist = [  # build list of "almost matching" keys
        new_fontnames[n]
        for n in new_fontnames.keys()
        if n.startswith(old_fontname) or old_fontname.startswith(n)
    ]
    if fontlist == []:
        return None
    # the list MUST contain exactly one item!
    if len(fontlist) > 1:  # this should not happen!
        error_exit(old_fontname, "new fontname")
    return fontlist[0]


def get_font(searchname, flags):
    """Return the font to be used. TO BE MODIFIED!

    Notes:
        This function is at the core of the script and highly depends on each
        individual input PDF. To be successful, the fonts actually used by any
        page must be known and an action for each original font must be defined
        here. I.e. for each item in 'page.getFontList()' it must be clear what
        should happen.
        Even if an original font should not be replaced, it still must be
        converted to a fitz.Font and as such be returned here.
        Non embedded fonts however must always be replaced!
    Args:
        fontname: (str) the original fontname for some text.
        flags: (int) flags describing font properties (weight, spacing)
    Returns:
        The buffer (bytes) and the name of the replacing font.
    """
    # See if we match a stored font replacement
    if len(new_fontnames.keys()) > 0:
        new_fontname = get_new_fontname(searchname)
        if new_fontname is None:
            error_exit(searchname, "not in new_fontnames")
        buffer = font_buffers.get(new_fontname, None)
        if buffer is None:
            error_exit(searchname, new_fontname)
        return buffer, new_fontname


def resize(span, font):
    """Adjust fontsize for the replacement font.

    Computes new fontsize such that text will not exceed the bbox width.

    Args:
        span: (dict) the text span
        font: (fitz.Font) the new font
    Returns:
        New fontsize (float). May be smaller than the original.
    """
    text = span["text"]  # the text to output
    rect = fitz.Rect(span["bbox"])  # the bbox it occupies
    fsize = span["size"]  # old fontsize
    # compute text length under new font with that size
    tl = font.text_length(text, fontsize=fsize)
    if tl <= rect.width:  # doesn't exceed bbox width
        return fsize
    new_size = rect.width / tl * fsize  # new fontsize
    return new_size


def cont_clean(page, fontrefs):  # 把用到了被替换字体的那一段BT~ET中的文字部分清空掉
    """Remove text written with one of the fonts to replace.

    Args:
        page: the page
        fontrefs: dict of contents stream xrefs. Each xref key has a list of
            ref names looking like b"/refname ".
    """

    def remove_font(fontrefs, lines):
        """This inline function removes references to fonts in a /Contents stream.

        Args:
            fontrefs: a list of bytes objects looking like b"/fontref ".
            lines: a list of the lines of the /Contents.
        Returns:
            (bool, lines), where the bool is True if we have changed any of
            the lines.
        """
        changed = False
        count = len(lines)
        for ref in fontrefs:
            found = False  # switch: processing our font
            for i in range(count):
                if lines[i] == b"ET":  # end text object
                    found = False  # no longer in found mode
                    continue
                if lines[i].endswith(b" Tf"):  # font invoker command
                    if lines[i].startswith(ref):  # our font?
                        found = True  # switch on
                        lines[i] = b""  # remove line
                        changed = True  # tell we have changed
                        continue  # next line
                    else:  # else not our font
                        found = False  # switch off
                        continue  # next line
                if found == True and (
                    lines[i].endswith(
                        (
                            b"TJ",
                            b"Tj",
                            b"TL",
                            b"Tc",
                            b"Td",
                            b"Tm",
                            b"T*",
                            b"Ts",
                            b"Tw",
                            b"Tz",
                            b"'",
                            b'"',
                        )
                    )
                ):  # write command for our font?
                    lines[i] = b""  # remove it
                    changed = True  # tell we have changed
                    continue
        return changed, lines

    doc = page.parent
    for xref in fontrefs.keys():
        xref0 = 0 + xref
        if xref0 == 0:  # the page contents
            xref0 = page.get_contents()[0]  # there is only one /Contents obj now
        cont = doc.xref_stream(xref0)
        cont_lines = cont.splitlines()
        changed, cont_lines = remove_font(fontrefs[xref], cont_lines)
        if changed:
            cont = b"\n".join(cont_lines) + b"\n"
            doc.update_stream(xref0, cont)  # replace command source


def build_subset(buffer, unc_set):
    """Build font subsets using fontTools.

    Args:
        buffer: (bytes) the font given as a binary buffer.
        unc_set: (set) required unicodes.
    Returns:
        Either None if subsetting is unsuccessful or the subset font buffer.
    """
    import fontTools.subset as fts

    unc_list = list(unc_set)
    unc_list.sort()
    unc_file = open("uncfile.txt", "w")  # store unicodes as text file
    for unc in unc_list:
        unc_file.write("%04x\n" % unc)
    unc_file.close()
    fontfile = open("oldfont.ttf", "wb")  # store fontbuffer as a file
    fontfile.write(buffer)
    fontfile.close()
    try:
        os.remove("newfont.ttf")  # remove old file
    except:
        pass
    try:  # invoke fontTools subsetter
        fts.main(
            [
                "oldfont.ttf",
                "--unicodes-file=uncfile.txt",
                "--output-file=newfont.ttf",
                "--recalc-bounds",
            ]
        )
        fd = open("newfont.ttf", "rb")
        new_buffer = fd.read()  # subset font
        fd.close()
    except Exception as err:
        new_buffer = None
        print(f"build_subset exception: {err}")
    os.remove("uncfile.txt")
    os.remove("oldfont.ttf")
    if new_buffer is not None:
        os.remove("newfont.ttf")
    return new_buffer


def clean_fontnames(page):
    """Remove multiple references to one font.

    When rebuilding the page text, dozens of font reference names '/Fnnn' may
    be generated pointing to the same font.
    This function removes these duplicates and thus reduces the size of the
    /Resources object.
    """
    cont = bytearray(page.readContents())  # read and concat all /Contents
    font_xrefs = {}  # key: xref, value: set of font refs using it
    for f in page.getFontList():
        xref = f[0]
        name = f[4]  # font ref name, 'Fnnn'
        names = font_xrefs.get(xref, set())
        names.add(name)
        font_xrefs[xref] = names
    for xref in font_xrefs.keys():
        names = list(font_xrefs[xref])
        names.sort()  # read & sort font names for this xref
        name0 = b"/" + names[0].encode() + b" "  # we will keep this font name
        for name in names[1:]:
            namex = b"/" + name.encode() + b" "
            cont = cont.replace(namex, name0)
    xref = page.get_contents()[0]  # xref of first /Contents
    page.parent.update_stream(xref, cont)  # replace it with our result
    page.set_contents(xref)  # tell PDF: this is the only /Contents object
    page.clean_contents(sanitize=True)  # sanitize ensures cleaning /Resources


def build_font_tables(doc):
    font_refs = set()
    for p in doc:
        font_refs.add(get_page_fontrefs(p))
    
    return font_refs

def build_repl_table(doc, repl_fontnames_list):
    """Populate font replacement information.

    Read the JSON font relacement file and store its information in
    dictionaries 'font_subsets', 'font_buffers' and 'new_fontnames'.
    """
    #fd = open(fname)
    font_refs = build_font_tables(doc)
    fontdicts = repl_fontnames_list
    #fd.close()

    for fontdict in fontdicts:
        oldfont = fontdict["oldfont"]
        newfont = fontdict["newfont"].strip()

        if newfont == "keep":  # ignore if not replaced
            continue
        if "." in newfont or "/" in newfont or "\\" in newfont:
            try:
                font = fitz.Font(fontfile=newfont)
            except Exception as err:
                sys.exit(f"Could not create font '{newfont}': {err}")
            fontbuffer = font.buffer
            new_fontname = font.name
            font_subsets[new_fontname] = set()
            font_buffers[new_fontname] = fontbuffer
            for item in oldfont:
                new_fontnames[item] = new_fontname
            del font
            continue

        try:
            font = fitz.Font(newfont)
        except Exception as err:
            sys.exit("Could not create font '{newfont}': {err}")
        fontbuffer = font.buffer

        new_fontname = font.name
        font_subsets[new_fontname] = set()
        font_buffers[new_fontname] = fontbuffer
        for item in oldfont:
            new_fontnames[item] = new_fontname
        del font
        continue


def tilted_span(page, wdir, span, font):
    """Output a non-horizontal text span."""
    cos, sin = wdir  # writing direction from the line
    matrix = fitz.Matrix(cos, -sin, sin, cos, 0, 0)  # corresp. matrix
    text = span["text"]  # text to write
    bbox = fitz.Rect(span["bbox"])
    fontsize = span["size"]  # adjust fontsize
    tl = font.text_length(text, fontsize)  # text length with new font
    m = max(bbox.width, bbox.height)  # must not exceed max bbox dimension
    if tl > m:
        fontsize *= m / tl  # otherwise adjust
    opa = 0.1 if fontsize > 100 else 1  # fake opacity for large fontsizes
    tw = fitz.TextWriter(page.rect, opacity=opa, color=fitz.sRGB_to_pdf(span["color"]))
    origin = fitz.Point(span["origin"])
    if sin > 0:  # clockwise rotation
        origin.y = bbox.y0
    tw.append(origin, text, font=font, fontsize=fontsize)
    tw.writeText(page, morph=(origin, matrix))


def get_page_fontrefs(page):
    fontlist = page.getFontList(full=True)
    # Ref names for each font to replace.
    # Each contents stream has a separate entry here: keyed by xref,
    # 0 = page /Contents, otherwise xref of XObject
    fontrefs = {}
    for f in fontlist:
        fontname = f[3]
        cont_xref = f[-1]  # xref of XObject, 0 if page /Contents
        idx = fontname.find("+") + 1
        fontname = fontname[idx:]  # remove font subset indicator
        if fontname in new_fontnames.keys():  # we replace this font!
            refname = f[4]
            refname = b"/" + refname.encode() + b" "
            refs = fontrefs.get(cont_xref, [])
            refs.append(refname)
            fontrefs[cont_xref] = refs
    return fontrefs  # return list of font reference names


def repl_fontnames(doc: fitz.Document):
        
    def norm_name(name):
        while "#" in name:
            p = name.find("#")
            c = int(name[p + 1 : p + 3], 16)
            name = name.replace(name[p : p + 3], chr(c))
        p = name.find("+") + 1
        return name[p:]


    def get_fontnames(doc, item):
        """Return a list of fontnames.

        There may be more than one alternative e.g. for Type0 fonts.
        """
        subset = False
        fontname = item[3]
        idx = fontname.find("+") + 1
        fontname = fontname[idx:]
        if idx > 0:
            subset = True
        names = [fontname]
        text = doc.xref_object(item[0])
        font = ""
        descendents = ""

        for line in text.splitlines():
            line = line.split()
            if line[0] == "/BaseFont":
                font = norm_name(line[1][1:])
            elif line[0] == "/DescendantFonts":
                descendents = " ".join(line[1:]).replace(" 0 R", " ")
                if descendents.startswith("["):
                    descendents = descendents[1:-1]
                descendents = map(int, descendents.split())

        if font and font not in names:
            names.append(font)
        if not descendents:
            return subset, tuple(names)

        # 'descendents' is a list of descendent font xrefs.
        # Should be just one by the books.
        for xref in descendents:
            for line in doc.xref_object(xref).splitlines():
                line = line.split()
                if line[0] == "/BaseFont":
                    font = norm_name(line[1][1:])
                    if font not in names:
                        names.append(font)
        return subset, tuple(names)


    def make_msg(font):
        flags = font.flags
        msg = ["%i glyphs" % font.glyph_count, "size %i" % len(font.buffer)]
        if flags["mono"] == 1:
            msg.append("mono")
        if flags["serif"]:
            msg.append("serifed")
        if flags["italic"]:
            msg.append("italic")
        if flags["bold"]:
            msg.append("bold")
        msg = ", ".join(msg)
        return msg

    font_list = set()
    for i in range(len(doc)):
        for f in doc.getPageFontList(i, full=True):
            msg = ""

            if re.match("[A-Z]{6}\+.+", f[3]):
                # this font was a subset, bypass
                continue

            subset, fontname = get_fontnames(doc, f)

            if f[1] == "n/a":
                #msg = "Not embedded!"
                continue
            else:
                extr = doc.extractFont(f[0])
                font = fitz.Font(fontbuffer=extr[-1])
                #msg = make_msg(font)
                for _fontname in fontname:   #  <<<<< cuteufo: build up font_buffers and new_fontnames dict here
                    font_buffers[_fontname] = extr[3]
                    new_fontnames[_fontname] = _fontname # <<<<< cuteufo

            if subset:
                msg += ", subset font"
            font_list.add((fontname, msg))



def subset_fonts(indoc: fitz.Document):          # <<<<<<<<<<< cuteufo: interface for this module
    # ------------------
    # main interface
    # ------------------
    repl_fontnames(indoc)   # <<<<<<<<<<<<<<<<<<<<<<<<< cuteufo: make up font_buffers and new_fontnames

    if new_fontnames == {}:
        # sys.exit("\n***** There are no fonts to replace. *****")       # <<<<<<<< cuteufo: don't exit, return
        return
    
    # the following flag prevents images from being extracted:
    extr_flags = fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_PRESERVE_WHITESPACE

    # Phase 1
    for page in indoc:
        fontrefs = get_page_fontrefs(page)
        if fontrefs == {}:  # page has no fonts to replace
            continue
        for block in page.getText("dict", flags=extr_flags)["blocks"]:
            for line in block["lines"]:
                for span in line["spans"]:
                    new_fontname = get_new_fontname(span["font"])
                    if new_fontname is None:  # do not replace this font
                        continue

                    # replace non-utf8 by section symbol
                    text = span["text"].replace(chr(0xFFFD), chr(0xB6))
                    # extend collection of used unicodes
                    subset = font_subsets.get(new_fontname, set())
                    for c in text:
                        subset.add(ord(c))  # add any new unicode values
                    font_subsets[new_fontname] = subset  # store back extended set

    for fontname in font_subsets.keys():
        old_buffer = font_buffers[fontname]

        new_buffer = build_subset(old_buffer, font_subsets[fontname])
        if new_buffer is not None:
            font_buffers[fontname] = new_buffer
        else:
            del font_buffers[fontname] # failure of subset, remove this fontname from 
            del new_fontnames[fontname] # font_buffers and new_fontnames
        del old_buffer

    # Phase 2
    for page in indoc:  
        # extract text again
        blocks = page.getText("dict", flags=extr_flags)["blocks"]

        # clean contents streams of the page and any XObjects.
        page.clean_contents(sanitize=True)
        fontrefs = get_page_fontrefs(page) 
        if fontrefs == {}:  # page has no fonts to replace
            continue
        cont_clean(page, fontrefs)  # remove text using fonts to be replaced
        textwriters = {}  # contains one text writer per detected text color

        for block in blocks:
            for line in block["lines"]:
                wmode = line["wmode"]  # writing mode (horizontal, vertical)
                wdir = list(line["dir"])  # writing direction
                markup_dir = 0
                bidi_level = 0  # not used
                if wdir == [0, 1]:
                    markup_dir = 4
                for span in line["spans"]:
                    new_fontname = get_new_fontname(span["font"])
                    if new_fontname is None:  # do not replace this font
                        continue
                    if font_buffers[new_fontname] is None: 
                        # do not replace this font due to failure of fontTools
                        continue
                    font = fitz.Font(fontbuffer=font_buffers[new_fontname]) 
                    text = span["text"].replace(chr(0xFFFD), chr(0xB6))
                    # guard against non-utf8 characters
                    textb = text.encode("utf8", errors="backslashreplace")
                    text = textb.decode("utf8", errors="backslashreplace")
                    span["text"] = text
                    if wdir != [1, 0]:  # special treatment for tilted text
                        tilted_span(page, wdir, span, font)
                        continue
                    color = span["color"]  # make or reuse textwriter for the color
                    if color in textwriters.keys():  # already have a textwriter?
                        tw = textwriters[color]  # re-use it
                    else:  # make new
                        tw = fitz.TextWriter(page.rect)  # make text writer
                        textwriters[color] = tw  # store it for later use
                    try:
                        tw.append(
                            span["origin"],
                            text,
                            font=font,
                            fontsize=resize(span, font),  # use adjusted fontsize
                        )
                    except Exception as err:
                        print(f"page {page.number} exception: {err}")

        # now write all text stored in the list of text writers
        for color in textwriters.keys():  # output the stored text per color
            tw = textwriters[color]
            outcolor = fitz.sRGB_to_pdf(color)  # recover (r,g,b)
            tw.writeText(page, color=outcolor)

        clean_fontnames(page)

