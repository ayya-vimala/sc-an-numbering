#!/usr/bin/env python3
"""
split_an_files.py

Reads an input file that contains a number of concatenated HTML documents
(each a full <!DOCTYPE html> ... </html> document, sutta or sutta-range
format for the Anguttara Nikaya), validates them, and writes each one out
as its own .html file into the correct an1..an11 folder.

Usage:
    python split_an_files.py input.txt -o output_dir
"""

import argparse
import os
import re
import sys
from lxml import etree

# ---------------------------------------------------------------------------
# Splitting the big input file into individual HTML documents
# ---------------------------------------------------------------------------

DOCTYPE_RE = re.compile(r'(?=<!DOCTYPE\s+html)', re.IGNORECASE)


def split_documents(text: str):
    """Split the raw file content into individual <!DOCTYPE html> ... documents."""
    parts = DOCTYPE_RE.split(text)
    # The first element may be empty / whitespace before the first DOCTYPE
    docs = [p.strip() for p in parts if p.strip()]
    return docs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_dash(s: str) -> str:
    """Normalize en-dash/em-dash to a plain hyphen for comparison purposes."""
    return s.replace('\u2013', '-').replace('\u2014', '-').strip()


NUMBER_RE = re.compile(r'^([0-9]+(?:\.[0-9]+)?(?:\s*[-\u2013\u2014]\s*[0-9]+)?)')


def extract_leading_number(text: str) -> str:
    """Extract the leading number/range pattern from a heading's text, e.g.
    '3.1. Gefahr' -> '3.1', '2.280\u2013309. Die Ordenszucht' -> '2.280-309'."""
    text = text.strip()
    m = NUMBER_RE.match(text)
    if not m:
        return ''
    return normalize_dash(m.group(1)).replace(' ', '')


def folder_for_id(an_id: str):
    """Given an id like 'an3.1' or 'an2.280-309', return the folder name
    'an3' / 'an2', or None if it doesn't match the expected an<N>... pattern."""
    m = re.match(r'^an(\d+)\.', an_id)
    if not m:
        return None
    return f"an{m.group(1)}"


# ---------------------------------------------------------------------------
# Validation + extraction for a single document
# ---------------------------------------------------------------------------

class DocError(Exception):
    pass


# Elements that never need a closing tag (HTML "void elements"), plus
# DOCTYPE-ish things we should just skip over.
VOID_ELEMENTS = {
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
    'link', 'meta', 'param', 'source', 'track', 'wbr',
}

# Matches: comments, doctype, closing tags, opening tags (with optional self-close /)
TAG_RE = re.compile(
    r'<!--.*?-->'           # comments
    r'|<!DOCTYPE[^>]*>'     # doctype
    r'|</(?P<close>[A-Za-z][A-Za-z0-9]*)\s*>'                  # closing tag
    r'|<(?P<open>[A-Za-z][A-Za-z0-9]*)((?:[^>"\'/]|"[^"]*"|\'[^\']*\')*)(?P<selfclose>/?)>',  # opening tag
    re.DOTALL,
)


def check_tag_balance(html_text: str):
    """Lightweight, HTML5-aware well-formedness check: verifies every
    non-void opening tag has a matching closing tag in the right order.
    Raises DocError with a line/column reference on mismatch."""
    stack = []  # list of (tag_name, line_no)

    def line_of(pos):
        return html_text.count('\n', 0, pos) + 1

    for m in TAG_RE.finditer(html_text):
        if m.group('close'):
            name = m.group('close').lower()
            if name in VOID_ELEMENTS:
                continue
            if not stack:
                raise DocError(
                    f"Closing tag </{name}> at line {line_of(m.start())} "
                    f"has no matching opening tag."
                )
            open_name, open_line = stack[-1]
            if open_name != name:
                raise DocError(
                    f"Mismatched tag: expected closing tag for <{open_name}> "
                    f"(opened at line {open_line}), but found </{name}> "
                    f"at line {line_of(m.start())}."
                )
            stack.pop()
        elif m.group('open'):
            name = m.group('open').lower()
            if m.group('selfclose') or name in VOID_ELEMENTS:
                continue
            stack.append((name, line_of(m.start())))

    if stack:
        unclosed = ', '.join(f"<{name}> (line {line})" for name, line in stack)
        raise DocError(f"Unclosed tag(s) at end of document: {unclosed}.")


