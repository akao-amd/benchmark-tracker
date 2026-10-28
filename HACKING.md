# Hacking benchmark-tracker

This app is the place a person or an agent keeps benchmark sets. Excel is only a reader of what the app exports. A `.bta` file is the document. An `.xlsx` is a rendering of it.

Python 3 standard library, plus openpyxl for the workbook. The UI is static files in `web/`. There is no build step.

## Run

```bash
python3 run.py                 # http://localhost:8766/
python3 run.py --no-browser
python3 -m unittest discover -s tests -v
```

The server binds `127.0.0.1:8766`. Static files are read from disk on each request, so a reload picks up `web/`. Python changes need a process restart. `print` in `run.py` is flushed because stdout is block-buffered when it is not a terminal.

Tests construct their own temporary archives. Do not point a test at the process's working archive, and do not let a test call `POST /api/onedrive`. That route talks to Microsoft Graph.

## Layout

| Path | Role |
| --- | --- |
| `run.py` | Process entry. Builds the HTTP app around the working archive. |
| `tracker/archive.py` | `.bta` model: blocks, measurements, ratios, figures. |
| `tracker/bench.py` | One SGLang or InferenceX `.json` or `.log`. |
| `tracker/ingest.py` | Walk a directory into blocks. |
| `tracker/workspace.py` | Load, mutate, save. A failed mutation is rolled back. |
| `tracker/fsview.py` | Left-hand file browser. |
| `tracker/export_xlsx.py` | The sheet Excel is asked to read. This is the style. |
| `tracker/excel_push.py` | Turn that sheet into Excel Graph edits. |
| `tracker/onedrive.py` | Add the sheet as a new tab on a OneDrive workbook. |
| `tracker/server.py` | JSON API and static files. |
| `web/` | The whiteboard. No framework. |
| `tests/test_tracker.py` | Model, ingest, sheet, and the OneDrive decision without Graph. |

## The archive

`format` is `"bta"`, `version` is `1`. Pretty JSON. A file holds a model name, up to 200 notices, and blocks. Block ids are unique. Labels are unique.

A block has a label, a GPU divisor (`gpus`, default 1), optional attributes, a chosen baseline, a chart flag, an active run, and measurements. Known attribute names are docker image, host name, accuracy score, BKC, chip, framework, and tp. Any other name is allowed. Empty known keys are kept so the editor always shows them.

A measurement is one `(input_len, output_len, concurrency, run)`. Throughput is stored as the server total. Per-GPU throughput is `server_total / gpus`, so changing the divisor does not reread files. Interactivity is `1000 / median_tpot_ms` and is not divided by the GPU count. Both are computed, not stored. `source` is the path the numbers came from. If that file later disappears, the numbers stay and the link is marked missing.

The sheet, the figure, and the measurement table show `active_run` only. Other runs stay in the file. `active_run` defaults to the lowest run present. A block with more than one run shows a `run N` badge and a Run select.

`gpus` is typed. A folder or a label that says `tp2` is not evidence of the divisor. The `tp` attribute is a label on the sheet (`TP2` if the value is digits). It is not the divisor.

## Ingest

A bench file is named:

```text
<label>_isl<isl>_osl<osl>_c<conc>[_r<run>]<suffix>.json
```

`.log` is the same shape. The match is case-insensitive. Underscores between the parts may repeat, and trailing underscores are stripped from the label. A missing `_r<run>` is run 1. The suffix is not part of the measurement identity, so `_OFFSPEC` still loads.

The walk skips `.git`, `__pycache__`, `node_modules`, and `.venv`, does not follow links, and stops after 20000 files. Directory names, then file names, are sorted. The first file that successfully loads for a label and `(isl, osl, conc, run)` is the one kept. A later file for the same key is a note, not an average. A file that fails to parse is skipped, so a later valid file for that key can still become the point. A throughput or median TPOT of zero is a skip.

Logs are matched case-insensitively on three lines: `Total token throughput (tok/s):`, `Median TTFT (ms):`, `Median TPOT (ms):`. JSON checks total-token throughput against the reported pieces within 5%, and checks isl, osl, and concurrency against the filename when the JSON has those fields. Framework is filled from the JSON `backend` when the block's framework attribute is still empty.

