\## Purpose



Cut the manual operation on giant app like Excel.  We shouldn't have needed that general a thing.



Call "the app" or "this app" to indicate this benchmark tracker below.



\## File Format



Define a `\*.bta` format to keep a \*\*Benchmark Tracking Archive\*\*, which basically have multiple \*\*data blocks\*\*.  This app can import a .bta file to further edit it, and it can also export a .bta file to an .xlsx file without glitch.

### Data Block



A data block has a \*\*label\*\*.  You may check the example in "C:\\msys64\\home\\akao\\work-journal\\2026\\ww43\\local\\1020\_autolog\\benchmark\_1020\_v8.xlsx" or all prior revisions.  There are many data blocks.  The top colored part of each block combined is the label of the block.



A block can have multiple \*\*attributes\*\*.  The common ones are: \*\*docker image\*\*, \*\*host name\*\*, \*\*accuracy score\*\*, \*\*BKC\*\*. They are treated optional.  Arbitrary attributes can be added as well.



The main body of a block is a few sub-blocks, where each has entries of unique `input\_len` and `output\_len` pairs.  The entries are often different in `concurrency`, and they are often 4,8,16,32,64.  Then there is the main metrics, Total Throughput per GPU, Interactivity (Check GitHub InferenceX benchmark toolset for details), Median TTFT and Median TPOT.

A block can have a augmented \*\*ratio\*\* part if it is not a \*\*baseline\*\* data block.  The content of the ratio blocks contains the ratio over some specified baseline blocks, and there are extra line to store/calculate the geomean of the ratio.

As the reference file, the right-hand side of the data block is the raw data dump.



Generally speaking both SGLang bench\_serving tool and InfernceX benchmark framework can be augmented to have the output file named in `<label>\_isl<isl>\_osl<osl>\_c<concurreny>\_r<run>.\[log|json]`.  We expect a general usage is to drag a directory into the operating space, and then the app automatically traverse the directory and subordinates to check the pattern, then retrieve the data into data blocks, in a temporary working `.bta` file.  The user can then edit the labels and attributes, or query figures (to be expanded in the future, however, the TTT-per-GPU-to-Interactivity pareto figure is a must).



\## Scenarios



\### Case A



New a workspace.  Left window is a tall and thin file browser of the local computer, and right a white board like space.  Navigate a drag some directory to the right window, then the data of the pattern is exported.  The data blocks are shown in thumbnails small blocks in the right window.  Mouse hover a block shows its label and attributes, click to edit.



Other than preview data blocks, should allow to support figures.



\### Case B



Import from a existing .bta file.  The file has internal information to track back to raw data files, but it acknowledges the missing links if any of the raw data were gone without error.  Do some editing like removing existing ones.  Drag a directory causing a label collision, can choose overwrite or rename.



If the data blocks become too many, there is a search bar (hidden usually) to be called out and search for label or attributes.