def parse_strict(html_text: str):
    """Validate well-formedness (HTML5-aware tag balance check), then parse
    leniently with lxml to build a tree we can extract data from."""
    check_tag_balance(html_text)

    parser = etree.HTMLParser(recover=True)
    root = etree.fromstring(html_text.encode('utf-8'), parser)
    if root is None:
        raise DocError("Could not parse document (empty result).")
    return root


def get_body(root):
    body = root.find('.//body')
    if body is None:
        raise DocError("No <body> tag found.")
    return body


def first_element_child(elem):
    for child in elem:
        if isinstance(child.tag, str):
            return child
    return None


def process_document(html_text: str, doc_index: int):
    """
    Validate one document and return (an_id, folder_name, html_text).
    Raises DocError on any problem.
    """
    root = parse_strict(html_text)
    body = get_body(root)

    main = first_element_child(body)
    if main is None:
        raise DocError("Body has no element children.")

    tag = main.tag.lower()
    if tag not in ('article', 'section'):
        raise DocError(
            f"Expected first child of <body> to be <article> or <section>, "
            f"found <{tag}>."
        )

    an_id = main.get('id')
    if not an_id:
        raise DocError(f"<{tag}> directly under <body> has no id attribute.")

    lang = main.get('lang')
    if lang != 'de':
        raise DocError(f"<{tag} id='{an_id}'> does not have lang='de' (found lang={lang!r}).")

    folder = folder_for_id(an_id)
    if not folder:
        raise DocError(f"id '{an_id}' does not match expected pattern 'an<N>....'.")

    if tag == 'article':
        # Top-level sutta case: an3.1, or a top-level range like an1.188-197
        h1 = main.find('./header/h1')
        if h1 is None:
            raise DocError(f"id '{an_id}': no <h1> found under <header>.")
        h1_class = h1.get('class', '')
        if not ({'sutta-title', 'range-title'} & set(h1_class.split())):
            raise DocError(
                f"id '{an_id}': <h1> is missing class 'sutta-title' or 'range-title' "
                f"(found class={h1_class!r})."
            )
        h1_text = h1.text or ''
        h1_number = extract_leading_number(h1_text)
        id_number = an_id[len('an'):]  # strip leading 'an'
        # strip the leading digits before the first dot that denote the book (already in folder name);
        # actually id_number already excludes 'an'
        id_number_norm = normalize_dash(id_number)
        if h1_number != id_number_norm:
            raise DocError(
                f"id '{an_id}': <h1> number '{h1_number}' (from text {h1_text!r}) "
                f"does not match id number '{id_number_norm}'."
            )

    else:
        # Range / section case: an2.280-309, containing nested <article> elements
        section_class = main.get('class', '')
        if 'range' not in section_class.split():
            raise DocError(
                f"id '{an_id}': <section> is missing class 'range' (found class={section_class!r})."
            )

        h1 = main.find('./header/h1')
        if h1 is None:
            raise DocError(f"id '{an_id}': no <h1> found under <header>.")
        h1_class = h1.get('class', '')
        if 'range-title' not in h1_class.split():
            raise DocError(
                f"id '{an_id}': <h1> is missing class 'range-title' (found class={h1_class!r})."
            )
        h1_text = h1.text or ''
        h1_number = extract_leading_number(h1_text)
        id_number = normalize_dash(an_id[len('an'):])
        if h1_number != id_number:
            raise DocError(
                f"id '{an_id}': <h1> number '{h1_number}' (from text {h1_text!r}) "
                f"does not match id number '{id_number}'."
            )

        # Validate each nested <article>
        articles = main.findall('./article')
        if not articles:
            raise DocError(f"id '{an_id}': <section class='range'> has no nested <article> elements.")

        for art in articles:
            art_id = art.get('id')
            if not art_id:
                raise DocError(f"id '{an_id}': a nested <article> has no id attribute.")
            if not art_id.startswith('an'):
                raise DocError(f"id '{an_id}': nested article id '{art_id}' does not start with 'an'.")
            h2 = art.find('./h2')
            if h2 is None:
                raise DocError(f"id '{an_id}': nested article '{art_id}' has no <h2>.")
            h2_class = h2.get('class', '')
            if 'sutta-title' not in h2_class.split():
                raise DocError(
                    f"id '{an_id}': nested article '{art_id}' <h2> is missing class "
                    f"'sutta-title' (found class={h2_class!r})."
                )
            h2_text = h2.text or ''
            h2_number = extract_leading_number(h2_text)
            art_number = normalize_dash(art_id[len('an'):])
            if h2_number != art_number:
                raise DocError(
                    f"id '{an_id}': nested article '{art_id}' <h2> number '{h2_number}' "
                    f"(from text {h2_text!r}) does not match article id number '{art_number}'."
                )

    return an_id, folder