Dropping a directory on the empty board creates one block per filename label. Dropping it on a card adopts every loaded point into that card. The filename label is ignored. A key the card already has stays as it is. Empty attribute keys on the receiving block are filled. Label, GPU divisor, baseline, and chart membership stay on the receiving block.

A label that already exists is a 409. Nothing is written until the client sends `overwrite` or `rename`. Overwrite replaces points and sources and fills only empty attributes. The selected run is kept when that run still exists. Rename suggests `label (2)`, then `(3)`.

A `.bta` dropped as results is refused. Import it instead. Import replaces the working archive immediately. Export `.bta` downloads a copy and does not write that copy back onto the imported path.

## Editing

Right-click a card for Ref and Retire. Ref lists the directories the block was built from, and clicking one copies that path. A merge or a drop onto the card adds the new directory. A directory inside another recorded one is omitted, and the list never goes down to the `.json` or `.log`. Retire keeps the block in the file, takes it off the sheet and the figure, and frees its label so the next import of that name is a new current block. Restore puts it back unless that label is already current. Several retired generations may share one label. Each block records `added_at` when it is created, and `retired_at` when it steps aside. The card shows those times, and the editor lists every block with that label, newest first. A block saved before this stamp has no added time. The sheet and the figure use the current block.

Drag a card onto another card to merge. Non-colliding points always move. For a key in both blocks, the choice is `overwrite`, `keep`, `worse`, or `better`. Better ranks by higher per-GPU throughput, then lower median TTFT, then lower median TPOT, using each block's own divisor. An equal rank keeps the target for both better and worse. The source block is deleted. Dependents that used it as a baseline lose that link. "Always this" lives in the page only and clears on refresh. The default radio is Keep.

Copy attributes snapshots the attribute list in the page. Paste replaces the whole set and asks when the target already has a value. GPU divisor, baseline, and chart membership are not copied.

Search is hidden until Search or `/`. It matches the label and non-empty attribute values. Escape closes search, then deselects. The client serializes `POST /api/act` through one promise queue.

## The sheet

`tracker/export_xlsx.py` is the layout. Summary starts at column B. The raw dump starts at column M, and its headers are `RAW_HEADERS` in `tracker/bench.py` (Framework before Precision). Summary headers are input_len, output_len, concurrency, Throughput per GPU, Interactivity, Median TTFT, Median TPOT.

Summary throughput, TTFT, and TPOT are formulas into the raw block. Interactivity is `=IF(H{row}="","",1000/H{row})`. Ratios are candidate divided by the same shape and concurrency on the named baseline, formatted `0%`. A concurrency the baseline does not have is a truly empty cell. An empty string would make Excel `GEOMEAN` return `#VALUE!`. `GEOMEAN` is written only when at least one concurrency in that shape matches. A shape the baseline lacks entirely gets no geomean row. The in-app geomean is the same rule: product to the power `1/n` of the positive ratios, blanks omitted. A baseline value of zero omits that one ratio in the app.

The green chip is the chip attribute, or the block label when chip is empty, with framework beside it. The block label is not also written into the accuracy column. The versus banner is `{label} v.s. {baseline}` on a yellow merge. Docker, machine, accuracy, date, and BKC sit on the rows under the chip. Gridlines are off. The page is landscape, fit to width. Column A is a narrow gutter.

One scatter per shape, and only for blocks with chart on. X is interactivity, Y is throughput per GPU. The line follows concurrency order. It is not sorted by X. The chart skin is dark (`#404040`) with the Office series colors. The in-app figure is the same series as an SVG, with the title inside the SVG.

A string that starts with `=` is stored as text. `formula()` is what marks a cell as a formula.

## Add tab

Add tab asks for an `https` sharing link, then adds one sheet named `bta-%m%d-%H%M`. The new tab is placed at index 1, immediately right of the leftmost tab. A workbook that has only the new sheet is left alone. Other sheets are not rewritten.

The link is remembered locally for the next time. `BENCHMARK_ONEDRIVE_URL` overrides that. Otherwise the default in `tracker/onedrive.py` is used. The remembered link is gitignored.

