import subprocess
import json

with open("caption_out.json", "r", encoding="utf-8") as f:
    d = json.load(f)

title = d["title"]
desc = d["description"]
tags = d["tags"]
file_path = r"C:\Users\said\Downloads\PXL_20260919_093337377 (1).jpg"

args = [
    "-charset", "utf8",
    "-charset", "iptc=utf8",
    "-codedcharacterset=utf8",
    "-overwrite_original",
    "-preserve",
    "-XMP-dc:Title=",
    f"-XMP-dc:Title={title}",
    "-XMP-photoshop:Headline=",
    f"-XMP-photoshop:Headline={title}",
    "-IPTC:Headline=",
    f"-IPTC:Headline={title}",
    "-IPTC:ObjectName=",
    f"-IPTC:ObjectName={title}",
    f"-EXIF:XPTitle={title}",
    "-XMP-dc:Description=",
    f"-XMP-dc:Description={desc}",
    "-IPTC:Caption-Abstract=",
    f"-IPTC:Caption-Abstract={desc}",
    f"-EXIF:ImageDescription={desc}",
    "-XMP-dc:Subject=",
    "-IPTC:Keywords=",
]
for t in tags:
    args.append(f"-XMP-dc:Subject={t}")
    args.append(f"-IPTC:Keywords={t}")

args.append(f"-EXIF:XPKeywords={';'.join(tags)}")
args.append(file_path)

argfile_path = "exif_args.txt"
with open(argfile_path, "w", encoding="utf-8") as f:
    for a in args:
        f.write(a + "\n")

cmd = ["exiftool", "-charset", "utf8", "-@", argfile_path]
res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
print("STDOUT:", res.stdout)
print("STDERR:", res.stderr)
