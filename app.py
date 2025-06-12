import threading
import time
from urllib.parse import urlparse

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI()

# Mount datasets as static for PDF serving
app.mount("/datasets", StaticFiles(directory="datasets"), name="datasets")
templates = Jinja2Templates(directory="templates")

# Azure Blob Storage config
AZURE_BLOB_URL = "https://irisdatasets.blob.core.windows.net/datasets/content_understanding/processed_data_for_evaluation_pipeline/"

# Simple in-memory cache for dataset list and details
CACHE = {
    "datasets": {"value": None, "ts": 0},
    "details": {},  # key: dataset_name, value: {"value": ..., "ts": ...}
}
CACHE_LOCK = threading.Lock()
CACHE_TTL = 2 * 60 * 60  # 2 hours in seconds

# Background thread to prefetch and cache all dataset details
PREFETCH_INTERVAL = 300  # seconds (5 minutes)


# Helper to get container and prefix from URL
def parse_blob_url(blob_url):
    parsed = urlparse(blob_url)
    # e.g. 'https://irisdatasets.blob.core.windows.net/datasets/content_understanding/processed_data_for_evaluation_pipeline/'
    account_url = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.lstrip("/")
    container, *prefix = path.split("/", 1)
    prefix = prefix[0] if prefix else ""
    return account_url, container, prefix


# List datasets in Azure Blob Storage
def list_azure_datasets():
    now = time.time()
    with CACHE_LOCK:
        if CACHE["datasets"]["value"] is not None and now - CACHE["datasets"]["ts"] < CACHE_TTL:
            return CACHE["datasets"]["value"]
    account_url, container, prefix = parse_blob_url(AZURE_BLOB_URL)
    credential = DefaultAzureCredential()
    service_client = BlobServiceClient(account_url, credential=credential)
    container_client = service_client.get_container_client(container)
    dataset_names = set()
    blobs = container_client.walk_blobs(name_starts_with=prefix, delimiter="/")
    for blob in blobs:
        if hasattr(blob, 'name') and blob.name != prefix:
            # e.g. prefix/dataset_name/
            rel = blob.name[len(prefix):].strip("/")
            if rel:
                dataset = rel.split("/", 1)[0]
                dataset_names.add(dataset)
    result = sorted(dataset_names)
    with CACHE_LOCK:
        CACHE["datasets"] = {"value": result, "ts": now}
    return result


# Background prefetching of all dataset details
def prefetch_all_dataset_details():
    while True:
        try:
            datasets = list_azure_datasets()
            for dataset_name in datasets:
                # Prefetch and cache details
                try:
                    api_dataset_detail(dataset_name)
                except Exception:
                    pass  # Optionally log error
        except Exception:
            pass  # Optionally log error
        time.sleep(PREFETCH_INTERVAL)


# Start background prefetch thread on app startup
@app.on_event("startup")
def start_prefetch_thread():
    t = threading.Thread(target=prefetch_all_dataset_details, daemon=True)
    t.start()


@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    datasets = list_azure_datasets()
    return templates.TemplateResponse("index.html", {"request": request, "datasets": datasets, "azure": True})


@app.get("/api/datasets")
def api_list_datasets():
    datasets = list_azure_datasets()
    return JSONResponse({"datasets": datasets})


@app.get("/api/datasets/{dataset_name}")
def api_dataset_detail(dataset_name: str):
    now = time.time()
    with CACHE_LOCK:
        cached = CACHE["details"].get(dataset_name)
        if cached and now - cached["ts"] < CACHE_TTL:
            return JSONResponse(cached["value"])
    account_url, container, prefix = parse_blob_url(AZURE_BLOB_URL)
    credential = DefaultAzureCredential()
    service_client = BlobServiceClient(account_url, credential=credential)
    container_client = service_client.get_container_client(container)
    dataset_prefix = f"{prefix}{dataset_name}/"
    blobs = list(container_client.list_blobs(name_starts_with=dataset_prefix))
    pdfs = [b.name[len(dataset_prefix + 'input_files/'):] for b in blobs if b.name.startswith(
        dataset_prefix + 'input_files/') and b.name.lower().endswith('.pdf')]
    kb_pdfs = [b.name[len(dataset_prefix + 'knowledge_base_files/'):] for b in blobs if b.name.startswith(
        dataset_prefix + 'knowledge_base_files/') and b.name.lower().endswith('.pdf')]
    analyzer = None
    analyzer_blob_name = dataset_prefix + 'analyzer.json'
    if any(b.name == analyzer_blob_name for b in blobs):
        blob_client = container_client.get_blob_client(analyzer_blob_name)
        analyzer = blob_client.download_blob().readall()
        import json
        analyzer = json.loads(analyzer)
    results = []
    for b in blobs:
        if b.name.startswith(dataset_prefix) and b.name.endswith('.result.json'):
            blob_client = container_client.get_blob_client(b.name)
            content = blob_client.download_blob().readall()
            import json
            results.append(
                {"name": b.name[len(dataset_prefix):], "content": json.loads(content)})
    result = {"pdfs": pdfs, "kb_pdfs": kb_pdfs,
              "analyzer": analyzer, "results": results}
    with CACHE_LOCK:
        CACHE["details"][dataset_name] = {"value": result, "ts": now}
    return JSONResponse(result)


@app.get("/pdf/{dataset_name}/{pdf_type}/{pdf_name}")
def pdf_view(dataset_name: str, pdf_type: str, pdf_name: str):
    account_url, container, prefix = parse_blob_url(AZURE_BLOB_URL)
    credential = DefaultAzureCredential()
    service_client = BlobServiceClient(account_url, credential=credential)
    container_client = service_client.get_container_client(container)
    blob_path = f"{prefix}{dataset_name}/{pdf_type}/{pdf_name}"
    blob_client = container_client.get_blob_client(blob_path)
    stream = blob_client.download_blob()
    return StreamingResponse(stream.chunks(), media_type="application/pdf")