def dump_error_doc(output_dir: str, doc_index: int, html_text: str) -> str:
    """Save a failing document's raw content to an errors/ folder so it can
    be located and inspected. Tries to include its id in the filename if
    one can be found, even though the document failed other validation."""
    errors_dir = os.path.join(output_dir, 'errors')
    os.makedirs(errors_dir, exist_ok=True)

    id_hint = ''
    m = re.search(r"""<(?:article|section)[^>]*\bid=['"]([^'"]+)['"]""", html_text)
    if m:
        id_hint = f"_{m.group(1)}"

    filename = f"doc{doc_index:04d}{id_hint}.html"
    path = os.path.join(errors_dir, filename)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html_text)
    return path


# ---------------------------------------------------------------------------
# Main driver
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Split a file of concatenated AN HTML documents into "
                    "individual files sorted into an1..an11 folders."
    )
    parser.add_argument('input_file', help="Path to the input file containing the HTML documents.")
    parser.add_argument('-o', '--output-dir', default='output',
                        help="Base output directory (default: ./output)")
    args = parser.parse_args()

    try:
        with open(args.input_file, 'r', encoding='utf-8') as f:
            text = f.read()
    except OSError as e:
        print(f"ERROR: could not read input file: {e}")
        sys.exit(1)

    docs = split_documents(text)
    if not docs:
        print("ERROR: no <!DOCTYPE html> documents found in the input file.")
        sys.exit(1)

    print(f"Found {len(docs)} document(s) in input file.")

    seen_ids = {}  # an_id -> doc_index (1-based) for duplicate detection
    results = []   # list of (an_id, folder, html_text)
    error_count = 0

    for i, doc in enumerate(docs, start=1):
        try:
            an_id, folder = process_document(doc, i)
        except DocError as e:
            error_path = dump_error_doc(args.output_dir, i, doc)
            print(f"ERROR in document #{i}: {e}\n    -> saved to {error_path}")
            error_count += 1
            continue
        except Exception as e:  # catch-all so one bad doc doesn't kill the run
            error_path = dump_error_doc(args.output_dir, i, doc)
            print(f"ERROR in document #{i}: unexpected failure: {e}\n    -> saved to {error_path}")
            error_count += 1
            continue

        if an_id in seen_ids:
            error_path = dump_error_doc(args.output_dir, i, doc)
            print(
                f"ERROR in document #{i}: duplicate id '{an_id}' "
                f"(already seen in document #{seen_ids[an_id]}). Skipping this one."
                f"\n    -> saved to {error_path}"
            )
            error_count += 1
            continue

        seen_ids[an_id] = i
        results.append((an_id, folder, doc))

    if not results:
        print("No valid documents to write. Exiting.")
        sys.exit(1)

    # Create folders an1..an11 up front
    for n in range(1, 12):
        os.makedirs(os.path.join(args.output_dir, f"an{n}"), exist_ok=True)

    written = 0
    for an_id, folder, doc in results:
        folder_path = os.path.join(args.output_dir, folder)
        os.makedirs(folder_path, exist_ok=True)  # in case folder is outside an1..an11
        filename = f"{an_id}.html"
        filepath = os.path.join(folder_path, filename)
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(doc)
                if not doc.endswith('\n'):
                    f.write('\n')
            written += 1
        except OSError as e:
            print(f"ERROR: could not write file '{filepath}': {e}")
            error_count += 1

    print(f"\nDone. {written} file(s) written successfully, {error_count} error(s) encountered.")


if __name__ == '__main__':
    main()