"""Execute the historical import notebook in an isolated project directory."""

import argparse
from pathlib import Path

import nbformat
from nbconvert.preprocessors import ExecutePreprocessor


PROJECT_MARKER = 'PROJECT_DIR = Path("/Users/akkouh/Desktop/POLYPBASE")'


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("notebook", type=Path)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--output-notebook", required=True, type=Path)
    return parser.parse_args()


def main():
    args = parse_args()
    project_dir = args.project_dir.resolve()
    notebook = nbformat.read(args.notebook, as_version=4)

    marker_replaced = False
    for cell in notebook.cells:
        if cell.cell_type != "code":
            continue
        if PROJECT_MARKER in cell.source:
            cell.source = cell.source.replace(
                PROJECT_MARKER,
                f"PROJECT_DIR = Path({str(project_dir)!r})",
            )
            marker_replaced = True

    if not marker_replaced:
        raise SystemExit("The PROJECT_DIR marker was not found in the notebook.")

    # Cells after the final export are exploratory displays with another old
    # absolute path. They do not participate in table generation.
    notebook.cells = notebook.cells[:27]

    processor = ExecutePreprocessor(timeout=1800, kernel_name="python3")
    processor.preprocess(notebook, {"metadata": {"path": str(project_dir)}})

    args.output_notebook.parent.mkdir(parents=True, exist_ok=True)
    nbformat.write(notebook, args.output_notebook)
    print(f"NOTEBOOK_OK {args.output_notebook}")


if __name__ == "__main__":
    main()
