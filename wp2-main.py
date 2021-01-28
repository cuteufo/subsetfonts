from subsetfont import subset_fonts
import fitz

if __name__ == "__main__":
    doc = fitz.open()
    page = doc.newPage()

    page.insertFont(fontname="_ST1", fontfile="songti/Songti1.ttf")
    page.insertFont(fontname="_ST3", fontfile="songti/Songti3.ttf")
    page.insertFont(fontname="_ST4", fontfile="songti/Songti4.ttf")

    text = "风景名胜 遊戲電競"
    page.insertText((20, 20), text, fontname="_ST1", fontsize=20)
    page.insertText((20, 60), text, fontname="_ST3", fontsize=20)
    page.insertText((20, 100), text, fontname="_ST4", fontsize=20)

    # call subset_fonts to subset the font data in final PDF
    # if we don't call subset_fonts, the final PDF file size is 41.5MB
    # if we call subset_fonts, the final PDF file size is 13KB
    subset_fonts(doc)

    # another idea is how about implement subset_fonts in doc.save()
    # with garbarge=3 or above?
    doc.save("abc.pdf", garbage=3, deflate=True)