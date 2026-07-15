"""
Search Loader Module - Azure AI Search Integration

Uploads ayah index documents to Azure AI Search for semantic search.
The Arabic analyzer enables proper tokenization of Quranic text.

Index Schema:
- surah (Int32, filterable)
- ayah (Int32, filterable)
- reciter (String, filterable, facetable)
- qiraa (String, filterable)
- text_uthmani (String, searchable, ar.microsoft analyzer)
- start_ms (Int64)
- end_ms (Int64)
- audio_url (String)
- confidence (Double)
"""

import os
import json
from pathlib import Path
from typing import List, Optional, Dict, Any

from rich.console import Console
from rich.progress import Progress, TaskID

console = Console()


# Azure AI Search index schema
INDEX_SCHEMA = {
    "name": "quran-ayah-index",
    "fields": [
        {"name": "id", "type": "Edm.String", "key": True, "searchable": False},
        {"name": "surah", "type": "Edm.Int32", "filterable": True, "sortable": True},
        {"name": "ayah", "type": "Edm.Int32", "filterable": True, "sortable": True},
        {"name": "reciter", "type": "Edm.String", "filterable": True, "facetable": True},
        {"name": "qiraa", "type": "Edm.String", "filterable": True, "facetable": True},
        {
            "name": "text_uthmani",
            "type": "Edm.String",
            "searchable": True,
            "analyzer": "ar.microsoft"  # Arabic analyzer — root-based stemming
        },
        {"name": "start_ms", "type": "Edm.Int64"},
        {"name": "end_ms", "type": "Edm.Int64"},
        {"name": "audio_url", "type": "Edm.String"},
        {"name": "confidence", "type": "Edm.Double", "filterable": True, "sortable": True}
    ]
}

SEMANTIC_CONFIG_NAME = "quran-semantic"


def create_search_client(
    endpoint: str,
    key: str,
    index_name: str
):
    """
    Create Azure AI Search client.
    
    Requires azure-search-documents package.
    """
    try:
        from azure.search.documents import SearchClient
        from azure.core.credentials import AzureKeyCredential
    except ImportError:
        raise ImportError(
            "azure-search-documents not installed. Install with:\n"
            "pip install azure-search-documents"
        )
    
    credential = AzureKeyCredential(key)
    return SearchClient(
        endpoint=endpoint,
        index_name=index_name,
        credential=credential
    )


def create_index_client(endpoint: str, key: str):
    """Create Azure AI Search index management client."""
    try:
        from azure.search.documents.indexes import SearchIndexClient
        from azure.core.credentials import AzureKeyCredential
    except ImportError:
        raise ImportError(
            "azure-search-documents not installed. Install with:\n"
            "pip install azure-search-documents"
        )
    
    credential = AzureKeyCredential(key)
    return SearchIndexClient(endpoint=endpoint, credential=credential)


