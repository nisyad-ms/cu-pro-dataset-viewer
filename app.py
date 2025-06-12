import json
import os

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI()

# Mount datasets as static for PDF serving
app.mount("/datasets", StaticFiles(directory="datasets"), name="datasets")
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
def list_datasets(request: Request):
    datasets_dir = "datasets"
    datasets = []
    for name in os.listdir(datasets_dir):
        path = os.path.join(datasets_dir, name)
        if os.path.isdir(path):
            datasets.append(name)
    return templates.TemplateResponse("index.html", {"request": request, "datasets": datasets})


@app.get("/dataset/{dataset_name}", response_class=HTMLResponse)
def dataset_detail(request: Request, dataset_name: str):
    base = os.path.join("datasets", dataset_name)
    input_dir = os.path.join(base, "input_files")
    kb_dir = os.path.join(base, "knowledge_base_files")
    pdfs = []
    kb_pdfs = []
    if os.path.isdir(input_dir):
        pdfs = [f for f in os.listdir(input_dir) if f.lower().endswith(".pdf")]
    if os.path.isdir(kb_dir):
        kb_pdfs = [f for f in os.listdir(kb_dir) if f.lower().endswith(".pdf")]
    analyzer = None
    analyzer_path = os.path.join(base, "analyzer.json")
    if os.path.isfile(analyzer_path):
        with open(analyzer_path) as f:
            analyzer = json.load(f)
    results = []
    for f in os.listdir(base):
        if f.endswith(".result.json"):
            with open(os.path.join(base, f)) as rf:
                results.append({"name": f, "content": json.load(rf)})
    return templates.TemplateResponse(
        "dataset.html", {"request": request, "dataset": dataset_name,
                         "pdfs": pdfs, "kb_pdfs": kb_pdfs, "analyzer": analyzer, "results": results}
    )
