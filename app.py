from urllib.parse import urlparse

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI()

# Mount datasets as static for PDF serving
app.mount("/datasets", StaticFiles(directory="datasets"), name="datasets")
templates = Jinja2Templates(directory="templates")

# Azure Blob Storage config
AZURE_BLOB_URL = "https://irisdatasets.blob.core.windows.net/datasets/content_understanding/processed_data_for_evaluation_pipeline/"


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
    account_url, container, prefix = parse_blob_url(AZURE_BLOB_URL)
    credential = DefaultAzureCredential()
    service_client = BlobServiceClient(account_url, credential=credential)
    container_client = service_client.get_container_client(container)
    # List folders (datasets) under prefix
    dataset_names = set()
    blobs = container_client.walk_blobs(name_starts_with=prefix, delimiter="/")
    for blob in blobs:
        if hasattr(blob, 'name') and blob.name != prefix:
            # e.g. prefix/dataset_name/
            rel = blob.name[len(prefix):].strip("/")
            if rel:
                dataset = rel.split("/", 1)[0]
                dataset_names.add(dataset)
    return sorted(dataset_names)


@app.get("/azure-datasets", response_class=HTMLResponse)
def list_azure_datasets_view(request: Request):
    datasets = list_azure_datasets()
    # Pass a flag so the template can generate correct links
    return templates.TemplateResponse("index.html", {"request": request, "datasets": datasets, "azure": True})


@app.get("/")
def root():
    return RedirectResponse(url="/azure-datasets")


@app.get("/azure-datasets/{dataset_name}", response_class=HTMLResponse)
def azure_dataset_detail(request: Request, dataset_name: str):
    account_url, container, prefix = parse_blob_url(AZURE_BLOB_URL)
    credential = DefaultAzureCredential()
    service_client = BlobServiceClient(account_url, credential=credential)
    container_client = service_client.get_container_client(container)
    dataset_prefix = f"{prefix}{dataset_name}/"
    blobs = list(container_client.list_blobs(name_starts_with=dataset_prefix))

    # PDFs in input_files
    pdfs = [b.name[len(dataset_prefix + 'input_files/'):] for b in blobs if b.name.startswith(
        dataset_prefix + 'input_files/') and b.name.lower().endswith('.pdf')]
    # PDFs in knowledge_base_files
    kb_pdfs = [b.name[len(dataset_prefix + 'knowledge_base_files/'):] for b in blobs if b.name.startswith(
        dataset_prefix + 'knowledge_base_files/') and b.name.lower().endswith('.pdf')]
    # analyzer.json
    analyzer = None
    analyzer_blob_name = dataset_prefix + 'analyzer.json'
    if any(b.name == analyzer_blob_name for b in blobs):
        blob_client = container_client.get_blob_client(analyzer_blob_name)
        analyzer = blob_client.download_blob().readall()
        import json
        analyzer = json.loads(analyzer)
    # *.result.json files
    results = []
    for b in blobs:
        if b.name.startswith(dataset_prefix) and b.name.endswith('.result.json'):
            blob_client = container_client.get_blob_client(b.name)
            content = blob_client.download_blob().readall()
            import json
            results.append(
                {"name": b.name[len(dataset_prefix):], "content": json.loads(content)})
    return templates.TemplateResponse(
        "dataset.html", {"request": request, "dataset": dataset_name,
                         "pdfs": pdfs, "kb_pdfs": kb_pdfs, "analyzer": analyzer, "results": results, "azure": True}
    )


@app.get("/azure-datasets/{dataset_name}/pdf/{pdf_type}/{pdf_name}")
def azure_pdf_view(dataset_name: str, pdf_type: str, pdf_name: str):
    # pdf_type: 'input_files' or 'knowledge_base_files'
    account_url, container, prefix = parse_blob_url(AZURE_BLOB_URL)
    credential = DefaultAzureCredential()
    service_client = BlobServiceClient(account_url, credential=credential)
    container_client = service_client.get_container_client(container)
    blob_path = f"{prefix}{dataset_name}/{pdf_type}/{pdf_name}"
    blob_client = container_client.get_blob_client(blob_path)
    stream = blob_client.download_blob()
    return StreamingResponse(stream.chunks(), media_type="application/pdf")
