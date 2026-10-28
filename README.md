# benchmark-tracker

A person or an agent keeps benchmark sets here. A workbook is an export. Excel only reads it.

Drop a directory of `label_isl*_osl*_c*_r*.json` or `.log` files. Each label becomes a block. Set the GPU divisor and which blocks go on the figure. One baseline in the header is shared by every other block. Then export `.xlsx`. **Add tab** asks for a OneDrive sharing link and appends that sheet to the workbook.

Python 3 standard library, plus openpyxl for the workbook. No build step. How the pieces fit is in [HACKING.md](HACKING.md).

## Run

```bash
python3 run.py                 # opens http://localhost:8766/
python3 -m unittest discover -s tests
```

On Windows: `C:\msys64\usr\bin\bash.exe -lc "cd ~/work-journal/2026/nocopy/benchmark-tracker && python3 run.py"`.
