lines = open(r"D:\\Project\\Clases\\src\\models.py","r",encoding="utf-8").readlines()
new_lines = []
for line in lines:
    if "language: TranscriptLanguage = TranscriptLanguage.UNKNOWN" in line:
        line = line.replace("language: TranscriptLanguage = TranscriptLanguage.UNKNOWN", "language: TranscriptLanguage = field(default_factory=lambda: TranscriptLanguage.UNKNOWN)")
    elif "language: Language = Language.UNKNOWN" in line:
        line = line.replace("language: Language = Language.UNKNOWN", "language: Language = field(default_factory=lambda: Language.UNKNOWN)")
    new_lines.append(line)
with open(r"D:\\Project\\Clases\\src\\models.py","w",encoding="utf-8") as f:
    f.writelines(new_lines)
print("Fixed")
