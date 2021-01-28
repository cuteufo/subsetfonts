import fitz
import os
import json

from subsetfont import subset_fonts

# 这个脚本，用了第三方ttf字体+subset的方式来创建PDF，以保证生成的PDF的体积尽量小

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
        print("Begin font subset...")
        fts.main(
            [
                "oldfont.ttf",
                "--unicodes-file=uncfile.txt",
                "--output-file=newfont.ttf",
                "--recalc-bounds",
            ]
        )
        print("Done font subset...")
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


def get_unc_list_from_text(text):
    unc_list = [ord(c) for c in text]
    return unc_list


def do_write_text(page: fitz.Page, point, text, method="inserttext", box=None, 
                    fontfile="songti/Songti0.ttf", box_align=fitz.TEXT_ALIGN_LEFT):

    font = fitz.Font(fontfile=fontfile)
    font_subset_buffer = build_subset(font.buffer, get_unc_list_from_text(text))
    if font_subset_buffer is None:
        print("Cannot subset font")

    if method == "textwriter" or (method == "filltextbox" and box is not None):
        font_subset = fitz.Font(fontbuffer=font_subset_buffer)

        textb = text.encode("utf8", errors="backslashreplace")
        text = textb.decode("utf8", errors="backslashreplace")

        tw = fitz.TextWriter(page.rect+(0, 72, 0, -72))  # make a TextWriter object

        ##### fillTextbox() or append(), same result #####
        # textbox = page.rect + (72, 72, -72, -72)  # leave borders of 1 inch
        if method == "filltextbox":
            page.drawRect(box)
            tw.fillTextbox(box, text, font=font_subset, align=box_align)
        else:
            tw.append(point, text, font=font_subset, fontsize=11)

        tw.writeText(page) #, color=(0.5, 0.5, 0.5))

    elif method == "inserttext":
        fontname = "ST0"
        page.insertFont(fontname=fontname, fontbuffer=font_subset_buffer)
        page.insertText(point, text, fontname=fontname)

    elif method == "filltextbox":
        if box is None:
            print("box is required for filltextbox")
            return
        

def do1(page):
    do_write_text(page, (74, 97), "abcdefg 1234567 一二三四 insert", method="inserttext", fontfile="songti/Songti0.ttf")
    do_write_text(page, (74, 127), "abcdefg 1234567 一二三四 writer", method="textwriter", fontfile="songti/Songti1.ttf")
    do_write_text(page, (74, 157), "abcdefg 1234567 一二三四 writer", method="textwriter", fontfile="songti/Songti3.ttf")
    do_write_text(page, None, "abcdefg 1234567 一二三四 fillTextbox", method="filltextbox", box=(74, 187, 400, 217), 
                    fontfile="songti/Songti4.ttf", box_align=fitz.TEXT_ALIGN_RIGHT)

    d = page.getText("dict")


def do2(page, text="全权委托 全權委託 SUMMARY"):
    page.insertFont(fontname="_ST1", fontfile="songti/Songti1.ttf")
    page.insertFont(fontname="_ST3", fontfile="songti/Songti3.ttf")

    page.insertText((72, 70), text, fontname="_ST1", fontsize=20)
    page.insertText((72, 100), text, fontname="_ST3", fontsize=20)

def do3(page, text="全权委托 全權委託 SUMMARY"):
    page.insertFont(fontname="_ST11", fontfile="songti/Songti1.ttf")
    page.insertFont(fontname="_ST13", fontfile="songti/Songti3.ttf")

    page.insertText((72, 70), text, fontname="_ST11", fontsize=20)
    page.insertText((72, 100), text, fontname="_ST13", fontsize=20)

#def subset_fonts(doc):



if __name__ == "__main__":
    doc = fitz.open()
    page = doc.newPage()
    do2(page)

    page = doc.newPage()
    do3(page, text="每月报告 每月報告 MONTHLY")

    pdf_name="abc.pdf"
    subset_fonts(doc)
    doc.save(pdf_name, garbage=3, deflate=True,) #, garbage=3, deflate=True)

    doc2 = fitz.open(pdf_name)
    print(json.dumps(doc2[0].getText("dict"), indent=2, ensure_ascii=False))

