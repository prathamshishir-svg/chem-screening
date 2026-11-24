"""Pulls ONE file out of a big zip into the current folder, without unpacking the rest.

Usage:
    python extract_from_zip.py                       (uses the defaults below)
    python extract_from_zip.py other.zip some_file.7z
"""
import os
import shutil
import sys
import zipfile

zip_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.expanduser("~"), "Downloads", "5104873.zip")
wanted = sys.argv[2] if len(sys.argv) > 2 else "1976_Sep2016_USPTOgrants_smiles.7z"

if not os.path.exists(zip_path):
    sys.exit(f"Zip file not found: {zip_path}\n"
             "Pass its full path as the first argument, in quotes.")

with zipfile.ZipFile(zip_path) as z:
    entries = [i for i in z.infolist() if not i.is_dir()]
    print(f"Files inside {os.path.basename(zip_path)}:")
    for i in entries:
        print(f"  {i.filename}   ({i.file_size / 1e6:,.0f} MB)")

    match = [i for i in entries if os.path.basename(i.filename).lower() == wanted.lower()]
    if not match:
        sys.exit(f"\nCould not find '{wanted}' in the zip. "
                 "Send me the list above and I will adjust the name.")

    target = os.path.basename(match[0].filename)
    if os.path.exists(target):
        sys.exit(f"\n'{target}' is already in this folder. Nothing to do.")
    print(f"\nExtracting {target} into {os.getcwd()} ...")
    with z.open(match[0]) as src, open(target, "wb") as dst:
        shutil.copyfileobj(src, dst)

print(f"Done: {target} ({os.path.getsize(target) / 1e6:,.0f} MB)")
