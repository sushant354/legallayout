# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python-based PDF parser and converter that transforms PDF documents into structured HTML output. The tool extracts text content, detects document layout (headers, footers, sections, paragraphs), identifies tables, and handles amendments in legal documents.

## Core Architecture

### Main Components

- **Main.py** (`source/Main.py`): Primary orchestrator class that coordinates the entire parsing workflow
  - Handles PDF to XML conversion via `ParserTool`
  - Manages page processing pipeline through `Page` objects
  - Builds final HTML output using `HTMLBuilder`
  - Implements header/footer detection across all pages using sequence matching

- **ParserTool.py** (`source/ParserTool.py`): PDF processing interface
  - Uses pdfminer's `pdf2txt.py` to convert PDFs to XML format
  - Parses XML to extract page elements

- **Page.py** (`source/Page.py`): Individual page analysis and classification
  - Processes textboxes and determines page layout (single/multi-column)
  - Classifies content into: headers, footers, sections, paragraphs, side notes, tables
  - Uses ML clustering (DBSCAN, KMeans) for layout analysis

- **HTMLBuilder.py** (`source/HTMLBuilder.py`): HTML generation with structured output
  - Converts classified content into semantic HTML
  - Applies CSS styling for different content types (sections, paragraphs, amendments)
  - Maintains document hierarchy

### Key Processing Pipeline

1. **PDF → XML Conversion**: Uses pdfminer to extract structured text and coordinates
2. **Header/Footer Detection**: Cross-page analysis using sequence matching to identify repeating elements
3. **Content Classification**: Each page's textboxes are classified into semantic categories
4. **HTML Generation**: Structured HTML with CSS styling based on content classification

### Caching System

- **cache_xml/**: Stores intermediate XML files from PDF conversion
- **cache_pdf/**: Temporary storage for input PDFs (when needed)
- Cache files are automatically cleaned up unless `--keep-xml` flag is used

## Commands

### Running the Parser
```bash
python source/Main.py -i <input_pdf> -o <output_directory> [options]
```

### Command Line Options
- `-i, --input-filePath`: Input PDF file path (required)
- `-o, --output-directory`: Output directory for HTML file (required)
- `-s, --section-startPage`: Section start page number (optional)
- `-e, --section-endPage`: Section end page number (optional)
- `-a, --amendments`: Flag for PDFs containing amendments
- `-l, --loglevel`: Log level (error|warning|info|debug, default: info)
- `-g, --logfile`: Log file path (optional)
- `-x, --keep-xml`: Keep intermediate XML files in cache_xml folder

### Testing
```bash
python -m unittest test.TestPageLayout
```

### Installing Dependencies
```bash
pip install -r requirements.txt
```

## Key Dependencies

- **pdfminer.six**: PDF text extraction and analysis
- **scikit-learn**: Machine learning for layout detection (DBSCAN, KMeans clustering)
- **camelot-py[cv]**: Table extraction from PDFs
- **pandas, numpy**: Data processing
- **matplotlib**: Visualization support

## Development Notes

- The system uses coordinate-based analysis extensively - textbox positions determine content classification
- Header/footer detection uses similarity matching across pages (SequenceMatcher with 0.4 threshold)
- Page layout detection uses clustering algorithms to identify single vs multi-column layouts
- Amendment detection is a specialized feature for legal documents
- All intermediate files are cached to avoid reprocessing during development