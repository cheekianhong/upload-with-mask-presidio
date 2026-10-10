from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path

import streamlit as st

from masking_core import (
    analyze_file,
    create_redacted_output,
    find_text_matches,
    load_analyzer,
    render_previews_for_pages,
)


st.set_page_config(
    page_title="Mask Local",
    page_icon="◼",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    :root {
      --ink: #243b32;
      --muted: #64776d;
      --paper: #f5f2e9;
      --panel: #fffdf8;
      --line: #d9ded3;
      --accent: #c64f38;
      --moss: #315c49;
    }
    .stApp { background: var(--paper); color: var(--ink); }
    [data-testid="stHeader"] { background: rgba(245, 242, 233, .88); }
    [data-testid="stSidebar"] { background: #e9eee5; border-right: 1px solid var(--line); }
    h1, h2, h3 { color: var(--ink); font-family: Georgia, "Times New Roman", serif; letter-spacing: -.025em; }
    h1 { font-size: clamp(2.3rem, 4vw, 3.7rem) !important; line-height: 1.03 !important; }
    .eyebrow { color: var(--moss); font: 700 .72rem/1.2 ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing: .13em; text-transform: uppercase; }
    .hero-note { max-width: 52rem; color: var(--muted); font-size: 1.05rem; line-height: 1.6; }
    .local-stamp { display: inline-block; padding: .38rem .65rem; border: 1px solid #b7c8b8; border-radius: 999px; color: #315c49; background: #edf3eb; font: 650 .74rem/1 ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing: .06em; text-transform: uppercase; }
    [data-testid="stMetric"] { background: var(--panel); border: 1px solid var(--line); padding: .9rem 1rem; border-radius: .55rem; }
    [data-testid="stExpander"] { background: var(--panel); border: 1px solid var(--line); border-radius: .55rem; }
    .stButton > button, .stDownloadButton > button { border-radius: .45rem; font-weight: 650; min-height: 2.8rem; }
    .stButton > button[kind="primary"] { background: var(--moss); border-color: var(--moss); }
    div[data-testid="stFileUploader"] { background: var(--panel); border: 1px dashed #9aab9f; border-radius: .65rem; padding: .5rem; }
    code { color: #7d3527 !important; }
    </style>
    """,
    unsafe_allow_html=True,
)


def _signature(data: bytes, manual_text: str, min_score: float, ocr_every_page: bool) -> str:
    digest = hashlib.sha256()
    digest.update(data)
    digest.update(json.dumps([manual_text, min_score, ocr_every_page], ensure_ascii=False).encode("utf-8"))
    return digest.hexdigest()


def _clear_session_artifacts() -> None:
    for key in ("analysis", "output", "output_signature", "source_hash"):
        st.session_state.pop(key, None)


def _set_mask_group(widget_keys: list[str], selected: bool) -> None:
    for widget_key in widget_keys:
        st.session_state[widget_key] = selected


def _parse_search_terms(value: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for raw_term in re.split(r"[,\n]", value):
        term = " ".join(raw_term.split())
        normalized = term.casefold()
        if term and normalized not in seen:
            terms.append(term)
            seen.add(normalized)
    return terms


@st.fragment
def _render_mask_review_controls(analysis, source_bytes: bytes, filename: str, is_pdf: bool) -> None:
    review_id = analysis["review_id"]
    detections = analysis["detections"]
    search_pages = analysis.get("search_pages", [])
    selected_ids: set[str] = set()

    st.markdown("#### Search for words or phrases")
    st.caption(
        "Searches use the text already extracted from every page, including OCR. Separate terms with commas or new lines; "
        "each term gets its own group."
    )
    with st.form(key=f"mask-review-form-{review_id}", enter_to_submit=False):
        search_query = st.text_area(
            "Words or phrases",
            placeholder="Apple, Boy\nTaylor Smith",
            key=f"search-query-{review_id}",
            help="Search is case-insensitive, matches whole words or exact phrases, and spans all pages. A new search replaces the previous search groups.",
        )
        search_col, clear_col = st.columns(2)
        search_clicked = search_col.form_submit_button(
            "Search all pages",
            key=f"search-submit-{review_id}",
            width="stretch",
            disabled=not bool(search_pages),
        )
        clear_search_clicked = clear_col.form_submit_button(
            "Clear search results",
            key=f"search-clear-{review_id}",
            width="stretch",
            disabled=not bool(analysis.get("search_terms")),
        )
        if not search_pages:
            st.info("Run Analyze locally again to enable search for this review.")

        category_groups: dict[str, list[dict[str, object]]] = {}
        for detection in detections:
            category_groups.setdefault(str(detection["entity"]), []).append(detection)

        if category_groups:
            st.markdown("#### Choose categories and matches to mask")
        else:
            st.caption("No matches yet. Search for exact words or phrases above to add mask candidates.")

        for category, category_detections in sorted(category_groups.items()):
            is_search_group = all(item["source"] == "search" for item in category_detections)
            group_id = (
                f"search-{analysis['search_id']}"
                if is_search_group
                else f"review-{review_id}"
            )
            widget_keys = [
                f"mask-{group_id}-{item['id']}"
                for item in category_detections
            ]
            with st.expander(
                f"{category} · {len(category_detections)} match{'es' if len(category_detections) != 1 else ''}",
                expanded=True,
            ):
                select_col, clear_col = st.columns(2)
                select_col.form_submit_button(
                    "Select all",
                    key=f"select-all-{group_id}-{category}",
                    width="stretch",
                    on_click=_set_mask_group,
                    args=(widget_keys, True),
                )
                clear_col.form_submit_button(
                    "Clear all",
                    key=f"clear-all-{group_id}-{category}",
                    width="stretch",
                    on_click=_set_mask_group,
                    args=(widget_keys, False),
                )

                for detection, widget_key in zip(category_detections, widget_keys, strict=True):
                    page_label = (
                        f"Page {int(detection['page']) + 1}"
                        if is_pdf
                        else "Photo"
                    )
                    confidence = f"{detection['score']:.0%}"
                    label = f"{page_label} · {confidence} · “{detection['text']}”"
                    if st.checkbox(label, value=False, key=widget_key):
                        selected_ids.add(str(detection["id"]))

        st.markdown("### 03 &nbsp; Create the clean copy", unsafe_allow_html=True)
        pdf_scale = 2.2
        if is_pdf:
            pdf_scale = st.slider(
                "PDF page quality",
                min_value=1.5,
                max_value=3.0,
                value=2.2,
                step=0.1,
                help="Higher settings create a larger PDF with sharper flattened pages.",
            )
        create_clicked = st.form_submit_button(
            "Create redacted copy",
            type="primary",
            width="stretch",
            disabled=not bool(detections),
        )

    if search_clicked:
        terms = _parse_search_terms(search_query)
        if not terms:
            st.warning("Enter one or more words or phrases, separated by commas or new lines.")
        else:
            try:
                search_detections, missing_terms = find_text_matches(search_pages, terms)
                all_detections = [
                    *analysis.get("base_detections", detections),
                    *search_detections,
                ]
                changed_pages = set(analysis.get("search_page_indices", []))
                changed_pages.update(int(item["page"]) for item in search_detections)
                updated_previews = render_previews_for_pages(
                    source_bytes, filename, all_detections, changed_pages
                )
                analysis["search_detections"] = search_detections
                analysis["search_terms"] = terms
                analysis["search_missing_terms"] = missing_terms
                analysis["search_id"] = uuid.uuid4().hex
                analysis["detections"] = all_detections
                analysis["search_page_indices"] = sorted(
                    {int(item["page"]) for item in search_detections}
                )
                analysis["previews"].update(updated_previews)
                st.session_state["analysis"] = analysis
                st.rerun()
            except Exception as exc:
                st.error(f"Search could not be completed: {exc}")

    if clear_search_clicked:
        changed_pages = set(analysis.get("search_page_indices", []))
        base_detections = analysis.get("base_detections", detections)
        updated_previews = render_previews_for_pages(
            source_bytes, filename, base_detections, changed_pages
        )
        analysis["search_detections"] = []
        analysis["search_terms"] = []
        analysis["search_missing_terms"] = []
        analysis["search_page_indices"] = []
        analysis["search_id"] = uuid.uuid4().hex
        analysis["detections"] = base_detections
        analysis["previews"].update(updated_previews)
        st.session_state["analysis"] = analysis
        st.rerun()

    if analysis.get("search_terms"):
        if analysis.get("search_detections"):
            st.success(f"Found {len(analysis['search_detections'])} matches across the searched terms.")
        if analysis.get("search_missing_terms"):
            st.warning("No matches found for: " + ", ".join(analysis["search_missing_terms"]))

    st.caption("Checkbox changes stay buffered while you choose. Search, group actions, or Create redacted copy submits them.")
    st.caption(f"Selected matches: {len(selected_ids)}")
    if detections and not selected_ids:
        st.info("Select at least one proposed mask to create a redacted copy.")

    selection_digest = hashlib.sha256(
        json.dumps(
            [analysis["signature"], analysis["search_id"], sorted(selected_ids), pdf_scale]
        ).encode("utf-8")
    ).hexdigest()
    if st.session_state.get("output_signature") != selection_digest:
        st.session_state.pop("output", None)
        st.session_state.pop("output_signature", None)

    if create_clicked:
        if not selected_ids:
            st.info("Select at least one proposed mask to create a redacted copy.")
        else:
            try:
                with st.spinner("Applying permanent masks and preparing the download…"):
                    output_bytes, output_name, mime = create_redacted_output(
                        source_bytes,
                        filename,
                        detections,
                        selected_ids,
                        pdf_scale=pdf_scale,
                    )
                st.session_state["output"] = {
                    "bytes": output_bytes,
                    "name": output_name,
                    "mime": mime,
                }
                st.session_state["output_signature"] = selection_digest
            except Exception as exc:
                st.error(str(exc))

    output = st.session_state.get("output")
    if output and st.session_state.get("output_signature") == selection_digest:
        st.success("Clean copy ready. The selected content was covered permanently.")
        st.download_button(
            "Download clean copy",
            data=output["bytes"],
            file_name=output["name"],
            mime=output["mime"],
            type="primary",
            width="stretch",
        )
        if is_pdf:
            st.caption("The PDF is flattened to page images. Text will no longer be selectable or searchable.")
        st.caption("Check the downloaded file before sharing. This tool cannot guarantee every sensitive detail was found.")


with st.sidebar:
    st.markdown('<div class="eyebrow">On this computer</div>', unsafe_allow_html=True)
    st.markdown("### A private review desk")
    st.markdown(
        "Files are processed by the app on this computer. The app binds to `127.0.0.1` "
        "and does not send documents to a cloud service."
    )
    st.divider()
    st.markdown("**What it can inspect**")
    st.markdown("PDF text, scanned PDF pages, and JPEG, PNG, TIFF, WebP, or BMP images.")
    st.markdown("**What it cannot guarantee**")
    st.markdown("Automatic PII detection can miss content. Read every page and add exact phrases that matter.")
    st.divider()
    st.caption("Uploads and generated files remain in this browser session's memory until you reset or close the app.")


st.markdown('<div class="eyebrow">Local document preparation · No upload service</div>', unsafe_allow_html=True)
st.title("Redact before it leaves.")
st.markdown(
    '<p class="hero-note">Inspect a proposed mask, choose what to cover, and create a clean copy on this computer. '
    'Nothing is redacted until you review the matches and press the button.</p>',
    unsafe_allow_html=True,
)
st.markdown('<span class="local-stamp">●  local processing</span>', unsafe_allow_html=True)

st.markdown("### 01 &nbsp; Choose a file", unsafe_allow_html=True)
upload_nonce = st.session_state.setdefault("upload_nonce", 0)
uploaded = st.file_uploader(
    "Drop one PDF or photo here",
    type=["pdf", "jpg", "jpeg", "png", "tif", "tiff", "webp", "bmp"],
    help="Maximum upload size is 100 MB. Files stay in this local app session.",
    label_visibility="collapsed",
    key=f"file-uploader-{upload_nonce}",
)

if uploaded is None:
    if st.session_state.get("source_hash"):
        _clear_session_artifacts()
else:
    source_bytes = uploaded.getvalue()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    if st.session_state.get("source_hash") not in (None, source_hash):
        _clear_session_artifacts()
    st.session_state["source_hash"] = source_hash

    name = uploaded.name
    is_pdf = Path(name).suffix.lower() == ".pdf"
    left, right = st.columns([1.2, 1], gap="large")
    with left:
        ocr_every_page = st.checkbox(
            "OCR every PDF page",
            value=False,
            disabled=not is_pdf,
            help="Use OCR instead of embedded PDF text on every page. Turn this on for mixed or partially scanned PDFs.",
        )
    with right:
        min_score = st.slider(
            "Detection confidence threshold",
            min_value=0.0,
            max_value=1.0,
            value=0.35,
            step=0.05,
            help="Lower values show more possible matches, including more false positives.",
        )
    manual_text = st.text_area(
        "Extra words or phrases to mask",
        placeholder="One exact phrase per line, for example:\nTaylor Smith\nACCT-009184",
        help="Useful for names or IDs that automatic detection may not recognize. Matching is case-insensitive.",
    )

    requested_signature = _signature(source_bytes, manual_text, min_score, ocr_every_page)
    analyze_clicked = st.button("Analyze locally", type="primary", width="stretch")
    if analyze_clicked:
        try:
            manual_terms = [line.strip() for line in manual_text.splitlines() if line.strip()]
            with st.spinner("Reading pages and checking text locally…"):
                analyzer = load_analyzer()
                result = analyze_file(
                    source_bytes,
                    name,
                    analyzer,
                    manual_terms=manual_terms,
                    min_score=min_score,
                    ocr_every_pdf_page=ocr_every_page,
                )
            st.session_state["analysis"] = {
                **result,
                "signature": requested_signature,
                "review_id": uuid.uuid4().hex,
                "base_detections": result["detections"],
                "search_detections": [],
                "search_terms": [],
                "search_missing_terms": [],
                "search_page_indices": [],
                "search_id": uuid.uuid4().hex,
            }
            st.session_state.pop("output", None)
            st.session_state.pop("output_signature", None)
        except Exception as exc:
            st.error(str(exc))

    analysis = st.session_state.get("analysis")
    if analysis and analysis.get("signature") != requested_signature:
        st.info("Your file or analysis options changed. Run **Analyze locally** again to refresh the review.")
        st.session_state.pop("output", None)
        st.session_state.pop("output_signature", None)
        analysis = None

    if analysis:
        analysis.setdefault("review_id", uuid.uuid4().hex)
        analysis.setdefault("base_detections", analysis["detections"])
        analysis.setdefault("search_detections", [])
        analysis.setdefault("search_terms", [])
        analysis.setdefault("search_missing_terms", [])
        analysis.setdefault("search_page_indices", [])
        analysis.setdefault("search_id", uuid.uuid4().hex)
        analysis["detections"] = [
            *analysis["base_detections"],
            *analysis["search_detections"],
        ]
        detections = analysis["detections"]
        st.markdown("---")
        st.markdown("### 02 &nbsp; Review proposed masks", unsafe_allow_html=True)
        count_col, page_col, method_col = st.columns(3)
        count_col.metric("Proposed masks", len(detections))
        page_col.metric("Pages / images", analysis["page_count"])
        method_col.metric(
            "Pages read by OCR",
            analysis["ocr_pages"] if is_pdf else "1 photo",
        )

        if not analysis["base_detections"] and not analysis["search_detections"]:
            st.warning(
                "No automatic matches were found. Search for exact words or phrases below to add mask candidates."
            )
        st.caption(
            "Coral outlines show automatic matches and searched text. Matches are grouped by detected type or search term; "
            "everything starts unchecked."
        )
        page_count = int(analysis["page_count"])
        for page_index in range(page_count):
            page_matches = [item for item in detections if int(item["page"]) == page_index]
            page_title = f"Page {page_index + 1}" if is_pdf else "Photo"
            with st.expander(
                f"{page_title} · {len(page_matches)} proposed mask{'s' if len(page_matches) != 1 else ''}",
                expanded=page_count == 1 or page_index == 0,
            ):
                st.image(analysis["previews"][page_index], width="stretch")
                if not page_matches:
                    st.caption("No matches on this page yet.")

        _render_mask_review_controls(analysis, source_bytes, name, is_pdf)

    st.markdown("---")
    if st.button("Reset this session", width="stretch"):
        _clear_session_artifacts()
        st.session_state["upload_nonce"] = upload_nonce + 1
        st.rerun()

st.caption("Setup may download software and a language model. Document analysis and redaction run locally after setup.")
