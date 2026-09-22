import re
with open(r"D:\\Project\\Clases\\src\\models.py","r",encoding="utf-8") as fh:
    mcontent=fh.read()
mcontent=re.sub(r"language: TranscriptLanguage = TranscriptLanguage\\.UNKNOWN", "language: TranscriptLanguage = field(default_factory=lambda: TranscriptLanguage.UNKNOWN)", mcontent)
mcontent=re.sub(r"language: Language = Language\\.UNKNOWN", "language: Language = field(default_factory=lambda: Language.UNKNOWN)", mcontent)
with open(r"D:\\Project\\Clases\\src\\models.py","w",encoding="utf-8") as fh:
    fh.write(mcontent)
print("Fixed")