def ensure_index_exists(
    endpoint: str,
    key: str,
    index_name: str
) -> bool:
    """
    Ensure the search index exists, creating it if necessary.
    
    Returns True if index was created, False if it already existed.
    """
    try:
        from azure.search.documents.indexes.models import (
            SearchIndex,
            SearchField,
            SearchFieldDataType,
            SemanticConfiguration,
            SemanticField,
            SemanticPrioritizedFields,
            SemanticSearch,
        )
    except ImportError:
        raise ImportError(
            "azure-search-documents not installed. Install with:\n"
            "pip install azure-search-documents"
        )

    index_client = create_index_client(endpoint, key)

    # Check if index exists
    existing_indexes = [idx.name for idx in index_client.list_indexes()]

    if index_name in existing_indexes:
        console.print(f"[dim]Index '{index_name}' already exists[/dim]")
        return False

    console.print(f"[blue]Creating index '{index_name}'...[/blue]")

    # Build fields from schema
    fields = []
    type_map = {
        "Edm.String": SearchFieldDataType.String,
        "Edm.Int32": SearchFieldDataType.Int32,
        "Edm.Int64": SearchFieldDataType.Int64,
        "Edm.Double": SearchFieldDataType.Double
    }

    for field_def in INDEX_SCHEMA["fields"]:
        field = SearchField(
            name=field_def["name"],
            type=type_map.get(field_def["type"], SearchFieldDataType.String),
            key=field_def.get("key", False),
            searchable=field_def.get("searchable", False),
            filterable=field_def.get("filterable", False),
            sortable=field_def.get("sortable", False),
            facetable=field_def.get("facetable", False),
            analyzer_name=field_def.get("analyzer")
        )
        fields.append(field)

    # Semantic configuration — re-ranks results by meaning using Microsoft's language models
    semantic_config = SemanticConfiguration(
        name=SEMANTIC_CONFIG_NAME,
        prioritized_fields=SemanticPrioritizedFields(
            content_fields=[SemanticField(field_name="text_uthmani")]
        )
    )
    semantic_search = SemanticSearch(configurations=[semantic_config])

    index = SearchIndex(name=index_name, fields=fields, semantic_search=semantic_search)
    index_client.create_index(index)

    console.print(f"[green]✓[/green] Index '{index_name}' created with semantic configuration")
    return True
    
    if index_name in existing_indexes:
        console.print(f"[dim]Index '{index_name}' already exists[/dim]")
        return False
    
    console.print(f"[blue]Creating index '{index_name}'...[/blue]")
    
    # Build fields from schema
    fields = []
    type_map = {
        "Edm.String": SearchFieldDataType.String,
        "Edm.Int32": SearchFieldDataType.Int32,
        "Edm.Int64": SearchFieldDataType.Int64,
        "Edm.Double": SearchFieldDataType.Double
    }
    
    for field_def in INDEX_SCHEMA["fields"]:
        field = SearchField(
            name=field_def["name"],
            type=type_map.get(field_def["type"], SearchFieldDataType.String),
            key=field_def.get("key", False),
            searchable=field_def.get("searchable", False),
            filterable=field_def.get("filterable", False),
            sortable=field_def.get("sortable", False),
            facetable=field_def.get("facetable", False),
            analyzer_name=field_def.get("analyzer")
        )
        fields.append(field)
    
    index = SearchIndex(name=index_name, fields=fields)
    index_client.create_index(index)
    
    console.print(f"[green]✓[/green] Index '{index_name}' created")
    return True


def load_json_to_documents(json_path: Path) -> List[Dict[str, Any]]:
    """
    Load JSON output file and convert to search documents.
    
    Each ayah becomes a separate document with a unique ID.
    """
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    documents = []
    
    surah = data["surah"]
    reciter = data.get("reciter", "unknown")
    qiraa = data.get("qiraa", "Hafs")
    
    for ayah in data["ayahs"]:
        # Create unique document ID
        doc_id = f"{surah:03d}_{ayah['ayah']:03d}_{reciter}"
        
        doc = {
            "id": doc_id,
            "surah": surah,
            "ayah": ayah["ayah"],
            "reciter": reciter,
            "qiraa": qiraa,
            "text_uthmani": ayah["text_uthmani"],
            "start_ms": ayah["start_ms"],
            "end_ms": ayah["end_ms"],
            "audio_url": ayah.get("audio_url", ""),
            "confidence": ayah.get("confidence", 0.0)
        }
        documents.append(doc)
    
    return documents


def upload_documents(
    endpoint: str,
    key: str,
    index_name: str,
    documents: List[Dict[str, Any]],
    batch_size: int = 100
) -> dict:
    """
    Upload documents to Azure AI Search index.
    
    Uses batching for efficiency.
    """
    client = create_search_client(endpoint, key, index_name)
    
    total = len(documents)
    uploaded = 0
    failed = 0
    
    console.print(f"[blue]Uploading {total} documents...[/blue]")
    
    with Progress(console=console) as progress:
        task = progress.add_task("Uploading...", total=total)
        
        for i in range(0, total, batch_size):
            batch = documents[i:i + batch_size]
            
            try:
                result = client.upload_documents(documents=batch)
                
                for r in result:
                    if r.succeeded:
                        uploaded += 1
                    else:
                        failed += 1
                        console.print(
                            f"[red]Failed to upload {r.key}: {r.error_message}[/red]"
                        )
                
            except Exception as e:
                console.print(f"[red]Batch upload failed: {e}[/red]")
                failed += len(batch)
            
            progress.update(task, advance=len(batch))
    
    console.print(f"[green]✓[/green] Uploaded: {uploaded}, Failed: {failed}")
    
    return {
        "uploaded": uploaded,
        "failed": failed,
        "total": total
    }


