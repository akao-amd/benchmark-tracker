# benchmark-tracker

A person or an agent keeps benchmark sets here. A workbook is an export. Excel only reads it.

Drop a directory of `label_isl*_osl*_c*_r*.json` or `.log` files. Each label becomes a block. Set the GPU divisor, the baseline, and which blocks go on the figure, then export `.xlsx`. **Add tab** appends that same sheet to the shared OneDrive workbook (`grok2_5_akao.xlsx` by default; override the link in `onedrive.url` or `BENCHMARK_ONEDRIVE_URL`). It uses the Microsoft 365 sign-in from `~/.config/microsoft-graph`. The working copy is `workspace.bta`.

Python 3 standard library, plus openpyxl for the workbook. No build step.

## Run

```bash
python3 run.py                 # opens http://localhost:8766/
python3 -m unittest discover -s tests
```

On Windows: `C:\msys64\usr\bin\bash.exe -lc "cd ~/work-journal/2026/nocopy/benchmark-tracker && python3 run.py"`.