Sign-in is the existing Microsoft Graph client (`MS_GRAPH_CLIENT`, or `graph_client.py` beside the slai-email skill). This app calls `get_token()` and makes its own requests. It does not start a device-code flow. The sign-in page belongs to the Teams public client. The token's audience is Graph, and the scopes used here are `Files.ReadWrite.All` and `Sites.ReadWrite.All`. Set `MS_GRAPH_CONFIG_DIR` when the token does not live under the interpreter's home. MSYS Python's home is not the Windows profile.

Two ways the bytes come back decide the write:

- A real xlsx starts with `PK`. openpyxl appends the sheet and `PUT` replaces the file, with `If-Match`. HTTP 412 means the file changed during the edit. HTTP 423 means Excel has it open.
- A protected package starts with the OLE header `d0 cf 11 e0` and contains `EncryptedPackage`. openpyxl cannot read it, and replacing the file would discard the other tabs and the protection. The sheet is drawn through an Excel workbook session instead: `createSession`, add a worksheet, move it to position 1, write cells, close the session. The same session path is the fallback when a `PK` upload returns 423.

`tracker/excel_push.py` reads the openpyxl sheet and plans those Graph calls. Font, fill, alignment, and borders are separate requests. A combined format body is rejected. Base font and base alignment run before the style batch, because batch order is not guaranteed and a later "not bold" would wipe the bold cells. Column widths run after the batch.

Graph `columnWidth` is not an openpyxl character width. A fresh column reports `54` for Excel's default of `8.43` characters. Sending `14` stores about two characters, and wrap then stacks every letter. Convert with `round(width * 54 / 8.43, 2)`. Row height is already in points. Width `0` stays `0` and hides the chart-data columns.

The session chart is `XYScatterLines` with the same line colors, a title, and a position. Excel's chart API has no per-series X inside one source range, so each chart gets a data island to the right: the series' own rows are contiguous formulas, and the other series are `=NA()` so the line does not plot blanks as zeros. The session path does not reproduce the dark chart skin, the plot layout, hidden gridlines, or the page setup. The `PK` path does, because it uploads the openpyxl workbook.

Do not print a download body. A protected package decoded as text is a string of replacement characters, which is how a successful download once showed up as a garbage banner.

## The page

Left: a file browser. Empty path lists drives. Names starting with `.` are hidden. Entries are newest mtime first, folders and files together, capped at 400. Each folder row has Add. There is no button that ingests the folder you are merely looking at.

Right: cards, or Figures. Hover shows the label and the non-empty attributes. Click opens the editor. A card drag carries `block:<id>`. A path drop on a card adopts. A path drop on the board ingests by filename. A `block:` drop on the board is ignored.

`GET /api/workspace` is the whole view, including the OneDrive target label. `GET /api/fs?path=` lists a directory. `POST /api/act` is `new`, `import`, `ingest`, `patch`, `delete`, `move`, `merge`, `delete_point`, `clear_notices`, or `model`. `ingest` takes `into` for adopt. A collision is HTTP 409 with `collisions` and `notices`, and the archive is unchanged.

## Traps

- Under MSYS Python, `Path.resolve()` on `C:\...` becomes `/:\...`, which does not exist. `fsview.normalize()` rewrites a drive path to `/c/...` when that path exists. Tests and anything else that opens a Windows path should go through it.
- The file browser is newest-first. Ingest is not. Ingest keeps the first valid file in directory-name, then filename, order.
- Do not infer the GPU divisor from a path.
- A ratio cell that does not apply must stay unset. `""` is text, and `GEOMEAN` rejects text.
- On the session path, apply column widths after every other format call, and convert them. The openpyxl width is the source of truth. The Graph number is not.
- A Graph 409 or 423 during the session still means the workbook is locked. Close it in Excel, or retry in a moment.
- If the session fails before the sheet is saved, the new sheet is deleted and the session is closed. A sheet that was never created is not deleted.
- `Path.home()` under MSYS is the MSYS home, not the Windows user profile. That is why the Graph token directory is searched in more than one place.