def upload_index_to_search(
    json_path: Path,
    endpoint: Optional[str] = None,
    key: Optional[str] = None,
    index_name: Optional[str] = None
) -> dict:
    """
    Upload a JSON index file to Azure AI Search.
    
    Args:
        json_path: Path to the JSON output file
        endpoint: Azure AI Search endpoint (or from env)
        key: Azure AI Search admin key (or from env)
        index_name: Index name (or from env)
        
    Returns:
        Upload statistics
    """
    console.print(f"\n[bold]═══ LOAD: Azure AI Search ═══[/bold]\n")
    
    # Get credentials from env if not provided
    endpoint = endpoint or os.getenv("AZURE_SEARCH_ENDPOINT")
    key = key or os.getenv("AZURE_SEARCH_KEY")
    index_name = index_name or os.getenv("AZURE_SEARCH_INDEX", "quran-ayah-index")
    
    if not endpoint or not key:
        console.print(
            "[yellow]Skipping Azure AI Search upload: "
            "AZURE_SEARCH_ENDPOINT or AZURE_SEARCH_KEY not set[/yellow]"
        )
        return {"skipped": True}
    
    # Ensure index exists
    ensure_index_exists(endpoint, key, index_name)
    
    # Load documents
    documents = load_json_to_documents(json_path)
    
    if not documents:
        console.print("[yellow]No documents to upload[/yellow]")
        return {"uploaded": 0, "total": 0}
    
    # Upload
    return upload_documents(endpoint, key, index_name, documents)


def search_ayahs(
    query: str,
    endpoint: Optional[str] = None,
    key: Optional[str] = None,
    index_name: Optional[str] = None,
    filters: Optional[str] = None,
    top: int = 10,
    semantic: bool = False
) -> List[Dict]:
    """
    Search for ayahs in the index.

    Args:
        query: Search query (Arabic text or keyword)
        endpoint: Azure AI Search endpoint
        key: Azure AI Search query key
        index_name: Index name
        filters: OData filter expression (e.g., "surah eq 67")
        top: Maximum results to return
        semantic: Enable semantic ranking (re-ranks by meaning, not just keyword frequency)

    Returns:
        List of matching ayah documents
    """
    endpoint = endpoint or os.getenv("AZURE_SEARCH_ENDPOINT")
    key = key or os.getenv("AZURE_SEARCH_KEY")
    index_name = index_name or os.getenv("AZURE_SEARCH_INDEX", "quran-ayah-index")

    if not endpoint or not key:
        raise ValueError("Azure AI Search credentials not configured")

    client = create_search_client(endpoint, key, index_name)

    search_kwargs: Dict[str, Any] = dict(
        search_text=query,
        filter=filters,
        top=top,
        include_total_count=True,
    )

    if semantic:
        try:
            from azure.search.documents.models import QueryType
            search_kwargs["query_type"] = QueryType.SEMANTIC
            search_kwargs["semantic_configuration_name"] = SEMANTIC_CONFIG_NAME
            search_kwargs["query_caption"] = "extractive"
        except ImportError:
            pass  # fall back to keyword search

    results = client.search(**search_kwargs)

    documents = []
    for result in results:
        doc = {k: v for k, v in result.items() if not k.startswith("@")}
        # Attach semantic captions if available
        captions = getattr(result, "@search.captions", None)
        if captions:
            doc["_caption"] = captions[0].text if captions else None
        documents.append(doc)

    return documents


def get_ayah_by_reference(
    surah: int,
    ayah: int,
    reciter: Optional[str] = None,
    endpoint: Optional[str] = None,
    key: Optional[str] = None,
    index_name: Optional[str] = None
) -> Optional[Dict]:
    """
    Get a specific ayah by surah:ayah reference.
    
    Returns the document with audio timing information.
    """
    endpoint = endpoint or os.getenv("AZURE_SEARCH_ENDPOINT")
    key = key or os.getenv("AZURE_SEARCH_KEY")
    index_name = index_name or os.getenv("AZURE_SEARCH_INDEX", "quran-ayah-index")
    
    if not endpoint or not key:
        raise ValueError("Azure AI Search credentials not configured")
    
    # Build filter
    filter_expr = f"surah eq {surah} and ayah eq {ayah}"
    if reciter:
        filter_expr += f" and reciter eq '{reciter}'"
    
    client = create_search_client(endpoint, key, index_name)
    
    results = client.search(
        search_text="*",
        filter=filter_expr,
        top=1
    )
    
    for result in results:
        return {k: v for k, v in result.items() if not k.startswith("@")}
    
    return None


if __name__ == "__main__":
    console.print("[yellow]Search loader module loaded successfully[/yellow]")
    console.print("[dim]Configure AZURE_SEARCH_* env vars to enable upload[/dim]")
