# Mask Local

A local app for reviewing and redacting sensitive content in PDFs and photos before sharing them.

## Requirements

- Python 3.12 (the project supports Python `>=3.12,<3.13`)
- [uv](https://docs.astral.sh/uv/)
- Tesseract OCR installed and available on `PATH` (needed for photos and scanned PDF pages)

## Set up

Run these commands from the project root:

```sh
uv python install 3.12.12
uv sync
uv run python -m spacy download en_core_web_sm
```

Setup needs an internet connection to download Python, packages, and the English spaCy model. After setup, document analysis and redaction run locally. Install Tesseract using the instructions for your operating system, then confirm the `tesseract` command is available.

On macOS, `setup.command` is an optional setup helper and `start.command` launches the app and opens it in a browser. These helpers are macOS-specific; the commands below work from a terminal on any supported operating system.

## Run

From the project root:

```sh
uv run streamlit run app.py --server.address 127.0.0.1
```

Open `http://127.0.0.1:8501` in a browser. The app listens only on the local computer.

## Privacy and file handling

- Streamlit listens only on `127.0.0.1` and usage statistics are disabled.
- Document analysis, OCR, previews, and redaction run in the local Python process. Documents are not sent to a hosted Presidio service or another cloud service.
- Uploaded source bytes and generated output stay in the app session's memory. Click **Reset this session** or close the app to clear them from that session.
- PDF downloads are flattened page images. This removes selectable/searchable text, form values, annotations, bookmarks, and source metadata. The PDF may be larger than the original.
- Image downloads are metadata-free PNG files.

## Use the app

1. Drop one PDF or photo into the file area. Supported photos: JPEG, PNG, TIFF, WebP, and BMP. Files may be up to 100 MB; PDFs may contain up to 300 pages.
2. For a mixed or partially scanned PDF, turn on **OCR every PDF page**. Otherwise, embedded PDF text is analyzed and OCR runs automatically on pages with no extractable text. Photos always use OCR.
3. Optionally enter exact extra names, IDs, or phrases under **Extra words or phrases to mask**, one per line. This is useful when Presidio does not recognize a particular value.
4. Click **Analyze locally**. Inspect each page preview, then review the checklist grouped by detected category (such as `PERSON`, `LOCATION`, or `DATE_TIME`). Every match starts unchecked. Use **Select all** or **Clear all** within a group to choose matches; checked matches will be masked. Checkbox changes stay responsive while you make selections and are applied when you submit an action.
5. To find specific text, enter words or phrases under **Search for words or phrases**. Separate terms with commas or new lines (for example, `Apple, Boy`). Search checks all analyzed pages using the extracted text, including OCR text. Each search term gets its own group, with every occurrence shown separately and unchecked. A new search replaces the previous search groups.
6. Select the matches to cover, click **Create redacted copy**, then **Download clean copy**. Check the downloaded file page by page before sharing.
7. Click **Reset this session** or close the app when you are done.

## Detection limits

Presidio uses English language processing and recognizers for common personal data. Tesseract OCR can misread small, rotated, handwritten, low-contrast, or blurry text. Names, uncommon identifiers, and context-specific secrets may be missed. Review the entire output; add known phrases manually and run the analysis again when needed. This app does not guarantee that every sensitive item will be detected.

If the coral box does not line up with visible text in a PDF, turn on **OCR every PDF page**, analyze again, and review the new boxes.

## Stop the app

Stop the Streamlit process with **Ctrl+C** in the terminal. If using `start.command` on macOS, you can also close its Terminal window.
